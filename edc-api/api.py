"""EDC ナレッジグラフ抽出 HTTP API（FastAPI・自己完結）。

他のRAGアプリ / Dify 等から HTTP で呼べる薄いサービス。OpenAPI(/openapi.json, /docs)を
自動公開するので、Dify のカスタムツール(OpenAPIスキーマ取込)としてそのまま登録できる。
CWD非依存（テンプレ/スキーマを絶対パス解決）・副作用なし（ファイル出力なし）。
E→D→C本体・run.py・app.py は無改変。

起動:
    uvicorn api:app --host 0.0.0.0 --port 8080
    # /docs (Swagger UI), /openapi.json (Dify取込用)

エンドポイント:
    GET  /health        ヘルスチェック（LLM と埋め込みの到達も確認。落ちていれば 503）
    GET  /livez         プロセスの生存確認のみ（コンテナの healthcheck 用）
    GET  /doctypes      登録済み文書タイプ一覧
    POST /classify      文書タイプ分類のみ
    POST /extract       テキスト→KG抽出（doctypeで標準スキーマ自動適用）
    POST /extract_file  ファイル(.txt/.md/.pdf)アップロード→KG抽出
    POST /convert       PDF→ページ毎テキスト変換のみ（PaddleX OCR。抽出なし。
                        llm-graph-builder のスキャンPDF前処理用）
"""

import logging
import os
import re
import tempfile
import time
import unicodedata
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parent  # プロジェクトルート（prompt_templates等がある場所）
_env = _ROOT / ".env"
if _env.exists():
    load_dotenv(_env)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _p(*parts) -> str:
    return str(_ROOT.joinpath(*parts))


# ============================================================
# パイプライン（CWD非依存・副作用なし）
# ============================================================
def _build_config(llm_model="vllm", embedder="vllm",
                  target_schema_path=None, target_types_path=None,
                  enrich_schema=False, enrich_types=False,
                  few_shot=None, extra_instructions="") -> dict:
    cfg = _base_config(llm_model, embedder, target_schema_path, target_types_path, enrich_schema, enrich_types)
    # registry.json の文書タイプに few_shot があれば、その文書タイプ用の手本に差し替える
    few_shot = few_shot or {}
    key_map = {"oie": "oie_few_shot_example_file_path",
               "oie_typed": "oie_typed_few_shot_example_file_path",
               "sd": "sd_few_shot_example_file_path"}
    for k, cfg_key in key_map.items():
        if few_shot.get(k):
            path = few_shot[k] if os.path.isabs(few_shot[k]) else _p(few_shot[k])
            if os.path.exists(path):
                cfg[cfg_key] = path
            else:
                logging.warning(f"few-shot file not found, keeping default: {path}")
    cfg["oie_extra_instructions"] = extra_instructions or ""
    return cfg


def _base_config(llm_model, embedder, target_schema_path, target_types_path,
                 enrich_schema, enrich_types) -> dict:
    return {
        "oie_llm": llm_model,
        "oie_prompt_template_file_path": _p("prompt_templates", "oie_template.txt"),
        "oie_few_shot_example_file_path": _p("few_shot_examples", "example", "oie_few_shot_examples.txt"),
        "sd_llm": llm_model,
        "sd_prompt_template_file_path": _p("prompt_templates", "sd_template.txt"),
        "sd_few_shot_example_file_path": _p("few_shot_examples", "example", "sd_few_shot_examples.txt"),
        "sc_llm": llm_model,
        "sc_embedder": embedder,
        "sc_prompt_template_file_path": _p("prompt_templates", "sc_template.txt"),
        "sr_adapter_path": None,
        "sr_embedder": embedder,
        "oie_refine_prompt_template_file_path": _p("prompt_templates", "oie_r_template.txt"),
        "oie_refine_few_shot_example_file_path": _p("few_shot_examples", "example", "oie_few_shot_refine_examples.txt"),
        "ee_llm": llm_model,
        "ee_prompt_template_file_path": _p("prompt_templates", "ee_template.txt"),
        "ee_few_shot_example_file_path": _p("few_shot_examples", "example", "ee_few_shot_examples.txt"),
        "em_prompt_template_file_path": _p("prompt_templates", "em_template.txt"),
        "oie_typed_prompt_template_file_path": _p("prompt_templates", "oie_typed_template.txt"),
        "oie_typed_few_shot_example_file_path": _p("few_shot_examples", "example", "oie_typed_few_shot_examples.txt"),
        "tc_prompt_template_file_path": _p("prompt_templates", "tc_template.txt"),
        "target_schema_path": target_schema_path,
        "enrich_schema": enrich_schema,
        "target_types_path": target_types_path,
        "enrich_types": enrich_types,
        "loglevel": None,
    }


