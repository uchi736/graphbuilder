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
文書だけを指す参照（項・別表の指定の無いもの）の中の選び方は、設定 reference_doc_select で切り替える（11/9）。
  rerank       … 従来。文書のチャンクから「質問 + 参照の一文」のリランカーで 1 件
  summary_list … 文書の節の要点（後処理 build_section_summaries）の一覧を LLM に見せて最大 reference_doc_max 節を選ばせ、
                 節ごとにチャンクを 1 件。節の数が reference_doc_two_stage_sections を超える文書は章を先に選ぶ。
                 足すかどうかの判定は rerank と同じ: 選んだ節のチャンクの点数（質問 + 参照の一文とのリランク）が
                 reference_min_score に満たない節は足さない。全部満たなければ rerank に戻す（rerank も同じ下限で判定する）。
                 2 件目以降は、本文（選んだチャンク + 参照で足すチャンク。both の従来の選び方の分を含む）が文脈の文字数の上限の
                 (1 - REFERENCE_EXTRA_LINE_SHARE) に収まる分だけ足す（エンティティ・リレーションの行の分を残す）。
                 2 回目のたどりでは、参照先 1 つにつき 1 件（rerank と同じ数）。
                 失敗したら rerank に戻す（要点のある節が 1 つも無い文書も戻す）。1 つの質問の中で LLM が一度失敗したら、
                 残りの参照は LLM を呼ばずに rerank で選ぶ。部品は src/section_summaries.py

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

# 経由点の表示名（M6）。規則の目録から作った節（source = 'catalogue'）は題名に番号を含まないので「番号 題名」（「3-5 潤滑油供給条件」）にする。
# Markdown の見出しから作った節は、題名が見出しの文字列そのもの（「(2) 計測」「3-1 .サージ誤差計算」）で番号を含むので、題名のまま。
# 題名の先頭の字で判定しない（章 3 の題名が「3台…」のように数字で始まると番号が消え、「(2) 計測」は「2 (2) 計測」になるため）
_SECTION_LABEL = ("CASE WHEN s.number IS NULL OR s.number = '' OR coalesce(s.source, '') <> 'catalogue' "
                  "OR coalesce(s.title, '') IN ['', s.number] THEN coalesce(s.title, s.number) "
                  "ELSE s.number + ' ' + s.title END")

# 章・節を指す参照（Chunk -REFERS_TO-> Section。章・節の層の後処理が規則で生成）。節と下位の節（3 段まで）に属するチャンクを参照先の候補にする。
# 候補は、その節（か下位の節）を主所属（IN_SECTION.primary）とするチャンクを先に、足りなければ副所属のチャンクも採る（M5）。
# 同じ所属の中は、質問との埋め込みの近さの順（$query_vector が無い場合は位置の順）。上限は $per_section 件
SECTION_REFERENCE_QUERY = """
UNWIND $ids AS id
MATCH (c:Chunk {id: id})-[r:REFERS_TO]->(s:Section)
MATCH (s)-[:HAS_SECTION*0..3]->(:Section)<-[m:IN_SECTION]-(t:Chunk)
WHERE NOT t.id IN $ids AND t.text IS NOT NULL
MATCH (t)-[:PART_OF]->(d:Document)
WITH c, r, s, t, d, max(CASE WHEN m.primary = true THEN 1 ELSE 0 END) AS prim
WITH c, r, s, t, d, prim,
     CASE WHEN $query_vector IS NULL OR t.embedding IS NULL THEN 0.0 ELSE vector.similarity.cosine(t.embedding, $query_vector) END AS sim
ORDER BY prim DESC, sim DESC, t.position
WITH c, r, s, collect({id: t.id, text: t.text, position: t.position, file_name: d.fileName, doc_source: __SOURCE__})[0..$per_section] AS rows
UNWIND rows AS row
RETURN c.id AS source, c.text AS source_text, c.section AS source_heading, r.quote AS quote, r.method AS method,
       s.id AS section_id, __LABEL__ AS section_title, s.fileName AS section_file, 'chunk' AS kind,
       row.id AS id, row.text AS text, row.position AS position, row.file_name AS file_name, row.doc_source AS doc_source
LIMIT 2000
""".replace("__SOURCE__", _SOURCE_OF_D).replace("__LABEL__", _SECTION_LABEL)

# 文書番号を経由する文書間の参照（文書層: Document -REFERS_TO-> Standard <-IS_VERSION_OF- Document）。
# 文書層の参照はチャンクの ID を持たないため、引用文（quotes）を本文に含むチャンクを参照元とする（空白・改行の違いは無視する）。
# sections: 参照元のチャンクから参照先の文書の節への、文書番号と番号で決めた線（章・節の層の後処理が作る via = standard の REFERS_TO、M3）。
#           あれば、参照先の文書の中はその節のチャンクだけから選ぶ（節のチャンクが全部文脈にあれば何も足さない。文書全体には戻らない）。
#           章・節の参照の追跡（reference_sections）も有効なら、同じ線を章・節の照会がたどるので、文書層の側ではたどらない。
#           無ければ従来どおり文書全体から近さで選ぶ
# 現行版: 文書番号に現行版の印（Standard -HAS_CURRENT_VERSION-> Document、文書層の後処理が付ける）があれば、その版だけをたどる（古い版へは行かない）。
#         印が無い文書番号（文書層を流し直す前のグラフ）は、従来どおり全部の版をたどる
DOCUMENT_LAYER_REFERENCE_QUERY = """
UNWIND $ids AS id
MATCH (c:Chunk {id: id})-[:PART_OF]->(d:Document)-[r:REFERS_TO]->(st:Standard)<-[:IS_VERSION_OF]-(d2:Document)
WHERE d2 <> d AND c.text IS NOT NULL
  AND (NOT EXISTS { MATCH (st)-[:HAS_CURRENT_VERSION]->(:Document) } OR EXISTS { MATCH (st)-[:HAS_CURRENT_VERSION]->(d2) })
WITH c, st, d2, replace(replace(replace(c.text, ' ', ''), '\\n', ''), '　', '') AS body,
     [q IN coalesce(r.quotes, []) WHERE size(trim(q)) > 0] AS quotes
WITH c, st, d2, [q IN quotes WHERE body CONTAINS replace(replace(replace(q, ' ', ''), '\\n', ''), '　', '')] AS hits
WHERE size(hits) > 0
OPTIONAL MATCH (c)-[rs:REFERS_TO]->(sec:Section)
WHERE rs.via = 'standard' AND rs.standard = st.id AND sec.fileName = d2.fileName
WITH c, st, d2, hits, [x IN collect(DISTINCT sec.id) WHERE x IS NOT NULL] AS sections
RETURN DISTINCT c.id AS source, c.text AS source_text, c.section AS source_heading, hits[0] AS quote, 'document' AS kind,
       st.id AS standard, d2.fileName AS file_name, sections
LIMIT 500
"""

