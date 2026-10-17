"""単体の試験: 参照のたどり方（選び方の並び、まとめ方、下限、深さ）、文脈への差し込み、設定、応答の控え。

LLM・リランカー・グラフは差し替える（fakes.py）。ネットワークなしで動く。
  tests/run.sh unit            全部
  tests/run.sh unit <名前の一部>  名前に一致する試験だけ
既定の設定で従来と同じ結果になることは、本体のリポジトリの rerank_retriever.py（/orig_src）を読み込み、同じ入力で比べて確かめる。
"""
import importlib.util
import os
import sys
import traceback

for key in [k for k in os.environ if k.startswith("CHAT_")]:
    del os.environ[key]
os.environ.setdefault("VLLM_EMBEDDING_ENDPOINT", "http://127.0.0.1:9/v1,dummy")
sys.path.insert(0, "/tests")

from fakes import FakeCandidates, FakeGraph, FakeReranker
from src import rerank_retriever as rr
from src.shared import constants

ORIG_PATH = os.environ.get("ORIG_SRC", "/orig_src") + "/rerank_retriever.py"
_spec = importlib.util.spec_from_file_location("orig_rerank_retriever", ORIG_PATH)
orig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orig)

Q = "最高回転まで回せない場合はどうするか"
Q1 = "回せない場合の取扱いは、B の第 9 条による。"
Q_BACK = "運転後は 5.1 をやり直す。"
Q_DOC = "記録の保存は C による。"
Q_HIST = "F 版: 5.2 を改め、B の第 7 条による取扱いを追加した。"
Q_CONT = "圧力は D の 3. による。"
Q_TABLE = "期限は別表 1 による。"
Q_FORM = "様式は C の第 3 条による。"


def corpus():
    chunks = {
        "a0": {"doc": "A.md", "position": 0, "section": "A > 5.1 性能試験", "text": "[A > 5.1 性能試験]\n性能試験の方法を定める。", "ents": ["Test:性能試験"]},
        "a1": {"doc": "A.md", "position": 1, "section": "A > 5.2 全力運転", "ents": ["Test:全力運転"], "rels": ["Test:全力運転 FOLLOWS Test:性能試験"],
               "text": f"[A > 5.2 全力運転]\n### 5.2 全力運転\n\n最高回転で運転する。{Q1}{Q_BACK}"},
        "a2": {"doc": "A.md", "position": 2, "section": "A > 6 記録", "text": f"[A > 6 記録]\n結果は成績表に記入する。{Q_DOC}"},
        "a3": {"doc": "A.md", "position": 9, "section": "A > 改正履歴", "text": f"[A > 改正履歴]\n{Q_HIST}"},
        "a4": {"doc": "A.md", "position": 4, "section": "A > 7 潤滑", "text": f"[A > 7 潤滑]\n{Q_CONT}"},
        "b7": {"doc": "B.md", "position": 7, "section": "B > 第 7 条", "text": f"[B > 第 7 条]\n処置を決める。{Q_TABLE}"},
        "b9": {"doc": "B.md", "position": 9, "section": "B > 第 9 条", "ents": ["Board:審査会"], "text": f"[B > 第 9 条]\n審査会の承認を受ける。{Q_TABLE}{Q_FORM}"},
        "bt": {"doc": "B.md", "position": 16, "section": "B > 別表 1", "text": "[B > 別表 1]\n| 甲 | 19 稼働日 |"},
        "c1": {"doc": "C.md", "position": 1, "section": "C > 第 1 条", "text": "[C > 第 1 条]\n目的を定める。"},
        "c2": {"doc": "C.md", "position": 2, "section": "C > 第 2 条", "text": "[C > 第 2 条]\n成績表の保存は 29 年とする。", "ents": ["Form:成績表"]},
        "c3": {"doc": "C.md", "position": 3, "section": "C > 第 3 条", "text": "[C > 第 3 条]\n様式 TCF-240 を用いる。"},
        "d1": {"doc": "D.md", "position": 1, "section": "D > 3. 供給条件", "text": "[D > 3. 供給条件]\n区分 EP の圧力は 149 kPa。"},
        "d2": {"doc": "D.md", "position": 2, "section": "D > 3. 供給条件", "text": "[D > 3. 供給条件]\n区分 EC の圧力は 219 kPa。"},
        "d3": {"doc": "D.md", "position": 3, "section": "D > 3. 供給条件", "text": "[D > 3. 供給条件]\n区分 ER の圧力は 329 kPa。"},
    }
    links = [
        {"source": "a1", "target": "b9", "quote": Q1, "locator": "第 9 条", "form": "文書番号＋項"},
        {"source": "a1", "target": "a0", "quote": Q_BACK, "locator": "5.1", "form": "同じ文書の項"},
        {"source": "a2", "doc": "C.md", "quote": Q_DOC, "locator": "", "form": "文書番号のみ"},
        {"source": "a3", "target": "b7", "quote": Q_HIST, "locator": "第 7 条", "form": "文書番号＋項", "source_section": "revision_history"},
        {"source": "a4", "target": "d1", "quote": Q_CONT, "locator": "3.", "form": "文書番号＋項"},
        {"source": "a4", "target": "d2", "quote": Q_CONT, "locator": "3.", "form": "文書番号＋項"},
        {"source": "a4", "target": "d3", "quote": Q_CONT, "locator": "3.", "form": "文書番号＋項"},
        {"source": "b9", "target": "bt", "quote": Q_TABLE, "locator": "別表 1", "form": "別表・図・添付資料"},
        {"source": "b9", "target": "c3", "quote": Q_FORM, "locator": "第 3 条", "form": "文書番号＋項"},
        {"source": "b7", "target": "bt", "quote": Q_TABLE, "locator": "別表 1", "form": "別表・図・添付資料"},
    ]
    return chunks, links


def build(module, hits, rules, settings=None, graph=None, document_names=None):
    """差し替えたグラフ・候補・リランカーで retriever を作る。module は写し（rr）か本体（orig）。"""
    if graph is None:
        graph = FakeGraph(*corpus())
    reranker = rules if isinstance(rules, FakeReranker) else FakeReranker(rules)
    module._rerank = reranker
    resolved = module.resolve_retrieval_settings(settings or {})
    retriever = module.RerankGraphRetriever(
        neo_db=graph, candidate_db=FakeCandidates(graph, hits), retrieval_query=rr.build_expansion_query(rr.resolve_retrieval_settings()),
        document_names=document_names, candidate_k=20, top_n=6, char_budget=14000, score_threshold=0.0, settings=resolved)
    return retriever, graph, reranker


def run(retriever, query=Q):
    return retriever._get_relevant_documents(query, run_manager=None)


LINK = {"reference_select": "link", "reference_max": 3}
# 候補の本文の点数（質問との近さ）。参照先の本文は質問と似ていない（0 点）という、参照をたどる必要のある場面
BASE_RULES = [(None, "### 5.2 全力運転", 0.95), (None, "[A > 6 記録]\n結果は", 0.40), (None, "[A > 7 潤滑]\n圧力は", 0.20), (None, "[A > 改正履歴]\nF 版", 0.10)]
HITS = [("a1", 1.0), ("a2", 0.9), ("a4", 0.8), ("a3", 0.7)]


def trace_of(docs):
    return rr.collect_reference_trace(docs)


# ───────────────────────── 設定 ─────────────────────────
def test_設定の一覧の形と既定値():
    for spec in rr.RETRIEVAL_SETTING_SPECS:
        assert len(spec) == 9, spec[0]
        key, kind, env, default, lo, hi, group, label, text = spec
        assert env.startswith("CHAT_") and group and label and text
        if kind in ("int", "float"):
            assert lo is not None and hi is not None and lo <= default <= hi, key
    d = rr.default_retrieval_settings()
    assert d["reference_max"] == 0 and d["reference_select"] == "target" and d["reference_presentation"] == "separate"
    assert d["reference_prompt"] is False and d["reference_depth"] == 1 and d["reference_min_score"] == 0.3
    assert d["reference_documents"] is True and d["reference_history_weight"] == 1.0, "改正履歴の重みの既定は 1（本文と同じ扱い）"
    # 従来の設定は、名前・既定値・範囲とも変えていない
    old = {s[0]: s[:6] for s in orig.RETRIEVAL_SETTING_SPECS}
    new = {s[0]: s[:6] for s in rr.RETRIEVAL_SETTING_SPECS}
    assert all(new[k] == v for k, v in old.items()), "従来の設定の定義が変わっている"
    assert [s[0] for s in rr.RETRIEVAL_SETTING_SPECS][:len(old)] == [s[0] for s in orig.RETRIEVAL_SETTING_SPECS], "従来の設定の並びが変わっている"


