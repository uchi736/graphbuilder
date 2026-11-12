# -*- coding: utf-8 -*-
"""節の要点（後処理 build_section_summaries）と、要点の一覧から LLM に節を選ばせる部品（参照追跡の reference_doc_select = summary_list）。

要点（2026-11-09、実験台帳 X16 の指示を写す）
  Section ごとに、見出しの経路＋主所属（IN_SECTION {primary: true}）のチャンクの本文の先頭 3,000 字を LLM（既定 gemma4）に渡し、
  検索用の要点（1〜2 文・80 字以内）を作る。主所属のチャンクが無い節（目録で切り出した小さな節が前の節のチャンクの中にある場合）は、
  Section.text（章・節の層が目録から切り出した節の本文）を使う。見出しの題・# ・空白を除いて 10 字に満たない本文は無いものとみなす。章（level 1）は、子の節の要点も入れて章の要点にする（子を先に作る）。
  level 2 以下でも、自分の本文が無く子を持つ節は、子の要点から作る（本文が無いと要点が空になるため）。
  書く先: Section.summary・summary_model・summary_at・summary_src（LLM への入力の sha1。指示文を含む）。
  summary_src が同じ節は作り直さない。LLM が断り・前置きだけを返した節は、summary を持たせず summary_src だけを書く（入力が変わるまで聞き直さない）。失敗した節は飛ばして続ける。
  打ち切り: 接続の失敗・時間切れ・HTTP 401/403/404/429/5xx が続けて ABORT_AFTER 回になったら、以後は LLM に送らない（接続先の設定 LLM_MODEL_CONFIG_<名前> が
        読めないときは最初から送らない）。打ち切った後も、控えからの書き戻しは浅い段（章）まで続ける。
        打ち切った場合と、LLM に送った節の FAIL_RATIO を超えて失敗した場合は complete = False を返し、後処理（score.py）は Failed を返す。
  同時要求: LLM への同時要求の上限 MAX_CONCURRENCY は 1 プロセスあたりである（本番の backend は gunicorn の複数のワーカー）。
        要点の作成は、同じコンテナの中ではファイルの鍵（fcntl）で同時に 1 本に限る（2 本目は 1 本目の終了を待つ。待った後は、1 本目が作った要点をそのまま使う）。
        一覧選択の要求は、別のワーカーから同時に届きうる（この上限の対象はそのワーカーの中だけ）。
  章・節の層との関係: 章・節の層の作り直しの途中に読まないよう、層の貸し出し（lease）が返るまで待ってから節を読む。
        書き戻しだけの実行（restore_only = True）は、LLM を呼ばずに控えから書き戻す。後処理で章・節の層を作り直し、節の要点の段を流さないときに使う。
  控え: 章・節の層の後処理（build_section_layer）は Section を毎回消して作り直すので、要点が消える。作った要点は
        (:SectionSummary {src, summary, model, at, fileName}) にも控え、入力が同じ節には LLM を呼ばずに書き戻す。
        SectionSummary は関係を持たない管理用のノードで、後処理・件数・スキーマの対象から外す（shared/constants.py）。
        控えの掃除（全文書を対象に流したときだけ）は、今の節の要点の入力と、この回の初めに読んだ節の入力のどちらとも一致しない控えだけを消す。
        グラフの貸し出しが出ている間（章・節の層の作り直しの途中など）は掃除しない。
  要点は既定の検索の文脈に出ない: 文脈のノードの行は id（無ければ name・title・id・description で終わる名前の値）と description だけを使い、
  Section はグラフ展開の起点（エンティティ）にならない（tests/test_section_summaries.py で確かめる）。

一覧選択（参照追跡の文書だけを指す参照）
  参照先の文書の節を候補にし、リランカー（「質問＋参照の一文」対「節の見出しの経路＋要点」、要点が無ければ本文の先頭）で上位 20 件にし、
  LLM に「質問・参照元の節・既に手元にある節・候補の一覧」を見せて、最大 k 件の番号を JSON の配列で返させる（X16 の一覧の指示）。
  節の数が閾値を超える文書は、章の要点で章を選んでから、その章の節で同じことをする（2 段）。
  LLM の失敗・空・範囲外の番号だけのときは SelectionFailed を上げ、呼び出し側が従来の選び方（rerank）に戻す。
  一覧選択の要求は、このプロセスの同時要求の枠が LIST_SLOT_WAIT 秒で空かなければ失敗とする（待ちに上限を付ける）。

このモジュールは langchain を import しない（試験を軽くするため）。DB は呼び出し側の関数（run）で読み書きする。
"""
import fcntl
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import requests

from src.shared.constants import GRAPH_EDIT_MANAGEMENT_LABELS

GRAPH_LOCK_LABEL = GRAPH_EDIT_MANAGEMENT_LABELS[2]   # __GraphLock__
# ---- 指示（X16: select_summ.py の SUM_SYS と、select_compare.py の LLM_SYS の写し）----
SUMMARY_SYSTEM = ("社内規格の 1 節を読み、検索用の要点を 1〜2 文（80 字以内）で書く。何についての規定か（対象の部品・型式・材質・工程・試験）、"
                  "何を定めているか（値・条件・手順・禁止）、他の規格や節を参照していればその番号を必ず含める。前置きや箇条書きは書かない。")