# ------------------------------------------------------------
# 抽出前処理: PDF の行の途中改行をつなぐ
# ------------------------------------------------------------
# PDF から取り出したテキストは版面の右端で行が折り返され、語の途中に改行が入る
# （例: 「JIS B 8340「ターボ形ブロワ・圧縮機の試験\n及び検査方法」」）。そのまま渡すと、
# 折り返しの後ろ（「及び検査方法」）が独立したエンティティとして抽出される。
_JA_RE = re.compile(r"[\u3005-\u3007\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
                    r"\uff10-\uff19\uff21-\uff3a\uff41-\uff5a\uff66-\uff9f]")
# 行末がこれらなら文・項目の区切りとみなし、改行を残す（句読点・閉じ括弧・表の区切り・コロン等）
_LINE_END_KEEP = set("。．.、，,！？!?」』）)］]｝}】〕〉》>”’\"'|｜：:；;*")
# 次行の先頭がこれらなら新しい見出し・箇条とみなし、改行を残す
_LINE_START_KEEP_RE = re.compile(
    r"^(?:"
    r"[#|｜・･●○◎■□◆◇▲△▼▽★☆※＊*•‣◦\-–—−＋+→⇒➢►▶✓✔☐☑〇【\[〔<＜]"
    r"|[\u2160-\u217f\u2460-\u24ff\u2776-\u2793\u3220-\u3229\u3280-\u3289]"  # Ⅰ ① ⑴ ⒈ ⓐ ❶ ㈠ ㊀
    r"|[\d０-９]+(?:[.\-－．][\d０-９]+)*(?:[.．](?![\d０-９])|[)）、]|[ \t\u3000])"  # 2-5 / 2.1 / 1) / 1．
    r"|[(（]\s*[\w０-９ａ-ｚＡ-Ｚ]{1,3}\s*[)）]"  # (1) / （a） / (ア)
    r"|第\s*[\d０-９一二三四五六七八九十百]+\s*[章節条項款編部]"
    r"|[A-Za-zＡ-Ｚａ-ｚ][.．)）](?![A-Za-z])"  # a) / A．
    r"|[ア-ンｱ-ﾝ][.．)）、]"  # ア． / イ)
    r"|[一二三四五六七八九十]+[、.．]"  # 一、
    r"|注\s*[\d０-９]*\s*[)）.．:：]"  # 注1) / 注:
    r"|[A-ZＡ-Ｚ]\s*改[正訂定]"  # 改訂来歴の B改正 / F 改正
    r")"
)
# 表の行とみなす: 「 / 」区切りが 2 つ以上（表を 1 行に畳んだ形）、点線の目次（・・・）、行中の 3 つ以上の空白（様式の欄）
_TABLE_LIKE_RE = re.compile(r"(?: / .*){2,}|[・･….．]{3,}|\S[ \u3000]{3,}\S")
# 1 文字ずつ空白で区切った行（様式の欄見出し「合 計」「定 期 的」）も表の一部とみなす
_SPACED_CHARS_RE = re.compile(r"^(?:\S[ \u3000]+)+\S$")
_TRAILING_NOTE_RE = re.compile(r"[(（][^()（）]*[)）]\s*$")
# 句点の無い要求文の終わり（「…を実施すること⏎旧型の場合…」）: 行末が「こと」で、次行が漢字・カタカナ・
# 英数字で始まるなら文の区切りとみなす（「…すること⏎とする。」のように仮名で続く折り返しはつなぐ）
_KOTO_END_RE = re.compile(r"こと$")
_NON_KANA_START_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u30a0-\u30ff\uff66-\uff9f"
                                r"A-Za-z0-9０-９Ａ-Ｚａ-ｚ]")