def test_設定の上書きと不正な値():
    s = rr.resolve_retrieval_settings({"reference_select": "link", "reference_depth": 3, "reference_presentation": "bogus", "reference_prompt": "true",
                                       "reference_min_score": "0.45", "reference_history_weight": 2})
    assert s["reference_select"] == "link"
    assert s["reference_depth"] == 1, "範囲の外（3）は既定値のまま"
    assert s["reference_presentation"] == "separate", "選択肢に無い値は既定値のまま"
    assert s["reference_prompt"] is True and s["reference_min_score"] == 0.45 and s["reference_history_weight"] == 1.0
    assert rr.resolve_retrieval_settings({"reference_select": "both"})["reference_select"] == "both"
    os.environ["CHAT_REFERENCE_SELECT"] = "bogus"
    os.environ["CHAT_REFERENCE_DEPTH"] = "2"
    try:
        d = rr.default_retrieval_settings()
        assert d["reference_select"] == "target" and d["reference_depth"] == 2
        os.environ["CHAT_REFERENCE_SELECT"] = "link"
        assert rr.default_retrieval_settings()["reference_select"] == "link"
        # 選択肢に無い値を既定値に戻すのは、参照の設定だけ。従来の項目（entity_order）は、従来どおり値をそのまま持つ
        os.environ["CHAT_ENTITY_ORDER"] = "bogus"
        assert rr.default_retrieval_settings()["entity_order"] == orig.default_retrieval_settings()["entity_order"] == "bogus"
        assert rr.build_expansion_query(rr.default_retrieval_settings()) == orig.build_expansion_query(orig.default_retrieval_settings())
    finally:
        del os.environ["CHAT_REFERENCE_SELECT"], os.environ["CHAT_REFERENCE_DEPTH"]
        os.environ.pop("CHAT_ENTITY_ORDER", None)


def test_画面に出す定義():
    spec = {item["key"]: item for item in rr.retrieval_settings_spec()}
    assert spec["reference_select"]["type"] == "choice" and [c["value"] for c in spec["reference_select"]["choices"]] == ["target", "link", "both"]
    assert [c["value"] for c in spec["reference_presentation"]["choices"]] == ["separate", "inline"]
    assert spec["reference_prompt"]["type"] == "bool" and spec["reference_depth"]["min"] == 1 and spec["reference_depth"]["max"] == 2
    assert all(item["help"] and item["label"] and item["group"] for item in spec.values())
    assert all("," not in c["label"] and "=" not in c["label"] for item in spec.values() for c in item.get("choices", []))


# ───────────────────────── 既定は従来と同じ ─────────────────────────
def test_既定の設定は従来と同じ結果():
    # 従来の選び方では、参照先の本文と質問の近さで並ぶ。参照先に点数を付けて、並びと件数の切り方まで同じかを見る
    rules = BASE_RULES + [(None, "[B > 第 9 条]", 0.05), (None, "[A > 5.1 性能試験]", 0.30), (None, "[B > 第 7 条]", 0.02), (None, "区分 EC", 0.25), (None, "区分 EP", 0.15)]
    for reference_max in (0, 1, 2, 10):
        a, _, _ = build(orig, HITS, rules, {"reference_max": reference_max})
        expected = run(a)
        b, graph, _ = build(rr, HITS, rules, {"reference_max": reference_max})
        got = run(b)
        assert len(expected) == len(got) and len(got) > 0
        for x, y in zip(expected, got):
            assert x.page_content == y.page_content, f"reference_max={reference_max}: 本文が違う"
            assert x.state == y.state
            assert x.metadata == {k: v for k, v in y.metadata.items() if k != "references"}
        ids = [e["chunk_id"] for e in trace_of(got)]
        assert len(ids) == min(reference_max, 6), ids
        assert not any("r.quote AS quote" in c for c, _ in graph.calls), "従来の選び方で、リンクごとの照会を流している"
    if True:
        # 従来の選び方では、見せ方の設定を inline にしても並びは変わらない（どのリンクで届いたかを持たないため）
        b, _, _ = build(rr, HITS, rules, {"reference_max": 2, "reference_presentation": "inline"})
        a, _, _ = build(orig, HITS, rules, {"reference_max": 2})
        assert [d.page_content for d in run(b)] == [d.page_content for d in run(a)]


# ───────────────────────── 参照の一文 ─────────────────────────
def test_参照の一文に見出しと直前の文を付ける():
    text = corpus()[0]["a1"]["text"]
    assert rr.reference_passage(text, "A > 5.2 全力運転", Q1) == f"A > 5.2 全力運転: 最高回転で運転する。 {Q1}"
    assert rr.reference_passage(text, "A > 5.2 全力運転", Q_BACK) == f"A > 5.2 全力運転: {Q1} {Q_BACK}"
    assert rr.reference_passage(text, None, Q1) == f"最高回転で運転する。 {Q1}"
    assert rr.reference_passage("本文に無い", "見出し", "別の文による。") == "見出し: 別の文による。"
    assert rr.reference_passage("この場合は、注 2 による。", "", "この場合は、注 2 による。") == "この場合は、注 2 による。"
    assert rr.reference_passage("| 型式 | 値 |\n| RK18 | 37,892（注 3） |", "表", "| RK18 | 37,892（注 3） |") == "表: | 型式 | 値 | | RK18 | 37,892（注 3） |"
    long_prev = "あ" * 500 + "。" + "い" * 400 + "条件は" + "、注 1 による。"
    assert len(rr.reference_passage(long_prev, "", "、注 1 による。")) <= rr.REFERENCE_PREVIOUS_CHARS + 1 + len("、注 1 による。")
    assert rr.reference_passage(None, None, None) == ""
    # 直前の文が、同じチャンクの別の参照の一文なら付けない（その次の文の参照まで高い点数にしない）
    assert rr.reference_passage(text, "A > 5.2 全力運転", Q_BACK, {Q1}) == f"A > 5.2 全力運転: {Q_BACK}"
    assert rr.reference_passage(text, "A > 5.2 全力運転", Q1, {Q_BACK}) == f"A > 5.2 全力運転: 最高回転で運転する。 {Q1}"


def test_質問に合う一文の次の文にある参照は足さない():
    # a1 の本文: 「…。{Q1}{Q_BACK}」。質問に合うのは Q1 だけ。Q_BACK の直前の文は Q1（別の参照の一文）なので付けず、Q_BACK だけで測る
    rules = BASE_RULES + [(None, Q1, 0.9)]
    retriever, _, reranker = build(rr, [("a1", 1.0)], rules, LINK)
    assert [e["chunk_id"] for e in trace_of(run(retriever))] == ["b9"]
    scored = [x for _, texts in reranker.calls for x in texts if Q_BACK in x and "[A" not in x]
    assert scored == [f"A > 5.2 全力運転: {Q_BACK}"], scored
    # 参照先が既に選ばれていてたどらないリンクの一文でも、「別の参照の一文」として数える
    retriever, _, reranker = build(rr, [("a1", 1.0), ("b9", 0.5)], rules + [(None, "[B > 第 9 条]", 0.5)], LINK)
    assert trace_of(run(retriever)) == []
    scored = [x for _, texts in reranker.calls for x in texts if Q_BACK in x and "[A" not in x]
    assert scored == [f"A > 5.2 全力運転: {Q_BACK}"], scored


# ───────────────────────── 選び方 ─────────────────────────
def test_参照の一文の点数の順に足し_下限未満は足さない():
    rules = BASE_RULES + [(None, Q1, 0.9), (None, Q_BACK, 0.8), (None, Q_CONT, 0.6), (None, Q_HIST, 0.9)]
    HALF = {**LINK, "reference_history_weight": 0.5}
    retriever, graph, reranker = build(rr, HITS, rules, HALF)
    t = trace_of(run(retriever))
    # a1→b9（0.9）、a1→a0（0.8）、a4→d?（0.6）。改正履歴の a3→b7 は 0.9×0.5=0.45 で 4 番目なので、上限 3 件に入らない
    assert [e["chunk_id"] for e in t[:2]] == ["b9", "a0"] and t[2]["chunk_id"] in ("d1", "d2", "d3"), [e["chunk_id"] for e in t]
    assert [e["score"] for e in t] == [0.9, 0.8, 0.6]
    assert all(e["hop"] == 1 and e["select"] == "link" for e in t) and [e["order"] for e in t] == [1, 2, 3]
    assert t[0]["from_chunk_id"] == "a1" and t[0]["quote"] == Q1 and t[0]["locator"] == "第 9 条" and t[0]["target_kind"] == "chunk"
    # 下限を上げると、0.6 の参照は足さない
    retriever, _, _ = build(rr, HITS, rules, {**HALF, "reference_min_score": 0.7})
    assert [e["chunk_id"] for e in trace_of(run(retriever))] == ["b9", "a0"]
    # 質問と参照の一文が合わない（どの一文も下限未満）なら、何も足さない
    retriever, _, _ = build(rr, HITS, BASE_RULES + [(None, Q1, 0.2)], LINK)
    docs = run(retriever)
    assert trace_of(docs) == [] and all("references" not in d.metadata for d in docs)


