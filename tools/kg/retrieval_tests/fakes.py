"""単体の試験のための差し替え: グラフ（neo_db.query）、候補の検索（candidate_db）、リランカー（_rerank）、LLM。"""
from langchain_core.documents import Document


class FakeEmbedding:
    def __init__(self):
        self.calls = 0
        self.texts = []

    def embed_query(self, text):
        self.calls += 1
        self.texts.append(text)
        return [0.1, 0.2, 0.3]

    def embed_documents(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeGraph:
    """チャンク・文書・参照リンクを辞書で持ち、retriever が流す 4 種類の照会に答える。書き込みの照会が来たら失敗させる。

    chunks: {id: {"text", "doc", "position", "section"?, "ents"?: [..], "rels"?: [..]}}
    links:  [{"source", "target"（チャンクの ID）または "doc"（文書名）, "quote", "locator"?, "form"?, "method"?, "source_section"?}]
    """

    def __init__(self, chunks, links):
        self.chunks, self.links = chunks, links
        self.embedding = FakeEmbedding()
        self.calls = []
        self.break_rows = False   # True にすると、取り出しの照会の本文を、チャンクの本文と合わない形で返す

    def query(self, cypher, params=None):
        params = params or {}
        self.calls.append((cypher, params))
        assert not any(word in cypher for word in ("CREATE ", "MERGE ", "DELETE ", " SET ", "REMOVE ")), "書き込みの照会"
        if "$chunk_rows" in cypher:
            return self._expand(params["chunk_rows"])
        if "r.quote AS quote" in cypher:
            return self._links(params["ids"])
        if "$per_document" in cypher:
            return self._document_chunks(params)
        if "-[:REFERS_TO]->(t:Chunk) WHERE NOT t.id IN $ids" in cypher:
            return self._legacy(params["ids"])
        if "c.page_number AS page_number" in cypher:
            return [{"id": i, "text": self.chunks[i]["text"], "fileName": self.chunks[i]["doc"], "position": self.chunks[i]["position"], "page_number": None,
                     "section": self.chunks[i].get("section"), "docs": [{"fileName": self.chunks[i]["doc"], "source": self.chunks[i]["doc"]}]} for i in params["ids"] if i in self.chunks]
        raise AssertionError("想定していない照会: " + cypher[:120])

    def _legacy(self, ids):
        out, seen = [], set()
        for link in self.links:
            target = link.get("target")
            if link["source"] in ids and target and target not in ids and target not in seen:
                seen.add(target)
                c = self.chunks[target]
                out.append({"id": target, "text": c["text"], "position": c["position"], "source": c["doc"]})
        return out

    def _links(self, ids):
        out = []
        for link in self.links:
            if link["source"] not in ids:
                continue
            s = self.chunks[link["source"]]
            row = {"source": link["source"], "source_text": s["text"], "source_heading": s.get("section"), "quote": link.get("quote"), "locator": link.get("locator", ""),
                   "form": link.get("form"), "method": link.get("method", "rule"), "source_section": link.get("source_section", "body")}
            if link.get("target"):
                t = self.chunks[link["target"]]
                row.update(kind="chunk", id=link["target"], text=t["text"], position=t["position"], file_name=t["doc"], doc_source=t["doc"])
            else:
                row.update(kind="document", id=None, text=None, position=None, file_name=link["doc"], doc_source=None)
            out.append(row)
        return out

    def _document_chunks(self, params):
        return [{"id": i, "text": c["text"], "position": c["position"], "file_name": c["doc"], "doc_source": c["doc"]}
                for i, c in self.chunks.items() if c["doc"] in params["docs"] and i not in params["skip"]][: params["per_document"] * max(1, len(params["docs"]))]

    def _expand(self, chunk_rows):
        docs = {}
        for row in chunk_rows:
            docs.setdefault(self.chunks[row["id"]]["doc"], []).append(row)
        out = []
        for name, rows in docs.items():
            texts = [self.chunks[r["id"]]["text"] + ("（別の写し）" if self.break_rows else "") for r in rows]
            ents = sorted({e for r in rows for e in self.chunks[r["id"]].get("ents", [])})
            rels = sorted({e for r in rows for e in self.chunks[r["id"]].get("rels", [])})
            text = "Text Content:\n" + "\n----\n".join(texts) + "\n----\nEntities:\n" + "\n".join(ents) + "\n----\nRelationships:\n" + "\n".join(rels)
            out.append({"text": text, "score": sum(r["score"] for r in rows) / len(rows),
                        "metadata": {"length": len(text), "source": name, "chunkdetails": [{"id": r["id"], "score": r["score"]} for r in rows],
                                     "entities": {"entityids": ents, "relationshipids": rels}}})
        return out


class FakeCandidates:
    """候補の検索。(チャンクの ID, 検索の点数) の並びをそのまま返す。"""

    def __init__(self, graph, hits):
        self.graph, self.hits = graph, hits

    def similarity_search_with_score(self, query, **kwargs):
        names = (kwargs.get("filter") or {}).get("fileName", {}).get("$in")
        out = []
        for chunk_id, score in self.hits[: kwargs.get("k", 20)]:
            c = self.graph.chunks[chunk_id]
            if names and c["doc"] not in names:
                continue
            out.append((Document(page_content=c["text"], metadata={"chunk_id": chunk_id, "source": c["doc"], "position": c["position"]}), score))
        return out


class FakeReranker:
    """規則の表で点数を返すリランカー。規則 = (問いに含まれる語, 文に含まれる語, 点数)。上から順に見て、最初に当たったもの。当たらなければ 0。"""

    def __init__(self, rules):
        self.rules = rules
        self.calls = []
        self.fail_on = None       # 文に、この語が含まれる呼び出しで例外にする

    def __call__(self, query, texts):
        self.calls.append((query, list(texts)))
        if self.fail_on and any(self.fail_on in t for t in texts):
            raise RuntimeError("reranker down")
        out = []
        for text in texts:
            score = 0.0
            for q_part, t_part, value in self.rules:
                if (q_part is None or q_part in query) and t_part in text:
                    score = value
                    break
            out.append(score)
        return out