LIST_SYSTEM = ("あなたは社内規格の調査員である。質問に答えるために追加で読むべき節を、候補の一覧から選ぶ。候補には番号が付いている。"
               "選んだ番号だけを JSON の配列で返す（例: [3, 7, 1, 9]）。説明は書かない。")

BODY_CHARS = 3000             # 要点の入力にする本文の先頭の字数
MIN_BODY_CHARS = 10           # 見出しの題・見出し記号（#）・空白を除いてこの字数に満たない本文は、本文が無いとみなす
                              # （「##」だけの節で LLM が「対象のテキストが入力されていません」と答えた。11/9 の kgb4 で 34 節）
CHILD_SUMMARY_CHARS = 3000    # 章の入力に入れる子の要点の合計の上限
SUMMARY_MAX_TOKENS = 160      # X16 と同じ
SUMMARY_MAX_CHARS = 200       # 保存する要点の上限（指示は 80 字。暴走した出力を切り詰める）
MAX_CONCURRENCY = 3           # gemma4 への同時要求の上限（1 プロセスあたり。26B は並列の要求で止まった記録がある）
ABORT_AFTER = 8               # 続けてこの数の要求が接続の失敗（時間切れ・HTTP 401/403/404/429/5xx を含む）になったら打ち切る
FAIL_RATIO = 0.1              # LLM に送った節のうち、この割合を超えて失敗したら complete = False（後処理は Failed）
LIST_SLOT_WAIT = 10.0         # 一覧選択の要求が、このプロセスの同時要求の枠を待つ上限（秒）
BUILD_LOCK_WAIT = 3600.0      # 要点の作成が、同じコンテナの別の作成の終了を待つ上限（秒）
SECTION_LAYER_WAIT = 900.0    # 章・節の層の作り直し（貸し出し中）の終了を待つ上限（秒）
WRITE_BATCH = 20              # この数ごとに書き込む（途中で止まっても作った分は残る）
LIST_TOP_K = 20               # 一覧に並べる候補の数（X16 の 20）
LIST_MAX_TOKENS = 64          # X16 と同じ
LIST_HEAD_CHARS = 150         # 要点の無い節の一覧の行に出す本文の先頭の字数
ALREADY_MAX = 12              # 「既に手元にある節」に並べる数の上限
# 要点ではない答え（入力への断り・前置き・箇条書き）の文。11/9 の kgb4 で 590 節中 4 節（「ご提示いただいた…」「…を貼り付けて…」）
META_SENTENCE = re.compile(r"ご提示|ご指定|提供されていません|入力されていません|含まれておりません|含まれていません|貼り付け|いただければ|"
                           r"以下のよう|以下の通り|以下のとおり|承知しました|作成いたします|整理します|まとめると")
CHAPTER_NOTE = "### 指示\nこの章全体について、要点を 1〜2 文（80 字以内）で書く。下位の節を列挙しない。"

_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENCY)   # このプロセスから LLM への同時要求（同じプロセスの要点の作成と一覧選択で共有。別のワーカーとは共有しない）
_BUILD_LOCK_PATH = os.path.join(tempfile.gettempdir(), "llm-graph-builder-section-summaries.lock")
_LLM_STATE = {"template_kwargs": True}

