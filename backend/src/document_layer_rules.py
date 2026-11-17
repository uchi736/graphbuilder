# -*- coding: utf-8 -*-
"""文書層（文書番号・版・目録・廃止・適用先）と章・節の層の規則。LLM もグラフも呼ばない。標準ライブラリだけで動く。

文書番号の形（HS-VTR-T001 など）はこのファイルには書かず、設定 document_numbers.json（backend 直下）から読む。

使い方:
    cfg = load_config()
    normalize_number("ＨＳ ＶＴＲ－Ｔ００１E", cfg)   -> ("HS-VTR-T001", "E")
    number_of_file("VTR-T004C.pdf", cfg)            -> ("HS-VTR-T004", "C")
    parse_catalog(text, cfg)                         -> 発行目録の行 [{"number", "name", "revisions": [(符号, 日付)], "latest_rev", "latest_date"}]
    parse_history(text, cfg)                         -> 基準来歴の行 [{"rev", "date", "note"}]
    parse_notice(text, cfg)                          -> 協議・通知票 {"kind": 改訂|廃止|新制, "number", "old_rev", "new_rev", "date"} または None
    parse_applies(text, cfg)                         -> 「1. 適用」から拾った型式の語 ["AT", "RH", "RU"]
    heading_form(title)                              -> ("章", "1") / ("節", "3-1") / ("項", "2") / ("号", "1") / ("目", "a") / (None, None)
    parse_locator("上記2-1及び2-2項")                -> 参照の位置の語から読んだ番号 [{"form": "節", "number": "2-1", ...}, {"form": "節", "number": "2-2", ...}]
    numbers_after_document_numbers("HS-VTR-T001の3-5項に依る", cfg) -> 文書番号の直後の番号 [("HS-VTR-T001", {"form": "節", "number": "3-5", ...})]
    annex_key("別表2 過給機性能試験") / annex_key("別紙-1")  -> 付属の題名の先頭の印 "別表2" / "別紙1"（無ければ None）
"""
import json
import os
import re
import unicodedata

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "document_numbers.json")

_DATE = r"(?:'?\d{2,4}[./]\s?\d{1,2}[./]\s?\d{1,2}|[HS]\d{1,2}[./]\s?\d{1,2}[./]\s?\d{1,2})"
_REV = r"[0A-Z]"


def load_config(path=None):
    path = path or os.environ.get("DOCUMENT_NUMBERS_CONFIG") or DEFAULT_CONFIG
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_series"] = [(s["name"], re.compile(s["match"]), s["canonical"], s.get("rev_group")) for s in cfg.get("series", [])]
    return cfg


def nfkc(text):
    return unicodedata.normalize("NFKC", text or "")


def squeeze(text):
    """NFKC にし、全角の記号を半角に寄せ、空白を消す（番号の照合用）。"""
    t = nfkc(text).replace("―", "-").replace("‐", "-").replace("–", "-")   # 全角の「－」は NFKC で「-」になる
    return re.sub(r"\s+", "", t).upper()


def _canonical(m, canonical, rev_group):
    """系列の正規表現の一致から (正規形, 版の符号)。"""
    canon = canonical
    for i in range(1, (m.lastindex or 0) + 1):
        canon = canon.replace("{%d}" % i, m.group(i) or "")
    rev = m.group(rev_group) if rev_group and m.lastindex and m.lastindex >= rev_group else None
    return canon, (rev or None)


def _series_matches(s, cfg):
    """系列ごとに、文字列 s の中の文書番号の一致を順に返す（[(一致, 正規形, 版の符号)]。系列の順、同じ系列の中は出現の順）。"""
    for _name, rx, canonical, rev_group in cfg["_series"]:
        for m in rx.finditer(s):
            yield (m,) + _canonical(m, canonical, rev_group)


def normalize_number(text, cfg, search=True, strict=False):
    """文書番号の表記 -> (正規形, 版の符号)。系列に当たらなければ (None, None)。
    search=True なら文字列のどこにあっても拾う（「D-C-HS-VTR-T003」のような前置きの混じった識別子も通す）。
    strict=True なら文字列全体が番号（＋版）でなければならない（名寄せの判定用。「REQ-VTR-T001-1a-1」を寄せない）。"""
    s = squeeze(text)
    if not s:
        return None, None
    for _name, rx, canonical, rev_group in cfg["_series"]:
        m = rx.fullmatch(s) if strict else (rx.search(s) if search else rx.match(s))
        if m:
            return _canonical(m, canonical, rev_group)
    return None, None


def numbers_in(text, cfg):
    """文字列に現れる文書番号の正規形を、重複なく返す（[(正規形, 版の符号), ...]）。目録や一覧のように複数並ぶかを見るのに使う。
    空白をすべて詰めてから探す（document_numbers_in と違い、数字の間の空白でも番号をつなげる）。"""
    out, seen = [], set()
    for _m, canon, rev in _series_matches(squeeze(text), cfg):
        if canon not in seen:
            seen.add(canon)
            out.append((canon, rev))
    return out


def is_catalog_file(file_name, head, cfg):
    """発行目録のファイルか（ファイル名か、先頭の見出しで判定する）。目録はどの文書の版でもない。"""
    return cfg["catalog_file_pattern"] in (file_name or "") or cfg["catalog_heading_pattern"] in (head or "")[:200]


