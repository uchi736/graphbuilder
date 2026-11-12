# -*- coding: utf-8 -*-
"""保守: 設定値と検証件数の一覧（参照のみ）。画面「グラフ設定 > 保守 > 設定値と検証件数」の中身。

  設定値 … 動作を制限する設定について、作業プロセスが現在使用している値を返す。下の一覧に載せた名前だけを返す
         （鍵・パスワード・トークン・接続文字列・LLM_MODEL_CONFIG_* は載せない。名前が SECRET_NAME に当たるものは、載っていても返さない）
  検証件数 … グラフの検証項目の件数。tools/ingest/verify_stage1.sh・verify_stage3.sh と同じ照会（大きなグラフでも重くならない形に直したものを含む）
書き込みはしない。照会は読み取りのトランザクションで実行する（書き込みはサーバーが拒否する）。
登録: score.py で app.include_router(router)。POST /settings_overview（資格情報は他の照会と同じくフォームで受ける）
"""
import asyncio
import logging
import os
import re
import shlex
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from src import document_layer_rules
from src.api_response import create_api_response
from src.entities.user_credential import Neo4jCredentials, get_neo4j_credentials
from src.shared import constants

# 名前がこれに当たる設定は返さない（一覧に誤って足しても漏らさない）
SECRET_NAME = re.compile(r"(_KEY$|_KEY_|API_KEY|PASSWORD|PASSWD|SECRET|_TOKEN$|ACCESS_TOKEN|CREDENTIAL|_URI$|_DSN$|^LLM_MODEL_CONFIG_)", re.I)