SECTIONS_QUERY = """
MATCH (s:Section) WHERE s.method = 'rule' AND ($files IS NULL OR s.fileName IN $files)
OPTIONAL MATCH (p:Section)-[:HAS_SECTION]->(s)
RETURN s.id AS id, s.fileName AS file_name, s.path AS path, s.title AS title, s.number AS number, s.level AS level,
       p.id AS parent, s.summary AS summary, s.summary_src AS summary_src, s.text AS text
"""
PRIMARY_BODY_QUERY = """
MATCH (c:Chunk)-[m:IN_SECTION]->(s:Section)
WHERE m.primary = true AND s.method = 'rule' AND c.text IS NOT NULL AND ($files IS NULL OR s.fileName IN $files)
RETURN s.id AS sid, c.text AS text, c.position AS position
ORDER BY sid, position
"""
CACHE_LOOKUP_QUERY = """
UNWIND $srcs AS src
MATCH (x:SectionSummary {src: src})
RETURN x.src AS src, x.summary AS summary, x.model AS model, x.at AS at, coalesce(x.rejected, false) AS rejected
"""
WRITE_QUERY = """
UNWIND $rows AS row
MATCH (s:Section {id: row.id})
SET s.summary = row.summary, s.summary_model = row.model, s.summary_at = row.at, s.summary_src = row.src
WITH row
MERGE (x:SectionSummary {src: row.src})
SET x.summary = row.summary, x.model = row.model, x.at = row.at, x.fileName = row.fileName
"""
# LLM が要点を返さなかった（断り・前置きだけの答え）節: 入力の指紋だけを持たせ、控えに印（rejected）を残す。入力が変わるまで再び聞かない
REJECT_QUERY = """
UNWIND $rows AS row
MATCH (s:Section {id: row.id})
SET s.summary_src = row.src, s.summary_model = row.model, s.summary_at = row.at
REMOVE s.summary
WITH row
MERGE (x:SectionSummary {src: row.src})
SET x.summary = '', x.rejected = true, x.model = row.model, x.at = row.at, x.fileName = row.fileName
"""
# 本文が無い（とみなす）節に残っている要点を消す（前の決め方で作った要点を残さない）
CLEAR_QUERY = """
UNWIND $ids AS id
MATCH (s:Section {id: id})
REMOVE s.summary, s.summary_model, s.summary_at, s.summary_src
"""
CACHE_INDEX_QUERY = "CREATE INDEX section_summary_src IF NOT EXISTS FOR (x:SectionSummary) ON (x.src)"
# 控えの掃除: 今のどの節の要点の入力とも、この回の初めに読んだ節の入力（$keep）とも一致しない控えを消す（全文書を対象に流したときだけ）。
# $keep は、作成の途中で章・節の層が節を作り直した（summary_src が消えた）場合に、この回に作った・書き戻した控えを残すため
CACHE_PRUNE_QUERY = """
MATCH (s:Section) WHERE s.summary_src IS NOT NULL
WITH collect(DISTINCT s.summary_src) AS live
MATCH (x:SectionSummary) WHERE NOT x.src IN live AND NOT x.src IN $keep
DETACH DELETE x
RETURN count(*) AS removed
"""
CACHE_ANY_QUERY = "MATCH (x:SectionSummary) RETURN x.src AS src LIMIT 1"
# グラフの貸し出し（src/graph_edit/lease.py）の今の持ち主。持ち主の名前は「<処理の名前>#<乱数>」（章・節の層は section_layer#...）
LEASE_HOLDER_QUERY = (f"MATCH (l:`{GRAPH_LOCK_LABEL}` {{name: 'graph'}}) WHERE coalesce(l.lease_until, 0) >= timestamp() "
                      "RETURN l.lease_owner AS owner")


class SelectionFailed(Exception):
    """一覧選択が使えなかった（呼び出し側は従来の選び方に戻す）。
    reason: llm_failed / empty / out_of_range / no_sections / no_summaries / below_min_score / all_taken / ..."""

    def __init__(self, reason: str, raw: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.raw = raw


class SlotBusy(RuntimeError):
    """このプロセスの LLM への同時要求の枠が、待ちの上限までに空かなかった。"""


# ============================================================
# LLM（OpenAI 互換の接続。LLM_MODEL_CONFIG_<名前> = "モデル名,接続先,鍵"）
# ============================================================
def llm_config(model_key: str):
    env_key = "LLM_MODEL_CONFIG_" + model_key.upper().replace(".", "_").strip()
    raw = os.environ.get(env_key) or ""
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) < 2 or not parts[1].lower().startswith("http"):
        # 値は表示しない（鍵を含む）
        raise RuntimeError(f"{env_key} is missing or is not 'model,url,key' of an OpenAI-compatible endpoint")
    return parts[0], parts[1].rstrip("/"), (parts[2] if len(parts) > 2 else "")


def strip_thinking(text: str) -> str:
    text = text or ""
    for mark in ("</think>", "<channel|>", "<|channel|>final<|message|>"):
        if mark in text:
            text = text.rsplit(mark, 1)[1]
    return text.strip()


