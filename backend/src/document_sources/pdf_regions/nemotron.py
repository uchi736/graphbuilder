"""Nemotron Parse（vLLM の OpenAI 互換の口）のクライアントと、出力のトークンを領域（blocks）に直す処理。

要求の形は同梱の CLI（~/ai-stack/vllm/nemotron-parse/model/nemotron_parse/inference.py の DocumentParser._generate、
ページ全体を 1 回で読む single の設定）と同じにした。出力は
    <x_0.13><y_0.04>本文<x_0.20><y_0.06><class_Page-header>
の並びで、座標は送った画像に対する 0〜1 の値。表は LaTeX の tabular で返る（CLI はこれを HTML に直す）。
ここでは CLI の後処理（postprocessing.py の clean_raw_output・extract_classes_bboxes と、表の変換）と同じことを、
必要な分だけ書き直した（CLI のコードは写していない。CLI と重みは OpenMDW-1.1）。表は HTML を経ずに直接セルの格子にする。
"""
from __future__ import annotations

import base64
import html
import re
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional

from . import limits

PROMPT = "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"
# CLI の既定（ページ全体を読むときの生成の設定）
GENERATION = {"temperature": 0.2, "repetition_penalty": 1.05, "max_tokens": 8744, "top_k": 50, "top_p": 1.0, "seed": 0}

_NUM = r"\d+(?:\.\d+)?"
_BLOCK = re.compile(rf"<x_({_NUM})><y_({_NUM})>(.*?)<x_({_NUM})><y_({_NUM})><class_([^>]+)>", re.S)
_START = rf"<x_{_NUM}><y_{_NUM}>"
# 中身の無い領域の印が次の領域の頭に付いたもの（CLI の repair_empty_prefix と同じ扱い）
_EMPTY_PREFIX = re.compile(rf"(?m)^[ \t]*{_START}<class_[A-Za-z][A-Za-z_-]*>\s*(?={_START})")


class ParseError(RuntimeError):
    pass


@dataclass
class Block:
    cls: str
    bbox: tuple     # (x0, y0, x1, y1)、送った画像に対する 0〜1
    text: str       # モデルの出力そのまま（表は LaTeX）


def parse_blocks(raw: str) -> list:
    """出力のトークン列を領域の並び（読み順）にする。同じ領域が 2 回出たら 1 つにする"""
    raw = raw or ""
    if "```" not in raw and not re.search(r"<class_(?:Code|Formula)>", raw):
        raw = _EMPTY_PREFIX.sub("", raw)
    seen = set()
    out = []
    for m in _BLOCK.finditer(raw):
        x0, y0, body, x1, y1, cls = m.groups()
        box = (float(x0), float(y0), float(x1), float(y1))
        key = (cls, box, body)
        if key in seen:
            continue
        seen.add(key)
        bx0, bx1 = sorted((box[0], box[2]))
        by0, by1 = sorted((box[1], box[3]))
        clamp = lambda v: min(1.0, max(0.0, v))
        text = body.replace("<tbc>", "").replace(r"\<|unk|\>", "").replace(r"\unknown", "")
        out.append(Block(cls, (clamp(bx0), clamp(by0), clamp(bx1), clamp(by1)), text))
    return out


# ---- 表: LaTeX の tabular / HTML の table → セルの格子 ----
def _split_top(text: str, sep: str) -> list:
    """波括弧の外にある区切り（エスケープされていないもの）で分ける"""
    parts, depth, cur, i = [], 0, [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            if sep == "\\\\" and text[i + 1] == "\\" and depth == 0:
                parts.append("".join(cur))
                cur = []
                i += 2
                continue
            cur.append(text[i:i + 2])
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif sep == "&" and c == "&" and depth == 0:
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


def _braced(text: str, i: int):
    """text[i] が { のとき、対応する } までの中身と次の位置"""
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] != "{":
        return None, i
    depth, start = 0, i + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1
    return text[start:], len(text)