# 環境変数で決まる設定: 名前, 区分, 説明, 既定値（コードに書かれた値。場所で違うものは併記。試験でコードと突き合わせる）
OVERVIEW_ENV = [
    ("MAX_TOKEN_CHUNK_SIZE", "取り込み",
     "1 文書から取り込む本文の上限（トークン数）。超過分は取り込まれず、ログに警告が出力されるのみである。0 以下の場合は上限を設けない。", "10000"),
    ("CHUNK_TOKENIZER", "取り込み",
     "チャンクのトークン数を算出するトークナイザ（tokenizer.json のパスまたは tiktoken の名称）。読み込めない場合は警告を出力し、gpt2 で算出する。", "gpt2"),
    ("PDF_CHUNK_STRATEGY", "取り込み",
     "PDF の分割方式。markdown は Markdown に変換し見出しで分割する（MARKDOWN_CHUNKING が true の場合のみ）。token は上流版と同じくトークン数で分割する。",
     "token"),
    ("MARKDOWN_CHUNKING", "取り込み", "Markdown を見出しで分割するかどうか。false の場合は PDF_CHUNK_STRATEGY=markdown も無効になる。", "true"),
    ("PDF_CONVERTER", "取り込み",
     "PDF を Markdown に変換する方式（PDF_CHUNK_STRATEGY=markdown の場合のみ）。regions のみ。"
     "本文の文字を文字層から、ページの構造を Nemotron Parse から取り、図は既定で図中の文字と題のみを残す。"
     "失敗した場合（ページ数が PDF_REGIONS_MAX_PAGES を超える場合を含む）は、文字層のみの簡易変換（ページの印と本文の行、番号の見出し）に戻す。"
     "以前の値 pymupdf4llm は AGPL の部品のため外しており、指定されていれば警告して regions として扱う。",
     "regions"),
    ("PDF_REGIONS_PARSE_URL", "取り込み", "PDF_CONVERTER=regions で使用する Nemotron Parse の接続先（OpenAI 互換）。",
     "http://host.docker.internal:8022/v1"),
    ("PDF_REGIONS_PARSE_MODEL", "取り込み", "Nemotron Parse のモデル名。", "nemotron-parse"),
    ("PDF_REGIONS_TRIAGE_URL", "取り込み", "PDF_CONVERTER=regions で図の種類を判定する軽量の VLM の接続先。",
     "http://host.docker.internal:8030/v1"),
    ("PDF_REGIONS_TRIAGE_MODEL", "取り込み", "図の種類を判定する VLM のモデル名。", "google/gemma-4-E4B-it"),
    ("PDF_REGIONS_VLM_URL", "取り込み", "PDF_CONVERTER=regions で図・グラフの説明文を作成する VLM の接続先。",
     "http://host.docker.internal:8000/v1"),
    ("PDF_REGIONS_VLM_MODEL", "取り込み", "図・グラフの説明文を作成する VLM のモデル名。", "google/gemma-4-26B-A4B-it"),
    ("PDF_REGIONS_FIGURES", "取り込み",
     "PDF_CONVERTER=regions での図の扱い。text（既定）は VLM を使用せず図中の文字と題のみを残す。"
     "describe は種類を判定し図・グラフに説明文（自動生成の印付き）を付ける（説明文には読み誤りが含まれることがある）。"
     "off は図を出力しない。", "text"),
    ("PDF_REGIONS_TIMEOUT", "取り込み",
     "PDF_CONVERTER=regions での 1 回の要求の時間切れ（秒）。応答の時間切れは送り直さず、時間切れ・接続の失敗・5xx が 2 回続いた接続先は"
     " 300 秒のあいだ呼ばない。", "180"),
    ("PDF_REGIONS_RETRIES", "取り込み", "PDF_CONVERTER=regions で接続の失敗・5xx の場合に送り直す回数（応答の時間切れは送り直さない）。", "2"),
    ("PDF_REGIONS_PARSE_CONCURRENCY", "取り込み",
     "Nemotron Parse への同時の要求の上限。同じコンテナの全作業プロセスで共有する（PDF_REGIONS_LOCK_DIR のロック用ファイル）。"
     "この変換器からの要求だけを数える。", "2"),
    ("PDF_REGIONS_VLM_CONCURRENCY", "取り込み",
     "図の判定・説明の VLM それぞれへの同時の要求の上限。同じコンテナの全作業プロセスで共有する。26B は画像付きの同時 6 本で停止した記録がある。",
     "2"),
    ("PDF_REGIONS_DEADLINE", "取り込み",
     "PDF_CONVERTER=regions で文書 1 本にかける時間の上限（秒、0 は無し）。超えたら残りのページは文字層だけで組み立て、図の説明を省く。", "600"),
    ("PDF_REGIONS_MAX_PAGES", "取り込み", "PDF_CONVERTER=regions で変換するページ数の上限。超える PDF は文字層のみの簡易変換で変換する（Nemotron Parse・VLM を使用しない）。", "300"),
    ("PDF_REGIONS_LOCK_DIR", "取り込み",
     "PDF_CONVERTER=regions で、作業プロセスをまたぐ同時数の上限に使うロック用ファイルの置き場所（空なら一時フォルダの pdf_regions_slots）。", ""),
    ("PDF_REGIONS_TITLE", "取り込み", "PDF_CONVERTER=regions で、枠の題名の欄を文書の先頭に見出し（## 題名）として置くかどうか。", "true"),
    ("MARKDOWN_MIN_CHUNK_TOKENS", "取り込み",
     "本文がこの値に満たないチャンク（見出しのみの節など）を、同じ文書の前後のチャンクに統合する（Markdown の分割）。", "80"),
    ("CHUNK_ID_MODE", "取り込み",
     "チャンク ID の生成方式。document は文書名・位置・本文のハッシュを使用する。content は本文のみを使用し、同一本文の節は文書をまたいで 1 つのノードになる（過去の記録の再現用）。",
     "document"),
    ("LOCAL_LLM_MAX_TOKENS", "抽出", "ローカル LLM（OpenAI 互換の接続）の 1 回あたりの出力上限（トークン数）。超過すると抽出の出力が途中で打ち切られる。", "8192"),
    ("UPDATE_GRAPH_CHUNKS_PROCESSED", "抽出", "抽出で一度に処理するチャンク数。この単位ごとに取り消しの有無を確認し、進捗を記録する。", "20"),
    ("KNN_MIN_SCORE", "後処理", "チャンク間に SIMILAR を生成する類似度の下限（後処理 materialize_text_chunk_similarities）。", "0.8"),
    ("DUPLICATE_SCORE_VALUE", "後処理", "「グラフ整備」の「重複ノード」で候補とする埋め込みの類似度の下限。", "0.97"),
    ("DUPLICATE_TEXT_DISTANCE", "後処理", "「グラフ整備」の「重複ノード」で候補とする名称の編集距離の上限（名称が 6 文字以上の場合、この値未満を候補とする）。", "3"),
    ("CHUNK_REFERENCE_MODE", "後処理",
     "参照リンク（REFERS_TO）の生成方式。llm は LLM のみ、hybrid は規則で解決できない参照のみ LLM、rule は規則のみを使用する。", "llm"),
    ("CHUNK_REFERENCE_MODEL", "後処理", "参照リンクの抽出に使用する LLM。未設定の場合は GRAPH_CLEANUP_MODEL、それも未設定の場合は gemma4 を使用する。", "gemma4"),
    ("SECTION_SUMMARY_MODEL", "後処理",
     "節の要点（後処理 build_section_summaries）の作成に使用する LLM。LLM_MODEL_CONFIG_<名前> の OpenAI 互換の接続（モデル名,接続先,鍵）を使用する。",
     "gemma4"),
    ("SECTION_SUMMARY_CONCURRENCY", "後処理",
     "節の要点の作成で LLM へ同時に送る要求の数。3 を超える値は 3 として扱う（gemma4 は同時の要求で停止した記録がある）。"
     "上限は 1 プロセスあたりで、同じプロセスの一覧選択の要求と共有する（backend の別のワーカーの一覧選択の要求とは共有しない）。"
     "節の要点の作成そのものは、同じコンテナの中では同時に 1 本だけ流れる（2 本目は 1 本目の終了を待つ）。", "3"),
    ("SECTION_SUMMARY_TIMEOUT", "後処理",
     "節の要点の作成の 1 回の要求の時間切れ（秒）。失敗した節は 2 回まで送り直し、それでも失敗すれば飛ばす。"
     "接続の失敗・時間切れ・HTTP 401/403/404/429/5xx が 8 回続いた場合と、接続先の設定が読めない場合は、以後の節を LLM に送らずに後処理を Failed にする"
     "（入力が変わらない節の要点の書き戻しは続ける）。LLM に送った節の 1 割を超えて失敗した場合も Failed にする。", "120"),
    ("DOCUMENT_NUMBERS_CONFIG", "後処理",
     "文書番号の規則（系列・正規形）の設定ファイル。文書層の後処理（build_document_layer）が読み込む。", document_layer_rules.DEFAULT_CONFIG),
    ("DOCUMENT_REGISTRY_TABLE", "後処理",
     "文書台帳の正本の表（人が直す Postgres の表）の名前。文書層の後処理が、表の値を文書から取り出した値より優先して文書番号ノードに写す。"
     "接続先は DOCUMENT_REGISTRY_DSN（接続文字列のためこの一覧には表示しない）。DOCUMENT_REGISTRY_DSN が空の場合と、表につながらない場合は表を使わない。",
     "document_registry"),
    ("GRAPH_CHUNK_LIMIT", "グラフ表示",
     "グラフ確認画面で 1 文書あたりに描画するチャンクの上限。0 以下は上限なし。上流版は 50 固定。", str(constants.GRAPH_CHUNK_LIMIT)),
    ("CHAT_RERANK_ENABLED", "チャット検索",
     "チャットの検索でリランク（候補を取得した後に並べ替える 2 段階の検索）を使用するかどうか。"
     "リランクの対象は、チャンクを検索するチャットモード（vector・graph+vector・fulltext・graph+vector+fulltext）である。"
     "false の場合は上流版の検索経路で検索する。", "false"),
    ("CHAT_RERANK_URL", "チャット検索", "リランカーの接続先。接続できない場合は警告を出力し、検索スコアの順序のまま使用する。",
     "http://host.docker.internal:8006/v1/rerank"),
    ("CHAT_REFERENCE_DOC_SELECT_MODEL", "チャット検索",
     "参照追跡で、文書のみを指す参照の参照先を節の要点の一覧から選択する場合（検索設定「文書のみを指す参照の参照先の選択方法」）に使用する LLM。"
     "LLM_MODEL_CONFIG_<名前> の OpenAI 互換の接続を使用する。", "gemma4"),
    ("CHAT_REFERENCE_DOC_SELECT_TIMEOUT", "チャット検索",
     "上記の LLM の 1 回の要求の時間切れ（秒）。時間切れ・失敗の場合は、リランクで選択する。"
     "1 つの質問の中で一度失敗した場合は、その質問の残りの参照は LLM を呼ばずにリランクで選択する。"
     "同じプロセスで節の要点の作成が同時要求の上限を使っている場合は、空きを 10 秒まで待ち、空かなければリランクで選択する。", "60"),
    ("CHAT_LINE_RERANK_ENABLED", "チャット検索",
     "文脈に含めるエンティティとリレーションの行もリランクで選択するかどうか。false の場合は文字数の上限まで順に格納する。", "true"),
    ("EMBEDDING_PROVIDER", "埋め込み",
     "埋め込みの提供元。未設定時の既定値が処理によって異なる（文書層の後処理のみ vllm）ため、必ず設定する。", "sentence-transformer（文書層の後処理は vllm）"),
    ("EMBEDDING_MODEL", "埋め込み", "埋め込みモデル。未設定時の既定値が処理によって異なる。",
     "all-MiniLM-L6-v2（文書層の後処理は cl-nagoya/ruri-v3-310m）"),
]

