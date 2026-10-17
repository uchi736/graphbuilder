"""P1-a: チャンク単位リランク付きの 2 段リトリーバ。

既定の流れ（hybrid で k 件 → 文書ごとに束ねて展開 → 3000 トークン断片の上位 4 片）を、
  1. hybrid で候補チャンクを CHAT_CANDIDATE_K 件取る（チャンク単位・軽いクエリ）
  2. クロスエンコーダ（ruri-v3-reranker, vLLM /v1/rerank）で並べ替え、上位 CHAT_RERANK_TOP_N 件だけ残す
  3. 残したチャンクだけを起点に、モードの retrieval_query（展開あり／なし）を流す
  4. 断片選別はせず、文字数予算 CHAT_CONTEXT_CHAR_BUDGET で「本文 → リレーション → エンティティ」の順に詰める
に置き換える。CHAT_RERANK_ENABLED=false なら呼び出し側で従来経路に戻す。

参照リンク（REFERS_TO）のたどり方は 3 通りある（設定 reference_select）。
  target … 従来。選んだチャンクから出る参照先を全部集め、参照先の本文と質問の近さで上位を足す
  link   … 参照の一文（リンクの quote）と質問の近さでリンクごとに点数を付け、下限以上の参照だけをたどる。
           文書だけを指す参照は、その文書の中から読むべきチャンクを選ぶ。設定で 2 回目のたどりも行う
  both   … target で足す分（上限 reference_max）はそのまま足し、それに加えて link でも選ぶ（上限は同じ数）。
           従来の設定で文脈に入っていた参照先を失わない。一文で選ぶ参照が無い質問では、target と同じ結果になる
足した参照先の見せ方（reference_presentation）と、回答の指示への追加（reference_prompt）は、別々に切り替えられる。

点数の扱い:
  - 参照をたどるかどうかは、参照の一文の点数（控えの score・link_score）で決める。
  - 足したチャンクの並べるための点数（chunkdetails の score）は、参照元のチャンクの点数 × 参照の点数にする。
    一覧を点数順に並べたとき、参照先が参照元より前に出ないようにするためである。
  - 参照元の直後に示した参照先は、文書行の並びと上限（CHAT_PROMPT_TOKEN_CUTOFF）の判定に使わない。
    直接選ばれた本文の行は、一文で選んだ参照先に押し出されない（従来の選び方で足した分は、従来どおり自分の文書行の点数で並ぶ）。
"""
import logging
import os
import re
from typing import Any, List, Optional

import requests
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import Field
from langchain_neo4j import Neo4jVector

from src.shared.common_fn import get_value_from_env

# 候補チャンクを取るだけの軽い retrieval_query（文書で束ねない）
CHUNK_CANDIDATE_QUERY = """
WITH node AS chunk, score
MATCH (chunk)-[:PART_OF]->(d:Document)
RETURN chunk.text AS text, score,
       {chunk_id: chunk.id, source: COALESCE(CASE WHEN d.url CONTAINS "None" THEN d.fileName ELSE d.url END, d.fileName),
        position: chunk.position} AS metadata
"""

# 文書の出典の式。取り出しの照会（metadata.source）と同じにして、文書行との突き合わせに使う
_SOURCE_OF_D = 'COALESCE(CASE WHEN d.url CONTAINS "None" THEN d.fileName ELSE d.url END, d.fileName)'

# 参照リンクを 1 本ずつ読む（reference_select = link）。参照先はチャンクか文書。参照元の本文は、参照の一文の前後を取るのに使う
REFERENCE_LINK_QUERY = """
UNWIND $ids AS id
MATCH (c:Chunk {id: id})-[r:REFERS_TO]->(t)
WHERE t:Chunk OR t:Document
OPTIONAL MATCH (t)-[:PART_OF]->(d:Document)
RETURN c.id AS source, c.text AS source_text, c.section AS source_heading,
       r.quote AS quote, r.locator AS locator, r.form AS form, r.method AS method, r.source_section AS source_section,
       CASE WHEN t:Chunk THEN 'chunk' ELSE 'document' END AS kind,
       CASE WHEN t:Chunk THEN t.id ELSE null END AS id,
       CASE WHEN t:Chunk THEN t.text ELSE null END AS text,
       CASE WHEN t:Chunk THEN t.position ELSE null END AS position,
       CASE WHEN t:Chunk THEN d.fileName ELSE t.fileName END AS file_name,
       CASE WHEN t:Chunk THEN __SOURCE__ ELSE null END AS doc_source
LIMIT 2000
""".replace("__SOURCE__", _SOURCE_OF_D)

# 文書だけを指す参照の先（文書）から、読むべきチャンクの候補を取る。文書が大きいときは、「質問 + 参照の一文」との埋め込みの近さで絞る
REFERENCE_DOCUMENT_CHUNKS_QUERY = """
UNWIND $docs AS name
MATCH (d:Document {fileName: name})<-[:PART_OF]-(c:Chunk)
WHERE c.text IS NOT NULL AND NOT c.id IN $skip
WITH d, c, CASE WHEN c.embedding IS NULL THEN 0.0 ELSE vector.similarity.cosine(c.embedding, $query_vector) END AS sim
ORDER BY sim DESC
WITH d, collect({id: c.id, text: c.text, position: c.position})[0..$per_document] AS rows
UNWIND rows AS row
RETURN row.id AS id, row.text AS text, row.position AS position, d.fileName AS file_name, __SOURCE__ AS doc_source
""".replace("__SOURCE__", _SOURCE_OF_D)

# 文書 1 つから取る候補の上限（これを超える文書は、「質問 + 参照の一文」との埋め込みの近さの上位だけをリランクする）
REFERENCE_DOCUMENT_CANDIDATES = 30
# 参照の一文に付ける直前の文の長さの上限（表の行などが長い場合に、後ろ側だけを使う）
REFERENCE_PREVIOUS_CHARS = 200
# 見出しに出す参照の一文の長さの上限（長い文は後ろ側を残す。「…による」は文末にあるため）
REFERENCE_QUOTE_CHARS = 100
# 参照の一文の位置を、空白の違いを無視して探すときに使う、一文の先頭からの字数（位置が分かればよいので、先頭だけで足りる）
REFERENCE_LOCATE_CHARS = 200

# 既定モードの retrieval_query は「WITH node as chunk, score」で始まる（索引の出力を受ける）。
# 選んだチャンク id から同じ本体を流すために、先頭だけ置き換える。
_INDEX_HEAD = re.compile(r"^\s*WITH\s+node\s+as\s+chunk,\s*score", re.IGNORECASE)
_CHUNK_HEAD = """
UNWIND $chunk_rows AS row
MATCH (chunk:Chunk {id: row.id})
WITH chunk, row.score AS score
"""


