"""単体の試験: 文書層の参照が一文の節番号で節まで決まっているとき（M3）の追跡と、章・節の照会の形（M5 主所属を先に、M6 番号付きの表示名）。

文書層の照会（DOCUMENT_LAYER_REFERENCE_QUERY）が sections（参照元のチャンクから参照先の文書の節への、文書番号と番号で決めた線）を返したら、
参照先の文書の中は、その節のチャンク（STANDARD_SECTION_CHUNKS_QUERY）だけから選ぶ。節のチャンクが全部文脈にあれば何も足さず、
文書全体からの選び方（REFERENCE_DOCUMENT_CHUNKS_QUERY）には戻らない。sections が空なら従来どおり文書全体から近さで選ぶ。
章・節の参照の追跡（reference_sections）も有効なとき（参照追跡の方式の設定）は、同じ via = standard の線を章・節の照会がたどるので、
文書層の側ではたどらない（1 つの参照から 2 件を足さない）。
LLM・リランカー・グラフは差し替える（fakes.py）。ネットワークなしで動く。
  python t_reference_sections_m3.py            全部
"""
import sys
import traceback

from t_reference_follow import BASE_RULES, HITS, build, rr, run, trace_of
from t_reference_layers import LINK, Q_STD, TARGET, LayerGraph


class M3Graph(LayerGraph):
    """文書層の参照に、一文の節番号で決めた節（sections）を足した偽のグラフ。doc_sections: {節の ID: [チャンクの ID（主所属を先に）]}"""

    def __init__(self, doc_sections=None, **kwargs):
        super().__init__(**kwargs)
        self.doc_sections = doc_sections or {}

    def query(self, cypher, params=None):
        params = params or {}
        if "UNWIND $sections" in cypher:
            self.calls.append((cypher, params))
            assert not any(word in cypher for word in ("CREATE ", "MERGE ", "DELETE ", " SET ", "REMOVE ")), "書き込みの照会"
            out = []
            for sid in params["sections"]:
                for i in self.doc_sections.get(sid, []):
                    if i not in params["skip"]:
                        c = self.chunks[i]
                        out.append({"id": i, "text": c["text"], "position": c["position"], "file_name": c["doc"], "doc_source": c["doc"]})
            return out[: params["limit"]]
        rows = super().query(cypher, params)
        if "IS_VERSION_OF" in cypher:
            for r in rows:
                src_doc = self.chunks[r["source"]]["doc"]
                r["sections"] = next((ref.get("sections", []) for ref in self.document_layer
                                      if ref["standard"] == r["standard"] and ref["source_doc"] == src_doc), [])
        return rows


def m3_graph(sections=("S-C3",), with_section_link=False):
    # a2（A > 6 記録）の「記録の保存は C による。」が、文書層で C.md へ。一文の節番号で C の「第 3 条」の節（c3）まで決まっている場合。
    # with_section_link: 章・節の層にも同じ線（a2 → S-C3、via = standard の REFERS_TO）を置く（本番の後処理は E10 でこの線を作る）
    extra = dict(sections={"S-C3": {"title": "3 第 3 条", "chunks": ["c3"]}},
                 section_links=[{"source": "a2", "section": "S-C3", "quote": Q_STD}]) if with_section_link else {}
    return M3Graph(doc_sections={"S-C3": ["c3"]},
                   document_layer=[{"source_doc": "A.md", "target_doc": "C.md", "standard": "C", "quotes": [Q_STD], "sections": list(sections)}],
                   **extra)


def without_llm_document_link(graph):
    """偽のコーパスにある LLM のリンク a2 → 文書 C.md（チャンク間の参照の層。文書層とは別）を外す。
    一文による選択では、このリンクも文書を指す参照としてたどられる（文書全体から選ぶ）ので、文書層の分だけを確かめるときに外す。"""
    graph.links = [l for l in graph.links if not (l["source"] == "a2" and l.get("doc"))]
    return graph


def calls_with(graph, word):
    return [c for c, _ in graph.calls if word in c]