def number_of_file(file_name, cfg):
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", file_name or "")
    return normalize_number(stem, cfg, search=False)


def external_id(text):
    """手元に無い文書の識別子の表示形。大文字、空白は 1 つに、先頭の英字と数字の間のハイフンは落とす（V-00862 → V00862）。
    空白やハイフンの位置だけが違う表記（「HZTL 20559」と「HZTL20559」）は、この形では同じにならない。表記ゆれの突き合わせは external_key で行い、
    同じ鍵の表記からどれを Standard の id にするかは document_layer.pick_external_ids が決める。"""
    t = re.sub(r"\s+", " ", nfkc(text)).strip().upper()
    return re.sub(r"^([A-Z])-(?=\d)", r"\1", t)


def is_external_identifier(text, cfg):
    """手元に無い文書として概念を作ってよい識別子か（JIS B 8340、QC-KAI-027、V00858 など）。法令名のような語は作らない。"""
    s = squeeze(text)
    if not s or len(s) > 40:
        return False
    for p in cfg.get("external_prefixes", []):
        if s.upper().startswith(p.upper()):
            return bool(re.search(r"\d{%d,}" % int(cfg.get("external_min_digits", 3)), s))
    return False


def _rev_date_pairs(text):
    """「A 98.5.9 B 02.06.12 C D」や「59.1.11 / A / 99.2.2 / B / ...」から (符号, 日付) の組を順に返す。日付の無い符号は (符号, None)。"""
    toks = [t for t in re.split(r"\s*/\s*|\s+", nfkc(text).strip()) if t]
    out = []
    i = 0
    while i < len(toks):
        t = toks[i]
        m = re.match(r"^(%s)(%s)$" % (_REV, _DATE), t)   # 「B01.09.21」のように符号と日付が付いたもの
        if m:
            out.append((m.group(1), m.group(2).lstrip("'")))
            i += 1
            continue
        if re.match(r"^%s$" % _REV, t):
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if re.match(r"^%s$" % _DATE, nxt):
                out.append((t, nxt.lstrip("'")))
                i += 2
            else:
                out.append((t, None))
                i += 1
            continue
        if re.match(r"^%s$" % _DATE, t) and not out:
            out.append(("0", t.lstrip("'")))   # 先頭の日付は初版（符号 0）
            i += 1
            continue
        i += 1
    return out


def _latest(pairs):
    dated = [(r, d) for r, d in pairs if d]
    return dated[-1] if dated else (None, None)


def parse_catalog(text, cfg):
    """発行目録の本文から行を読む。2 つの書式に対応する。
    (1) 「番号 / 名称 / 日付 / 符号 / 日付 / ...」（行ごと、Markdown 変換後）
    (2) 「HS-VTR 名称前半 A 日付 B 日付 C D ... -T001 名称後半 L M N ...」（字数分割で崩れた表）"""
    t = nfkc(text)
    rows = []
    # (1) 行ごとの書式
    for line in t.splitlines():
        if " / " not in line:
            continue
        parts = [p.strip() for p in line.split(" / ")]
        number, _ = normalize_number(parts[0], cfg, search=False)
        if not number or len(parts) < 2:
            continue
        name = parts[1]
        if re.match(r"^[A-Z]$", name):   # 名称なし（空き番号）
            continue
        pairs = _rev_date_pairs(" / ".join(parts[2:]))
        lr, ld = _latest(pairs)
        rows.append({"number": number, "name": name, "revisions": pairs, "latest_rev": lr, "latest_date": ld})
    if rows:
        return rows
    # (2) 崩れた表の書式: 系列の接頭（HS-VTR など）で区切り、区切りごとに「-T001」の形の番号を探す
    prefixes = sorted({s["canonical"].split("{")[0].rstrip("-") for s in cfg.get("series", [])}, key=len, reverse=True)
    for pre in prefixes:
        segs = re.split(r"(?=(?<![A-Z0-9-])%s(?![A-Z0-9]))" % re.escape(pre), t)
        for seg in segs:
            if not seg.startswith(pre):
                continue
            m = re.search(r"-?([A-Z]?\d{3})(?![0-9])", seg[len(pre):])
            if not m:
                continue
            number, _ = normalize_number(pre + "-" + m.group(1), cfg, search=False)
            if not number:
                continue
            head = seg[len(pre):m.start() + len(pre)]
            tail = seg[m.end() + len(pre):]
            first_rev = re.search(r"(?<![A-Z])%s\s+%s" % (_REV, _DATE), head)
            name1 = head[:first_rev.start()].strip(" -") if first_rev else head.strip(" -")
            name2 = re.split(r"\s(?=(?:[A-Z](?:\s|$))+)", tail, maxsplit=1)[0].strip()
            name2 = re.sub(r"(?:^|\s)(?:[A-Z]\s*)+$", "", name2).strip()
            pairs = _rev_date_pairs(head[first_rev.start():] if first_rev else "")
            tail_rev = re.search(r"(?<![A-Z])%s\s+%s" % (_REV, _DATE), tail)
            if tail_rev:
                pairs += _rev_date_pairs(tail[tail_rev.start():])
            lr, ld = _latest(pairs)
            name = " ".join(x for x in (name1, name2) if x).strip()
            if re.match(r"^\d+$", name) or not name:
                name = None
            rows.append({"number": number, "name": name, "revisions": pairs, "latest_rev": lr, "latest_date": ld})
    return rows


