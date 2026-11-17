# -*- coding: utf-8 -*-
"""網羅型の質問（該当するものをすべて挙げる質問）に向けた検索「エンティティ起点の収集」。

既定の検索は、質問に近いチャンクを候補にしてリランクで数件に絞る。該当する文書が多い質問では、上位に入らなかった文書が漏れる。
この検索は、起点をエンティティにする。検索設定 entity_collect が真のときだけ動く（方式 entity_collect の既定値。ほかの方式には影響しない）。

  1. 質問の語     質問から、対象を特定する語を LLM で抜き出す（材料記号・規格番号・検査名など。一般の語は除く）
  2. エンティティ 語を名前に含むエンティティをすべて拾う。照合は正規形（NFKC・空白とハイフンを除く・小文字・かなのゆれを無視）で行う
  3. 別名         拾った名前が「A(B)」の形で、質問の語が A の側にあるとき、B を別名として 1 段だけ広げる
                  （例: 蛍光浸透探傷検査(ザイクロ検査) → ザイクロ）。質問の語が B の側にあるときは広げない（A は上位の語であることが多い）
  4. チャンク     拾ったエンティティが出てくるチャンクをすべて集め、本文に語があるチャンクを全文索引で補う
  5. 選別         文書ごとにリランクで上位のチャンクを残す。該当した文書は落とさない。文字数の上限を超えるときは、
                  文書あたりのチャンク数を減らし、それでも超えるときは語の前後だけに切り詰める

何も拾えなかったとき・失敗したときは None を返し、呼び出し側は既定の検索に戻る。
名前の正規形での照合は Cypher で書けないため、エンティティの名前を 1 回の照会で読み出して照合する（数千個の規模を想定。
規模が大きい場合は、正規形を取り込み時にプロパティとして持たせて索引で引く形に改める）。
実験台帳 X25（見込み）・X26（回答まで）。
"""
import json
import logging
import os
import re
import time
import unicodedata
from typing import Callable, Dict, List, Optional

from src.shared.constants import DOCUMENT_SOURCE_EXPR

EXCLUDED_LABELS = {"Document", "Chunk", "Section", "__Community__", "Evidence", "Session", "Message", "SectionSummary"}
TERM_MAX = 4
FULLTEXT_LIMIT = 60
MIN_SNIPPET = 300
TERM_MAX_TOKENS = 64          # 語の配列だけを返させる
TERM_SLOT_WAIT = 10.0         # このプロセスの LLM への同時要求の枠を待つ上限（秒。要点の作成が枠を使っている間に検索を長く止めない）

_SEP = re.compile(r"[\s_\-‐‑‒–—―−]+")
_SMALL = str.maketrans("ァィゥェォッャュョヮぁぃぅぇぉっゃゅょゎ", "アイウエオツヤユヨワあいうえおつやゆよわ")
# 語の後ろに付く一般の語。語の側だけから外す（「窒化処理」で「窒化部品」「塩浴窒化」を拾うため）
_TERM_SUFFIX = re.compile(r"(又は相当材|または相当材|相当材|処理|加工|検査|試験|観察|測定|計測|コーティング|コーテイング)$")
_NAME_SUFFIX = re.compile(r"(又は相当材|または相当材|相当材|材|検査|試験|観察|測定|計測)$")
_PAREN = re.compile(r"^(.{2,}?)\((.{2,}?)\)(.*)$")
_NUMBER_ONLY = re.compile(r"^[\d.,年月日版号]+$")


def norm(text) -> str:
    """NFKC → 空白・ハイフンの異体・下線を除く → 小文字。"""
    return _SEP.sub("", unicodedata.normalize("NFKC", "" if text is None else str(text))).casefold()


def fold(text) -> str:
    """照合用の形。norm に加えて、かなのゆれ（濁点・半濁点、小書き、長音符）を無視する。
    例: ザイグロ／ザイクロ、クロムカーバイト／クローム カーバイド、コーテイング／コーティング。"""
    s = unicodedata.normalize("NFD", norm(text))
    s = "".join(ch for ch in s if ch not in ("゙", "゚"))
    s = unicodedata.normalize("NFC", s).translate(_SMALL)
    return s.replace("ー", "")


def _long_enough(key: str) -> bool:
    if not key or _NUMBER_ONLY.match(key):
        return False
    return len(key) >= (3 if key.isascii() else 2)


def term_key(term: str) -> str:
    """質問の語の照合用の形。後ろの一般の語を外す。短くなりすぎるときは外さない。"""
    base = fold(term)
    cut = fold(_TERM_SUFFIX.sub("", norm(term)))
    return cut if _long_enough(cut) and len(cut) >= 2 and cut != base and not cut.isdigit() else base


def parse_terms(raw: str) -> List[str]:
    """LLM の返答から語の配列を取り出す。JSON の配列が無ければ空。"""
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out[:TERM_MAX]