# ───────────────────────── 参照先の本文による選択 ─────────────────────────
def test_本文による選択で節まで決まった文書層はその節から足す():
    rules = BASE_RULES + [(None, "成績表の保存は 29 年", 0.6), (None, "様式 TCF-240", 0.1)]
    retriever, graph, _ = build(rr, HITS, rules, {**TARGET, "reference_document_layer": True}, graph=m3_graph())
    t = trace_of(run(retriever))
    c_ids = [e["chunk_id"] for e in t if e["chunk_id"].startswith("c")]
    assert c_ids == ["c3"], t
    assert all(e["target_kind"] == "document_layer" for e in t if e["chunk_id"] == "c3")
    assert calls_with(graph, "UNWIND $sections") and not calls_with(graph, "$per_document"), "節があるのに文書全体から選んでいる"


def test_節が無ければ従来どおり文書全体から近さで選ぶ():
    rules = BASE_RULES + [(None, "成績表の保存は 29 年", 0.6), (None, "様式 TCF-240", 0.1)]
    settings = {**TARGET, "reference_document_layer": True}
    retriever, graph, _ = build(rr, HITS, rules, settings, graph=m3_graph(sections=()))
    t = trace_of(run(retriever))
    plain, _, _ = build(rr, HITS, rules, settings, graph=LayerGraph(document_layer=[{"source_doc": "A.md", "target_doc": "C.md", "standard": "C", "quotes": [Q_STD]}]))
    assert [e["chunk_id"] for e in t] == [e["chunk_id"] for e in trace_of(run(plain))]
    assert [e["chunk_id"] for e in t if e["chunk_id"].startswith("c")][0] == "c2", t
    assert not calls_with(graph, "UNWIND $sections") and calls_with(graph, "$per_document")


def test_節のチャンクが全部文脈にあれば足さず文書全体にも戻らない():
    rules = BASE_RULES + [(None, "成績表の保存は 29 年", 0.6), (None, "様式 TCF-240", 0.1)]
    hits = HITS + [("c3", 0.05)]
    retriever, graph, _ = build(rr, hits, rules, {**TARGET, "reference_document_layer": True}, graph=m3_graph())
    t = trace_of(run(retriever))
    assert not [e for e in t if e["target_kind"] == "document_layer"], t
    assert "c2" not in [e["chunk_id"] for e in t], "節と関係の無いチャンク（c2）を文書全体から足している"
    assert calls_with(graph, "UNWIND $sections") and not calls_with(graph, "$per_document")


def test_章_節と文書層を両方有効にしても節の外のチャンクを足さない():
    # 参照追跡の方式の設定（両方有効）。章・節の照会が c3 を足し、文書層の側は同じ線をたどらない（c2 を文書全体から足さない）
    rules = BASE_RULES + [(None, "成績表の保存は 29 年", 0.6), (None, "様式 TCF-240", 0.1)]
    settings = {**TARGET, "reference_sections": True, "reference_document_layer": True}
    retriever, graph, _ = build(rr, HITS, rules, settings, graph=m3_graph(with_section_link=True))
    t = trace_of(run(retriever))
    c = [e for e in t if e["chunk_id"].startswith("c")]
    assert [e["chunk_id"] for e in c] == ["c3"], t
    assert c[0]["target_kind"] == "section" and c[0]["via_id"] == "S-C3"
    assert not calls_with(graph, "$per_document"), "節があるのに文書全体から選んでいる"


def test_章_節と文書層を両方有効にしても節の無い文書層の参照は従来どおり():
    rules = BASE_RULES + [(None, "成績表の保存は 29 年", 0.6), (None, "様式 TCF-240", 0.1)]
    settings = {**TARGET, "reference_sections": True, "reference_document_layer": True}
    retriever, graph, _ = build(rr, HITS, rules, settings, graph=m3_graph(sections=()))
    t = trace_of(run(retriever))
    assert [e["chunk_id"] for e in t if e["chunk_id"].startswith("c")][0] == "c2", t
    assert calls_with(graph, "$per_document")