# ============================================================
# 検索設定（画面から変更できる制限値）
# 既定値は環境変数、なければここの値。画面からの指定はリクエストごとに上書きする。
# ============================================================
RETRIEVAL_SETTING_SPECS = [
    # key, 型, 環境変数, 既定値, 最小, 最大, 区分, 表示名, 説明
    ("candidate_k", "int", "CHAT_CANDIDATE_K", 20, 1, 200, "チャンクの検索", "候補チャンク数",
     "ベクトル検索と全文検索で取る候補の数。この中からリランクで選ぶ。"),
    ("rerank_top_n", "int", "CHAT_RERANK_TOP_N", 6, 1, 50, "チャンクの検索", "選ぶチャンク数",
     "リランク後に残すチャンクの数。本文として LLM に渡り、グラフ展開の起点にもなる。"),
    ("candidate_min_score", "float", "CHAT_CANDIDATE_MIN_SCORE", 0.5, 0.0, 1.0, "チャンクの検索", "候補の点数しきい値",
     "検索の点数（最上位を 1 とした値）がこれ未満の候補を捨てる。本家の既定は 0.5。"),
    ("reference_max", "int", "CHAT_REFERENCE_MAX", 0, 0, 20, "チャンクの検索", "参照先として足すチャンク数",
     "選んだチャンクから参照リンク（REFERS_TO。「〜による」「〜参照」の参照先）をたどり、参照先のチャンクを足す数の上限。0 は使わない。"
     "参照先の選び方は「参照のたどり方」で決める。参照の一文で選ぶ場合に 2 回目もたどると、1 回目に足した参照先 1 つにつき 1 件まで、さらに足す。"
     "「従来の選び方に加えて、参照の一文でも選ぶ」では、従来の分と一文の分のそれぞれに、この上限を使う。"),
    ("entity_order", "choice:similarity=質問との類似度順,count=選んだチャンクでの出現数順（本家）", "CHAT_ENTITY_ORDER", "similarity", None, None,
     "起点のエンティティ", "起点の並べ方", "上限で切る前の並び順。本家は出現数順で、同数の順は決まっていない。"),
    ("entity_limit", "int", "CHAT_ENTITY_LIMIT", 40, 0, 1000, "起点のエンティティ", "起点の上限（文書ごと）",
     "選んだチャンクに付くエンティティのうち、起点にする数の上限。0 は制限なし。本家の既定は 40。"),
    ("sim_min", "float", "CHAT_EXPAND_SIM_MIN", 0.3, 0.0, 1.0, "グラフの展開", "展開する類似度の下限",
     "質問とエンティティの類似度がこれ未満の起点は展開しない。本家の既定は 0.3。"),
    ("sim_max", "float", "CHAT_EXPAND_SIM_MAX", 0.9, 0.0, 1.01, "グラフの展開", "2 ホップに切り替える類似度",
     "類似度がこれを超える起点は 2 ホップ先まで展開する。本家の既定は 0.9。"),
    ("two_hop", "bool", "CHAT_EXPAND_TWO_HOP", True, None, None, "グラフの展開", "2 ホップ展開を使う",
     "オフにすると、類似度に関係なく 1 ホップまでにする。"),
    ("one_hop_limit", "int", "CHAT_EXPAND_ONE_HOP_LIMIT", 0, 0, 100000, "グラフの展開", "1 ホップの経路の上限（起点ごと）",
     "起点 1 個から取り出す経路の数。0 は制限なし。本家の既定は 20（起点自身を含む）で、並び順は作成順。"),
    ("two_hop_limit", "int", "CHAT_EXPAND_TWO_HOP_LIMIT", 40, 0, 100000, "グラフの展開", "2 ホップの経路の上限（起点ごと）",
     "2 ホップ展開する起点から取り出す経路の数。0 は制限なし（件数が急増するので注意）。本家の既定は 40。"),
    ("line_min_score", "float", "CHAT_LINE_MIN_SCORE", 0.0, 0.0, 1.0, "文脈に入れる行", "行の点数しきい値",
     "エンティティとリレーションの行をリランクし、点数がこれ未満の行を捨てる。0 は捨てない。"),
    ("line_max", "int", "CHAT_LINE_MAX", 120, 0, 5000, "文脈に入れる行", "行の上限",
     "点数の高い順に文脈へ入れる行の最大数。0 は制限なし。"),
    ("char_budget", "int", "CHAT_CONTEXT_CHAR_BUDGET", 14000, 1000, 200000, "文脈に入れる行", "文脈の文字数の上限",
     "本文と行を合わせた文字数の上限。LLM の入力長を超えないように設定する。"),
    # ---- 参照のたどり方（「参照先として足すチャンク数」が 1 以上のときに効く）----
    ("reference_select", "choice:target=参照先の本文と質問の近さで選ぶ（従来）,link=参照の一文と質問の近さで選ぶ,both=従来の選び方に加えて、参照の一文でも選ぶ",
     "CHAT_REFERENCE_SELECT", "target", None, None,
     "参照のたどり方", "参照先の選び方",
     "従来は、選んだチャンクから出る参照先を全部集め、参照先の本文が質問に近い順に足す。"
     "「参照の一文」を選ぶと、本文の中の「〜による」「〜参照」の一文が質問に合っている参照だけをたどる（答えの箇所が質問と似ていない場合に届く）。"
     "「従来の選び方に加えて」を選ぶと、従来の選び方で足す分はそのまま足し、さらに参照の一文で選んだ分を足す（従来の設定で届いていた参照先を失わない。足す数は増える）。"
     "以下の項目は、従来以外を選んだときに使う。"),
    ("reference_min_score", "float", "CHAT_REFERENCE_MIN_SCORE", 0.3, 0.0, 1.0, "参照のたどり方", "たどる参照の点数の下限",
     "参照の一文と質問の近さ（リランクの点数。0〜1）がこれ未満の参照はたどらない。上げると、参照を必要としない質問に余計なチャンクを足しにくくなる。"
     "評価用の文書群では 0.1〜0.35 で結果が変わらず、0.4 以上では届く質問が減った。"),
    ("reference_depth", "int", "CHAT_REFERENCE_DEPTH", 1, 1, 2, "参照のたどり方", "たどる回数",
     "2 にすると、足した参照先の中にある参照を、もう 1 回たどる（参照先がさらに別表や別の項を指している場合のため）。2 回目に足すのは、1 回目の参照先 1 つにつき 1 件まで。"),
    ("reference_documents", "bool", "CHAT_REFERENCE_DOCUMENTS", True, None, None, "参照のたどり方", "文書だけを指す参照もたどる",
     "番号だけ・名称だけで文書を指す参照（項や別表の指定が無いもの）について、その文書の中から、質問と参照の一文に合うチャンクを 1 つ選んで足す。"),
    ("reference_history_weight", "float", "CHAT_REFERENCE_HISTORY_WEIGHT", 1.0, 0.0, 1.0, "参照のたどり方", "改正履歴の中の参照の重み",
     "改正履歴の記述の中にある参照の点数に掛ける値。1 は本文の参照と同じ扱い（既定）。下げると改正履歴からはたどりにくくなり、0 にするとたどらない。"),
    ("reference_presentation", "choice:separate=文書ごとにまとめて並べる（従来）,inline=参照元の直後に見出しを付けて置く", "CHAT_REFERENCE_PRESENTATION", "separate", None, None,
     "参照先の見せ方", "足した参照先の置き場所",
     "「参照元の直後」を選ぶと、足した参照先を、参照している本文のすぐ後ろに「参照先（上の本文の『…による』が指す箇所）」という見出しを付けて LLM に渡す。"
     "エンティティとリレーションの行の扱いは変わらない。"),
    ("reference_prompt", "bool", "CHAT_REFERENCE_PROMPT", False, None, None, "参照先の見せ方", "回答の指示に参照の扱いを足す",
     "オンにすると、回答の指示に「本文が他の箇所を指していて、その参照先が文脈にあるときは、参照先の内容で具体的に答える。無いときは、どこを見ればよいかを答え、中身を推測しない」を加える。"),
]


def _cast(kind, value):
    if kind == "int":
        return int(float(value))
    if kind == "float":
        return float(value)
    if kind == "bool":
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
    return str(value)


def _choices(kind):
    return [c.split("=")[0] for c in kind.split(":", 1)[1].split(",")]