def parse_history(text, cfg):
    """基準来歴の「A改正:02.06.12 一部見直し」「Ａ改正：96. 1.20全面見直し」を順に読む。"""
    t = nfkc(text)
    out = []
    for m in re.finditer(r"(%s)\s*改正\s*[:：]?\s*(%s)\s*(.*?)(?=\s*%s\s*改正|\n|$)" % (_REV, _DATE, _REV), t):
        note = re.split(r"\s{2,}|担当", m.group(3))[0].strip()
        out.append({"rev": m.group(1), "date": re.sub(r"\s", "", m.group(2)).lstrip("'"), "note": note[:60]})
    return out


def parse_notice(text, cfg):
    """協議・通知票の「■ 改訂 ... HS-RU-001 B C」「■ 廃止 HS-RU-012 0 廃止」を読む。無ければ None。"""
    t = nfkc(text)
    kind = None
    if re.search(r"■\s*廃\s*止", t):
        kind = "廃止"
    elif re.search(r"■\s*改\s*訂", t):
        kind = "改訂"
    elif re.search(r"■\s*新\s*制", t):
        kind = "新制"
    if not kind:
        return None
    mark = re.search(r"■\s*(?:廃\s*止|改\s*訂|新\s*制)", t)
    m = re.search(r"([A-Z][A-Z0-9-]{2,}\d{3})\s+(%s)(?:\s+(%s))?(?=\s|$)" % (_REV, _REV), t[mark.end():]) if mark else None
    if not m:
        return None
    number, _ = normalize_number(m.group(1), cfg)
    if not number:
        return None
    date = re.search(r"(\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", t)
    old, new = (m.group(2), m.group(3)) if m.group(3) else (None, m.group(2))
    if kind == "廃止":
        old, new = new, None
    return {"kind": kind, "number": number, "old_rev": old, "new_rev": new,
            "date": "%s.%s.%s" % (date.group(1), date.group(2).zfill(2), date.group(3).zfill(2)) if date else None}


def scope_text(text, cfg):
    """「1. 適用」の節の本文だけを切り出す（次の章の見出しまで）。無ければ None。"""
    t = nfkc(text)
    m = re.search(r"(?:^|\n|\s)1\s*[.．]\s*適\s*用\s*", t)
    if not m:
        return None
    rest = t[m.end():]
    end = re.search(r"(?:^|\n|\s)2\s*[.．]\s*[^\s\d]", rest)
    return rest[:end.start()] if end else rest[:600]


def parse_applies(text, cfg):
    """適用先の型式（RU・RH・RH3・AT・TPL・TPS・VTR4 など）を「…形過給機」の直前から拾う。"""
    scope = scope_text(text, cfg)
    if not scope:
        return []
    models = set()
    suffix = cfg.get("model_suffix", "(?:形|型)(?:舶用)?過給機")
    for m in re.finditer(r"([A-Z][A-Z0-9・、,/.\s\"”]*?)\s*" + suffix, scope):
        for tok in re.findall(r"[A-Z]{2,}[0-9]*|[A-Z]{2}\d+", m.group(1)):
            models.add(tok)
    return sorted(models)


_HEADING_NUMS = [("節", re.compile(r"^(\d+)-(\d+)(?:-\d+)?\s*[.．]?\s*(.*)")), ("章", re.compile(r"^(\d+)\s*[.．]\s*(.*)")),
                 ("項", re.compile(r"^\((\d+)\)\s*(.*)")), ("号", re.compile(r"^(\d+)\)\s*(.*)")), ("目", re.compile(r"^([a-z])\)\s*(.*)"))]


def heading_form(title):
    """見出しの文字列から (形, 番号)。「3-1 .サージ誤差計算」-> ("節", "3-1")、「2. 単独性能確認試験要領」-> ("章", "2")。"""
    t = nfkc(title or "").strip()
    for form, rx in _HEADING_NUMS:
        m = rx.match(t)
        if m:
            num = m.group(1) if form != "節" else "%s-%s" % (m.group(1), m.group(2))
            return form, num
    return None, None


def is_control_heading(title, cfg):
    t = nfkc(title or "")
    return any(k in t for k in cfg.get("control_headings", []))


# ---- 参照の位置の語（locator）と参照の一文から番号を読む（章・節の層の後処理 E9 と、文書層の一文の節番号 M3 で使う）----
# ハイフンの異体のうち NFKC で「-」にならないもの（U+2010・U+2011・U+2012・U+2013・U+2014・U+2015・U+2212・U+2796・U+FF0D）。
# PDF の文字の取り出しや OCR では、番号の間のハイフンがこれらの字や長音記号（ー）になることがある。読めないと「3−5項」の「5項」だけを章 5 と読む
_DASHES = "‐‑‒–—―−➖－"
# 長音記号（ー・半角のｰ）。片仮名の語に使うので、英数字に挟まれたときだけハイフンとみなす
_LONG_VOWELS = "ーｰ"
_DASH = r"[\-%s%s]" % (_DASHES, _LONG_VOWELS)


def unify_dashes(text):
    """ハイフンの異体を「-」にそろえる。長音記号は英数字に挟まれたときだけ「-」にする。字数は変えない（位置の対応表を崩さない）。"""
    t = re.sub("[%s]" % _DASHES, "-", text or "")
    return re.sub(r"(?<=[0-9A-Za-z])[%s](?=[0-9A-Za-z])" % _LONG_VOWELS, "-", t)


