"""抽出方式（標準の LLM 抽出か EDC か）を、LLM の選択から切り離す。

/extract の extraction_method は standard（既定）か edc。旧来の model=edc も edc とみなす。
EDC の LLM は EDC 側の設定（EDC の .env の VLLM_MODEL）で固定で、要求ごとには替えられない。
EDC で抽出した文書は Document.extraction_method=edc とし、Document.model にはその LLM の名前を残す。

EDC の /health の llm は到達の結果（ok / down: ... / http 503）で、モデル名は返さない。名前は次の順で決める。
  1. /health の応答のモデル名の項目（llm_model か model）。EDC が返すようになったとき用
  2. /health の llm が到達の結果でなく名前に見えるとき、その値
  3. 環境変数 EDC_LLM_MODEL（EDC の VLLM_MODEL と同じ値を backend/.env に書く）
  4. どれも無ければ edc-default
名前が LLM_MODEL_CONFIG_<名前> のモデル名（カンマ区切りの先頭）と同じなら、画面の呼び名（例 gemma4）にそろえる。

GET /extraction_methods の EDC の項目には、backend の現在の EDC 設定（settings）と、文書種別ごとの語彙の件数を付与する。
  settings … LLM_MODEL_CONFIG_EDC の 2 番目の値（文書種別。auto・none・文書種別の名称）、EDC_ENRICH_SCHEMA（スキーマ運用モード）、
             EDC_FOLD_UNKNOWN_RELATIONS（語彙にない関係の扱い）、EDC_DROP_SENTENCE_NAMES（文の形の名称の除外）。
             判定は edc_extraction と同じである。LLM_MODEL_CONFIG_EDC の URL と資格情報は返さない
  語彙の件数 … EDC_SCHEMAS_DIR（既定 /edc_schemas）の registry.json と語彙 CSV から集計する。文書種別の語彙は、共通の語彙（backbone）と
             文書種別ごとの CSV の和集合である（EDC の doctype_router.compose_target と同じ）。registry.json の types（*_types.csv）の
             1 列目をノードラベル、schema（*_schema.csv）の 1 列目をリレーションタイプとして数え、2 列に満たない行は数えない
             （EDC の _read_kv_csv と同じ）。CSV はファイル名のみを使用し、EDC_SCHEMAS_DIR の直下から読み込む
EDC に接続できない場合も、settings は環境変数から、件数と文書種別の一覧は定義ファイルから返す。読み込めない値は null とし、例外は送出しない。
"""
import asyncio
import csv
import json
import logging
import os

import httpx

from src.api_response import create_api_response
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException

STANDARD = "standard"
EDC = "edc"
EDC_UNKNOWN_LLM = "edc-default"
LABELS = {STANDARD: "標準", EDC: "EDC（スキーマ統制）"}
# 画面の一覧用の待ち時間。EDC の /health は LLM と埋め込みへの到達をそれぞれ最大 3 秒で確認する
STATUS_TIMEOUT_SECONDS = 5.0
# /extract で LLM の名前のみを照会する場合の待ち時間。EDC に接続できない場合も抽出は中止しない
LABEL_TIMEOUT_SECONDS = 3.0
DEFAULT_SCHEMAS_DIR = "/edc_schemas"
# 文書種別の設定値（LLM_MODEL_CONFIG_EDC の 2 番目の値）の区分。auto は文書ごとに判定、none は判定なし、それ以外は固定の文書種別である
DOCTYPE_AUTO = "auto"
DOCTYPE_NONE = "none"
DOCTYPE_FIXED = "fixed"
# スキーマ運用モード（EDC_ENRICH_SCHEMA）。false は固定スキーマ、true は固定＋拡張である
SCHEMA_MODE_FIXED = "fixed"
SCHEMA_MODE_FIXED_EXTENDED = "fixed_extended"
# 語彙にない関係の扱い（EDC_FOLD_UNKNOWN_RELATIONS）。true は RELATED_TO に集約、false は語彙にない名称のまま追加する
UNKNOWN_RELATIONS_FOLD = "fold"
UNKNOWN_RELATIONS_KEEP = "keep"
_TRUE_VALUES = ("1", "true", "yes", "on")