def test_参照先の本文と質問の近さは基準にしない():
    # 参照先 a0 の本文は質問に近い（0.99）が、参照の一文は合っていない。参照先 b9 の本文は質問に遠い（0）が、一文は合っている
    rules = [(None, "### 5.2 全力運転", 0.95), (None, "[A > 5.1 性能試験]", 0.99), (None, Q_BACK, 0.0), (None, Q1, 0.8)]
    retriever, _, _ = build(rr, [("a1", 1.0)], rules + [], {**LINK, "reference_max": 1})
    assert [e["chunk_id"] for e in trace_of(run(retriever))] == ["b9"]
    legacy, _, _ = build(rr, [("a1", 1.0)], rules, {"reference_max": 1})
    assert [e["chunk_id"] for e in trace_of(run(legacy))] == ["a0"], "従来の選び方は、質問に近い本文の a0 を足す"


def test_既に選ばれているチャンクは足さない():
    rules = BASE_RULES + [(None, "[B > 第 9 条]", 0.5), (None, Q_BACK, 0.0), (None, Q1, 0.9)]
    retriever, _, _ = build(rr, [("a1", 1.0), ("b9", 0.9)], rules, LINK)
    docs = run(retriever)
    assert trace_of(docs) == [], "b9 は候補として選ばれているので、参照先としては足さない"
    ids = [d["id"] for doc in docs for d in doc.metadata["chunkdetails"]]
    assert sorted(ids) == ["a1", "b9"]


def test_同じ参照先へのリンクは1つにまとめる():
    chunks, links = corpus()
    chunks["a5"] = {"doc": "A.md", "position": 5, "section": "A > 8 聴音", "text": "[A > 8 聴音]\n接触音を認めた場合は、B の第 9 条により処置する。"}
    links.append({"source": "a5", "target": "b9", "quote": "接触音を認めた場合は、B の第 9 条により処置する。", "locator": "第 9 条"})
    rules = BASE_RULES + [(None, "[A > 8 聴音]\n接触音", 0.3), (None, Q_BACK, 0.0), (None, "接触音を認めた場合は", 0.7), (None, Q1, 0.9)]
    retriever, _, _ = build(rr, [("a1", 1.0), ("a5", 0.9)], rules, LINK, graph=FakeGraph(chunks, links))
    t = trace_of(run(retriever))
    assert [e["chunk_id"] for e in t] == ["b9"], "同じチャンクへ 2 本のリンクがあっても、足すのは 1 回"
    assert t[0]["from_chunk_id"] == "a1" and t[0]["score"] == 0.9, "点数の高い方のリンクを控えに残す"


def test_項が複数のチャンクに続く参照は1つだけ足す():
    # a4 →（d1, d2, d3）は同じ一文・同じ場所の語。候補の中から、質問と参照の一文に合う本文のもの（区分 EC）を選ぶ
    rules = BASE_RULES + [("区分 EC の場合", "区分 EC の圧力", 0.8), ("区分 EC の場合", "区分 EP の圧力", 0.3), (None, Q_CONT, 0.6)]
    retriever, _, reranker = build(rr, [("a4", 1.0)], rules, LINK)
    t = trace_of(run(retriever, "区分 EC の場合の圧力はいくらか"))
    assert [e["chunk_id"] for e in t] == ["d2"], [e["chunk_id"] for e in t]
    aux = [q for q, texts in reranker.calls if any("区分 EP の圧力" in x for x in texts)]
    assert aux and all(Q_CONT in q for q in aux), "続きのチャンクの選び分けは、質問と参照の一文の両方で測る"


def test_同点の参照は参照元の順位の高い方から():
    chunks, links = corpus()
    rules = [(None, "[A > 7 潤滑]\n圧力は", 0.9), (None, "### 5.2 全力運転", 0.5), (None, Q_BACK, 0.0), (None, Q1, 0.6), (None, Q_CONT, 0.6)]
    retriever, _, _ = build(rr, [("a1", 1.0), ("a4", 0.9)], rules, {**LINK, "reference_max": 1})
    t = trace_of(run(retriever))
    assert len(t) == 1 and t[0]["from_chunk_id"] == "a4", "同点（0.6）なら、順位の高い参照元（a4）の参照を先にたどる"


def test_改正履歴の中の参照は重みを掛ける():
    rules = BASE_RULES + [(None, Q_HIST, 0.5)]
    retriever, _, _ = build(rr, HITS, rules, {**LINK, "reference_history_weight": 0.5})
    assert trace_of(run(retriever)) == [], "0.5 × 0.5 = 0.25 は下限 0.3 未満"
    retriever, _, _ = build(rr, HITS, rules, LINK)      # 既定の重みは 1（本文と同じ扱い）
    t = trace_of(run(retriever))
    assert [e["chunk_id"] for e in t] == ["b7"] and t[0]["source_section"] == "revision_history" and t[0]["score"] == 0.5
    retriever, _, _ = build(rr, HITS, BASE_RULES + [(None, Q_HIST, 1.0)], {**LINK, "reference_history_weight": 0.0})
    assert trace_of(run(retriever)) == [], "重み 0 なら、改正履歴からはたどらない"


def test_文書へのリンクは文書の中から1つ選ぶ():
    rules = BASE_RULES + [(Q_DOC, "成績表の保存は 29 年", 0.7), (Q_DOC, "様式 TCF-240", 0.2), (None, Q_DOC, 0.8)]
    retriever, graph, reranker = build(rr, HITS, rules, LINK)
    t = trace_of(run(retriever))
    assert [e["chunk_id"] for e in t] == ["c2"], [e["chunk_id"] for e in t]
    assert t[0]["target_kind"] == "document" and t[0]["from_chunk_id"] == "a2" and t[0]["quote"] == Q_DOC and t[0]["score"] == 0.8
    inside = [q for q, texts in reranker.calls if any("様式 TCF-240" in x for x in texts)]
    assert inside and all(Q in q and Q_DOC in q for q in inside), "文書の中のチャンクは、質問と参照の一文の両方を問いにして選ぶ"
    # 文書の中に合うチャンクが無ければ（下限未満）、足さない
    retriever, _, _ = build(rr, HITS, BASE_RULES + [(Q_DOC, "成績表の保存は 29 年", 0.1), (None, Q_DOC, 0.8)], LINK)
    assert trace_of(run(retriever)) == []
    # 設定で、文書だけを指す参照をたどらないようにできる
    retriever, graph, _ = build(rr, HITS, rules, {**LINK, "reference_documents": False})
    assert trace_of(run(retriever)) == [] and not any("$per_document" in c for c, _ in graph.calls)


def test_絞り込みの外の文書へはたどらない():
    rules = BASE_RULES + [(None, Q_BACK, 0.9), (None, Q1, 0.9)]
    retriever, _, _ = build(rr, HITS, rules, LINK, document_names=["A.md"])
    assert [e["chunk_id"] for e in trace_of(run(retriever))] == ["a0"], "B.md の b9 は絞り込みの外"


# ───────────────────────── 2 回目のたどり ─────────────────────────
HOP2_RULES = BASE_RULES + [(None, Q_BACK, 0.0), (None, Q1, 0.9), (Q1, Q_FORM, 0.4), (Q1, Q_TABLE, 0.8)]


def test_深さ1では2回目をたどらない():
    retriever, _, _ = build(rr, [("a1", 1.0)], HOP2_RULES, LINK)
    assert [e["chunk_id"] for e in trace_of(run(retriever))] == ["b9"]


def test_深さ2で参照先の中の参照をたどる():
    retriever, _, reranker = build(rr, [("a1", 1.0)], HOP2_RULES, {**LINK, "reference_depth": 2})
    t = trace_of(run(retriever))
    # 2 回目の点数 = 親の点数 × 2 回目の参照の一文の近さ: bt は 0.9 × 0.8 = 0.72、c3 は 0.9 × 0.4 = 0.36。
    # どちらも下限以上だが、親 1 つにつき足すのは点数の最も高い 1 件（bt）だけ
    assert [(e["chunk_id"], e["hop"]) for e in t] == [("b9", 1), ("bt", 2)], [(e["chunk_id"], e["hop"]) for e in t]
    assert abs(t[1]["score"] - 0.72) < 1e-9 and t[1]["from_chunk_id"] == "b9" and t[1]["quote"] == Q_TABLE and t[1]["link_score"] == 0.8
    second = [q for q, texts in reranker.calls if any(Q_TABLE in x and "[B" not in x for x in texts)]
    assert second and all(q.startswith(Q) and Q1 in q for q in second), "2 回目の問いは、質問に 1 回目の参照の一文を足したもの"


def test_2回目の点数は親の点数を引き継ぎ_下限未満は足さない():
    rules = BASE_RULES + [(None, Q_BACK, 0.0), (None, Q1, 0.5), (Q1, Q_FORM, 0.5), (Q1, Q_TABLE, 0.5)]
    retriever, _, _ = build(rr, [("a1", 1.0)], rules, {**LINK, "reference_depth": 2})
    assert [e["chunk_id"] for e in trace_of(run(retriever))] == ["b9"], "0.5 × 0.5 = 0.25 は下限 0.3 未満"
    retriever, _, _ = build(rr, [("a1", 1.0)], rules, {**LINK, "reference_depth": 2, "reference_min_score": 0.2})
    assert [e["chunk_id"] for e in trace_of(run(retriever))] == ["b9", "bt"], "下限以上が 2 件あっても、親 1 つにつき 1 件"


