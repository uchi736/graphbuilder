"""参照リンクの確認（LLM・リランカー・Neo4j は偽物）"""
import json
import re
import sys

sys.path.insert(0, "/code")
import src.chunk_references as cr

ok = True


def check(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("OK  " if cond else "NG  ") + name, detail if not cond else "")


# --- 出力の読み取り
check("json with prose", cr._json_of('考えます {"x":1} 答え: {"references": []} 以上') == {"references": []})
check("json: last one wins", cr._json_of('参照が無ければ {"references": []} とする。\n{"references": [{"quote": "a"}]}') == {"references": [{"quote": "a"}]})
check("json missing -> None", cr._json_of("参照はありません") is None and cr._json_of('{"refs": []}') is None)
check("numbers strict", cr._numbers_of("理由は3つ\n[2], [5]", 8) == [2, 5] and cr._numbers_of("2、5", 8) == [2, 5] and cr._numbers_of("2。", 8) == [2])
check("numbers none", cr._numbers_of("なし", 8) == [] and cr._numbers_of("該当なし。", 8) == [] and cr._numbers_of("0", 8) == [] and cr._numbers_of("N/A", 8) == [])
check("numbers unreadable", cr._numbers_of("3-5 項は [4] にある", 8) is None and cr._numbers_of("", 8) is None)
check("choice", cr._choice_of("[3]", 5) == 3 and cr._choice_of("S", 5) == cr.SELF and cr._choice_of("0", 5) == 0 and cr._choice_of("**2**。", 5) == 2)
check("choice unreadable", cr._choice_of("HS-VTR-T003", 5) is None and cr._choice_of("3 番の文書", 5) is None and cr._choice_of("9", 5) is None)
check("mentions", cr._mentions("別表2", cr._norm("別表1及び別表2による")) and cr._mentions("別表 2", cr._norm("別表1及び2による")) and not cr._mentions("第9条", cr._norm("第3条に従う")))

# --- 文書の候補: 字面が同点の文書が多くても、完全一致とリランカーの 1 位が入る
docs = [{"fileName": "就業規則.pdf", "head": "本則"}] + [{"fileName": f"就業規則 別表{i}.pdf", "head": "別表"} for i in range(1, 15)] + [{"fileName": "賃金規程.pdf", "head": "賃金"}]
names = [d["fileName"] for d in cr.document_candidates("就業規則", "就業規則による。", docs, lambda q, ts: [1.0 if t.startswith("賃金規程") else 0.0 for t in ts])]
check("document candidates: exact name and reranker top both kept", "就業規則.pdf" in names and "賃金規程.pdf" in names and len(names) == 12, names)

# --- チャンクの前段: 位置の語を含むチャンクが、長いチャンクに押し出されない
texts = ["一般的な説明が長く続く。" * 30 + f"その{i}" for i in range(200)] + ["第30条 休暇の定め。"]
top = cr._Corpus(texts).top("第30条 一般的な説明が長く続く", 80, "第30条")
check("prefilter keeps the chunk that contains the locator", 200 in top[:5], top[:5])


# --- 偽の環境
class Graph:
    def __init__(self, chunks):
        self.chunks = chunks
        self.links = []
        self.props = {}

    def query(self, q, params=None):
        params = params or {}
        if q.startswith("CREATE INDEX"):
            return []
        if "RETURN c.id AS id" in q:
            return [dict(c, stored=self.props.get(c["id"], {}).get("stored"), unresolved=self.props.get(c["id"], {}).get("unresolved", False),
                         links=sum(1 for l in self.links if l["source"] == c["id"])) for c in sorted(self.chunks, key=lambda c: (c["fileName"], c["position"]))]
        if "DELETE r" in q:
            assert params["method"] == "llm"
            self.links = [l for l in self.links if l["source"] not in params["ids"]]
            return []
        if "MERGE" in q:
            known = {c["id"] for c in self.chunks} | {c["fileName"] for c in self.chunks}
            for r in params["rows"]:
                key = (r["source"], r["target"], r["quote"], r["locator"])
                if r["target"] in known and key not in {(l["source"], l["target"], l["quote"], l["locator"]) for l in self.links}:
                    self.links.append(dict(r, to="Document" if "t:Document" in q else "Chunk"))
            return []
        if "reference_extraction" in q:
            for r in params["rows"]:
                self.props.setdefault(r["id"], {}).update(r)
            return []
        if "SET c.reference_unresolved = true" in q:
            for i in params["ids"]:
                self.props.setdefault(i, {})["unresolved"] = True
            return []
        raise AssertionError(q)

    def remove_document(self, name):
        gone = {c["id"] for c in self.chunks if c["fileName"] == name}
        self.chunks = [c for c in self.chunks if c["fileName"] != name]
        self.links = [l for l in self.links if l["source"] not in gone and l["target"] not in gone and l["target"] != name]