def normalize_extraction_method(model, extraction_method):
    """standard か edc にそろえる。model=edc（旧来の指定）は edc。それ以外の値は誤り。"""
    if str(model or "").strip().lower() == EDC:
        return EDC
    method = str(extraction_method or "").strip().lower()
    if method in ("", STANDARD):
        return STANDARD
    if method == EDC:
        return EDC
    raise LLMGraphBuilderException(f"抽出方式 '{extraction_method}' は使用できません（standard または edc を指定してください）")


def _edc_base_url():
    """LLM_MODEL_CONFIG_EDC の先頭（EDC API の URL）。未設定なら None。書式は edc_extraction._get_edc_config と同じ。"""
    base = (os.getenv("LLM_MODEL_CONFIG_EDC") or "").split(",")[0].strip().rstrip("/")
    return base or None


async def fetch_edc_status(include_doctypes=False, timeout=STATUS_TIMEOUT_SECONDS, transport=None):
    """EDC の /health（と /doctypes）を読む。例外は投げず dict で返す。transport は試験用。"""
    status = {"configured": False, "reachable": False, "health_code": None, "health": {}, "doctypes": [], "error": None,
              "error_detail": None}
    base = _edc_base_url()
    if not base:
        status["error"] = "LLM_MODEL_CONFIG_EDC が未設定"
        return status
    status["configured"] = True
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 2.0)), transport=transport) as client:
        calls = [client.get(f"{base}/health")]
        if include_doctypes:
            calls.append(client.get(f"{base}/doctypes"))
        results = await asyncio.gather(*calls, return_exceptions=True)
    health = results[0]
    if isinstance(health, BaseException):
        # 画面に表示する理由には例外の名前を含めず、error_detail（reason_detail）で返す
        status["error"] = "EDC に接続できない"
        status["error_detail"] = type(health).__name__
        return status
    status["reachable"] = True
    status["health_code"] = health.status_code
    try:
        body = health.json()
        status["health"] = body if isinstance(body, dict) else {}
    except ValueError:
        pass
    if include_doctypes and not isinstance(results[1], BaseException) and results[1].status_code == 200:
        try:
            status["doctypes"] = [{"name": d["name"], "description": d.get("description", "")}
                                  for d in results[1].json() if isinstance(d, dict) and d.get("name")]
        except (ValueError, TypeError):
            pass
    return status


def _is_probe_result(value):
    """/health の llm が到達の結果（ok / down: ... / http 503）かどうか。"""
    v = value.strip().lower()
    return v == "ok" or v.startswith("down") or v.startswith("http ")


def _gb_model_name(llm_id):
    """LLM_MODEL_CONFIG_<名前> のモデル名が llm_id と同じなら <名前> を小文字で返す（GEMMA4 → gemma4）。"""
    want = llm_id.strip().lower()
    for key in sorted(os.environ):
        if not key.startswith("LLM_MODEL_CONFIG_") or key == "LLM_MODEL_CONFIG_EDC":
            continue
        if (os.environ.get(key) or "").split(",")[0].strip().lower() == want:
            return key[len("LLM_MODEL_CONFIG_"):].lower()
    return None


def resolve_edc_llm(health=None):
    """EDC の LLM の (Document.model に残す名前, EDC 側のモデル名) を返す。分からなければ (None, None)。"""
    health = health if isinstance(health, dict) else {}
    llm_id = next((health[k].strip() for k in ("llm_model", "model")
                   if isinstance(health.get(k), str) and health[k].strip()), None)
    probe = health.get("llm")
    if not llm_id and isinstance(probe, str) and probe.strip() and not _is_probe_result(probe):
        llm_id = probe.strip()
    if not llm_id:
        llm_id = (os.getenv("EDC_LLM_MODEL") or "").strip() or None
    if not llm_id:
        return None, None
    return (_gb_model_name(llm_id) or llm_id), llm_id


# EDC の /health が返す依存サービスの項目と、画面に表示する名称
DEPENDENCY_LABELS = {"llm": "LLM", "embedding": "埋め込みモデル"}