def test_2回目は親1つにつき1件ずつ足す():
    # 1 回目で b9（0.9）と b7（0.8）を足す。b9 の中には参照が 2 つ（bt, c3）、b7 の中にも 2 つ（bt, c1）。
    # b9 から 1 件（bt）→ b7 から 1 件（bt は取得済みなので、次の c1）。b9 の 2 件目（c3）は足さない
    chunks, links = corpus()
    chunks["a1"]["text"] += "不合格の場合は、B の第 7 条による。"
    links.append({"source": "a1", "target": "b7", "quote": "不合格の場合は、B の第 7 条による。", "locator": "第 7 条"})
    chunks["b7"]["text"] += "手続は C の第 1 条による。"
    links.append({"source": "b7", "target": "c1", "quote": "手続は C の第 1 条による。", "locator": "第 1 条"})
    rules = BASE_RULES + [(None, "不合格の場合は、B の第 7 条による。", 0.8), (None, Q_BACK, 0.0), (None, Q1, 0.9),
                          (None, "手続は C の第 1 条による。", 0.6), (None, Q_FORM, 0.9), (None, Q_TABLE, 1.0)]
    retriever, _, _ = build(rr, [("a1", 1.0)], rules, {**LINK, "reference_max": 2, "reference_depth": 2}, graph=FakeGraph(chunks, links))
    t = trace_of(run(retriever))
    first = [e["chunk_id"] for e in t if e["hop"] == 1]
    second = [(e["chunk_id"], e["from_chunk_id"]) for e in t if e["hop"] == 2]
    assert first == ["b9", "b7"], first
    # b9 の子: bt（0.9×1.0）、c3（0.9×0.9）。b7 の子: bt（0.8×1.0。b9 から取得済みなので飛ばす）、c1（0.8×0.6）。
    # 点数の順だけなら bt, c3（どちらも b9 の子）になるところを、親 1 つにつき 1 件なので bt, c1 になる
    assert second == [("bt", "b9"), ("c1", "b7")], second
    assert len(t) == 4, "1 回目 2 件 + 2 回目 2 件"
    # 上限に余裕があっても（上限 5）、親 1 つにつき 1 件まで。c3 は足さない
    retriever, _, _ = build(rr, [("a1", 1.0)], rules, {**LINK, "reference_max": 5, "reference_depth": 2}, graph=FakeGraph(chunks, links))
    t = trace_of(run(retriever))
    assert [(e["chunk_id"], e["from_chunk_id"]) for e in t if e["hop"] == 2] == [("bt", "b9"), ("c1", "b7")], [(e["chunk_id"], e["hop"]) for e in t]


def test_2回目は文書だけを指す参照をたどらない():
    chunks, links = corpus()
    chunks["b9"]["text"] = f"[B > 第 9 条]\n審査会の承認を受ける。{Q_DOC}"
    links = [l for l in links if l["source"] != "b9"] + [{"source": "b9", "doc": "C.md", "quote": Q_DOC, "locator": ""}]
    rules = BASE_RULES + [(None, Q_BACK, 0.0), (None, Q1, 0.9), (None, Q_DOC, 0.9), (None, "成績表の保存は 29 年", 0.9)]
    retriever, graph, _ = build(rr, [("a1", 1.0)], rules, {**LINK, "reference_depth": 2}, graph=FakeGraph(chunks, links))
    assert [e["chunk_id"] for e in trace_of(run(retriever))] == ["b9"]
    assert not any("$per_document" in c for c, _ in graph.calls)


# ───────────────────────── 文脈への差し込み ─────────────────────────
def test_従来の並びでは参照先は自分の文書の行に入る():
    retriever, _, _ = build(rr, [("a1", 1.0)], HOP2_RULES, {**LINK, "reference_depth": 2})
    docs = run(retriever)
    by = {d.metadata["source"]: d for d in docs}
    assert "参照先（" not in "".join(d.page_content for d in docs)
    assert by["A.md"].page_content.startswith("Text Content:\n[A > 5.2 全力運転]") and "[B > 第 9 条]" not in by["A.md"].page_content
    assert "[B > 第 9 条]" in by["B.md"].page_content and "[B > 別表 1]" in by["B.md"].page_content and "C.md" not in by
    assert {e["chunk_id"]: e["shown_in"] for e in trace_of(docs)} == {"b9": "B.md", "bt": "B.md"}
    assert all(e["source"] == "B.md" for e in trace_of(docs)), "控えの source は、足したチャンクの文書"


def test_参照元の直後に見出しを付けて置く():
    chunks = corpus()[0]
    rules = HOP2_RULES + [(None, "[A > 6 記録]", 0.3)]
    separate, _, _ = build(rr, [("a1", 1.0), ("a2", 0.5)], rules, {**LINK, "reference_depth": 2})
    before = {d.metadata["source"]: d for d in run(separate)}
    inline, _, _ = build(rr, [("a1", 1.0), ("a2", 0.5)], rules, {**LINK, "reference_depth": 2, "reference_presentation": "inline"})
    docs = run(inline)
    after = {d.metadata["source"]: d for d in docs}
    a = after["A.md"].page_content
    head, tail = a.split("\n----\nEntities:\n", 1)
    expected = ("Text Content:\n" + chunks["a1"]["text"]
                + f"\n\n参照先（上の本文の「{Q1}」が指す箇所。出典: B.md）:\n" + chunks["b9"]["text"]
                + f"\n\n参照先（上の参照先の「{Q_TABLE}」が指す箇所。出典: B.md）:\n" + chunks["bt"]["text"]
                + "\n----\n" + chunks["a2"]["text"])
    assert head == expected, head
    # エンティティとリレーションの行は変えない
    assert tail == before["A.md"].page_content.split("\n----\nEntities:\n", 1)[1]
    for name in ("A.md", "B.md"):
        assert after[name].page_content.partition("\n----\nEntities:\n")[1:] == before[name].page_content.partition("\n----\nEntities:\n")[1:]
        drop = ("references", "chunkdetails")
        assert {k: v for k, v in after[name].metadata.items() if k not in drop} == {k: v for k, v in before[name].metadata.items() if k not in drop}
    # チャンクの項目（chunkdetails）は、本文と一緒に、示した文書行へ移す。並びは本文の並びと同じ
    assert [d["id"] for d in after["A.md"].metadata["chunkdetails"]] == ["a1", "b9", "bt", "a2"]
    assert after["B.md"].metadata["chunkdetails"] == []
    every = lambda rows: sorted((d["id"], d["score"]) for doc in rows.values() for d in doc.metadata["chunkdetails"])
    assert every(after) == every(before), "項目の中身（ID と点数）は変えない。置き場所だけが変わる"
    # 参照先は元の文書の行の本文からは外し、断り書きを置く（同じ本文を 2 回入れない）。行（エンティティ）が残る文書行は残す
    assert after["B.md"].page_content.split("\n----\nEntities:\n", 1)[0] == "Text Content:\n" + rr.REFERENCE_MOVED_NOTE
    assert "Board:審査会" in after["B.md"].page_content
    whole = "\n".join(d.page_content for d in docs)
    for chunk_id in ("b9", "bt"):
        assert whole.count(chunks[chunk_id]["text"]) == 1
    # 文書行の並び: 直接選ばれた本文の行が先、断り書きだけの行は 0 点で末尾
    assert [d.metadata["source"] for d in docs] == ["A.md", "B.md"]
    assert after["A.md"].state["query_similarity_score"] == 0.95 and after["B.md"].state["query_similarity_score"] == 0.0
    # 控え: 文脈のどの文書行に示したか
    assert {e["chunk_id"]: e["shown_in"] for e in trace_of(docs)} == {"b9": "A.md", "bt": "A.md"}
    assert [e["chunk_id"] for e in after["A.md"].metadata["references"]] == ["b9", "bt"] and "references" not in after["B.md"].metadata


def test_足したチャンクの並べるための点数は参照元の点数を超えない():
    # 参照元 a1 の点数 0.95、参照の一文 0.9、2 回目 0.8。並べるための点数（chunkdetails の score）= 0.95 × 0.9、0.95 × 0.9 × 0.8
    retriever, _, _ = build(rr, [("a1", 1.0)], HOP2_RULES, {**LINK, "reference_depth": 2})
    docs = run(retriever)
    score = {d["id"]: d["score"] for doc in docs for d in doc.metadata["chunkdetails"]}
    assert abs(score["b9"] - 0.95 * 0.9) < 1e-9 and abs(score["bt"] - 0.95 * 0.72) < 1e-9 and score["a1"] == 0.95, score
    assert score["a1"] > score["b9"] > score["bt"], "点数順に並べると、参照元 → 参照先 → その先 の順になる"
    t = {e["chunk_id"]: e for e in trace_of(docs)}
    assert t["b9"]["score"] == 0.9 and abs(t["bt"]["score"] - 0.72) < 1e-9, "控えの score は、たどるかどうかを決めた点数（参照の一文の近さ）のまま"
    # 従来の並び（separate）では、参照先だけの文書行は、参照元の行より後ろに並ぶ
    assert [d.metadata["source"] for d in docs] == ["A.md", "B.md"]


