"""番号見出しの規則（行頭の番号の形の正規表現と、題として成り立つかの判定）。
見出しの段の決め方は pdf_regions/headings.py（promote_line）が、これらの規則を使って行う。

社内規格に共通の形だけを見る。文書ごとの規則は持たない。
    章 1.  節 2-1  項 (1)  号 1)  目 a.  a)
見出しとみなす条件: 題が 40 字以内で字を含み、数字や助詞で始まらず、句点で終わらず、助詞（を・は・が）や連用の形を含まないこと。
文の形の項目（「(1) 羽根付け根 R 部はいかなる欠陥も許容しない。」）は見出しにしない。
"""
import re

CHAPTER = re.compile(r"^\s*(\d{1,2})[.．]\s*(\S.{0,38})?$")
SECTION = re.compile(r"^\s*(\d{1,2})[-－](\d{1,2})\s*(\S.{0,38})?$")
ITEM = re.compile(r"^\s*[（(](\d{1,2})[)）]\s*(\S.{0,38})?$")          # (1)
NUMBERED = re.compile(r"^\s*(\d{1,2})[)）]\s*(\S.{0,38})?$")            # 1)
SUBITEM = re.compile(r"^\s*([a-z])[.)）]\s*(\S.{0,38})?$")
SENTENCE_END = re.compile(r"[。．.」）)]\s*$")
CONTROL = re.compile(r"(基準来歴|改正来歴|来歴|協議・通知票|協議\s*・\s*通知|見直し確認欄|承認|審査)")


LETTER = re.compile(r"[^\W\d_]")          # 字（数字と記号だけの題は見出しにしない。1.5 や 2.6 のような小数を章と読まないため）
BAD_START = re.compile(r"^[\d０-９のをはがにとでもや、。・～~\-]")   # 数字や助詞で始まる題は、小数（0.7D）か折り返した文の続き（2.の規格を満足した場）
SENTENCE_SIGN = re.compile(r"[をはが]|し、|して|また、")


def _is_heading_title(title: str, item: bool = False) -> bool:
    """題として成り立つか。40 字以内、字を含む、数字や助詞で始まらない、文末が句点でない、助詞や連用の形（文の途中）を含まない。項・目は 30 字以内。"""
    title = (title or "").strip()
    if not title or len(title) > 40 or not LETTER.search(title) or BAD_START.match(title):
        return False
    if SENTENCE_END.search(title) and not title.endswith(("）", ")")):
        return False
    if SENTENCE_SIGN.search(title):
        return False
    if item and len(title) > 30:
        return False
    return True


def is_control_heading(path) -> bool:
    """見出しの経路（文字列の並び）が、改正来歴や協議・通知票などの管理の部分を指すか。"""
    return any(CONTROL.search(h or "") for h in (path or []))
