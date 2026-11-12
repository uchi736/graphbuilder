"""図の領域の扱い: 軽量の VLM（E4B）で種類を判定し、図・グラフだけ VLM（26B）で説明文にする。

判定の指示（TRIAGE_PROMPT）と、種類から扱いを決める表（ACTION_BY_KIND）、説明の指示（DESCRIBE_PROMPT）は、
CPT の前処理の試作 ~/ai-stack/finetune/cpt-data-pipeline/scripts/preprocess/onprem_regions.py（2026-11-08）の考え方を写した。
扱いは種類から規則で決める（E4B と 26B は種類では一致したが、写真の扱いで割れたため）。

説明文の後処理: ``` の囲み、行頭の番号・箇条の印・#、太字の印を外し、1 つの段落にする
（行頭の番号が残ると、目録が章・節の見出しと読む）。
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from typing import Optional

from . import limits
from .nemotron import LATEX_SYMBOLS

TRIAGE_PROMPT = """これは技術文書の 1 ページから切り出した領域の画像である。文書解析器はこの領域を「{cls}」と判定した。
画像を見て、次の JSON だけを返す(説明文は不要):
{{"種類": "写真|人物写真|系統図・構成図|グラフ|表|数式|文字だけ(図ではない)|ロゴ・装飾",
  "扱い": "捨てる|本文として読む|表として書き起こす|図として説明する|キャプションだけ残す",
  "理由": "15 字以内"}}