def test_本文も行も残らない文書行は外し_出典には残す():
    # a4 → d1〜d3（D.md。エンティティもリレーションも無い）。直後に置くと D.md の行は断り書きだけになるので、外す
    rules = BASE_RULES + [("区分 EC の場合", "区分 EC の圧力", 0.8), (None, Q_CONT, 0.6)]
    settings = {**LINK, "reference_presentation": "inline"}
    retriever, _, _ = build(rr, [("a4", 1.0)], rules, settings)
    docs = run(retriever, "区分 EC の場合の圧力はいくらか")
    assert [d.metadata["source"] for d in docs] == ["A.md"], "D.md の行は、本文（移した）も行も無いので外す"
    assert rr.REFERENCE_MOVED_NOTE not in docs[0].page_content and "区分 EC の圧力は 219 kPa" in docs[0].page_content
    assert [d["id"] for d in docs[0].metadata["chunkdetails"]] == ["a4", "d2"]
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.runnables import RunnableLambda
    from src import QA_integration as qa
    cms = {"mode": "graph_vector_fulltext", "retrieval_settings": rr.resolve_retrieval_settings(settings)}
    _content, result, _tokens, context = qa.process_documents(docs, Q, [HumanMessage(content=Q)], RunnableLambda(lambda _p: AIMessage(content="219 kPa")), "gemma4", cms)
    assert set(result["sources"]) == {"A.md", "D.md"}, "本文が文脈にある文書（D.md）は、文書行が無くても出典の一覧に入れる"
    assert sorted(d["id"] for d in result["nodedetails"]["chunkdetails"]) == ["a4", "d2"]
    assert [(e["chunk_id"], e["shown_in"], e["source"]) for e in result["references"]] == [("d2", "A.md", "D.md")]
    assert context.count("Document start") == 1


def _cutoff_world():
    """直接選ばれる 6 件が別々の文書（P1〜P6）、足される参照先 3 件も別々の文書（R1〜R3）= 従来の並びなら文書行 9 行。"""
    chunks, links, hits, rules = {}, [], [], []
    for i in range(1, 7):
        chunks[f"p{i}"] = {"doc": f"P{i}.md", "position": i, "section": f"P{i} > 本文", "text": f"[P{i} > 本文]\n直接の本文p{i}。", "ents": [f"E:p{i}"]}
        hits.append((f"p{i}", 1.0 - i / 10))
    for i, (source, quote) in enumerate((("p1", "甲の扱いは R1 による。"), ("p3", "乙の扱いは R2 による。"), ("p6", "丙の扱いは R3 による。")), start=1):
        chunks[source]["text"] += quote
        chunks[f"r{i}"] = {"doc": f"R{i}.md", "position": i, "section": f"R{i} > 表", "text": f"[R{i} > 表]\n参照先の中身r{i}。", "ents": [f"E:r{i}"]}
        links.append({"source": source, "target": f"r{i}", "quote": quote, "locator": "表"})
    return chunks, links, hits


def _in_context(docs, cutoff):
    from src import QA_integration as qa
    os.environ["CHAT_PROMPT_TOKEN_CUTOFF"] = str(cutoff)
    try:
        context, sources, _ents, _coms = qa.format_documents(docs, "gemma4", {"mode": "graph_vector_fulltext"})
    finally:
        del os.environ["CHAT_PROMPT_TOKEN_CUTOFF"]
    listed = [d["id"] for d in qa.get_sources_and_chunks(sources, docs)["chunkdetails"]]
    references = rr.collect_reference_trace(docs, sources)
    return context, set(sources) | rr.reference_sources(references), listed, [e["chunk_id"] for e in references]


def test_文書行の上限で本文と出典の一覧が食い違わない():
    chunks, links, hits = _cutoff_world()
    # 直接の本文の点数: p1 0.9 … p5 0.5、p6 は最も低い 0.05。参照の一文: 丙（p6 → r3）が最も高い 0.95
    # 候補の本文は「[P1 > 本文]\n…」で始まる。参照の一文（見出し + 直前の文 + 一文）には、この角括弧と改行の並びは出ない
    rules = [(None, "[P6 > 本文]\n", 0.05)] + [(None, f"[P{i} > 本文]\n", 1.0 - i / 10) for i in range(1, 6)] + [
        (None, "丙の扱いは R3 による。", 0.95), (None, "甲の扱いは R1 による。", 0.9), (None, "乙の扱いは R2 による。", 0.8)]
    link3 = {"reference_select": "link", "reference_max": 3}
    base_docs = run(build(rr, hits, rules, {"reference_max": 0}, graph=FakeGraph(chunks, links))[0])
    for cutoff in (8, 4, 3):
        base_context, _, base_listed, _ = _in_context(base_docs, cutoff)
        for present in ("separate", "inline"):
            docs = run(build(rr, hits, rules, {**link3, "reference_presentation": present}, graph=FakeGraph(chunks, links))[0])
            context, sources, listed, referenced = _in_context(docs, cutoff)
            for cid, c in chunks.items():
                body = c["text"] in context
                assert body == (cid in listed), f"上限 {cutoff}・{present}: {cid} の本文が文脈に {body}、出典の一覧に {cid in listed}"
                if body:
                    assert c["doc"] in sources, f"上限 {cutoff}・{present}: 本文が文脈にある {cid} の文書が出典に無い"
            assert set(referenced) <= set(listed)
            if present == "inline":
                # 直接選ばれた本文の行は、参照を足さない場合と同じものが、同じ順で残る（参照先が、直接の行を上限から押し出さない）
                direct = [cid for cid in listed if cid.startswith("p")]
                assert direct == base_listed, f"上限 {cutoff}: 直接の本文 {direct}、参照なしでは {base_listed}"
                order = lambda text: [cid for cid in sorted((c for c in chunks if c.startswith("p")), key=lambda c: text.find(chunks[c]["text"])) if chunks[cid]["text"] in text]
                assert order(context) == order(base_context)
                # 参照先は、参照元の行が文脈にあるときだけ、その直後にある
                for i, source in ((1, "p1"), (2, "p3"), (3, "p6")):
                    assert (chunks[f"r{i}"]["text"] in context) == (chunks[source]["text"] in context)
    # 上限 8・直後に置く: 文書行は 9 行（直接 6 + 断り書きだけ 3）。断り書きだけの行は末尾なので、落ちるのはその 1 行で、本文は 9 件とも入る
    docs = run(build(rr, hits, rules, {**link3, "reference_presentation": "inline"}, graph=FakeGraph(chunks, links))[0])
    assert [d.metadata["source"] for d in docs][:6] == [f"P{i}.md" for i in range(1, 7)] and len(docs) == 9
    context, sources, listed, referenced = _in_context(docs, 8)
    assert sorted(listed) == sorted(chunks) and sorted(referenced) == ["r1", "r2", "r3"] and context.count("Document start") == 8
    assert sources == {f"P{i}.md" for i in range(1, 7)} | {"R1.md", "R2.md", "R3.md"}, "行が落ちた文書（本文は参照元の直後にある）も出典に入る"
    assert context.index("直接の本文p6") < context.index(rr.REFERENCE_MOVED_NOTE), "断り書きは、直接の本文より後ろ"
    # 従来の並び（separate）で文書行 9 > 上限 8 のとき、従来の並びなら入る参照先 r3（点数は p6 0.05 × 0.95）は、直後に置いても入る
    sep = run(build(rr, hits, rules, {**link3, "reference_presentation": "separate"}, graph=FakeGraph(chunks, links))[0])
    sep_context = _in_context(sep, 8)[0]
    assert all((chunks[f"r{i}"]["text"] in context) >= (chunks[f"r{i}"]["text"] in sep_context) for i in (1, 2, 3))


def test_同じ文書の中の参照先も直後へ移す():
    rules = BASE_RULES + [(None, Q_BACK, 0.9), (None, Q1, 0.1)]
    chunks = corpus()[0]
    inline, _, _ = build(rr, [("a1", 1.0), ("a2", 0.5)], rules, {**LINK, "reference_presentation": "inline"})
    docs = run(inline)
    assert len(docs) == 1
    head = docs[0].page_content.split("\n----\nEntities:\n", 1)[0]
    assert head == ("Text Content:\n" + chunks["a1"]["text"] + f"\n\n参照先（上の本文の「{Q_BACK}」が指す箇所。出典: A.md）:\n" + chunks["a0"]["text"] + "\n----\n" + chunks["a2"]["text"])