# 付属の資料を指す語。番号を伴うときだけ付属の節として引く（「別表2」「別紙-1」「付表6」）。「添付資料」だけでは番号が無いので読まない
_ANNEX_WORDS = r"別表|別紙|付表|附表|付図|附図|別図|添付資料"
_ANNEX_RX = re.compile(r"(?P<w>%s)\s*%s?\s*(?P<n>\d{1,2})(?!\d)" % (_ANNEX_WORDS, _DASH))
_RANGE = r"\s*[~〜～]\s*"
# 節・項の番号（2-1、3-1-2）。前後に数字・小数点・ハイフンが続くものは除く（日付や枝番の一部を拾わない）
_SEC_RX = re.compile(r"(?<![\d.\-])(\d{1,2})-(\d{1,2})(?:-(\d{1,2}))?(?![\d.]|-\d)")
# 章の番号（「2項」「上記2項」「第3章」「3章」）。「3-5項」の「5項」は節の番号の一部なので読まない。
# 「項目」（「上記3項目」）は番号ではないので読まない。「第2項」は条の中の項（「第3条第2項」）なので章とみなさない（読んだ上で捨てる）
_CHAP_RX = re.compile(r"(?<![\d.\-])(?P<dai>第\s*)?(?P<n>\d{1,2})\s*(?P<w>項(?!目)|章)")
_CHAP_RANGE_RX = re.compile(r"(?<![\d.\-])(\d{1,2})" + _RANGE + r"(\d{1,2})\s*(?:項(?!目)|章)")
# 項（「(2)」）。かっこの内側の空白（「( 2 )」）も許す
_PARA_RX = re.compile(r"\(\s*(\d{1,2})\s*\)")
# 号（「3)」）。「(2)」の閉じかっこは除く（項を先に読むので、「( 2 )」も号にはならない）
_ITEM_RX = re.compile(r"(?<![\d(\-])(\d{1,2})\s*\)")


def _blank_codes(text):
    """文書番号や識別子（英字で始まり数字を含む、ハイフンでつながった語。HS-VTR-T001、V-00862、QC-KAI-027）を同じ長さの空白にする。
    番号の読み取りで「T001-5」の「001-5」などを節の番号と取り違えないため。"""
    def blank(m):
        return " " * len(m.group(0)) if re.search(r"\d", m.group(0)) else m.group(0)
    return re.sub(r"[A-Za-z][A-Za-z0-9]*(?:\s*-\s*[A-Za-z0-9]+)*", blank, text)


def _target(form, number, label):
    return {"form": form, "number": number, "label": label}


def parse_locator(text):
    """参照の位置の語（LLM が付けた locator。例「3-5項」「上記2項」「2-1及び2-2項」「別表2」「別紙-1」「別表1 符号A」）から、
    指している章・節・付属の番号を出現順に返す。読めなければ空のリスト。

    返り値の 1 件 = {"form": 形, "number": 番号, "label": 元の語}。形は
      章 … 「2項」「上記2項」「第3章」（番号 "2"）。「2~3項」は 2 と 3
      節 … 「3-5」「3-5項」「上記4-1及び4-2」（番号 "3-5"）。「2-1~2-3」は 2-1・2-2・2-3 に広げる
      項 … 「3-1-2」（番号 "3-1-2"）と「(2)」（番号 "2"。見出しの形の「(N)」と同じ扱い）
      付属 … 「別表2」「別紙-1」「付表6」（番号は None、label は "別表2" の形。題名の先頭で引く）
      号 … 「3)」（番号 "3"。同じ節の中の箇条を指す。節は参照元の節とする）
    文書番号や識別子の中の数字は読まない。ハイフンの異体（「3−5」「3ー5」「3 - 5」）は「3-5」として読む。"""
    t = unify_dashes(nfkc(text or ""))
    t = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "-", t)   # 数字の間のハイフンの前後の空白を詰める（「3 - 5項」の「5項」を章と読まない）
    t = _blank_codes(t)
    found = []   # (位置, 対象)
    used = []    # 読んだ範囲（重ねて読まない）

    def free(s, e):
        return all(e <= a or s >= b for a, b in used)

    for m in _ANNEX_RX.finditer(t):
        found.append((m.start(), _target("付属", None, m.group("w") + m.group("n"))))
        used.append((m.start(), m.end()))
    secs = [m for m in _SEC_RX.finditer(t) if free(m.start(), m.end())]
    i = 0
    while i < len(secs):
        m = secs[i]
        nxt = secs[i + 1] if i + 1 < len(secs) else None
        if nxt and re.fullmatch(_RANGE, t[m.end():nxt.start()]) and m.group(1) == nxt.group(1) and not m.group(3) and not nxt.group(3) \
                and int(m.group(2)) < int(nxt.group(2)) <= int(m.group(2)) + 20:
            for k in range(int(m.group(2)), int(nxt.group(2)) + 1):   # 「2-1~2-3」→ 2-1・2-2・2-3
                found.append((m.start(), _target("節", "%s-%d" % (m.group(1), k), t[m.start():nxt.end()].strip())))
            used.append((m.start(), nxt.end()))
            i += 2
            continue
        short = re.match(_RANGE + r"(\d{1,2})(?![\d\-])", t[m.end():])
        if short and not m.group(3) and int(m.group(2)) < int(short.group(1)) <= int(m.group(2)) + 20:   # 「2-1~3」
            for k in range(int(m.group(2)), int(short.group(1)) + 1):
                found.append((m.start(), _target("節", "%s-%d" % (m.group(1), k), t[m.start():m.end() + short.end()].strip())))
            used.append((m.start(), m.end() + short.end()))
            i += 1
            continue
        if m.group(3):
            found.append((m.start(), _target("項", "%s-%s-%s" % (m.group(1), m.group(2), m.group(3)), m.group(0))))
        else:
            found.append((m.start(), _target("節", "%s-%s" % (m.group(1), m.group(2)), m.group(0))))
        used.append((m.start(), m.end()))
        i += 1
    for m in _CHAP_RANGE_RX.finditer(t):
        if free(m.start(), m.end()) and int(m.group(1)) < int(m.group(2)) <= int(m.group(1)) + 20:
            for k in range(int(m.group(1)), int(m.group(2)) + 1):
                found.append((m.start(), _target("章", str(k), m.group(0).strip())))
            used.append((m.start(), m.end()))
    for m in _CHAP_RX.finditer(t):
        if free(m.start(), m.end()):
            used.append((m.start(), m.end()))
            if m.group("dai") and m.group("w") == "項":
                continue   # 「第2項」は条の中の項。章の番号ではない
            found.append((m.start(), _target("章", m.group("n"), m.group(0).strip())))
    for m in _PARA_RX.finditer(t):
        if free(m.start(), m.end()):
            found.append((m.start(), _target("項", m.group(1), m.group(0))))
            used.append((m.start(), m.end()))
    for m in _ITEM_RX.finditer(t):
        if free(m.start(), m.end()):
            found.append((m.start(), _target("号", m.group(1), m.group(0).strip())))
            used.append((m.start(), m.end()))
    out, seen = [], set()
    for _pos, tgt in sorted(found, key=lambda x: x[0]):
        key = (tgt["form"], tgt["number"], tgt["label"] if tgt["form"] == "付属" else None)
        if key not in seen:
            seen.add(key)
            out.append(tgt)
    return out