# 未設定のときに代わりに読む環境変数
FALLBACK_ENV = {"CHUNK_REFERENCE_MODEL": "GRAPH_CLEANUP_MODEL"}

# リランク有効のとき、チャンクを検索するチャットモードで代わりに適用される設定: (環境変数名, 検索設定の表示名)。None は使用されない
# 表示名は rerank_retriever.py の RETRIEVAL_SETTING_SPECS の表示名と一字一句同じにする（langchain 系に依存させないため import せず、写しを持つ）
RERANK_REPLACES = {"VECTOR_SEARCH_TOP_K": ("CHAT_CANDIDATE_K", "候補チャンク数"),
                   "CHAT_SEARCH_KWARG_SCORE_THRESHOLD": ("CHAT_CANDIDATE_MIN_SCORE", "候補のスコアしきい値"),
                   "CHAT_DOC_SPLIT_SIZE": None,
                   "VECTOR_GRAPH_SEARCH_ENTITY_LIMIT": ("CHAT_ENTITY_LIMIT", "起点の上限（文書ごと）")}


def _rerank_replacement(name) -> str:
    """リランク有効時に代わりに適用される設定の呼び方。例: 検索設定の「候補チャンク数」（CHAT_CANDIDATE_K）"""
    env, label = RERANK_REPLACES[name]
    return f"検索設定の「{label}」（{env}）"