def test_見出しの一文は長いとき後ろ側を残す():
    long_quote = "前置き" * 60 + "は、B の第 9 条による。"
    h = rr._reference_heading({"quote": long_quote, "hop": 1, "file_name": "B.md"})
    assert h.startswith("参照先（上の本文の「…") and h.endswith("は、B の第 9 条による。」が指す箇所。出典: B.md）:") and len(h) < rr.REFERENCE_QUOTE_CHARS + 60
    assert rr._reference_heading({"quote": "", "hop": 2}) == "参照先（上の参照先が参照している箇所）:"
    assert rr._reference_heading({"quote": "| RK18 |\n 37,892（注 3） |", "hop": 1}) == "参照先（上の本文の「| RK18 | 37,892（注 3） |」が指す箇所）:"


def test_文書行を組み直せないときは従来の並びのまま():
    graph = FakeGraph(*corpus())
    graph.break_rows = True
    inline, _, _ = build(rr, [("a1", 1.0)], HOP2_RULES, {**LINK, "reference_presentation": "inline"}, graph=graph)
    docs = run(inline)
    assert "参照先（" not in "".join(d.page_content for d in docs) and rr.REFERENCE_MOVED_NOTE not in "".join(d.page_content for d in docs)
    assert {e["chunk_id"]: e["shown_in"] for e in trace_of(docs)} == {"b9": "B.md"}


def test_文脈の字数の予算は差し込み後の本文で数える():
    inline, _, _ = build(rr, [("a1", 1.0)], HOP2_RULES, {**LINK, "reference_presentation": "inline"})
    inline.char_budget = 10   # 本文だけで予算を超える → 行は 1 つも入らない。本文は削られない
    docs = run(inline)
    a = [d for d in docs if d.metadata["source"] == "A.md"][0]
    assert "Entities:" not in a.page_content and "参照先（上の本文の" in a.page_content


# ───────────────────────── 失敗しても検索は続ける ─────────────────────────
def test_参照のたどりが失敗しても検索は続く():
    reranker = FakeReranker(BASE_RULES + [(None, Q1, 0.9)])
    reranker.fail_on = "最高回転で運転する。 " + Q1          # 参照の一文の採点だけ失敗させる（候補の本文には空白が入らない）
    retriever, _, _ = build(rr, [("a1", 1.0)], reranker, LINK)
    docs = run(retriever)
    assert len(docs) == 1 and trace_of(docs) == [] and "[A > 5.2 全力運転]" in docs[0].page_content


# ───────────────────────── 応答の控え ─────────────────────────
def test_控えの項目と絞り込み():
    retriever, _, _ = build(rr, [("a1", 1.0)], HOP2_RULES, {**LINK, "reference_depth": 2})
    docs = run(retriever)
    t = trace_of(docs)
    need = {"chunk_id", "from_chunk_id", "quote", "score", "hop"}
    assert all(need <= set(e) for e in t) and all(set(e) == set(rr.REFERENCE_TRACE_KEYS) for e in t)
    assert not any(k in e for e in t for k in ("text", "passage", "doc_source")), "本文は控えに入れない（チャンクの一覧にある）"
    assert [e["chunk_id"] for e in rr.collect_reference_trace(docs, {"B.md"})] == ["b9", "bt"], "文脈に入れた文書行の分だけ"
    assert rr.collect_reference_trace([], None) == [] and rr.collect_reference_trace(None) == []
    import json
    json.dumps(t, ensure_ascii=False)


def test_回答の指示の切り替え():
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    base = constants.chat_system_template(False)
    plus = constants.chat_system_template(True)
    assert base == constants.CHAT_SYSTEM_TEMPLATE
    assert plus.count(constants.CHAT_REFERENCE_GUIDELINE) == 1 and plus.replace(constants.CHAT_REFERENCE_GUIDELINE + "\n", "") .replace("\n\n\n\n**IMPORTANT**", "\n\n\n**IMPORTANT**") == base
    assert plus.index("10. **Context Availability**") < plus.index("11. **Cross-references**") < plus.index("**IMPORTANT**") < plus.index("{context}")
    assert "{" not in constants.CHAT_REFERENCE_GUIDELINE and "}" not in constants.CHAT_REFERENCE_GUIDELINE
    assert len(constants.CHAT_REFERENCE_GUIDELINE) < 600 and constants.CHAT_REFERENCE_GUIDELINE.count(". ") <= 2, "足すのは 2 文まで"
    assert "not included in the provided documents" in constants.CHAT_REFERENCE_GUIDELINE, "参照先が文脈に無いときは、含まれていないと明言させる"
    for template in (base, plus):
        prompt = ChatPromptTemplate.from_messages([("system", template), MessagesPlaceholder(variable_name="messages"), ("human", "User question: {input}")])
        assert sorted(prompt.input_variables) == ["context", "input", "messages"]


def test_回答の流れでの指示と控え():
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.runnables import RunnableLambda
    from src import QA_integration as qa
    seen = []

    def llm(prompt_value):
        seen.append(prompt_value.to_messages())
        return AIMessage(content="審査会の承認を受ける。")

    for flag in (False, True):
        settings = {**LINK, "reference_depth": 2, "reference_presentation": "inline", "reference_prompt": flag}
        retriever, _, _ = build(rr, [("a1", 1.0)], HOP2_RULES, settings)
        docs = run(retriever)
        cms = {"mode": "graph_vector_fulltext", "retrieval_settings": rr.resolve_retrieval_settings(settings)}
        content, result, _tokens, context = qa.process_documents(docs, Q, [HumanMessage(content=Q)], RunnableLambda(llm), "gemma4", cms)
        system = seen[-1][0].content
        assert ("11. **Cross-references**" in system) is flag
        assert "参照先（上の本文の「" in system and context in system
        assert content == "審査会の承認を受ける。"
        assert [(e["chunk_id"], e["from_chunk_id"], e["hop"]) for e in result["references"]] == [("b9", "a1", 1), ("bt", "b9", 2)]
        assert all(e["quote"] and e["score"] > 0 for e in result["references"])
        assert sorted(d["id"] for d in result["nodedetails"]["chunkdetails"]) == ["a1", "b9", "bt"]
        assert set(result["sources"]) == {"A.md", "B.md"}


def test_検索だけの口の応答():
    from src import retrieve_only
    captured = {}

    def fake_get(graph, document_names, chat_mode_settings, embedding_provider=None, embedding_model=None, **_):
        retriever, _, _ = build(rr, [("a1", 1.0)], HOP2_RULES, chat_mode_settings["retrieval_settings"], graph=graph)
        captured["settings"] = chat_mode_settings["retrieval_settings"]
        return retriever

    def fake_chain(_llm, retriever, _provider, _model):
        # 本物は TokenTextSplitter の準備でネットワークへ出る（tiktoken の語彙の取得）。RerankGraphRetriever のときは retriever をそのまま使う作りなので、同じ形に差し替える
        from langchain_core.runnables import RunnableLambda
        return RunnableLambda(lambda x: retriever._get_relevant_documents(x["messages"][-1].content, run_manager=None))

    original, original_chain = retrieve_only.get_neo4j_retriever, retrieve_only.create_document_retriever_chain
    retrieve_only.get_neo4j_retriever = fake_get
    retrieve_only.create_document_retriever_chain = fake_chain
    try:
        graph = FakeGraph(*corpus())
        r = retrieve_only.retrieve_context(graph, Q, document_names="[]", mode="graph_vector_fulltext", embedding_provider="vllm", embedding_model="cl-nagoya/ruri-v3-310m",
                                           retrieval_settings={**LINK, "reference_depth": 2, "reference_presentation": "inline"}, model="gemma4")
        assert set(r) == {"question", "mode", "retrieval_settings", "chunks", "entityids", "relationshipids", "references", "context"}
        assert [(e["chunk_id"], e["from_chunk_id"], e["hop"], e["quote"]) for e in r["references"]] == [("b9", "a1", 1, Q1), ("bt", "b9", 2, Q_TABLE)]
        assert {c["id"]: c["via_reference"] for c in r["chunks"]} == {"a1": False, "b9": True, "bt": True}
        assert {c["id"]: c["fileName"] for c in r["chunks"]} == {"a1": "A.md", "b9": "B.md", "bt": "B.md"}, "参照元の直後に示したチャンクの fileName は、自分の文書"
        assert [c["id"] for c in r["chunks"]] == ["a1", "b9", "bt"], "点数順に並べると、参照元 → 参照先 → その先"
        assert all(set(c) == {"id", "text", "fileName", "position", "page_number", "section", "score", "via_reference"} for c in r["chunks"]), "チャンクの項目は従来のまま"
        assert "参照先（上の本文の「" in r["context"]
        r0 = retrieve_only.retrieve_context(FakeGraph(*corpus()), Q, document_names="[]", mode="graph_vector_fulltext", embedding_provider="vllm",
                                            embedding_model="cl-nagoya/ruri-v3-310m", retrieval_settings={"reference_max": 0}, model="gemma4")
        assert r0["references"] == [] and [c["via_reference"] for c in r0["chunks"]] == [False]
        legacy = retrieve_only.retrieve_context(FakeGraph(*corpus()), Q, document_names="[]", mode="graph_vector_fulltext", embedding_provider="vllm",
                                                embedding_model="cl-nagoya/ruri-v3-310m", retrieval_settings={"reference_max": 2}, model="gemma4")
        assert sorted(c["id"] for c in legacy["chunks"] if c["via_reference"]) == ["a0", "b9"]
        assert all(e["select"] == "target" and e["from_chunk_id"] is None and e["hop"] == 1 for e in legacy["references"]) and len(legacy["references"]) == 2
    finally:
        retrieve_only.get_neo4j_retriever, retrieve_only.create_document_retriever_chain = original, original_chain


