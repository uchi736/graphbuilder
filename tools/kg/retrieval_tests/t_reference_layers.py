"""単体の試験: 章・節を指す参照と、文書番号を経由する文書間の参照（文書層）の追跡（設定 reference_sections・reference_document_layer）。

LLM・リランカー・グラフは差し替える（fakes.py）。ネットワークなしで動く。
  python t_reference_layers.py            全部
  python t_reference_layers.py <名前の一部> 名前に一致する試験だけ
"""
import sys
import traceback

from fakes import FakeGraph
from t_reference_follow import BASE_RULES, HITS, Q, Q_DOC, build, corpus, orig, rr, run, trace_of

Q_SEC = "供給条件は D の 3. による。"
Q_STD = "記録の保存は C による。"


class LayerGraph(FakeGraph):
    """章・節の層と文書層を足した偽のグラフ。

    sections: {節の ID: {"title", "chunks": [チャンクの ID]}}
    section_links: [{"source", "section", "quote"}]
    document_layer: [{"source_doc", "target_doc", "standard", "quotes": [..]}]
    """

    def __init__(self, sections=None, section_links=None, document_layer=None, fail_layer=False):
        super().__init__(*corpus())
        self.sections = sections or {}
        self.section_links = section_links or []
        self.document_layer = document_layer or []
        self.fail_layer = fail_layer

    def query(self, cypher, params=None):
        params = params or {}
        if "link_quote" in cypher:
            self.calls.append((cypher, params))
            return [{"from_id": l["source"], "to_id": l["target"], "link_quote": l.get("quote")} for l in self.links
                    if l.get("target") and l["source"] in params["sources"] and l["target"] in params["targets"]]
        if "HAS_SECTION" in cypher or "IS_VERSION_OF" in cypher:
            self.calls.append((cypher, params))
            assert not any(word in cypher for word in ("CREATE ", "MERGE ", "DELETE ", " SET ", "REMOVE ")), "書き込みの照会"
            if self.fail_layer:
                raise RuntimeError("layer query failed")
            if "HAS_SECTION" in cypher:
                return self._section_rows(params)
            return self._document_layer_rows(params)
        return super().query(cypher, params)

    def _section_rows(self, params):
        out = []
        for link in self.section_links:
            if link["source"] not in params["ids"]:
                continue
            s = self.chunks[link["source"]]
            section = self.sections[link["section"]]
            targets = [i for i in section["chunks"] if i not in params["ids"]][: params["per_section"]]
            for i in targets:
                c = self.chunks[i]
                out.append({"source": link["source"], "source_text": s["text"], "source_heading": s.get("section"), "quote": link["quote"], "method": "rule",
                            "section_id": link["section"], "section_title": section["title"], "section_file": c["doc"], "kind": "chunk",
                            "id": i, "text": c["text"], "position": c["position"], "file_name": c["doc"], "doc_source": c["doc"]})
        return out

    def _document_layer_rows(self, params):
        out = []
        for i in params["ids"]:
            c = self.chunks[i]
            for ref in self.document_layer:
                hits = [q for q in ref["quotes"] if "".join(q.split()) in "".join(c["text"].split())]
                if ref["source_doc"] == c["doc"] and ref["target_doc"] != c["doc"] and hits:
                    out.append({"source": i, "source_text": c["text"], "source_heading": c.get("section"), "quote": hits[0], "kind": "document",
                                "standard": ref["standard"], "file_name": ref["target_doc"]})
        return out


def layer_graph(**kwargs):
    # a4（A > 7 潤滑）の参照を、D の「3. 供給条件」の節（d1〜d3）へ結ぶ。a2（A > 6 記録）の本文の「記録の保存は C による。」は文書層で C.md へ
    defaults = dict(sections={"S-D3": {"title": "3. 供給条件", "chunks": ["d1", "d2", "d3"]}},
                    section_links=[{"source": "a4", "section": "S-D3", "quote": Q_SEC}],
                    document_layer=[{"source_doc": "A.md", "target_doc": "C.md", "standard": "C", "quotes": [Q_STD]}])
    defaults.update(kwargs)
    return LayerGraph(**defaults)


def layer_calls(graph):
    return [c for c, _ in graph.calls if "HAS_SECTION" in c or "IS_VERSION_OF" in c]


# a4 は既存のチャンク間リンクで d1〜d3 を指すため、章・節の経路を測る試験では起点から外す
HITS_NO_A4 = [h for h in HITS if h[0] != "a4"]
TARGET = {"reference_max": 2}
LINK = {"reference_select": "link", "reference_max": 3}