def _span_command(cell: str, name: str):
    """\\multicolumn{n}{spec}{中身} / \\multirow{n}{幅}{中身} を (n, 中身) にする"""
    s = cell.strip()
    if not s.startswith("\\" + name):
        return None
    i = len(name) + 1
    n, i = _braced(s, i)
    _, i = _braced(s, i)
    body, i = _braced(s, i)
    if n is None or body is None:
        return None
    try:
        count = max(1, int(re.sub(r"[^\d]", "", n) or "1"))
    except ValueError:
        count = 1
    return count, body + s[i:]


_LATEX_WRAP = re.compile(r"\\(?:thead|textbf|textit|emph|underline|makecell|text|mathrm|mathbf|mathit|operatorname|sout|st)"
                         r"\s*(?:\[[^\]]*\])?\s*\{")

# 記号の命令を文字に直す表（vlm.py の説明文の数式でも使う）。範囲の ~ と \sim は文字層と同じ全角の「～」にする
LATEX_SYMBOLS = {"pi": "π", "Pi": "Π", "Delta": "Δ", "delta": "δ", "rho": "ρ", "eta": "η", "alpha": "α", "beta": "β",
                 "gamma": "γ", "theta": "θ", "mu": "μ", "sigma": "σ", "Sigma": "Σ", "omega": "ω", "Omega": "Ω", "phi": "φ",
                 "varphi": "φ", "Phi": "Φ", "lambda": "λ", "tau": "τ", "epsilon": "ε", "varepsilon": "ε", "nu": "ν",
                 "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓", "le": "≤", "leq": "≤", "leqq": "≦",
                 "ge": "≥", "geq": "≥", "geqq": "≧", "neq": "≠", "ne": "≠", "approx": "≈", "fallingdotseq": "≒",
                 "simeq": "≃", "equiv": "≡", "circ": "°", "degree": "°", "textdegree": "°", "sim": "～", "infty": "∞",
                 "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒", "uparrow": "↑", "downarrow": "↓",
                 "varnothing": "∅", "emptyset": "∅", "angle": "∠", "sqrt": "√", "propto": "∝",
                 "textasciitilde": "～", "textperthousand": "‰", "permil": "‰", "ohm": "Ω", "micro": "μ"}


def _latex_symbol(m) -> str:
    return LATEX_SYMBOLS.get(m.group(1), "")