# ───────────────────────── 検証で見つかった欠陥の直し ─────────────────────────
# 従来の選び方の点数（参照先の本文と質問の近さ）: a0 0.30、d2（区分 EC）0.25、d1（区分 EP）0.15、b9 0.05、b7 0.02、d3 0
TARGET_RULES = [(None, "[B > 第 9 条]", 0.05), (None, "[A > 5.1 性能試験]", 0.30), (None, "[B > 第 7 条]", 0.02), (None, "区分 EC", 0.25), (None, "区分 EP", 0.15)]


def test_併用は一文で選ぶ参照が無ければ従来と同じ結果():
    rules = BASE_RULES + TARGET_RULES          # 参照の一文はどれも 0 点（質問と合わない）
    for reference_max in (1, 2, 10):
        a, _, _ = build(orig, HITS, rules, {"reference_max": reference_max})
        expected = run(a)
        for present in ("separate", "inline"):
            b, graph, _ = build(rr, HITS, rules, {"reference_max": reference_max, "reference_select": "both", "reference_depth": 2, "reference_presentation": present})
            got = run(b)
            assert [d.page_content for d in got] == [d.page_content for d in expected], f"上限 {reference_max}・{present}: 本文が従来と違う"
            assert [d.state for d in got] == [d.state for d in expected]
            assert [{k: v for k, v in d.metadata.items() if k != "references"} for d in got] == [d.metadata for d in expected]
            t = trace_of(got)
            assert len(t) == min(reference_max, 6) and all(e["select"] == "target" and e["from_chunk_id"] is None for e in t)
            legacy, _, _ = build(rr, HITS, rules, {"reference_max": reference_max})
            assert trace_of(run(legacy)) == t, "控えも、従来の選び方のときと同じ"


def test_併用は従来の分をそのまま足し_一文で選んだ分を加える():
    rules = BASE_RULES + TARGET_RULES + [(None, Q_BACK, 0.0), (None, Q1, 0.9)]
    both = {"reference_select": "both", "reference_max": 2}
    retriever, _, _ = build(rr, HITS, rules, both)
    t = trace_of(run(retriever))
    # 一文で b9（0.9）。従来の選び方の上位 2 件（a0 0.30、d2 0.25）は、そのまま足す
    assert [(e["chunk_id"], e["select"], e["hop"]) for e in t] == [("b9", "link", 1), ("a0", "target", 1), ("d2", "target", 1)], [(e["chunk_id"], e["select"]) for e in t]
    assert t[0]["from_chunk_id"] == "a1" and t[0]["quote"] == Q1 and t[1]["from_chunk_id"] is None and t[1]["quote"] is None and t[1]["score"] == 0.30
    # 従来の設定（target、同じ上限）で足されるチャンクは、併用でも必ず足される
    for reference_max in (1, 2, 3):
        legacy = {e["chunk_id"] for e in trace_of(run(build(rr, HITS, rules, {"reference_max": reference_max})[0]))}
        got = [e["chunk_id"] for e in trace_of(run(build(rr, HITS, rules, {**both, "reference_max": reference_max})[0]))]
        assert legacy <= set(got) and len(got) == len(set(got)), (reference_max, legacy, got)
        assert got == ["b9"] + [i for i in ("a0", "d2", "d1")[:reference_max]], got
    # 一文で選んだチャンクが従来の上位にもある場合は、1 回だけ足す（従来の 3 位を繰り上げることはしない）
    top = BASE_RULES + [(None, "[B > 第 9 条]", 0.99)] + TARGET_RULES[1:] + [(None, Q_BACK, 0.0), (None, Q1, 0.9)]
    retriever, _, _ = build(rr, HITS, top, both)
    t = trace_of(run(retriever))
    assert [(e["chunk_id"], e["select"]) for e in t] == [("b9", "link"), ("a0", "target")], [(e["chunk_id"], e["select"]) for e in t]
    # 2 回目（一文で選んだ参照先の中の参照）は、親 1 つにつき 1 件。順は 1 回目 → 2 回目 → 従来の分
    hop2 = BASE_RULES + TARGET_RULES + [(None, Q_BACK, 0.0), (None, Q1, 0.9), (Q1, Q_FORM, 0.4), (Q1, Q_TABLE, 0.8)]
    retriever, _, _ = build(rr, HITS, hop2, {**both, "reference_depth": 2})
    t = trace_of(run(retriever))
    assert [(e["chunk_id"], e["select"], e["hop"]) for e in t] == [("b9", "link", 1), ("bt", "link", 2), ("a0", "target", 1), ("d2", "target", 1)]
    assert [e["order"] for e in t] == [1, 2, 3, 4]


def test_併用で直後に置くのは一文で選んだ分だけ():
    chunks = corpus()[0]
    rules = BASE_RULES + TARGET_RULES + [(None, Q_BACK, 0.0), (None, Q1, 0.9)]
    retriever, _, _ = build(rr, HITS, rules, {"reference_select": "both", "reference_max": 2, "reference_presentation": "inline"})
    docs = run(retriever)
    by = {d.metadata["source"]: d for d in docs}
    whole = "\n".join(d.page_content for d in docs)
    assert whole.count("参照先（") == 1, "見出しを付けて移すのは、一文で選んだ b9 だけ"
    a = by["A.md"].page_content
    assert a.index(chunks["a1"]["text"]) < a.index(f"参照先（上の本文の「{Q1}」が指す箇所。出典: B.md）:\n" + chunks["b9"]["text"]) < a.index(chunks["a2"]["text"])
    # 従来の選び方の分（a0、d2）は、従来どおり自分の文書の行に、見出しなしで入る
    assert chunks["a0"]["text"] in a and chunks["d2"]["text"] in by["D.md"].page_content
    assert {e["chunk_id"]: e["shown_in"] for e in trace_of(docs)} == {"b9": "A.md", "a0": "A.md", "d2": "D.md"}
    # 文書行の並び: D.md（従来の分 d2 の点数 0.25）は、従来と同じく自分の点数で並ぶ。B.md（b9 を移した後は断り書きとエンティティの行だけ）は 0 点で末尾
    assert by["D.md"].state["query_similarity_score"] == 0.25 and by["A.md"].state["query_similarity_score"] == 0.95
    assert [d.metadata["source"] for d in docs] == ["A.md", "D.md", "B.md"] and by["B.md"].state["query_similarity_score"] == 0.0
    assert by["B.md"].page_content.startswith("Text Content:\n" + rr.REFERENCE_MOVED_NOTE + "\n----\nEntities:\n") and "Board:審査会" in by["B.md"].page_content
    # 直接選ばれた本文と従来の分の文書行は、従来の設定（target）のときと同じ並び・同じ点数
    legacy = run(build(rr, HITS, rules, {"reference_max": 2})[0])
    assert [(d.metadata["source"], d.state["query_similarity_score"]) for d in docs if d.metadata["source"] != "B.md"] == \
           [(d.metadata["source"], d.state["query_similarity_score"]) for d in legacy]


def test_併用は片方の段が失敗しても残りを足す():
    rules = BASE_RULES + TARGET_RULES + [(None, Q_BACK, 0.0), (None, Q1, 0.9)]
    reranker = FakeReranker(rules)
    reranker.fail_on = "最高回転で運転する。 " + Q1          # 参照の一文の採点だけ失敗させる
    retriever, _, _ = build(rr, HITS, reranker, {"reference_select": "both", "reference_max": 2})
    t = trace_of(run(retriever))
    assert [(e["chunk_id"], e["select"]) for e in t] == [("a0", "target"), ("d2", "target")], [(e["chunk_id"], e["select"]) for e in t]
    reranker = FakeReranker(rules)
    reranker.fail_on = "[A > 5.1 性能試験]"                   # 従来の選び方の採点（参照先の本文）だけ失敗させる
    retriever, _, _ = build(rr, HITS, reranker, {"reference_select": "both", "reference_max": 2})
    t = trace_of(run(retriever))
    assert [(e["chunk_id"], e["select"]) for e in t] == [("b9", "link")], [(e["chunk_id"], e["select"]) for e in t]