# ───────────────────────── 既定（無効）─────────────────────────
def test_既定では新しい照会を出さず従来と同じ結果():
    rules = BASE_RULES + [(None, "[B > 第 9 条]", 0.05), (None, "区分 EC", 0.25), (None, "成績表の保存は 29 年", 0.6)]
    for settings in (TARGET, LINK, {"reference_select": "both", "reference_max": 2}):
        graph = layer_graph()
        b, _, _ = build(rr, HITS, rules, settings, graph=graph)
        got = run(b)
        assert layer_calls(graph) == [], f"{settings}: 無効なのに章・節・文書層の照会を出している"
        plain, _, _ = build(rr, HITS, rules, settings)
        expected = run(plain)
        assert [d.page_content for d in got] == [d.page_content for d in expected]
        assert [e["chunk_id"] for e in trace_of(got)] == [e["chunk_id"] for e in trace_of(expected)]
    # 変更前の版（本番の現行）とも同じ（参照先の本文による選択）
    a, _, _ = build(orig, HITS, rules, TARGET)
    b, _, _ = build(rr, HITS, rules, TARGET, graph=layer_graph())
    assert [d.page_content for d in run(a)] == [d.page_content for d in run(b)]


def test_設定の定義():
    keys = [s[0] for s in rr.RETRIEVAL_SETTING_SPECS]
    assert "reference_sections" in keys and "reference_document_layer" in keys
    d = rr.default_retrieval_settings()
    assert d["reference_sections"] is False and d["reference_document_layer"] is False
    r = rr.resolve_retrieval_settings({"reference_sections": "true", "reference_document_layer": True})
    assert r["reference_sections"] is True and r["reference_document_layer"] is True
    spec = {s["key"]: s for s in rr.retrieval_settings_spec()}
    assert spec["reference_sections"]["group"] == "参照の追跡" and spec["reference_sections"]["type"] == "bool"


# ───────────────────────── 参照先の本文による選択（本番の既定の方式）─────────────────────────
def test_本文による選択で章_節のチャンクを足す():
    rules = BASE_RULES + [(None, "区分 ER", 0.7), (None, "区分 EC", 0.3), (None, "区分 EP", 0.1)]
    retriever, graph, _ = build(rr, HITS_NO_A4, rules, {**TARGET, "reference_sections": True}, graph=layer_graph(section_links=[{"source": "a1", "section": "S-D3", "quote": Q_SEC}]))
    t = trace_of(run(retriever))
    ids = [e["chunk_id"] for e in t]
    assert "d3" in ids, ids
    assert all(e["target_kind"] == "section" for e in t if e["chunk_id"].startswith("d"))
    assert len(ids) <= 2


def test_本文による選択で1つの節から足すのは2件まで():
    rules = BASE_RULES + [(None, "区分 ER", 0.7), (None, "区分 EC", 0.69), (None, "区分 EP", 0.68)]
    graph = layer_graph(section_links=[{"source": "a1", "section": "S-D3", "quote": Q_SEC}])
    retriever, _, _ = build(rr, HITS_NO_A4, rules, {"reference_max": 5, "reference_sections": True}, graph=graph)
    ids = [e["chunk_id"] for e in trace_of(run(retriever))]
    assert sorted(i for i in ids if i.startswith("d")) == ["d2", "d3"], ids


def test_本文による選択で文書層の参照先の文書から足す():
    rules = BASE_RULES + [(None, "成績表の保存は 29 年", 0.6), (None, "様式 TCF-240", 0.1)]
    retriever, graph, _ = build(rr, HITS, rules, {**TARGET, "reference_document_layer": True}, graph=layer_graph())
    t = trace_of(run(retriever))
    assert any(e["chunk_id"] == "c2" and e["target_kind"] == "document_layer" for e in t), t
    # 参照先の文書のチャンクは、質問と参照の一文の両方の埋め込みで絞り込む
    assert any(Q_STD in text for text in graph.embedding.texts)


def test_絞り込みの外の文書へは足さない():
    rules = BASE_RULES + [(None, "成績表の保存は 29 年", 0.6), (None, "区分 ER", 0.7)]
    graph = layer_graph(section_links=[{"source": "a1", "section": "S-D3", "quote": Q_SEC}])
    retriever, _, _ = build(rr, HITS_NO_A4, rules, {**TARGET, "reference_sections": True, "reference_document_layer": True}, graph=graph, document_names=["A.md"])
    assert not any(e["chunk_id"][0] in "cd" for e in trace_of(run(retriever)))


# ───────────────────────── 参照の一文による選択 ─────────────────────────
def test_一文による選択で章_節は一文に合う1件を足す():
    rules = BASE_RULES + [(None, Q_SEC, 0.8), (Q_SEC, "区分 ER", 0.7), (Q_SEC, "区分 EC", 0.2), (Q_SEC, "区分 EP", 0.1)]
    graph = layer_graph(section_links=[{"source": "a1", "section": "S-D3", "quote": Q_SEC}])
    retriever, _, reranker = build(rr, HITS_NO_A4, rules, {**LINK, "reference_sections": True}, graph=graph)
    t = [e for e in trace_of(run(retriever)) if e["chunk_id"].startswith("d")]
    assert [e["chunk_id"] for e in t] == ["d3"], t
    assert t[0]["target_kind"] == "section" and t[0]["quote"] == Q_SEC and t[0]["from_chunk_id"] == "a1"