# コードの定数（環境変数では変更不可）: 名前, 区分, 説明。値は src/shared/constants.py から読む
OVERVIEW_CONST = [
    ("VECTOR_SEARCH_TOP_K", "チャット検索",
     "上流版の検索経路で取得するチャンク数。リランク有効時、チャンクを検索するチャットモードでは使用されず、"
     f"{_rerank_replacement('VECTOR_SEARCH_TOP_K')}が適用される。"),
    ("CHAT_SEARCH_KWARG_SCORE_THRESHOLD", "チャット検索",
     "上流版の検索経路で、スコアがこの値未満の候補を除外する。リランク有効時、チャンクを検索するチャットモードでは"
     f"{_rerank_replacement('CHAT_SEARCH_KWARG_SCORE_THRESHOLD')}が適用される。"),
    ("CHAT_DOC_SPLIT_SIZE", "チャット検索",
     "上流版の検索経路で、取得した文脈をこの大きさ（トークン数）の断片に分割して再選択する。"
     "リランク有効時、チャンクを検索するチャットモードでは使用されない。"),
    ("VECTOR_GRAPH_SEARCH_ENTITY_LIMIT", "チャット検索",
     "上流版の検索経路で、グラフ展開の起点とするエンティティの上限（文書ごと）。リランク有効時、チャンクを検索するチャットモードでは"
     f"{_rerank_replacement('VECTOR_GRAPH_SEARCH_ENTITY_LIMIT')}が適用される。"),
]

GUNICORN_TIMEOUT_MEANING = ("作業プロセスが応答しない時間の上限（秒）。超過すると gunicorn がそのプロセスを停止し、処理中のリクエストはすべて失敗する。"
                            "uvicorn の作業プロセスでは、リクエスト 1 件の処理時間ではなく、イベントループが停止している時間に適用される。")

GROUP_ORDER = ["取り込み", "抽出", "後処理", "グラフ表示", "チャット検索", "埋め込み", "サーバー"]