def default_retrieval_settings() -> dict:
    out = {}
    for key, kind, env, default, lo, hi, *_ in RETRIEVAL_SETTING_SPECS:
        raw = os.environ.get(env)
        try:
            out[key] = _cast(kind, raw) if raw not in (None, "") else default
        except (TypeError, ValueError):
            out[key] = default
        if kind.startswith("choice") and key.startswith("reference_") and out[key] not in _choices(kind):
            # 環境変数の値が選択肢に無い場合は、既定値にする。参照の設定だけに掛ける（従来の項目の動きは変えない）
            logging.warning(f"{env}={raw!r} is not one of {_choices(kind)}; using {default!r}")
            out[key] = default
    return out


def resolve_retrieval_settings(overrides=None) -> dict:
    """既定値に画面からの指定を重ね、型と範囲を確かめた設定を返す。不正な値は既定値のまま。"""
    settings = default_retrieval_settings()
    for key, kind, env, default, lo, hi, *_ in RETRIEVAL_SETTING_SPECS:
        if not overrides or key not in overrides or overrides[key] in (None, ""):
            continue
        try:
            value = _cast("str" if kind.startswith("choice") else kind, overrides[key])
        except (TypeError, ValueError):
            logging.warning(f"retrieval setting {key}={overrides[key]!r} is invalid; keeping {settings[key]!r}")
            continue
        if kind.startswith("choice"):
            allowed = [c.split("=")[0] for c in kind.split(":", 1)[1].split(",")]
            if value not in allowed:
                continue
        elif lo is not None and hi is not None and not (lo <= value <= hi):
            logging.warning(f"retrieval setting {key}={value!r} is out of range [{lo}, {hi}]")
            continue
        settings[key] = value
    return settings


def retrieval_settings_spec() -> list:
    """画面に設定欄を出すための定義。"""
    defaults = default_retrieval_settings()
    spec = []
    for key, kind, env, default, lo, hi, group, label, help_text in RETRIEVAL_SETTING_SPECS:
        item = {"key": key, "type": kind.split(":")[0], "group": group, "label": label, "help": help_text,
                "default": defaults[key], "min": lo, "max": hi}
        if kind.startswith("choice"):
            item["choices"] = [{"value": c.split("=")[0], "label": c.split("=")[1]} for c in kind.split(":", 1)[1].split(",")]
        spec.append(item)
    return spec


# 既定モード（グラフ展開あり）の取り出し照会。本家の VECTOR_GRAPH_SEARCH_ENTITY_QUERY と同じ形で、
# 起点の並べ方・上限・経路の上限・類似度のしきい値を設定から差し込む。
_EXPAND_ENTITY_TEMPLATE = """
    OPTIONAL MATCH (chunk)-[:HAS_ENTITY]->(e)
    WITH e, count(*) AS numChunks
    WITH e, numChunks,
         CASE WHEN e IS NULL OR e.embedding IS NULL THEN null ELSE vector.similarity.cosine($query_vector, e.embedding) END AS qsim
    ORDER BY __ORDER_BY__
    __ENTITY_LIMIT__

    WITH
    CASE
        WHEN qsim IS NULL OR (__SIM_MIN__ <= qsim AND (qsim <= __SIM_MAX__ OR NOT __TWO_HOP__)) THEN
            collect {
                OPTIONAL MATCH path=(e)(()-[rels:!HAS_ENTITY&!PART_OF]-()){0,1}(:!Chunk&!Document&!__Community__)
                RETURN path __ONE_HOP_LIMIT__
            }
        WHEN __TWO_HOP__ AND qsim > __SIM_MAX__ THEN
            collect {
                OPTIONAL MATCH path=(e)(()-[rels:!HAS_ENTITY&!PART_OF]-()){0,2}(:!Chunk&!Document&!__Community__)
                RETURN path __TWO_HOP_LIMIT__
            }
        ELSE
            collect {
                MATCH path=(e)
                RETURN path
            }
    END AS paths, e
"""


def build_expansion_query(settings: dict) -> str:
    from src.shared.constants import VECTOR_GRAPH_SEARCH_QUERY_PREFIX, VECTOR_GRAPH_SEARCH_QUERY_SUFFIX
    lim = lambda n: f"LIMIT {int(n)}" if int(n) > 0 else ""
    order = "coalesce(qsim, -1.0) DESC, numChunks DESC" if settings["entity_order"] == "similarity" else "numChunks DESC"
    body = (_EXPAND_ENTITY_TEMPLATE
            .replace("__ORDER_BY__", order)
            .replace("__ENTITY_LIMIT__", lim(settings["entity_limit"]))
            .replace("__SIM_MIN__", repr(float(settings["sim_min"])))
            .replace("__SIM_MAX__", repr(float(settings["sim_max"])))
            .replace("__TWO_HOP__", "true" if settings["two_hop"] else "false")
            .replace("__ONE_HOP_LIMIT__", lim(settings["one_hop_limit"]))
            .replace("__TWO_HOP_LIMIT__", lim(settings["two_hop_limit"])))
    return VECTOR_GRAPH_SEARCH_QUERY_PREFIX + body + VECTOR_GRAPH_SEARCH_QUERY_SUFFIX


class DocWithState(Document):
    """format_documents が doc.state を読むので、EmbeddingsFilter と同じく state を持つ Document にする。"""
    state: dict = Field(default_factory=dict)


def rerank_enabled() -> bool:
    return get_value_from_env("CHAT_RERANK_ENABLED", "false", "bool")


_RERANK_STATE = {"truncate": True}


def _post_rerank(url: str, body: dict) -> dict:
    r = requests.post(url, json=body, timeout=120)
    r.raise_for_status()
    return r.json()


def _rerank(query: str, texts: List[str]) -> List[float]:
    url = get_value_from_env("CHAT_RERANK_URL", "http://host.docker.internal:8006/v1/rerank")
    model = get_value_from_env("CHAT_RERANK_MODEL", "cl-nagoya/ruri-v3-reranker-310m")
    max_chars = get_value_from_env("CHAT_RERANK_MAX_CHARS", 1500, "int")
    batch = get_value_from_env("CHAT_RERANK_BATCH", 200, "int")
    scores = [0.0] * len(texts)
    failed = 0
    for i in range(0, len(texts), batch):
        # 空の文書はサーバーが 400 を返すので、1 字の代わりを渡す
        chunk = [(t or "")[:max_chars] or "-" for t in texts[i:i + batch]]
        # 字数で切っても、数値の表などは 1 組がモデルの入力長を超えることがある。サーバー側でも入力長まで切り詰めさせる
        body = {"model": model, "query": query, "documents": chunk}
        try:
            try:
                data = _post_rerank(url, dict(body, truncate_prompt_tokens=-1) if _RERANK_STATE["truncate"] else body)
            except requests.HTTPError as exc:
                if not (_RERANK_STATE["truncate"] and exc.response is not None and exc.response.status_code in (400, 422)):
                    raise
                # 切り詰めの引数を受け付けないサーバーの場合は、引数なしでやり直し、以後は付けない
                data = _post_rerank(url, body)
                _RERANK_STATE["truncate"] = False
        except (requests.Timeout, requests.ConnectionError):
            # サーバーが応答しない。束ごとに待っても同じなので、ここで打ち切る（呼び出し側が検索の順に切り替える）
            raise
        except Exception as exc:
            # 1 つの束の失敗で全体を捨てない。失敗した束の点数は 0 のままにする
            failed += 1
            logging.warning("rerank batch %d-%d failed: %s", i, i + len(chunk), exc)
            continue
        for item in data.get("results", []):
            scores[i + item["index"]] = float(item.get("relevance_score", 0.0))
    if failed and failed * batch >= len(texts):
        raise RuntimeError("rerank failed for every batch")
    return scores


