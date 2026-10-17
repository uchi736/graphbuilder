# -*- coding: utf-8 -*-
"""つなぎ込み（src/chunk_references.py の link_chunk_references）の試験。LLM・リランカー・Neo4j は差し替え用の関数で、本物は呼ばない。

確かめること: rule では LLM が 0 回、hybrid では規則の残りだけ、やり直しで古いリンクが残らない、
規則と LLM のリンクが共存して重ならない、別の仕組みの REFERS_TO を消さない、失敗時の扱いを壊していない。
使い方: cd /code && python /tests/t_link.py [実物のチャンクの JSON]   （tests/run.sh がそう動かす）
"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.environ.get("CODE_DIR", "/code"))
import src.chunk_references as cr  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("OK  " if cond else "NG  ") + name, "" if cond else detail)


class Graph:
    """Neo4j の代わり。link_chunk_references が出す照会だけを受ける。知らない照会は例外にする。"""

    def __init__(self, chunks):
        self.chunks = [dict(c) for c in chunks]
        self.links = []
        self.props = {}
        self.fail_on = None  # 照会の文面にこの語があれば例外を出す（書き込みの途中で切れた場合の試験用）
        self.fewest_rule_links = None  # 書き込みの間に、規則のリンクが最も少なくなったときの本数

    def query(self, q, params=None):
        params = params or {}
        if self.fail_on and self.fail_on in q:
            raise ConnectionError("書き込みの途中で切れた")
        try:
            return self._query(q, params)
        finally:
            n = sum(1 for l in self.links if l.get("method") == "rule")
            self.fewest_rule_links = n if self.fewest_rule_links is None else min(self.fewest_rule_links, n)

    def _query(self, q, params):
        ids, files = {c["id"] for c in self.chunks}, {c["fileName"] for c in self.chunks}
        if q.startswith("CREATE INDEX"):
            return []
        if "RETURN s.id AS source" in q:
            return [{"source": l["source"], "target": l["target"]} for l in self.links if l.get("method") == params["method"]]
        if "RETURN c.id AS id" in q:
            rows = []
            for c in sorted(self.chunks, key=lambda c: (c["fileName"], c["position"])):
                p = self.props.get(c["id"], {})
                mine = [l for l in self.links if l["source"] == c["id"]]
                rows.append(dict(c, stored=p.get("stored"), unresolved=p.get("unresolved", False), external=p.get("external"),
                                 links=sum(1 for l in mine if l.get("method") == params["method"]),
                                 rule_links=sum(1 for l in mine if l.get("method") == params["rule"])))
            return rows
        if "DELETE r" in q:
            assert params["method"] in ("llm", "rule"), q  # method の無いリンクを消す照会は出ないはず
            if "UNWIND" in q:
                label = "Document" if "t:Document" in q else "Chunk"
                pairs = {(r["source"], r["target"]) for r in params["rows"]}
                self.links = [l for l in self.links if not (l.get("method") == params["method"] and l["to"] == label and (l["source"], l["target"]) in pairs)]
            elif "c.id IN $ids" in q:
                self.links = [l for l in self.links if not (l["source"] in params["ids"] and l.get("method") == params["method"])]
            elif "r.run" in q:  # 今回の印の無い古いリンクだけを消す
                self.links = [l for l in self.links if not (l.get("method") == params["method"] and l.get("run") != params["run"])]
            else:
                self.links = [l for l in self.links if l.get("method") != params["method"]]
            return []
        if "MERGE" in q:
            label = "Document" if "t:Document" in q else "Chunk"
            known = files if label == "Document" else ids
            have = {(l["source"], l["target"], l.get("method"), l.get("quote"), l.get("locator")): l for l in self.links}
            for r in params["rows"]:
                key = (r["source"], r["target"], params["method"], r["quote"], r["locator"])
                if r["source"] in ids and r["target"] in known:
                    if key not in have:
                        have[key] = {"source": r["source"], "target": r["target"], "to": label, "method": params["method"], "quote": r["quote"], "locator": r["locator"]}
                        self.links.append(have[key])
                    have[key].update({"scope": r.get("scope"), "form": r.get("form"), "source_section": r.get("section"), "topic": r.get("topic")})
                    if "r.run = $run" in q:
                        have[key]["run"] = params["run"]
            return []
        if "reference_extraction" in q:
            for r in params["rows"]:
                self.props.setdefault(r["id"], {}).update({k: (v or None) if k == "external" else v for k, v in r.items() if k != "id"})
            return []
        if "SET c.reference_unresolved = true" in q:
            for i in params["ids"]:
                self.props.setdefault(i, {})["unresolved"] = True
            return []
        if "SET c.external_references" in q:
            for r in params["rows"]:
                self.props.setdefault(r["id"], {})["external"] = r["external"] or None
            return []
        raise AssertionError(q)

    def of(self, method=None, source=None, to=None):
        return [l for l in self.links if l.get("method") == method and (source is None or l["source"] == source) and (to is None or l["to"] == to)]

    def pairs(self, method):
        return sorted((l["source"], l["target"]) for l in self.links if l.get("method") == method)


def corpus():
    a = ["# KA-101 受入検査基準\n\n## 1. 適用範囲\n\nこの基準は受入検査に適用する。判定は 3. による。記録は KA-102 の 2.1 による。",
         "## 2. 引用文書\n\nKA-102 による。JIS Z 9015 に準拠する。帳票規定の第 6 条による。別表 9 による。合否は 3. による。",
         "## 3. 判定\n\n合否は責任者が決める。溶剤により洗浄する。必要な手順は品質マニュアルの定めによる。",
         "## 4. 改訂履歴\n\n| 版 | 内容 |\n|---|---|\n| B | 3. の判定を見直した。KA-102 による。別表 8 による |"]
    b = ["# KA-102 記録要領\n\n## 2. 記録\n\n### 2.1 様式\n\n記録は所定の様式に残す。", "### 2.2 保管\n\n5 年間保管する。"]
    rows = [{"id": "a%d" % (i + 1), "fileName": "KA-101B.md", "position": i + 1, "text": "[KA-101B]\n" + t} for i, t in enumerate(a)]
    return rows + [{"id": "b%d" % (i + 1), "fileName": "KA-102A.md", "position": i + 1, "text": "[KA-102A]\n" + t} for i, t in enumerate(b)]


prompts = []


def kind_of(prompt):
    return "extract" if prompt.startswith("次の本文は") else ("document" if "が指している文書を" in prompt else "chunk")


def ask(prompt):
    """LLM の代わり。抽出は「参照なし」、文書の特定は「手元に無い」、チャンクの特定は、位置の語を見出しに持つ文書片を答える。"""
    prompts.append(prompt)
    kind = kind_of(prompt)
    if kind == "extract":
        return '{"references": []}'
    if kind == "document":
        return "0"
    target = re.search(r"参照先の文書: (.+)", prompt).group(1)
    locator = re.search(r"参照先の位置: (.+)", prompt).group(1)
    items = re.split(r"\n\n(?=\[\d+\]\n|（参照元)", prompt.split("番号だけをカンマ区切り")[-1])
    want = "2.1 様式" if target == "KA-102A.md" else ("## 3. 判定" if locator.replace(" ", "") == "別表9" else "%%none%%")
    for it in items:
        m = re.match(r"\[(\d+)\]\n(.*)", it, re.S)
        if m and want in m.group(2):
            return m.group(1)
    return "0"


def never(prompt):
    raise AssertionError("LLM が呼ばれた: " + prompt[:60])


def rerank(query, texts):
    return [sum(1 for ch in set(query) if ch in t) for t in texts]


def env(**kw):
    for k in ("CHUNK_REFERENCE_MODE", "CHUNK_REFERENCE_DOCUMENT_PLACES", "CHUNK_REFERENCE_RULE_DISABLE", "CHUNK_REFERENCE_MIN_FAILURES"):
        os.environ.pop(k, None)
    os.environ.update(kw)


# ───────── 0. 設定なし: 従来の動き（LLM だけ） ─────────
env()
g = Graph(corpus())
prompts.clear()
res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("設定なし: 既定の mode は llm（従来の動き）で、規則のリンクを書かない", res["summary"]["mode"] == "llm" and not g.of("rule") and "rule" not in res["summary"]
      and collections.Counter(kind_of(p) for p in prompts)["extract"] == 6, res["summary"])
check("設定なし: 保存の形は従来のキーを保つ", {"v", "docs", "links", "refs"} <= set(json.loads(g.props["a1"]["stored"])), g.props["a1"])

# ───────── 1. rule: LLM を 1 回も呼ばない ─────────
env(CHUNK_REFERENCE_MODE="rule")
g = Graph(corpus())
g.links.append({"source": "a1", "target": "b2", "to": "Chunk", "quote": "別の仕組み", "locator": ""})          # method の無い REFERS_TO（別の仕組みが張ったもの）
g.links.append({"source": "a1", "target": "a3", "to": "Chunk", "method": "llm", "quote": "古い", "locator": "3."})   # 規則と同じ先への LLM のリンク
g.links.append({"source": "a1", "target": "b2", "to": "Chunk", "method": "llm", "quote": "古い", "locator": ""})     # 規則が張らない先への LLM のリンク
res = cr.link_chunk_references(g, ask=never, rerank=never)
check("rule: LLM が 0 回", res["llm_calls"] == 0 and res["summary"]["mode"] == "rule", res["summary"])
check("rule: 規則のリンクが method='rule' で書かれる", ("a1", "a3") in g.pairs("rule") and ("a1", "b1") in g.pairs("rule") and ("a2", "a3") in g.pairs("rule"), g.pairs("rule"))
check("rule: 文書だけを指す参照は文書へのリンク", ("a2", "KA-102A.md") in [(l["source"], l["target"]) for l in g.of("rule", to="Document")], g.of("rule", to="Document"))
check("rule: 同じ参照元の同じ参照先に LLM のリンクを重ねない（規則のリンクを残す）", ("a1", "a3") not in g.pairs("llm") and ("a1", "b2") in g.pairs("llm"), g.pairs("llm"))
check("rule: method の無い REFERS_TO を消さない", len(g.of(None)) == 1, g.of(None))
hist = g.of("rule", source="a4")
check("rule: 改正履歴からのリンクも張り、source_section で見分けられる", hist and all(l["source_section"] == "revision_history" for l in hist)
      and all(l["source_section"] == "body" for l in g.of("rule") if l["source"] != "a4"), hist)
check("rule: リンクに型（form）と範囲（scope）が付く", all(l["form"] and l["scope"] in ("same_document", "other_document") for l in g.of("rule")), g.of("rule")[:2])
check("rule: 外部の文書の表記を Chunk.external_references に残す", g.props["a2"]["external"] == ["JIS Z 9015"], g.props.get("a2"))
check("rule: LLM の保存（reference_extraction、reference_unresolved）には触れない", not any("stored" in p or "unresolved" in p for p in g.props.values()), g.props)
s = res["summary"]
check("rule: 要約に規則の件数が出る", s["rule"]["references"] == len([r for r in res["references"]]) and s["rule"]["links"] == len(g.of("rule", to="Chunk")) and s["llm_routed"]["chunks"] == 0
      and s["aborted"] is None and s["skipped_documents"] == [], s)
json.dumps(s, ensure_ascii=False)  # 要約は JSON にできる（後処理の message に入る）
n_rule = len(g.of("rule"))
res = cr.link_chunk_references(g, ask=never, rerank=never)
check("rule: やり直しても増えない", len(g.of("rule")) == n_rule and len(g.of(None)) == 1, (len(g.of("rule")), n_rule))
g.chunks[0]["text"] = g.chunks[0]["text"].replace("判定は 3. による。", "判定は 2. による。")
res = cr.link_chunk_references(g, ask=never, rerank=never)
check("rule: 本文が変わると、古い規則のリンクが残らない（a1→a3 が消え、a1→a2 が張られる）", ("a1", "a3") not in g.pairs("rule") and ("a1", "a2") in g.pairs("rule") and ("a1", "b1") in g.pairs("rule"), g.pairs("rule"))

# ───────── 2. hybrid: 規則が先、残りだけ LLM ─────────
HYBRID = dict(CHUNK_REFERENCE_MODE="hybrid")
env(**HYBRID)
g = Graph(corpus())
g.links.append({"source": "a1", "target": "b2", "to": "Chunk", "quote": "別の仕組み", "locator": ""})
prompts.clear()
res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
kinds = collections.Counter(kind_of(p) for p in prompts)
s = res["summary"]
check("hybrid: CHUNK_REFERENCE_MODE=hybrid で hybrid になる", s["mode"] == "hybrid", s)
check("hybrid: 抽出を LLM に頼むのは、規則の参照が無い文に「文書らしい語＋参照の語」があるチャンクだけ（1 回）", kinds["extract"] == 1 and "責任者が決める" in [p for p in prompts if kind_of(p) == "extract"][0], kinds)
check("hybrid: 文書の特定を LLM に頼むのは、規則が文書を決められなかった参照だけ（帳票規定の 1 回）", kinds["document"] == 1 and "帳票規定" in [p for p in prompts if kind_of(p) == "document"][0], kinds)
check("hybrid: チャンクの特定は、文書だけを指す参照と、題名の無い別表の 2 回", kinds["chunk"] == 2, kinds)
check("hybrid: LLM の呼び出しは合わせて 4 回", res["llm_calls"] == 4 and s["llm_calls"] == 4, s)
check("hybrid: 要約に、LLM に回したチャンクの数が理由ごとに出る", s["llm_routed"] == {"chunks": 2, "unresolved": 1, "document_only": 1, "no_detection": 1, "sent": 2}, s["llm_routed"])
check("hybrid: 規則のリンクと LLM のリンクが共存する", ("a2", "b1") in g.pairs("llm") and ("a1", "a3") in g.pairs("rule") and ("a2", "KA-102A.md") in g.pairs("rule"), (g.pairs("llm"), g.pairs("rule")))
check("hybrid: LLM が規則と同じ先（a2→a3）を選んでも重ねて張らない", ("a2", "a3") in g.pairs("rule") and ("a2", "a3") not in g.pairs("llm"), g.pairs("llm"))
check("hybrid: 改正履歴の中の参照は LLM に回さない", not any(re.search(r"記述: .*別表 8", p) for p in prompts) and not any("改訂履歴" in p for p in prompts if kind_of(p) == "extract"))
check("hybrid: 外部の文書は、規則の分と LLM の分を合わせて残す", g.props["a2"]["external"] == ["JIS Z 9015", "帳票規定"], g.props["a2"].get("external"))
check("hybrid: 保存の形（v、docs、links、refs）を保ち、mode・task・covered を足す", set(json.loads(g.props["a2"]["stored"])) == {"v", "docs", "links", "refs", "mode", "task", "covered"}
      and json.loads(g.props["a2"]["stored"])["links"] == 1 and g.props["a2"]["unresolved"] is False, g.props["a2"])
check("hybrid: method の無い REFERS_TO を消さない", len(g.of(None)) == 1)
check("hybrid: 失敗なし", not res["failed"] and not s["skipped_documents"] and s["aborted"] is None, res["failed"])
before = (g.pairs("rule"), g.pairs("llm"))
prompts.clear()
res2 = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("hybrid: やり直しでは LLM を呼ばず、リンクも変わらない", not prompts and res2["llm_calls"] == 0 and (g.pairs("rule"), g.pairs("llm")) == before and res2["summary"]["unchanged"] == 6,
      (len(prompts), res2["summary"]))
check("hybrid: やり直しの要約にも、LLM の担当のチャンクの数が出る（今回送った数は 0）", res2["summary"]["llm_routed"]["chunks"] == 2 and res2["summary"]["llm_routed"]["sent"] == 0, res2["summary"]["llm_routed"])

# 本文が変わる: 規則のリンクが入れ替わり、LLM に渡す仕事が変わったチャンクだけ引き直す
g.chunks[1]["text"] = g.chunks[1]["text"].replace("別表 9 による。", "").replace("合否は 3. による。", "合否は 1. による。")
prompts.clear()
res3 = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("hybrid: 本文が変わると、古い規則のリンクが残らない（a2→a3 が消え、a2→a1 が張られる）", ("a2", "a3") not in g.pairs("rule") and ("a2", "a1") in g.pairs("rule"), g.pairs("rule"))
check("hybrid: 引き直すのは仕事の変わったチャンクだけ（a2 の 2 回）", res3["chunks"] == 1 and collections.Counter(kind_of(p) for p in prompts) == {"document": 1, "chunk": 1}, (res3["chunks"], [kind_of(p) for p in prompts]))

# 文書だけを指す参照の場所選びを止める
env(CHUNK_REFERENCE_DOCUMENT_PLACES="false", **HYBRID)
g = Graph(corpus())
prompts.clear()
res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("hybrid: CHUNK_REFERENCE_DOCUMENT_PLACES=false で、文書だけを指す参照を LLM に回さない", res["llm_calls"] == 3 and res["summary"]["llm_routed"]["document_only"] == 0
      and ("a2", "b1") not in g.pairs("llm") and ("a2", "KA-102A.md") in g.pairs("rule"), (res["llm_calls"], g.pairs("llm")))

# ───────── 3. 失敗時の扱い ─────────
env(**HYBRID)
g = Graph(corpus())
cr.link_chunk_references(g, ask=ask, rerank=rerank)
before = (g.pairs("rule"), g.pairs("llm"))


def ask_down(prompt):
    raise ConnectionError("connection refused")


os.environ["CHUNK_REFERENCE_ABORT_AFTER"] = "2"
res = cr.link_chunk_references(g, ask=ask_down, rerank=rerank, reresolve=True)
check("LLM が止まっている: 連続の失敗で打ち切り、LLM のリンクを消さず、規則のリンクは書き終えている", res["summary"]["aborted"] and res["llm_calls"] == 2 and (g.pairs("rule"), g.pairs("llm")) == before, (res["summary"], g.pairs("llm")))
os.environ.pop("CHUNK_REFERENCE_ABORT_AFTER")
res = cr.link_chunk_references(g, ask=lambda p: "わかりません", rerank=rerank, reresolve=True)
check("読めない答え: 特定に失敗したチャンクは既存のリンクを消さず、失敗として数え、次回の対象にする", any(f["stage"] == "resolve" for f in res["failed"]) and res["summary"]["failed_chunks"] == 1
      and (g.pairs("rule"), g.pairs("llm")) == before and g.props["a2"]["unresolved"] is True, (res["summary"], res["failed"], g.props["a2"]))
prompts.clear()
res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("次の実行で、失敗したチャンクだけを引き直して元に戻る", not res["failed"] and (g.pairs("rule"), g.pairs("llm")) == before and g.props["a2"]["unresolved"] is False
      and res["chunks"] == 1 and len(prompts) == 3, (res["failed"], g.pairs("llm"), res["chunks"], len(prompts)))
g.props["a3"]["stored"] = None
res = cr.link_chunk_references(g, ask=lambda p: "サーバーエラー", rerank=rerank)
check("抽出の失敗（hybrid）: 失敗が 1 件だけなら文書を飛ばさず、失敗として数え、既存のリンクは変えない", res["summary"]["skipped_documents"] == [] and res["summary"]["failed_chunks"] == 1
      and (g.pairs("rule"), g.pairs("llm")) == before, res["summary"])
os.environ["CHUNK_REFERENCE_MIN_FAILURES"] = "1"
res = cr.link_chunk_references(g, ask=lambda p: "サーバーエラー", rerank=rerank)
check("抽出の失敗（hybrid）: CHUNK_REFERENCE_MIN_FAILURES=1 なら、LLM の要る 1 チャンクの失敗で文書を飛ばす（規則だけで済んだチャンクは割合に数えない）",
      res["summary"]["skipped_documents"] == ["KA-101B.md"] and (g.pairs("rule"), g.pairs("llm")) == before, res["summary"])
os.environ.pop("CHUNK_REFERENCE_MIN_FAILURES")

# ───────── 4. mode の切り替え ─────────
env(**HYBRID)
g = Graph(corpus())
cr.link_chunk_references(g, ask=ask, rerank=rerank)
n_rule = len(g.of("rule"))
env(CHUNK_REFERENCE_MODE="llm")
prompts.clear()
res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("llm: 規則を使わず、全チャンクの抽出を LLM に頼む（6 回）", collections.Counter(kind_of(p) for p in prompts)["extract"] == 6 and res["summary"]["mode"] == "llm" and "rule" not in res["summary"], res["summary"])
check("llm: 規則のリンクが残らない", not g.of("rule") and res["summary"]["rule_links_removed"] == n_rule, (len(g.of("rule")), res["summary"]))
check("llm: 切り替えた回の要約に、切り替える前の本数が出る", res["summary"].get("mode_switch", {}).get("rule_links_before") == n_rule, res["summary"])
n_llm = len(g.of("llm"))
env(**HYBRID)
res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("llm → hybrid: 切り替えた回の要約に、切り替える前の LLM のリンクの本数が出る", res["summary"].get("mode_switch", {}).get("llm_links_before") == n_llm
      and res["summary"]["mode_switch"]["chunks_stored_in_other_mode"] == 6, res["summary"])
res = cr.link_chunk_references(g, ask=never, rerank=never)
check("切り替えの次の回は、要約に切り替えの項目が出ない", "mode_switch" not in res["summary"], res["summary"])
env(CHUNK_REFERENCE_MODE="llm")
cr.link_chunk_references(g, ask=ask, rerank=rerank)
env(CHUNK_REFERENCE_MODE="なにか")
check("知らない mode は既定（llm）として扱う", cr._mode() == "llm" and cr._mode("RULE") == "rule")
res = cr.link_chunk_references(g, ask=ask, rerank=rerank, mode="rule")
check("引数の mode が環境変数より優先される", res["summary"]["mode"] == "rule" and len(g.of("rule")) == n_rule and res["llm_calls"] == 0, res["summary"])
env(CHUNK_REFERENCE_MODE="rule")
res = cr.link_chunk_references(Graph(corpus()), ask=None, rerank=None)
check("rule: ask と rerank を渡さなくても動く（LLM への接続を作らない）", res["llm_calls"] == 0 and res["summary"]["rule"]["links"] > 0 and "src.llm" not in sys.modules, res["summary"])
dry = Graph(corpus())
res = cr.link_chunk_references(dry, ask=never, rerank=never, write=False)
check("write=False では何も書かない", not dry.links and not dry.props and res["summary"]["rule"]["links"] > 0)

# 規則の側で例外が出ても、後処理の全体は止まらない
env(**HYBRID)
g = Graph(corpus())
cr.link_chunk_references(g, ask=ask, rerank=rerank)
before = (g.pairs("rule"), g.pairs("llm"))
original = cr.find_references


def broken(chunks, disable=()):
    raise RuntimeError("規則の不具合")


cr.find_references = broken
prompts.clear()
res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("規則が例外を出したとき（hybrid）: 規則のリンクにも LLM のリンクにも触れず、前回の保存を使い回し（LLM 0 回）、要約に残す", res["summary"]["mode"] == "llm"
      and "規則の不具合" in res["summary"]["rule_error"] and (g.pairs("rule"), g.pairs("llm")) == before and not prompts and res["summary"]["aborted"] is None, res["summary"])
g.props["a3"]["stored"] = None
prompts.clear()
res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("規則が例外を出したとき（hybrid）: 保存の無いチャンクだけを LLM の抽出に回し、規則と同じ先へは重ねて張らない", collections.Counter(kind_of(p) for p in prompts)["extract"] == 1
      and g.pairs("rule") == before[0] and not set(g.pairs("rule")) & set(g.pairs("llm")), (prompts[:1], g.pairs("llm")))
res = cr.link_chunk_references(g, ask=never, rerank=never, mode="rule")
check("規則が例外を出したとき（rule）: 何も書かず、打ち切りとして返す", res["summary"]["aborted"] and g.pairs("rule") == before[0] and res["llm_calls"] == 0, res["summary"])
cr.find_references = original
prompts.clear()
res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("規則が直れば、次の実行で hybrid に戻る", res["summary"]["mode"] == "hybrid" and (g.pairs("rule"), g.pairs("llm")) == before and not res["failed"], (res["summary"], g.pairs("llm")))

# ───────── 5. 実物の 72 チャンク（渡されたときだけ）。LLM は差し替え用の関数 ─────────
if len(sys.argv) > 1:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    real = [{"id": c["id"], "fileName": c["fileName"], "position": c["position"], "text": c["text"], "page_number": c.get("page"), "section": c.get("section")} for c in data["chunks"]]
    env(CHUNK_REFERENCE_MODE="rule")
    g = Graph(real)
    res = cr.link_chunk_references(g, ask=never, rerank=never)
    s = res["summary"]
    print("  実物・rule:", json.dumps(s, ensure_ascii=False))
    check("実物・rule: LLM が 0 回で、規則のリンクが書かれる", res["llm_calls"] == 0 and len(g.of("rule", to="Chunk")) > 0 and len(g.of("rule", to="Document")) > 0, s)
    check("実物・rule: 書かれたリンクの数は、同じ参照元と参照先を 1 本にまとめた数", len(g.of("rule")) == len({(l["source"], l["target"]) for l in g.of("rule")}), len(g.of("rule")))
    env(**HYBRID)
    g = Graph(real)
    prompts.clear()
    res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
    s = res["summary"]
    print("  実物・hybrid:", json.dumps(s, ensure_ascii=False))
    print("  実物・hybrid の LLM の呼び出し:", dict(collections.Counter(kind_of(p) for p in prompts)), "/ 書かれたリンク: rule", len(g.of("rule")), "llm", len(g.of("llm")))
    check("実物・hybrid: 抽出を頼むのは、検出の無かったチャンクの数だけ", collections.Counter(kind_of(p) for p in prompts)["extract"] == s["llm_routed"]["no_detection"], s["llm_routed"])
    check("実物・hybrid: LLM に回るのは 72 チャンクの一部", 0 < s["llm_routed"]["chunks"] < 72 and s["llm_routed"]["sent"] == s["llm_routed"]["chunks"], s["llm_routed"])
    prompts.clear()
    res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
    check("実物・hybrid: やり直しでは LLM を呼ばない", not prompts and res["summary"]["unchanged"] == 72, res["summary"])

print("%d 件中 %d 件が OK" % (len(results), sum(results)))
print("ALL OK" if all(results) else "SOME FAILED")
sys.exit(0 if all(results) else 1)