# 検証件数の一覧: key, 区分, 表示名, 照会, 基準値（None は参考、0 は 0 件で合格、文字列はその key の件数と同数で合格）
# 照会は value 列を返す。sub 列も返すときは、value の合計を件数とし、sub ごとの内訳を detail に並べる
COUNTS = [
    ("documents", "文書とチャンク", "文書（Document）", "MATCH (d:Document) RETURN count(d) AS value", None),
    ("chunks", "文書とチャンク", "チャンク", "MATCH (c:Chunk) RETURN count(c) AS value", None),
    ("chunks_multi_doc", "文書とチャンク", "複数の文書に属するチャンク",
     "MATCH (c:Chunk)-[:PART_OF]->(d:Document) WITH c, count(DISTINCT d) AS n WHERE n > 1 RETURN count(c) AS value", 0),
    ("next_chunk_cross_doc", "文書とチャンク", "文書をまたぐ NEXT_CHUNK",
     "MATCH (a:Chunk)-[:NEXT_CHUNK]->(b:Chunk) WHERE a.fileName <> b.fileName RETURN count(*) AS value", 0),
    ("document_without_filename", "文書とチャンク", "ファイル名のない文書",
     "MATCH (d:Document) WHERE d.fileName IS NULL RETURN count(d) AS value", 0),
    ("skipped_chunks", "文書とチャンク", "抽出失敗チャンク（例外で抽出が中断したチャンク）",
     "MATCH (d:Document) WHERE coalesce(d.skipped_chunks, 0) > 0 RETURN d.fileName AS sub, d.skipped_chunks AS value ORDER BY sub", 0),
    ("standard_internal", "文書層", "文書番号ノード（取り込み済み文書）",
     "MATCH (s:Standard) WHERE s.method = 'rule' AND NOT coalesce(s.external, false) RETURN count(s) AS value", None),
    ("standard_external", "文書層", "文書番号ノード（未取り込み文書）",
     "MATCH (s:Standard) WHERE s.method = 'rule' AND coalesce(s.external, false) RETURN count(s) AS value", None),
    ("standard_duplicates", "文書層", "正規形が同一の文書番号の重複（大文字・小文字、空白・ハイフンの違いを含む）",
     "MATCH (s:Standard) WHERE s.method = 'rule' WITH replace(replace(toUpper(s.id), ' ', ''), '-', '') AS k, count(*) AS n WHERE n > 1 "
     "RETURN count(k) AS value", 0),
    ("is_version_of", "文書層", "IS_VERSION_OF（文書 → 文書番号）",
     "MATCH (:Document)-[r:IS_VERSION_OF]->(:Standard) RETURN count(r) AS value", None),
    ("has_current_version", "文書層", "HAS_CURRENT_VERSION（文書番号 → 現行版の文書）",
     "MATCH (:Standard)-[r:HAS_CURRENT_VERSION]->(:Document) RETURN count(r) AS value", None),
    ("doc_refers_internal", "文書層", "文書から取り込み済み文書への REFERS_TO",
     "MATCH (:Document)-[r:REFERS_TO]->(:Standard) WHERE r.method = 'rule' AND coalesce(r.scope, '') <> 'external' RETURN count(r) AS value", None),
    ("doc_refers_external", "文書層", "文書から未取り込み文書への REFERS_TO",
     "MATCH (:Document)-[r:REFERS_TO]->(:Standard) WHERE r.method = 'rule' AND r.scope = 'external' RETURN count(r) AS value", None),
    ("lists", "文書層", "LISTS（発行目録 → 文書番号）", "MATCH (:Document)-[r:LISTS]->(:Standard) RETURN count(r) AS value", None),
    ("applies_to", "文書層", "APPLIES_TO（文書 → 型式、規則により生成）",
     "MATCH (:Document)-[r:APPLIES_TO]->() WHERE r.method = 'rule' RETURN count(r) AS value", None),
    ("sections", "章・節の層", "規則により生成した章・節（Section）",
     "MATCH (s:Section) WHERE s.method = 'rule' RETURN s.fileName AS sub, count(*) AS value ORDER BY sub", None),
    ("sections_without_document", "章・節の層", "所属文書のない章・節（削除済み文書の残存分）",
     "MATCH (d:Document) WITH collect(d.fileName) AS files "
     "MATCH (s:Section) WHERE s.method = 'rule' AND NOT s.fileName IN files RETURN s.fileName AS sub, count(*) AS value ORDER BY sub", 0),
    ("chunks_of_sectioned_documents", "章・節の層", "章・節を持つ文書のチャンク",
     "MATCH (s:Section) WHERE s.method = 'rule' WITH collect(DISTINCT s.fileName) AS files "
     "MATCH (c:Chunk)-[:PART_OF]->(d:Document) WHERE d.fileName IN files RETURN count(c) AS value", None),
    ("chunks_in_section", "章・節の層", "章・節に属するチャンク（IN_SECTION）",
     "MATCH (c:Chunk) WHERE EXISTS { (c)-[:IN_SECTION]->(:Section) } RETURN count(c) AS value", "chunks_of_sectioned_documents"),
    ("chunk_refers_chunk", "参照リンク", "チャンク → チャンクの REFERS_TO（method 別）",
     "MATCH (:Chunk)-[r:REFERS_TO]->(:Chunk) RETURN coalesce(r.method, '(method なし)') AS sub, count(*) AS value ORDER BY sub", None),
    ("chunk_refers_document", "参照リンク", "チャンク → 文書の REFERS_TO（参照先のチャンクを特定できなかったもの）",
     "MATCH (:Chunk)-[r:REFERS_TO]->(:Document) RETURN count(r) AS value", None),
    ("chunk_refers_section", "参照リンク", "チャンク → 章・節の REFERS_TO（via 別）",
     "MATCH (:Chunk)-[r:REFERS_TO]->(:Section) RETURN coalesce(r.via, '(via なし)') AS sub, count(*) AS value ORDER BY sub", None),
    ("entities", "エンティティ", "エンティティ", "MATCH (e:__Entity__) RETURN count(e) AS value", None),
    ("entities_without_chunk", "エンティティ", "チャンクからの HAS_ENTITY がないエンティティ",
     "MATCH (e:__Entity__) WHERE NOT EXISTS { (:Chunk)-[:HAS_ENTITY]->(e) } "
     "RETURN CASE WHEN e.method = 'rule' THEN '規則により生成' ELSE '抽出・その他' END AS sub, count(*) AS value ORDER BY sub", None),
    ("untyped_entities", "エンティティ", "__Entity__ 以外のラベルがないエンティティ",
     "MATCH (e:__Entity__) WHERE size(labels(e)) = 1 RETURN count(e) AS value", None),
    ("rule_entities_without_embedding", "エンティティ", "埋め込みのない、規則により生成したエンティティ",
     "MATCH (e:__Entity__) WHERE e.method = 'rule' AND e.embedding IS NULL RETURN count(e) AS value", 0),
    ("extracted_section_entities", "エンティティ", "抽出でラベル Section が付与されたエンティティ（規則により生成した章・節と混在する）",
     "MATCH (e:__Entity__:Section) RETURN count(e) AS value", 0),
]