def _squeeze_index(text):
    """squeeze と同じ形の文字列と、その各文字が NFKC にした元の文字列の何文字目かの表（末尾に元の長さを足す）。
    数字と数字の間の空白だけは「/」として残す（「HS-VTR-T001 3-5項」を「T0013-5」とつなげて文書番号を見失わないため）。
    返す元の文字列は、NFKC にしてハイフンの異体を「-」にそろえたもの（字数は NFKC の後と同じ）。"""
    t = unify_dashes(nfkc(text))
    out, idx = [], []
    for i, ch in enumerate(t):
        if ch.isspace():
            if out and out[-1].isdigit() and idx and out[-1] != "/":
                j = i
                while j < len(t) and t[j].isspace():
                    j += 1
                if j < len(t) and t[j].isdigit():
                    out.append("/")
                    idx.append(i)
            continue
        for r in ch.upper():
            out.append(r)
            idx.append(i)
    idx.append(len(t))
    return "".join(out), idx, t


def document_numbers_in(text, cfg):
    """文字列に現れる文書番号の正規形を、出現順に重複なく返す（numbers_in と同じだが、数字の間の空白で番号をつなげない）。"""
    s, _idx, _t = _squeeze_index(text)
    out = []
    for _pos, canon in sorted((m.start(), canon) for m, canon, _rev in _series_matches(s, cfg)):
        if canon not in out:
            out.append(canon)
    return out


# 文書番号の直後の番号（「HS-VTR-T001の3-5項」「HS-VTR-T001 3-5項」「HS VTR-T001 付表6」）。番号は「項」で終わるか、節・項の形（3-5）に限る
_AFTER_NUMBER_RX = re.compile(r"\s*(?:の|に於ける|における)?\s*(?:(?P<sec>\d{1,2}\s*%(d)s\s*\d{1,2}(?:\s*%(d)s\s*\d{1,2})?)(?![\d.])\s*(?:項|節)?"
                              r"|(?P<chap>\d{1,2})\s*(?:項(?!目)|章)|(?P<w>%(a)s)\s*%(d)s?\s*(?P<n>\d{1,2})(?!\d))" % {"a": _ANNEX_WORDS, "d": _DASH})


def numbers_after_document_numbers(text, cfg):
    """参照の一文で、文書番号の直後に続く章・節・付属の番号を返す（[(文書番号の正規形, parse_locator と同じ形の対象)]）。
    文書番号から離れた番号（「4-2項の管理カーブの管理要領をHS VTR-T003とした」の 4-2 は参照元の文書の項）は読まない。"""
    s, idx, t = _squeeze_index(text)
    out = []
    if not s:
        return out
    for m, canon, _rev in _series_matches(s, cfg):
        rest = t[idx[m.end()]:] if m.end() < len(idx) else ""
        a = _AFTER_NUMBER_RX.match(rest)
        if not a:
            continue
        if a.group("w"):
            tgt = _target("付属", None, a.group("w") + a.group("n"))
        elif a.group("chap"):
            tgt = _target("章", a.group("chap"), a.group(0).strip())
        else:
            parts = [p.strip() for p in re.split(_DASH, a.group("sec"))]
            tgt = _target("節" if len(parts) == 2 else "項", "-".join(parts), a.group(0).strip())
        out.append((canon, tgt))
    return out