# ───────────────────────── 参照の一文による選択 ─────────────────────────
def test_一文による選択で節まで決まった文書層はその節から足す():
    rules = BASE_RULES + [(None, Q_STD, 0.8), (Q_STD, "成績表の保存は 29 年", 0.7), (Q_STD, "様式 TCF-240", 0.2)]
    retriever, graph, _ = build(rr, HITS, rules, {**LINK, "reference_documents": True, "reference_document_layer": True}, graph=m3_graph())
    t = [e for e in trace_of(run(retriever)) if e["chunk_id"].startswith("c")]
    layer = [e for e in t if e["target_kind"] == "document_layer"]
    assert [e["chunk_id"] for e in layer] == ["c3"], t
    assert layer[0]["from_chunk_id"] == "a2" and layer[0]["quote"] == Q_STD and layer[0]["via_id"] == "C"


def test_一文による選択で節のチャンクが全部文脈にあれば足さない():
    rules = BASE_RULES + [(None, Q_STD, 0.8), (Q_STD, "成績表の保存は 29 年", 0.7), (Q_STD, "様式 TCF-240", 0.2)]
    hits = HITS + [("c3", 0.05)]
    graph = without_llm_document_link(m3_graph())
    retriever, graph, _ = build(rr, hits, rules, {**LINK, "reference_documents": True, "reference_document_layer": True}, graph=graph)
    t = trace_of(run(retriever))
    assert not [e for e in t if e["target_kind"] == "document_layer"], t
    assert "c2" not in [e["chunk_id"] for e in t], t
    assert calls_with(graph, "UNWIND $sections") and not calls_with(graph, "$per_document")


def test_一文による選択で両方有効でも1つの参照から1件だけ足す():
    rules = BASE_RULES + [(None, Q_STD, 0.8), (Q_STD, "成績表の保存は 29 年", 0.7), (Q_STD, "様式 TCF-240", 0.2)]
    settings = {**LINK, "reference_documents": True, "reference_sections": True, "reference_document_layer": True}
    retriever, graph, _ = build(rr, HITS, rules, settings, graph=without_llm_document_link(m3_graph(with_section_link=True)))
    t = [e for e in trace_of(run(retriever)) if e["from_chunk_id"] == "a2"]
    assert [e["chunk_id"] for e in t] == ["c3"], t
    assert t[0]["target_kind"] == "section" and t[0]["via_id"] == "S-C3"
    assert not calls_with(graph, "$per_document")
    # LLM のリンク（a2 → 文書 C.md）が残っていれば、それは従来どおり文書を指す参照としてたどる（文書層の分は足さない）
    retriever, graph, _ = build(rr, HITS, rules, settings, graph=m3_graph(with_section_link=True))
    t = [e for e in trace_of(run(retriever)) if e["from_chunk_id"] == "a2"]
    assert sorted(e["target_kind"] for e in t) == ["document", "section"], t


def test_一文による選択で節が無ければ従来と同じ():
    rules = BASE_RULES + [(None, Q_STD, 0.8), (Q_STD, "成績表の保存は 29 年", 0.7), (Q_STD, "様式 TCF-240", 0.2)]
    settings = {**LINK, "reference_documents": True, "reference_document_layer": True}
    a, _, _ = build(rr, HITS, rules, settings, graph=m3_graph(sections=()))
    b, _, _ = build(rr, HITS, rules, settings, graph=LayerGraph(document_layer=[{"source_doc": "A.md", "target_doc": "C.md", "standard": "C", "quotes": [Q_STD]}]))
    assert [e["chunk_id"] for e in trace_of(run(a))] == [e["chunk_id"] for e in trace_of(run(b))]


# ───────────────────────── 照会の形（M5・M6）─────────────────────────
def test_章_節の照会は主所属を先に番号付きの表示名を返す():
    q = rr.SECTION_REFERENCE_QUERY
    assert "ORDER BY prim DESC, sim DESC, t.position" in q and "m.primary = true" in q
    assert "s.number + ' ' + s.title" in q and "coalesce(s.source, '') <> 'catalogue'" in q and "STARTS WITH" not in q
    assert "[0..$per_section]" in q, "上限の数え方を変えていない"
    d = rr.DOCUMENT_LAYER_REFERENCE_QUERY
    assert "rs.via = 'standard' AND rs.standard = st.id AND sec.fileName = d2.fileName" in d and "sections" in d
    s = rr.STANDARD_SECTION_CHUNKS_QUERY
    assert "ORDER BY prim DESC, sim DESC, t.position" in s and "LIMIT $limit" in s


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