def _split_sections(text: str):
    """展開結果のテキストを 本文 / Entities / Relationships の 3 節に分ける。無ければ本文扱い。"""
    if "\n----\nEntities:\n" not in text:
        return text, [], []
    head, rest = text.split("\n----\nEntities:\n", 1)
    if "\n----\nRelationships:\n" in rest:
        ents, rels = rest.split("\n----\nRelationships:\n", 1)
    else:
        ents, rels = rest, ""
    ent_lines = [l for l in ents.split("\n") if l.strip()]
    rel_lines = [l for l in rels.split("\n") if l.strip()]
    return head, ent_lines, rel_lines


def _apply_budget(docs: List[Document], budget: int) -> List[Document]:
    """本文は全部残し、残りの予算で Relationships → Entities の行を各文書に順番に配る。"""
    parts = []
    used = 0
    for d in docs:
        head, ents, rels = _split_sections(d.page_content)
        parts.append({"doc": d, "head": head, "ents": ents, "rels": rels, "keep_rels": [], "keep_ents": []})
        used += len(head)
    # リレーション行を round-robin で詰める（先頭＝リランク上位の文書から）
    for kind in ("rels", "ents"):
        progress = True
        idx = [0] * len(parts)
        while progress and used < budget:
            progress = False
            for i, p in enumerate(parts):
                lines = p[kind]
                if idx[i] < len(lines):
                    line = lines[idx[i]]
                    if used + len(line) + 1 > budget:
                        idx[i] = len(lines)
                        continue
                    p["keep_" + kind].append(line)
                    used += len(line) + 1
                    idx[i] += 1
                    progress = True
    out = []
    for p in parts:
        text = p["head"]
        if p["keep_ents"] or p["keep_rels"]:
            text += "\n----\nEntities:\n" + "\n".join(p["keep_ents"]) + "\n----\nRelationships:\n" + "\n".join(p["keep_rels"])
        d = p["doc"]
        out.append(DocWithState(page_content=text, metadata=d.metadata, state=getattr(d, "state", {}) or {}))
    return out


def _apply_budget_reranked(query: str, docs: List[Document], budget: int, line_max: int = None, min_score: float = 0.0) -> List[Document]:
    """P1-b: 本文は全部残し、Entities／Relationships の全行を質問でリランクして上位から予算に詰める。
    行の種類や Neo4j の返却順には依らない。同じ行が複数の文書行に出るときは最初の文書行に付ける。"""
    parts = []
    used = 0
    pool = []          # (doc_index, kind, line)
    seen = set()
    for i, d in enumerate(docs):
        head, ents, rels = _split_sections(d.page_content)
        parts.append({"doc": d, "head": head, "keep_rels": [], "keep_ents": []})
        used += len(head)
        for kind, lines in (("rels", rels), ("ents", ents)):
            for line in lines:
                if line in seen:
                    continue
                seen.add(line)
                pool.append((i, kind, line))
    if pool and used < budget:
        try:
            scores = _rerank(query, [l for _, _, l in pool])
        except Exception as e:
            logging.warning(f"line rerank failed ({e}); falling back to original order")
            scores = [float(len(pool) - k) for k in range(len(pool))]
        order = sorted(range(len(pool)), key=lambda k: -scores[k])
        max_lines = get_value_from_env("CHAT_LINE_MAX", 120, "int") if line_max is None else int(line_max)
        kept = 0
        stop = "all lines"
        for k in order:
            i, kind, line = pool[k]
            if scores[k] < min_score:
                stop = f"score<{min_score}"
                break
            if max_lines > 0 and kept >= max_lines:
                stop = f"line_max={max_lines}"
                break
            if used + len(line) + 1 > budget:
                stop = f"char_budget={budget}"
                break
            parts[i]["keep_" + kind].append(line)
            used += len(line) + 1
            kept += 1
        srt = sorted(scores, reverse=True)
        q = lambda p: round(srt[min(len(srt) - 1, int(len(srt) * p))], 3) if srt else None
        logging.info(f"line rerank: {len(pool)} lines -> kept {kept} (stopped by {stop}); score p10={q(0.1)} p50={q(0.5)}, "
                     f">=0.1: {sum(s >= 0.1 for s in scores)}, >=0.3: {sum(s >= 0.3 for s in scores)}; top: "
                     + " | ".join(pool[k][2][:60] for k in order[:3]))
        dump_dir = os.environ.get("CHAT_LINE_DUMP_DIR")
        if dump_dir:
            try:
                os.makedirs(dump_dir, exist_ok=True)
                import json, hashlib, time
                name = time.strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha1(query.encode()).hexdigest()[:8] + ".json"
                with open(os.path.join(dump_dir, name), "w", encoding="utf-8") as f:
                    json.dump({"query": query, "lines": [{"score": round(scores[k], 4), "kind": pool[k][1], "line": pool[k][2],
                               "kept": rank < kept} for rank, k in enumerate(order)]}, f, ensure_ascii=False)
            except OSError as e:
                logging.warning(f"line dump failed: {e}")
    out = []
    for p in parts:
        text = p["head"]
        if p["keep_ents"] or p["keep_rels"]:
            text += "\n----\nEntities:\n" + "\n".join(p["keep_ents"]) + "\n----\nRelationships:\n" + "\n".join(p["keep_rels"])
        d = p["doc"]
        out.append(DocWithState(page_content=text, metadata=d.metadata, state=getattr(d, "state", {}) or {}))
    return out


# ============================================================
# 参照リンクのたどり方（reference_select = link・both）の部品
# ============================================================
def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _locate_quote(text: str, quote: str) -> int:
    """参照の一文が本文のどこにあるか（無ければ -1）。そのままで見つからないときは、空白と改行の違いを無視して探す。

    規則で張ったリンクの一文は本文と 1 字も違わないが、LLM が張ったリンクの一文は、改行や空白が本文と違うことがある。
    見つからないと直前の文を付けられず、主題の分からない一文だけで測ることになる。
    """
    if not quote or not text:
        return -1
    at = text.find(quote)
    if at >= 0:
        return at
    squeezed = _squeeze(quote)[:REFERENCE_LOCATE_CHARS]
    if len(squeezed) < 4:
        return -1
    found = re.search(r"\s*".join(re.escape(ch) for ch in squeezed), text)
    return found.start() if found else -1


def reference_passage(source_text: str, heading: str, quote: str, other_quotes=()) -> str:
    """参照の一文を、質問との近さを測るための文にする: 「見出し: 直前の一文 参照の一文」。

    参照の一文だけでは主題が分からないことが多い（「この場合の扱いは、注 2 による。」）。主題は見出しと直前の文にあるので、
    それを付けてリランカーに渡す。参照の一文が本文に見つからない場合は、見出しと一文だけにする。
    直前の文が、同じチャンクの別の参照の一文（other_quotes）であるときは付けない。その文の主題は、その文自身の参照のものであり、
    付けると、質問に合う参照の一文の「次の文」にある別の参照まで高い点数になるためである。
    """
    quote = (quote or "").strip()
    text = source_text or ""
    previous = ""
    at = _locate_quote(text, quote)
    if at > 0:
        head = text[:at].rstrip()
        # 直前の文の始まり = その前の句点か改行の次。head の末尾の句点（直前の文の終わり）は区切りに数えない
        cut = max(head.rfind("。", 0, max(0, len(head) - 1)), head.rfind("\n", 0, max(0, len(head) - 1)))
        previous = head[cut + 1:].strip()[-REFERENCE_PREVIOUS_CHARS:]
        if previous:
            # 別の参照の一文かどうかは、空白の違いを無視して見る（一文と本文で改行や空白が違うことがある）
            squeezed = _squeeze(previous)
            if any(other and (squeezed in _squeeze(other) or _squeeze(other) in squeezed) for other in other_quotes):
                previous = ""
    body = f"{previous} {quote}".strip() if previous else quote
    heading = (heading or "").strip()
    return f"{heading}: {body}" if heading and body else (body or heading)