def annex_key(title):
    """付属の節の題名の先頭の印（「別表2 過給機性能試験…」→ "別表2"、「別紙-1」→ "別紙1"）。無ければ None。参照の「別表2」と題名を突き合わせる。"""
    t = re.sub(r"^[#\s]+", "", nfkc(title or ""))
    m = re.match(r"(?P<w>%s)\s*%s?\s*(?P<n>\d{1,2})(?!\d)" % (_ANNEX_WORDS, _DASH), t)
    return (m.group("w") + m.group("n")) if m else None


# ---- 文書台帳の属性（種類・略称・旧名称・旧番号・版の順序・日付）。文書層の後処理（document_layer.py）が Standard に写す ----
# 種類は名称の語尾の語。設定 document_kinds が無ければこの一覧を使う（長い語を先に見る必要は無い。末尾に近いものを採る）
DOCUMENT_KINDS = ["規程", "規定", "規則", "基準", "要領", "要綱", "細則", "標準", "仕様書", "方案", "規格", "指針", "手順書", "マニュアル", "目録"]
_SELF_REF = r"(?:この|本|当)\s*(?:%s)"
_IDENT_RX = re.compile(r"[A-Z][A-Z0-9]*(?:\s?-\s?[A-Z0-9]+)+|[A-Z]{1,6}\s?\d{3,}")


def _kinds(cfg):
    return (cfg or {}).get("document_kinds") or DOCUMENT_KINDS


def document_kind(name, cfg=None):
    """名称の語尾から文書の種類（規程・規則・基準・要領・標準・方案 など）。「要領及び判定基準」は最後の「基準」。無ければ None。"""
    t = nfkc(name).strip()
    best = None
    for k in _kinds(cfg):
        i = t.rfind(k)
        if i >= 0:
            end = i + len(k)
            if best is None or end > best[0] or (end == best[0] and len(k) > len(best[1])):
                best = (end, k)
    # 語尾から遠い語（「規格値の表」の「規格」）は種類にしない。名称の最後の 4 字以内で終わるものに限る
    if best and len(t) - best[0] <= 4:
        return best[1]
    return None


def looks_like_document_name(text, cfg=None):
    """文書の名称らしい語か（種類の語で終わる）。「○○規程による」の○○が台帳に無いかを数えるのに使う。"""
    return bool(document_kind(text, cfg))


def is_self_reference(text, cfg=None):
    """「この基準」「本規程」など、その文書自身を指す語か。"""
    return bool(re.fullmatch(_SELF_REF % "|".join(map(re.escape, _kinds(cfg))) + r"\s*", nfkc(text).strip()))


def external_key(text):
    """手元に無い文書の識別子を突き合わせる鍵。大文字にし、空白とハイフンをすべて除く（「HZTL 20559」「HZTL20559」、「JIS H 5202」「JIS-H5202」「JISH5202」、
    「V-00862」「V00862」を同じにする）。Standard の id には使わない（id は external_id の表記のうち、決まった規則で 1 つを選ぶ。document_layer.py）。"""
    return re.sub(r"[\s\-]+", "", squeeze(text))


def _antecedent(text, end):
    """定義の括弧の直前の語（読点・句点・改行・括弧の切れ目まで遡る）。カギ括弧で囲まれていればその中。"""
    seg = text[max(0, end - 80):end]
    seg = re.split(r"[、。\n;；]", seg)[-1]
    q = re.search(r"[「『]([^「」『』]{2,60})[」』]\s*$", seg)
    if q:
        return q.group(1).strip()
    # 「…に用いる単独運転装置」の「に用いる」までを除く（述語の後ろが名詞句）
    seg = re.split(r"(?:する|した|される|された|いう|による|に用いる|の内|のうち|に関し|について|における|に於ける|として)\s*", seg)[-1]
    return seg.strip(" 　・")


_ABBR_DEF_RX = re.compile(r"[(（]\s*以下\s*[、,]?\s*[「『]?(?P<abbr>[^」』()（）、。\n]{1,30}?)[」』]?\s*(?:という|と称する|と呼ぶ|と略す|と略称する)\s*[。.]?\s*[)）]")
_ABBR_SAY_RX = re.compile(r"以下\s*[、,]\s*[「『]?(?P<abbr>[^」』()（）、。\n]{1,30}?)[」』]?\s*と(?:称する|呼ぶ|略す|略称する)")
_ABBR_PAREN_RX = re.compile(r"[(（]\s*(?:通称|略称)\s*[:：]?\s*[「『]?(?P<abbr>[^」』()（）、。\n]{1,30}?)[」』]?\s*[)）]")
_COVER_KEYS_ABBR = r"(?:通称|略称|別称|別名)"
_COVER_KEYS_FORMER = r"(?:旧番号|旧文書番号|旧基準番号|旧規格番号|旧規定番号|旧称|旧名称)"


def _cover_rows(text, keys):
    return [(m.group(1), m.group(2).strip()) for m in re.finditer(r"(?m)^[ \t]*\|[ \t]*(" + keys + r")[ \t]*\|[ \t]*([^|\n]{2,40}?)[ \t]*\|[ \t]*$", text)]