def _down_dependencies(status):
    """EDC の /health で ok 以外を返した依存サービスの項目（llm・embedding）。"""
    h = status.get("health") or {}
    return [k for k in DEPENDENCY_LABELS if isinstance(h.get(k), str) and h[k] != "ok"]


def _unavailable_reason(status):
    """EDC を現在使用できない理由（画面に表示する文）。使用できる場合は None。
    依存サービスの状態の原文（例: down: <urlopen error ...>）は含めず、_unavailable_detail が返す。"""
    if not status["configured"] or not status["reachable"]:
        return status["error"] or "EDC に接続できない"
    if status["health_code"] != 200:
        down = _down_dependencies(status)
        return (f"EDC の依存サービス（{'、'.join(DEPENDENCY_LABELS[k] for k in down)}）が停止している" if down
                else f"EDC の /health の応答が HTTP {status['health_code']}")
    return None


def _unavailable_detail(status):
    """使用できない理由の詳細（EDC に接続できない場合は例外の名前、依存サービスの停止の場合は /health が返した状態の原文）。
    画面ではポインタを合わせると表示する。ない場合は None。"""
    if not status.get("reachable"):
        return status.get("error_detail")
    h = status.get("health") or {}
    return "、".join(f"{k}: {h[k]}" for k in _down_dependencies(status)) or None


def _flag(name):
    """edc_extraction の _flag_enabled と同じ判定（未設定と空は false）。"""
    return str(os.getenv(name) or "false").strip().lower() in _TRUE_VALUES


def edc_settings():
    """backend の現在の EDC 設定（画面に参照のみで表示する値）。URL と資格情報は含めない。判定できない値は None とし、例外は送出しない。"""
    settings = {"doctype": None, "doctype_mode": None, "enrich_schema": None, "schema_mode": None,
                "fold_unknown_relations": None, "unknown_relations": None, "drop_sentence_names": None}
    try:
        # 文書種別は edc_extraction._get_edc_config と同じ規則で決定する（空は auto、大文字小文字を問わず none は判定なし）
        parts = [p.strip() for p in (os.getenv("LLM_MODEL_CONFIG_EDC") or "").split(",")]
        if parts[0]:
            doctype = parts[1] if len(parts) > 1 and parts[1] else DOCTYPE_AUTO
            settings["doctype"] = doctype
            if doctype.lower() == DOCTYPE_NONE:
                settings["doctype_mode"] = DOCTYPE_NONE
            elif doctype == DOCTYPE_AUTO:
                settings["doctype_mode"] = DOCTYPE_AUTO
            else:
                settings["doctype_mode"] = DOCTYPE_FIXED
        enrich = _flag("EDC_ENRICH_SCHEMA")
        fold = _flag("EDC_FOLD_UNKNOWN_RELATIONS")
        settings.update(enrich_schema=enrich, schema_mode=SCHEMA_MODE_FIXED_EXTENDED if enrich else SCHEMA_MODE_FIXED,
                        fold_unknown_relations=fold,
                        unknown_relations=UNKNOWN_RELATIONS_FOLD if fold else UNKNOWN_RELATIONS_KEEP,
                        drop_sentence_names=_flag("EDC_DROP_SENTENCE_NAMES"))
    except Exception as e:
        logging.warning(f"extraction_methods: EDC の設定を判定できない（{type(e).__name__}: {e}）")
    return settings


def _vocabulary_names(schemas_dir, path):
    """語彙 CSV の 1 列目の名称の集合。registry.json のパスはファイル名のみを使用し、schemas_dir の直下を読む。"""
    with open(os.path.join(schemas_dir, os.path.basename(str(path))), encoding="utf-8") as f:
        return {row[0].strip() for row in csv.reader(f) if len(row) >= 2 and row[0].strip()}