def _group_links(rows: List[dict], quotes_of: Optional[dict] = None, history_weight: float = 1.0) -> List[dict]:
    """リンクの行を「参照」ごとにまとめる。同じ参照元・同じ一文・同じ場所の語のリンクが 1 つの参照である。

    規則のリンクは、項が複数のチャンクに続くとき、先頭と続きの両方へ張られる。それらは 1 つの参照の候補として束ね、
    足すのは 1 つだけにする（どれを足すかは、質問と参照の一文に合う本文のものを後で選ぶ）。
    一文の無いリンク（別の仕組みが張ったもの）は束ねない。何が同じ参照かを示すものが無く、束ねると、別々の参照先のうち 1 つしか足せなくなる。
    quotes_of は、参照元ごとの参照の一文の集まり（たどらないリンクの分も含む）。直前の文が別の参照の一文かどうかを見るのに使う。
    """
    groups, index = [], {}
    if quotes_of is None:
        quotes_of = {}
        for row in rows:
            if row.get("quote"):
                quotes_of.setdefault(row["source"], set()).add(row["quote"].strip())
    for row in rows:
        quote = (row.get("quote") or "").strip()
        # 一文が無いリンクは、参照先ごとに別の参照として扱う
        target_key = "" if quote else (row.get("id") or row.get("file_name") or "")
        key = (row["source"], quote, row.get("locator") or "", row["kind"], target_key)
        group = index.get(key)
        if group is None:
            group = {"source": row["source"], "quote": quote, "locator": row.get("locator") or "", "kind": row["kind"],
                     "form": row.get("form"), "method": row.get("method"), "source_section": row.get("source_section") or "body",
                     "passage": reference_passage(row.get("source_text"), row.get("source_heading"), quote,
                                                  quotes_of.get(row["source"], set()) - {quote}) if quote else "",
                     "targets": [], "documents": [], "link_score": 0.0, "score": 0.0}
            group["weight"] = history_weight if group["source_section"] == "revision_history" else 1.0
            index[key] = group
            groups.append(group)
        if row["kind"] == "document":
            if row.get("file_name") and row["file_name"] not in group["documents"]:
                group["documents"].append(row["file_name"])
        elif row.get("id") and row.get("text") and all(t["id"] != row["id"] for t in group["targets"]):
            group["targets"].append({"id": row["id"], "text": row["text"], "position": row.get("position"),
                                     "file_name": row.get("file_name"), "doc_source": row.get("doc_source") or row.get("file_name")})
    return groups


def _reference_heading(entry: dict) -> str:
    """足した参照先の前に置く見出し。何の参照先かを、参照の一文で示す。"""
    quote = " ".join((entry.get("quote") or "").split())
    if len(quote) > REFERENCE_QUOTE_CHARS:
        quote = "…" + quote[-REFERENCE_QUOTE_CHARS:]
    where = "上の参照先" if entry.get("hop", 1) >= 2 else "上の本文"
    origin = f"。出典: {entry['file_name']}" if entry.get("file_name") else ""
    if not quote:
        return f"参照先（{where}が参照している箇所{origin}）:"
    return f"参照先（{where}の「{quote}」が指す箇所{origin}）:"


# 参照先を参照元の直後へ移した結果、本文が空になった文書行に置く断り書き
REFERENCE_MOVED_NOTE = "（この文書の該当箇所は、参照している本文の直後に「参照先」として示している）"

# 応答（/retrieve_context、/chat_bot）に載せる、足した参照先ごとの項目
REFERENCE_TRACE_KEYS = ("chunk_id", "from_chunk_id", "quote", "locator", "score", "link_score", "hop", "order", "target_kind", "form",
                        "source_section", "method", "file_name", "source", "position", "select", "shown_in")


def collect_reference_trace(docs, sources=None) -> list:
    """retriever が文書行に控えた「足した参照先」を集める。sources を渡すと、その出典の文書行に示したものだけにする。

    1 件 = {chunk_id: 足したチャンク, from_chunk_id: 参照元のチャンク, quote: 参照の一文, score: 点数, hop: 何回目のたどりか,
            order: 足した順（1 から）, source: 足したチャンクの文書（文書行の出典と同じ式）, shown_in: 文脈のどの文書行に示したか,
            select: link（参照の一文で選んだ）か target（参照先の本文の近さで選んだ）, ...}。足した順に並べて返す。
    """
    out, seen = [], set()
    for doc in docs or []:
        for entry in (getattr(doc, "metadata", None) or {}).get("references") or []:
            if entry.get("chunk_id") in seen or (sources is not None and entry.get("shown_in") not in sources):
                continue
            seen.add(entry.get("chunk_id"))
            out.append(entry)
    out.sort(key=lambda e: e.get("order") or 0)
    return out


def reference_sources(references) -> set:
    """足した参照先の文書の出典（文書行の metadata.source と同じ式）。

    参照元の直後に示した参照先は、自分の文書の行ではなく、参照元の文書行の中にある。出典の一覧を作る側は、これを足して、
    文脈に本文がある文書を漏らさないようにする。従来の選び方で足した分は、自分の文書の行にあるので数えない
    （従来の設定のままなら、出典の一覧は従来と同じになる）。
    """
    return {entry["source"] for entry in references or [] if entry.get("select") == "link" and entry.get("source")}


def _target_entry(doc, score) -> dict:
    """従来の選び方（参照先の本文と質問の近さ）で足したチャンクの控え。どのリンクで届いたかは持たない。"""
    return {"chunk_id": doc.metadata["chunk_id"], "text": doc.page_content, "doc_source": doc.metadata.get("source"), "file_name": doc.metadata.get("source"),
            "position": doc.metadata.get("position"), "score": float(score), "chunk_score": score, "link_score": None, "hop": 1,
            "from_chunk_id": None, "quote": None, "locator": None, "form": None, "method": None, "source_section": None,
            "target_kind": "chunk", "select": "target", "passage": ""}


def _is_note_only(doc) -> bool:
    """本文が断り書きだけで、エンティティとリレーションの行も残っていない文書行か。"""
    head, ents, rels = _split_sections(doc.page_content)
    return not ents and not rels and head.replace("Text Content:\n", "", 1).strip() == REFERENCE_MOVED_NOTE