# 文書層の参照が節まで決まっているとき（上の sections）の、参照先の候補のチャンク。節と下位の節（3 段まで）に属するチャンクを、
# 主所属のものを先に、同じ所属の中は「質問 + 参照の一文」との埋め込みの近さの順に、$limit 件まで
STANDARD_SECTION_CHUNKS_QUERY = """
UNWIND $sections AS sid
MATCH (s:Section {id: sid})-[:HAS_SECTION*0..3]->(:Section)<-[m:IN_SECTION]-(t:Chunk)
WHERE t.text IS NOT NULL AND NOT t.id IN $skip
MATCH (t)-[:PART_OF]->(d:Document)
WITH t, d, max(CASE WHEN m.primary = true THEN 1 ELSE 0 END) AS prim
WITH t, d, prim,
     CASE WHEN $query_vector IS NULL OR t.embedding IS NULL THEN 0.0 ELSE vector.similarity.cosine(t.embedding, $query_vector) END AS sim
ORDER BY prim DESC, sim DESC, t.position
RETURN t.id AS id, t.text AS text, t.position AS position, d.fileName AS file_name, __SOURCE__ AS doc_source
LIMIT $limit
""".replace("__SOURCE__", _SOURCE_OF_D)

# 文書のみを指す参照の一覧選択（reference_doc_select = summary_list）の候補の節。節ごとに、親の節・要点・主所属のチャンクの本文の先頭
REFERENCE_DOC_SECTIONS_QUERY = """
UNWIND $docs AS name
MATCH (s:Section {fileName: name})
WHERE s.method = 'rule'
OPTIONAL MATCH (p:Section)-[:HAS_SECTION]->(s)
OPTIONAL MATCH (s)<-[m:IN_SECTION]-(c:Chunk)
WHERE m.primary = true AND c.text IS NOT NULL
WITH s, p, c ORDER BY c.position
WITH s, p, collect(c.text)[0] AS head
RETURN s.id AS id, s.fileName AS file_name, s.path AS path, s.title AS title, s.number AS number, s.level AS level,
       p.id AS parent, s.summary AS summary, left(coalesce(head, s.text, ''), $head_chars) AS head
"""

# 一覧選択で選んだ節から足すチャンクの候補。段（tier）は 3 = その節を主所属とするチャンク、2 = 下位の節（3 段まで）を主所属とするチャンク、
# 1 = その節に副所属で属するチャンク（目録で切り出した小さな節は、前の節を主所属とするチャンクの中にあることがある）。呼び出し側は最も高い段だけを使う。
# 同じ段の中は「質問 + 参照の一文」との埋め込みの近さの順に、節ごとに $per_section 件まで
SECTION_PICK_CHUNKS_QUERY = """
UNWIND $sections AS sid
MATCH (s:Section {id: sid})-[:HAS_SECTION*0..3]->(sub:Section)<-[m:IN_SECTION]-(t:Chunk)
WHERE t.text IS NOT NULL AND NOT t.id IN $skip AND (m.primary = true OR sub.id = sid)
MATCH (t)-[:PART_OF]->(d:Document)
WITH sid, t, d, max(CASE WHEN sub.id = sid AND m.primary = true THEN 3 WHEN m.primary = true THEN 2 ELSE 1 END) AS tier
WITH sid, t, d, tier,
     CASE WHEN $query_vector IS NULL OR t.embedding IS NULL THEN 0.0 ELSE vector.similarity.cosine(t.embedding, $query_vector) END AS sim
ORDER BY tier DESC, sim DESC, t.position
WITH sid, collect({id: t.id, text: t.text, position: t.position, file_name: d.fileName, doc_source: __SOURCE__, tier: tier})[0..$per_section] AS rows
UNWIND rows AS row
RETURN sid AS section_id, row.id AS id, row.text AS text, row.position AS position, row.file_name AS file_name,
       row.doc_source AS doc_source, row.tier AS tier
""".replace("__SOURCE__", _SOURCE_OF_D)

# 一覧選択で LLM に見せる「既に手元にある節」（文脈にあるチャンクの主所属の節と要点）
ALREADY_SECTIONS_QUERY = """
UNWIND $ids AS id
MATCH (c:Chunk {id: id})-[:PART_OF]->(d:Document)
OPTIONAL MATCH (c)-[m:IN_SECTION]->(s:Section)
WHERE m.primary = true
RETURN c.id AS id, d.fileName AS file_name, s.id AS section_id, s.path AS path, s.title AS title, s.number AS number,
       s.summary AS summary, c.section AS chunk_section
"""
# 一覧選択で、選んだ節 1 つから候補にするチャンクの上限
REFERENCE_DOC_PICK_CANDIDATES = 8
# 一覧選択の候補の節に付ける本文の先頭の字数（要点の無い節の代わりの文）
REFERENCE_DOC_HEAD_CHARS = 300
# 参照元の節として LLM に見せる、参照の一文の前後の字数
REFERENCE_CONTEXT_CHARS = 150
# 応答の参照の控えに、一覧選択のときだけ載せる項目（従来の選び方では載せない。応答の形を変えないため）
REFERENCE_SELECTION_KEYS = ("selection", "selection_fallback", "selection_stage", "selected_section", "selected_sections",
                            "selection_chapters", "summary_coverage", "llm_raw")
# 一覧選択で 2 件目以降を足すとき、文脈の文字数の上限のうちエンティティ・リレーションの行のために残す割合
# （_apply_budget_reranked は本文を削らず、残りに行を詰めるので、本文で上限を使い切ると行が 0 本になる）
REFERENCE_EXTRA_LINE_SHARE = 0.15