def edc_vocabulary_summary():
    """registry.json と語彙 CSV から、文書種別ごとのノードラベル数とリレーションタイプ数（共通の語彙との和集合）を集計する。
    共通の語彙を読み込めない場合は available=False とし、文書種別ごとの CSV を読み込めない場合はその件数のみを None とする。例外は送出しない。"""
    schemas_dir = os.getenv("EDC_SCHEMAS_DIR") or DEFAULT_SCHEMAS_DIR
    summary = {"available": False, "error": None, "backbone_node_label_count": None,
               "backbone_relationship_type_count": None, "doctypes": []}
    try:
        with open(os.path.join(schemas_dir, "registry.json"), encoding="utf-8") as f:
            registry = json.load(f)
        backbone_labels = _vocabulary_names(schemas_dir, registry["backbone"]["types"])
        backbone_types = _vocabulary_names(schemas_dir, registry["backbone"]["schema"])
        entries = registry.get("doctypes") or []
    except Exception as e:
        summary["error"] = f"EDC の定義ファイルを読み込めない（{type(e).__name__}）"
        logging.warning(f"extraction_methods: EDC の定義ファイルを読み込めない（{schemas_dir}、{type(e).__name__}: {e}）")
        return summary
    summary.update(available=True, backbone_node_label_count=len(backbone_labels),
                   backbone_relationship_type_count=len(backbone_types))
    for entry in entries if isinstance(entries, list) else []:
        name = str(entry.get("name") or "").strip() if isinstance(entry, dict) else ""
        if not name:
            continue
        try:
            node_label_count = len(backbone_labels | _vocabulary_names(schemas_dir, entry["types"]))
            relationship_type_count = len(backbone_types | _vocabulary_names(schemas_dir, entry["schema"]))
        except Exception as e:
            node_label_count = relationship_type_count = None
            logging.warning(f"extraction_methods: 文書種別 '{name}' の語彙を読み込めない（{type(e).__name__}: {e}）")
        summary["doctypes"].append({"name": name, "description": str(entry.get("description") or ""),
                                    "node_label_count": node_label_count,
                                    "relationship_type_count": relationship_type_count})
    return summary


def _doctypes_with_counts(edc_doctypes, summary):
    """EDC の /doctypes の一覧に語彙の件数を付与する。EDC から一覧を取得できない場合は定義ファイルの一覧を返す。
    (一覧, 出所) を返す。出所は edc・registry・None のいずれかである。"""
    counted = {d["name"]: d for d in summary["doctypes"]}
    if edc_doctypes:
        rows = []
        for d in edc_doctypes:
            c = counted.get(d["name"], {})
            rows.append({"name": d["name"], "description": d.get("description", ""),
                         "node_label_count": c.get("node_label_count"),
                         "relationship_type_count": c.get("relationship_type_count")})
        return rows, "edc"
    if summary["doctypes"]:
        return [dict(d) for d in summary["doctypes"]], "registry"
    return [], None


SCHEMA_MODES = ("fixed", "fixed_extended")


def edc_options_of(params):
    """取り込みの引数から、EDC の初期スキーマの指定を取り出す。EDC 以外の方式では None。
    文書種別は auto / none / 文書種別の名前（空白を除く）、運用モードは fixed / fixed_extended。それ以外の値は無視して backend の設定に従う（警告を出す）。"""
    if getattr(params, "extraction_method", None) != EDC:
        return None
    doctype = (getattr(params, "edc_doctype", None) or "").strip() or None
    mode = (getattr(params, "edc_schema_mode", None) or "").strip() or None
    if mode is not None and mode not in SCHEMA_MODES:
        logging.warning(f"edc_schema_mode={mode!r} is not one of {SCHEMA_MODES}; using the backend setting")
        mode = None
    if doctype is not None and ("," in doctype or "\n" in doctype or len(doctype) > 100):
        logging.warning(f"edc_doctype={doctype!r} is not a valid doctype name; using the backend setting")
        doctype = None
    if doctype is None and mode is None:
        return None
    return {"doctype": doctype, "schema_mode": mode}