COUNT_TIMEOUT_SECONDS = 20  # 照会 1 本の時間の上限
DETAIL_ITEMS = 30           # 内訳に並べる件数の上限

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
PROCESS_STARTED_AT = time.time()  # 作業プロセスが score.py を読み込んだ時刻（backend/.env もこのとき読まれる）

_URL_USERINFO = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]*@", re.I)
_URL_SECRET_PARAM = re.compile(r"([?&][^=&\s]*(?:key|token|secret|password|auth)[^=&\s]*=)[^&\s]*", re.I)


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


def _safe(value):
    """返す値の整形。URL の利用者名・パスワードの部分と、鍵らしいクエリの値は伏せ、長すぎる値は切る。"""
    if value is None:
        return None
    text = _URL_SECRET_PARAM.sub(r"\1***", _URL_USERINFO.sub(r"\1***@", str(value)))
    return text if len(text) <= 300 else text[:300] + "…"


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds") if timestamp else None


def _env_file_values(path) -> dict:
    """backend/.env の今の値のうち、一覧の名前だけ。読めなければ空。"""
    if not path or not os.path.isfile(path):
        return {}
    try:
        from dotenv import dotenv_values
        values = dotenv_values(path)
    except Exception as e:
        logging.warning("settings_overview: cannot read %s: %s", path, e)
        return {}
    names = {name for name, *_ in OVERVIEW_ENV}
    return {k: v for k, v in values.items() if k in names and v is not None}


def _timeout_arg(args):
    for i, a in enumerate(args):
        if a in ("--timeout", "-t") and i + 1 < len(args) and args[i + 1].isdigit():
            return int(args[i + 1])
        if a.startswith("--timeout=") and a.split("=", 1)[1].isdigit():
            return int(a.split("=", 1)[1])
        if a.startswith("-t") and a[2:].isdigit():
            return int(a[2:])
    return None