def chat(messages: List[dict], *, model_key: str, max_tokens: int, timeout: float, slot_wait: Optional[float] = None):
    """1 回の要求。temperature 0・思考なし。返り値: (本文, モデル名)。
    このプロセスの同時要求の枠（_SLOTS）を slot_wait 秒（省くと timeout 秒）待っても空かなければ SlotBusy。"""
    name, url, key = llm_config(model_key)
    body = {"model": name, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    wait = timeout if slot_wait is None else slot_wait
    if not _SLOTS.acquire(timeout=max(0.0, float(wait))):
        raise SlotBusy(f"no free LLM slot in this process within {float(wait):.0f} s")
    try:
        payload = dict(body, chat_template_kwargs={"enable_thinking": False}) if _LLM_STATE["template_kwargs"] else body
        r = requests.post(url + "/chat/completions", json=payload, headers=headers, timeout=timeout)
        if r.status_code in (400, 422) and _LLM_STATE["template_kwargs"]:
            # 思考の切り替えの引数を受け付けない接続先。引数なしでやり直し、以後は付けない
            r = requests.post(url + "/chat/completions", json=body, headers=headers, timeout=timeout)
            if r.ok:
                _LLM_STATE["template_kwargs"] = False
    finally:
        _SLOTS.release()
    r.raise_for_status()
    content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return strip_thinking(content), name


def summary_model_key() -> str:
    return os.environ.get("SECTION_SUMMARY_MODEL", "gemma4")


def list_model_key() -> str:
    return os.environ.get("CHAT_REFERENCE_DOC_SELECT_MODEL", "gemma4")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def list_chat(messages: List[dict]) -> str:
    """一覧選択の 1 回の要求（試験では差し替える）。同時要求の枠は LIST_SLOT_WAIT 秒まで待つ（要点の作成が枠を使っている間）。"""
    text, _ = chat(messages, model_key=list_model_key(), max_tokens=LIST_MAX_TOKENS,
                   timeout=_float_env("CHAT_REFERENCE_DOC_SELECT_TIMEOUT", 60.0), slot_wait=LIST_SLOT_WAIT)
    return text


# ============================================================
# 要点の作成
# ============================================================
def _stem(file_name: str) -> str:
    name = file_name or ""
    return re.sub(r"\.(pdf|md|txt|docx?|xlsx?|pptx?|html?)$", "", name, flags=re.I)


def section_label(row: dict) -> str:
    path = row.get("path")
    if isinstance(path, str):
        try:
            path = json.loads(path)
        except ValueError:
            path = [path]
    path = [p for p in (path or []) if p]
    if path:
        return " > ".join(path)
    return row.get("title") or row.get("number") or ""


def clean_summary(text: str) -> str:
    """LLM の答えを要点にする。前置き「要点:」・改行を落とし、断り・前置き・箇条書きの文を除いて先頭の 2 文を残す。要点が残らなければ空。"""
    text = re.sub(r"\s+", " ", strip_thinking(text or "")).strip()
    text = re.sub(r"^(要点|要約)\s*[:：]\s*", "", text)
    sentences = [x.strip() for x in re.split(r"(?<=。)", text) if x.strip()]
    kept = [x for x in sentences if not META_SENTENCE.search(x) and "**" not in x and not re.match(r"^[*・\-]\s", x)]
    return "".join(kept[:2])[:SUMMARY_MAX_CHARS]


def usable_summary(text: str) -> bool:
    """保存済みの要点をそのまま使えるか（断りや前置きを含む要点は作り直す）。"""
    return bool(text) and clean_summary(text) == text


def has_body(row: dict, body: str) -> bool:
    """本文に、見出しの題と見出し記号と空白を除いて MIN_BODY_CHARS 字以上の中身があるか。"""
    text = re.sub(r"[#\s]", "", body or "")
    title = re.sub(r"\s", "", section_label(row).split(" > ")[-1])
    if len(title) >= 2:
        text = text.replace(title, "", 1)
    # 題は「2 ガス流路部切断検査要領」、本文は「2.ガス流路部切断検査要領」のように番号の書き方が違うことがある。
    # 番号を除いた題（4 字以上）が本文の先頭（番号の後）にあれば、それも見出しの行として除く
    bare = re.sub(r"^[0-9０-９\-－.．()（）]+", "", title)
    if len(bare) >= 4:
        text = re.sub(r"^[0-9０-９\-－.．()（）]*" + re.escape(bare), "", text, count=1)
    return len(text) >= MIN_BODY_CHARS


def summary_input(row: dict, body: str, children: List[tuple]) -> str:
    """要点の LLM への入力（X16 の形 [文書名 見出しの経路]\\n本文 に、子の要点を足す）。"""
    text = f"[{_stem(row.get('file_name'))} {section_label(row)}]\n{(body or '')[:BODY_CHARS]}".rstrip()
    if children:
        lines, used = [], 0
        for label, summary in children:
            line = f"- {label}: {summary}"
            if used + len(line) > CHILD_SUMMARY_CHARS:
                break
            lines.append(line)
            used += len(line) + 1
        if lines:
            text += "\n\n### 下位の節の要点\n" + "\n".join(lines) + "\n\n" + CHAPTER_NOTE
    return text


def summary_src(user_text: str) -> str:
    return hashlib.sha1((SUMMARY_SYSTEM + "\n" + user_text).encode("utf-8")).hexdigest()


def _default_run(graph):
    from src.shared.common_fn import execute_graph_query

    def run(query, params=None):
        return execute_graph_query(graph, query, params=params or {})
    return run


def _default_summarize(model_key: str, timeout: float):
    def summarize(user_text: str):
        messages = [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": user_text}]
        return chat(messages, model_key=model_key, max_tokens=SUMMARY_MAX_TOKENS, timeout=timeout)
    return summarize


def _failure_kind(exc) -> tuple:
    """LLM の要求の失敗の分け方。返り値 (打ち切りの回数に数えるか, 送り直すか)。"""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True, True
    if isinstance(exc, requests.HTTPError):
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if code in (401, 403, 404):
            return True, False      # 接続先・モデル名・鍵の誤り。送り直しても同じ答えになる
        if code is None or code == 429 or code >= 500:
            return True, True       # 接続先の不調・混雑
        return False, False         # 400・413・422 など、その節の入力による失敗。送り直しても同じ答えになる
    return False, True


def _lease_holder(run):
    """グラフの貸し出しの今の持ち主。返り値 (読めたか, 持ち主の名前か None)。"""
    try:
        rows = run(LEASE_HOLDER_QUERY) or []
    except Exception as exc:
        logging.warning("section summaries: reading the graph lease failed (%s)", exc)
        return False, None
    return True, ((rows[0].get("owner") or "unknown") if rows else None)


def _wait_for_section_layer(run, max_wait: float) -> float:
    """章・節の層の作り直し（貸し出し中）の途中で節を読まないよう、層の貸し出しが返るまで待つ。待った秒数を返す。"""
    started = time.time()
    logged = False
    while True:
        ok, holder = _lease_holder(run)
        if not (ok and holder and str(holder).startswith("section_layer")):
            return round(time.time() - started, 1)
        if time.time() - started >= max_wait:
            logging.warning("section summaries: the section layer still holds the graph lease after %.0f s; reading the sections anyway", max_wait)
            return round(time.time() - started, 1)
        if not logged:
            logging.info("section summaries: waiting for the section layer (%s) to finish", holder)
            logged = True
        time.sleep(2.0)


@contextmanager
def _build_lock(max_wait: float):
    """同じコンテナの中で、要点の作成を同時に 1 本に限る（gunicorn の別のワーカーの作成も待つ）。待った秒数を渡す。"""
    try:
        f = open(_BUILD_LOCK_PATH, "a+")
    except OSError as exc:      # 鍵のファイルを開けなくても作成は続ける（同時に 2 本流れうるだけ）
        logging.warning("section summaries: opening the lock file failed (%s); running without the lock", exc)
        yield 0.0
        return
    started = time.time()
    logged = False
    try:
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() - started >= max_wait:
                    raise RuntimeError(f"another build_section_summaries is still running in this container after {max_wait:.0f} s")
                if not logged:
                    logging.info("section summaries: waiting for another build_section_summaries in this container")
                    logged = True
                time.sleep(1.0)
        yield round(time.time() - started, 1)
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


def build_section_summaries(graph=None, *, run: Optional[Callable] = None, summarize: Optional[Callable] = None,
                            file_names: Optional[List[str]] = None, concurrency: Optional[int] = None,
                            retries: int = 2, backoff: float = 3.0, restore_only: bool = False) -> dict:
    """後処理 build_section_summaries。返り値 {"summary": {件数と時間}}。

    run(query, params) -> 行の一覧、summarize(入力) -> (要点, モデル名) は試験で差し替える。
    file_names を渡すとその文書の節だけを対象にする（控えの掃除はしない）。
    restore_only = True: LLM を呼ばず、入力が同じ節の要点を控えから書き戻すだけ（控えが 1 つも無い DB では何もしない）。
                         索引の作成・控えの掃除・同時に 1 本の鍵は使わない。LLM が要る節の数は pending に入る。
    complete: 打ち切らず、LLM に送った節の失敗が FAIL_RATIO 以下なら True（後処理は False のとき Failed を返す）。
    """
    if restore_only:
        return _build_section_summaries(graph, run, summarize, file_names, concurrency, retries, backoff, True, 0.0)
    with _build_lock(BUILD_LOCK_WAIT) as waited:
        return _build_section_summaries(graph, run, summarize, file_names, concurrency, retries, backoff, False, waited)


def _build_section_summaries(graph, run, summarize, file_names, concurrency, retries, backoff, restore_only, lock_wait) -> dict:
    t0 = time.time()
    run = run or _default_run(graph)
    model_key = summary_model_key()
    try:
        workers = int(concurrency or os.environ.get("SECTION_SUMMARY_CONCURRENCY", "3"))
    except ValueError:
        workers = MAX_CONCURRENCY
    workers = max(1, min(workers, MAX_CONCURRENCY))
    files = list(file_names) if file_names else None
    stats = {"mode": "restore_only" if restore_only else "build", "sections": 0, "made": 0, "kept": 0, "restored": 0, "failed": 0,
             "pending": 0, "empty": 0, "made_with_children": 0, "cleared": 0, "rejected": 0, "aborted": False, "abort_reason": None,
             "complete": True, "model": None, "concurrency": workers, "seconds": 0.0, "llm_seconds": 0.0, "pruned": 0,
             "prune_skipped": None, "lock_wait_seconds": lock_wait, "layer_wait_seconds": 0.0}

    def finish():
        stats["seconds"] = round(time.time() - t0, 1)
        stats["llm_seconds"] = round(stats["llm_seconds"], 1)
        logging.info("section summaries: %s", json.dumps(stats, ensure_ascii=False))
        return {"summary": stats}

    if restore_only:
        if not run(CACHE_ANY_QUERY):
            return finish()        # 要点を作ったことの無い DB では何もしない（既定の後処理に余計な照会を足さない）
    else:
        if summarize is None:
            # 接続先の設定の誤りは、節ごとに送り直して空回りする前に分かる。LLM には送らず、控えからの書き戻しだけをして Failed にする
            try:
                llm_config(model_key)
            except RuntimeError as exc:
                stats["aborted"] = True
                stats["abort_reason"] = str(exc)
                logging.error("section summaries: %s; only restoring summaries from the cache", exc)
        try:
            run(CACHE_INDEX_QUERY)
        except Exception as exc:   # 索引が無くても動く（遅くなるだけ）
            logging.warning("section summaries: creating the cache index failed (%s)", exc)
    summarize = summarize or _default_summarize(model_key, _float_env("SECTION_SUMMARY_TIMEOUT", 120.0))
    stats["layer_wait_seconds"] = _wait_for_section_layer(run, SECTION_LAYER_WAIT)

    rows = {r["id"]: dict(r) for r in run(SECTIONS_QUERY, {"files": files})}
    stats["sections"] = len(rows)
    bodies: Dict[str, List[str]] = {}
    for r in run(PRIMARY_BODY_QUERY, {"files": files}):
        bodies.setdefault(r["sid"], []).append(r["text"] or "")
    children: Dict[str, List[str]] = {}
    for sid, r in rows.items():
        if r.get("parent") in rows:
            children.setdefault(r["parent"], []).append(sid)
    # 子を先に作る: 木の深さ（level ではなく親をたどった段数）の深い順
    depth = {}

    def depth_of(sid, guard=0):
        if sid not in depth:
            parent = rows[sid].get("parent")
            depth[sid] = 0 if (parent not in rows or guard > 20) else depth_of(parent, guard + 1) + 1
        return depth[sid]

    by_depth: Dict[int, List[str]] = {}
    for sid in rows:
        by_depth.setdefault(depth_of(sid), []).append(sid)
    position = {}
    for sid, r in rows.items():
        position[sid] = (r.get("file_name") or "", section_label(r))

    consecutive = [0]
    answered = [0]                 # LLM が答えた節の数（要点・断り）。失敗の割合の分母に使う
    keep = set()                   # この回に読んだ節の要点の入力（控えの掃除で残す）
    lock = threading.Lock()

    def one(item):
        sid, user_text = item
        started = time.time()
        last = None
        for attempt in range(retries + 1):
            if stats["aborted"]:
                return sid, None, None, "aborted"
            try:
                text, model_name = summarize(user_text)
                summary = clean_summary(text)
                with lock:
                    consecutive[0] = 0
                    stats["llm_seconds"] += time.time() - started
                # 断り・前置きだけの答えは要点にしない（送り直しても同じ答えになるので、そのまま飛ばす）
                return sid, summary or None, model_name, None if summary else ("rejected" if (text or "").strip() else "empty answer")
            except Exception as exc:
                last = exc
                count, retry = _failure_kind(exc)
                if count:
                    with lock:
                        consecutive[0] += 1
                        if consecutive[0] >= ABORT_AFTER and not stats["aborted"]:
                            stats["aborted"] = True
                            stats["abort_reason"] = f"the LLM failed {ABORT_AFTER} times in a row (last: {str(exc)[:200]})"
                if not retry:
                    break
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
        with lock:
            stats["llm_seconds"] += time.time() - started
        return sid, None, None, str(last)

    def flush(pending):
        if pending:
            run(WRITE_QUERY, {"rows": pending})
            pending.clear()

    for level in sorted(by_depth, reverse=True):
        todo, pending, cleared, rejects = [], [], [], []
        sids = sorted(by_depth[level], key=lambda s: position[s])
        srcs = {}
        for sid in sids:
            r = rows[sid]
            # 主所属のチャンクが無ければ、章・節の層が切り出した節の本文（Section.text）
            body = "\n".join(bodies.get(sid, [])) or (r.get("text") or "")
            if not has_body(r, body):
                body = ""
            kids = sorted(children.get(sid, []), key=lambda s: position[s])
            use_kids = bool(kids) and ((r.get("level") or 0) <= 1 or not body)
            kid_rows = [(section_label(rows[k]).split(" > ")[-1], rows[k].get("summary")) for k in kids
                        if usable_summary(rows[k].get("summary"))] if use_kids else []
            if not body and not kid_rows:
                stats["empty"] += 1
                if r.get("summary"):
                    cleared.append(sid)
                    r["summary"] = None
                continue
            user_text = summary_input(r, body, kid_rows)
            src = summary_src(user_text)
            srcs[sid] = (src, user_text, bool(kid_rows))
            keep.add(src)
        # 入力が同じで要点がある節は作り直さない。消された節は控えから書き戻す
        # summary_src だけがあり要点が無い節は、前回 LLM が要点を返さなかった節（入力が同じなら聞き直さない）
        cached, rejects = {}, []
        missing = [src for sid, (src, _, _) in srcs.items() if rows[sid].get("summary_src") != src]
        for i in range(0, len(missing), 500):
            for c in run(CACHE_LOOKUP_QUERY, {"srcs": missing[i:i + 500]}):
                if usable_summary(c.get("summary")) or c.get("rejected"):
                    cached[c["src"]] = c
        for sid, (src, user_text, chapter) in srcs.items():
            r = rows[sid]
            if r.get("summary_src") == src and usable_summary(r.get("summary")):
                stats["kept"] += 1
                continue
            if r.get("summary_src") == src and not r.get("summary"):
                stats["rejected"] += 1
                continue
            if src in cached and cached[src].get("rejected"):
                c = cached[src]
                rejects.append({"id": sid, "src": src, "model": c.get("model"), "at": c.get("at"), "fileName": r.get("file_name")})
                stats["rejected"] += 1
                continue
            if src in cached:
                c = cached[src]
                r.update(summary=c["summary"], summary_src=src)
                pending.append({"id": sid, "summary": c["summary"], "model": c.get("model"), "at": c.get("at"), "src": src,
                                "fileName": r.get("file_name")})
                stats["restored"] += 1
                if len(pending) >= WRITE_BATCH:
                    flush(pending)
                continue
            todo.append((sid, user_text))
        flush(pending)
        if rejects:
            run(REJECT_QUERY, {"rows": rejects})
            rejects = []
        if cleared:
            run(CLEAR_QUERY, {"ids": cleared})
            stats["cleared"] += len(cleared)
            cleared = []
        if not todo:
            continue
        if restore_only or stats["aborted"]:
            # 書き戻しだけの実行と、打ち切りの後は LLM に送らない。浅い段（章）の控えからの書き戻しは続ける
            stats["pending"] += len(todo)
            continue
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for sid, summary, model_name, error in pool.map(one, todo):
                if error == "aborted":
                    stats["pending"] += 1
                    continue
                if summary is None:
                    stats["rejected" if error == "rejected" else "failed"] += 1
                    if error == "rejected":
                        answered[0] += 1
                        # 前の要点（断りの文など）を消し、入力の指紋と控えの印を残す
                        rejects.append({"id": sid, "src": srcs[sid][0], "model": model_name,
                                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fileName": rows[sid].get("file_name")})
                        rows[sid].update(summary=None, summary_src=srcs[sid][0])
                    logging.warning("section summaries: %s %s failed (%s); skipped", rows[sid].get("file_name"),
                                    section_label(rows[sid])[:60], error)
                    continue
                answered[0] += 1
                src, _, chapter = srcs[sid]
                at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                rows[sid].update(summary=summary, summary_src=src)
                stats["model"] = model_name
                stats["made"] += 1
                stats["made_with_children"] += int(chapter)
                pending.append({"id": sid, "summary": summary, "model": model_name, "at": at, "src": src,
                                "fileName": rows[sid].get("file_name")})
                if len(pending) >= WRITE_BATCH:
                    flush(pending)
        flush(pending)
        if rejects:
            run(REJECT_QUERY, {"rows": rejects})
    if stats["aborted"]:
        logging.error("section summaries: stopped sending to the LLM (%s); %d sections are left without a new summary",
                      stats["abort_reason"], stats["pending"])
    if files is None and not restore_only and not stats["aborted"]:
        # グラフの貸し出しが出ている間（章・節の層の作り直しの途中など）は、節の summary_src が揃っていないので掃除しない
        ok, holder = _lease_holder(run)
        if not ok or holder:
            stats["prune_skipped"] = f"graph lease held by {holder}" if holder else "the graph lease could not be read"
        else:
            try:
                out = run(CACHE_PRUNE_QUERY, {"keep": sorted(keep)})
                stats["pruned"] = (out[0].get("removed") if out else 0) or 0
            except Exception as exc:
                logging.warning("section summaries: pruning the cache failed (%s)", exc)
    stats["complete"] = (not stats["aborted"]) and stats["failed"] <= FAIL_RATIO * (answered[0] + stats["failed"])
    return finish()


# ============================================================
# 一覧選択（参照追跡の reference_doc_select = summary_list）
# ============================================================
def section_item(row: dict) -> dict:
    """一覧選択の候補の形にする。row: id, file_name, path, title, number, level, parent, summary, head"""
    return {"id": row["id"], "file_name": row.get("file_name"), "label": section_label(row), "level": row.get("level") or 0,
            "parent": row.get("parent"), "summary": (row.get("summary") or "").strip(),
            "head": re.sub(r"\s+", " ", row.get("head") or "").strip()}


def _item_text(item: dict) -> str:
    """リランカーに渡す節の文: 見出しの経路＋要点（無ければ本文の先頭）"""
    return f"{item['label']} {item['summary'] or item['head'] or item.get('children_text', '')}".strip() or "-"


def _item_line(n: int, item: dict) -> str:
    gist = item["summary"] or (item["head"] or item.get("children_text", ""))[:LIST_HEAD_CHARS]
    return f"{n}. [{_stem(item['file_name'])} {item['label']}] {gist}".rstrip()


def list_messages(question: str, source_context: str, already: List[str], items: List[dict], k: int, what: str = "節") -> List[dict]:
    """X16 の一覧の指示（質問・既に手元にある節・候補）に、参照元の節を足したもの。"""
    lines = [_item_line(i + 1, item) for i, item in enumerate(items)]
    user = f"### 質問\n{question}\n\n"
    if source_context:
        user += f"### 参照元の節（この節が参照している文書の中から選ぶ）\n{source_context}\n\n"
    user += "### 既に手元にある節\n" + ("\n".join(already) if already else "（なし）") + "\n\n"
    user += "### 候補\n" + "\n".join(lines) + "\n\n"
    if what == "章":
        user += f"質問に答えるのに役立つ節を含む章を、役立つ順に最大 {k} 件、番号を JSON の配列で返せ。"
    else:
        user += f"質問に答えるのに役立つ順に、最大 {k} 件の番号を JSON の配列で返せ。"
    return [{"role": "system", "content": LIST_SYSTEM}, {"role": "user", "content": user}]


def parse_picks(raw: str, n: int, k: int) -> List[int]:
    """LLM の答えから番号（1 始まり）を読み、0 始まりの添字にする。JSON の配列を優先し、読めなければ数字を拾う（X16 と同じ）。"""
    raw = raw or ""
    numbers = None
    m = re.search(r"\[[^\[\]]*\]", raw)
    if m:
        try:
            value = json.loads(m.group(0))
            numbers = [int(x) for x in value if isinstance(x, (int, float, str)) and str(x).strip().lstrip("-").isdigit()]
        except (ValueError, TypeError):
            numbers = None
    if numbers is None:
        numbers = [int(x) for x in re.findall(r"\d+", raw)]
    out = []
    for x in numbers:
        if 1 <= x <= n and (x - 1) not in out:
            out.append(x - 1)
    return out[:k]


def _pick(question, rerank_query, source_context, already, items, *, k, top_k, rerank, ask, what="節"):
    """リランカーで上位 top_k 件 → LLM に最大 k 件選ばせる。返り値 (選んだ候補, 生の答え, 一覧の候補の数)。"""
    if not items:
        raise SelectionFailed("no_candidates")
    order = list(range(len(items)))
    try:
        scores = rerank(rerank_query, [_item_text(x) for x in items])
        order.sort(key=lambda i: -scores[i])
    except Exception as exc:   # リランカーが使えなくても、文書の順のまま LLM に選ばせる
        logging.warning("summary list: rerank failed (%s); listing in document order", exc)
    shown = [items[i] for i in order[:top_k]]
    try:
        raw = ask(list_messages(question, source_context, already, shown, k, what))
    except Exception as exc:
        raise SelectionFailed("llm_failed", str(exc)[:200])
    picks = parse_picks(raw, len(shown), k)
    if not picks:
        raise SelectionFailed("out_of_range" if re.search(r"\d", raw or "") else "empty", (raw or "")[:200])
    return [shown[i] for i in picks], (raw or "")[:200], len(shown)


def _root_of(item, by_id):
    seen = set()
    while item.get("parent") in by_id and item["id"] not in seen:
        seen.add(item["id"])
        item = by_id[item["parent"]]
    return item


def select_by_list(question: str, rerank_query: str, source_context: str, already: List[str], sections: List[dict], *,
                   rerank: Callable, ask: Callable, max_picks: int = 4, top_k: int = LIST_TOP_K, two_stage_min: int = 30) -> dict:
    """要点の一覧から LLM に節を選ばせる。sections は section_item の形。

    節の数が two_stage_min を超え、章（親の無い節）が 2 つ以上ある文書は、章を先に選ぶ（2 段）。
    返り値 {"picked": [候補], "raw": [生の答え], "stage": 1|2, "chapters": [章の id], "candidates": 一覧の候補の数}。
    失敗は SelectionFailed（reason と生の答えを持つ）。
    """
    if not sections:
        raise SelectionFailed("no_sections")
    by_doc: Dict[str, List[dict]] = {}
    for s in sections:
        by_doc.setdefault(s.get("file_name") or "", []).append(s)
    by_id = {s["id"]: s for s in sections}
    raws, chapter_ids, stage = [], [], 1
    pool, large = [], []
    for doc, items in by_doc.items():
        roots = [s for s in items if s.get("parent") not in by_id]
        if len(items) > two_stage_min and len(roots) >= 2:
            large.append((doc, items, roots))
        else:
            pool.extend(items)
    if large:
        stage = 2
        chapters = []
        for _doc, items, roots in large:
            kids: Dict[str, List[str]] = {}
            for s in items:
                if s.get("parent") in by_id:
                    kids.setdefault(s["parent"], []).append(s["label"].split(" > ")[-1])
            for r in roots:
                chapters.append(dict(r, children_text=("子の節: " + "、".join(kids.get(r["id"], [])))[:300] if kids.get(r["id"]) else ""))
        picked_chapters, raw, _ = _pick(question, rerank_query, source_context, already, chapters, k=max_picks, top_k=top_k,
                                        rerank=rerank, ask=ask, what="章")
        raws.append(raw)
        chapter_ids = [c["id"] for c in picked_chapters]
        chosen = set(chapter_ids)
        for _doc, items, _roots in large:
            pool.extend(s for s in items if _root_of(s, by_id)["id"] in chosen)
    picked, raw, shown = _pick(question, rerank_query, source_context, already, pool, k=max_picks, top_k=top_k,
                               rerank=rerank, ask=ask)
    raws.append(raw)
    return {"picked": picked, "raw": raws, "stage": stage, "chapters": chapter_ids, "candidates": shown}