def parse_abbreviations(text, cfg=None, cover=False):
    """本文から略称・通称の定義を拾う。[{"antecedent": 定義される語, "abbr": 略称, "how": 書式}]。どの文書の略称かは呼び出し側が決める
    （antecedent に文書番号があればその文書、「この基準」なら自分の文書、文書の名称なら名称の一致する文書。それ以外は用語の定義なので文書の略称にしない）。
    書式: definition 「X（以下「Y」という。）」 / say 「X。以下、Y と称する」 / paren 「X（通称 Y）」 / call 「X は「Y」と呼ぶ」「この基準は、通称を「Y」といい」 /
          cover 表紙の欄「| 通称 | Y |」（cover=True のときだけ。先頭のチャンクに限って呼ぶ）"""
    t = nfkc(text)
    out = []
    for rx, how in ((_ABBR_DEF_RX, "definition"), (_ABBR_PAREN_RX, "paren")):
        for m in rx.finditer(t):
            out.append({"antecedent": _antecedent(t, m.start()), "abbr": m.group("abbr").strip(), "how": how})
    for m in _ABBR_SAY_RX.finditer(t):
        if not _ABBR_DEF_RX.search(t[max(0, m.start() - 2):m.end() + 4]):
            out.append({"antecedent": _antecedent(t, m.start()), "abbr": m.group("abbr").strip(), "how": "say"})
    kinds = "|".join(map(re.escape, _kinds(cfg)))
    for m in re.finditer(r"(?P<ante>" + (_SELF_REF % kinds) + r"|[A-Z][A-Z0-9\- ]{2,24}\d)\s*は\s*[、,]?\s*(?:(?:通称|略称)を\s*)?[「『](?P<abbr>[^」』\n]{2,20})[」』]\s*"
                         r"(?:と呼ぶ|という|といい|と称する|と略す|と略称|と呼ばれ)", t):
        out.append({"antecedent": m.group("ante").strip(), "abbr": m.group("abbr").strip(), "how": "call"})
    # 並び（「X は「a」、Y は「b」と呼ぶことがある」）: 同じ文の中に呼び方の語があれば、識別子ごとに拾う
    for sent in re.split(r"[。\n]", t):
        if re.search(r"と呼ぶ|という|と称する|と略す", sent):
            for m in re.finditer(r"(?P<ante>[A-Z][A-Z0-9\-]{2,24}\d)\s*は\s*[「『](?P<abbr>[^」』\n]{2,20})[」』]", sent):
                item = {"antecedent": m.group("ante").strip(), "abbr": m.group("abbr").strip(), "how": "call"}
                if item not in out:
                    out.append(item)
    if cover:
        for _k, v in _cover_rows(t, _COVER_KEYS_ABBR):
            out.append({"antecedent": "", "abbr": v, "how": "cover"})
    return [x for x in out if x["abbr"] and x["abbr"] != x["antecedent"]]


def parse_former_names(text, cfg=None, own_number=None, in_history=False, cover=False):
    """旧名称・旧番号を拾う。[{"former": 旧の表記, "kind": "name"|"number", "target": 現行の文書番号の表記（自分の文書なら None）, "how": 書式}]。
    書式: cover 表紙の欄「| 旧番号 | X |」「| 旧称 | X |」 / table 旧番号と現行の番号の対照表（どの文書でも読む）/
          以下は来歴・協議・通知票の中（in_history=True）だけで読む:
          established 「新規制定（X）」の括弧の識別子 / revised 「X を全面見直しの上」「X の全部を改正」（自分以外の文書番号）/
          renamed 「旧名称 X」「X から改称」「名称を「X」から…」/ rename_word 「名称変更（A から B に変更）」（語の置き換え。呼び出し側が現行の名称から旧名称を作る）"""
    t = nfkc(text)
    out = []

    def kind_of(v):
        return "number" if re.search(r"\d", v) and len(squeeze(v)) <= 24 else "name"

    if cover:
        for k, v in _cover_rows(t, _COVER_KEYS_FORMER):
            out.append({"former": v, "kind": "number" if "番号" in k else kind_of(v), "target": None, "how": "cover"})
    for m in re.finditer(r"(?m)^[ \t]*\|[ \t]*旧[^|\n]{0,6}番号[ \t]*\|[ \t]*(?:現行|新)[^|\n]{0,8}[ \t]*\|[ \t]*$", t):
        for row in t[m.end():].split("\n")[1:]:
            cells = [x.strip() for x in row.strip().strip("|").split("|")]
            if not row.strip().startswith("|") or len(cells) != 2:
                break
            if set(cells[0]) <= set("-: ") or not cells[0] or not cells[1]:
                continue
            out.append({"former": cells[0], "kind": "number", "target": cells[1], "how": "table"})
    if not in_history:
        return out
    own_key = external_key(own_number) if own_number else None
    for m in re.finditer(r"(?:新規制定|新規作成|新規調整|制定)\s*[(（]\s*([^()（）\n]{2,20}?)\s*[)）]", t):
        v = m.group(1)
        if re.search(r"\d", v) and external_key(v) != own_key:
            out.append({"former": v, "kind": "number", "target": None, "how": "established"})
    for m in re.finditer(r"(" + _IDENT_RX.pattern + r")\s*(?:を\s*(?:全面見直し|全部改正|全面改正|全部改訂)|の全部を改正)", t):
        v = re.sub(r"\s+", "", m.group(1))
        num, _rev = normalize_number(v, cfg, strict=True) if cfg else (None, None)
        v = num or re.sub(r"(?<=\d{3})[A-Z]$", "", v)   # 版の符号（HS-RH-014A の A）を落とす
        if external_key(v) != own_key:
            out.append({"former": v, "kind": "number", "target": None, "how": "revised"})
    for m in re.finditer(r"旧(?:名称|称)\s*[:：は]?\s*[「『]([^」』\n]{2,40})[」』]", t):
        out.append({"former": m.group(1).strip(), "kind": "name", "target": None, "how": "renamed"})
    for m in re.finditer(r"[「『]([^」』\n]{2,40})[」』]\s*(?:から|より)\s*(?:改称|名称を変更|名称変更)", t):
        out.append({"former": m.group(1).strip(), "kind": "name", "target": None, "how": "renamed"})
    for m in re.finditer(r"名称を\s*[「『]([^」』\n]{2,40})[」』]\s*から", t):
        out.append({"former": m.group(1).strip(), "kind": "name", "target": None, "how": "renamed"})
    for m in re.finditer(r"名称変更\s*[(（]\s*([^()（）\s]{1,12}?)\s*(?:から|を)\s*([^()（）\s]{1,12}?)\s*に\s*(?:変更|改め)[^()（）]{0,4}[)）]", t):
        a, b = (re.sub(r"(?<=[ァ-ヶ])-", "ー", x) for x in (m.group(1), m.group(2)))
        out.append({"former": a, "kind": "rename_word", "target": None, "how": "rename_word", "new_word": b})
    return out