_BRACKET_PAIRS = ("「」", "『』", "（）", "()", "【】", "〔〕", "［］", "[]")
# 折り返しとみなす行の長さ（その文書の本文行で最も長い行に対する比）。見出しなどの短い行をつながない
_WRAP_WIDTH_RATIO = 0.6
# 折り返しとみなす行の長さの下限（表示幅。全角で 15 字）。短い行だけの入力（欄が 1 行ずつの表、
# OCR の表、項目の並び）では比の基準が小さくなり、隣り合う欄をつないでしまうため
_WRAP_MIN_WIDTH = 30
# 閉じていない括弧を理由に短い行をつなぐのは、括弧がこの行数以内で閉じるときだけ
_BRACKET_JOIN_MAX_LINES = 2


def _display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _has_unclosed_bracket(s: str) -> bool:
    return any(s.count(o) > s.count(c) for o, c in _BRACKET_PAIRS)


def _is_structural_line(s: str) -> bool:
    """表（| で始まる行・タブ区切り・「 / 」区切り・点線・欄の空白・1 文字ずつ空白で区切った欄見出し）と
    Markdown の見出し（# 行）は触らない。"""
    t = s.strip()
    return (t.startswith(("|", "｜", "#")) or "\t" in s or bool(_TABLE_LIKE_RE.search(t))
            or bool(_SPACED_CHARS_RE.match(t)))


def join_wrapped_lines(text: str, width_ratio: float = _WRAP_WIDTH_RATIO,
                       min_width_floor: int = _WRAP_MIN_WIDTH) -> str:
    """日本語の行の途中改行をつなぐ（抽出前処理。純粋関数）。

    次の条件をすべて満たすときだけ、行 A と次の行 B の間の改行を除く。
      - A・B とも空行でなく、表（| で始まる行・タブ区切り・「 / 」区切り・点線・欄の空白・
        1 文字ずつ空白で区切った欄見出し）でも見出し（# 行）でもない
      - A の行末が句読点・閉じ括弧・表の区切り・コロン等でない
      - B の先頭が見出し記号・箇条記号・番号見出し（2-5, (1), 第3条, ①, a) 等）でない
      - A が「こと」で終わり B が漢字・カタカナ・英数字で始まる形（句点の無い要求文の終わり）でない
      - A と B がともに日本語を含み（B は末尾の括弧書きを除いて判定）、つなぎ目の両側が
        ともに半角文字ではない（英文の語や、文書番号・記号・欄の値だけの行を連結しない）
      - A の表示幅が基準以上ある。基準は「本文行の最長 × width_ratio」と min_width_floor
        （表示幅 30、全角 15 字）の大きい方（短い題目行や、短い欄が 1 行ずつ並ぶ表をつながないため）。
        基準に満たない短い行でも、A までに開いた括弧が B を含む先の _BRACKET_JOIN_MAX_LINES 行
        以内で閉じるならつなぐ（「規格「ターボ形ブロワの試験⏎及び検査方法」」の形）
    つなぐときは A の行末と B の行頭の空白を除いて連結する。空行（段落の区切り）は残す。
    """
    if not text or "\n" not in text:
        return text
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    prose = [_display_width(ln.strip()) for ln in lines if ln.strip() and not _is_structural_line(ln)]
    min_width = max((max(prose) * width_ratio) if prose else 0, min_width_floor)

    def bracket_closes_soon(buf: str, i: int) -> bool:
        if not _has_unclosed_bracket(buf):
            return False
        acc = buf.rstrip()
        for k in range(i, min(i + _BRACKET_JOIN_MAX_LINES, len(lines))):
            acc += lines[k].strip()
            if not _has_unclosed_bracket(acc):
                return True
        return False

    def can_join(buf: str, prev: str, i: int) -> bool:
        nxt = lines[i]
        a, b = prev.rstrip(), nxt.lstrip()
        if not a or not b or _is_structural_line(prev) or _is_structural_line(nxt):
            return False
        if a[-1] in _LINE_END_KEEP or _LINE_START_KEEP_RE.match(b):
            return False
        if _KOTO_END_RE.search(a) and _NON_KANA_START_RE.match(b):
            return False
        # B の日本語は末尾の括弧書き（「TS-A102(最新版)」の「(最新版)」）の外で判定する
        b_core = _TRAILING_NOTE_RE.sub("", b)
        if not _JA_RE.search(a) or not _JA_RE.search(b_core) or (ord(a[-1]) < 128 and ord(b[0]) < 128):
            return False
        return _display_width(a.strip()) >= min_width or bracket_closes_soon(buf, i)

    out, buf, prev = [], None, None
    for i, ln in enumerate(lines):
        if buf is not None and can_join(buf, prev, i):
            buf = buf.rstrip() + ln.strip()
        else:
            if buf is not None:
                out.append(buf)
            buf = ln
        prev = ln
    if buf is not None:
        out.append(buf)
    return "\n".join(out)