class RerankGraphRetriever(BaseRetriever):
    """hybrid 候補 → リランク → 選んだチャンクだけ展開 → 文字数予算。"""

    neo_db: Any                    # モード本来の retrieval_query を持つ Neo4jVector（展開に使う）
    candidate_db: Any              # CHUNK_CANDIDATE_QUERY を持つ Neo4jVector（候補取得に使う）
    retrieval_query: str
    document_names: Optional[List[str]] = None
    candidate_k: int = 20
    top_n: int = 6
    char_budget: int = 14000
    ef_ratio: int = 5
    score_threshold: float = 0.0
    line_max: int = 120
    line_min_score: float = 0.0
    settings: dict = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True

    def _candidates(self, query: str):
        kwargs = {"k": self.candidate_k, "effective_search_ratio": self.ef_ratio}
        if self.document_names:
            kwargs["filter"] = {"fileName": {"$in": self.document_names}}
        rows = self.candidate_db.similarity_search_with_score(query, **kwargs)
        return [(d, s) for d, s in rows if s is None or s >= self.score_threshold]

    def _referenced(self, query: str, chosen, limit: int):
        """選んだチャンクから参照リンクを 1 つたどり、参照先のチャンクを質問でリランクして上位 limit 件を返す。

        参照先は選んだチャンクと同じ扱い（本文を文脈に入れ、グラフ展開の起点にする）になる。
        """
        ids = [d.metadata["chunk_id"] for d, _ in chosen]
        rows = self.neo_db.query(
            "UNWIND $ids AS id MATCH (c:Chunk {id: id})-[:REFERS_TO]->(t:Chunk) WHERE NOT t.id IN $ids "
            "OPTIONAL MATCH (t)-[:PART_OF]->(d:Document) "
            "RETURN DISTINCT t.id AS id, t.text AS text, t.position AS position, d.fileName AS source LIMIT 100",
            params={"ids": ids})
        if self.document_names:
            rows = [r for r in rows if r.get("source") in self.document_names]
        rows = [r for r in rows if r.get("text")]
        if not rows:
            return []
        scores = _rerank(query, [r["text"] for r in rows])
        ranked = sorted(zip(rows, scores), key=lambda x: -x[1])[:limit]
        return [(Document(page_content=r["text"], metadata={"chunk_id": r["id"], "source": r.get("source"), "position": r.get("position"),
                                                            "via_reference": True}), sc) for r, sc in ranked]

    # ---------- 参照の一文で選ぶたどり方（reference_select = link） ----------
    def _query_vector(self, query: str, cache: dict):
        key = ("query_vector", query)
        if key not in cache:
            cache[key] = self.neo_db.embedding.embed_query(query)
        return cache[key]

    def _reference_links(self, ids: List[str], taken: set):
        """ids のチャンクから出る参照リンクを読む。既に文脈にあるチャンク（taken）へのリンクと、絞り込みの外の文書へのリンクは除く。

        返り値: (たどる候補のリンクの行, 参照元ごとの参照の一文の集まり)。後者は、除いたリンクの分も含む。
        """
        if not ids:
            return [], {}
        rows = self.neo_db.query(REFERENCE_LINK_QUERY, params={"ids": list(ids)})
        out, quotes_of = [], {}
        for row in rows:
            if row.get("quote"):
                quotes_of.setdefault(row["source"], set()).add(row["quote"].strip())
            if row.get("kind") == "chunk" and (row.get("id") in taken or not row.get("text")):
                continue
            if self.document_names and row.get("file_name") not in self.document_names:
                continue
            out.append(row)
        return out, quotes_of

    def _score_groups(self, groups: List[dict], query_of) -> None:
        """参照ごとに「参照の一文と問いの近さ」を付ける。問いは参照元ごとに決まる（query_of(参照元の ID)）。

        一文の無いリンク（別の仕組みが張ったもの）は、参照先の本文と問いの近さで代える。文書だけを指していて一文も無いものは 0 点。
        """
        by_query = {}
        for group in groups:
            by_query.setdefault(query_of(group["source"]), []).append(group)
        for query, members in by_query.items():
            texts, slots = [], []
            for group in members:
                if group["passage"]:
                    texts.append(group["passage"])
                    slots.append((group, None))
                else:
                    for target in group["targets"]:
                        texts.append(target["text"])
                        slots.append((group, target))
            if not texts:
                continue
            for (group, target), score in zip(slots, _rerank(query, texts)):
                group["link_score"] = max(group["link_score"], float(score))

    def _resolve_target(self, question: str, group: dict, taken: set, min_score: float, cache: dict):
        """参照 1 つにつき、足すチャンクを 1 つ決める。

        チャンクへのリンク: 候補が 1 つならそれ。複数（項が複数のチャンクに続く場合）は、質問と参照の一文に合う本文のものにする。
        文書へのリンク: その文書のチャンクを候補にし、質問と参照の一文に合うものにする。合うものが無ければ足さない（下限は同じ値）。
        """
        if group["kind"] == "document":
            if not group["documents"]:
                return None
            # 候補の絞り込み（大きい文書だけに効く）も、選ぶときと同じ「質問 + 参照の一文」で行う。
            # 質問だけで絞ると、質問と似ていない答えの箇所（参照をたどる理由そのもの）が候補から外れる
            rows = self.neo_db.query(REFERENCE_DOCUMENT_CHUNKS_QUERY, params={
                "docs": group["documents"], "skip": list(taken),
                "query_vector": self._query_vector(f"{question} {group['quote']}".strip(), cache),
                "per_document": REFERENCE_DOCUMENT_CANDIDATES})
            candidates = [{"id": r["id"], "text": r["text"], "position": r.get("position"), "file_name": r.get("file_name"),
                           "doc_source": r.get("doc_source") or r.get("file_name")} for r in rows if r.get("text") and r["id"] not in taken]
            if not candidates:
                return None
            scores = _rerank(f"{question} {group['quote']}".strip(), [c["text"] for c in candidates])
            best = max(range(len(candidates)), key=lambda i: scores[i])
            return candidates[best] if scores[best] >= min_score else None
        candidates = [t for t in group["targets"] if t["id"] not in taken]
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        scores = _rerank(f"{question} {group['quote']}".strip(), [c["text"] for c in candidates])
        return candidates[max(range(len(candidates)), key=lambda i: scores[i])]

    def _follow_links(self, query: str, chosen, limit: int, with_target: bool = False) -> List[dict]:
        """参照リンクを、参照の一文と質問の近さで選んでたどる。足すチャンクの一覧（1 回目 → 2 回目 → 従来の選び方の分）を返す。

        1 回目: 選んだチャンクから出る参照ごとに、点数 = リランク(質問, 見出し + 直前の一文 + 参照の一文) × 改正履歴の重み
                （直前の一文が別の参照の一文なら付けない）。
                点数が下限以上の参照を、高い順に limit 件までたどる。同じチャンクへ行く参照は、点数の高い 1 つにまとめる。
        2 回目: 1 回目で足したチャンクの中にある参照を、問いを「質問 + 1 回目の参照の一文」に替えて同じように測る。
                点数 = 親の点数 × その近さ。親 1 つにつき、点数の最も高い 1 件だけを足す（下限は同じ値）。
                2 回目の点数は、親の中の参照のほとんどで高くなり、どれが要るかを見分けられないので、数で抑える。
                2 回目は、チャンクへのリンクだけをたどる。2 回目が失敗しても、1 回目に足した分は残す。
        従来の選び方の分（with_target = True。設定 reference_select = both）: 従来の選び方（参照先の本文と質問の近さ。_referenced）の
                上位 limit 件を、そのまま足す（一文で選んだものと重なる分は 1 回だけ）。従来の設定で文脈に入っていた参照先は、必ず残る。
                一文で選ぶ参照が無い質問では、従来と同じ結果になる。
        参照元のチャンクの順位は、同点のときの並び順に使う。参照先の本文と質問の近さは、一文で選ぶ分の基準にはしない。
        """
        taken = {d.metadata["chunk_id"] for d, _ in chosen}
        picks: List[dict] = []
        try:
            self._link_picks(query, chosen, limit, taken, picks)
        except Exception as exc:
            if not with_target and not picks:
                raise
            # 途中で失敗しても、そこまでに選んだ分は残す。both では、従来の選び方の分も足す（both は従来を含む、という約束を守る）
            logging.warning(f"selecting references by their sentences failed ({exc}); keeping {len(picks)} selected so far")
        if with_target:
            try:
                for doc, score in self._referenced(query, chosen, limit):
                    if doc.metadata["chunk_id"] not in taken:
                        taken.add(doc.metadata["chunk_id"])
                        picks.append(_target_entry(doc, score))
            except Exception as exc:
                if not picks:
                    raise
                logging.warning(f"selecting references by the target text failed ({exc}); keeping the ones selected by their sentences")
        return picks

    def _link_picks(self, query: str, chosen, limit: int, taken: set, picks: List[dict]) -> None:
        """参照の一文で選ぶ分（1 回目と 2 回目）を picks に足す。途中で失敗したら、そこまでに足した分を残して例外を上げる。"""
        settings = self.settings
        min_score = float(settings.get("reference_min_score", 0.3))
        depth = int(settings.get("reference_depth", 1) or 1)
        follow_documents = bool(settings.get("reference_documents", True))
        history_weight = float(settings.get("reference_history_weight", 1.0))
        cache: dict = {}
        rank = {d.metadata["chunk_id"]: i for i, (d, _) in enumerate(chosen)}
        chosen_score = {d.metadata["chunk_id"]: float(sc) for d, sc in chosen}

        def entry(group, target, hop, score, root_score):
            # chunk_score = 並べるための点数。参照元（選んだチャンク）の点数 × 参照の点数。参照先が参照元より前に並ばないようにする
            return {"chunk_id": target["id"], "text": target["text"], "doc_source": target.get("doc_source"), "file_name": target.get("file_name"),
                    "position": target.get("position"), "score": float(score), "link_score": float(group["link_score"]), "hop": hop,
                    "from_chunk_id": group["source"], "quote": group["quote"], "locator": group["locator"], "form": group.get("form"),
                    "method": group.get("method"), "source_section": group["source_section"], "target_kind": group["kind"], "select": "link",
                    "passage": group["passage"], "root_score": float(root_score), "chunk_score": float(root_score) * float(score)}

        # ---- 1 回目 ----
        groups = _group_links(*self._reference_links(list(rank), taken), history_weight=history_weight)
        if not follow_documents:
            groups = [g for g in groups if g["kind"] != "document"]
        self._score_groups(groups, lambda _source: query)
        for group in groups:
            group["score"] = group["link_score"] * group["weight"]
        groups.sort(key=lambda g: (-g["score"], rank.get(g["source"], 10 ** 6)))
        first = []
        for group in groups:
            if len(first) >= limit or group["score"] < min_score or group["score"] <= 0:
                break
            target = self._resolve_target(query, group, taken, min_score, cache)
            if target is None:
                continue
            taken.add(target["id"])
            first.append(entry(group, target, 1, group["score"], chosen_score.get(group["source"], 0.0)))
            picks.append(first[-1])
        if depth < 2 or not first:
            return

        # ---- 2 回目（失敗しても 1 回目の分は残す）----
        try:
            parents = {e["chunk_id"]: e for e in first}
            groups = [g for g in _group_links(*self._reference_links(list(parents), taken), history_weight=history_weight) if g["kind"] == "chunk"]
            self._score_groups(groups, lambda source: f"{query} {parents[source]['passage'] or parents[source]['quote']}".strip())
            queues = {}
            for group in groups:
                group["score"] = parents[group["source"]]["score"] * group["link_score"] * group["weight"]
                if group["score"] >= min_score and group["score"] > 0:
                    queues.setdefault(group["source"], []).append(group)
            for parent in first:                       # 親の点数の高い順に、1 件ずつ
                for group in sorted(queues.get(parent["chunk_id"]) or [], key=lambda g: -g["score"]):
                    target = self._resolve_target(query, group, taken, min_score, cache)
                    if target is None:
                        continue
                    taken.add(target["id"])
                    picks.append(entry(group, target, 2, group["score"], parent["root_score"]))
                    break
        except Exception as exc:
            logging.warning(f"following references a second time failed ({exc}); keeping the first-hop references")

    def _expand(self, query: str, chosen):
        chunk_rows = [{"id": d.metadata["chunk_id"], "score": float(s)} for d, s in chosen]
        body = _INDEX_HEAD.sub(_CHUNK_HEAD, self.retrieval_query, count=1)
        params = {"chunk_rows": chunk_rows}
        if "$query_vector" in body:
            params["query_vector"] = self.neo_db.embedding.embed_query(query)
        records = self.neo_db.query(body, params=params)
        docs = []
        for rec in records:
            md = rec.get("metadata") or {}
            doc = DocWithState(page_content=rec.get("text") or "", metadata=md)
            docs.append(doc)
        return docs

    def _present_references(self, docs, chosen, trace: List[dict]) -> set:
        """足した参照先を文脈のどこに示すかを決め、文書行に控え（metadata.references）を付ける。

        返り値は、参照元の直後へ移したチャンクの ID の集まり（従来の並びのままなら空）。
        見せ方の処理が失敗しても検索は続ける（従来の並びのまま、控えだけを付ける）。
        """
        text_of = {d.metadata["chunk_id"]: d.page_content for d, _ in chosen}
        row_of = {}
        for doc in docs:
            for detail in doc.metadata.get("chunkdetails") or []:
                row_of.setdefault(detail.get("id"), doc)
        shown_in = {e["chunk_id"]: (row_of[e["chunk_id"]].metadata.get("source") if e["chunk_id"] in row_of else None) for e in trace}
        moved = set()
        if self.settings.get("reference_presentation", "separate") == "inline":
            try:
                moved, placed = self._inline_references(docs, text_of, row_of, trace)
                shown_in.update(placed)
            except Exception as exc:
                logging.warning(f"reference presentation failed ({exc}); keeping the original layout")
                moved = set()

        by_source = {}
        for order, e in enumerate(trace, start=1):
            public = {key: e.get(key) for key in REFERENCE_TRACE_KEYS}
            public["order"] = order
            public["source"] = e.get("doc_source")
            public["shown_in"] = shown_in.get(e["chunk_id"])
            by_source.setdefault(public["shown_in"], []).append(public)
        for doc in docs:
            entries = by_source.get(doc.metadata.get("source"))
            if entries:
                doc.metadata["references"] = entries
        return moved

    def _inline_references(self, docs, text_of: dict, row_of: dict, trace: List[dict]):
        """参照の一文で選んだ参照先を、参照元のチャンクの直後へ移す。返り値: (移したチャンクの ID の集まり, {チャンクの ID: 示した文書行の出典})

        取り出しの照会は、文書ごとに「本文 1 ----  本文 2 ---- … Entities … Relationships …」という 1 行にまとめて返す。
        その本文の部分だけを組み直す:
          - 足した参照先を、参照元のチャンクの直後に、見出し（参照先（上の本文の「…」が指す箇所。出典: …）:）を付けて置く
          - 参照先は、元の文書行の本文からは外す（同じ本文を 2 回入れない）。本文が空になった行には断り書きを置く
          - チャンクの項目（metadata.chunkdetails）は、本文と一緒に、示した文書行へ移す。文書行は上限（CHAT_PROMPT_TOKEN_CUTOFF）で
            切られることがあり、本文と項目が別々の行にあると、片方だけが落ちて「出典の一覧にあるのに本文が無い」「本文があるのに一覧に無い」になる
          - Entities と Relationships の部分、metadata.entities は変えない
        従来の選び方で足したもの（select = target。どの一文からたどったかを持たない）は移さない。
        本文の部分を組み直せない文書行が 1 つでもあれば、何も変えずに従来の並びのままにする（書き換えは、全部の行の組み直しが済んでから行う）。
        """
        children = {}
        for e in trace:
            # 参照元が文脈にあり、自分も文書行に載っているものだけを移す
            if e.get("select") == "link" and e.get("from_chunk_id") in row_of and e["chunk_id"] in row_of and e["chunk_id"] in text_of:
                children.setdefault(e["from_chunk_id"], []).append(e)
        moved = {e["chunk_id"] for group in children.values() for e in group}
        if not moved:
            return set(), {}
        plans, detail_of = [], {}
        for doc in docs:
            details = list(doc.metadata.get("chunkdetails") or [])
            ids = [detail.get("id") for detail in details]
            prefix = "Text Content:\n" if doc.page_content.startswith("Text Content:\n") else ""
            expected = prefix + "\n----\n".join(text_of.get(i, "") for i in ids)
            tail = doc.page_content[len(expected):]
            # 本文の部分が「選んだチャンクの本文を ---- でつないだもの」と一致し、その後ろが行の部分（または何も無い）であること
            if any(i not in text_of for i in ids) or not doc.page_content.startswith(expected) or not (tail == "" or tail.startswith("\n----\nEntities:\n")):
                logging.warning("reference presentation: a document row does not match its chunks; keeping the original layout")
                return set(), {}
            for detail in details:
                detail_of.setdefault(detail.get("id"), detail)
            plans.append((doc, ids, prefix, tail))

        placed = {}

        def block(chunk_id, source, details_out, guard):
            details_out.append(detail_of[chunk_id])
            text = text_of[chunk_id]
            for child in children.get(chunk_id, []):
                child_id = child["chunk_id"]
                if child_id in guard or child_id in placed:
                    continue
                placed[child_id] = source
                text += "\n\n" + _reference_heading(child) + "\n" + block(child_id, source, details_out, guard | {child_id})
            return text

        results = []
        for doc, ids, prefix, tail in plans:
            details_out = []
            blocks = [block(i, doc.metadata.get("source"), details_out, {i}) for i in ids if i not in moved]
            results.append((doc, prefix + ("\n----\n".join(blocks) if blocks else REFERENCE_MOVED_NOTE) + tail, details_out))
        if set(placed) != moved:
            # 参照元の本文までたどれない参照先がある。本文を落とさないために、組み直しをやめる
            logging.warning("reference presentation: a followed chunk has no place under its source; keeping the original layout")
            return set(), {}
        for doc, content, details_out in results:
            doc.page_content = content
            doc.metadata["chunkdetails"] = details_out
        return moved, placed

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> List[Document]:
        cands = self._candidates(query)
        if not cands:
            return []
        try:
            scores = _rerank(query, [d.page_content for d, _ in cands])
        except Exception as e:
            logging.warning(f"rerank failed ({e}); falling back to hybrid order")
            scores = [s or 0.0 for _, s in cands]
        ranked = sorted(zip(cands, scores), key=lambda x: -x[1])[: self.top_n]
        chosen = [(d, sc) for (d, _), sc in ranked]
        logging.info("rerank picked: " + ", ".join(f"{d.metadata.get('source','?')}#{d.metadata.get('position')}:{sc:.3f}" for d, sc in chosen))
        reference_max = int(self.settings.get("reference_max") or 0)
        trace = []
        if reference_max > 0:
            try:
                select = self.settings.get("reference_select", "target")
                if select in ("link", "both"):
                    trace = self._follow_links(query, chosen, reference_max, with_target=(select == "both"))
                else:
                    # 従来の選び方は、どのリンクで届いたかを区別しない。控えには、足したチャンクと点数だけを残す
                    trace = [_target_entry(d, sc) for d, sc in self._referenced(query, chosen, reference_max)]
            except Exception as e:
                logging.warning(f"following references failed ({e}); continuing without them")
                trace = []
            followed = [(Document(page_content=e["text"], metadata={"chunk_id": e["chunk_id"], "source": e["doc_source"], "position": e["position"],
                                                                    "via_reference": True}), e["chunk_score"]) for e in trace]
            if followed:
                logging.info("reference followed: " + ", ".join(f"{d.metadata.get('source','?')}#{d.metadata.get('position')}:{sc:.3f}" for d, sc in followed))
                chosen = chosen + followed
        docs = self._expand(query, chosen)
        moved = set()
        if trace:
            try:
                moved = self._present_references(docs, chosen, trace)
            except Exception as e:
                logging.warning(f"recording the followed references failed ({e}); continuing without the record")
        # 文書行の並び＝その文書に含まれる選ばれたチャンクの最大リランクスコア。
        # 参照元の直後へ移した参照先は数えない（本文は参照元の文書行にあり、その行は参照元の点数で並ぶ）。
        # 本文が断り書きだけになった行は 0 点で末尾に回り、直接選ばれた本文の行を上限から押し出さない
        best = {}
        for d, sc in chosen:
            if d.metadata.get("chunk_id") in moved:
                continue
            best[d.metadata.get("source")] = max(best.get(d.metadata.get("source"), 0.0), sc)
        docs.sort(key=lambda d: -best.get(d.metadata.get("source"), 0.0))
        for d in docs:
            d.state["query_similarity_score"] = best.get(d.metadata.get("source"), 0.0)
        if get_value_from_env("CHAT_LINE_RERANK_ENABLED", "true", "bool"):
            out = _apply_budget_reranked(query, docs, self.char_budget, self.line_max, self.line_min_score)
        else:
            out = _apply_budget(docs, self.char_budget)
        if moved:
            # 本文を参照元の直後へ移した結果、断り書きだけになり、行（エンティティ・リレーション）も残らなかった文書行は外す
            out = [d for d in out if not _is_note_only(d)]
        return out