def make_chunks(n_docs, per_doc=3):
    out = []
    for d in range(1, n_docs + 1):
        for p in range(1, per_doc + 1):
            text = f"[規程{d:03d}]\n第{p}条 規程{d:03d}の内容その{p}。"
            if p == 2:
                text += f" 前条の定めによる。規程{d % n_docs + 1:03d}の第3条に従う。別表1による。JIS Z 9999 に準拠する。"
            if p == 3:
                text += " 別表1 手当の一覧。"
            out.append({"id": f"d{d}p{p}", "text": text, "position": p, "fileName": f"規程{d:03d}.pdf"})
    return out


prompts = []


def ask(prompt):
    prompts.append(prompt)
    if prompt.startswith("次の本文は"):
        body = prompt.split("本文:\n")[-1]
        refs = []
        if "前条の定めによる" in body:
            nxt = re.search(r"規程(\d{3})の第3条に従う", body).group(1)
            refs = [{"quote": "前条の定めによる。", "target_document": None, "locator": "前条", "topic": "前条の定め"},
                    {"quote": f"規程{nxt}の第3条に従う。", "target_document": f"規程{nxt}", "locator": "第3条", "topic": "第3条の内容"},
                    {"quote": "別表1による。", "target_document": "別表1", "locator": None, "topic": "別表の内容"},
                    {"quote": "JIS Z 9999 に準拠する。", "target_document": "JIS Z 9999", "locator": None, "topic": "規格"},
                    {"quote": "規程999の第9条による。", "target_document": "規程999", "locator": "第9条", "topic": "作り話"}]
        return '書式は {"references": []} である。抜き出します。\n' + json.dumps({"references": refs}, ensure_ascii=False)
    if "が指している文書を" in prompt:
        name = re.search(r"この記述の中の「(.+?)」", prompt).group(1)
        if name == "別表1":
            return "S"
        for m in re.finditer(r"\[(\d+)\] (.+?)\.pdf", prompt):
            if m.group(2) == name:
                return m.group(1)
        return "0"
    if "文書片の番号を答えよ" in prompt:
        loc = re.search(r"参照先の位置: (.+)", prompt).group(1)
        listing = prompt.split("番号だけをカンマ区切り")[-1]
        items = re.split(r"\n\n(?=\[\d+\]\n|（参照元)", listing)
        if loc == "前条":
            idx = [i for i, it in enumerate(items) if it.startswith("（参照元")][0]
            return re.match(r"\[(\d+)\]", items[idx - 1]).group(1)
        want = "別表1 手当" if loc == "指定なし" else loc.replace("第3条", "第3条 ")
        for it in items:
            m = re.match(r"\[(\d+)\]\n(.*)", it, re.S)
            if m and want in m.group(2):
                return m.group(1) + "。"
        return "0"
    raise AssertionError(prompt[:80])


def rerank(query, texts):
    return [sum(1 for ch in set(query) if ch in t) for t in texts]


def chunk_links(g):
    return [l for l in g.links if l["to"] == "Chunk"]


# --- 6 文書
g = Graph(make_chunks(6))
res = cr.link_chunk_references(g, ask=ask, rerank=rerank)
by = {(r["source_doc"], r["quote"][:3]): r for r in res["references"]}
r = by[("規程001.pdf", "前条の")]
check("relative ref -> previous chunk", [t["id"] for t in r["targets"]] == ["d1p1"], r)
r = by[("規程001.pdf", "規程0")]
check("other document + section", r["target_doc"] == "規程002.pdf" and [t["id"] for t in r["targets"]] == ["d2p3"], r)
r = by[("規程001.pdf", "別表1")]
check("appendix -> same document", r["scope"] == "same_document" and [t["id"] for t in r["targets"]] == ["d1p3"], r)
r = by[("規程001.pdf", "JIS")]
check("external", r["scope"] == "external" and not r["targets"], r)
check("fabricated dropped", len(res["dropped"]) == 6 and all("規程999" in d["quote"] for d in res["dropped"]), res["dropped"][:2])
check("external stored", g.props["d1p2"]["external"] == ["JIS Z 9999"] and g.props["d1p2"]["unresolved"] is False)
check("links written", len(chunk_links(g)) == 18, len(g.links))
check("no failures", not res["failed"] and not res["skipped_documents"] and res["aborted"] is None)
n_first = res["llm_calls"]