# 章・節 1 つから候補にするチャンクの上限
REFERENCE_SECTION_CANDIDATES = 8
# 参照先の本文による選択で、章・節 1 つ・文書 1 つから足すチャンクの上限
REFERENCE_LAYER_TARGET_CAP = 2

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
    # candidate_k・candidate_min_score・entity_limit の表示名は settings_overview.py の RERANK_REPLACES と同じにする
    ("candidate_k", "int", "CHAT_CANDIDATE_K", 20, 1, 200, "チャンク検索", "候補チャンク数",
     "ベクトル検索と全文検索で取得する候補の数。この候補からリランクで選択する。"),
    ("rerank_top_n", "int", "CHAT_RERANK_TOP_N", 6, 1, 50, "チャンク検索", "選択するチャンク数",
     "リランク後に保持するチャンクの数。本文として LLM に渡され、グラフ展開の起点にもなる。"),
    ("candidate_min_score", "float", "CHAT_CANDIDATE_MIN_SCORE", 0.5, 0.0, 1.0, "チャンク検索", "候補のスコアしきい値",
     "検索スコア（最上位を 1 とした値）がこの値未満の候補を除外する。上流版の既定値は 0.5。"),
    ("reference_max", "int", "CHAT_REFERENCE_MAX", 0, 0, 20, "チャンク検索", "参照先として追加するチャンク数",
     "選択したチャンクから参照リンク（REFERS_TO。「〜による」「〜参照」の参照先）を追跡し、参照先のチャンクを追加する数の上限。0 の場合は追加しない。"
     "参照先の選択方法は「参照の追跡」の「参照先の選択方法」で設定する。参照の一文で選択する場合に 2 回目の追跡を行うと、1 回目に追加した参照先 1 つにつき 1 件まで、さらに追加する。"
     "「参照先の本文による選択に加え、参照の一文でも選択」では、参照先の本文による分と参照の一文による分のそれぞれに、この上限を適用する。"),
    ("entity_order", "choice:similarity=質問との類似度順,count=選択したチャンクでの出現数順（上流版）", "CHAT_ENTITY_ORDER", "similarity", None, None,
     "起点エンティティ", "起点の並び順", "上限で絞り込む前の並び順。上流版は出現数順であり、出現数が同じ場合の順序は不定である。"),
    ("entity_limit", "int", "CHAT_ENTITY_LIMIT", 40, 0, 1000, "起点エンティティ", "起点の上限（文書ごと）",
     "選択したチャンクに関連付けられたエンティティのうち、起点とする数の上限。0 は制限なし。上流版の既定値は 40。"),
    ("sim_min", "float", "CHAT_EXPAND_SIM_MIN", 0.3, 0.0, 1.0, "グラフ展開", "展開する類似度の下限",
     "質問とエンティティの類似度がこの値未満の起点は展開しない。上流版の既定値は 0.3。"),
    ("sim_max", "float", "CHAT_EXPAND_SIM_MAX", 0.9, 0.0, 1.01, "グラフ展開", "2 ホップに切り替える類似度",
     "類似度がこの値を超える起点は 2 ホップ先まで展開する。上流版の既定値は 0.9。"),
    ("two_hop", "bool", "CHAT_EXPAND_TWO_HOP", True, None, None, "グラフ展開", "2 ホップ展開を使用",
     "オフの場合は、類似度に関係なく 1 ホップまで展開する。"),
    ("one_hop_limit", "int", "CHAT_EXPAND_ONE_HOP_LIMIT", 0, 0, 100000, "グラフ展開", "1 ホップの経路の上限（起点ごと）",
     "起点 1 個から取得する経路の数。0 は制限なし。上流版の既定値は 20（起点自身を含む）で、並び順は作成順である。"),
    ("two_hop_limit", "int", "CHAT_EXPAND_TWO_HOP_LIMIT", 40, 0, 100000, "グラフ展開", "2 ホップの経路の上限（起点ごと）",
     "2 ホップ展開する起点から取得する経路の数。0 は制限なし（件数が急増するため注意）。上流版の既定値は 40。"),
    ("line_min_score", "float", "CHAT_LINE_MIN_SCORE", 0.0, 0.0, 1.0, "文脈に含める行", "行のスコアしきい値",
     "エンティティとリレーションの行をリランクし、スコアがこの値未満の行を除外する。0 の場合は除外しない。"),
    ("line_max", "int", "CHAT_LINE_MAX", 120, 0, 5000, "文脈に含める行", "行の上限",
     "スコアの高い順に文脈へ含める行の最大数。0 は制限なし。"),
    ("char_budget", "int", "CHAT_CONTEXT_CHAR_BUDGET", 14000, 1000, 200000, "文脈に含める行", "文脈の文字数の上限",
     "本文と行を合わせた文字数の上限。LLM の入力長を超えないように設定する。"),
    # ---- 参照の追跡（「参照先として追加するチャンク数」が 1 以上の場合に適用）----
    ("reference_select", "choice:target=参照先の本文と質問の類似度で選択,link=参照の一文と質問の類似度で選択,both=参照先の本文による選択に加え、参照の一文でも選択",
     "CHAT_REFERENCE_SELECT", "target", None, None,
     "参照の追跡", "参照先の選択方法",
     "参照先の本文による選択では、選択したチャンクを参照元とする参照先をすべて収集し、参照先の本文が質問に類似する順に追加する。"
     "「参照の一文」を選択すると、本文中の「〜による」「〜参照」の一文が質問に合致する参照のみを追跡する（回答の箇所が質問と類似していない場合にも到達できる）。"
     "「参照先の本文による選択に加え」を選択すると、参照先の本文による選択で追加する分はそのまま追加し、さらに参照の一文で選択した分を追加する"
     "（参照先の本文による選択で到達していた参照先を失わない。追加する数は増加する）。"
     "以下の項目は、選択方法が参照先の本文による選択以外の場合に使用する。"),
    ("reference_min_score", "float", "CHAT_REFERENCE_MIN_SCORE", 0.3, 0.0, 1.0, "参照の追跡", "追跡する参照のスコアの下限",
     "参照の一文と質問の類似度（リランクのスコア。0〜1）がこの値未満の参照は追跡しない。値を上げると、参照を必要としない質問に不要なチャンクが追加されにくくなる。"
     "評価用の文書群では 0.1〜0.35 で結果は変わらず、0.4 以上では参照先に到達する質問が減少した。"),
    ("reference_depth", "int", "CHAT_REFERENCE_DEPTH", 1, 1, 2, "参照の追跡", "追跡回数",
     "2 の場合は、追加した参照先の中にある参照を、さらに 1 回追跡する（参照先がさらに別表や別の項を指している場合のため）。2 回目に追加するのは、1 回目の参照先 1 つにつき 1 件までである。"),
    ("reference_documents", "bool", "CHAT_REFERENCE_DOCUMENTS", True, None, None, "参照の追跡", "文書のみを指す参照も追跡",
     "番号のみ・名称のみで文書を指す参照（項や別表の指定がないもの）について、その文書の中から、質問と参照の一文に合致するチャンクを 1 つ選択して追加する。"),
    ("reference_history_weight", "float", "CHAT_REFERENCE_HISTORY_WEIGHT", 1.0, 0.0, 1.0, "参照の追跡", "改正履歴の中の参照の重み",
     "改正履歴の記述の中にある参照のスコアに乗じる値。1 は本文の参照と同じ扱い（既定値）。値を下げると改正履歴からは追跡されにくくなり、0 の場合は追跡しない。"),
    ("reference_presentation", "choice:separate=文書ごとに集約して配置,inline=参照元の直後に見出しを付与して配置", "CHAT_REFERENCE_PRESENTATION", "separate", None, None,
     "参照先の提示方法", "追加した参照先の配置",
     "「参照元の直後」を選択すると、追加した参照先を、参照している本文の直後に「参照先（上の本文の『…による』が指す箇所）」という見出しを付与して LLM に渡す。"
     "エンティティとリレーションの行の扱いは変更されない。"),
    ("reference_prompt", "bool", "CHAT_REFERENCE_PROMPT", False, None, None, "参照先の提示方法", "回答の指示に参照の扱いを追加",
     "オンの場合は、回答の指示に「本文が他の箇所を指し、その参照先が文脈にある場合は、参照先の内容で具体的に回答する。ない場合は、参照すべき箇所を回答し、内容を推測しない」を追加する。"),
    # ---- 章・節と文書層の参照（11/8 追加。従来の設定の並びを変えないよう末尾に置く。画面では区分名で「参照の追跡」にまとまる）----
    ("reference_sections", "bool", "CHAT_REFERENCE_SECTIONS", False, None, None, "参照の追跡", "章・節を指す参照も追跡",
     "本文中の「2-1 項による」などの参照が章・節を指す場合に、その章・節（下位の節を含む）に属するチャンクを参照先の候補にする。"
     "参照先の本文による選択では 1 つの章・節から 2 件まで、参照の一文による選択では 1 件を追加する。追加する数の合計は「参照先として追加するチャンク数」の範囲内である。"),
    ("reference_document_layer", "bool", "CHAT_REFERENCE_DOCUMENT_LAYER", False, None, None, "参照の追跡", "文書番号を経由する参照も追跡",
     "選択したチャンクが他の文書の文書番号を参照している場合（文書層の参照）に、その文書番号の取り込み済みの文書から、質問と参照の一文に合致するチャンクを参照先の候補にする。"
     "参照先の本文による選択では 1 つの文書から 2 件まで、参照の一文による選択では 1 件を追加する。未取り込みの文書への参照は対象外である。"),
    # ---- 文書のみを指す参照の選び方（11/9 追加。末尾に置く）----
    ("reference_doc_select", "choice:rerank=文書のチャンクをリランクで 1 件選択,summary_list=節の要点の一覧から LLM で選択",
     "CHAT_REFERENCE_DOC_SELECT", "rerank", None, None, "参照の追跡", "文書のみを指す参照の参照先の選択方法",
     "参照の一文で選択した参照のうち、項や別表の指定がなく文書のみを指すものについて、文書の中から追加する箇所の選び方。"
     "「リランク」は、文書のチャンクから質問と参照の一文に合致するものを 1 件選択する（従来）。"
     "「節の要点の一覧」は、文書の章・節の要点（後処理「節の要点」で作成）の一覧を LLM に示して、読むべき節を最大「一覧から選択する節の上限」件選ばせ、"
     "選んだ節ごとにチャンクを 1 件追加する。LLM が失敗した場合や番号を返さない場合、要点のある節が 1 つも無い文書は、リランクで選択する。"
     "選んだ節のチャンクのスコアが「追跡する参照のスコアの下限」未満の場合は追加しない（リランクと同じ下限）。"
     "2 件目以降は、本文の合計が文脈の文字数の上限の 85% 以内に収まる場合のみ追加する（エンティティとリレーションの行の分を残すため）。"
     "2 回目の追跡では、参照先 1 つにつき 1 件を追加する。"),
    ("reference_doc_max", "int", "CHAT_REFERENCE_DOC_MAX", 4, 1, 10, "参照の追跡", "一覧から選択する節の上限",
     "選択方法が「節の要点の一覧」の場合に、1 つの参照について LLM に選ばせる節の数の上限（1 回目の追跡。2 回目は 1 件）。"),
    ("reference_doc_two_stage_sections", "int", "CHAT_REFERENCE_DOC_TWO_STAGE_SECTIONS", 30, 1, 100000, "参照の追跡", "章から選択する文書の節の数",
     "選択方法が「節の要点の一覧」の場合に、節の数がこの値を超える文書は、先に章の要点の一覧から章を選び、その章の中の節から選ぶ。"),
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