def term_messages(question: str) -> List[dict]:
    rules = (
        "技術文書の検索の前処理として、質問から「探す対象を特定する語」を抜き出してください。\n"
        "- 材料記号、規格番号、文書番号、検査・試験・処理の名前、物質名など、対象を特定する固有の語だけを抜き出す\n"
        "- 「部品」「基準」「機種」「材料」「規格」「方法」「要求」「値」など、多くの文書に出る一般の語は抜き出さない\n"
        "- 質問に書かれた表記のまま抜き出す。言い換えや推測の語を足さない\n"
        "- 括弧で併記された別の表記は、別の語として抜き出す\n"
        "- 出力は JSON の配列だけにする。語は 1〜4 個\n\n"
        "例: 「SCM440H を材料に使う部品をすべて挙げよ。」 → [\"SCM440H\"]\n"
        "例: 「浸炭焼入れ（ガス浸炭）を要求している部品をすべて挙げよ。」 → [\"浸炭焼入れ\", \"ガス浸炭\"]\n\n"
        f"質問: {question}"
    )
    return [{"role": "user", "content": rules}]


def ask_terms(messages: List[dict]) -> str:
    """質問の語の抜き出しの 1 回の要求（試験では差し替える）。接続先は環境変数 CHAT_REFERENCE_DOC_SELECT_MODEL（既定 gemma4）の
    LLM_MODEL_CONFIG_<名前>、時間切れは CHAT_REFERENCE_DOC_SELECT_TIMEOUT 秒（既定 60）。"""
    from src import section_summaries as SS
    text, _ = SS.chat(messages, model_key=os.environ.get("CHAT_REFERENCE_DOC_SELECT_MODEL", "gemma4"), max_tokens=TERM_MAX_TOKENS,
                      timeout=SS._float_env("CHAT_REFERENCE_DOC_SELECT_TIMEOUT", 60.0), slot_wait=TERM_SLOT_WAIT)
    return text


def question_terms(question: str, ask: Optional[Callable[[List[dict]], str]] = None) -> List[str]:
    return parse_terms((ask or ask_terms)(term_messages(question)))


def alias_keys(name: str, keys: List[str]) -> List[str]:
    """名前が「A(B)」の形で、質問の語が A の側にあるとき、B を別名（照合用の形）として返す。"""
    m = _PAREN.match(norm(name))
    if not m:
        return []
    outer, inner = fold(m.group(1) + m.group(3)), m.group(2)
    if re.search(r"[、,]", inner) or _NUMBER_ONLY.match(inner):
        return []
    if not any(k in outer for k in keys):
        return []
    alias = fold(_NAME_SUFFIX.sub("", inner))
    if not _long_enough(alias) or len(alias) < 3 or any(k in alias or alias in k for k in keys):
        return []
    return [alias]


def match_entities(names: List[str], keys: List[str]) -> Dict[str, List[str]]:
    """名前の一覧から、語を含むもの（matched）と、別名で広げたもの（aliased）、使った別名（aliases）を返す。"""
    folded = {n: fold(n) for n in names}
    matched = [n for n in names if any(k in folded[n] for k in keys)]
    aliases = []
    for n in matched:
        for a in alias_keys(n, keys):
            if a not in aliases:
                aliases.append(a)
    hit = set(matched)
    aliased = [n for n in names if n not in hit and any(a in folded[n] for a in aliases)]
    return {"matched": matched, "aliased": aliased, "aliases": aliases}


