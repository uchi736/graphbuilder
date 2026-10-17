#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""規則だけで、チャンクの間の参照リンクをどこまで張れるかを測る試作（LLM もサービスも使わない）。

考え方は、法令文の参照抽出（北野・天笠, NLP2026: パターンによる抽出）を、社内の規格・規定に移したもの。
法令の「法令名・条項番号」を「文書の識別子・項番号や別表などの場所の名前」に置き換える。
/home/uchida/ai-stack/graphrag/graphrag_core/graph/references.py と同じ 2 段構え（目録を作る → 参照を拾って目録で引く）。

  1. 目録: 入力のチャンクだけから、文書の識別子と、文書ごとの「場所の名前」（項番号、条、別表、図、注 など）を作る
  2. 抽出: 参照の表現をパターンで拾う
  3. 解決: 目録で参照先のチャンクを決める。場所の指定が無い他文書への参照は、文書単位のリンクにする
  4. 採点: gold_refs.json に対して score_refs.py と同じ基準で数え、LLM 方式（chunk_refs_v1〜v3）と並べる

実物の文書番号・型式名・特定の文書だけの言い回しは、どのパターンにも書いていない。

使い方:
  python3 rule_refs.py                       # 既定の入力で実行し、rule_refs_result.json を書く
  python3 rule_refs.py --show catalogue      # 目録を表示
  python3 rule_refs.py --show refs           # 抽出した参照を表示
  python3 rule_refs.py --selftest            # 条の形（第 N 条）の作り物の例で、規則が動くことだけ確かめる