def resolve_retrieval_settings(overrides=None, mode_defaults=None) -> dict:
    """既定値に、方式ごとの既定値（mode_defaults）、画面からの指定（overrides）の順に重ね、型と範囲を確かめた設定を返す。
    不正な値はその段の前の値のまま。優先順位は backend の設定 < 方式の既定値 < 画面の上書き。"""
    settings = default_retrieval_settings()
    merged = dict(mode_defaults or {})
    for key, value in (overrides or {}).items():
        if value not in (None, ""):
            merged[key] = value
    overrides = merged
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
                OPTIONAL MATCH path=(e)(()-[rels:!HAS_ENTITY&!PART_OF&!TOUCHED]-()){0,1}(:!Chunk&!Document&!__Community__&!Evidence)
                RETURN path __ONE_HOP_LIMIT__
            }
        WHEN __TWO_HOP__ AND qsim > __SIM_MAX__ THEN
            collect {
                OPTIONAL MATCH path=(e)(()-[rels:!HAS_ENTITY&!PART_OF&!TOUCHED]-()){0,2}(:!Chunk&!Document&!__Community__&!Evidence)
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
    if pool and used >= budget:
        # 本文だけで上限に達した（参照で足した本文が多い場合など）。行は 1 本も入らない
        logging.warning(f"line rerank: the chunk texts use {used} chars >= char_budget={budget}; no entity/relationship lines are kept ({len(pool)} lines)")
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


def reference_context(source_text: str, heading: str, quote: str, chars: int = REFERENCE_CONTEXT_CHARS) -> str:
    """一覧選択で LLM に見せる参照元の節: 「見出し: …前の文 参照の一文 後ろの文…」（参照の一文の前後 chars 字）。
    一文が本文に見つからなければ、見出しと一文だけにする。"""
    quote = (quote or "").strip()
    text = source_text or ""
    at = _locate_quote(text, quote)
    if at >= 0 and quote:
        end = at + len(quote)
        if text[at:end] != quote:
            # 空白の違いを無視して見つけた。一文の終わりは、空白を除いた字数で数えて決める
            count, end = 0, at
            target = len(_squeeze(quote))
            while end < len(text) and count < target:
                count += 0 if text[end].isspace() else 1
                end += 1
        before = text[max(0, at - chars):at]
        after = text[end:end + chars]
        body = ("…" if at > chars else "") + before + text[at:end] + after + ("…" if end + chars < len(text) else "")
    else:
        body = quote
    body = " ".join(body.split())
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
                     "targets": [], "documents": [], "sections": [], "link_score": 0.0, "score": 0.0, "layer": row.get("layer"),
                     "via_id": row.get("section_id") or row.get("standard"), "via_label": row.get("section_title") or row.get("standard"),
                     "via_file": row.get("section_file") or (row.get("file_name") if row.get("standard") else None),
                     # 一覧選択（reference_doc_select = summary_list）で、参照元の節（参照の一文の前後）を LLM に見せるのに使う
                     "source_text": row.get("source_text"), "source_heading": row.get("source_heading")}
            group["weight"] = history_weight if group["source_section"] == "revision_history" else 1.0
            index[key] = group
            groups.append(group)
        if row["kind"] == "document":
            if row.get("file_name") and row["file_name"] not in group["documents"]:
                group["documents"].append(row["file_name"])
            for sid in row.get("sections") or []:     # 文書層の参照で、一文の節番号から決まった節（M3）
                if sid not in group["sections"]:
                    group["sections"].append(sid)
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
                        "source_section", "method", "file_name", "source", "position", "select", "shown_in",
                        "via_id", "via_label", "via_file")
