"""本文の文字の正規化と、見出しの段の決め方。

段は番号の形で決める（番号の形の正規表現と題の判定は number_headings.py）。
    章   1.  2.                → ##
    節   2-1  3-5              → ###       （題の頭の「.」は外す）
    点区切り 3.1 / 3.1.2 / 3.1.2.1 → ### / #### / #####（同じ文書に章 3 があるか、見出しの領域の行のときだけ）
    付属 別表2・付表・別紙1・添付資料3（字間の空白も許す） → ##（### だと章の下に入り、付属の節ができない）。
         語と番号の間のダッシュは残す（別紙-1。章・節の層の annex_key は「別紙-1」も「別紙1」と読む）
    表・注  表 1  注 1          → ###
    項 (1) → ####、号 1) → #####（「(1)」の下に置かれる番号。番号の形は書き換えない）、目 a) → ######
    番号の無い見出し（変換器が見出しと判定した領域の行）は、本文の枠の無いページに限り ## にする（枠のある本文のページでは見出しにしない）
    章・節・点区切りの題は、末尾の括弧書きの注記を外した題で判定してもよい（「2-5 外部振動計測 (…、測定値は…に記録)」の
    注記の中の「は」で文と判定しない。heading_title_ok）
    付属の見出しの後ろ（次の付属の見出しか、協議・通知票などの最上位の見出しまで）は、番号の見出し・番号の無い見出しを 1 段下げる
    （付属の中の「1.」は ###）。章・節の層の目録が付属の範囲を閉じず、「別表2」の節の下に付属の中の節が入るようにするため

見出しにしない行:
    来歴の章（「10. 基準来歴」）の中の行。記録の「1. S62.03.30 新規作成 担当 品管部 大槻」を章にしない。来歴の範囲は、
    次の付属の見出し・最上位の見出し（協議・通知票など）・来歴の章の次の番号の章（11.）・変換器が見出しと判定した領域の
    章の行（来歴の後ろに綴じた補足のページ）まで
    章の番号が直前の章より小さく戻る行（付属の見出し・最上位の見出しで数え直す）
    題が日付（S62.03.30・’04.03.26・H01…）か、記録の語（新規作成・制定・改訂・一部見直し…担当）で始まる行
    点区切りの番号が 0 で始まる成分を含む行（7.03.07.07 は日付）、章 0 の行（0.7 D は寸法）
    図の範囲から見出しの候補として出した行（strict）は、点区切り・節とも、その章が文書に既にあるときだけ見出しにする。
    見出しにならなかった候補は「図中の文字」に戻す

正規化: 全角の英数字・記号を半角に（＃｜＊＿～｀＜＞［］＼．！？ は残す）、全角空白を半角に、
半角カナを全角に、番号の中のハイフンの異体（−－‐ など）を半角の - に、バッククォートを消す。
行頭の # は \\# に、行頭の | は \\| に逃がし、行全体が [ ] で囲まれた行は \\[ にする（パンくずと取り違えない）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from ..number_headings import (CHAPTER, ITEM, NUMBERED, SECTION, SUBITEM, _is_heading_title)

# ---- 正規化 ----
_KEEP_FULLWIDTH = set("＃｜＊＿～｀＜＞［］＼．！？")
_FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F) if chr(code) not in _KEEP_FULLWIDTH}
_FULLWIDTH[0x3000] = 0x20
_HALFWIDTH_KANA = re.compile(r"[｡-ﾟ]+")
_DASHES = "‐‑‒–—―−➖－"
_DASH_IN_NUMBER = re.compile(r"(?<=[0-9A-Za-z])[%s](?=[0-9A-Za-z])" % _DASHES)
_LONG_VOWEL_IN_NUMBER = re.compile(r"(?<=[0-9A-Za-z])[ーｰ](?=[0-9A-Za-z])")


def normalize_text(text: str) -> str:
    text = (text or "").replace("`", "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_FULLWIDTH)
    text = _HALFWIDTH_KANA.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), text)
    text = _DASH_IN_NUMBER.sub("-", text)
    text = _LONG_VOWEL_IN_NUMBER.sub("-", text)
    text = text.replace("<!--", "<! --").replace("-->", "- ->")
    return re.sub(r"[ \t]+", " ", text)


_LEAD_HASH = re.compile(r"^(\s{0,3})([#|]|~~~)")
_BRACKET_LINE = re.compile(r"^\s*\[.*\]\s*$")


def escape_body_line(line: str) -> str:
    """見出しでない本文の行が、見出し・表・コードの囲み・パンくずと読まれないようにする"""
    line = _LEAD_HASH.sub(lambda m: m.group(1) + "\\" + m.group(2), line)
    if _BRACKET_LINE.match(line):
        line = line.replace("[", "\\[", 1)
    return line


# ---- 付属のラベル ----
ANNEX_WORD = r"(?:別\s*表|別\s*紙|付\s*表|附\s*表|付\s*図|附\s*図|別\s*図|添\s*付\s*資\s*料)"
# 語と番号の間のダッシュは捕まえておき、見出しに残す（「別紙-1」は「別紙-1」のまま。章・節の層の annex_key はどちらも「別紙1」と読む）
ANNEX_LINE = re.compile(r"^\s*(%s)\s*([-‐−－])?\s*(\d{1,2})(?!\d)\s*[.．:：]?\s*(.*)$" % ANNEX_WORD)
ANNEX_ANY = re.compile(r"(%s)\s*([-‐−－])?\s*(\d{1,2})?(?!\d)" % ANNEX_WORD)
TABLE_NOTE = re.compile(r"^\s*((表)\s*[-－]?\s*\d{1,2}|注\s*\d{0,2})(\s*\S.{0,38})?$")
DOTTED = re.compile(r"^\s*(\d{1,2})[.．](\d{1,2})((?:[.．]\d{1,2}){0,2})[.．]?\s*(\S.{0,38})$")
# 点区切りの番号の後に来ると小数や量（1.5 倍、2.6 mm）とみなす字
BAD_DOTTED_START = re.compile(r"^[倍%％°℃㎜mMkKgGsSlL秒分時回個本台枚点度φΦ×xX+\-=~～]")
_LEADER = re.compile(r"[・･.．…‥]{4,}")
PAGE_NUMBER = re.compile(r"^[\s\-–—()()0-9/／]{1,9}$")
DOC_NUMBER = re.compile(r"(?:HS[-\s]?)?([A-Z]{2,4})[-\s]?([A-Z]?)(\d{2,4})(?!\d)")


def annex_label(word: str, number: str = None, dash: str = None) -> str:
    """付属のラベル。字間の空白は詰め、語と番号の間のダッシュは半角の - にして残す（別 表 1 → 別表1、別紙−1 → 別紙-1）"""
    return re.sub(r"\s+", "", word) + ("-" if dash and number else "") + (number or "")


def doc_number_keys(text: str) -> set:
    """文書番号の核（系列, 番号の数字）。「HS-VTR-004 C」と「VTR-T004C」を同じとみなすため英字の接頭は外す"""
    return {(m.group(1), m.group(3).lstrip("0") or "0") for m in DOC_NUMBER.finditer(normalize_text(text or "").upper())}


def header_annex(text: str, own_number_keys: set = frozenset()):
    """柱の文字列が付属のラベル（別表2・別紙-1・添付資料）なら正規化したラベルを返す。違えば None。

    文書番号・括弧書き（V-00862による）だけが残るときに限る。自分のものでない文書番号を伴うラベル
    （QC-KAI-001 の別紙3 など、様式の出典）は返さない。
    """
    t = normalize_text(text).strip()
    m = ANNEX_ANY.search(t)
    if not m or len(t) > 40:
        return None
    rest = (t[:m.start()] + " " + t[m.end():])
    keys = doc_number_keys(rest)
    if keys and own_number_keys and not (keys & own_number_keys):
        return None
    rest = DOC_NUMBER.sub(" ", rest.upper())
    rest = re.sub(r"[(（][^()（）]*[)）]", " ", rest)
    rest = re.sub(r"\bHS\b|[\s\-‐./／:：|]+|\b[A-Z]\b", "", rest)
    if rest:
        return None
    return annex_label(m.group(1), m.group(3), m.group(2))


def header_notes(text: str) -> list:
    """柱の文字列のうち、括弧書きの注記（「(V-00862による)」）。付属のラベルと同じ柱に入っていても本文に残す"""
    t = normalize_text(text)
    return [re.sub(r"\s+", " ", m.group(0)).strip() for m in re.finditer(r"[(（][^()（）]{2,}[)）]", t)]


# ---- 見出しの段 ----
FIGURE_TEXT_LABEL = "図中の文字: "


@dataclass
class FigureText:
    """図中の文字の行。見出しにならなかった候補（strict の行）を、出すときに頭に足す"""
    rest: str
    pending: list = field(default_factory=list)

    def render(self, label: str) -> str:
        body = " ".join(t for t in self.pending + [self.rest] if t and t.strip()).strip()
        return f"{label}{body}" if body else ""


@dataclass
class Line:
    text: str
    hint: bool = False      # 変換器が見出し（Section-header・Title・番号で始まる Caption）と判定した領域の行
    kind: str = "text"      # text | final（そのまま出す行: 表・図の説明・組み立て済みの見出し）| blank | figure（図中の文字）
    annex: bool = False     # 組み立て済みの付属の見出し（ここから付属の範囲に入る）
    root: bool = False      # 組み立て済みの最上位の見出し（協議・通知票など。付属の範囲を終える）
    strict: bool = False    # 図の範囲から出した見出しの候補（章が文書にあるときだけ見出しにする）
    figure: Optional[FigureText] = None   # strict の行が見出しにならなかったときに戻す先（無ければ捨てる）


@dataclass
class DocState:
    chapters: set = field(default_factory=set)
    annex: bool = False     # 付属（別表・添付資料など）の見出しの後ろにいるか
    control: Optional[int] = None   # 来歴の章の中にいるとき、その章の番号（番号の無い来歴の見出しは 0）
    last_chapter: int = 0   # 直前の章の番号（付属・最上位の見出しで 0 に戻す）

    def reset_scope(self, annex: bool) -> None:
        self.annex = annex
        self.control = None
        self.last_chapter = 0


def _clean_title(title: str) -> str:
    return re.sub(r"^[.．、,，:：\s]+", "", (title or "").strip())


# 来歴の章の題
CONTROL_TITLE = re.compile(r"来\s*歴|見直し確認")
# 記録の行の題: 日付（S62.03.30・H01.3.15・’04.03.26）で始まる、記録にしか使わない語（新規作成・一部見直し）で始まる、
# 記録の語（制定・改訂・追加…）で始まり「担当 …」か日付を含む、のいずれか
RECORD_DATE = re.compile(r"^\s*(?:[HSR]\s*\d{1,2}\s*[./．]\s*\d|[’'`]\s*\d{2}\s*[./．]\s*\d)")
RECORD_ONLY = re.compile(r"^\s*(?:新\s*規\s*(?:作\s*成|制\s*定|調\s*整)|[一全]\s*[部面]\s*(?:見\s*直|改\s*[訂正定]))")
RECORD_VERB = re.compile(r"^\s*(?:制\s*定|改\s*[訂定正]|見\s*直\s*し|追\s*加|廃\s*止|統\s*合|移\s*行)")
RECORD_TAIL = re.compile(r"\S\s+担\s*当\s+\S")
ANY_DATE = re.compile(r"(?:[HSR]\s*\d{1,2}|[’'`]\s*\d{2}|\d{4})\s*[./．]\s*\d{1,2}\s*[./．]\s*\d{1,2}")


def _record_title(title: str) -> bool:
    """来歴の記録の行の題か"""
    t = title or ""
    if RECORD_DATE.match(t) or RECORD_ONLY.match(t):
        return True
    return bool(RECORD_VERB.match(t) and (RECORD_TAIL.search(t) or ANY_DATE.search(t))) or bool(RECORD_TAIL.search(t) and ANY_DATE.search(t))


_TRAILING_NOTE = re.compile(r"\s*[(（][^()（）]*[)）]\s*$")


def heading_title_ok(title: str, item: bool = False) -> bool:
    """題として成り立つか（number_headings._is_heading_title）。成り立たなくても、末尾の括弧書きの注記を外した題が成り立てばよい
    （「外部振動計測 (VTR形過給機に適用、測定値は組付チェックシートに記録)」。注記の中の「は」で文と判定しない）"""
    if _is_heading_title(title, item):
        return True
    core = _TRAILING_NOTE.sub("", title or "")
    return bool(core.strip()) and core != title and _is_heading_title(core, item)


def heading_number_line(text: str) -> bool:
    """行頭が章・節・点区切りの番号で、その行だけで見出しの題になる行か。textlayer.paragraph_text は、この行に次の行をつなげない
    （「2-5 外部振動計測 (…)」に次の段落の「振動計測は、…」がつながって見出しにならない例）。括弧が閉じていない行
    （題が次の行に折り返している）は違うとする"""
    s = normalize_text(text).strip()
    if s.count("(") + s.count("（") > s.count(")") + s.count("）"):
        return False
    m = SECTION.match(s)
    if m:
        title = _clean_title(m.group(3))
        return bool(title) and heading_title_ok(title) and not title.startswith(("項", "号", "条")) and not _record_title(title)
    m = DOTTED.match(s)
    if m:
        parts = _dotted_parts(m)
        title = _clean_title(m.group(4))
        return (m.group(1) != "0" and not any(len(p) > 1 and p.startswith("0") for p in parts[1:]) and bool(title)
                and heading_title_ok(title) and not BAD_DOTTED_START.match(title) and not _record_title(title))
    m = CHAPTER.match(s)
    if m:
        title = _clean_title(m.group(2))
        return bool(title) and heading_title_ok(title) and not _record_title(title)
    return False


def _dotted_parts(m) -> list:
    return [m.group(1), m.group(2)] + [p for p in re.split(r"[.．]", m.group(3) or "") if p]


def promote_line(line: str, nxt: str, hint: bool, framed: bool, state: DocState, strict: bool = False):
    """1 行を見出しにできれば ((段, 見出しの文字列, 種類), 次の行を使ったか) を返す。できなければ (None, False)。

    種類は annex（付属の見出し）| number（番号の見出し）| plain（番号の無い見出し）| control（来歴の章）。
    段は付属の範囲での下げる前の段。strict は図の範囲から出した候補（章が文書にあるときだけ見出しにする）。
    """
    s = line.strip()
    if not s or s.startswith("#") or s.startswith("|"):
        return None, False
    if _LEADER.search(s):
        return None, False   # 点線（リーダー）の入った行は目次や箇条の行で、見出しではない
    in_control = state.control is not None
    m = ANNEX_LINE.match(s)
    if m and not strict and (hint or not in_control):
        rest = _clean_title(m.group(4))
        if not rest or (_is_heading_title(rest) and not _record_title(rest)):
            label = annex_label(m.group(1), m.group(3), m.group(2))
            return (2, f"{label} {rest}".rstrip(), "annex"), False
    if in_control:
        # 来歴の章の中。来歴の章の次の番号の章（来歴が 10. なら 11.）か、変換器が見出しと判定した領域の章の行
        # （来歴の後ろに綴じた補足のページの「1.型式・スペック及び抜取頻度」）だけを見出しにして、来歴の範囲を終える
        m = CHAPTER.match(s)
        if m and (m.group(2) or "").strip() and heading_title_ok(m.group(2)) and not _record_title(m.group(2)):
            number = int(m.group(1))
            if (state.control and number == state.control + 1) or (hint and not strict):
                state.control = None
                state.chapters.add(m.group(1))
                state.last_chapter = number
                return (2, f"{m.group(1)}. {_clean_title(m.group(2))}", "number"), False
        return None, False
    m = TABLE_NOTE.match(s)
    if m and not strict and _is_heading_title(m.group(3) or ""):
        return (3, s, "number"), False
    m = DOTTED.match(s)
    if m:
        chapter = m.group(1)
        parts = _dotted_parts(m)
        if chapter == "0" or any(len(p) > 1 and p.startswith("0") for p in parts[1:]):
            # 0.7 D（寸法）や 7.03.07.07（日付）は番号ではない
            return None, False
        title = _clean_title(m.group(4))
        known = chapter in state.chapters
        if (title and heading_title_ok(title) and not BAD_DOTTED_START.match(title) and not _record_title(title)
                and (known or (hint and not strict))):
            number = f"{m.group(1)}.{m.group(2)}{m.group(3).replace('．', '.')}"
            return (2 + number.count("."), f"{number} {title}", "number"), False
        if known or hint:
            # 小数や量の行（1.5 倍）。章と読まないよう、ここで打ち切る
            return None, False
    # 節。CHAPTER（番号の後が .）とは同時に当たらないので、章より先に見てよい。strict の候補は章が文書にあるときだけ。
    # 「1-1項と別表の摘要欄…」のように番号の後が「項」で始まる行は、節の見出しではなく参照の文
    m = SECTION.match(s)
    title = _clean_title(m.group(3)) if m else ""
    if (m and title and heading_title_ok(title) and not title.startswith(("項", "号", "条"))
            and (not strict or m.group(1) in state.chapters)):
        return (3, f"{m.group(1)}-{m.group(2)} {title}", "number"), False
    if strict:
        return None, False
    m = CHAPTER.match(s)
    if m:
        title, used = None, False
        if (m.group(2) or "").strip() and heading_title_ok(m.group(2)):
            title = _clean_title(m.group(2))
        elif not (m.group(2) or "").strip() and nxt and _is_heading_title(nxt.strip()):
            title, used = nxt.strip(), True
        if title and not _record_title(title):
            number = int(m.group(1))
            if number < state.last_chapter:
                return None, False   # 章の番号が戻る行は、箇条や記録の番号
            state.chapters.add(m.group(1))
            state.last_chapter = number
            if CONTROL_TITLE.search(title):
                state.control = number
                return (2, f"{m.group(1)}. {title}", "control"), used
            return (2, f"{m.group(1)}. {title}", "number"), used
    m = ITEM.match(s)
    if m and m.group(2) and _is_heading_title(m.group(2), item=True):
        return (4, f"({m.group(1)}) {m.group(2).strip()}", "number"), False
    m = NUMBERED.match(s)
    if m and m.group(2) and _is_heading_title(m.group(2), item=True):
        return (5, f"{m.group(1)}) {m.group(2).strip()}", "number"), False
    m = SUBITEM.match(s)
    if m and m.group(2) and _is_heading_title(m.group(2), item=True):
        return (6, f"{m.group(1)}) {m.group(2).strip()}", "number"), False
    if hint and not framed and 2 <= len(s) <= 40 and not PAGE_NUMBER.match(s) and _unnumbered_heading(s):
        if CONTROL_TITLE.search(s):
            state.control = 0
            return (2, s, "control"), False
        return (2, s, "plain"), False
    return None, False


_UNNUMBERED_BAD = re.compile(r"[。．]$|^[\d０-９(（]")


def _unnumbered_heading(s: str) -> bool:
    if _UNNUMBERED_BAD.search(s) or not re.search(r"[^\W\d_]", s):
        return False
    if DOC_NUMBER.fullmatch(s.replace(" ", "")):
        return False
    return True


def heading_markdown(level: int, text: str, kind: str, state: DocState) -> str:
    """付属の範囲では、番号の見出しと番号の無い見出しを 1 段下げる（付属の見出し ## の下に入れる）。

    章・節の層の目録は、付属の見出しと同じ段かそれより浅い見出しが来ると付属の範囲を閉じる。付属の中の「1.」を ## のままにすると
    範囲が閉じ、本文の章の付け直しと読まれて「別表2」の節が作られない。
    """
    if kind == "annex":
        state.reset_scope(annex=True)
        return f"## {text}"
    if state.annex:
        level += 1
    return f"{'#' * min(level, 6)} {text}"


def render_lines(lines, framed: bool, state: DocState) -> str:
    """ページの行の並びを Markdown にする。見出しにできる行は見出しに、それ以外の本文の行は逃がす。"""
    out = []
    skip = False
    for i, ln in enumerate(lines):
        if skip:
            skip = False
            continue
        if ln.kind == "blank":
            out.append("")
            continue
        if ln.kind == "figure":
            text = ln.figure.render(FIGURE_TEXT_LABEL) if ln.figure else ""
            if text:
                out.append(escape_body_line(text))
            continue
        if ln.kind == "final":
            if ln.annex:
                state.reset_scope(annex=True)
            elif ln.root:
                state.reset_scope(annex=False)
            out.append(ln.text)
            continue
        nxt = lines[i + 1].text if i + 1 < len(lines) and lines[i + 1].kind == "text" else ""
        heading, used = promote_line(ln.text, nxt, ln.hint, framed, state, strict=ln.strict)
        if heading:
            out.append(heading_markdown(*heading, state))
            skip = used
        elif ln.strict:
            # 図の範囲から出した候補で見出しにならなかった行は、図中の文字に戻す（戻す先が無ければ捨てる）
            if ln.figure is not None:
                ln.figure.pending.append(ln.text)
        else:
            out.append(escape_body_line(ln.text))
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