判断の目安: 機器の写真や人物写真は文字にしても知識にならないので「キャプションだけ残す」か「捨てる」。
系統図・構成図・グラフは中身を文章にする価値があるので「図として説明する」。表は「表として書き起こす」。
画像の大半が文字の段落や日付・番号の行なら「文字だけ(図ではない)」「本文として読む」。"""

ACTION_BY_KIND = {"写真": "キャプションだけ残す", "人物写真": "捨てる", "系統図・構成図": "図として説明する",
                  "グラフ": "図として説明する", "表": "表として書き起こす", "数式": "本文として読む",
                  "文字だけ(図ではない)": "本文として読む", "ロゴ・装飾": "捨てる"}

DESCRIBE_PROMPT = {
    "系統図・構成図": "この図は技術文書の{cap}である。図に描かれた機器・部品の名前と、それらのつながり・流れの向きを、日本語の文章で正確に説明する。"
                      "図に書かれた文字はそのまま使い、図に無いことは書かない。300 字以内。",
    "グラフ": "このグラフは技術文書の{cap}である。軸の名前と単位、系列、読み取れる値の範囲と傾向を、日本語の文章で正確に説明する。"
              "読み取れない値は推測しない。300 字以内。",
    "_default": "この図は技術文書の{cap}である。図の内容を、図に書かれた文字を使って日本語の文章で正確に説明する。図に無いことは書かない。300 字以内。",
}

FIGURE_MARK = "（図の説明・自動生成）"
FIGURE_TEXT_LABEL = "図中の文字: "

_FENCE = re.compile(r"^\s*(```|~~~).*$", re.M)
_LEAD = re.compile(r"^\s*(?:#{1,6}\s*|[-*•・●○■□◆◇▶►]\s+|\d{1,2}(?:[-.．]\d{1,2})+(?![0-9])[.)．）:：]?\s*|"
                   r"\d{1,2}[.)．）:：](?![0-9])\s*|[（(]\d{1,2}[)）]\s*|[①-⑳]\s*|[a-zA-Z][.)）]\s+)")


# 記号の命令を文字に直す表は Nemotron Parse の表と共通（範囲の \sim は「～」）
_LATEX_SYMBOLS = dict(LATEX_SYMBOLS, **{"%": "%"})


def _plain_math(text: str) -> str:
    r"""説明文の中の数式の記法（$\pi^c$、$\text{m}^3/\text{s}$）を普通の文字にする"""
    def math(m):
        s = m.group(1)
        s = re.sub(r"\\(?:text|mathrm|mathit|mathbf|operatorname)\s*\{([^{}]*)\}", r"\1", s)
        for _ in range(3):
            s = re.sub(r"([_^])\{([^{}]*)\}", r"\1\2", s)
        s = re.sub(r"\\(?:d?frac)\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\1/\2", s)
        s = re.sub(r"\\([A-Za-z]+|%)", lambda k: _LATEX_SYMBOLS.get(k.group(1), k.group(1)), s)
        s = re.sub(r"[{}]", "", s)
        return s.replace("\\", "")
    text = re.sub(r"\$\$(.+?)\$\$", math, text, flags=re.S)
    text = re.sub(r"\$([^$\n]+)\$", math, text)
    text = re.sub(r"\\\((.+?)\\\)", math, text)
    return text


def clean_vlm_text(text: str) -> str:
    """VLM の説明文を 1 つの段落にする。囲み・行頭の番号や印・見出しの印・太字の印・数式の記法を外す"""
    s = _plain_math(_FENCE.sub("", text or ""))
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*(?!\*)", "", s)
    lines = []
    for line in s.split("\n"):
        line = line.strip()
        for _ in range(3):
            new = _LEAD.sub("", line, count=1)
            if new == line:
                break
            line = new.strip()
        if line and not re.fullmatch(r"[|:\-\s]+", line):
            lines.append(line)
    out = ""
    for line in lines:
        if out and re.match(r"[0-9A-Za-z]", line[0]) and re.match(r"[0-9A-Za-z.,;:]", out[-1]):
            out += " "
        out += line
    return re.sub(r"\s+", " ", out).replace("`", "").strip()


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}


class VlmClient:
    """画像 1 枚と指示を OpenAI 互換の口に送る。

    同時数の上限は接続先ごと（同じプロセスの変換どうしと、同じコンテナの作業プロセスどうしで共有する。limits.py）。
    接続の失敗・5xx は retries 回まで送り直し、応答の時間切れは送り直さない。時間切れ・接続の失敗・5xx が続いた接続先は
    しばらく呼ばない（26B のハングで待ちが積み重ならないように）。deadline を過ぎたら送らずに失敗とする。
    """

    def __init__(self, base_url: str, model: str, timeout: float = 180.0, retries: int = 2, concurrency: int = 2,
                 deadline: Optional[float] = None, lock_dir: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.deadline = deadline
        self._endpoint = limits.endpoint(self.base_url, concurrency, lock_dir)
        self.calls = 0
        self.failures = 0
        self._lock = threading.Lock()

    def payload(self, prompt: str, png: bytes, max_tokens: int) -> dict:
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        return {"model": self.model, "temperature": 0, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                          {"type": "text", "text": prompt}]}]}

    def _post(self, payload: dict) -> dict:
        import httpx
        left = limits.remaining(self.deadline)
        timeout = self.timeout if left is None else max(1.0, min(self.timeout, left))
        with httpx.Client(timeout=timeout) as c:
            r = c.post(self.base_url + "/chat/completions", json=payload)
        r.raise_for_status()
        return r.json()

    def chat(self, prompt: str, png: bytes, max_tokens: int = 700) -> str:
        payload = self.payload(prompt, png, max_tokens)
        last = None
        for attempt in range(self.retries + 1):
            try:
                with self._endpoint.slot(self.deadline):
                    with self._lock:
                        self.calls += 1
                    try:
                        body = self._post(payload)
                    except Exception as error:
                        last = error
                        if limits.is_unavailable_error(error):
                            self._endpoint.failure()
                        if not limits.is_retryable(error):
                            break
                    else:
                        self._endpoint.success()
                        return ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            except limits.EndpointUnavailable as error:   # 遮断中・締め切り
                last = error
                break
            if attempt < self.retries:
                wait = 1.0 + 2.0 * attempt
                left = limits.remaining(self.deadline)
                if left is not None and left <= wait:
                    break
                time.sleep(wait)
        with self._lock:
            self.failures += 1
        raise RuntimeError(f"VLM {self.model} failed: {last}")


class FigureReader:
    """図の領域を判定し、説明文を作る。triage と describe は VlmClient（試験では差し替える）"""

    def __init__(self, triage: Optional[VlmClient], describe: Optional[VlmClient]):
        self.triage_client = triage
        self.describe_client = describe

    def triage(self, png: bytes, cls: str) -> tuple:
        """(種類, 扱い) を返す。種類が想定外のときだけモデルの扱いを使う"""
        raw = self.triage_client.chat(TRIAGE_PROMPT.format(cls=cls), png, max_tokens=200)
        tri = parse_json(raw)
        kind = str(tri.get("種類", "")).strip()
        action = ACTION_BY_KIND.get(kind) or str(tri.get("扱い", "")).strip() or "キャプションだけ残す"
        return kind, action

    def describe(self, png: bytes, kind: str, caption: str) -> str:
        prompt = DESCRIBE_PROMPT.get(kind, DESCRIBE_PROMPT["_default"]).format(cap=caption or "図")
        return clean_vlm_text(self.describe_client.chat(prompt, png, max_tokens=700))