def _latex_math(m) -> str:
    """$...$ の中: 上付き・下付きの印を外す（m$^{3}$/s → m3/s。文字層の書き方に合わせる）"""
    s = m.group(1)
    for _ in range(3):
        s = re.sub(r"[_^]\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"(?<!\\)[_^](?=\S)", "", s)
    # 記号の命令は数式の中で字にする（$^{\circ}$C の \circ が後ろの C とつながって別の命令に読まれないように）
    return re.sub(r"\\([a-zA-Z]+)\s*", _latex_symbol, s)


def _latex_text(cell: str) -> str:
    s = cell
    for _ in range(5):
        new = _LATEX_WRAP.sub("{", s)
        if new == s:
            break
        s = new
    s = re.sub(r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]*\}|hdashline)", " ", s)
    s = re.sub(r"\$([^$]*)\$", _latex_math, s)
    s = (s.replace("\\\\", " ").replace("\\newline", " ").replace("\\%", "%").replace("\\&", "&").replace("\\_", "_")
         .replace("\\#", "#").replace("\\$", "$").replace("\\textless", "<").replace("\\textgreater", ">")
         .replace("\\backslash", "\\").replace("\\*", "*").replace("\\~", "～").replace("\\{", "{").replace("\\}", "}"))
    # 範囲の「~」（1000~1350）は「～」に。空白の代わりの ~ と区別できないので、両側が空白でないときだけ字として残す
    s = re.sub(r"(?<=\S)~(?=\S)", "～", s).replace("~", " ")
    s = re.sub(r"\\([a-zA-Z]+)\s*", _latex_symbol, s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def _place(rows) -> list:
    """(中身, colspan, rowspan) の行の並びを、結合セルの値を繰り返した格子にする"""
    grid, pending = [], {}   # pending: (行, 列) → 値（上の行の rowspan）
    for r, cells in enumerate(rows):
        line, c = [], 0
        for text, colspan, rowspan in cells:
            if not text and colspan == 1 and rowspan == 1 and (r, c) in pending:
                # 上の行の \multirow の下に置かれた空の欄（場所取り）。上の値で埋める
                line.append(pending.pop((r, c)))
                c += 1
                continue
            while (r, c) in pending:
                line.append(pending.pop((r, c)))
                c += 1
            for dc in range(colspan):
                line.append(text)
                for dr in range(1, rowspan):
                    pending[(r + dr, c + dc)] = text
            c += colspan
        while (r, c) in pending:
            line.append(pending.pop((r, c)))
            c += 1
        grid.append(line)
    return grid


def latex_rows(text: str) -> Optional[list]:
    """LaTeX の tabular を、展開前のセル (中身, colspan, rowspan) の行の並びにする"""
    m = re.search(r"\\begin\{tabular\}\s*(?:\{(?:[^{}]|\{[^{}]*\})*\})?(.*?)(?:\\end\{tabular\}|$)", text, re.S)
    if not m:
        return None
    rows = []
    for row in _split_top(m.group(1), "\\\\"):
        if not re.sub(r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]*\})|\s", "", row):
            continue
        cells = []
        for cell in _split_top(row, "&"):
            colspan, rowspan = 1, 1
            mc = _span_command(cell, "multicolumn")
            if mc:
                colspan, cell = mc
            mr = _span_command(cell, "multirow")
            if mr:
                rowspan, cell = mr
            cells.append((_latex_text(cell), colspan, rowspan))
        rows.append(cells)
    return rows or None


def latex_grid(text: str) -> Optional[list]:
    rows = latex_rows(text)
    return _place(rows) if rows else None


class _TableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cell, self.attrs = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self.cell, self.attrs = [], dict(attrs)
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            if not self.rows:
                self.rows.append([])
            def num(key):
                try:
                    return max(1, int(self.attrs.get(key) or 1))
                except ValueError:
                    return 1
            self.rows[-1].append((re.sub(r"\s+", " ", "".join(self.cell)).strip(), num("colspan"), num("rowspan")))
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def html_rows(text: str) -> Optional[list]:
    if "<table" not in text.lower():
        return None
    p = _TableHTML()
    p.feed(text)
    rows = [r for r in p.rows if r]
    return rows or None


def html_grid(text: str) -> Optional[list]:
    rows = html_rows(text)
    return _place(rows) if rows else None


def table_rows(text: str) -> Optional[list]:
    """表を、展開前のセル (中身, colspan, rowspan) の行の並びにする。字数の比較はこちらで数える（結合セルを 1 回だけ数える）"""
    if "\\begin{tabular}" in (text or ""):
        return latex_rows(text)
    return html_rows(text or "")


def place(rows) -> list:
    """展開前のセルの行の並びを、結合セルの値を繰り返した格子にする（GFM に書くとき用）"""
    return _place(rows)


def table_grid(text: str) -> Optional[list]:
    rows = table_rows(text)
    return _place(rows) if rows else None


def plain_text(text: str) -> str:
    """表以外の領域の出力を本文の文字にする（文字層の無い領域で使う）。表は結合セルを繰り返さずに行ごとに並べる"""
    if "\\begin{tabular}" in text or "<table" in text.lower():
        rows = table_rows(text) or []
        return "\n".join(" ".join(c for c, _cs, _rs in row if c) for row in rows)
    s = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    s = re.sub(r"</?u>", "", s, flags=re.I)
    s = re.sub(r"<(sup|sub)>(.*?)</\1>", lambda m: m.group(0) if re.fullmatch(r"[+\-−]?[0-9A-Za-z]{1,4}", m.group(2).strip())
               else m.group(2), s, flags=re.S | re.I)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", s)
    return html.unescape(s).strip()