def gunicorn_timeout(argv=None, cmd_args=None):
    """gunicorn の --timeout を (秒, 出どころ) で返す。作業プロセスの親（arbiter）の起動コマンドを読む。
    優先は 起動コマンド > GUNICORN_CMD_ARGS > gunicorn の既定（30）。設定ファイル（gunicorn.conf.py）は見ない。"""
    if argv is None:
        try:
            with open(f"/proc/{os.getppid()}/cmdline", "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except OSError:
            argv = []
    if not any(os.path.basename(a).startswith("gunicorn") for a in argv[:3]):
        return None, "gunicorn 外で動作中（取得不可）"
    found = _timeout_arg(argv)
    if found is not None:
        return found, "起動コマンド"
    try:
        found = _timeout_arg(shlex.split(os.environ.get("GUNICORN_CMD_ARGS", "") if cmd_args is None else cmd_args))
    except ValueError:
        found = None
    if found is not None:
        return found, "GUNICORN_CMD_ARGS"
    return 30, "gunicorn の既定値"


def collect_settings(env_file=ENV_FILE) -> list:
    """設定の一覧。値は作業プロセスの環境変数（get_value_from_env と同じく、空の値は未設定と同じに扱う）。
    backend/.env の現在の値と異なる項目は pending にする（作業プロセスの起動後に書き換えた。再起動後に反映される）。"""
    file_values = _env_file_values(env_file)
    rows, effective = [], {}
    for name, group, meaning, default in OVERVIEW_ENV:
        if SECRET_NAME.search(name):
            continue
        raw = os.environ.get(name)
        if raw is None or raw == "":
            value, source = default, "既定値（未設定）" if raw is None else "既定値（空の値）"
            fallback = FALLBACK_ENV.get(name)
            if fallback and os.environ.get(fallback):
                value, source = os.environ[fallback], f"{fallback} の値（未設定のため）"
        else:
            # backend/.env にある名前は起動時にその値で上書きされている（score.py の load_dotenv(override=True)）
            value = raw
            source = "環境変数（backend/.env）" if name in file_values else "環境変数（docker-compose.yml・ルートの .env）"
        effective[name] = value
        pending = name in file_values and file_values[name] != (raw or "")
        rows.append({"name": name, "group": group, "value": _safe(value), "default": _safe(default), "source": source,
                     "meaning": meaning, "file_value": _safe(file_values[name]) if pending else None,
                     "pending": pending, "note": None})
    for name, group, meaning in OVERVIEW_CONST:
        value = getattr(constants, name, None)
        rows.append({"name": name, "group": group, "value": _safe(value), "default": _safe(value),
                     "source": "コードの定数（環境変数では変更不可）", "meaning": meaning,
                     "file_value": None, "pending": False, "note": None})
    timeout, source = gunicorn_timeout()
    rows.append({"name": "gunicorn --timeout", "group": "サーバー", "value": "—" if timeout is None else str(timeout),
                 "default": "300（Dockerfile の CMD）", "source": source, "meaning": GUNICORN_TIMEOUT_MEANING,
                 "file_value": None, "pending": False, "note": None})

    # 設定が反映されない組み合わせに注記する
    by_name = {r["name"]: r for r in rows}
    tokenizer = str(effective.get("CHUNK_TOKENIZER", ""))
    if tokenizer.endswith(".json") and not os.path.isfile(tokenizer):
        by_name["CHUNK_TOKENIZER"]["note"] = "ファイルが存在しない。トークン数は gpt2 で算出している"
    config = str(effective.get("DOCUMENT_NUMBERS_CONFIG", ""))
    if config and not os.path.isfile(config):
        by_name["DOCUMENT_NUMBERS_CONFIG"]["note"] = "ファイルが存在しない。文書層の後処理は失敗する"
    if (str(effective.get("PDF_CHUNK_STRATEGY", "")).strip().lower() == "markdown"
            and not _truthy(effective.get("MARKDOWN_CHUNKING", "true"))):
        by_name["PDF_CHUNK_STRATEGY"]["note"] = "MARKDOWN_CHUNKING が false のため無効（トークン数で分割している）"
    if (str(effective.get("PDF_CHUNK_STRATEGY", "")).strip().lower() != "markdown"
            or not _truthy(effective.get("MARKDOWN_CHUNKING", "true"))):
        by_name["PDF_CONVERTER"]["note"] = "PDF_CHUNK_STRATEGY が markdown でないか MARKDOWN_CHUNKING が false のため無効"
    elif str(effective.get("PDF_CONVERTER", "regions") or "regions").strip().lower() != "regions":
        by_name["PDF_CONVERTER"]["note"] = "regions 以外の値（pymupdf4llm は外した）。regions として扱っている"
    if _truthy(effective.get("CHAT_RERANK_ENABLED", "false")):
        for name, replacement in RERANK_REPLACES.items():
            if name in by_name:
                by_name[name]["note"] = (f"リランク有効のため、チャンクを検索するチャットモードでは{_rerank_replacement(name)}が適用される"
                                         if replacement else "リランク有効のため、チャンクを検索するチャットモードでは使用されない")
    rows.sort(key=lambda r: GROUP_ORDER.index(r["group"]) if r["group"] in GROUP_ORDER else len(GROUP_ORDER))
    return rows


def process_info(env_file=ENV_FILE) -> dict:
    """作業プロセスの起動時刻と backend/.env の更新時刻。起動後に書き換えた設定は、再起動するまで反映されない。"""
    try:
        mtime = os.path.getmtime(env_file)
    except OSError:
        mtime = None
    return {"started_at": _iso(PROCESS_STARTED_AT), "env_file_mtime": _iso(mtime),
            "env_file_changed": bool(mtime and mtime > PROCESS_STARTED_AT)}


def collect_counts(run) -> list:
    """検証件数の一覧。run(照会) は行の辞書のリストを返す。照会ごとに失敗を拾い、1 本の失敗で全体を止めない。"""
    rows, values = [], {}
    for key, group, label, query, _line in COUNTS:
        row = {"key": key, "group": group, "label": label, "value": None, "pass_line": "—", "ok": None,
               "detail": None, "error": None}
        try:
            records = run(query)
            if records and "sub" in records[0]:
                row["value"] = sum(int(r.get("value") or 0) for r in records)
                parts = [f"{r.get('sub')} {r.get('value')}" for r in records[:DETAIL_ITEMS]]
                if len(records) > DETAIL_ITEMS:
                    parts.append(f"ほか {len(records) - DETAIL_ITEMS} 件")
                row["detail"] = "、".join(parts)
            else:
                row["value"] = int(records[0].get("value") or 0) if records else 0
        except Exception as e:
            logging.warning("settings_overview: count %s failed: %s", key, e)
            row["error"] = str(e)[:300]
        values[key] = row["value"]
        rows.append(row)
    labels = {key: label for key, _group, label, _query, _line in COUNTS}
    for row, (_key, _group, _label, _query, line) in zip(rows, COUNTS):
        if line is None:
            continue
        if line == 0:
            row["pass_line"], expected = "0", 0
        else:
            row["pass_line"], expected = f"「{labels[line]}」と同数", values.get(line)
        row["ok"] = None if row["value"] is None or expected is None else row["value"] == expected
    return rows


def collect_overview(credentials) -> dict:
    """設定値と検証件数をまとめて返す。資格情報が通らなければ例外にする（設定も返さない）。"""
    from neo4j import GraphDatabase, READ_ACCESS, unit_of_work

    @unit_of_work(timeout=COUNT_TIMEOUT_SECONDS)
    def read_all(tx, text):
        return [record.data() for record in tx.run(text)]

    start = time.time()
    # 画面が接続を backend の設定に任せたとき（ユーザー名・パスワードを送らない）は、ほかの API と同じく環境変数で補う
    # （ほかの API の Neo4jGraph は、空の値を NEO4J_URI・NEO4J_USERNAME・NEO4J_PASSWORD・NEO4J_DATABASE で補う）
    uri = credentials.uri or os.environ.get("NEO4J_URI")
    user = credentials.userName or os.environ.get("NEO4J_USERNAME")
    password = credentials.password or os.environ.get("NEO4J_PASSWORD")
    database = credentials.database or os.environ.get("NEO4J_DATABASE") or None
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
            counts = collect_counts(lambda text: session.execute_read(read_all, text))
    finally:
        driver.close()
    return {"settings": collect_settings(), "counts": counts, "process": process_info(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed": round(time.time() - start, 2)}


router = APIRouter()


@router.post("/settings_overview")
async def settings_overview(credentials: Neo4jCredentials = Depends(get_neo4j_credentials)):
    """保守: 設定値と検証件数の一覧（参照のみ）。"""
    try:
        data = await asyncio.to_thread(collect_overview, credentials)
        logging.info("settings_overview: %d settings, %d counts, %.2fs", len(data["settings"]), len(data["counts"]), data["elapsed"])
        return create_api_response("Success", data=data)
    except Exception as e:
        logging.exception("settings_overview failed")
        return create_api_response("Failed", message="設定値と検証件数を取得できませんでした", error=str(e))