"""
import argparse
import bisect
import collections
import json
import pathlib
import re
import sys
import unicodedata

HERE = pathlib.Path(__file__).resolve().parent

# ───────────────────────── 使った規則の一覧（報告用） ─────────────────────────
# 採点の前に決めた規則。採点のあとに足したもの・直したものは POST_SCORING に分けて書く。
RULES = [
    "R01 文書の識別子: ファイル名から英数字の符号を取り、末尾の改正記号（英字 1 字・枝番）の有無、全角半角、ハイフンと空白のゆれを正規化して照合する。接頭の部門記号の有無は、区切りの位置がそろう後方一致で吸収する",
    "R02 文書の名称: ファイル名の和文の部分、先頭チャンクの最初の見出し、識別子と同じ行の和文を名称として登録する（「」で囲まれた名称の照合に使う）",
    "R03 項番号の見出し: 行頭（箇条書き記号・罫線・見出し記号の後を含む）か空白の直後の「N.」「N-M」「N.M.K」＋題名。直後が「項・及び・、・〜・助詞」なら見出しでなく参照とみなす。番号の順序が前の見出しと合うものだけ採る（先頭ゼロ、0 を含む番号、2 つを超える飛びは捨てる）",
    "R04 番号の付け直し: 番号が 1 に戻り題名も違うときは、別の部（別表や添付の中の番号）が始まったとみなして範囲を分ける。別表・添付資料などの題名でも範囲を切り替える",
    "R05 条・章・節の見出し: 行頭の「第 N 条」「第 N 章」「第 N 節」。条のある文書では、行頭の番号だけの行を条の中の項（第 2 項、第 3 項 …）として記録し、項番号の見出しには数えない",
    "R06 別表・別紙・付表・付図・添付資料・様式・付属書などの題名: 見出し、行頭、または短い行の末尾（自分の文書の識別子の直後を含む）。字間に空白のある表記（別 表 1）も同じとみなす",
    "R07 図・表の題名: R06 と同じ位置の規則。図の題名は図の下に付くので、題名がチャンクの先頭にあれば 1 つ前のチャンクも対象に含める",
    "R08 注の定義: 行頭の（注 N）",
    "R09 箇条 n) の位置を、属する項と合わせて記録する",
    "R10 場所の続き: 項は次の見出しが出るチャンクまで、別表などは次の題名か番号の付け直しの手前まで（上限 3 チャンク）。題名がチャンクの末尾にあれば次のチャンクから始まるとみなす。「N 項」は N-1、N-2 … の下位の項を含む",
    "R11 文書の識別子の言及を目録と照合する。他の文書なら文書単位のリンクにする",
    "R12 識別子＋場所（「X の 3-5 項」「X 別表 2」「X 第 3 条」）は、参照先の文書の中の場所へ結ぶ",
    "R13 識別子＋「の」＋見出しの名称＋参照の語（「X の○○による」）は、参照先の文書で題名が一致する見出しへ結ぶ。一致が無ければ文書単位にとどめる",
    "R14 同じ文書の項: 「N 項」「N-M 項」「N.M 節」、接頭の語（上記・前記・下記・本基準 など）＋番号、番号＋参照の語（による・に示す・参照 など。規則で引けたものだけ残す）",
    "R14b 「N-M 項」の見出しが無く、N 項の箇条 M) があれば、その箇条へ結ぶ",
    "R15 列挙と範囲: 「A 及び B 項」「A、B 項」「A〜B」「A から B まで」",
    "R16 別表・別紙・図・表・添付資料などへの参照（題名の定義でない出現すべて）",
    "R17 場所＋下位の指定（「別表 1 符号 A」「別表 2 の 3-1-1 項」）。下位の指定は、その場所の範囲の中の行頭・見出しから探す",
    "R17b 別表などの題名が目録に無いときは、下位の指定（符号 A など）を文書全体の行頭・見出しから探し、3 チャンク以内に収まれば結ぶ（本文を読んで、題名が変換で落ちた文書があると分かってから入れた。採点の前）",
    "R18 注への参照は、最も近い注の定義へ結ぶ",
    "R19 相対的な参照（前項・次項・前条・次条・本条・同条 など）は、参照元の位置から数えて決める。同じチャンクの中で済むものはリンクを作らず「チャンク内で解決」と記録する",
    "R20 番号の無い「上記・下記・次の・以下の」は規則では結ばず、件数だけ数える（隣り合う記述を指す）",
    "R21 目録に無い識別子らしい語は、参照の語を伴うか規格の番号（JIS・ISO など）の形のときだけ、外部の文書として記録し、結ばない",
    "R22 「」『』で囲んだ名称＋参照の語（または直後の括弧の中の場所の名前）は、名称を見出しの題名・文書の名称と照合する。無ければ外部として記録する",
    "R22b 目録にある文書の名称の直後に場所が続くもの（「○○規則第 4 条」「○○規程別表第 1」）は、その文書の中の場所へ結ぶ（条の形の作り物の例で動作を確かめて入れた。採点の前。実物の 6 文書には該当なし）",
    "R23 ページ参照（P.N 参照、N ページ参照）は、ページが同じチャンクへ結ぶ。場所の名前が別にあればそちらを優先する",
    "R24 参照元と同じチャンクへのリンクは作らない。自分の文書の識別子だけの言及（場所なし）は参照としない",
    "R14c・R15c 採点後に追加（P7）: 接尾の語の無い多段の番号＋助詞、「N. の○○」",
    "R22c 採点後に追加（P8）: 「X の」に続く場所で X が決まらないものは結ばない",
    "R25 採点後に追加（P6）: 「同基準・同規定 など＋場所」は直前に挙げた文書の中の場所",
]
# 採点のあとに足した規則・直した実装。どれも正解 41 件を見て足したものではなく、規則を作るときに見ていない架空の文書
# （別の担当が執筆中の 38 文書。数字 2 桁の文書番号、条の形の規定、JIS 風の「5.2 による」を含む）に当てて見つかった取り違えを直したもの。
# 実物の 6 文書の採点（40/41）は、どの変更の前後でも同じ。実物で増えた参照は 1 件（表に崩れた「2-1〜2-3」）。
POST_SCORING = [
    "採点後に追加 P1（R01 の条件を緩めた）: 識別子の条件を「数字 3 桁以上」から「英字と数字を含む 4 字以上」へ。目録に無い語を外部として記録する側は元の厳しさのまま。理由: 数字 2 桁の文書番号の文書が目録に入らず、その文書への「X の第 9 条」を自分の文書の条として扱った",
    "採点後に追加 P2（R06 を狭めた）: 番号の無い「様式」「付表」などは場所の名前としない。様式・帳票の直後の符号は外部の文書として記録。理由: 「台帳の様式は」を参照として拾った",
    "採点後に追加 P3（R03・R14 の直し）: 「項目」「条件」のように接尾の語（項・条 など）で始まる別の語を、参照の接尾と取り違えない。理由: 見出し「4.2 項目及び判定」を「4.2 項」への参照として拾った",
    "採点後に追加 P4（R09 の直し）: 箇条の種類 n) と (n) と a. を分けて記録し、前項・前号は同じ種類の並びの中で数える",
    "採点後に追加 P5（R05 の追加）: 「第 N 条 M 項」、条を言わない「第 N 項」（属する条の項）、「第 A 条から第 B 条まで」の間の条。「第 15 条の一覧」を枝番と取り違えない",
    "採点後に追加 P6（R25 新設、R19 の直し）: 「同基準・同規定 など＋場所」は、直前に挙げた文書の中の場所へ結ぶ。「同条・同項」は、同じチャンクで直前に挙げた参照と同じ先とし、無ければ結ばない。理由: 「同項」を自分の属する項として結んだ",
    "採点後に追加 P7（R14c・R15c 新設、R12 の追加）: 接尾の語の無い多段の番号＋助詞（「5.2 では」「5.1 から 5.7 まで」「3.1 及び 3.2 に」）、「N. の○○」、識別子＋「の」＋番号（「X の 6.1 を受けて」「X の 7. による」）。目録で全部引けたものだけ残す。理由: JIS 風の書き方を拾えなかった",
    "採点後に追加 P8（R22c 新設）: 「X の」に続く場所で、X が目録の文書とも自分の文書とも決まらないもの（略称・通称・旧番号）は、自分の文書の場所とみなさず、結ばない。理由: 「（他の文書の通称）の 3-2 による」を自分の文書の 3-2 へ結んだ",
    "採点後に追加 P9（R10・R04 の追加）: 見出し記号（#）つきの題名は、同じ深さ以上の次の見出しで終わる。別表などのあとの同じ深さ以上の見出しで、番号の範囲を本文へ戻す。理由: 別表のあとの「改正来歴」まで別表の続きとして結んだ。別表のあとの注にある「1.」を別表の中の番号として引いた",
    "採点後に追加 P10（R03 の直し）: 見出し記号の行でも番号は行の先頭だけを見る。参照の語や助詞で始まる題名（「2-3 による。」）は見出しにしない。表の行の先頭の欄の番号だけの欄は見出しにしない。見出し記号つきの見出しを、本文中の同じ番号より優先する。理由: 題名の中の数字（「2-2 2 台目及び 3 台目の計測」の 3）を見出しと取り違え、番号の付け直しと誤認した",
    "採点後に追加 P11（R17 の追加）: 下位の指定の値が、表の行の先頭の欄にある場合（「別表 1 の符号 B」の B）も探す",
    "採点後に追加 P12（R18・R12 の追加）: 他の文書の注（「X の注 2」）は、その文書の注へ結ぶ。理由: 自分の文書の注 2 へ結んだ",
]
ABLATE = set()  # 感度の確認用。--ablate で、本文を読んでから入れた規則を外して採点する

# ───────────────────────── 文字の正規化（元の位置を保つ） ─────────────────────────
DASHES = "‐‑‒–—―−－﹣"
CIRCLED = {chr(0x2460 + i) for i in range(20)}
JP = r"぀-ヿ㐀-鿿"
KANJI = r"㐀-鿿"
KNUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def normalize(text):
    """NFKC と記号のゆれをならした文字列と、各文字の元の位置を返す。丸数字は数字として扱わない。"""
    out, idx = [], []
    for i, ch in enumerate(text):
        if ch in CIRCLED:
            rep = "○"
        elif ch in DASHES:
            rep = "-"
        elif ch in "　\t\r":
            rep = " "
        else:
            rep = unicodedata.normalize("NFKC", ch) or ch
        for r in rep:
            out.append(r)
            idx.append(i)
    idx.append(len(text))
    return "".join(out), idx


def squeeze(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def kanji_int(s):
    """漢数字（九十九まで）か算用数字を整数にする。"""
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch == "百":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch in KNUM:
            cur = KNUM[ch]
        else:
            return None
    return total + cur


# ───────────────────────── パターン ─────────────────────────
NUM = r"[1-9]\d?(?:[-.][1-9]\d?){0,3}"
CODE_RX = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,6}[0-9]{0,6}(?:(?: ?- ?| {1,2})[A-Z0-9]{1,8}){0,5}(?![A-Za-z0-9])")
STD_PREFIX = r"(?:JIS|ISO|IEC|JEM|JEC|JASO|ASME|ASTM|ANSI|DIN|EN|BS|IEEE)"
CUE = (r"(?:によること|による|により|に依る|に依り|に拠る|に拠り|に基づ|に基き|にもとづ|に従|に準|に示す|に定め|に規定|に掲げ|に記載"
       r"|を参照|参照|のとおり|の通り|の要領|を適用|を準用|の値を|で定め)")
CUE_RX = re.compile(CUE)
SEP = r"(?:、|,|及び|並びに|又は|若しくは|および|または|と|・)"
RANGE = r"(?:~|〜|から|乃至|ないし)"
PREFIX = r"(?:上記|前記|下記|後記|前述の?|後述の?|上述の?|本基準の?|本規定の?|本規程の?|本要領の?|本書の?)"
PART_TYPES = ["添付資料", "添付書類", "添付図", "別表", "別紙", "別図", "別添", "別記", "別冊", "付表", "付図", "附表", "附図",
              "付属書", "附属書", "付録", "附録", "付則", "附則", "様式"]
FLOAT_TYPES = ["写真", "図", "表"]
UNNUMBERED_OK = {"添付資料", "添付書類", "添付図", "別表", "別紙", "別添", "別記", "別冊", "付録", "付則", "付属書"}
TYPE_ALIAS = {"附表": "付表", "附図": "付図", "附属書": "付属書", "附録": "付録", "附則": "付則"}


def _spaced(word):
    return "[ ]?".join(map(re.escape, word))


LABEL_NUM = r"(?:[ ]?第)?[ ]?-?[ ]?(?P<num>\d{1,3}(?:[.\-]\d{1,2})?|[A-Z](?![A-Za-z0-9])|[一二三四五六七八九十]{1,3}(?![一-龥]))(?:[ ]?号)?"
PART_RX = re.compile("(?P<type>" + "|".join(_spaced(t) for t in PART_TYPES) + ")(?:" + LABEL_NUM + ")?")
FLOAT_RX = re.compile("(?P<type>" + "|".join(FLOAT_TYPES) + ")" + LABEL_NUM)
FLOAT2_RX = re.compile(r"第[ ]?(?P<num>\d{1,3})[ ]?(?P<type>図|表)")
SUBLOC_RX = re.compile(r"[ ]{0,3}(?:の[ ]?)?(?P<sub>(?:符号|記号|区分|項目|番号|種別|No\.?)[ ]?[A-Za-z0-9ア-ンｱ-ﾝ]{1,3})(?![A-Za-z0-9])")
ELEM = r"(?P<n@>" + NUM + r")(?!\d)(?:[ ]?(?P<it@>\d{1,2})[ ]?\)(?!\S*\)))?(?P<s@>[ ]?(?:項|節|章)(?![目点約電件例立]))?"
ART_RX = re.compile(r"第[ ]?(?P<a>\d{1,3}|[一二三四五六七八九十百]{1,5})[ ]?条(?:[ ]?の[ ]?(?P<b>\d{1,2}(?![ ]?[)" + JP + r"])|[一二三四五六七八九十]{1,3}(?![一-龥])))?"
                    r"(?:[ ]?第?[ ]?(?P<p>\d{1,2}|[一二三四五六七八九十]{1,3})[ ]?項)?(?:[ ]?第[ ]?(?P<g>\d{1,2}|[一二三四五六七八九十]{1,3})[ ]?号)?")
DOC_SUFFIX = r"(?:規程|規定|規則|基準|要領|要綱|細則|マニュアル|手順書|指針|方針|標準|仕様書|方案|規格|通知|台帳|指示書|チェックシート|要項|内規)"
PARA_RX = re.compile(r"第[ ]?(?P<p>\d{1,2}|[一二三四五六七八九十]{1,3})[ ]?項")
ANA_RX = re.compile(r"同[ ]?(?:基準|規定|規程|規則|要領|要綱|細則|文書|書|法|令|マニュアル|手順書|標準|仕様書|規格)")
FOREIGN_BEFORE_RX = re.compile(r"(?:" + DOC_SUFFIX + r"|\d[ ]?号|」|』)[ ]?(?:\([^)\n]{1,12}\))?[ ]?の?[ ]?$")
OWN_BEFORE_RX = re.compile(r"(?:この|本|当)[ ]?" + DOC_SUFFIX + r"[ ]?の?[ ]?$")
CHAP_RX = re.compile(r"第[ ]?(?P<n>\d{1,2}|[一二三四五六七八九十]{1,3})[ ]?(?P<k>章|節|編|款)")
REL_RX = re.compile(r"(?<![一-龥])(?P<w>前各[条項号]|前[ ]?(?P<k>\d|[二三四五])?[ ]?[条項号節章]|次[条項号節章]|本[条項号節章]|同[条項号])(?![一-龥])")
NOTE_RX = re.compile(r"[(\[]?[ ]?(?:注|※)[ ]?(?P<n>\d{1,2})[ ]?[)\]]?")
QUOTE_RX = re.compile(r"[「『](?P<name>[^」』\n]{3,40})[」』]")
PAGE_RX = re.compile(r"(?:(?<![A-Za-z])[Pp]\.?[ ]?(?P<p1>\d{1,3})(?:/\d{1,3})?|(?P<p2>\d{1,3})[ ]?(?:ページ|頁))[ ]?(?:を)?(?:参照|に示す|による)")
VAGUE_RX = re.compile(r"(?<![一-龥])(?:上記|前記|上述|前述|下記|後記|後述|次|以下)(?:の|に示す|のとおり|の通り|により|による|表|図|要領|方法|例)")
NAME_LOC_RX = re.compile(r"(?P<name>[^\s、。,()「」『』|]{2,20}?)(?=" + CUE + ")")

TITLE_STOP_RX = re.compile(r"(?:及び|又は|並びに|若しくは|乃至|ないし|から|まで|より|[項節章条号](?![目点約電件例立])|参照|以上|以下|以内|未満|および|または)")
TITLE_PARTICLE_RX = re.compile(r"[のにをはがでともへや]")
TITLE_FUNC_RX = re.compile(r"(?:" + CUE + r"|では|には|にも|とは|とも|において|について|に対し|[のをが]|[にはでとも](?:[ 、" + KANJI + r"]))")
TITLE_BAD_FIRST = set("~〜、,。.)%℃°±×=:;/+-*<>≦≧」』]'\"…・･")
REF_BEFORE_RX = re.compile(r"(?:上記|前記|下記|後記|前述の?|後述の?|及び|又は|並びに|~|〜|から|第)[ ]*$")


# ───────────────────────── 入力 ─────────────────────────
class Chunk:
    __slots__ = ("id", "doc", "pos", "page", "raw", "body", "norm", "idx", "flat")

    def __init__(self, c):
        self.id, self.doc, self.pos, self.page = c["id"], c["fileName"], c["position"], c.get("page")
        self.raw = c["text"] or ""
        first, _, rest = self.raw.partition("\n")
        self.body = rest if (first.startswith("[") and first.rstrip().endswith("]")) else self.raw  # [文書名 > 見出しの経路] を除いた本文
        self.norm, self.idx = normalize(self.body)
        self.flat = self.norm.replace("|", " ")  # 表の罫線を空白とみなした版（長さは同じ）

    def line(self, off):
        s = self.norm.rfind("\n", 0, off) + 1
        e = self.norm.find("\n", off)
        return s, (len(self.norm) if e < 0 else e)

    def quote(self, s, e):
        """参照を含む一文（。か改行まで）。長い文は参照の前後だけ。"""
        a = max(self.norm.rfind("\n", 0, s), self.norm.rfind("。", 0, s)) + 1
        ends = [x for x in (self.norm.find("\n", e), self.norm.find("。", e)) if x >= 0]
        b = min(ends) + 1 if ends else len(self.norm)
        if b - a > 170:
            a, b = max(a, s - 70), min(b, e + 70)
        return self.body[self.idx[a]:self.idx[b]].strip()


def load_markdown_dir(path, size=700):
    """Markdown の文書の置き場を、見出しごと・size 字までの段落単位のチャンクにする（正解の無い文書群に規則を当ててみる用）。"""
    rows = []
    for f in sorted(pathlib.Path(path).rglob("*.md")):
        trail, pos, buf = [], 0, []

        def flush():
            nonlocal pos, buf
            body, buf = "\n".join(buf).strip(), []
            parts, cur = [], ""
            for para in re.split(r"\n\s*\n", body) if body else []:
                if cur and len(cur) + len(para) > size:
                    parts.append(cur)
                    cur = para
                else:
                    cur = (cur + "\n\n" + para) if cur else para
            for part in parts + ([cur] if cur else []):
                pos += 1
                rows.append({"id": "%s#%d" % (f.stem, pos), "fileName": f.stem + ".pdf", "position": pos, "page": None, "text": "[%s]\n%s" % (" > ".join([f.stem] + trail), part)})

        for line in f.read_text(encoding="utf-8").split("\n"):
            m = re.match(r"(#{1,6})\s+(.*)", line)
            if m:
                flush()
                trail = (trail[:len(m.group(1)) - 1] + [m.group(2).strip()]) if len(m.group(1)) > 1 else []
            buf.append(line)
        flush()
    docs = collections.defaultdict(list)
    for c in rows:
        docs[c["fileName"]].append(Chunk(c))
    return docs


def load_chunks(path):
    data = json.load(open(path, encoding="utf-8"))
    rows = data["chunks"] if isinstance(data, dict) else data
    docs = collections.defaultdict(list)
    for c in rows:
        docs[c["fileName"]].append(Chunk(c))
    for v in docs.values():
        v.sort(key=lambda c: c.pos)
    return docs


# ───────────────────────── 目録 1: 文書の識別子 ─────────────────────────
def code_forms(token):
    """識別子の表記から、改正記号や枝番を順に落とした形（区切りごとの列）を返す。"""
    segs = re.findall(r"[A-Z0-9]+", token.upper())
    forms, cur = [], segs
    for _ in range(4):
        joined = "".join(cur)
        if len(joined) < 4 or not any(ch.isdigit() for ch in joined) or not any(ch.isalpha() for ch in joined):
            break
        forms.append(tuple(cur))
        last = cur[-1]
        m = re.fullmatch(r"([A-Z]*\d{2,})([A-Z])", last)
        if m:
            cur = cur[:-1] + [m.group(1)]
        elif len(cur) > 1 and (re.fullmatch(r"[A-Z]", last) or re.fullmatch(r"\d{1,2}", last)):
            cur = cur[:-1]
        else:
            break
    return forms


def strict_code(token):
    """目録に無い語を外部の文書の番号とみなしてよい形か（数字 3 桁以上、または区切りつきで数字 2 桁以上）。"""
    digits = sum(ch.isdigit() for ch in token)
    return digits >= 3 or (digits >= 2 and "-" in token)


def form_match(a, b):
    """2 = 完全一致、1 = 区切りのそろう後方一致、0 = 不一致。"""
    A, B = "".join(a), "".join(b)
    if A == B:
        return 2
    long_, short, segs = (A, B, a) if len(A) > len(B) else (B, A, b)
    if len(short) < 5 or sum(ch.isdigit() for ch in short) < 2 or not long_.endswith(short):
        return 0
    cut, n, bounds = len(long_) - len(short), 0, set()
    for s in segs:
        bounds.add(n)
        n += len(s)
    return 1 if cut in bounds else 0


class Catalogue:
    def __init__(self, docs):
        self.docs = docs
        self.codes = {}   # 文書 → 識別子の形の集合
        self.names = {}   # 文書 → 名称の集合（空白を除いた形）
        self.doc = {}     # 文書 → DocIndex
        for name, chunks in docs.items():
            stem = unicodedata.normalize("NFKC", pathlib.PurePath(name).stem)
            forms = set()
            for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9 _\-]*[A-Za-z0-9]", stem):
                forms.update(code_forms(tok.replace("_", "-")))
            self.codes[name] = forms
        for name, chunks in docs.items():  # 本文に出る長い形（接頭の部門記号つき）を別名に足す。ファイル名に符号が無ければ先頭付近から拾う
            own, head = self.codes[name], collections.Counter()
            for c in chunks:
                for m in CODE_RX.finditer(c.norm):
                    fs = code_forms(m.group())
                    if own and any(form_match(f, o) == 1 and len("".join(f)) > len("".join(o)) for f in fs for o in own):
                        own = own | {fs[-1]}
                    elif not self.codes[name] and m.start() < 150 and fs and strict_code(m.group()):
                        head[fs[-1]] += 1
            if not self.codes[name] and head:
                form, n = head.most_common(1)[0]
                taken = any(form_match(form, o) for other, fo in self.codes.items() if other != name for o in fo)
                if n >= 2 and not taken:
                    own = {form}
            self.codes[name] = own
        for name, chunks in docs.items():
            self.names[name] = self._names(name, chunks)
            self.doc[name] = DocIndex(name, chunks, self)

    def _names(self, name, chunks):
        out = set()
        stem = unicodedata.normalize("NFKC", pathlib.PurePath(name).stem)
        for run in re.findall("[" + JP + "][" + JP + "・ ]{3,}", stem):
            out.add(squeeze(run))
        first = chunks[0]
        m = re.search(r"(?m)^#{1,6}[ ]*(.+)$", first.norm)
        if m and len(squeeze(m.group(1))) >= 6 and re.search("[" + KANJI + "]", m.group(1)):
            out.add(squeeze(m.group(1)))
        for m in CODE_RX.finditer(first.norm[:400]):
            if self.resolve_code(m.group(), restrict=name) == name:
                s, e = first.line(m.start())
                runs = re.findall("[" + JP + "][" + JP + "・ ]{5,}", first.norm[s:e])
                if runs:
                    words = max(runs, key=len).split()
                    while words and len(words[-1]) == 1:
                        words.pop()
                    while words and len(words[0]) == 1:
                        words.pop(0)
                    out.add("".join(words))
        return {n for n in out if len(n) >= 4}

    def resolve_code(self, token, restrict=None):
        """識別子の表記 → 文書名。目録に無ければ None、複数に当たれば "?"。"""
        best, hit = 0, set()
        forms = code_forms(token)
        for name, own in self.codes.items():
            if restrict and name != restrict:
                continue
            score = max((form_match(f, o) for f in forms for o in own), default=0)
            if score > best:
                best, hit = score, {name}
            elif score and score == best:
                hit.add(name)
        if not hit:
            return None
        return hit.pop() if len(hit) == 1 else "?"

    def resolve_name(self, text, source_doc):
        key = squeeze(text)
        hit = [name for name, ns in self.names.items() if any(n == key or (len(n) >= 6 and n in key) for n in ns)]
        return hit[0] if len(hit) == 1 else None


# ───────────────────────── 目録 2: 文書ごとの場所の名前 ─────────────────────────
class DocIndex:
    """1 文書の、見出し・別表など・図表・注・箇条・条の位置。位置は（チャンクの順番, 本文中の位置）。"""

    def __init__(self, name, chunks, cat):
        self.name, self.chunks, self.cat = name, chunks, cat
        self.by_pos = {c.pos: c for c in chunks}
        self.order = [c.pos for c in chunks]
        self.sections = collections.defaultdict(dict)   # 範囲 → 番号 → [(pos, off, 題名)]
        self.heads = []      # 採用した見出し（順）: (pos, off, 範囲, 番号)
        self.scopes = []     # 範囲の切り替え（順）: (pos, off, 範囲)
        self.labels = collections.defaultdict(list)     # (種類, 番号) → [(pos, off, end)]
        self.label_occ = collections.defaultdict(list)  # pos → [(s, e, 種類, 番号, 定義か, 他文書の識別子の直後か)]
        self.titles = collections.defaultdict(list)     # 題名（空白なし）→ [pos]
        self.line_heads = collections.defaultdict(list)  # pos → [(off, 行頭からの文字列)]
        self.items = collections.defaultdict(list)      # (範囲, 属する項, n) → [(pos, off)]
        self.item_marks = []  # (pos, off, n)
        self.notes = collections.defaultdict(list)      # n → [pos]
        self.articles = collections.defaultdict(list)   # (条, 枝) → [(pos, off)]
        self.art_marks = []   # (pos, off, (条, 枝))
        self.chapters = collections.defaultdict(list)   # (章や節, n) → [(pos, off)]
        self.paras = collections.defaultdict(list)      # ((条, 枝), n) → [(pos, off)]
        self.para_marks = []  # (pos, off, (条, 枝), n)
        self.has_articles = sum(len(re.findall(r"(?m)^[ #>*\-]*第[ ]?[0-9一二三四五六七八九十百]{1,5}[ ]?条", c.flat)) for c in chunks) >= 2
        self.md_heads = []    # 見出し記号（#）の行: (pos, off, 深さ)。図・表の題名の行は除く
        self.rejected = []    # 捨てた見出し候補（点検用）
        self.codes = {}       # pos → [(s, e, 表記, 解決先)]
        self._scan()

    # -- 走査 --
    def _scan(self):
        events = []
        for c in self.chunks:
            toks = []
            for m in CODE_RX.finditer(c.norm):
                if not code_forms(m.group()):
                    continue
                toks.append((m.start(), m.end(), m.group(), self.cat.resolve_code(m.group())))
            self.codes[c.pos] = toks
            for occ in self._labels(c, toks):
                self.label_occ[c.pos].append(occ)
                s, e, typ, num, is_def, foreign = occ
                if is_def:
                    events.append((c.pos, s, "part" if typ in PART_KEYS else "float", (typ, num, e, foreign)))
            for h in self._headings(c):
                events.append((c.pos, h["off"], "head", h))
            for m in re.finditer(r"(?m)^[ ]*(#{1,6})[ ]*(.+?)[ ]*$", c.norm):
                if not (FLOAT_RX.match(m.group(2)) or FLOAT2_RX.match(m.group(2))):
                    events.append((c.pos, m.start(), "md", len(m.group(1))))
            for m in re.finditer(r"(?<![^\s])(?:(?P<a>\d{1,2})[ ]?\)|\((?P<b>\d{1,2})\)|(?P<c>[a-z])[.)])[ ]?(?=[^\s\d).])", c.flat):
                kind = ")" if m.group("a") else ("()" if m.group("b") else "a")
                n = int(m.group("a") or m.group("b")) if kind != "a" else ord(m.group("c")) - 96
                events.append((c.pos, m.start(), "item", (kind, n)))
            for m in re.finditer(r"(?m)^[ >*\-]*[(\[]?[ ]?(?:注|※)[ ]?(\d{1,2})[ ]?[)\]:.]?(?=\S)", c.flat):
                self.notes[int(m.group(1))].append(c.pos)
            for m in re.finditer(r"(?m)^[ #>*\-]*" + ART_RX.pattern + r"(?=[ ((]|$)", c.flat):
                a = kanji_int(m.group("a"))
                if a and not m.group("p") and not m.group("g"):
                    events.append((c.pos, m.start(), "article", (a, kanji_int(m.group("b")) if m.group("b") else 0)))
            for m in re.finditer(r"(?m)^[ #>*\-]*" + CHAP_RX.pattern + r"(?=[ ((]|$)", c.flat):
                self.chapters[(m.group("k"), kanji_int(m.group("n")))].append((c.pos, m.start()))
            if self.has_articles:
                for m in re.finditer(r"(?m)^[ ]*(\d{1,2})[ ]+(?=[" + JP + "])", c.flat):
                    events.append((c.pos, m.start(), "para", int(m.group(1))))
            for m in re.finditer(r"(?m)^[ ]*(#{1,6})[ ]*(.+?)[ ]*$", c.norm):
                self.titles[squeeze(m.group(2))].append(c.pos)
                if not (FLOAT_RX.match(m.group(2)) or FLOAT2_RX.match(m.group(2))):
                    self.md_heads.append((c.pos, m.start(), len(m.group(1))))
            for m in re.finditer(r"(?m)^[ #>*\-|]*(\S[^\n]{0,30})", c.norm):
                self.line_heads[c.pos].append((m.start(1), squeeze(m.group(1))))
        events.sort(key=lambda e: (e[0], e[1], e[2] != "md"))
        scope, top, seen, restarts, current, article = "main", 0, {}, 0, (), None
        main_state, part_level, md_level, md_seen = None, None, {}, {}
        self.scopes.append((self.order[0] if self.order else 0, 0, "main"))
        for pos, off, kind, data in events:
            if kind == "md":
                md_level[(pos, off)] = data
                if part_level is not None and data <= part_level[0] and (pos, off) > part_level[1] and main_state is not None:
                    scope, (top, seen, current) = "main", main_state  # 別表などが終わり、本文の範囲へ戻る
                    main_state, part_level = None, None
                    self.scopes.append((pos, off, scope))
            elif kind == "part":
                typ, num, end, foreign = data
                if scope == "main":
                    main_state = (top, seen, current)
                if not foreign:
                    self.labels[(typ, num)].append((pos, off, end))
                    scope = typ + (num or "")
                else:
                    scope = "他文書の様式など@%d" % pos
                top, seen, current = 0, {}, ()
                ls = self.by_pos[pos].line(off)[0]
                part_level = (md_level[(pos, ls)], (pos, ls)) if (pos, ls) in md_level else None
                self.scopes.append((pos, off, scope))
            elif kind == "float":
                typ, num, end, foreign = data
                if not foreign:
                    self.labels[(typ, num)].append((pos, off, end))
            elif kind == "head":
                t, title, strong = data["num"], squeeze(data["title"])[:6], data["strong"]
                verdict = None
                if self.has_articles and len(t) == 1 and not data["dot"]:
                    continue  # 条のある文書では、行頭の番号だけの行は条の中の項
                if t in seen:
                    n = min(len(seen[t]), len(title))
                    if n >= 2 and seen[t][:n] == title[:n]:
                        verdict = "dup"
                    elif data["md"] and not md_seen.get((scope, t)):
                        verdict = "replace"  # 先に本文中の番号を見出しと取り違えていた。見出し記号つきの行を正とする
                    elif t in ((1,), (1, 1)) and strong and top >= 1:
                        verdict = "restart"
                elif t[0] == top or top < t[0] <= top + 2:
                    verdict = "new"
                elif t[0] < top and t in ((1,), (1, 1)) and strong:
                    verdict = "restart"
                if verdict is None:
                    self.rejected.append((pos, off, t, data["title"][:20]))
                    continue
                if verdict == "restart":
                    restarts += 1
                    scope = "番号の付け直し%d" % restarts
                    seen = {}
                    self.scopes.append((pos, off, scope))
                top = max(top, t[0]) if verdict != "restart" else t[0]
                if verdict == "replace":
                    seen[t] = title
                    self.sections[scope][t] = []
                    self.heads = [h for h in self.heads if not (h[2] == scope and h[3] == t)]
                seen.setdefault(t, title)
                md_seen[(scope, t)] = md_seen.get((scope, t)) or data["md"]
                current = t
                self.sections[scope].setdefault(t, []).append((pos, off, data["title"]))
                self.heads.append((pos, off, scope, t))
                for key in {squeeze(data["title"]), squeeze(data["title"].split(" ")[0])}:
                    if len(key) >= 2:
                        self.titles[key].append(pos)
            elif kind == "item":
                self.items[(scope, current) + data].append((pos, off))
                self.item_marks.append((pos, off, data[1], scope, current, data[0]))
            elif kind == "article":
                self.articles[data].append((pos, off))
                self.art_marks.append((pos, off, data))
                article = data
            elif kind == "para" and article is not None:
                self.paras[(article, data)].append((pos, off))
                self.para_marks.append((pos, off, article, data))

    def _headings(self, c):
        out = []
        for m in re.finditer(r"(?<![^\s])(?P<num>" + NUM + r")(?!\d)(?P<dot>\.)?(?P<sp>[ ]*)", c.flat):
            ls, le = c.line(m.start())
            before, rest = c.flat[ls:m.start()], c.flat[m.end():le]
            if not rest.strip() or re.match(r"[-.]\d", c.flat[m.end("num"):]):
                continue
            num = tuple(int(x) for x in re.split(r"[-.]", m.group("num")))
            strong = not before.strip(" #>*-")
            md = strong and c.norm[ls:le].lstrip().startswith("#")  # 見出し記号（#）の直後の番号
            first = rest[0]
            if not md and (first in TITLE_BAD_FIRST or TITLE_STOP_RX.match(rest) or REF_BEFORE_RX.search(before)):
                continue
            if not md and not m.group("dot") and not m.group("sp") and TITLE_PARTICLE_RX.match(rest):
                continue
            if not md and TITLE_FUNC_RX.match(rest):
                continue
            if len(num) == 1 and not m.group("dot") and c.norm[ls:le].lstrip().startswith("|"):
                continue  # 表の行の先頭の欄の番号
            if not re.search("[A-Za-z" + JP + "]", rest[:6]):
                continue
            if len(num) == 1:
                if first.isdigit() or not (m.group("dot") or (m.group("sp") and strong)):
                    continue
                if not m.group("dot") and not re.match("[A-Za-z" + JP + "]", first):
                    continue
            elif first.isdigit() and not re.match(r"\d{1,2}[" + JP + "]", rest):
                continue
            out.append({"off": m.start(), "num": num, "title": rest.strip()[:60], "strong": strong, "end": m.end(), "dot": bool(m.group("dot")), "md": md})
        return out

    def _labels(self, c, toks):
        """別表・図などの出現を、題名の定義か参照かに分けて返す。"""
        found, taken = [], []
        for rx in (PART_RX, FLOAT2_RX, FLOAT_RX):
            for m in rx.finditer(c.norm):
                s, e = m.start(), m.end()
                if any(s < b and a < e for a, b in taken):
                    continue
                typ = TYPE_ALIAS.get(re.sub(r"\s", "", m.group("type")), re.sub(r"\s", "", m.group("type")))
                num = m.group("num")
                if num is None and (typ not in UNNUMBERED_OK or (s > 0 and re.match("[" + KANJI + "]", c.norm[s - 1]))):
                    continue  # 番号の無い「付表」などが、別の語の一部である場合（直前が漢字）。番号の無い「様式」は普通の名詞
                if typ in FLOAT_TYPES and s > 0 and re.match("[" + KANJI + "]", c.norm[s - 1]) and c.norm[s - 1] not in "同本各該当下上右左次前":
                    continue
                if num is not None:
                    num = str(kanji_int(num)) if kanji_int(num) is not None else num.replace(".", "-")
                taken.append((s, e))
                ls, le = c.line(s)
                before, after = c.flat[ls:s].strip(" #>*-"), c.flat[e:le].strip()
                md = c.norm[ls:le].lstrip().startswith("#")
                prev = [t for t in toks if t[1] <= s and not c.norm[t[1]:s].strip(" の")]
                own_before = bool(prev) and prev[-1][3] == self.name and not c.flat[ls:prev[-1][0]].strip(" #>*-")
                foreign = bool(prev) and prev[-1][3] != self.name
                ref_after = bool(re.match(r"(?:[ ]?(?:符号|記号|区分|項目))?.{0,12}?" + CUE, after)) or bool(re.match(r"[のにをはがでと、,)]|及び|又は|並びに", after))
                short_tail = not after and (len(before) <= 20 or own_before) and not re.search(r"[のはをにで、(「『]$", before)
                own_tail = own_before and (not after or not ref_after)
                at_start = not before and not ref_after
                is_def = bool((md and not before) or at_start or short_tail or own_tail) and not c.norm[max(0, s - 1):s] == "("
                if foreign and not (not after and not c.flat[ls:prev[-1][0]].strip(" #>*-")):
                    is_def = False  # 他の文書の識別子に続く場所は、その文書の場所（R12）。行全体が「識別子＋題名」のときだけ区切りとして扱う
                found.append((s, e, typ, num, is_def, foreign))
        found.sort()
        return found

    # -- 位置の問い合わせ --
    def scope_at(self, pos, off):
        i = bisect.bisect_right([(p, o) for p, o, _ in self.scopes], (pos, off)) - 1
        return self.scopes[max(i, 0)][2]

    def section_at(self, pos, off):
        cur = None
        for p, o, scope, t in self.heads:
            if (p, o) <= (pos, off):
                cur = (scope, t)
            else:
                break
        return cur

    def item_at(self, pos, off):
        cur = None
        for mark in self.item_marks:
            if (mark[0], mark[1]) <= (pos, off):
                cur = mark
            else:
                break
        return cur

    def para_at(self, pos, off):
        cur = None
        for p, o, art, n in self.para_marks:
            if (p, o) <= (pos, off):
                cur = (art, n)
            else:
                break
        return cur

    def article_chunks(self, key, para=None):
        if para and para > 1 and self.paras.get((key, para)):
            return [self.paras[(key, para)][0][0]]
        defs = self.articles.get(key)
        if not defs:
            return None
        start = min(defs)
        ends = [(p, o) for p, o, _ in self.art_marks if (p, o) > start][:1] + [(p, o) for p, o, _ in self.scopes if (p, o) > start][:1] + [x for x in [self._md_end(start)] if x]
        if para == 1:
            return [start[0]]
        return self._span(start, min(ends) if ends else None)

    def article_at(self, pos, off):
        cur = None
        for p, o, key in self.art_marks:
            if (p, o) <= (pos, off):
                cur = key
            else:
                break
        return cur

    def _md_end(self, start):
        """start の行が見出し記号つきなら、同じ深さ以上の次の見出しの位置。"""
        c = self.by_pos[start[0]]
        ls, le = c.line(start[1])
        m = re.match(r"[ ]*(#{1,6})", c.norm[ls:le])
        if not m:
            return None
        return next(((p, o) for p, o, lv in self.md_heads if (p, o) > (start[0], ls) and lv <= len(m.group(1))), None)

    # -- 範囲（続きのチャンク）--
    def _span(self, start, end, cap=3):
        """start=(pos, off) から end=(pos, off) の手前までのチャンク。"""
        spos, soff = start
        c = self.by_pos[spos]
        order = self.order
        i = order.index(spos)
        last = order[-1] if end is None else end[0]
        j = order.index(last)
        if end is not None and end[1] <= 100 and j > i:
            j -= 1  # 次の見出しがチャンクの先頭にあるなら、そのチャンクは含めない
        ls, le = c.line(soff)
        at_tail, title_only = len(c.norm) - le <= 40, le - soff <= 30
        span = order[i:j + 1]
        if "R10tail" not in ABLATE and at_tail and title_only and i + 1 < len(order) and (end is None or end[0] > spos):
            # 題名だけがチャンクの末尾にある → 中身は次のチャンクから始まる
            if j < i + 1:
                span = order[i:i + 2]
            if soff > 40:
                span = span[1:]  # 題名のほかに別の内容が詰まったチャンクは外す
        keep = [p for p in span if p == span[0] or len(self.by_pos[p].body.strip()) >= 80]  # 続きのうち、柱やフッタだけの短いチャンクは外す
        return keep[:cap]

    def section_chunks(self, scope, t):
        table = self.sections.get(scope, {})
        defs = table.get(t) or [d for k, v in table.items() if k[:len(t)] == t for d in v]
        if not defs:
            return None
        start = min((p, o) for p, o, _ in defs)
        end = None
        for p, o, sc, k in self.heads:  # 次の見出し（重なりで繰り返された前の見出しは数えない）
            if (p, o) > start and sc == scope and k > t and k[:len(t)] != t:
                end = (p, o)
                break
        for p, o, sc in self.scopes:
            if (p, o) > start and (end is None or (p, o) < end):
                end = (p, o)
                break
        md = self._md_end(start)
        if md and (end is None or md < end):
            end = md
        return self._span(start, end)

    def label_chunks(self, key):
        defs = self.labels.get(key)
        if not defs and key[1] is None:
            same = [k for k in self.labels if k[0] == key[0]]
            defs = self.labels[same[0]] if len(same) == 1 else None
        if not defs:
            return None
        out = []
        for pos, off, end in defs:
            c = self.by_pos[pos]
            if key[0] in FLOAT_TYPES:
                span = [pos]
                i = self.order.index(pos)
                if "R07" not in ABLATE and key[0] == "図" and off <= 40 and i > 0:
                    span.insert(0, self.order[i - 1])
                if key[0] == "表" and len(c.norm) - end <= 40 and i + 1 < len(self.order):
                    span.append(self.order[i + 1])
            else:
                nxt = next(((p, o) for p, o, _ in self.scopes if (p, o) > (pos, off)), None)
                md = self._md_end((pos, off))
                if md and (nxt is None or md < nxt):
                    nxt = md
                span = self._span((pos, off), nxt)
            out += [p for p in span if p not in out]
        return out[:4]

    def find_line_start(self, text, positions=None):
        key = squeeze(text)
        hit = [p for p in (positions or self.order) if any(h.startswith(key) for _, h in self.line_heads[p])]
        if not hit and positions:  # 「符号 B」の B が、表の行の先頭の欄にある
            value = re.sub(r"^(?:符号|記号|区分|項目|番号|種別|No\.?)", "", key)
            if value:
                rx = re.compile(r"(?m)^[ ]*\|[ ]*" + re.escape(value) + r"[ ]*\|")
                hit = [p for p in positions if rx.search(self.by_pos[p].norm)]
        return hit


PART_KEYS = set(TYPE_ALIAS.get(t, t) for t in PART_TYPES)


# ───────────────────────── 抽出と解決 ─────────────────────────
def numlist_rx():
    parts = [ELEM.replace("@", "0")]
    for i in range(1, 6):
        parts.append(r"(?:[ ]?(?P<j%d>" % i + SEP + "|" + RANGE + r")[ ]?" + ELEM.replace("@", str(i)) + r"(?:[ ]?まで)?)?")
    return "".join(parts)


NUMLIST = numlist_rx()
NUMLIST_RX = re.compile(r"(?<![\d.\-A-Za-z])(?P<pre>" + PREFIX + r"[ ]?)?" + NUMLIST)
SINGLE_DOT_RX = re.compile(r"(?<![\d.\-A-Za-z])(?P<n>[1-9]\d?)\.[ ]?(?=" + CUE + r"|の[" + JP + "])")


def parse_numlist(m):
    """列挙・範囲を [(番号, 箇条, 接尾の語, つなぎの語)] にする。"""
    out = []
    for i in range(6):
        n = m.group("n%d" % i)
        if not n:
            break
        out.append((tuple(int(x) for x in re.split(r"[-.]", n)), int(m.group("it%d" % i)) if m.group("it%d" % i) else None,
                    (m.group("s%d" % i) or "").strip(), m.group("j%d" % i) if i else None))
    return out


class Extractor:
    def __init__(self, cat):
        self.cat = cat
        self.refs = []
        self.vague = []
        self.dropped = collections.Counter()

    def run(self):
        for name, chunks in self.cat.docs.items():
            for c in chunks:
                self._chunk(self.cat.doc[name], c)
        return self.refs

    # -- 記録 --
    def _add(self, D, c, s, e, form, locator, target_doc=None, positions=None, written=None, scope=None, note="", rule=""):
        T = self.cat.doc.get(target_doc) if target_doc else None
        positions = positions or []
        in_chunk = bool(positions) and target_doc == D.name and all(p == c.pos for p in positions)
        links = [p for p in positions if not (target_doc == D.name and p == c.pos)]
        if scope is None:
            scope = "external" if target_doc is None else ("same_document" if target_doc == D.name else "other_document")
        if target_doc is None and scope != "external":
            target_doc = None
        level = "chunk" if links else ("in_chunk" if in_chunk else ("document" if (target_doc and target_doc != D.name and not locator_given(form)) else "none"))
        if level == "none" and target_doc and target_doc != D.name:
            level = "document"  # 場所を引けなくても、文書までは結ぶ
        self.refs.append({
            "source": c.id, "source_doc": D.name, "source_position": c.pos, "quote": c.quote(s, e), "form": form, "locator": locator,
            "target_document": written, "target_doc": target_doc, "scope": scope, "level": level, "note": note, "rule": rule,
            "span": (s, e),
            "targets": [{"id": T.by_pos[p].id, "position": p, "text": T.by_pos[p].raw} for p in links] if T else [],
        })

    # -- 1 チャンク --
    def _chunk(self, D, c):
        text, used = c.norm, []
        first_ref, pending = len(self.refs), []

        def free(s, e):
            return not any(s < b and a < e for a, b in used)

        def cue_after(e, reach=14):
            return bool(re.match(r"[^。\n]{0,%d}?" % reach + CUE, text[e:]))

        # R11-R13, R21: 文書の識別子
        toks = D.codes[c.pos]
        for k, (s, e, tok, target) in enumerate(toks):
            if not free(s, e):
                continue
            written = re.sub(r"\s+", " ", tok)
            if target == "?":
                self._add(D, c, s, e, "文書番号のみ", "", None, written=written, scope="external", note="目録の複数の文書に当たり、決められない", rule="R11")
                used.append((s, e))
                continue
            T = self.cat.doc.get(target) if target else None
            m0 = re.match(r"[ ]?\)?[ ]?(?:\([^)\n]{1,8}\))?[ ]?(?:の[ ]?)?", text[e:])
            p = e + m0.end()
            loc = self._locator_at(c, p, T, target is not None)
            if loc and loc["kind"] == "label" and target == D.name and any(o[0] == p and o[4] for o in D.label_occ[c.pos]):
                loc = None  # 「自分の識別子＋題名」は別表などの柱（題名の定義）
            if target is None:  # 目録に無い識別子
                std = re.match(STD_PREFIX + r"\b", tok)
                listed = any(t[0] >= e and not re.sub(r"[\s、,.・及び\-]|" + CODE_RX.pattern, "", text[e:t[0]]) and cue_after(t[1], 6) for t in toks[k + 1:k + 4])
                named = bool(re.match(r"[" + JP + r"]{0,10}" + DOC_SUFFIX, text[e:])) or bool(re.search(r"(?:様式|帳票|図面|図番)[ ]?$", text[max(0, s - 6):s]))
                if not strict_code(tok):
                    self.dropped["目録に無い符号（型式などの短い語）"] += 1
                elif std or cue_after(e, 10) or listed or named or (loc and loc["kind"] != "name") or re.search(r"[;:;:「『(\[][ ]?$", text[max(0, s - 2):s]) and cue_after(e, 4):
                    end = loc["end"] if loc and loc["kind"] != "name" else e
                    self._add(D, c, s, end, "外部の文書", text[e:end].strip(" の") if end > e else "", None, written=written, scope="external", rule="R21")
                    used.append((s, end))
                else:
                    self.dropped["目録に無い符号（参照の語なし）"] += 1
                continue
            if loc is None:
                if target == D.name:
                    self.dropped["自分の文書の識別子だけの言及"] += 1
                else:
                    self._add(D, c, s, e, "文書番号のみ", "", target, written=written, rule="R11")
                used.append((s, e))
                continue
            form = {"section": "文書番号＋項", "article": "文書番号＋項", "label": "文書番号＋別表・図", "name": "文書番号＋見出し名", "note": "注"}[loc["kind"]]
            if target == D.name:
                form = {"文書番号＋項": "同じ文書の項", "文書番号＋別表・図": "別表・図・添付資料", "文書番号＋見出し名": "名称のみ", "注": "注"}[form]
            positions, note = self._resolve(T, "main", loc)
            if loc["kind"] == "name" and not positions:
                if target == D.name:
                    self.dropped["自分の文書の識別子だけの言及"] += 1
                    used.append((s, e))
                    continue
                self._add(D, c, s, e, "文書番号のみ", "", target, written=written, rule="R11")
                used.append((s, e))
                continue
            self._add(D, c, s, loc["end"], form, loc["text"], target, positions, written=written, note=note, rule="R12" if loc["kind"] != "name" else "R13")
            used.append((s, loc["end"]))

        # R16-R17: 別表・図・添付資料など
        for s, e, typ, num, is_def, foreign in D.label_occ[c.pos]:
            if is_def or foreign or not free(s, e):
                continue
            loc = self._label_loc(c, s, e, typ, num)
            other, how = self._owner_before(D, c, s, toks)
            used.append((s, loc["end"]))
            if other == "?":
                self._add(D, c, s, loc["end"], "名称のみ", loc["text"], None, written=how, scope="external", note="名称だけで指す文書の中の場所。目録の名称と一致せず、結べない", rule="R22c")
                continue
            T = self.cat.doc[other] if other else D
            positions, note = self._resolve(T, T.scope_at(c.pos, s) if T is D else "main", loc)
            self._add(D, c, s, loc["end"], "文書番号＋別表・図" if other else "別表・図・添付資料", loc["text"], T.name, positions, written=other and how, note=note,
                      rule=("R25" if how == "同" else "R22b") if other else ("R16" if not loc.get("sub") and not loc.get("nums") else "R17"))

        # R05/R12/R22b: 第 N 条、第 N 章（直前に目録の文書の名称があれば、その文書の条）
        for rx in (ART_RX, CHAP_RX):
            for m in rx.finditer(text):
                s, e = m.span()
                if not free(s, e):
                    continue
                used.append((s, e))
                ls, _ = c.line(s)
                if not c.flat[ls:s].strip(" #>*-") and re.match(r"[ ((]|$", text[e:e + 1] or " ") and not (rx is ART_RX and m.group("p")):
                    continue  # 見出しそのもの
                other, how = self._owner_before(D, c, s, toks)
                if other == "?":
                    self._add(D, c, s, e, "名称のみ", m.group(), None, written=how, scope="external", note="名称だけで指す文書の中の場所。目録の名称と一致せず、結べない", rule="R22c")
                    continue
                T = self.cat.doc[other] if other else D
                if rx is ART_RX:
                    loc = {"kind": "article", "art": (kanji_int(m.group("a")), kanji_int(m.group("b")) if m.group("b") else 0),
                           "para": kanji_int(m.group("p")) if m.group("p") else None, "text": m.group(), "end": e}
                else:
                    loc = {"kind": "chapter", "key": (m.group("k"), kanji_int(m.group("n"))), "text": m.group(), "end": e}
                positions, note = self._resolve(T, "main", loc)
                nxt = rx.match(text, e + len(re.match(r"[ ]?(?:から|乃至|ないし|~|〜)?[ ]?", text[e:]).group())) if rx is ART_RX and re.match(r"[ ]?(?:から|乃至|ないし|~|〜)", text[e:]) else None
                if nxt and positions and not loc["para"]:  # 範囲の間の条を足す
                    a, b = loc["art"][0], kanji_int(nxt.group("a"))
                    for k in range(a + 1, min(b, a + 6)):
                        positions = positions + [p for p in (T.article_chunks((k, 0)) or [])[:1] if p not in positions]
                    note = (note + " 第 %d 条までの範囲として、間の条も結んだ" % b).strip()
                self._add(D, c, s, e, "文書番号＋項" if other else "同じ文書の項", m.group(), T.name, positions, written=other and how, note=note, rule=("R25" if how == "同" else "R22b") if other else "R14")

        # R05: 条のある文書で、条を言わずに項だけを指す「第 N 項」は、属する条の項
        if D.has_articles:
            for m in PARA_RX.finditer(text):
                s, e = m.span()
                if not free(s, e):
                    continue
                used.append((s, e))
                cur = D.article_at(c.pos, s)
                got = D.article_chunks(cur, kanji_int(m.group("p"))) if cur else None
                self._add(D, c, s, e, "同じ文書の項", m.group(), D.name, got or [], note="属する条（第 %d 条）の項とみなした" % cur[0] if cur else "属する条が分からない", rule="R05")

        # R19: 相対的な参照
        for m in REL_RX.finditer(text):
            s, e = m.span()
            if not free(s, e):
                continue
            word = re.sub(r"\s", "", m.group("w"))
            used.append((s, e))
            if word.startswith("同"):
                pending.append((s, e, word))
                continue
            positions, note = self._relative(D, c, s, word, m.group("k"))
            self._add(D, c, s, e, "相対的な参照", m.group(), D.name, positions, note=note, rule="R19")

        # R14-R15: 項番号
        for m in NUMLIST_RX.finditer(text):
            s, e = m.span()
            if not free(s, e):
                continue
            elems = parse_numlist(m)
            pre, suffixed = bool(m.group("pre")), any(x[2] for x in elems)
            multi = all(len(x[0]) >= 2 or x[2] or x[1] for x in elems)
            follow = text[e:e + 1]
            cue = bool(re.match(r"[ ]?(?:を)?" + CUE, text[e:]))
            bare = multi and len(elems[0][0]) >= 2 and (s == 0 or text[s - 1] in " \n(、はをにでがとも") and bool(re.match(r"[ ]?(?:では|には|にも|の|に|を|で|は|から|まで|及び|又は|並びに|、|と|\))", text[e:]))
            ok = suffixed or (pre and (len(elems[0][0]) >= 2 or follow in "のにをでとは及、,)")) or (cue and multi and len(elems[0][0]) >= 2) or bare
            if not ok:
                continue
            if any((p, o) == (c.pos, s) for p, o, _, _ in D.heads) and not pre:
                if not suffixed:
                    continue
            loc = {"kind": "section", "nums": elems, "text": m.group().strip(), "end": e}
            other, how = self._owner_before(D, c, s, toks) if not pre else (None, "")
            if other == "?":
                if suffixed or cue:
                    self._add(D, c, s, e, "名称のみ", loc["text"], None, written=how, scope="external", note="名称だけで指す文書の中の場所。目録の名称と一致せず、結べない", rule="R22c")
                    used.append((s, e))
                continue
            T = self.cat.doc[other] if other else D
            positions, note = self._resolve(T, D.scope_at(c.pos, s) if T is D else "main", loc)
            if not suffixed and not pre and (not positions or "目録に無い項" in note):
                self.dropped["番号＋参照の語（目録で引けず捨てた）"] += 1
                continue
            self._add(D, c, s, e, "文書番号＋項" if other else "同じ文書の項", loc["text"], T.name, positions, written=other and how, note=note,
                      rule=("R25" if how == "同" else "R22b") if other else ("R14" if len(elems) == 1 else "R15") + ("c" if not (suffixed or pre or cue) else ""))
            used.append((s, e))
        for m in SINGLE_DOT_RX.finditer(text):
            s, e = m.span()
            if not free(s, e) or any((p, o) == (c.pos, s) for p, o, _, _ in D.heads):
                continue
            loc = {"kind": "section", "nums": [((int(m.group("n")),), None, "", None)], "text": m.group().strip(), "end": e}
            other, how = self._owner_before(D, c, s, toks)
            if other == "?":
                continue
            T = self.cat.doc[other] if other else D
            positions, note = self._resolve(T, D.scope_at(c.pos, s) if T is D else "main", loc)
            if positions or T.sections.get("main", {}).get((int(m.group("n")),)):
                self._add(D, c, s, e, "文書番号＋項" if other else "同じ文書の項", loc["text"], T.name, positions, written=other and how, note=note, rule="R25" if other else "R14")
                used.append((s, e))

        # R18: 注
        for m in NOTE_RX.finditer(text):
            s, e = m.span()
            ls, _ = c.line(s)
            if not free(s, e) or not c.flat[ls:s].strip(" >*-"):
                continue  # 行頭は注の定義
            n = int(m.group("n"))
            other, how = self._owner_before(D, c, s, toks)
            used.append((s, e))
            if other == "?":
                self._add(D, c, s, e, "名称のみ", m.group(), None, written=how, scope="external", note="名称だけで指す文書の中の場所。目録の名称と一致せず、結べない", rule="R22c")
                continue
            T = self.cat.doc[other] if other else D
            cand = T.notes.get(n, [])
            near = sorted(cand, key=lambda p: (abs(p - c.pos), p < c.pos))[:1] if T is D else cand[:1]
            self._add(D, c, s, e, "注", m.group(), T.name, near, written=other and how, note="" if near else "注の定義が目録に無い", rule="R18")

        # R22: 囲んだ名称
        for m in QUOTE_RX.finditer(text):
            s, e = m.span()
            name = m.group("name")
            if not free(s, e) or CODE_RX.search(name) and any(a < e and s < b for a, b in used):
                continue
            after = text[e:e + 24]
            if not (re.match(r"[ ]?(?:\([^)\n]{1,12}\))?[ ]?(?:の[^。、\n]{0,8})?" + CUE, after) or re.match(r"[ ]?\(", after) and any(a >= e and a - e <= 3 for a, b in used)):
                continue
            key = squeeze(name)
            here = [p for p in D.titles.get(key, [])]
            other = self.cat.resolve_name(name, D.name)
            if here:
                self._add(D, c, s, e, "名称のみ", name, D.name, here[:3], rule="R22")
            elif other and other != D.name:
                self._add(D, c, s, e, "名称のみ", "", other, written=name, rule="R22")
            elif re.search(DOC_SUFFIX + r"$", key) or CODE_RX.search(name):
                self._add(D, c, s, e, "外部の文書", "", None, written=name, scope="external", rule="R22")
            else:
                continue
            used.append((s, e))

        # R23: ページ
        for m in PAGE_RX.finditer(text):
            s, e = m.span()
            if not free(s, e):
                continue
            page = int(m.group("p1") or m.group("p2"))
            positions = [x.pos for x in D.chunks if x.page == page][:3]
            self._add(D, c, s, e, "その他", m.group(), D.name, positions, note="ページ" if positions else "そのページのチャンクが無い", rule="R23")
            used.append((s, e))

        # R19: 同条・同項は、同じチャンクの中で直前に挙げた参照と同じ先
        for s, e, word in pending:
            want = {"条": "条", "項": "項", "号": "号"}[word[-1]]
            prev = [r for r in self.refs[first_ref:] if r["span"][1] <= s and s - r["span"][1] <= 200 and (want in (r["locator"] or "") or (want == "項" and r["form"] in ("同じ文書の項", "文書番号＋項")))]
            if prev:
                a = max(prev, key=lambda r: r["span"][1])
                T = self.cat.doc.get(a["target_doc"])
                self._add(D, c, s, e, "相対的な参照", text[s:e], a["target_doc"], [t["position"] for t in a["targets"]] if T else [], written=a["target_document"], scope=a["scope"],
                          note="直前の参照（%s）と同じ先" % (a["locator"] or a["target_document"]), rule="R19")
            else:
                self._add(D, c, s, e, "相対的な参照", text[s:e], D.name, [], note="先行する参照が同じチャンクの中に無く、決められない", rule="R19")

        # R20: 番号の無い上記・下記
        for m in VAGUE_RX.finditer(text):
            if free(m.start(), m.end()) and not re.match(r"[ ]?(?:第[ ]?)?\d", text[m.end():]):
                self.vague.append({"source_doc": D.name, "source_position": c.pos, "quote": c.quote(m.start(), m.end())[:80]})

    def _owner_before(self, D, c, s, toks):
        """位置 s の場所の表現が、どの文書の中の場所かを、直前の語から決める。

        戻り値は（文書名, 手がかり）。自分の文書なら (None, "")。目録の名称に続くなら（その文書, "名称"）、
        「同基準」「同規定」に続くなら（直前に挙げた文書, "同"）、目録に無い名称や旧番号に続くなら ("?", その語)。
        """
        raw = c.norm[max(0, s - 40):s]
        head = squeeze(raw)
        head = head[:-1] if head.endswith("の") else head
        hit = {name for name, ns in self.cat.names.items() if name != D.name and any(head.endswith(n) for n in ns)}
        if len(hit) == 1:
            return hit.pop(), "名称"
        m = re.search(ANA_RX.pattern + r"[ ]?の?[ ]?$", raw)
        if m:
            start = s - (len(raw) - m.start())
            prev = [t for t in toks if t[1] <= start and t[3] and t[3] != "?"]
            if prev and start - prev[-1][1] <= 150:
                return (prev[-1][3] if prev[-1][3] != D.name else None), "同"
            return "?", m.group().strip(" の")
        if OWN_BEFORE_RX.search(raw) or any(head.endswith(n) for n in self.cat.names.get(D.name, ())):
            return None, ""
        m = FOREIGN_BEFORE_RX.search(raw)
        if m:
            word = re.search(r"[^\s、。,()]{1,16}$", raw[:m.end()].rstrip(" の"))
            return "?", word.group() if word else m.group().strip()
        m = re.search(r"([^\s、。,()「」『』|:;]{1,16})[ ]?の[ ]?$", raw)
        if m:  # 「X の 3-2」の X が何か分からない（略称や通称など）
            x = m.group(1)
            if re.match(r"(?:この|本|当|上記|前記|下記|後記|当該|これら|それ|同)", x) or x[-1] in "0123456789.)項条章節表図":
                return None, ""
            return "?", x
        return None, ""

    # -- 場所の表現の読み取り --
    def _label_loc(self, c, s, e, typ, num):
        text = c.norm
        loc = {"kind": "label", "key": (typ, num), "text": text[s:e], "end": e}
        m = SUBLOC_RX.match(text, e)
        if m:
            loc.update(sub=m.group("sub"), end=m.end(), text=text[s:m.end()])
            return loc
        m = re.compile(r"[ ]?の[ ]?" + NUMLIST).match(text, e)
        if m and any((m.group("s%d" % i) or "") for i in range(6) if m.group("n%d" % i)):
            loc.update(nums=parse_numlist(m), end=m.end(), text=text[s:m.end()])
        return loc

    def _locator_at(self, c, p, T, known):
        """識別子の直後（p から）にある場所の表現を読む。"""
        text = c.norm
        for rx in (PART_RX, FLOAT_RX):
            m = rx.match(text, p)
            typ = m and TYPE_ALIAS.get(re.sub(r"\s", "", m.group("type")), re.sub(r"\s", "", m.group("type")))
            if m and (m.group("num") or typ in UNNUMBERED_OK):
                num = m.group("num")
                if num is not None:
                    num = str(kanji_int(num)) if kanji_int(num) is not None else num.replace(".", "-")
                return self._label_loc(c, p, m.end(), typ, num)
        m = ART_RX.match(text, p)
        if m:
            return {"kind": "article", "art": (kanji_int(m.group("a")), kanji_int(m.group("b")) if m.group("b") else 0),
                    "para": kanji_int(m.group("p")) if m.group("p") else None, "text": m.group(), "end": m.end()}
        m = re.compile(r"\(?[ ]?注[ ]?(?P<n>\d{1,2})[ ]?\)?").match(text, p)
        if m:
            return {"kind": "note", "n": int(m.group("n")), "text": m.group().strip(), "end": m.end()}
        after_no = (p > 0 and text[p - 1] == "の") or text[max(0, p - 2):p] == "の "
        m = re.compile(NUMLIST).match(text, p)
        if m:
            elems = parse_numlist(m)
            if any(x[2] for x in elems) or (len(elems[0][0]) >= 2 and (after_no or re.match(r"[ ]?(?:を)?" + CUE, text[m.end():]))):
                return {"kind": "section", "nums": elems, "text": m.group().strip(), "end": m.end()}
        m = re.compile(r"(?P<n>[1-9]\d?)\.(?=[ ]?(?:" + CUE + r"|の|を|に|及び|、))").match(text, p)
        if m and after_no:
            return {"kind": "section", "nums": [((int(m.group("n")),), None, "", None)], "text": m.group(), "end": m.end()}
        if "R13" not in ABLATE and (p > 0 and text[p - 1] == "の" or text[p - 2:p] == "の "):
            m = NAME_LOC_RX.match(text, p)
            if m and re.search("[" + KANJI + "ァ-ヶ]{2}", m.group("name")):
                return {"kind": "name", "name": m.group("name"), "text": m.group("name"), "end": m.end()}
        return None

    # -- 場所 → チャンク --
    def _resolve(self, T, scope, loc):
        """場所の表現を、文書 T のチャンクの順番の一覧にする。（一覧, 注記）を返す。"""
        if T is None:
            return [], ""
        if loc["kind"] == "note":
            hits = T.notes.get(loc["n"], [])
            return hits[:1], "" if hits else "注の定義が目録に無い"
        if loc["kind"] == "name":
            hits = T.titles.get(squeeze(loc["name"]), [])
            return sorted(set(hits))[:3], "" if hits else "題名の一致する見出しが無い"
        if loc["kind"] == "article":
            got = T.article_chunks(loc["art"], loc.get("para"))
            return (got or []), "" if got else "条が目録に無い"
        if loc["kind"] == "chapter":
            defs = T.chapters.get(loc["key"])
            if defs:
                return [min(defs)[0]], ""
            got = T.section_chunks("main", (loc["key"][1],)) if loc["key"][0] == "章" else None
            return (got or []), "" if got else "章・節が目録に無い"
        if loc["kind"] == "label":
            span = T.label_chunks(loc["key"])
            if loc.get("sub"):
                hits = T.find_line_start(loc["sub"], span) if span else []
                if hits:
                    return hits[:3], ""
                if span:
                    return span, "下位の指定は範囲の中に見つからず、場所全体へ結んだ"
                hits = T.find_line_start(loc["sub"]) if "R17b" not in ABLATE else []
                if 0 < len(hits) <= 3:
                    return hits, "題名は目録に無く、下位の指定の行頭から結んだ（R17b）"
                return [], "題名が目録に無い"
            if loc.get("nums"):
                if not span:
                    return [], "題名が目録に無い"
                key = loc["key"][0] + (loc["key"][1] or "")
                out, miss = [], []
                for t, item, suf, join in self._expand(T, key, loc["nums"]):
                    got = T.section_chunks(key, t)
                    out += [p for p in (got or []) if p not in out]
                    if not got:
                        miss.append("-".join(map(str, t)))
                return (out[:4] or span), ("" if out else "項は見つからず、場所全体へ結んだ") + (" 見つからない項: " + "、".join(miss) if miss and out else "")
            return (span or []), "" if span else "題名が目録に無い"
        # section
        out, miss, notes = [], [], []
        for t, item, suf, join in self._expand(T, scope, loc["nums"]):
            got = None
            for sc in ([scope, "main"] if scope != "main" else ["main"]):
                got = T.section_chunks(sc, t)
                if item is not None:
                    hit = T.items.get((sc, t, ")", item))
                    if hit:
                        got = [hit[0][0]]
                if got:
                    break
                if len(t) >= 2 and "R14b" not in ABLATE:  # R14b
                    hit = T.items.get((sc, t[:-1], ")", t[-1])) or T.items.get((sc, t[:-1], "()", t[-1]))
                    if hit and not any(k[:len(t) - 1] == t[:-1] and len(k) >= len(t) for k in T.sections.get(sc, {})):
                        got = [hit[0][0]]
                        notes.append("%s は見出しに無く、箇条 %d) へ結んだ（R14b）" % ("-".join(map(str, t)), t[-1]))
                        break
            if got:
                out += [p for p in got if p not in out]
            else:
                miss.append("-".join(map(str, t)))
        if miss:
            notes.append("目録に無い項: " + "、".join(miss))
        return out[:5], " ".join(notes)

    def _expand(self, T, scope, elems):
        """範囲（A〜B）を、目録にある同じ深さの番号へ広げる。"""
        out = []
        for i, (t, item, suf, join) in enumerate(elems):
            if join and re.fullmatch(RANGE, join) and out:
                a = out[-1][0]
                if len(a) == len(t) and a[:-1] == t[:-1]:
                    known = set()
                    for sc in (scope, "main"):
                        known |= {k for k in T.sections.get(sc, {}) if len(k) == len(t) and k[:-1] == t[:-1] and a < k < t}
                    out += [(k, None, suf, None) for k in sorted(known)]
            out.append((t, item, suf, join))
        return out

    def _relative(self, D, c, off, word, k):
        kind = word[-1]
        step = (kanji_int(k) or int(k)) if k else 1
        if D.art_marks:
            cur = D.article_at(c.pos, off)
            if cur is None:
                return [], "条の外にあり、数えられない"
            if kind == "条":
                want = {"前": (cur[0] - step, 0), "次": (cur[0] + 1, 0)}.get(word[0], cur)
                if word.startswith("同"):
                    prev = [m for m in ART_RX.finditer(c.norm[:off])]
                    want = (kanji_int(prev[-1].group("a")), 0) if prev else cur
                got = D.article_chunks(want)
                if step > 1 and word[0] == "前":
                    got = [p for k in range(1, step + 1) for p in (D.article_chunks((cur[0] - k, 0)) or [])]
                return (got or []), "" if got else "数えた先の条が目録に無い"
            if kind == "項":  # 条の中の項。行頭の番号で数える（第 1 項は条の先頭）
                para = D.para_at(c.pos, off)
                n = para[1] if para and para[0] == cur else 1
                want = {"前": n - step, "次": n + 1}.get(word[0], n)
                got = D.article_chunks(cur, want) if want >= 1 else None
                return (got or []), ("第 %d 項を指すとみなした" % want) if got else "数えた先の項が目録に無い"
            defs = D.articles.get(cur)  # 号は条の中。条の先頭のチャンクへ（同じチャンクならリンクなし）
            return [min(defs)[0]], "号の位置までは数えず、属する条の先頭へ"
        item = D.item_at(c.pos, off)
        sect = D.section_at(c.pos, off)
        if kind in "項号" and item and (sect is None or (item[0], item[1]) >= self._sect_pos(D, sect)):
            n = {"前": item[2] - step, "次": item[2] + 1}.get(word[0], item[2])
            defs = D.items.get((item[3], item[4], item[5], n), [])
            hit = [(p, o) for p, o in defs if (p, o) <= (c.pos, off)] if word[0] != "次" else [(p, o) for p, o in defs if (p, o) >= (c.pos, off)]
            if hit:
                return [hit[-1][0] if word[0] != "次" else hit[0][0]], "同じ並びの %d 番目の箇条を指すとみなした" % n
            return [], "数えた先の箇条が目録に無い"
        if sect:
            scope, t = sect
            n = {"前": t[-1] - step, "次": t[-1] + 1}.get(word[0], t[-1])
            got = D.section_chunks(scope, t[:-1] + (n,)) if n >= 1 else None
            return (got or []), ("" if got else "数えた先の項が目録に無い")
        return [], "属する項が分からない"

    @staticmethod
    def _sect_pos(D, sect):
        defs = D.sections[sect[0]][sect[1]]
        return max((p, o) for p, o, _ in defs)


def locator_given(form):
    return form not in ("文書番号のみ", "外部の文書")


# ───────────────────────── 採点（score_refs.py と同じ基準） ─────────────────────────
FORM_OF_KIND = {"他の文書の項": "文書番号＋項", "同じ文書の項": "同じ文書の項", "同じ文書の図": "別表・図・添付資料", "同じ文書の添付": "別表・図・添付資料",
                "同じ文書の別表": "別表・図・添付資料", "同じ文書の別紙": "別表・図・添付資料", "同じ文書": "文書番号のみ"}
FORMS = ["文書番号＋項", "文書番号＋見出し名", "文書番号のみ", "同じ文書の項", "別表・図・添付資料", "注", "相対的な参照", "名称のみ", "その他"]


def gold_form(g):
    if g["kind"] == "他の文書":
        return "文書番号のみ" if g["level"] == "doc" else "文書番号＋見出し名"
    return FORM_OF_KIND.get(g["kind"], "その他")


def score(refs, gold):
    """score_refs.py と同じ照合。各正解に、抽出・文書・チャンクの判定を付ける。"""
    used, rows = set(), []
    for g in gold:
        hits = [i for i, r in enumerate(refs) if r["source_doc"] == g["src"] and squeeze(g["key"]) in squeeze(r["quote"])]
        used.update(hits)
        row = {"src": g["src"], "key": g["key"], "kind": g["kind"], "form": gold_form(g), "level": g["level"], "optional": bool(g.get("optional")),
               "extracted": bool(hits), "doc_ok": False, "chunk": "なし", "links": 0, "links_ok": 0}
        loose = False
        for i in hits:
            r = refs[i]
            row["doc_ok"] = row["doc_ok"] or r["target_doc"] == g["target"]
            for t in r["targets"]:
                row["links"] += 1
                text = squeeze(t["text"])
                row["links_ok"] += r["target_doc"] == g["target"] and any(squeeze(a) in text for a in g["accept"])
                loose = loose or (r["target_doc"] == g["target"] and any(squeeze(a) in text for a in g.get("accept_loose", [])))
        if row["links_ok"]:
            row["chunk"] = "正"
        elif row["links"]:
            row["chunk"] = "近い" if loose else "誤"
        if g["level"] == "chunk":
            row["verdict"] = {"正": "correct", "近い": "wrong", "誤": "wrong", "なし": "unresolved"}[row["chunk"]]
        else:
            row["verdict"] = "correct" if row["doc_ok"] else ("wrong" if any(refs[i]["target_doc"] for i in hits) else "unresolved")
        rows.append(row)
    return rows, used


def summarize(rows):
    need = [r for r in rows if not r["optional"]]
    c = collections.Counter(r["verdict"] for r in need)
    return {"gold_total": len(need), "extracted": sum(r["extracted"] for r in need), "correct": c["correct"], "wrong": c["wrong"], "unresolved": c["unresolved"],
            "chunk_level": {"total": sum(r["level"] == "chunk" for r in need), "correct": sum(r["level"] == "chunk" and r["verdict"] == "correct" for r in need)},
            "doc_level": {"total": sum(r["level"] == "doc" for r in need), "correct": sum(r["level"] == "doc" and r["verdict"] == "correct" for r in need)},
            "links_in_gold_refs": sum(r["links"] for r in need), "links_ok_in_gold_refs": sum(r["links_ok"] for r in need)}


# ───────────────────────── 表示と出力 ─────────────────────────
def show_catalogue(cat):
    for name, D in cat.doc.items():
        print("■", name, "識別子:", sorted("-".join(f) for f in cat.codes[name]), "名称:", sorted(cat.names[name]))
        for scope, table in D.sections.items():
            print("  範囲", scope, ":", " ".join("%s@%s" % ("-".join(map(str, t)), ",".join(str(p) for p, _, _ in v)) for t, v in sorted(table.items())))
        print("  別表・図など:", {k[0] + (k[1] or ""): [p for p, _, _ in v] for k, v in D.labels.items()})
        print("  範囲の切り替え:", [(p, o, s) for p, o, s in D.scopes])
        print("  注:", dict(D.notes), " 条:", {k: [p for p, _ in v] for k, v in D.articles.items()})
        print("  捨てた見出し候補:", [(p, "-".join(map(str, t)), ttl) for p, _, t, ttl in D.rejected])


def show_refs(refs):
    for r in refs:
        print("[%s#%d] %-10s %-18s → %s %s %s | %s" % (r["source_doc"][:13], r["source_position"], r["form"], (r["locator"] or r["target_document"] or "")[:18],
                                                     (r["target_doc"] or "外部")[:13], [t["position"] for t in r["targets"]], r["level"], r["note"]))


def slim(r):
    out = {k: v for k, v in r.items() if k not in ("targets", "span")}
    out["targets"] = [{"id": t["id"], "position": t["position"], "head": t["text"][:60]} for t in r["targets"]]
    return out


def selftest():
    """条の形の作り物の例（実物ではない）で、規則が動くことだけを確かめる。"""
    rows = [
        {"id": "a1", "fileName": "就業規則.pdf", "position": 1, "page": 1, "text": "[就業規則]\n# 就業規則\n第1章 総則\n(目的)\n第1条 この規則は、従業員の就業に関する事項を定める。\n(適用範囲)\n第2条 この規則は、第3条に定める従業員に適用する。\n2 前項の規定にかかわらず、嘱託については別表第1による。"},
        {"id": "a2", "fileName": "就業規則.pdf", "position": 2, "page": 2, "text": "[就業規則]\n(定義)\n第3条 従業員とは、第2章の手続により採用された者をいう。\n(休暇)\n第4条 年次有給休暇は、前条の従業員に与える。手続は休暇規程(HR-012)第5条による。\n第5条 前2条に定めるもののほか、必要な事項は別に定める。"},
        {"id": "a3", "fileName": "就業規則.pdf", "position": 3, "page": 3, "text": "[就業規則]\n別表第1\n嘱託の勤務時間\n区分A 週3日"},
        {"id": "b1", "fileName": "HR-012_休暇規程.pdf", "position": 1, "page": 1, "text": "[HR-012_休暇規程]\n# 休暇規程\n第1条 この規程は、就業規則第4条に基づき、休暇の手続を定める。"},
        {"id": "b2", "fileName": "HR-012_休暇規程.pdf", "position": 2, "page": 2, "text": "[HR-012_休暇規程]\n第5条 休暇の申請は、様式1により行う。\n第6条 ISO 27001に定める管理策は、情報セキュリティ規程による。"},
    ]
    docs = collections.defaultdict(list)
    for c in rows:
        docs[c["fileName"]].append(Chunk(c))
    cat = Catalogue(docs)
    show_catalogue(cat)
    ex = Extractor(cat)
    show_refs(ex.run())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default=str(HERE.parent / "routes" / "graph_dump.json"))
    ap.add_argument("--gold", default=str(HERE / "gold_refs.json"))
    ap.add_argument("--llm", nargs="*", default=[str(HERE / ("chunk_refs_v%d.json" % i)) for i in (1, 2, 3)])
    ap.add_argument("--out", default=str(HERE / "rule_refs_result.json"))
    ap.add_argument("--verdicts", help="正解に無いリンクを人が確かめた結果（JSON）。結果のファイルに写す")
    ap.add_argument("--show", choices=["catalogue", "refs", "score", "extra"])
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ablate", default="", help="外す規則（R13,R17b,R10tail,R07,R14b）。感度の確認用")
    ap.add_argument("--md-dir", help="Markdown の文書の置き場。正解の無い文書群に規則を当て、件数だけ出す（採点しない）")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    ABLATE.update(x for x in args.ablate.split(",") if x)
    if args.md_dir:
        cat = Catalogue(load_markdown_dir(args.md_dir))
        ex = Extractor(cat)
        refs = ex.run()
        if args.show == "catalogue":
            return show_catalogue(cat)
        if args.show == "refs":
            return show_refs(refs)
        print(json.dumps({"documents": len(cat.docs), "chunks": sum(len(v) for v in cat.docs.values()), "references": len(refs),
                          "documents_without_identifier": sum(not v for v in cat.codes.values()), "documents_with_articles": sum(D.has_articles for D in cat.doc.values()),
                          "by_form": dict(collections.Counter(r["form"] for r in refs)), "by_level": dict(collections.Counter(r["level"] for r in refs)),
                          "unresolved_reasons": dict(collections.Counter(r["note"].split(":")[0] for r in refs if r["level"] == "none" and r["scope"] != "external")),
                          "relative_unlinked": len(ex.vague), "dropped": dict(ex.dropped)}, ensure_ascii=False, indent=1))
        return

    cat = Catalogue(load_chunks(args.chunks))
    ex = Extractor(cat)
    refs = ex.run()
    if args.show == "catalogue":
        return show_catalogue(cat)
    if args.show == "refs":
        return show_refs(refs)

    gold = json.load(open(args.gold, encoding="utf-8"))["references"]
    rule_rows, used = score(refs, gold)
    rule_sum = summarize(rule_rows)
    llm = {}
    for path in args.llm:
        if pathlib.Path(path).exists():
            rows, _ = score(json.load(open(path, encoding="utf-8"))["references"], gold)
            llm[pathlib.Path(path).name] = {"summary": summarize(rows), "rows": rows}
    best = max(llm, key=lambda k: (llm[k]["summary"]["correct"], llm[k]["summary"]["chunk_level"]["correct"])) if llm else None
    best_rows = llm[best]["rows"] if best else []
    union = sum(1 for a, b in zip(rule_rows, best_rows) if not a["optional"] and (a["verdict"] == "correct" or b["verdict"] == "correct")) if best else None
    any_llm = sum(1 for i, a in enumerate(rule_rows) if not a["optional"] and (a["verdict"] == "correct" or any(v["rows"][i]["verdict"] == "correct" for v in llm.values())))

    by_form = []
    for form in FORMS:
        idx = [i for i, r in enumerate(rule_rows) if r["form"] == form and not r["optional"]]
        by_form.append({"form": form, "gold": len(idx), "rule_correct": sum(rule_rows[i]["verdict"] == "correct" for i in idx),
                        "llm_correct": sum(best_rows[i]["verdict"] == "correct" for i in idx) if best else None})

    extra = [r for i, r in enumerate(refs) if i not in used]
    linked = [r for r in extra if r["level"] in ("chunk", "document")]
    extra_sum = {"references": len(extra), "by_level": dict(collections.Counter(r["level"] for r in extra)), "by_form": dict(collections.Counter(r["form"] for r in extra)),
                 "chunk_links": sum(len(r["targets"]) for r in extra), "document_links": sum(r["level"] == "document" for r in extra)}
    stats = {"references": len(refs), "by_form": dict(collections.Counter(r["form"] for r in refs)), "by_level": dict(collections.Counter(r["level"] for r in refs)),
             "chunk_links": sum(len(r["targets"]) for r in refs), "document_links": sum(r["level"] == "document" for r in refs),
             "relative_unlinked": len(ex.vague), "dropped": dict(ex.dropped)}

    if args.show == "score" or not args.show:
        print("規則:", json.dumps(rule_sum, ensure_ascii=False))
        for k, v in llm.items():
            print("LLM %s:" % k, json.dumps(v["summary"], ensure_ascii=False))
        print("最良の LLM:", best, " 規則と最良の LLM の和集合:", union, " 規則と LLM 3 回のどれかの和集合:", any_llm)
        print("型ごと:", json.dumps(by_form, ensure_ascii=False))
        print("-- 規則で正しく結べなかった正解")
        for i, r in enumerate(rule_rows):
            if r["verdict"] != "correct":
                print("  [%s] %-30s 抽出=%s 文書=%s チャンク=%s%s  LLM=%s" % (r["src"][:12], r["key"][:30], "○" if r["extracted"] else "×", "○" if r["doc_ok"] else "×", r["chunk"],
                                                                     "（任意）" if r["optional"] else "", best_rows[i]["verdict"] if best else "-"))
        print("正解に無い参照:", json.dumps(extra_sum, ensure_ascii=False))
        print("全体:", json.dumps(stats, ensure_ascii=False))
    if args.show == "extra":
        for n, r in enumerate(linked):
            print("%3d [%s#%d] %s | %s | → %s %s %s\n      %s" % (n, r["source_doc"][:13], r["source_position"], r["form"], r["locator"] or r["target_document"], (r["target_doc"] or "")[:13],
                                                              [t["position"] for t in r["targets"]], r["note"], r["quote"][:110].replace("\n", "⏎")))
        return

    if args.no_write or ABLATE:
        return
    ablation = {}
    for off in (["R13"], ["R17b"], ["R10tail"], ["R07"], ["R14b"], ["R13", "R17b", "R10tail", "R07", "R14b"]):
        ABLATE.clear()
        ABLATE.update(off)
        rows2, _ = score(Extractor(Catalogue(load_chunks(args.chunks))).run(), gold)
        ablation["+".join(off) + " を外す"] = {k: v for k, v in summarize(rows2).items() if k in ("extracted", "correct", "wrong", "unresolved")}
    ABLATE.clear()
    print("感度:", json.dumps(ablation, ensure_ascii=False))
    verdicts = json.load(open(args.verdicts, encoding="utf-8")) if args.verdicts else None
    result = {
        "_note": "規則だけで張った参照リンクの試作の結果。採点は score_refs.py と同じ基準（抽出は原文の一部の一致、文書は参照先の文書の一致、チャンクは結んだチャンクに accept の語があるか）。",
        "inputs": {"chunks": args.chunks, "gold": args.gold, "llm": args.llm},
        "gold_total": rule_sum["gold_total"],
        "rule": rule_sum,
        "llm": {k: v["summary"] for k, v in llm.items()},
        "llm_best": best,
        "union_correct_rule_and_best_llm": union,
        "union_correct_rule_and_any_llm": any_llm,
        "by_form": by_form,
        "per_gold": [{"src": a["src"], "key": a["key"], "form": a["form"], "level": a["level"], "optional": a["optional"], "rule": a["verdict"], "rule_links": a["links"], "rule_links_ok": a["links_ok"],
                      **{k: v["rows"][i]["verdict"] for k, v in llm.items()}} for i, a in enumerate(rule_rows)],
        "stats": stats,
        "extra": extra_sum,
        "extra_links": [slim(r) for r in linked],
        "manual_checks": verdicts,
        "ablation": ablation,
        "catalogue": {name: {"identifiers": sorted("-".join(f) for f in cat.codes[name]), "names": sorted(cat.names[name]),
                             "sections": {sc: {"-".join(map(str, t)): sorted({p for p, _, _ in v}) for t, v in sorted(tb.items())} for sc, tb in D.sections.items()},
                             "labels": {k[0] + (k[1] or ""): sorted({p for p, _, _ in v}) for k, v in D.labels.items()},
                             "notes": {str(k): sorted(set(v)) for k, v in D.notes.items()},
                             "rejected_heading_candidates": len(D.rejected)} for name, D in cat.doc.items()},
        "references": [slim(r) for r in refs],
        "relative_unlinked": ex.vague,
        "rules_used": RULES,
        "post_scoring_changes": POST_SCORING,
    }
    json.dump(result, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("書き出し:", args.out)


if __name__ == "__main__":
    main()