def build_candidate_db(neo_db: Neo4jVector, chat_mode_settings, embedding_function, graph) -> Neo4jVector:
    """モードと同じ索引で、候補チャンクだけを返す Neo4jVector を作る。"""
    keyword_index = chat_mode_settings.get("keyword_index", "")
    common = dict(
        embedding=embedding_function,
        index_name=chat_mode_settings.get("index_name"),
        retrieval_query=CHUNK_CANDIDATE_QUERY,
        graph=graph,
        node_label=chat_mode_settings.get("node_label"),
        embedding_node_property=chat_mode_settings.get("embedding_node_property"),
        text_node_properties=chat_mode_settings.get("text_node_properties"),
    )
    if keyword_index:
        return Neo4jVector.from_existing_graph(search_type="hybrid", keyword_index_name=keyword_index, **common)
    return Neo4jVector.from_existing_graph(**common)


def make_rerank_retriever(neo_db, graph, chat_mode_settings, document_names, embedding_function, score_threshold):
    from src.shared.constants import VECTOR_GRAPH_SEARCH_QUERY
    s = chat_mode_settings.get("retrieval_settings") or resolve_retrieval_settings()
    retrieval_query = chat_mode_settings["retrieval_query"]
    if retrieval_query == VECTOR_GRAPH_SEARCH_QUERY:
        # グラフ展開のあるモードは、設定から取り出し照会を組み立てる
        retrieval_query = build_expansion_query(s)
    logging.info(f"Retrieval settings: {s}")
    return RerankGraphRetriever(
        neo_db=neo_db,
        candidate_db=build_candidate_db(neo_db, chat_mode_settings, embedding_function, graph),
        retrieval_query=retrieval_query,
        document_names=document_names if (document_names and chat_mode_settings.get("document_filter")) else None,
        candidate_k=s["candidate_k"],
        top_n=s["rerank_top_n"],
        char_budget=s["char_budget"],
        ef_ratio=get_value_from_env("EFFECTIVE_SEARCH_RATIO", 5, "int"),
        score_threshold=s["candidate_min_score"],
        line_max=s["line_max"],
        line_min_score=s["line_min_score"],
        settings=s,
    )