def _join_wrapped_lines_enabled() -> bool:
    """EDC_JOIN_WRAPPED_LINES=0/false/off で行つなぎを止められる（既定は有効）。"""
    return os.environ.get("EDC_JOIN_WRAPPED_LINES", "1").strip().lower() not in ("0", "false", "off", "no")


def _prepare_text(text):
    if not isinstance(text, str) or not _join_wrapped_lines_enabled():
        return text
    return join_wrapped_lines(text)


def _split_by_line(text):
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _split_by_heading(text):
    chunks, cur = [], []
    for line in text.split("\n"):
        if re.match(r'^#{1,6}\s', line) and cur:
            c = "\n".join(cur).strip()
            if c:
                chunks.append(c)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        c = "\n".join(cur).strip()
        if c:
            chunks.append(c)
    return chunks if chunks else _split_by_line(text)


def _split_recursive(text, chunk_size=1000, overlap=100):
    seps = ["\n\n", "\n", "。", ".", " "]

    def _s(t, i):
        if len(t) <= chunk_size:
            return [t.strip()] if t.strip() else []
        if i >= len(seps):
            return [t[j:j + chunk_size].strip() for j in range(0, len(t), chunk_size - overlap) if t[j:j + chunk_size].strip()]
        out, cur = [], ""
        for p in t.split(seps[i]):
            cand = cur + seps[i] + p if cur else p
            if len(cand) <= chunk_size:
                cur = cand
            else:
                if cur.strip():
                    out.append(cur.strip())
                if len(p) > chunk_size:
                    out.extend(_s(p, i + 1)); cur = ""
                else:
                    cur = p
        if cur.strip():
            out.append(cur.strip())
        return out
    r = _s(text, 0)
    return r if r else _split_by_line(text)


def _chunk(text, method="line", chunk_size=1000):
    if method == "heading":
        return _split_by_heading(text)
    if method == "recursive":
        return _split_recursive(text, chunk_size)
    return _split_by_line(text)


def _assemble_inputs(text=None, file_bytes=None, filename=None, chunk_method="line", chunk_size=1000):
    import pdf_processor
    is_pdf = bool(filename) and filename.lower().endswith(".pdf")
    if text is not None:
        return _chunk(_prepare_text(text), chunk_method, chunk_size)
    if file_bytes is not None:
        if is_pdf:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(file_bytes); tmp_path = tmp.name
            try:
                return [_prepare_text(p) for p in pdf_processor.extract_text_from_pdf(tmp_path)]  # ページ毎
            finally:
                os.unlink(tmp_path)
        return _chunk(_prepare_text(file_bytes.decode("utf-8")), chunk_method, chunk_size)
    raise ValueError("text または file が必要です")