def test_併用でも絞り込みの外の文書へはたどらない():
    rules = BASE_RULES + TARGET_RULES + [(None, Q_BACK, 0.9), (None, Q1, 0.9)]
    retriever, _, _ = build(rr, HITS, rules, {"reference_select": "both", "reference_max": 3, "reference_depth": 2}, document_names=["A.md"])
    t = trace_of(run(retriever))
    assert [(e["chunk_id"], e["select"]) for e in t] == [("a0", "link")], [(e["chunk_id"], e["select"]) for e in t]
    legacy, _, _ = build(orig, HITS, rules, {"reference_max": 3}, document_names=["A.md"])
    whole = "\n".join(d.page_content for d in run(legacy))
    assert "[B > 第 9 条]" not in whole and "[D > 3. 供給条件]" not in whole


def test_一文で選ぶ途中で失敗しても選んだ分は残す():
    # 1 つ目の参照（a1 → b9）を選んだ後、2 つ目（a4 → d1〜d3 のどれを足すかの選び分け）の採点で失敗させる
    reranker = FakeReranker(BASE_RULES + [(None, Q_BACK, 0.0), (None, Q1, 0.9), (None, Q_CONT, 0.6)])
    reranker.fail_on = "区分 EP の圧力"
    retriever, _, _ = build(rr, HITS, reranker, LINK)
    assert [e["chunk_id"] for e in trace_of(run(retriever))] == ["b9"]


def test_2回目のたどりが失敗しても1回目の分は残す():
    reranker = FakeReranker(HOP2_RULES)
    reranker.fail_on = "審査会の承認を受ける。 " + Q_TABLE     # 2 回目の参照の一文の採点だけ失敗させる（b9 の本文には空白が入らない）
    for present in ("separate", "inline"):
        retriever, _, _ = build(rr, [("a1", 1.0)], reranker, {**LINK, "reference_depth": 2, "reference_presentation": present})
        docs = run(retriever)
        assert [(e["chunk_id"], e["hop"]) for e in trace_of(docs)] == [("b9", 1)], [(e["chunk_id"], e["hop"]) for e in trace_of(docs)]
        assert "[B > 第 9 条]" in "\n".join(d.page_content for d in docs)


def test_一文の無いリンクは束ねない():
    # 別の仕組みが張った古いリンク（quote なし）: a1 → c1、c2、c3。束ねると 1 件しか足せない。従来の選び方は 3 件足す
    for quote, locator in ((None, None), ("", ""), (None, "同じ語")):
        chunks, links = corpus()
        links = [l for l in links if l["source"] != "a1"] + [{"source": "a1", "target": f"c{i}", "quote": quote, "locator": locator, "method": "llm"} for i in (1, 2, 3)]
        rules = BASE_RULES + [(None, "[C > 第 1 条]", 0.9), (None, "[C > 第 2 条]", 0.8), (None, "[C > 第 3 条]", 0.7)]
        retriever, _, _ = build(rr, [("a1", 1.0)], rules, {**LINK, "reference_presentation": "inline"}, graph=FakeGraph(chunks, links))
        docs = run(retriever)
        t = trace_of(docs)
        assert [(e["chunk_id"], e["score"]) for e in t] == [("c1", 0.9), ("c2", 0.8), ("c3", 0.7)], [(e["chunk_id"], e["score"]) for e in t]
        assert all(e["quote"] == "" and e["from_chunk_id"] == "a1" for e in t), "一文が無いリンクは、参照先の本文と質問の近さで測る"
        assert "\n".join(d.page_content for d in docs).count("参照先（上の本文が参照している箇所。出典: C.md）:") == 3
        legacy, _, _ = build(orig, [("a1", 1.0)], rules, {"reference_max": 3}, graph=FakeGraph(chunks, links))
        assert sum(chunks[f"c{i}"]["text"] in "\n".join(d.page_content for d in run(legacy)) for i in (1, 2, 3)) == 3
    # 一文の無い、文書だけを指すリンクは、測るものが無いのでたどらない
    chunks, links = corpus()
    links = [{"source": "a1", "doc": "C.md", "quote": None, "locator": None}, {"source": "a1", "doc": "D.md", "quote": "", "locator": ""}]
    retriever, _, _ = build(rr, [("a1", 1.0)], FakeReranker([(None, "", 0.9)]), LINK, graph=FakeGraph(chunks, links))
    assert trace_of(run(retriever)) == []


def test_参照の一文の位置は空白の違いを無視して探す():
    text = "[見出し]\n前の文。\n参照の一文は、\n  別表 1 による。次の文。"
    assert rr._locate_quote(text, "参照の一文は、別表 1 による。") == text.index("参照の一文は")
    assert rr._locate_quote(text, "参照の一文は、\n  別表 1 による。") == text.index("参照の一文は"), "そのまま見つかるなら、その位置"
    assert rr._locate_quote(text, "本文に無い一文による。") == -1 and rr._locate_quote(text, "") == -1 and rr._locate_quote("", "一文") == -1
    assert rr._locate_quote("a.b*c による", "a.b*c による") == 0 and rr._locate_quote("a.b*c\nによる", "a.b*c による") == 0, "記号は正規表現として読まない"
    # 改行や空白が本文と違う一文（LLM が張ったリンク）でも、直前の文が付く
    assert rr.reference_passage(text, "見出し", "参照の一文は、別表 1 による。") == "見出し: 前の文。 参照の一文は、別表 1 による。"
    # 直前の文が別の参照の一文かどうかも、空白の違いを無視して見る
    assert rr.reference_passage(text, "見出し", "参照の一文は、別表 1 による。", {"前 の 文。"}) == "見出し: 参照の一文は、別表 1 による。"
    long_quote = "あ" * 1000 + "による。"
    assert rr._locate_quote("前。\n" + "あ" * 500 + "\n" + "あ" * 500 + "による。", long_quote) == 3


def test_文書へのリンクの候補は質問と参照の一文で絞る():
    rules = BASE_RULES + [(Q_DOC, "成績表の保存は 29 年", 0.7), (None, Q_DOC, 0.8)]
    retriever, graph, _ = build(rr, HITS, rules, LINK)
    assert [e["chunk_id"] for e in trace_of(run(retriever))] == ["c2"]
    assert any("$per_document" in c for c, _ in graph.calls)
    assert f"{Q} {Q_DOC}" in graph.embedding.texts, "大きい文書の候補の絞り込みは、質問だけでなく「質問 + 参照の一文」の埋め込みで行う"


def test_見せ方の処理が失敗しても検索は続く():
    rules = HOP2_RULES
    expected = [d.page_content for d in run(build(rr, [("a1", 1.0)], rules, {**LINK, "reference_depth": 2})[0])]
    original = rr.RerankGraphRetriever._inline_references

    def broken(self, *args, **kwargs):
        raise RuntimeError("layout bug")

    rr.RerankGraphRetriever._inline_references = broken
    try:
        retriever, _, _ = build(rr, [("a1", 1.0)], rules, {**LINK, "reference_depth": 2, "reference_presentation": "inline"})
        docs = run(retriever)
    finally:
        rr.RerankGraphRetriever._inline_references = original
    assert [d.page_content for d in docs] == expected, "組み直しに失敗したら、従来の並びのまま返す"
    assert {e["chunk_id"]: e["shown_in"] for e in trace_of(docs)} == {"b9": "B.md", "bt": "B.md"}, "控えは残す"
    original = rr.RerankGraphRetriever._present_references
    rr.RerankGraphRetriever._present_references = broken
    try:
        retriever, _, _ = build(rr, [("a1", 1.0)], rules, {**LINK, "reference_depth": 2, "reference_presentation": "inline"})
        docs = run(retriever)
    finally:
        rr.RerankGraphRetriever._present_references = original
    assert [d.page_content for d in docs] == expected and trace_of(docs) == []


def test_組み直しは途中で止めても文書行を書き換えない():
    # 2 つ目の文書行だけが本文と合わない場合: 1 つ目の文書行も書き換えずに、従来の並びのままにする
    chunks, links = corpus()
    graph = FakeGraph(chunks, links)
    original = graph._expand

    def partly_broken(chunk_rows):
        rows = original(chunk_rows)
        rows[-1]["text"] = rows[-1]["text"].replace("\n----\nEntities:", "（別の写し）\n----\nEntities:", 1)
        return rows

    graph._expand = partly_broken
    retriever, _, _ = build(rr, [("a1", 1.0)], HOP2_RULES, {**LINK, "reference_presentation": "inline"}, graph=graph)
    docs = run(retriever)
    whole = "".join(d.page_content for d in docs)
    assert "参照先（" not in whole and rr.REFERENCE_MOVED_NOTE not in whole
    assert {d.metadata["source"]: [x["id"] for x in d.metadata["chunkdetails"]] for d in docs} == {"A.md": ["a1"], "B.md": ["b9"]}


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_") and callable(fn) and only in name]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  ok  " + name[5:], flush=True)
        except Exception:
            failed += 1
            print("  NG  " + name[5:], flush=True)
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} 件 合格")
    sys.exit(1 if failed else 0)