def rev_rank(rev):
    """版の符号の順（初版 0 または符号なし < A < B < …）。"""
    r = (rev or "0").strip().upper()
    if r in ("", "0"):
        return 0
    return 1 + ord(r[0]) - ord("A") if "A" <= r[0] <= "Z" else 0


def iso_date(date):
    """来歴・目録の日付（98.10.21、H6.8.4、S59.3.26、'07.06.11、2006.06.10）を YYYY-MM-DD に。読めなければ None。"""
    t = re.sub(r"\s", "", nfkc(date or "")).lstrip("'’‘")
    m = re.fullmatch(r"([HSRhsr])?(\d{1,4})[./\-](\d{1,2})[./\-](\d{1,2})", t)
    if not m:
        return None
    era, y, mo, d = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    if era:
        y += {"H": 1988, "S": 1925, "R": 2018}[era.upper()]
    elif y < 100:
        # 元号の無い 2 桁の年: 64〜99 は 1964〜1999 年、0〜49 は 2000〜2049 年。50〜63 は昭和とみなす（1975〜1988 年。
        # 社内規格の発行目録・来歴は「59.1.11」（昭和 59 年）と「99.2.2」（1999 年）が混在する。1950 年代の制定は無い前提）
        y += 1900 if y >= 64 else (1925 if y >= 50 else 2000)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return "%04d-%02d-%02d" % (y, mo, d)


def name_near_identifier(quote, written, cfg=None):
    """参照の一文で、手元に無い文書の識別子に添えて書かれた名称を拾う（「QC-KAI-027初品管理規定」「『コンプレッサ性能計測標準試験方案;V00862』」
    「JIS B 8340「ターボ形ブロワ・圧縮機の試験及び検査方法」」）。種類の語で終わる名称か、カギ括弧の中の名称に限る。無ければ None。"""
    t, w = nfkc(quote), nfkc(written or "").strip()
    if not t or not w:
        return None
    kinds = "|".join(map(re.escape, _kinds(cfg)))
    i = t.find(w)
    if i >= 0:
        rest = t[i + len(w):]
        m = re.match(r"\s*[「『]([^」』\n]{3,40})[」』]", rest)
        if m:
            return m.group(1).strip()
        m = re.match(r"\s*([぀-ヿ㐀-鿿ー・][぀-ヿ㐀-鿿ー・A-Za-z0-9\-]{1,38}?(?:%s))" % kinds, rest)
        # 接続の語や助詞で始まるもの（「JIS G 4052 及び注文要領」の「及び注文要領」）は名称ではない
        if m and not re.match(r"(?:及び|並びに|又は|若しくは|等|の|に|を|は|が|と|で|や|より|から|による|に基づ)", m.group(1)):
            return m.group(1).strip()
    m = re.search(r"[「『]([^」』;；:：\n]{3,40})[;；:：]\s*" + re.escape(w) + r"\s*[」』]", t)
    if m:
        return m.group(1).strip()
    return None


_ANY_DATE = r"[’‘']?(?:[HSRhsr]\s?)?\d{1,4}\s?[./\-]\s?\d{1,2}\s?[./\-]\s?\d{1,2}(?!\d)"


def parse_enacted(text):
    """来歴の制定日（「制定: 59．1．11 新規制定」「1. S63.03.26 新規作成」「新規制定 ;’01.06.04」「0改正:H6. 8. 4新規調整」「- 0: 1996.03.20 新規制定」）。
    YYYY-MM-DD か None。最初に見つかったものを採る（来歴は古い順に並ぶ）。"""
    t = nfkc(text)
    for rx in (r"(%s)[^\n。]{0,40}?新規(?:作成|制定|調整)" % _ANY_DATE, r"新規(?:作成|制定|調整)\s*[;；:：]?\s*(%s)" % _ANY_DATE,
               r"制\s*定\s*[:：;；]?\s*(%s)" % _ANY_DATE):
        m = re.search(rx, t)
        if m:
            d = iso_date(m.group(1))
            if d:
                return d
    return None