def _noise_checker(entry):
    """文書タイプの noise_terms / noise_patterns / noise_keep から判定関数を作る。未設定なら何も落とさない。"""
    entry = entry or {}
    def norm(s):
        return re.sub(r"[\s\u3000]+", "", unicodedata.normalize("NFKC", str(s)))
    terms = {norm(x) for x in entry.get("noise_terms", [])}
    keep = {norm(x) for x in entry.get("noise_keep", [])}
    pats = [re.compile(p) for p in entry.get("noise_patterns", [])]
    if not terms and not pats:
        return lambda name: False
    def is_noise(name):
        n = norm(name)
        if not n:
            return True
        if n in keep:
            return False
        return n in terms or any(p.fullmatch(n) for p in pats)
    return is_noise


def _postprocess(results, A, entry):
    """正規化後の結果から、ノイズを除いたトリプルと、語彙に合わず落ちたトリプルを作る。

    戻り値: (triplets, n_noise_dropped, dropped)
      dropped は enrich_schema=False のとき語彙に写せず捨てられた抽出結果。
      人が語彙を育てるための候補として、定義文と一緒に返す。
    """
    is_noise = _noise_checker(entry)
    kept, n_noise = [], 0
    for t in _format_triplets(results):
        if is_noise(t["subject"]) or is_noise(t["object"]):
            n_noise += 1
            continue
        kept.append(t)

    dropped = []
    typed = A.get("typed_oie_list")
    for i, chunk in enumerate(results):
        sd = (A.get("sd_dict_list") or [{}] * len(results))[i] or {}
        for j, t in enumerate(chunk):
            if t is not None:
                continue
            src = None
            if typed is not None and i < len(typed) and j < len(typed[i]):
                src = typed[i][j]
            elif i < len(A["oie_triplets_list"]) and j < len(A["oie_triplets_list"][i]):
                src = A["oie_triplets_list"][i][j]
            if not src:
                continue
            if len(src) == 5:
                rec = {"subject": src[0], "subject_type": src[1], "relation": src[2],
                       "object": src[3], "object_type": src[4]}
            else:
                rec = {"subject": src[0], "relation": src[1], "object": src[2]}
            if is_noise(rec["subject"]) or is_noise(rec["object"]):
                continue
            rec["definition"] = sd.get(rec["relation"], "")
            rec["chunk_index"] = i
            dropped.append(rec)
    return kept, n_noise, dropped


def _format_triplets(results):
    out = []
    for chunk in results:
        for t in chunk:
            if t is None:
                continue
            if len(t) == 3:
                out.append({"subject": t[0], "relation": t[1], "object": t[2]})
            elif len(t) == 5:
                out.append({"subject": t[0], "subject_type": t[1], "relation": t[2],
                            "object": t[3], "object_type": t[4]})
    return out