# --- やり直し: 変わっていないチャンクには何もしない
prompts.clear()
res2 = cr.link_chunk_references(g, ask=ask, rerank=rerank)
check("rerun does nothing when nothing changed", res2["chunks"] == 0 and res2["skipped"] == 18 and not prompts, (res2["chunks"], res2["skipped"], len(prompts)))
check("rerun keeps links", len(chunk_links(g)) == 18)

# --- 文書を後から足す: 外部だった参照だけを引き直す
g.chunks.append({"id": "jis1", "text": "[JIS Z 9999]\nJIS Z 9999 の本文。規格の内容。", "position": 1, "fileName": "JIS Z 9999.pdf"})


def ask2(prompt):
    if "文書片の番号を答えよ" in prompt and "参照先の文書: JIS Z 9999.pdf" in prompt:
        prompts.append(prompt)
        return "1"
    return ask(prompt)


prompts.clear()
res3 = cr.link_chunk_references(g, file_names=["JIS Z 9999.pdf"], ask=ask2, rerank=rerank)
check("incremental relink of externals", sum(1 for l in g.links if l["target"] == "jis1") == 6, [l for l in g.links if l["target"] == "jis1"][:1])
check("incremental scope and cost", res3["chunks"] == 7 and res3["llm_calls"] == 13 and len(chunk_links(g)) == 24, (res3["chunks"], res3["llm_calls"], len(chunk_links(g))))

# --- 結び先の文書を消す: その文書を指していたチャンクだけ引き直し、外部扱いに戻る
g.remove_document("JIS Z 9999.pdf")
res3b = cr.link_chunk_references(g, file_names=["無関係.pdf"], ask=ask, rerank=rerank)
check("deleted target is noticed", res3b["chunks"] == 6 and g.props["d1p2"]["external"] == ["JIS Z 9999"] and len(chunk_links(g)) == 18, (res3b["chunks"], g.props["d1p2"].get("external"), len(chunk_links(g))))

# --- LLM が壊れた出力を返す（抽出の段）: 既存のリンクを消さない
g2 = Graph(make_chunks(2))
cr.link_chunk_references(g2, ask=ask, rerank=rerank)
before = len(g2.links)
res4 = cr.link_chunk_references(g2, ask=lambda p: "サーバーエラー", rerank=rerank, reextract=True)
check("broken extraction keeps links", len(g2.links) == before and len(res4["failed"]) == 6 and len(res4["skipped_documents"]) == 2, (len(g2.links), before, res4["skipped_documents"]))

# --- 特定の段で読めない答えが返る: 既存のリンクを消さず、失敗として数える
def ask_bad_resolve(prompt):
    return ask(prompt) if prompt.startswith("次の本文は") else "わかりません"


res5 = cr.link_chunk_references(g2, ask=ask_bad_resolve, rerank=rerank, reresolve=True)
check("unreadable resolution keeps links and is reported", len(g2.links) == before and any(f["stage"] == "resolve" for f in res5["failed"]), (len(g2.links), before, res5["failed"][:1]))

# --- LLM が止まっている: 途中で打ち切り、既存のリンクを消さない
def ask_down(prompt):
    raise ConnectionError("connection refused")


res6 = cr.link_chunk_references(g2, ask=ask_down, rerank=rerank, reresolve=True)
check("LLM down aborts and keeps links", len(g2.links) == before and res6["aborted"] and res6["llm_calls"] == 5, (len(g2.links), before, res6["aborted"], res6["llm_calls"]))
res7 = cr.link_chunk_references(g2, ask=ask, rerank=rerank)
check("recovers on the next run", len(g2.links) == before and not res7["failed"], (len(g2.links), before, res7["failed"][:1]))

# --- 300 文書: 文書の一覧が長くならない、止まらない
g3 = Graph(make_chunks(300))
prompts.clear()
res8 = cr.link_chunk_references(g3, ask=ask, rerank=rerank)
doc_prompts = [p for p in prompts if "が指している文書を" in p]
check("300 docs complete", not res8["failed"] and len(res8["references"]) == 1200, (len(res8["failed"]), len(res8["references"])))
check("document prompt bounded", max(len(p) for p in doc_prompts) < 1500, max(len(p) for p in doc_prompts))
ok_other = sum(1 for r in res8["references"] if r["scope"] == "other_document" and r["targets"])
check("300 docs other-document links", ok_other == 300, ok_other)
print("llm calls: 6 docs", n_first, "/ 300 docs", res8["llm_calls"], "| summary:", json.dumps(res8["summary"], ensure_ascii=False))
print("ALL OK" if ok else "SOME FAILED")