def test_一文による選択で文書層は文書を指す参照として1件を足す():
    rules = BASE_RULES + [(None, Q_STD, 0.8), (Q_STD, "成績表の保存は 29 年", 0.7), (Q_STD, "様式 TCF-240", 0.2)]
    graph = layer_graph()
    retriever, _, _ = build(rr, HITS, rules, {**LINK, "reference_documents": True, "reference_document_layer": True}, graph=graph)
    t = [e for e in trace_of(run(retriever)) if e["chunk_id"].startswith("c")]
    assert [e["chunk_id"] for e in t] == ["c2"], t
    # 同じ一文の文書リンク（既存）と文書層の参照は 1 つの参照にまとまり、2 回は足さない
    assert len(t) == 1


# ───────────────────────── 失敗時 ─────────────────────────
def test_章_節と文書層の照会が失敗しても検索と従来の参照は続く():
    rules = BASE_RULES + [(None, "[B > 第 9 条]", 0.05), (None, "区分 EC", 0.25)]
    for settings in ({**TARGET, "reference_sections": True, "reference_document_layer": True},
                     {**LINK, "reference_sections": True, "reference_document_layer": True}):
        graph = layer_graph(fail_layer=True)
        retriever, _, _ = build(rr, HITS, rules, settings, graph=graph)
        docs = run(retriever)
        plain, _, _ = build(rr, HITS, rules, {k: v for k, v in settings.items() if k not in ("reference_sections", "reference_document_layer")})
        assert docs and [d.page_content for d in docs] == [d.page_content for d in run(plain)], settings


# ───────────────────────── たどった道の控え ─────────────────────────
def test_本文による選択の控えに参照元と一文が入る():
    # 従来のチャンク間の参照（a1 → b9）。選ぶときは参照元を使わないが、控えには引き直して残す
    rules = BASE_RULES + [(None, "[B > 第 9 条]", 0.6)]
    retriever, graph, _ = build(rr, HITS, rules, TARGET, graph=layer_graph())
    t = {e["chunk_id"]: e for e in trace_of(run(retriever))}
    assert t["b9"]["from_chunk_id"] == "a1" and t["b9"]["quote"] == "回せない場合の取扱いは、B の第 9 条による。", t["b9"]
    assert t["b9"]["target_kind"] == "chunk" and t["b9"]["via_id"] is None


def test_章_節と文書層の控えに経由点が入る():
    rules = BASE_RULES + [(None, "区分 ER", 0.7), (None, "成績表の保存は 29 年", 0.65)]
    graph = layer_graph(section_links=[{"source": "a1", "section": "S-D3", "quote": Q_SEC}])
    retriever, _, _ = build(rr, HITS_NO_A4, rules, {"reference_max": 3, "reference_sections": True, "reference_document_layer": True}, graph=graph)
    t = {e["chunk_id"]: e for e in trace_of(run(retriever))}
    assert t["d3"]["target_kind"] == "section" and t["d3"]["from_chunk_id"] == "a1" and t["d3"]["quote"] == Q_SEC
    assert t["d3"]["via_id"] == "S-D3" and t["d3"]["via_label"] == "3. 供給条件" and t["d3"]["via_file"] == "D.md"
    assert t["c2"]["target_kind"] == "document_layer" and t["c2"]["from_chunk_id"] == "a2" and t["c2"]["quote"] == Q_STD
    assert t["c2"]["via_id"] == "C" and t["c2"]["via_label"] == "C" and t["c2"]["via_file"] == "C.md"


def test_一文による選択の控えに経由点が入る():
    rules = BASE_RULES + [(None, Q_SEC, 0.8), (Q_SEC, "区分 ER", 0.7)]
    graph = layer_graph(section_links=[{"source": "a1", "section": "S-D3", "quote": Q_SEC}])
    retriever, _, _ = build(rr, HITS_NO_A4, rules, {**LINK, "reference_sections": True}, graph=graph)
    t = {e["chunk_id"]: e for e in trace_of(run(retriever))}
    assert t["d3"]["via_id"] == "S-D3" and t["d3"]["via_label"] == "3. 供給条件" and t["d3"]["from_chunk_id"] == "a1"


def test_控えの引き直しが失敗しても検索は続く():
    rules = BASE_RULES + [(None, "[B > 第 9 条]", 0.6)]
    graph = layer_graph()
    original = graph.query
    def failing(cypher, params=None):
        if "link_quote" in cypher:
            raise RuntimeError("provenance failed")
        return original(cypher, params)
    graph.query = failing
    retriever, _, _ = build(rr, HITS, rules, TARGET, graph=graph)
    t = {e["chunk_id"]: e for e in trace_of(run(retriever))}
    assert "b9" in t and t["b9"]["from_chunk_id"] is None


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