def snippet(text: str, keys: List[str], width: int) -> str:
    """語の最初の出現の前後 width 字。見つからなければ先頭から。正規形と原文の位置は 1 字ずつ対応させて求める。"""
    if len(text) <= width:
        return text
    # 原文を 1 字ずつ照合用の形にして、連結した中での位置から原文の位置を逆算する
    acc, starts = [], []
    for i, ch in enumerate(text):
        f = fold(ch)
        if f:
            acc.append(f)
            starts.extend([i] * len(f))
    folded = "".join(acc)
    pos = -1
    for k in keys:
        if not k:
            continue
        j = folded.find(k)
        if j >= 0:
            pos = starts[j]
            break
    if pos < 0:
        return text[:width] + "…"
    head = text.split("\n", 1)[0] if text.startswith("[") else ""
    start = max(0, pos - width // 3)
    body = text[start:start + width]
    prefix = (head + "\n…") if head and start > len(head) else ("…" if start > 0 else "")
    return prefix + body + ("…" if start + width < len(text) else "")


def _entity_names(graph):
    rows = graph.query(
        "MATCH (e:__Entity__) WHERE e.id IS NOT NULL AND none(l IN labels(e) WHERE l IN $excluded) RETURN DISTINCT e.id AS id",
        params={"excluded": sorted(EXCLUDED_LABELS)})
    return [r["id"] for r in rows if isinstance(r.get("id"), str)]


CHUNKS_OF_ENTITIES = f"""
MATCH (e:__Entity__) WHERE e.id IN $names
MATCH (c:Chunk)-[:HAS_ENTITY]->(e)
MATCH (c)-[:PART_OF]->(d:Document)
WHERE $documents IS NULL OR d.fileName IN $documents
RETURN c.id AS id, c.text AS text, c.position AS position, {DOCUMENT_SOURCE_EXPR} AS source, collect(DISTINCT e.id) AS entities
"""

CHUNKS_BY_TEXT = f"""
CALL db.index.fulltext.queryNodes('keyword', $q, {{limit: $limit}}) YIELD node AS c, score
MATCH (c)-[:PART_OF]->(d:Document)
WHERE $documents IS NULL OR d.fileName IN $documents
RETURN c.id AS id, c.text AS text, c.position AS position, {DOCUMENT_SOURCE_EXPR} AS source
"""


def _phrase(term: str) -> str:
    return '"' + re.sub(r'(["\\])', r"\\\1", term) + '"'


def collect(retriever, query: str, ask: Optional[Callable[[List[dict]], str]] = None):
    """RerankGraphRetriever から呼ぶ。文書ごとの文書行（DocWithState）の一覧を返す。何も拾えなければ None。"""
    from src.rerank_retriever import DocWithState, _rerank
    settings = retriever.settings or {}
    per_doc = int(settings.get("collect_per_doc") or 2)
    max_docs = int(settings.get("collect_max_docs") or 30)
    budget = int(settings.get("char_budget") or 14000)
    graph = retriever.neo_db
    started = time.time()

    terms = question_terms(query, ask)
    keys = [k for k in dict.fromkeys(term_key(t) for t in terms) if _long_enough(k)]
    if not keys:
        logging.info(f"entity collect: no usable term from {terms!r}")
        return None
    names = _entity_names(graph)
    found = match_entities(names, keys)
    picked = found["matched"] + found["aliased"]
    documents = retriever.document_names or None

    chunks: Dict[str, dict] = {}
    if picked:
        for r in graph.query(CHUNKS_OF_ENTITIES, params={"names": picked, "documents": documents}):
            chunks[r["id"]] = {"id": r["id"], "text": r["text"] or "", "position": r["position"], "source": r["source"],
                               "entities": sorted(r["entities"] or []), "via": "entity"}
    all_keys = keys + found["aliases"]
    for term in terms:
        try:
            rows = graph.query(CHUNKS_BY_TEXT, params={"q": _phrase(term), "limit": FULLTEXT_LIMIT, "documents": documents})
        except Exception as exc:
            logging.warning(f"entity collect: fulltext lookup failed for a term ({exc})")
            continue
        for r in rows:
            # 全文索引は 1 字ずつの一致なので、語がそのまま本文にあるものだけを残す
            if r["id"] in chunks:
                continue
            folded = fold(r["text"])
            if any(k in folded for k in keys):
                chunks[r["id"]] = {"id": r["id"], "text": r["text"] or "", "position": r["position"], "source": r["source"],
                                   "entities": [], "via": "text"}
    if not chunks:
        logging.info(f"entity collect: terms {terms!r} matched {len(picked)} entities but no chunk")
        return None

    rows = list(chunks.values())
    try:
        scores = _rerank(query, [r["text"] for r in rows])
    except Exception as exc:
        logging.warning(f"entity collect: rerank failed ({exc}); keeping the document order")
        scores = [0.0] * len(rows)
    for r, s in zip(rows, scores):
        r["score"] = float(s)
    by_doc: Dict[str, List[dict]] = {}
    for r in rows:
        by_doc.setdefault(r["source"], []).append(r)
    for rs in by_doc.values():
        rs.sort(key=lambda r: (-r["score"], r["position"] if r["position"] is not None else 0))
    order = sorted(by_doc, key=lambda s: -by_doc[s][0]["score"])
    dropped = order[max_docs:]
    order = order[:max_docs]

    def kept(n):
        return {s: by_doc[s][:n] for s in order}

    def total(sel):
        return sum(len(r["text"]) for rs in sel.values() for r in rs)

    sel = kept(per_doc)
    while per_doc > 1 and total(sel) > budget:
        per_doc -= 1
        sel = kept(per_doc)
    trimmed = False
    if total(sel) > budget:
        width = max(MIN_SNIPPET, budget // max(1, sum(len(rs) for rs in sel.values())) - 40)
        for rs in sel.values():
            for r in rs:
                r["text"] = snippet(r["text"], all_keys, width)
        trimmed = True

    docs = []
    for s in order:
        rs = sel[s]
        text = "\n----\n".join(r["text"] for r in rs)
        meta = {"source": s, "chunkdetails": [{"id": r["id"], "score": r["score"]} for r in rs],
                "collect": {"terms": terms, "aliases": found["aliases"], "chunks_found": len(by_doc[s]),
                            "entities": sorted({e for r in by_doc[s] for e in r["entities"]})[:20]}}
        docs.append(DocWithState(page_content=text, metadata=meta, state={"query_similarity_score": rs[0]["score"]}))
    logging.info("entity collect: terms=%r aliases=%r entities=%d(+%d) chunks=%d docs=%d per_doc=%d trimmed=%s dropped_docs=%d in %.2fs",
                 terms, found["aliases"], len(found["matched"]), len(found["aliased"]), len(rows), len(order), per_doc, trimmed,
                 len(dropped), time.time() - started)
    return docs