def extract_kg(text=None, file_bytes=None, filename=None, doctype=None,
               chunk_method="line", chunk_size=1000, llm_model="vllm", embedder="vllm",
               enrich_schema=None, enrich_types=None) -> dict:
    """文書からKG(トリプル)を抽出。doctypeで文書タイプ分類→標準スキーマ自動適用。"""
    import edc.utils.llm_utils as llm_utils
    llm_utils.reset_call_stats()
    input_texts = _assemble_inputs(text, file_bytes, filename, chunk_method, chunk_size)
    if not input_texts:
        return {"doctype": None, "classification": None, "schema": {}, "types": {},
                "n_input_chunks": 0, "n_triplets": 0, "triplets": []}

    dt_name = schema_path = types_path = cls = None
    entry = None
    if doctype:
        from doctype_router import load_registry, resolve, _find_doctype
        reg = load_registry()
        sample = "\n".join(input_texts[:40])[:3000]
        dt_name, schema_path, types_path, cls = resolve(sample, reg, doctype=doctype)
        entry = _find_doctype(dt_name, reg) if dt_name else None

    # 既定: 関係は常に enrich(=フリー発見でも空スキーマに全ドロップしない)。
    # 型は doctype で型スキーマが来たときだけ既定ON(=typed)。指定があれば尊重。
    if enrich_schema is None:
        enrich_schema = True
    if enrich_types is None:
        enrich_types = bool(types_path)
    enrich_schema, enrich_types = bool(enrich_schema), bool(enrich_types)
    # 閉じる語彙が無い（文書タイプ無し・UNKNOWN・未登録）のに enrich_schema=False だと、
    # 全トリプルが語彙外として落ちて 0 件になる。その場合は自由発見に切り替える。
    if not enrich_schema and schema_path is None:
        logging.warning(f"No vocabulary for doctype={dt_name!r}; falling back to open discovery (enrich_schema=True)")
        enrich_schema = True

    from edc.edc_framework import EDC
    edc = EDC(**_build_config(llm_model, embedder, schema_path, types_path, enrich_schema, enrich_types,
                              few_shot=(entry or {}).get("few_shot"),
                              extra_instructions=(entry or {}).get("extract_instructions", "")))
    A = edc.extract_and_define(input_texts)
    results = edc.canonicalize_with_schema(
        input_texts, A["oie_triplets_list"], A["sd_dict_list"], dict(edc.schema),
        typed_oie_list=A["typed_oie_list"], enrich=enrich_schema,
        curated_types=(dict(edc.types) if edc.types else None), enrich_types=enrich_types,
    )
    triplets, n_noise, dropped = _postprocess(results, A, entry)
    stats = llm_utils.get_call_stats()
    if stats["truncated"]:
        logging.warning(f"{stats['truncated']}/{stats['calls']} LLM calls hit the output limit in this request")
    return {
        "doctype": dt_name, "classification": cls,
        "schema": dict(edc.schema), "types": dict(edc.types),
        "n_input_chunks": len(input_texts), "n_triplets": len(triplets), "triplets": triplets,
        "n_noise_dropped": n_noise, "dropped": dropped,
        "n_llm_calls": stats["calls"], "n_truncated": stats["truncated"],
        "vocabulary_closed": not enrich_schema,
    }


# ============================================================
# FastAPI
# ============================================================
app = FastAPI(
    title="EDC Knowledge Graph Extraction API",
    description="文書からトリプル(KG)を抽出。文書タイプ分類→標準スキーマ自動適用に対応（完全ローカル）。",
    version="1.0.0",
)


class ExtractRequest(BaseModel):
    text: str
    doctype: Optional[str] = "auto"          # None / "auto" / "<タイプ名>"
    chunk_method: str = "line"               # line / heading / recursive
    enrich_schema: Optional[bool] = None
    enrich_types: Optional[bool] = None


class Triplet(BaseModel):
    subject: str
    relation: str
    object: str
    subject_type: Optional[str] = None
    object_type: Optional[str] = None


class ExtractResponse(BaseModel):
    doctype: Optional[str] = None
    classification: Optional[Dict[str, Any]] = None
    schema_: Dict[str, str] = {}
    types: Dict[str, str] = {}
    n_input_chunks: int = 0
    n_triplets: int = 0
    triplets: List[Triplet] = []
    n_noise_dropped: int = 0                 # 文書タイプのノイズ規則で落としたトリプル数
    dropped: List[Dict[str, Any]] = []       # 語彙に写せず落ちた抽出結果（enrich_schema=False のとき）
    n_llm_calls: int = 0
    n_truncated: int = 0                     # 出力上限で切れた LLM 呼び出しの数（0 以外は取りこぼしあり）
    vocabulary_closed: bool = False          # True: 語彙外を落とした / False: 語彙外もそのまま足した（自由発見）