# ---- HTTP ----
class NemotronClient:
    """1 枚の画像を Nemotron Parse に送り、領域の並びを返す。

    同時に送る数は接続先ごとに concurrency まで（同じプロセスの変換どうしと、同じコンテナの作業プロセスどうしで共有する。
    limits.py）。接続の失敗・5xx は retries 回まで待って送り直す。応答の時間切れは送り直さない（相手が止まっていると待ちが
    積み重なる）。時間切れ・接続の失敗・5xx が続いた接続先はしばらく呼ばない（limits.Endpoint の遮断）。
    生成が途中で切れた応答（finish_reason が length）は同じ設定で送り直しても同じ結果になるので、送り直さずに失敗とする。
    deadline（time.time() の値）を過ぎたら送らずに失敗とし、1 回の要求の時間切れも締め切りまでに縮める。
    """

    def __init__(self, base_url: str, model: str = "nemotron-parse", timeout: float = 180.0, retries: int = 2,
                 concurrency: int = 2, client=None, deadline: Optional[float] = None, lock_dir: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.deadline = deadline
        self._endpoint = limits.endpoint(self.base_url, concurrency, lock_dir)
        self._client = client
        self.calls = 0
        self.failures = 0
        self._lock = threading.Lock()

    def payload(self, png: bytes) -> dict:
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        return {"model": self.model,
                "messages": [{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                                          {"type": "image_url", "image_url": {"url": url}}]}],
                **GENERATION, "skip_special_tokens": False}

    def _timeout_now(self) -> float:
        left = limits.remaining(self.deadline)
        return self.timeout if left is None else max(1.0, min(self.timeout, left))

    def _post(self, payload: dict, tag: str) -> dict:
        import httpx
        client = self._client
        timeout = self._timeout_now()
        if client is None:
            with httpx.Client(timeout=timeout) as c:
                r = c.post(self.base_url + "/chat/completions", json=payload, headers={"Authorization": "Bearer EMPTY"})
        else:
            r = client.post(self.base_url + "/chat/completions", json=payload, headers={"Authorization": "Bearer EMPTY"},
                            timeout=timeout)
        r.raise_for_status()
        return r.json()

    def parse_raw(self, png: bytes, tag: str = "") -> str:
        payload = self.payload(png)
        last = None
        for attempt in range(self.retries + 1):
            body = None
            try:
                with self._endpoint.slot(self.deadline):
                    with self._lock:
                        self.calls += 1
                    try:
                        body = self._post(payload, tag)
                    except Exception as error:   # 接続の失敗・時間切れ・HTTP のエラー
                        last = error
                        if limits.is_unavailable_error(error):
                            self._endpoint.failure()
                        if not limits.is_retryable(error):
                            break
                    else:
                        self._endpoint.success()
            except limits.EndpointUnavailable as error:   # 遮断中・締め切り
                last = error
                break
            if body is not None:
                choice = (body.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content") or ""
                finish = choice.get("finish_reason")
                if finish not in (None, "stop", "eos") or not content.strip():
                    last = ParseError(f"incomplete generation (finish_reason={finish!r})")
                    break
                return content
            if attempt < self.retries:
                wait = 1.0 + 2.0 * attempt
                left = limits.remaining(self.deadline)
                if left is not None and left <= wait:
                    break
                time.sleep(wait)
        with self._lock:
            self.failures += 1
        raise ParseError(f"Nemotron Parse failed for {tag or 'image'}: {last}")

    def parse(self, png: bytes, tag: str = "") -> list:
        return parse_blocks(self.parse_raw(png, tag))