# via_*: 経由した点。章・節の参照は節（ID・見出し・その節の文書）、文書層の参照は文書番号ノード（ID・文書番号・参照先の文書）

# 参照先の本文による選択で足したチャンクについて、どの参照元からどの一文で届いたかを引き直す（足す処理には使わない。控えのため）
TARGET_PROVENANCE_QUERY = """
MATCH (c:Chunk)-[r:REFERS_TO]->(t:Chunk)
WHERE c.id IN $sources AND t.id IN $targets
RETURN c.id AS from_id, t.id AS to_id, r.quote AS link_quote
"""


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
            "from_chunk_id": doc.metadata.get("reference_from"), "quote": doc.metadata.get("reference_quote"), "locator": None, "form": None,
            "method": None, "source_section": None, "target_kind": doc.metadata.get("reference_kind") or "chunk", "select": "target", "passage": "",
            "via_id": doc.metadata.get("reference_via_id"), "via_label": doc.metadata.get("reference_via_label"),
            "via_file": doc.metadata.get("reference_via_file")}


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
        extra = self._layer_target_rows(query, ids, {r["id"] for r in rows})
        if not extra:
            # 章・節と文書層の追跡が無効（既定）か候補が無い場合は、従来と同じ処理
            if not rows:
                return []
            scores = _rerank(query, [r["text"] for r in rows])
            ranked = sorted(zip(rows, scores), key=lambda x: -x[1])[:limit]
            return [(Document(page_content=r["text"], metadata={"chunk_id": r["id"], "source": r.get("source"), "position": r.get("position"),
                                                                "via_reference": True}), sc) for r, sc in ranked]
        rows = rows + extra
        scores = _rerank(query, [r["text"] for r in rows])
        picked, per_group = [], {}
        for r, sc in sorted(zip(rows, scores), key=lambda x: -x[1]):
            group = r.get("group")
            if group is not None:
                if per_group.get(group, 0) >= REFERENCE_LAYER_TARGET_CAP:
                    continue
                per_group[group] = per_group.get(group, 0) + 1
            picked.append((r, sc))
            if len(picked) >= limit:
                break
        return [(Document(page_content=r["text"], metadata={"chunk_id": r["id"], "source": r.get("source"), "position": r.get("position"),
                                                            "via_reference": True, "reference_kind": r.get("layer") or "chunk",
                                                            "reference_from": r.get("from_id"), "reference_quote": r.get("quote"),
                                                            "reference_via_id": r.get("via_id"), "reference_via_label": r.get("via_label"),
                                                            "reference_via_file": r.get("via_file")}), sc)
                for r, sc in picked]

    def _fill_target_provenance(self, chosen, trace: List[dict]) -> None:
        """参照先の本文による選択で、チャンク間の参照リンクで足したものに、参照元と参照の一文を控えとして付ける。
        どの参照元から届いたかは選ぶときには使わないので、足した後に引き直す。失敗しても検索は続ける（控えが欠けるだけ）。"""
        missing = [e for e in trace if not e.get("from_chunk_id")]
        if not missing:
            return
        try:
            seeds = [d.metadata["chunk_id"] for d, _ in chosen]
            rows = self.neo_db.query(TARGET_PROVENANCE_QUERY, params={"sources": seeds, "targets": [e["chunk_id"] for e in missing]})
            order = {cid: i for i, cid in enumerate(seeds)}
            best = {}
            for r in rows:
                prev = best.get(r["to_id"])
                if prev is None or order.get(r["from_id"], 10 ** 6) < order.get(prev["from_id"], 10 ** 6):
                    best[r["to_id"]] = r
            for e in missing:
                hit = best.get(e["chunk_id"])
                if hit:
                    e["from_chunk_id"] = hit["from_id"]
                    e["quote"] = hit.get("link_quote")
        except Exception as exc:
            logging.warning(f"recording where the references came from failed ({exc}); continuing without it")

    def _layer_target_rows(self, query: str, ids: List[str], known: set) -> List[dict]:
        """参照先の本文による選択で使う、章・節と文書層の参照先の候補。設定が無効なら空。
        1 行 = {id, text, position, source, layer, group}。group は章・節または文書ごとの上限の数え方に使う。失敗しても検索は続ける。"""
        out, seen = [], set(known) | set(ids)
        cache: dict = {}
        sections_followed = False   # 章・節の照会が通った（文書層の参照のうち節まで決まったものは、章・節の側でたどった）
        if self.settings.get("reference_sections"):
            try:
                for r in self.neo_db.query(SECTION_REFERENCE_QUERY, params={"ids": ids, "query_vector": self._query_vector(query, cache),
                                                                            "per_section": REFERENCE_SECTION_CANDIDATES}):
                    if not r.get("text") or r["id"] in seen or (self.document_names and r.get("file_name") not in self.document_names):
                        continue
                    seen.add(r["id"])
                    out.append({"id": r["id"], "text": r["text"], "position": r.get("position"), "source": r.get("doc_source") or r.get("file_name"),
                                "layer": "section", "group": ("section", r.get("section_id")), "from_id": r.get("source"), "quote": r.get("quote"),
                                "via_id": r.get("section_id"), "via_label": r.get("section_title"), "via_file": r.get("section_file") or r.get("file_name")})
                sections_followed = True
            except Exception as exc:
                logging.warning(f"following section references failed ({exc}); continuing without them")
        if self.settings.get("reference_document_layer"):
            try:
                for ref in self.neo_db.query(DOCUMENT_LAYER_REFERENCE_QUERY, params={"ids": ids}):
                    name = ref.get("file_name")
                    if not name or (self.document_names and name not in self.document_names):
                        continue
                    if ref.get("sections") and sections_followed:
                        continue   # 節まで決まった参照（via = standard の線）は、章・節の照会が同じ線をたどった。同じ参照から重ねて足さない
                    query_vector = self._query_vector(f"{query} {ref.get('quote') or ''}".strip(), cache)
                    if ref.get("sections"):
                        # 一文の節番号で節まで決まっている（M3）: その節のチャンクだけを候補にする。全部文脈にあれば足さない（文書全体には戻らない）
                        rows = self._standard_section_rows(ref.get("sections"), seen, query_vector)
                    else:
                        rows = self.neo_db.query(REFERENCE_DOCUMENT_CHUNKS_QUERY, params={
                            "docs": [name], "skip": list(seen), "query_vector": query_vector,
                            "per_document": REFERENCE_DOCUMENT_CANDIDATES})
                    for r in rows:
                        if not r.get("text") or r["id"] in seen:
                            continue
                        seen.add(r["id"])
                        out.append({"id": r["id"], "text": r["text"], "position": r.get("position"), "source": r.get("doc_source") or r.get("file_name"),
                                    "layer": "document_layer", "group": ("document", name), "from_id": ref.get("source"), "quote": ref.get("quote"),
                                    "via_id": ref.get("standard"), "via_label": ref.get("standard"), "via_file": name})
            except Exception as exc:
                logging.warning(f"following document-layer references failed ({exc}); continuing without them")
        return out

    def _standard_section_rows(self, sections, skip, query_vector) -> List[dict]:
        """文書層の参照が節まで決まっているとき（M3）の候補のチャンク（skip に無いもの）。節が無いか、節のチャンクが全部 skip にあれば空。
        呼び出し側は、節があるのに空なら何も足さない（文書全体からの選び方には戻らない）。"""
        if not sections:
            return []
        rows = self.neo_db.query(STANDARD_SECTION_CHUNKS_QUERY, params={
            "sections": list(sections), "skip": list(skip), "query_vector": query_vector, "limit": REFERENCE_DOCUMENT_CANDIDATES})
        return [r for r in rows if r.get("text")]

    # ---------- 参照の一文で選ぶたどり方（reference_select = link） ----------
    def _query_vector(self, query: str, cache: dict):
        key = ("query_vector", query)
        if key not in cache:
            cache[key] = self.neo_db.embedding.embed_query(query)
        return cache[key]

    def _reference_links(self, ids: List[str], taken: set, query_vector=None):
        """ids のチャンクから出る参照リンクを読む。既に文脈にあるチャンク（taken）へのリンクと、絞り込みの外の文書へのリンクは除く。

        返り値: (たどる候補のリンクの行, 参照元ごとの参照の一文の集まり)。後者は、除いたリンクの分も含む。
        """
        if not ids:
            return [], {}
        rows = list(self.neo_db.query(REFERENCE_LINK_QUERY, params={"ids": list(ids)}))
        rows += self._layer_link_rows(list(ids), query_vector)
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

    def _layer_link_rows(self, ids: List[str], query_vector=None) -> List[dict]:
        """参照の一文による選択で使う、章・節と文書層のリンクの行（REFERENCE_LINK_QUERY と同じ形）。設定が無効なら空。失敗しても検索は続ける。
        章・節: 1 つの参照（参照元と一文）の候補として、節のチャンクを束ねる（足すのは質問と一文に合う 1 件）。文書層: 文書を指す参照として扱う。
        文書層の参照のうち節まで決まったもの（sections）は、章・節の照会が通っていれば章・節の側でたどったので、ここでは行にしない。"""
        out = []
        sections_followed = False
        if self.settings.get("reference_sections"):
            try:
                for r in self.neo_db.query(SECTION_REFERENCE_QUERY, params={"ids": ids, "query_vector": query_vector,
                                                                            "per_section": REFERENCE_SECTION_CANDIDATES}):
                    out.append({**r, "locator": r.get("section_title") or "", "form": "章・節", "source_section": "body", "layer": "section"})
                sections_followed = True
            except Exception as exc:
                logging.warning(f"reading section references failed ({exc}); continuing without them")
        if self.settings.get("reference_document_layer"):
            try:
                for r in self.neo_db.query(DOCUMENT_LAYER_REFERENCE_QUERY, params={"ids": ids}):
                    if r.get("sections") and sections_followed:
                        continue   # 同じ via = standard の線を、章・節の参照として既に読んだ。1 つの参照で 2 件を足さない
                    out.append({**r, "id": None, "text": None, "position": None, "doc_source": None, "locator": r.get("standard") or "",
                                "form": "文書番号", "method": "rule", "source_section": "body", "layer": "document_layer"})
            except Exception as exc:
                logging.warning(f"reading document-layer references failed ({exc}); continuing without them")
        return out

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
            if group.get("sections"):
                # 文書層の参照が一文の節番号で節まで決まっている（M3）: その節のチャンクから、質問と参照の一文に合うものを選ぶ。
                # 節のチャンクが全部文脈にあれば足さない（文書全体からの選び方には戻らない。節と関係の無いチャンクを足さないため）。
                # 本文の点数の下限は掛けない。チャンクへのリンクで候補が複数ある場合と同じく、候補の範囲は規則で決まっており、
                # たどるかどうかは参照の一文の点数（下限は呼び出し側で判定済み）で決めているため
                rows = self._standard_section_rows(group["sections"], taken, self._query_vector(f"{question} {group['quote']}".strip(), cache))
                candidates = [{"id": r["id"], "text": r["text"], "position": r.get("position"), "file_name": r.get("file_name"),
                               "doc_source": r.get("doc_source") or r.get("file_name")} for r in rows if r["id"] not in taken]
                if len(candidates) <= 1:
                    return candidates[0] if candidates else None
                scores = _rerank(f"{question} {group['quote']}".strip(), [c["text"] for c in candidates])
                return candidates[max(range(len(candidates)), key=lambda i: scores[i])]
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
                2 回目は、チャンクへのリンクだけをたどる（一覧選択 reference_doc_select = summary_list では、文書のみを指す参照も
                同じ選び方でたどり、親 1 つにつき 1 件にする）。2 回目が失敗しても、1 回目に足した分は残す。
        従来の選び方の分（with_target = True。設定 reference_select = both）: 従来の選び方（参照先の本文と質問の近さ。_referenced）の
                上位 limit 件を、そのまま足す（一文で選んだものと重なる分は 1 回だけ）。従来の設定で文脈に入っていた参照先は、必ず残る。
                一文で選ぶ参照が無い質問では、従来と同じ結果になる。
                この分は一文で選ぶ分より先に求め、その本文の長さを、一覧選択で 2 件目以降を足すときの文字数の判定に入れる
                （足す順と控えの並びは従来どおり、一文で選んだ分 → 従来の選び方の分）。
        参照元のチャンクの順位は、同点のときの並び順に使う。参照先の本文と質問の近さは、一文で選ぶ分の基準にはしない。
        """
        taken = {d.metadata["chunk_id"] for d, _ in chosen}
        picks: List[dict] = []
        target_rows, target_error = [], None
        if with_target:
            # 従来の選び方の分は選んだチャンクだけで決まる（一文で選ぶ分に依らない）ので先に求め、本文の長さを文字数の判定に入れる
            try:
                target_rows = self._referenced(query, chosen, limit)
            except Exception as exc:
                target_error = exc
        reserved = sum(len(doc.page_content or "") for doc, _ in target_rows if doc.metadata["chunk_id"] not in taken)
        try:
            self._link_picks(query, chosen, limit, taken, picks, reserved_chars=reserved)
        except Exception as exc:
            if not with_target and not picks:
                raise
            # 途中で失敗しても、そこまでに選んだ分は残す。both では、従来の選び方の分も足す（both は従来を含む、という約束を守る）
            logging.warning(f"selecting references by their sentences failed ({exc}); keeping {len(picks)} selected so far")
        if with_target:
            if target_error is not None:
                if not picks:
                    raise target_error
                logging.warning(f"selecting references by the target text failed ({target_error}); keeping the ones selected by their sentences")
            for doc, score in target_rows:
                if doc.metadata["chunk_id"] not in taken:
                    taken.add(doc.metadata["chunk_id"])
                    picks.append(_target_entry(doc, score))
        return picks

    def _link_picks(self, query: str, chosen, limit: int, taken: set, picks: List[dict], reserved_chars: int = 0) -> None:
        """参照の一文で選ぶ分（1 回目と 2 回目）を picks に足す。途中で失敗したら、そこまでに足した分を残して例外を上げる。
        reserved_chars: この後で足す本文の長さ（both の従来の選び方の分）。一覧選択の 2 件目以降の文字数の判定に入れる。"""
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
                    "method": group.get("method"), "source_section": group["source_section"], "target_kind": group.get("layer") or group["kind"], "select": "link",
                    "passage": group["passage"], "root_score": float(root_score), "chunk_score": float(root_score) * float(score),
                    "via_id": group.get("via_id"), "via_label": group.get("via_label"), "via_file": group.get("via_file")}

        # 一覧選択（reference_doc_select = summary_list）では、1 回目は 1 つの参照から最大 reference_doc_max 件を足す（2 回目は 1 件）。
        # 参照 1 つにつき 1 件目は従来の選び方と同じく必ず足し、2 件目以降（extra）は本文の合計が extra_limit に収まる分だけ足す。
        # 本文は _apply_budget_reranked でも削られず、行（エンティティ・リレーション）は残りに詰めるので、ここで本文の合計
        # （選んだチャンク + 一文で足すチャンク + この後で足す従来の選び方の分）を数え、行の分（REFERENCE_EXTRA_LINE_SHARE）を残す
        summary_list = settings.get("reference_doc_select", "rerank") == "summary_list"
        budget = int(settings.get("char_budget") or 14000)
        extra_limit = budget - int(budget * REFERENCE_EXTRA_LINE_SHARE)
        body_chars = [sum(len(d.page_content or "") for d, _ in chosen) + int(reserved_chars or 0)]

        def admit(text, free):
            if free or body_chars[0] + len(text or "") <= extra_limit:
                body_chars[0] += len(text or "")
                return True
            return False

        def add(group, targets, info, hop, root_score, free_first):
            """targets（足す候補の列）を、取っていないもの・予算に収まるものだけ足す。足した控えの列を返す。"""
            added = []
            for i, target in enumerate(targets):
                if target["id"] in taken or not admit(target["text"], free_first and i == 0):
                    continue
                taken.add(target["id"])
                e = entry(group, target, hop, group["score"], root_score)
                if info is not None:
                    e.update({k: v for k, v in info.items() if v is not None})
                    if target.get("selected_section"):
                        e["selected_section"] = target["selected_section"]
                e["extra"] = i > 0
                added.append(e)
                picks.append(e)
            return added

        # ---- 1 回目 ----
        layer_on = self.settings.get("reference_sections") or self.settings.get("reference_document_layer")
        groups = _group_links(*self._reference_links(list(rank), taken, self._query_vector(query, cache) if layer_on else None),
                              history_weight=history_weight)
        if not follow_documents:
            groups = [g for g in groups if g["kind"] != "document"]
        self._score_groups(groups, lambda _source: query)
        for group in groups:
            group["score"] = group["link_score"] * group["weight"]
        groups.sort(key=lambda g: (-g["score"], rank.get(g["source"], 10 ** 6)))
        first = []
        followed = 0                                   # たどった参照の数（上限 limit。一覧選択で 1 つの参照から複数足しても 1 と数える）
        for group in groups:
            if followed >= limit or group["score"] < min_score or group["score"] <= 0:
                break
            targets, info = self._resolve_targets(query, group, taken, min_score, cache, summary_list)
            added = add(group, targets, info, 1, chosen_score.get(group["source"], 0.0), True)
            if not added:
                continue
            followed += 1
            first.extend(added)
        if depth < 2 or not first:
            return

        # ---- 2 回目（失敗しても 1 回目の分は残す）----
        # 従来はチャンクへのリンクだけをたどる。一覧選択では、文書のみを指す参照も同じ選び方でたどる
        try:
            parents = {e["chunk_id"]: e for e in first}
            groups = [g for g in _group_links(*self._reference_links(list(parents), taken), history_weight=history_weight)
                      if g["kind"] == "chunk" or (summary_list and follow_documents and g["kind"] == "document")]
            self._score_groups(groups, lambda source: f"{query} {parents[source]['passage'] or parents[source]['quote']}".strip())
            queues = {}
            for group in groups:
                group["score"] = parents[group["source"]]["score"] * group["link_score"] * group["weight"]
                if group["score"] >= min_score and group["score"] > 0:
                    queues.setdefault(group["source"], []).append(group)
            for parent in first:                       # 親の点数の高い順に、1 件ずつ
                # 一覧選択で 2 件目以降として足した親（extra）から先は、1 件目も文字数の判定に掛ける。
                # 判定で足せないと分かっている親では、参照先を決めない（LLM の一覧選択を呼ばない）
                free = not parent.get("extra")
                for group in sorted(queues.get(parent["chunk_id"]) or [], key=lambda g: -g["score"]):
                    if not free and body_chars[0] >= extra_limit:
                        break
                    targets, info = self._resolve_targets(query, group, taken, min_score, cache, summary_list)
                    targets = targets[:1]              # 2 回目は親 1 つにつき 1 件（一覧選択でも同じ）
                    if add(group, targets, info, 2, parent["root_score"], free):
                        break
                    if targets and not free:
                        break                          # 文字数の判定で足せなかった。同じ親の残りの参照も試さない
        except Exception as exc:
            logging.warning(f"following references a second time failed ({exc}); keeping the first-hop references")

    # ---------- 文書のみを指す参照の一覧選択（reference_doc_select = summary_list） ----------
    def _resolve_targets(self, question: str, group: dict, taken: set, min_score: float, cache: dict, summary_list: bool):
        """参照 1 つにつき、足すチャンクの列と、控えに足す選び方の情報を返す: (targets, info)。

        従来（summary_list = False）は _resolve_target の 1 件（または空）で、info は None（控えは従来のまま）。
        一覧選択は、文書のみを指す参照（kind = document で、文書層の一文の節番号で節まで決まっていないもの）だけに使う。
        失敗・空・範囲外の番号だけ・候補の節が無い・要点のある節が無い・選んだ節のチャンクが全部文脈にあるか下限に満たない場合は、
        従来の選び方で 1 件にする（控えには selection = rerank と、戻した理由を残す）。
        1 つの質問の中で LLM が一度失敗したら（時間切れ・接続の失敗・同時要求の枠の待ちの上限）、以後の参照は LLM を呼ばずに戻す
        （gemma4 が止まっているときに、参照の数だけ時間切れを待たないため。理由は llm_failed_earlier）。
        """
        if not (summary_list and group["kind"] == "document" and group["documents"] and not group.get("sections")):
            target = self._resolve_target(question, group, taken, min_score, cache)
            return ([target] if target else []), None
        from src import section_summaries as SS
        if cache.get("summary_list_llm_failed"):
            reason, raw = "llm_failed_earlier", ""
        else:
            try:
                return self._summary_list_targets(question, group, taken, cache, min_score)
            except SS.SelectionFailed as exc:
                reason, raw = exc.reason, exc.raw
                if reason == "llm_failed":
                    cache["summary_list_llm_failed"] = True
            except Exception as exc:
                reason, raw = "error", str(exc)[:200]
        logging.info(f"summary list selection fell back to rerank ({reason}): {group.get('documents')}")
        target = self._resolve_target(question, group, taken, min_score, cache)
        return ([target] if target else []), {"selection": "rerank", "selection_fallback": reason, "llm_raw": raw or None}

    def _doc_sections(self, documents, cache: dict) -> List[dict]:
        key = ("doc_sections", tuple(documents))
        if key not in cache:
            cache[key] = list(self.neo_db.query(REFERENCE_DOC_SECTIONS_QUERY, params={"docs": list(documents), "head_chars": REFERENCE_DOC_HEAD_CHARS}))
        return cache[key]

    def _already_sections(self, taken: set) -> List[str]:
        """LLM に見せる「既に手元にある節」。文脈にあるチャンクの主所属の節（同じ節は 1 行）。"""
        from src import section_summaries as SS
        if not taken:
            return []
        lines, seen = [], set()
        for r in self.neo_db.query(ALREADY_SECTIONS_QUERY, params={"ids": sorted(taken)}):
            key = r.get("section_id") or r.get("chunk_section") or r.get("id")
            if key in seen:
                continue
            seen.add(key)
            label = SS.section_label(r) if r.get("section_id") else (r.get("chunk_section") or "")
            line = f"- {SS._stem(r.get('file_name'))} {label}".rstrip()
            if r.get("summary"):
                line += f": {r['summary']}"
            lines.append(line)
            if len(lines) >= SS.ALREADY_MAX:
                break
        return lines

    def _summary_list_targets(self, question: str, group: dict, taken: set, cache: dict, min_score: float = 0.0):
        """要点の一覧から LLM に節を選ばせ、選んだ節ごとにチャンクを 1 件決める。返り値 (targets, info)。失敗は SelectionFailed。

        要点のある節が 1 つも無い文書（後処理「節の要点」を流していない・章・節の層の作り直しで消えた）は、一覧選択をしない（no_summaries）。
        選んだ節のチャンクの点数（質問 + 参照の一文とのリランク）が min_score に満たない節は足さない（従来の選び方と同じ下限）。
        """
        from src import section_summaries as SS
        settings = self.settings
        rerank_query = f"{question} {group['quote']}".strip()
        sections = [SS.section_item(r) for r in self._doc_sections(group["documents"], cache)]
        if not sections:
            raise SS.SelectionFailed("no_sections")
        covered = sum(1 for s in sections if s["summary"])
        if not covered:
            logging.warning(f"summary list: no section of {group['documents']} has a summary (run the post-processing "
                            "'build_section_summaries'); selecting by rerank")
            raise SS.SelectionFailed("no_summaries")
        result = SS.select_by_list(
            question, rerank_query,
            reference_context(group.get("source_text"), group.get("source_heading"), group["quote"]),
            self._already_sections(taken), sections, rerank=_rerank, ask=SS.list_chat,
            max_picks=int(settings.get("reference_doc_max", 4) or 4),
            two_stage_min=int(settings.get("reference_doc_two_stage_sections", 30) or 30))
        picked = result["picked"]
        rows = self.neo_db.query(SECTION_PICK_CHUNKS_QUERY, params={
            "sections": [s["id"] for s in picked], "skip": list(taken),
            "query_vector": self._query_vector(rerank_query, cache), "per_section": REFERENCE_DOC_PICK_CANDIDATES})
        by_tier = {}
        for r in rows:
            if r.get("text"):
                by_tier.setdefault(r["section_id"], {}).setdefault(int(r.get("tier") or 0), []).append(r)
        # 節ごとに最も高い段（主所属 → 下位の節の主所属 → 副所属）の候補だけを使う
        candidates = {s["id"]: (lambda tiers: tiers[max(tiers)] if tiers else [])(by_tier.get(s["id"], {})) for s in picked}
        pool = [r for s in picked for r in candidates[s["id"]]]
        scores = {}
        if pool:
            # 選んだ節の候補をまとめて 1 回でリランクする（点数は組ごとに決まる）
            for r, sc in zip(pool, _rerank(rerank_query, [r["text"] for r in pool])):
                scores[(r["section_id"], r["id"])] = sc
        targets, used, below = [], set(taken), 0
        selected = [{"id": s["id"], "label": s["label"], "file_name": s["file_name"]} for s in picked]
        for s, info in zip(picked, selected):
            options = [r for r in candidates[s["id"]] if r["id"] not in used]
            if not options:
                continue
            best = max(options, key=lambda r: scores.get((s["id"], r["id"]), 0.0))
            if scores.get((s["id"], best["id"]), 0.0) < min_score:
                below += 1
                continue
            used.add(best["id"])
            targets.append({"id": best["id"], "text": best["text"], "position": best.get("position"), "file_name": best.get("file_name"),
                            "doc_source": best.get("doc_source") or best.get("file_name"), "selected_section": info})
        if not targets:
            raise SS.SelectionFailed("below_min_score" if below else "all_taken", " | ".join(result["raw"]))
        info = {"selection": "summary_list", "selection_stage": result["stage"], "selected_sections": selected,
                "selection_chapters": result["chapters"] or None, "summary_coverage": f"{covered}/{len(sections)}",
                "llm_raw": " | ".join(result["raw"])}
        return targets, info

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
            # 一覧選択の控え（選び方・選んだ節・LLM の生の答え）は、あるときだけ載せる（従来の選び方の応答の形は変えない）
            public.update({key: e[key] for key in REFERENCE_SELECTION_KEYS if e.get(key) is not None})
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
                    self._fill_target_provenance(chosen, trace)
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
    """モードと同じ索引で、候補チャンクだけを返す Neo4jVector を作る（既存の索引を開くだけで、埋め込みは書き込まない）。"""
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
        return Neo4jVector.from_existing_index(search_type="hybrid", keyword_index_name=keyword_index, **common)
    return Neo4jVector.from_existing_index(**common)


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