def effective_edc_options(params):
    """文書ノードに記録する EDC の初期スキーマの指定（11/9）。取り込みごとの指定があればそれを、無ければ backend の設定
    （LLM_MODEL_CONFIG_EDC の文書種別、EDC_ENRICH_SCHEMA）を返す。EDC 以外の方式では (None, None)。"""
    if getattr(params, "extraction_method", None) != EDC:
        return None, None
    requested = edc_options_of(params) or {}
    doctype, mode = requested.get("doctype"), requested.get("schema_mode")
    if doctype is None or mode is None:
        try:
            from src.edc_extraction import _get_edc_config, _enrich_schema_enabled
            if doctype is None:
                doctype = _get_edc_config()[1] or "none"
            if mode is None:
                mode = "fixed_extended" if _enrich_schema_enabled(None) else "fixed"
        except Exception as e:  # 記録のための値なので、読めなくても取り込みは止めない
            logging.warning(f"could not resolve the EDC defaults for the document record: {e}")
    return doctype, mode


async def apply_extraction_method(params):
    """/extract の引数の抽出方式を決める（params を書き換える）。
    EDC のときは params.model を EDC 側の LLM の名前に置き換え、Document.model と応答の model に実際に使った LLM が残るようにする。
    EDC が止まっていてもここでは止めない。止めるのは edc_extraction の /health の確認で、失敗の扱い（チャンクが残り再処理できる）を今までと同じにする。"""
    params.extraction_method = normalize_extraction_method(params.model, getattr(params, "extraction_method", None))
    if params.extraction_method == EDC:
        status = await fetch_edc_status(timeout=LABEL_TIMEOUT_SECONDS)
        label, llm_id = resolve_edc_llm(status["health"])
        params.model = label or EDC_UNKNOWN_LLM
        logging.info(f"extraction_method=edc: Document.model に EDC の LLM '{params.model}'（{llm_id or '不明'}）を残す")
    return params


async def list_extraction_methods():
    """GET /extraction_methods: 画面の抽出方式の選択肢。標準は常に選択できる。EDC は /health が 200 の場合のみ選択できる。
    EDC の項目には、backend の現在の EDC 設定（settings）、文書種別ごとの語彙の件数（doctypes）、共通の語彙の件数（vocabulary）を付与する。"""
    try:
        status = await fetch_edc_status(include_doctypes=True)
        label, llm_id = resolve_edc_llm(status["health"])
        reason = _unavailable_reason(status)
        summary = edc_vocabulary_summary()
        doctypes, doctypes_source = _doctypes_with_counts(status["doctypes"], summary)
        data = [
            {"id": STANDARD, "label": LABELS[STANDARD], "available": True, "llm": None, "llm_id": None,
             "llm_status": None, "reason": None, "doctypes": []},
            {"id": EDC, "label": LABELS[EDC], "available": reason is None, "llm": label, "llm_id": llm_id,
             "llm_status": status["health"].get("llm"), "reason": reason,
             "reason_detail": _unavailable_detail(status) if reason else None, "doctypes": doctypes,
             "doctypes_source": doctypes_source, "settings": edc_settings(),
             "vocabulary": {key: summary[key] for key in ("available", "error", "backbone_node_label_count",
                                                          "backbone_relationship_type_count")}},
        ]
        return create_api_response("Success", data=data)
    except Exception as e:
        logging.exception(f"extraction_methods: {e}")
        return create_api_response("Failed", message="抽出方式の一覧を取得できませんでした", error=str(e))


def check_resume_method(previous, method):
    """「続きから」の再処理で前回と違う抽出方式を使わせない（1 つの文書に 2 つの方式の結果が混ざるため）。
    previous は get_current_status_document_node の 1 行。方式を記録する前の文書は、model=edc なら edc、ほかは standard とみなす。"""
    if not method:
        return
    previous = previous or {}
    before = previous.get("extraction_method") or (EDC if str(previous.get("model") or "").strip().lower() == EDC else STANDARD)
    if before != method:
        raise LLMGraphBuilderException(
            f"続きからの再処理は、前回と同じ抽出方式で実行してください。前回は「{LABELS.get(before, before)}」、"
            f"今回は「{LABELS.get(method, method)}」です。"
            "同じ方式で続ける場合は、抽出方式を前回と同じ方式に設定して再実行してください。"
            "方式を変更する場合は、再処理オプションのダイアログで Delete Entities And Start From Beginning を選択してください"
            "（アップロードしたファイルが保持されている場合は Start From Beginning も選択できます）。")