def _to_response(r: dict) -> dict:
    return {
        "doctype": r.get("doctype"),
        "classification": r.get("classification"),
        "schema_": r.get("schema", {}),
        "types": r.get("types", {}),
        "n_input_chunks": r.get("n_input_chunks", 0),
        "n_triplets": r.get("n_triplets", 0),
        "triplets": r.get("triplets", []),
        "n_noise_dropped": r.get("n_noise_dropped", 0),
        "dropped": r.get("dropped", []),
        "n_llm_calls": r.get("n_llm_calls", 0),
        "n_truncated": r.get("n_truncated", 0),
        "vocabulary_closed": r.get("vocabulary_closed", False),
    }


_HEALTH_CACHE = {"t": 0.0, "code": 200, "body": None}
_HEALTH_TTL_SECONDS = 10


def _probe(url: str):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return ("ok" if r.status == 200 else f"http {r.status}")
    except Exception as e:
        return f"down: {str(e)[:120]}"


@app.get("/livez")
def livez():
    """プロセスが応答するかだけを見る（コンテナの healthcheck 用）。"""
    return {"status": "ok"}


@app.get("/health")
def health():
    """LLM と埋め込みに届くかまで確かめる。どちらかが落ちていれば 503 を返す。

    呼び出し側（Graph Builder 等）が、抽出を始める前に「今は抽出できない」と分かるようにするため。
    vLLM 以外のプロバイダでは到達確認を省く。結果は 10 秒キャッシュする。
    """
    now = time.time()
    if _HEALTH_CACHE["body"] is not None and now - _HEALTH_CACHE["t"] < _HEALTH_TTL_SECONDS:
        return JSONResponse(_HEALTH_CACHE["body"], status_code=_HEALTH_CACHE["code"])
    body = {"status": "ok"}
    if os.environ.get("LLM_PROVIDER", "vllm") == "vllm" and os.environ.get("VLLM_ENDPOINT"):
        body["llm"] = _probe(os.environ["VLLM_ENDPOINT"].rstrip("/") + "/models")
        emb = os.environ.get("VLLM_EMBEDDING_ENDPOINT")
        if emb:
            body["embedding"] = _probe(emb.rstrip("/") + "/models")
        if any(v != "ok" for k, v in body.items() if k != "status"):
            body["status"] = "degraded"
    code = 200 if body["status"] == "ok" else 503
    _HEALTH_CACHE.update(t=now, code=code, body=body)
    return JSONResponse(body, status_code=code)


@app.get("/doctypes")
def doctypes():
    from doctype_router import load_registry
    reg = load_registry()
    return [{"name": d["name"], "description": d.get("description", "")} for d in reg.get("doctypes", [])]


@app.post("/classify")
def classify(req: ExtractRequest):
    from doctype_router import load_registry, classify_document
    return classify_document(req.text, load_registry())


@app.post("/extract", response_model=ExtractResponse)
def extract(req: ExtractRequest):
    return _to_response(extract_kg(
        text=req.text, doctype=req.doctype, chunk_method=req.chunk_method,
        enrich_schema=req.enrich_schema, enrich_types=req.enrich_types,
    ))


@app.post("/extract_file", response_model=ExtractResponse)
async def extract_file(file: UploadFile = File(...), doctype: str = Form("auto"),
                       chunk_method: str = Form("line")):
    data = await file.read()
    return _to_response(extract_kg(
        file_bytes=data, filename=file.filename, doctype=doctype, chunk_method=chunk_method,
    ))


class ConvertResponse(BaseModel):
    filename: Optional[str] = None
    n_pages: int = 0
    pages: List[str] = []


@app.post("/convert", response_model=ConvertResponse)
async def convert(file: UploadFile = File(...)):
    """PDF→ページ毎テキスト変換のみ（KG抽出なし）。

    バックエンドは env（PDF_PROCESSOR/PDF_BACKEND）に従う: 既定は PaddleX PP-OCRv5。
    スキャンPDFのOCRを他アプリ（llm-graph-builder等）から使うための薄い口。
    """
    import pdf_processor
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data); tmp_path = tmp.name
    try:
        pages = pdf_processor.extract_text_from_pdf(tmp_path)
    finally:
        os.unlink(tmp_path)
    return {"filename": file.filename, "n_pages": len(pages), "pages": pages}
