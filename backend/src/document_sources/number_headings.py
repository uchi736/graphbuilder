"""番号見出しの規則。PDF から変換した Markdown（見出しの印が付いていない）に、行頭の番号の形で見出しの印を付ける。

社内規格に共通の形だけを見る。文書ごとの規則は持たない。
    章   1.  2.  3.           → ##
    節   2-1  3-5             → ###
    別表・注  別表 1  注 1     → ###
    項   (1)                  → ####
    号   1)                   → #####（「(1)」の下に置かれる番号。番号の形は書き換えない）
    目   a.  a)  b)           → ######
項と目は題が短いので細切れになりやすいが、分割のあとで 80 トークン未満の節を同じ章の前の節に寄せるので、数は増えない。
見出しとみなす条件: 題が 40 字以内で字を含み、数字や助詞で始まらず、句点で終わらず、助詞（を・は・が）や連用の形を含まないこと。
文の形の項目（「(1) 羽根付け根 R 部はいかなる欠陥も許容しない。」）は見出しにしない。
"""
import re

CHAPTER = re.compile(r"^\s*(\d{1,2})[.．]\s*(\S.{0,38})?$")
SECTION = re.compile(r"^\s*(\d{1,2})[-－](\d{1,2})\s*(\S.{0,38})?$")
TABLE_NOTE = re.compile(r"^\s*((別表|別紙|付表|表)\s*[-－]?\s*\d{1,2}|注\s*\d{0,2})(\s*\S.{0,38})?$")
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


def promote_number_headings(text: str) -> str:
    """行頭の番号の形を Markdown の見出しに直す。既に見出しの印がある行、表の行、囲みの中は触らない。"""
    out = []
    lines = text.split("\n")
    fence = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = not fence
            out.append(line)
            continue
        if fence or stripped.startswith("#") or stripped.startswith("|") or not stripped:
            out.append(line)
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        m = CHAPTER.match(stripped)
        if m and (m.group(2) or "").strip() and _is_heading_title(m.group(2)):
            out.append(f"## {m.group(1)}. {m.group(2).strip()}")
            continue
        if m and not (m.group(2) or "").strip() and nxt and _is_heading_title(nxt) and not stripped.startswith("|"):
            # 番号だけの行の次の行が題（「3.」の次の行に「試運転判定基準」）
            out.append(f"## {m.group(1)}. {nxt}")
            lines[i + 1] = ""
            continue
        m = SECTION.match(stripped)
        if m and (m.group(3) or "").strip() and _is_heading_title(m.group(3)):
            out.append(f"### {m.group(1)}-{m.group(2)} {(m.group(3) or '').strip()}".rstrip())
            continue
        m = TABLE_NOTE.match(stripped)
        if m and _is_heading_title(m.group(3) or ""):
            out.append(f"### {stripped}")
            continue
        m = ITEM.match(stripped)
        if m and m.group(2) and _is_heading_title(m.group(2), item=True):
            out.append(f"#### ({m.group(1)}) {m.group(2).strip()}")
            continue
        m = NUMBERED.match(stripped)
        if m and m.group(2) and _is_heading_title(m.group(2), item=True):
            out.append(f"##### {m.group(1)}) {m.group(2).strip()}")
            continue
        m = SUBITEM.match(stripped)
        if m and m.group(2) and _is_heading_title(m.group(2), item=True):
            out.append(f"###### {m.group(1)}) {m.group(2).strip()}")
            continue
        out.append(line)
    return "\n".join(out)


def is_control_heading(path) -> bool:
    """見出しの経路（文字列の並び）が、改正来歴や協議・通知票などの管理の部分を指すか。"""
    return any(CONTROL.search(h or "") for h in (path or []))
