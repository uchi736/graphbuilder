# -*- coding: utf-8 -*-
"""規則によるチャンク間の参照の抽出と解決。

LLM もグラフも呼ばない。標準ライブラリだけで動く。入力はチャンクの一覧、出力は参照の一覧である。

手順は、法令文の参照抽出（北野・天笠「法令知識グラフ構築のための法令文における参照関係抽出手法」NLP2026）の
8 段を、社内の規格・規定に移したものである。法令の「法令名・法令番号」を「文書の識別子・名称」に、
「条項番号」を「項番号・条・別表・図・注などの場所の名前」に読み替える。

  段 0 目録づくり     入力のチャンクだけから、文書の識別子・名称・別名（旧番号・通称）と、文書ごとの場所の名前の位置を作る
  段 1 文書の検索     文書の識別子と名称を本文から探し、目録と照合する（論文の ①）
  段 2 略称の定義     「○○（以下「△△」という。）」を拾う（②）
  段 3 略称での再検索 定義より後ろの本文から略称を探す。文書群で定義された別名（旧番号・通称）も探す（③）
  段 4 相対的な参照   前条・次項・同条・同基準・本基準・上記 など（④）
  段 5 直後の番号     段 1・3・4 で見つけた語の直後にある場所の名前。並列と範囲を含めて読む（⑤）
  段 6 単独の番号     残った場所の名前。自分の文書の中の参照とみなす。ただし、直前が法令の名前や目録に無い文書らしい語のものは、
                      自分の文書の場所にしない（手元に無い文書として記録するか、決められないものとして残す）（⑥）
  段 7 具体化         文書と場所を組み合わせ、相対的な参照を具体的な番号に置き換える（⑦）
  段 8 展開           範囲（A から B まで）と並列（A 及び B）を目録にある場所の一覧に直し、チャンクを引く（⑧）

決められない参照は、先頭のものを採らずに残す（reason に理由を書く。呼び出し側が LLM に回す）。同じ番号の条が複数ある文書
（例の並記、附則の振り直し）、同じ番号の箇条が 2 か所にある項、目録に無い語に続く場所が、これに当たる。

当てる順序に結果が依存しないよう、次の 3 つを守る。
  - 段は上の順に 1 回ずつ進む。前の段が取った範囲（文字の区間）は、後の段では使わない
  - 同じ段の中では、長い一致を先に取る（同じ長さなら前にあるもの、それも同じなら種類の決まった順）
  - カギ括弧（「」『』）の中と目次の中は、検索の対象にしない。括弧の全体を 1 つの名称として段 1 で照合するだけである

特定の文書番号・型式名・特定の文書にしか無い言い回しは、どのパターンにも書いていない。

使い方:
    result = find_references(chunks)            # chunks: [{"id", "fileName", "position", "text", "page_number"?, "section"?}]
    result["references"]                        # 参照の一覧（下の _emit の説明を参照）
"""
import bisect
import collections
import pathlib
import re
import unicodedata

# 規則や出力の形を変えたら上げる（呼び出し側が、保存した結果を使い回してよいかの判断に使う）
RULE_VERSION = "2"

# 切り替えられる規則。find_references(chunks, disable=[...]) で外せる（感度の確認用）
#   heading_name   文書の識別子や名称＋「の」＋見出しの名称＋参照の語（「X の○○による」「「○○規定」の、廃棄の定めによる」）を、
#                  題名の一致する見出しへ結ぶ
#   sub_fallback   別表などの題名が目録に無いとき、下位の指定（符号 A など）の見出しが文書に 1 か所だけなら、そこへ結ぶ
#   alias_table    文書をまたいで使う別名（旧番号・通称）を、表紙の欄・旧番号との対照表・「X は「Y」と呼ぶ」から拾い、別名での参照を結ぶ
# どこで確かめた規則か: heading_name は実物 6 文書と架空の 38 文書、sub_fallback は実物 6 文書だけ、alias_table は架空の 38 文書だけである
# （見ていない実物 29 文書では、3 つとも 1 回も発火しなかった）。
OPTIONAL_RULES = ("heading_name", "sub_fallback", "alias_table")

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
    """漢数字（九百九十九まで）か算用数字を整数にする。読めなければ None。"""
    if not s:
        return None
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
KN = r"[一二三四五六七八九十百]"
NUM = r"[1-9]\d?(?:[-.][1-9]\d?){0,3}"
CODE_RX = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,6}[0-9]{0,6}(?:(?: ?- ?| {1,2})[A-Z0-9]{1,12}){0,5}(?![A-Za-z0-9])")
STD_PREFIX = r"(?:JIS|ISO|IEC|JEM|JEC|JASO|ASME|ASTM|ANSI|DIN|EN|BS|IEEE)"
# 参照の語。見に行く先を示す言い方
CUE = (r"(?:によること|による|により|に依る|に依り|に拠る|に拠り|に基づ|に基き|にもとづ|に従|に準|に示す|に定め|に規定|に掲げ|に記載"
       r"|を参照|参照|のとおり|の通り|の要領|を適用|を準用|の値を|で定め)")
CUE_RX = re.compile(CUE)
SEP = r"(?:、|,|及び|並びに|又は|若しくは|および|または|と|・)"      # 並列
RANGE = r"(?:~|〜|から|乃至|ないし)"                              # 範囲
RANGE_RX = re.compile(RANGE)
PART_TYPES = ["添付資料", "添付書類", "添付図", "別表", "別紙", "別図", "別添", "別記", "別冊", "付表", "付図", "附表", "附図",
              "付属書", "附属書", "付録", "附録", "付則", "附則", "様式"]
FLOAT_TYPES = ["写真", "図", "表"]
UNNUMBERED_OK = {"添付資料", "添付書類", "添付図", "別表", "別紙", "別添", "別記", "別冊", "付録", "付則", "付属書"}
TYPE_ALIAS = {"附表": "付表", "附図": "付図", "附属書": "付属書", "附録": "付録", "附則": "付則"}
PART_KEYS = set(TYPE_ALIAS.get(t, t) for t in PART_TYPES)
DOC_SUFFIX = r"(?:規程|規定|規則|基準|要領|要綱|細則|マニュアル|手順書|指針|方針|標準|仕様書|方案|規格|通知|台帳|指示書|チェックシート|要項|内規)"


def _spaced(word):
    return "[ ]?".join(map(re.escape, word))


LABEL_NUM = (r"(?:[ ]?第)?[ ]?-?[ ]?(?P<num>\d{1,3}(?:[.\-]\d{1,2})?|[A-Z](?![A-Za-z0-9])|[一二三四五六七八九十]{1,3}(?![一-龥]))"
             r"(?:[ ]?号)?")
PART_RX = re.compile("(?P<type>" + "|".join(_spaced(t) for t in PART_TYPES) + ")(?:" + LABEL_NUM + ")?")
FLOAT_RX = re.compile("(?P<type>" + "|".join(FLOAT_TYPES) + ")" + LABEL_NUM)
FLOAT2_RX = re.compile(r"第[ ]?(?P<num>\d{1,3})[ ]?(?P<type>図|表)")
SUBLOC_RX = re.compile(r"[ ]{0,3}(?:の[ ]?)?(?P<word>符号|記号|区分|項目|番号|種別|No\.?)[ ]?(?P<val>[A-Za-z0-9ア-ンｱ-ﾝ]{1,3})(?![A-Za-z0-9])")
ELEM = (r"(?P<n@>" + NUM + r")(?!\d)(?:[ ]?(?P<it@>\d{1,2})[ ]?\)(?!\S*\))|\((?P<ip@>\d{1,2})\)(?=[ ]?(?:項|節)))?(?P<s@>[ ]?(?:項|節|章)(?![目点約電件例立]))?")
_ANUM = r"\d{1,3}|" + KN + r"{1,5}"
_PNUM = r"\d{1,2}|" + KN + r"{1,3}"
# 枝番の条（第 7 条の 2）は、後ろに助詞・「第」・「及び」が続いても枝番として読む。枝番としないのは、後ろが「項・号」や数量の語のとき
# （第 5 条の 2 項、第 3 条の 2 つの要件）である。後ろが閉じ括弧のとき（第 7 条の 2)）は、箇条の番号か括弧書きの終わりかが字面では
# 決まらないので、ART_RX では枝番とせず、括弧が開いている場合だけ _read_articles が ART_OPEN_RX で読み直す
_BRANCH_STOP = r"(?![ ]?(?:[\d.\-]|項|号|つ|個|か|ヶ|ケ|箇|点|種|倍|回|件|名|人|台|年|月|日|時|分|週|歳|円|%|割|番|段|行|列|章|節|次|級|等分))"
_ART_HEAD = r"第[ ]?(?P<a>" + _ANUM + r")[ ]?条(?:[ ]?の[ ]?(?P<b>\d{1,2}" + _BRANCH_STOP + r"@|" + KN + r"{1,3}(?![一-龥])))?"
_ART_TAIL = r"(?:[ ]?第?[ ]?(?P<p>" + _PNUM + r")[ ]?項)?(?:[ ]?第[ ]?(?P<g>" + _PNUM + r")[ ]?号)?"
ART_RX = re.compile(_ART_HEAD.replace("@", r"(?![ ]?\))") + _ART_TAIL)
ART_OPEN_RX = re.compile(_ART_HEAD.replace("@", "") + _ART_TAIL)
ART_TAIL_RX = re.compile(r"第[ ]?(?P<p>" + _PNUM + r")[ ]?項(?:[ ]?第[ ]?(?P<g>" + _PNUM + r")[ ]?号)?|第[ ]?(?P<g2>" + _PNUM + r")[ ]?号")
PARA_RX = re.compile(r"第[ ]?(?P<p>" + _PNUM + r")[ ]?項(?:[ ]?第[ ]?(?P<g>" + _PNUM + r")[ ]?号)?")
CHAP_RX = re.compile(r"第[ ]?(?P<n>" + _PNUM + r")[ ]?(?P<k>章|節|編|款)")
JOIN_RX = re.compile(r"[ ]?(?P<j>" + SEP + "|" + RANGE + r")[ ]?")
UNTIL_RX = re.compile(r"[ ]?まで")
# 段 4 の語
# 後ろに続くと別の語になる字（条件、項目、号機、条文、章立て など）だけを除く。「前条及び」「同条第 2 項」は相対的な参照として読む
REL_RX = re.compile(r"(?<![一-龥])(?P<w>前各[条項号]|前[ ]?(?P<k>\d|[二三四五])?[ ]?[条項号節章]|次[条項号節章]|本[条項号節章]|同[条項号])(?![目件機数文約例立室車線館棟炉船艇])")
ANA_RX = re.compile(r"(?<![一-龥])同[ ]?(?:基準|規定|規程|規則|要領|要綱|細則|文書|書|法|令|マニュアル|手順書|標準|仕様書|規格)(?![人的類])")
OWN_RX = re.compile(r"(?<![一-龥ァ-ヶ])(?:この|本|当)[ ]?(?:" + DOC_SUFFIX + r"|書|法律|法|政令|省令|条例|則)")
PRE_RX = re.compile(r"(?<![一-龥])(?:上記|前記|下記|後記|前述|後述|上述)")
NOTE_RX = re.compile(r"[(\[]?[ ]?(?:注|※)[ ]?(?P<n>\d{1,2})(?![ ]?(?:\d|日|時|分|年|月|回|個|人|名|円|%))[ ]?[)\]]?")  # 「※85日」は注の番号ではない
NOTE_AFTER_RX = re.compile(r"\(?[ ]?注[ ]?(?P<n>\d{1,2})[ ]?\)?")
# 注の定義は、行頭の（注 N）に本文が続くもの。直後が参照の語や助詞（に示す、による、の、を）なら、定義ではなく参照である
NOTE_DEF_RX = re.compile(r"(?m)^[ >*\-]*[(\[]?[ ]?(?:注|※)[ ]?(?P<n>\d{1,2})(?![ ]?[)\]:.]?[ ]?(?:" + CUE + r"|に|を|の|は|、|及び|又は))[ ]?[)\]:.]?(?=\S)")
QUOTE_RX = re.compile(r"[「『](?P<name>[^」』\n]{1,60})[」』]")
PAGE_RX = re.compile(r"(?:(?<![A-Za-z])[Pp]\.?[ ]?(?P<p1>\d{1,3})(?:/\d{1,3})?|(?P<p2>\d{1,3})[ ]?(?:ページ|頁))[ ]?(?:を)?(?:参照|に示す|による)")
VAGUE_RX = re.compile(r"(?<![一-龥])(?:上記|前記|上述|前述|下記|後記|後述|次|以下)(?:の|に示す|のとおり|の通り|により|による|表|図|要領|方法|例)")
NAME_LOC_RX = re.compile(r"(?P<name>[^\s、。,()「」『』|]{2,20}?)(?=" + CUE + ")")
# 識別子や名称の直後。閉じ括弧、短い括弧書き（最新版、制定の日付、略称の定義 など）、題名のカギ括弧、「の」を読み飛ばす。
# 表の行では、識別子の欄の次の欄に場所の名前が来る（| 文書番号 | 条 | の並び）ので、欄の区切りも 1 つだけ読み飛ばす
SKIP_RX = re.compile(r"[ ]?\)?[ ]?(?:\([^()\n]{1,40}\))?[ ]?(?:[「『][^」』\n]{1,60}[」』])?[ ]?(?:の[ ]?|\|[ ]?)?")
# カギ括弧の名称を、文書や見出しの名称として扱う条件（段 1）。後ろに参照の語が続くか、場所の名前が続く
QUOTE_CUE_RX = re.compile(r"[ ]?(?:\([^)\n]{1,12}\))?[ ]?(?:の[^。\n]{0,24}?)?" + CUE)
QUOTE_PLACE_RX = re.compile(r"[ ]?\(?[ ]?(?:の[ ]?)?")
NAME_CUE_RX = re.compile(r"[ ]?(?:\([^)\n]{1,12}\))?[ ]?(?:の[^。、\n]{0,8})?" + CUE)
PRE_SKIP_RX = re.compile(r"[ ]?(?:の[ ]?)?")
# 法令の名前の語尾。「労基法第 22 条」「○○法施行令第 3 条」のように、条・項・章の直前に付いた語がこの語尾で終わるときは、
# その語の文書の条であり、自分の文書の条ではない（就業規則の解説や条文が法令を引く形。見ていない実物の文書で見つかった型）。
# 「本則・附則・総則・原則」などの則は、文書の中の区分や普通の語なので除く
LAW_TAIL = r"(?:法律|法|施行規則|施行細則|令|条例|告示|通達|条約|(?<![規原附付本総罰通細雑補準])則)"
_NAME_CH = r"[一-龥ァ-ヶー・A-Za-z0-9]"
# 語と場所の間の括弧書き（法令番号、略称の定義）。括弧の入れ子は 1 段まで読む
_PAREN = r"(?:\((?:[^()\n]|\([^()\n]{0,40}\)){1,80}\))"
# 名前の中の空白 1 つは、行の折り返しの跡として読み飛ばす（「パート タイム・有期雇用労働法」）
LAW_BEFORE_RX = re.compile(r"(?P<name>(?![0-9 ])(?:(?:" + _NAME_CH + r"[ ]?)+(?:の|に関する|等に関する)[ ]?)*(?:" + _NAME_CH + r"[ ]?)*?" + LAW_TAIL + r")[ ]?" + _PAREN + r"?[ ]?$")
LAW_NAME_RX = re.compile(r".*" + LAW_TAIL + r"$")
# 「検査方法 別表 1」の方法・寸法などは法令ではない。別表・付録の直前の語を法令の名前と読むのは、この形でないときだけ
METHOD_TAIL_RX = re.compile(r"[方寸手工製用技療作文解算]法$")
# 関連する文書の一覧の見出し（「関連規程類」「適用規格及び関連指示書」「引用規格」「参照文書」など）
REFLIST_RX = re.compile(r"(?:関連|適用|引用|参照|準拠|参考)(?:する)?(?:[" + KANJI + r"]{0,4}(?:及び|および|・|、|と))?(?:規程|規定|規格|文書|基準|指示書|法令|図書|資料|標準|図面|規則)")
# 法令番号（昭和○年法律第○号、平成○年○○省告示第○号）
LAW_NUMBER_RX = re.compile(r"(?:法律|政令|勅令|省令|府令|告示|条例|規則|訓令|通達)[ ]?第[ ]?[0-9一二三四五六七八九十百千]+[ ]?号")
# 場所の直前の語が、場所の名前そのもの（別表 1 の、3.2 の、第 3 条の）である形。この語は文書の名前ではない
PLACE_WORD_RX = re.compile(r"(?:" + "|".join(_spaced(x) for x in PART_TYPES + FLOAT_TYPES) + r")?[ ]?(?:第[ ]?)?[0-9A-Z一二三四五六七八九十.\-()]*[ ]?[項条章節号表図)]?$")
# 略称の定義（段 2）
_ALIAS_VERB = r"(?:という|と言う|といふ|と称する|と略す|と略称する|と略記する|と呼ぶ|と記す)"
ALIAS_RX = re.compile(r"[ ]?\)?[ ]?\((?:[^()\n]{0,40}?[。、,;][ ]?)?以下[、, ]?(?:単に)?[ ]?(?:[「『](?P<q>[^」』\n]{1,20})[」』][ ]?" + _ALIAS_VERB + r"?"
                      r"|(?P<b>[^\s「」『』()、。]{1,12}?)[ ]?" + _ALIAS_VERB + r")[。.]?[ ]?\)")

# 見出しの判定に使う語
TITLE_STOP_RX = re.compile(r"(?:及び|又は|並びに|若しくは|乃至|ないし|から|まで|より|[項節章条号](?![目点約電件例立])|参照|以上|以下|以内|未満|および|または)")
TITLE_PARTICLE_RX = re.compile(r"[のにをはがでともへや]")
TITLE_FUNC_RX = re.compile(r"(?:" + CUE + r"|では|には|にも|とは|とも|において|について|に対し|[のをが]|[にはでとも](?:[ 、" + KANJI + r"]))")
TITLE_BAD_FIRST = set("~〜、,。.)%℃°±×=:;/+-*<>≦≧」』]'\"…・･")
# 行頭の番号の直後が助数詞や単位で、文として続く形（「1 台目は…」「3 台の計測が…」「3.2 mm」）。数量であり、見出しではない
QTY_AFTER_RX = re.compile(
    r"(?:台|回|点|本|個|件|名|人|年|月|日|時間?|分|秒|度|倍|番|級|種|枚|社|箇所|か所|ヶ所|カ所|段|行|列|歳|円|週|階|期|次|組|式)"
    r"(?:目|以上|以下|未満|以内|ずつ|間|ごと|毎|当たり|程度|[のはをにがでともや、。)/~〜]|及び|又は|$)"
    r"|(?:[µμ]m|[mck]m[23]?|m[23]?/(?:s|min|h)|m[23]|kg|mg|kN|[kM]Pa|Pa|kW|kV|mA|k?Hz|rpm|min|sec|m?[Ll]|%|℃|°C?|dB|ppm|mol|[mgsht])(?![A-Za-z0-9])")
REF_BEFORE_RX = re.compile(r"(?:上記|前記|下記|後記|前述の?|後述の?|及び|又は|並びに|~|〜|から|第)[ ]*$")
ART_HEAD_AFTER = r"(?=[ (]|$)(?![ ]?(?:の[規定第各]|に[定規基よ掲]|を|及び|又は|並びに|若しくは|から|まで|、|,))"
# 条の直後に空白の無い見出し（「第10条労働者は、…」）。後ろが助詞や参照の続きでないことに加え、番号が直前の条の次であるときだけ採る（DocIndex._scan）
ART_HEAD_WEAK = r"(?=[" + JP + r"])(?![のにをはがでともへや]|及び|又は|並びに|若しくは|から|まで|より|第|中|各|本文|ただし|但|前段|後段|以[上下外前後]|等|関係|違反|参照|所定|協定)"
# 目次。目次の中の番号や条は、見出しとして目録に入れない
TOC_TITLE_RX = re.compile(r"(?m)^[ #>*\-|]*(?:目[ ]*次|もくじ|CONTENTS|Contents|Table of Contents)[ |]*$")
# 点線＋ページ番号。行末にあるもの、または 6 字以上の点線のあとにページ番号と空白が続くもの（1 行に目次の項目が並ぶ変換）。
# 点線の先頭から 1 回だけ照合するので、長い行でも時間は長さに比例する。その行の全体を目次とみなす
TOC_LEADER = r"[.…・･‥]"
TOC_LINE_RX = re.compile(r"(?m)(?<!" + TOC_LEADER + r")(?:" + TOC_LEADER + r"{3,}[ ]?\d{1,4}[ ]?$|" + TOC_LEADER + r"{6,}[ ]?\d{1,4}(?=[ ]))")
# 改正履歴の見出し。見出しの語だけで見分ける（特定の文書の言い回しではない）
_HIST_HEAD = r"(?:改正|改訂|改定|変更|更新|改廃|制定|制改定|制定改廃|制定・改廃|制定及び改廃|版数|版)"
_HIST_TAIL = r"(?:来歴|履歴|経緯|経過|記録|沿革)"
_HIST_WORD = (r"(?:" + _HIST_HEAD + r"(?:の)?" + _HIST_TAIL + r"|(?:基準|規程|規定|規則|文書|標準|要領|本書)(?:の)?(?:来歴|履歴|沿革)"
              r"|RevisionHistory|ChangeLog|DocumentHistory)")
# 先頭の番号の部分は 1 つの文字の集合で読む（量指定を入れ子にすると、数字の並ぶ見出しで照合が終わらなくなる）
HISTORY_RX = re.compile(r"^[第0-9一二三四五六七八九十章節条項.\-]*(?:" + _HIST_WORD + r"|来歴|沿革)(?![ぁ-ん])", re.I)
HISTORY_LINE_RX = re.compile(r"^" + _HIST_WORD + r"(?:表|一覧)?$", re.I)
# 表の欄の先頭にある改正履歴の見出し（枠の中に「基準来歴」と書かれ、同じ欄に改正の記録が続く様式）
HISTORY_CELL_RX = re.compile(r"\|[ ]*(?:\d{1,2}[.][ ]?)?(?P<w>" + _HIST_WORD + r")(?=[ |]|$)", re.I | re.M)
# 改正の記録の 1 行。日付と、改正を表す語の両方がある行（「D: 2021.04.20 ○○の条を追加した」「07.06.14 2.4)項の…を削除し」）
_DATE = r"(?:(?:19|20)\d{2}|'?\d{2}|[HSR]\d{1,2}|(?:昭和|平成|令和)[ ]?\d{1,2})[ ]?[./年][ ]?\d{1,2}[ ]?[./月][ ]?\d{1,2}"
REVISION_LINE_RX = re.compile(r"(?<![\d.])" + _DATE + r"(?![\d.]).{0,200}?(?:制定|改正|改訂|改定|訂正|追加|追記|削除|削り|変更|改め|見直|反映|廃止)")

# 段 6 で同じ長さの候補が重なったときの順（小さいほうを先に取る）
PRIORITY = {"label": 0, "article": 1, "chapter": 2, "para": 3, "section": 4, "item": 4, "note": 5, "page": 6}


VAGUE_BEFORE_RX = re.compile(r"(?:上記|前記|上述|前述|下記|後記|後述|次|以下|下図|上図|下表|上表|左記|右記)(?:の)?[ ]?$")


def _has_cue(norm):
    """参照の語（による、参照、に定める、に示す、準ずる など）があるか。

    「下図に示す」「次により」のように、番号も名前も無い隣の記述を指す言い方は数えない（結ぶ先が無く、LLM の抽出でも参照に含めない）。
    """
    return any(not VAGUE_BEFORE_RX.search(norm[max(0, m.start() - 4):m.start()]) for m in CUE_RX.finditer(norm))


# 規則が何も検出しなかった文のうち、LLM の抽出に回す価値のあるもの。参照の語の直前が、文書らしい語（○○基準、○○指示書、
# ○○図、○○法 など）、カギ括弧の名称、規則が読めなかった番号のいずれかである文と、「参照」と書かれた文である。
# 「検知液により」「温度により変化する」のような手段・原因の「により」は、直前が文書らしい語でないので回さない
DOCLIKE_BEFORE_RX = re.compile(
    r"(?<!この)(?<!本)(?<!当)(?<!同)(?:" + DOC_SUFFIX + r"|法律|(?<![方寸手工製用技療作文解算])法|条例|告示|通達|書|図|図面|資料|カタログ|ガイドライン|ポリシー|協定|契約|約款|定款|規約|[」』]|[0-9][ ]?号|[0-9][.)]?)"
    r"[ ]?(?:\([^()\n]{0,24}\))?[ ]?(?:の(?:定め|規定|とおり|通り))?[ ]?$")
# 文書の種類の語で終わる名詞＋参照の語（「機種ごとの組付指示書による」「別途発行の防錆要領図によること」）。目録の識別子も名称も無い、
# 一般の名称での参照である。結ぶ先は無いので、手元に無い文書への参照として表記だけを記録する（架空の文書と、見ていない実物の規格で見つかった型）
GENERIC_DOC_RX = re.compile(
    r"(?P<name>[一-龥ァ-ヶーA-Za-z0-9・]{1,24}?(?:" + DOC_SUFFIX + r"|説明書|図面|承認図|要領図|図書|協定書|契約書|約款|ガイドライン|法令))"
    r"(?=[ ]?(?:によること|による|により|に依る|に依り|に基づ|に基き|に従|に準|に定め|に規定|を参照|参照|に示す))")
GENERIC_SKIP_RX = re.compile(r"^(?:この|本|当|同|所定|次|下記|上記|前記|後記|当該|各|その|別|以下|関係|関連|該当|一般|社内|諸|他)")
SENTENCE_END_RX = re.compile(r"[。\n]")


def llm_worthy(c, spans, in_history, listed):
    """チャンク c に、規則の参照が 1 つも掛かっていない「LLM の抽出に回す価値のある文」があるか。

    spans は規則の参照の区間、in_history(off) は改正履歴の中か、listed(off) は目次の中か。
    """
    norm = c.norm
    for m in CUE_RX.finditer(norm):
        if VAGUE_BEFORE_RX.search(norm[max(0, m.start() - 4):m.start()]) or in_history(m.start()) or listed(m.start()):
            continue
        a = max(norm.rfind("\n", 0, m.start()), norm.rfind("。", 0, m.start())) + 1
        end = SENTENCE_END_RX.search(norm, m.end())
        b = end.start() if end else len(norm)
        if any(s < b and a < e for s, e in spans):
            continue  # この文には規則の参照がある
        if m.group() == "に記載" and not re.match(r"の|され|した|がある|ある", norm[m.end():]):
            continue  # 「○○表に記載すること」は書き込む先であり、見に行く先ではない
        if m.group().endswith("参照") or DOCLIKE_BEFORE_RX.search(norm[a:m.start()]):
            return True
    return False


def has_reference_cue(text):
    """本文に参照の語があるか。パンくずの行は見ない。"""
    first, _, rest = (text or "").partition("\n")
    body = rest if (first.startswith("[") and first.rstrip().endswith("]")) else (text or "")
    return _has_cue(normalize(body)[0])


# ───────────────────────── 入力 ─────────────────────────
class Chunk:
    __slots__ = ("id", "doc", "pos", "position", "page", "raw", "body", "crumb", "norm", "idx", "flat")

    def __init__(self, row):
        self.id, self.doc = row["id"], row["fileName"]
        self.position = row.get("position")
        self.pos = 0  # 文書の中での順番（1 から）。目録を作るときに振る
        self.page = row.get("page_number", row.get("page"))
        self.raw = row.get("text") or ""
        first, _, rest = self.raw.partition("\n")
        has_crumb = first.startswith("[") and first.rstrip().endswith("]")
        self.body = rest if has_crumb else self.raw  # [文書名 > 見出しの経路] を除いた本文
        crumb = row.get("section") or (first.strip()[1:-1] if has_crumb else "")
        self.crumb = [p.strip() for p in crumb.split(" > ") if p.strip()]
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


# ───────────────────────── 段 0-1: 文書の識別子と名称の目録 ─────────────────────────
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


def _short_ok(s):
    return len(s) >= 5 and sum(ch.isdigit() for ch in s) >= 2


def form_match(a, b):
    """2 = 完全一致、1 = 区切りのそろう後方一致（接頭の部門記号の有無）、0 = 不一致。"""
    A, B = "".join(a), "".join(b)
    if A == B:
        return 2
    long_, short, segs = (A, B, a) if len(A) > len(B) else (B, A, b)
    if not _short_ok(short) or not long_.endswith(short):
        return 0
    cut, n, bounds = len(long_) - len(short), 0, set()
    for s in segs:
        bounds.add(n)
        n += len(s)
    return 1 if cut in bounds else 0


def code_tokens(text):
    """本文から識別子らしい語を拾う。(開始, 終了, 表記) の一覧。

    識別子に空白でつながった 1〜2 桁の数字は、後ろに「.2」「-3」「項」などが続くなら場所の番号とみなして切り離す
    （「X 5.2 による」の 5 を、識別子の枝番として飲み込まない）。
    """
    out = []
    for m in CODE_RX.finditer(text):
        s, e = m.span()
        # 空白のあとに英字で始まる語が続くとき、そこまでで識別子の形（数字 3 桁以上など）ができていれば、後ろは題名の始まりである
        # （「X-005  AB･CD 型…基準」の AB を識別子に飲み込まない）。「JIS H 9302」のように、まだ形ができていなければ続けて読む
        for sp in re.finditer(r" {1,2}(?=[A-Z])", m.group()):
            if strict_code(m.group()[:sp.start()]) and code_forms(m.group()[:sp.start()]):
                e = s + sp.start()
                break
        tail = re.search(r" {1,2}(\d{1,2})$", text[s:e])
        if tail and re.match(r"[.\-]\d|[ ]?(?:項|節|章|条)(?![目点約電件例立])|\.[ ]?(?:" + CUE + "|の)", text[e:]):
            e -= len(tail.group())
        if code_forms(text[s:e]):
            out.append((s, e, text[s:e]))
    return out


class Catalogue:
    """段 0。入力のチャンクだけから作る目録。文書の識別子・名称と、文書ごとの場所の名前（DocIndex）。"""

    def __init__(self, rows, disable=()):
        docs = collections.defaultdict(list)
        for n, row in enumerate(rows):
            docs[row["fileName"]].append((row.get("position") if row.get("position") is not None else 0, n, Chunk(row)))
        self.docs = {}
        for name, items in docs.items():
            chunks = [c for _, _, c in sorted(items, key=lambda x: (x[0], x[1]))]
            for i, c in enumerate(chunks):
                c.pos = i + 1
            self.docs[name] = chunks
        self.codes = {}   # 文書 → 識別子の形の集合
        self.names = {}   # 文書 → 名称の集合（空白を除いた形）
        self.doc = {}     # 文書 → DocIndex
        self._tokens = {name: {c.pos: code_tokens(c.norm) for c in chunks} for name, chunks in self.docs.items()}
        for name in self.docs:
            stem = unicodedata.normalize("NFKC", pathlib.PurePath(name).stem)
            forms = set()
            for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9 _\-]*[A-Za-z0-9]", stem):
                forms.update(code_forms(tok.replace("_", "-")))
            self.codes[name] = forms
        for name, chunks in self.docs.items():
            # 本文に出る長い形（接頭の部門記号つき）を別名に足す。ファイル名に符号が無ければ、各チャンクの先頭付近から拾う
            own, head = self.codes[name], collections.Counter()
            for c in chunks:
                for s, e, tok in self._tokens[name][c.pos]:
                    fs = code_forms(tok)
                    if own and any(form_match(f, o) == 1 and len("".join(f)) > len("".join(o)) for f in fs for o in own):
                        own = own | {fs[-1]}
                    elif not self.codes[name] and s < 150 and strict_code(tok):
                        head[fs[-1]] += 1
            if not self.codes[name] and head:
                form, n = head.most_common(1)[0]
                taken = any(form_match(form, o) for other, fo in self.codes.items() if other != name for o in fo)
                if n >= 2 and not taken:
                    own = {form}
            self.codes[name] = own
        self._index_codes()
        for name, chunks in self.docs.items():
            self.names[name] = self._names(name, chunks)
        self._share_partial_names()
        self._index_names()
        self.aliases = {} if "alias_table" in set(disable or ()) else self._aliases()
        self._alias_rx = re.compile("|".join(_spaced(a) for a in sorted(self.aliases, key=len, reverse=True))) if self.aliases else None
        for name, chunks in self.docs.items():
            self.doc[name] = DocIndex(name, chunks, self)

    # -- 識別子 --
    def _index_codes(self):
        self._exact, self._suffix, self._cache = collections.defaultdict(set), collections.defaultdict(set), {}
        for name, own in self.codes.items():
            for form in own:
                joined, n = "".join(form), 0
                self._exact[joined].add(name)
                for seg in form[:-1]:
                    n += len(seg)
                    if _short_ok(joined[n:]):
                        self._suffix[joined[n:]].add(name)

    def resolve_code(self, token):
        """識別子の表記 → 文書名。目録に無ければ None、複数に当たれば "?"。完全一致を、接頭の記号を除いた一致より優先する。"""
        if token in self._cache:
            return self._cache[token]
        exact, loose = set(), set()
        for form in code_forms(token):
            joined, n = "".join(form), 0
            exact |= self._exact.get(joined, set())
            for seg in form[:-1]:
                n += len(seg)
                if _short_ok(joined[n:]):
                    loose |= self._exact.get(joined[n:], set())
            if _short_ok(joined):
                loose |= self._suffix.get(joined, set())
        hit = exact or loose
        result = None if not hit else (next(iter(hit)) if len(hit) == 1 else "?")
        self._cache[token] = result
        return result

    def code_is(self, token, name):
        return any(form_match(f, o) for f in code_forms(token) for o in self.codes.get(name, ()))

    # -- 名称 --
    def _share_partial_names(self):
        """題名の一部だけの名称（最後の語、識別子と同じ行の和文）が、他の文書の題名にも含まれるなら、その文書の名称にも足す。

        「○○品質要求基準」が並ぶ文書群の「品質要求基準」のような語は、1 つの文書の名前ではない。複数の文書の名称になった語は、
        名称だけでは文書を決めない（resolve_name、find_names）。
        """
        title = {}
        for name, chunks in self.docs.items():
            m = re.search(r"(?m)^#{1,6}[ ]*(.+)$", chunks[0].norm)
            stem = unicodedata.normalize("NFKC", pathlib.PurePath(name).stem)
            title[name] = squeeze(m.group(1) if m else "") + "|" + squeeze(stem)
        for name, ns in list(self.names.items()):
            for n in list(ns):
                for other in self.docs:
                    if other != name and n in title[other]:
                        self.names[other].add(n)

    def _aliases(self):
        """文書をまたいで使う別名（旧番号・通称・略称）→ 文書。規則 alias_table。1 つの文書に決まる別名だけを返す。

        拾う書式は 3 つである（どれも書式だけで、語は決め打ちしない）。
          (1) 文書の先頭チャンクの表の「旧番号・旧文書番号・通称・略称・別称・別名・旧称」の欄 → その文書の別名
          (2) 列名が「旧番号」と「現行（新）の番号」の 2 列の表 → 旧番号 → 現行の識別子の文書
          (3) 「<識別子> は「<別名>」と呼ぶ／という／と称する／と略す」 → その識別子の文書の別名
        """
        found = collections.defaultdict(set)
        key = r"(?:旧番号|旧文書番号|旧基準番号|旧規格番号|通称|略称|別称|別名|旧称|旧名称)"
        for name, chunks in self.docs.items():
            for m in re.finditer(r"(?m)^[ ]*\|[ ]*" + key + r"[ ]*\|[ ]*([^|\n]{2,30}?)[ ]*\|[ ]*$", chunks[0].norm):
                found[squeeze(m.group(1))].add(name)
            for c in chunks:
                for m in re.finditer(r"(?m)^[ ]*\|[ ]*旧[^|\n]{0,6}番号[ ]*\|[ ]*(?:現行|新)[^|\n]{0,8}[ ]*\|[ ]*$", c.norm):
                    for row in c.norm[m.end():].split("\n")[1:]:
                        cells = [x.strip() for x in row.strip().strip("|").split("|")]
                        if not row.strip().startswith("|") or len(cells) != 2:
                            break
                        target = self.resolve_code(cells[1]) if code_forms(cells[1]) else None
                        if target and target != "?" and not set(cells[0]) <= set("-: "):
                            found[squeeze(cells[0])].add(target)
                for s, e, tok in self._tokens[name][c.pos]:
                    m = re.compile(r"[ ]?は[ ]?[「『]([^」』\n]{2,20})[」』]").match(c.norm, e)
                    target = self.resolve_code(tok) if m else None
                    # 並び（A は「x」、B は「y」と呼ぶ）も読む: 同じ文の中に「と呼ぶ・という・と称する・と略す」があればよい
                    if target and target != "?" and re.match(r"[^。\n]{0,80}?(?:と呼ぶ|という|と称する|と略す|と略称)", c.norm[m.end():]):
                        found[squeeze(m.group(1))].add(target)
        return {a: next(iter(ds)) for a, ds in found.items() if len(ds) == 1 and len(a) >= 3 and a not in self._name_owner}

    def _names(self, name, chunks):
        """文書の名称。ファイル名の和文の部分、先頭チャンクの最初の見出し、自分の識別子と同じ行の和文。"""
        out = set()
        stem = unicodedata.normalize("NFKC", pathlib.PurePath(name).stem)
        for run in re.findall("[" + JP + "][" + JP + "・ ]{3,}", stem):
            out.add(squeeze(run))
        first = chunks[0]
        m = re.search(r"(?m)^#{1,6}[ ]*(.+)$", first.norm)
        if m and len(squeeze(m.group(1))) >= 6 and re.search("[" + KANJI + "]", m.group(1)):
            out.add(squeeze(m.group(1)))
            last = m.group(1).split()[-1]  # 「対象の機種 文書の種類の名前」の形の題名は、最後の語だけでも呼ばれる
            if len(last) >= 6 and re.search("[" + KANJI + "]", last):
                out.add(last)
        for s, e, tok in self._tokens[name][first.pos]:
            if s < 400 and self.code_is(tok, name):
                ls, le = first.line(s)
                runs = re.findall("[" + JP + "][" + JP + "・ ]{5,}", first.norm[ls:le])
                # 文の一節（「…として支給する素材の状態と」）は名称ではない
                runs = [r for r in runs if not re.search(r"する|した|して|され|こと|である|ます|[をはがにでとへも][ ]?$", r)]
                if runs:
                    words = max(runs, key=len).split()
                    while words and len(words[-1]) == 1:
                        words.pop()
                    while words and len(words[0]) == 1:
                        words.pop(0)
                    out.add("".join(words))
        return {n for n in out if len(n) >= 4}

    def _index_names(self):
        owners = collections.defaultdict(set)
        for name, ns in self.names.items():
            for n in ns:
                owners[n].add(name)
        self._name_owner = dict(owners)
        self._name_index = collections.defaultdict(list)  # 先頭 2 字 → 名称（長い順）
        for n in sorted(owners, key=len, reverse=True):
            self._name_index[n[:2]].append(n)

    def find_names(self, c):
        """本文に出る目録の名称を、長いものから重ならないように拾う。(開始, 終了, 文書の集合) の一覧。"""
        chars, at = [], []
        for i, ch in enumerate(c.norm):
            if not ch.isspace():
                chars.append(ch)
                at.append(i)
        sq, out, i = "".join(chars), [], 0
        while i < len(sq) - 1:
            hit = next((n for n in self._name_index.get(sq[i:i + 2], ()) if sq.startswith(n, i)), None)
            if hit is None:
                i += 1
                continue
            s, e = at[i], at[i + len(hit) - 1] + 1
            if "\n" not in c.norm[s:e]:
                out.append((s, e, self._name_owner[hit]))
            i += len(hit)
        return out

    def maybe_known(self, word):
        """目録に無い語が、目録の文書の名称の一部か略称（名称の字を順に拾った形）でありうるか。"""
        w = squeeze(word)
        if len(w) < 2:
            return False
        for n in self._name_owner:
            if w in n or n in w:
                return True
            it = iter(n)
            if all(ch in it for ch in w):
                return True
        return False

    def resolve_name(self, text):
        """名称 → 文書名。一致する文書がちょうど 1 つのときだけ返す。"""
        key = squeeze(text)
        hit = {name for name, ns in self.names.items() if key in ns}
        if len(hit) == 1:
            return next(iter(hit))  # 題名の全体と一致する文書が 1 つなら、それを採る（題名の一部が他の文書と重なっていても）
        hit = {name for name, ns in self.names.items() if any(n == key or (len(n) >= 6 and n in key) for n in ns)}
        if not hit and len(key) >= 6:
            hit = {name for name, ns in self.names.items() if any(n.endswith(key) for n in ns)}  # 題名の後ろの部分だけで呼んでいる
        return next(iter(hit)) if len(hit) == 1 else None


# ───────────────────────── 段 0: 文書ごとの場所の名前の目録 ─────────────────────────
class DocIndex:
    """1 文書の、見出し・別表など・図表・注・箇条・条の位置。位置は（チャンクの順番, 本文中の位置）で表す。"""

    def __init__(self, name, chunks, cat):
        self.name, self.chunks, self.cat = name, chunks, cat
        self.by_pos = {c.pos: c for c in chunks}
        self.order = [c.pos for c in chunks]
        self.sections = collections.defaultdict(dict)   # 範囲 → 番号 → [(pos, off, 題名)]
        self.heads = []      # 採用した見出し（順）: (pos, off, 範囲, 番号, 題名)
        self.scopes = []     # 範囲の切り替え（順）: (pos, off, 範囲)
        self.labels = collections.defaultdict(list)     # (種類, 番号) → [(pos, off, end)]
        self.label_occ = collections.defaultdict(list)  # pos → [(s, e, 種類, 番号, 題名の定義か, 他文書の識別子の直後か)]
        self.titles = collections.defaultdict(list)     # 題名（空白なし）→ [pos]
        self.line_heads = collections.defaultdict(list)  # pos → [(off, 行頭の 40 字)]
        self.items = collections.defaultdict(list)      # (範囲, 属する項, 種類, n) → [(pos, off)]
        self.item_marks = []  # (pos, off, n, 範囲, 属する項, 種類)
        self._items_by = collections.defaultdict(list)  # (種類, n) → [(pos, off, 強さ, 範囲, 属する項)]。強さ: 2 = 見出しの行、1 = 行頭、0 = 文中
        self.notes = collections.defaultdict(list)      # n → [pos]
        # 条。同じ番号の条が 1 つの文書に 2 か所以上あることがある（附則の振り直し、例 1・例 2 の並記、チャンクの重なり）ので、
        # 出現ごとに「条の実体」を作る。重なりで繰り返された見出しは 1 つの実体にまとめ、番号が戻ったところで「まとまり」を分ける
        self.art_inst = []    # 条の実体（順）: {"key": (条, 枝), "marks": [(pos, off)], "block": まとまりの番号}
        self.articles = collections.defaultdict(list)   # (条, 枝) → [実体の番号]
        self.art_marks = []   # (pos, off, 実体の番号)
        self.chapters = collections.defaultdict(list)   # (章や節, n) → [(pos, off)]
        self.chapter_marks = []                         # 章・節の見出しの位置（順）
        self.paras = collections.defaultdict(list)      # (実体の番号, n) → [(pos, off)]
        self.para_marks = []  # (pos, off, 実体の番号, n)
        self.md_heads = []    # 見出し記号（#）の行: (pos, off, 深さ, 題名)。図・表の題名の行は除く
        self.rejected = []    # 捨てた見出し候補（点検用）
        self.codes = {}       # pos → [(s, e, 表記, 解決先)]
        self.history = []     # 改正履歴の範囲: ((pos, off), (pos, off) か None)
        self._toc = {}        # pos → [(s, e)] 目次の範囲
        self.has_articles = sum(len(re.findall(r"(?m)^[ #>*\-]*第[ ]?[0-9一二三四五六七八九十百]{1,5}[ ]?条", c.flat)) for c in chunks) >= 2
        self._where = {pos: i for i, pos in enumerate(self.order)}
        self._scan()
        self.chapter_marks = sorted(x for v in self.chapters.values() for x in v)
        self._md_keys = [(p, o) for p, o, _, _ in self.md_heads]
        self._keys = {k: [(m[0], m[1]) for m in getattr(self, k)] for k in ("scopes", "heads", "item_marks", "art_marks", "para_marks")}
        self.head_at = {(h[0], h[1]) for h in self.heads}
        self.art_head_at = {(m[0], m[1]) for m in self.art_marks}
        self._find_history()

    # -- 走査 --
    def _toc_ranges(self, c):
        if any(re.fullmatch(r"目[ ]*次|もくじ|contents|table of contents", re.sub(r"^[\d.\- ]+", "", part), re.I) for part in c.crumb[-1:]):
            return [(0, len(c.norm) + 1)]
        out = [c.line(m.start()) for m in TOC_LINE_RX.finditer(c.norm)]
        for m in TOC_TITLE_RX.finditer(c.norm):
            nxt = re.compile(r"(?m)^[ ]*#{1,6}[ ]").search(c.norm, m.end())
            out.append((m.start(), nxt.start() if nxt else len(c.norm) + 1))
        return out

    def _scan(self):
        events = []
        for c in self.chunks:
            toks = [(s, e, tok, self.cat.resolve_code(tok)) for s, e, tok in self.cat._tokens[self.name][c.pos]]
            self.codes[c.pos] = toks
            toc = self._toc[c.pos] = self._toc_ranges(c)
            listed = lambda off: any(a <= off < b for a, b in toc)
            for occ in self._labels(c, toks):
                self.label_occ[c.pos].append(occ)
                s, e, typ, num, is_def, foreign = occ
                if is_def and not listed(s):
                    events.append((c.pos, s, "part" if typ in PART_KEYS else "float", (typ, num, e, foreign)))
            for h in self._headings(c):
                if not listed(h["off"]):
                    events.append((c.pos, h["off"], "head", h))
            for m in re.finditer(r"(?m)^[ ]*(#{1,6})[ ]*(.+?)[ ]*$", c.norm):
                self.titles[squeeze(m.group(2))].append(c.pos)
                if not (FLOAT_RX.match(m.group(2)) or FLOAT2_RX.match(m.group(2))):
                    events.append((c.pos, m.start(), "md", len(m.group(1))))
                    self.md_heads.append((c.pos, m.start(), len(m.group(1)), m.group(2)))
            for m in re.finditer(r"(?<![^\s])(?:(?P<a>\d{1,2})[ ]?\)|\((?P<b>\d{1,2})\)|(?P<c>[a-z])[.)])[ ]?(?=[^\s\d).])", c.flat):
                kind = ")" if m.group("a") else ("()" if m.group("b") else "a")
                n = int(m.group("a") or m.group("b")) if kind != "a" else ord(m.group("c")) - 96
                before = c.flat[c.line(m.start())[0]:m.start()]
                rank = 2 if (before.lstrip().startswith("#") and not before.strip(" #")) else (1 if not before.strip(" >*-") else 0)
                events.append((c.pos, m.start(), "item", (kind, n, rank)))
            for m in NOTE_DEF_RX.finditer(c.flat):
                self.notes[int(m.group("n"))].append(c.pos)
            for m in re.finditer(r"(?m)^(?P<pre>[ #>*\-]*)" + ART_RX.pattern + r"(?:(?P<strong>" + ART_HEAD_AFTER + r")|" + ART_HEAD_WEAK + r")", c.flat):
                a = kanji_int(m.group("a"))
                if a and not m.group("p") and not m.group("g") and not listed(m.start()):
                    events.append((c.pos, m.end("pre"), "article", ((a, kanji_int(m.group("b")) if m.group("b") else 0), m.group("strong") is not None)))
                    title = re.match(r"[ ]?\(([^()\n]{2,30})\)", c.flat[m.end():])
                    if title:
                        self.titles[squeeze(title.group(1))].append(c.pos)  # 条の見出しのかっこ書きの題名（第 10 条（廃棄））
            for m in re.finditer(r"(?m)^[ #>*\-]*" + CHAP_RX.pattern + r"(?=[ (]|$)", c.flat):
                if not listed(m.start()):
                    self.chapters[(m.group("k"), kanji_int(m.group("n")))].append((c.pos, m.start()))
                    events.append((c.pos, m.start(), "chapter", None))
            if self.has_articles:
                # 条の中の項の印（行頭の「2 …」）。変換が付ける箇条書きの記号（- ）や引用の記号の後ろも行頭として読む。
                # 項として採るのは、属する条の中で番号が続いているものだけである（DocIndex._scan の para の処理）
                for m in re.finditer(r"(?m)^(?P<pre>[ >*\-]*)(\d{1,2})[ ]+(?=[" + JP + "])", c.flat):
                    rest = c.flat[m.end():m.end() + 12]
                    if QTY_AFTER_RX.match(rest) or re.match(r"(?:年|か月|ヶ月|月|日|時間?|分|回|名|人|個|件|台|本|枚|円|歳|割|倍|点|級)(?:[ ]|$)", rest):
                        continue  # 表の欄の数量（「3 日」「2 回」）は項の印ではない
                    if not listed(m.start()):
                        events.append((c.pos, m.end("pre"), "para", int(m.group(2))))
            for m in re.finditer(r"(?m)^[ #>*\-|]*(\S[^\n]{0,40})", c.norm):
                self.line_heads[c.pos].append((m.start(1), m.group(1)))
        events.sort(key=lambda e: (e[0], e[1], e[2] != "md"))
        scope, top, seen, restarts, current, article = "main", 0, {}, 0, (), None
        # 条の中の項の印の読み取りの状態: 最後に採った項の番号、条の本文が続いているか、行頭の番号が振り直されたか
        last_para, para_open, para_restart = 1, False, False
        main_state, part_level, md_level, md_seen = None, None, {}, {}
        self.scopes.append((self.order[0] if self.order else 0, 0, "main"))
        for pos, off, kind, data in events:
            if kind in ("md", "chapter"):
                # 見出し記号の行と章・節の見出しで、条の本文は終わったとみなす（その行が条の見出しなら、直後の条の印で開き直す）。
                # 章・節の見出しの後ろは、次の条まで項を採らない。見出し記号の行の後ろは、項の番号が続くときだけ採る（下の para）
                para_open = False
            if kind == "chapter":
                para_restart = True
                continue
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
                elif data["md"] and (data["dot"] or len(t) >= 2 or data["sp"]):
                    verdict = "new"  # 見出し記号つきの見出しは、番号が飛んでいても前後していても採る（本文の数字を先に見出しと取り違えていても落とさない）
                if verdict is None:
                    self.rejected.append((pos, off, t, data["title"][:20]))
                    continue
                if verdict == "restart":
                    # 番号が 1 に戻り題名も違う。別の部（別表や添付の中の番号）が始まったとみなして範囲を分ける
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
                self.heads.append((pos, off, scope, t, data["title"]))
                for key in {squeeze(data["title"]), squeeze(data["title"].split(" ")[0])}:
                    if len(key) >= 2:
                        self.titles[key].append(pos)
            elif kind == "item":
                self.items[(scope, current) + data[:2]].append((pos, off))
                self.item_marks.append((pos, off, data[1], scope, current, data[0]))
                self._items_by[data[:2]].append((pos, off, data[2], scope, current))
            elif kind == "article":
                key, strong = data
                last = self.art_inst[-1] if self.art_inst else None
                if not strong and not (last and key == (last["key"][0] + 1, 0)):
                    continue  # 条の直後に空白の無い行は、番号が直前の条の次であるときだけ見出しとして採る（文の折り返しと取り違えない）
                if last and key == last["key"] and self._repeated(last["marks"][-1], (pos, off)):
                    last["marks"].append((pos, off))  # チャンクの重なりで繰り返された同じ見出し
                else:
                    block = 0 if last is None else last["block"] + (1 if key <= last["key"] else 0)
                    self.articles[key].append(len(self.art_inst))
                    self.art_inst.append({"key": key, "marks": [(pos, off)], "block": block})
                article = len(self.art_inst) - 1
                self.art_marks.append((pos, off, article))
                last_para, para_open, para_restart = 1, True, False
            elif kind == "para" and article is not None and not para_restart:
                # 項として採るのは、属する条の中で番号が続いている行頭の数字だけである（2、3、… と、条の直後の 1）。
                # 番号が戻ったら（解説や注の番号の振り直し）、次の条まで採らない。見出し記号の行をはさんだ後ろは、
                # すでに項の番号が 2 以上まで進んでいて、その続きであるときだけ採る（表や図の題名が見出しに変換された場合）
                marks = self.paras.get((article, data))
                if data == last_para + 1 and (para_open or last_para >= 2):
                    last_para, para_open = data, True
                elif data == 1 and last_para == 1 and not marks and para_open:
                    pass
                elif not (data == last_para and marks and self._repeated(marks[-1], (pos, off))):
                    if data <= last_para:
                        para_restart = True
                    continue  # 番号が続かない行頭の数字（解説の番号、箇条書きの数量）は項としない
                self.paras[(article, data)].append((pos, off))
                self.para_marks.append((pos, off, article, data))

    def _repeated(self, a, b):
        """位置 b の行が、1 つ前のチャンクの位置 a の行の繰り返し（チャンクの重なり）か。"""
        if self._where[b[0]] - self._where[a[0]] != 1:
            return False
        ca, cb = self.by_pos[a[0]], self.by_pos[b[0]]
        la, lb = squeeze(ca.norm[a[1]:ca.line(a[1])[1]]), squeeze(cb.norm[b[1]:cb.line(b[1])[1]])
        n = min(len(la), len(lb), 24)  # 前のチャンクの末尾で行が途中で切れていることがあるので、短い方の長さで比べる
        return n >= 6 and la[:n] == lb[:n]

    def _headings(self, c):
        """項番号の見出しの候補。行頭（箇条書き記号・罫線・見出し記号の後を含む）か空白の直後の「N.」「N-M」「N.M.K」＋題名。"""
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
            # 直後が「項・及び・、・〜・助詞・参照の語」なら、見出しでなく参照である
            if not md and (first in TITLE_BAD_FIRST or TITLE_STOP_RX.match(rest) or REF_BEFORE_RX.search(before)):
                continue
            if not md and not m.group("dot") and not m.group("sp") and TITLE_PARTICLE_RX.match(rest):
                continue
            if not md and TITLE_FUNC_RX.match(rest):
                continue
            if not md and QTY_AFTER_RX.match(rest.lstrip()):
                continue  # 数量（1 台目は…、3.2 mm）
            if len(num) == 1 and not m.group("dot") and c.norm[ls:le].lstrip().startswith("|"):
                continue  # 表の行の先頭の欄の番号
            if not re.search("[A-Za-z" + JP + "]", rest[:6]):
                continue
            if md:
                # 見出し記号つきの行の番号は、題名が数字で始まっていても見出しである（「### 2-1 1 台目の計測」）。
                # ただし「## 2)窒化層検査」は箇条の番号であり、項番号ではない
                if first in TITLE_BAD_FIRST or (len(num) == 1 and not (m.group("dot") or m.group("sp"))):
                    continue
            elif len(num) == 1:
                if first.isdigit() or not (m.group("dot") or (m.group("sp") and strong)):
                    continue
                if not m.group("dot") and not re.match("[A-Za-z" + JP + "]", first):
                    continue
            elif first.isdigit() and not re.match(r"\d{1,2}[" + JP + "]", rest):
                continue
            out.append({"off": m.start(), "num": num, "title": rest.strip()[:60], "strong": strong, "end": m.end(), "dot": bool(m.group("dot")), "md": md,
                        "sp": bool(m.group("sp"))})
        return out

    def _labels(self, c, toks):
        """別表・図などの出現を、題名の定義か参照かに分けて返す。

        題名の定義とみなすのは、見出し、行頭、短い行の末尾（自分の文書の識別子の直後を含む）にあるものである。
        """
        found, taken = [], []
        for rx in (PART_RX, FLOAT2_RX, FLOAT_RX):
            for m in rx.finditer(c.norm):
                s, e = m.start(), m.end()
                if any(s < b and a < e for a, b in taken):
                    continue
                typ, num = label_key(m)
                if num is None and (typ not in UNNUMBERED_OK or (s > 0 and re.match("[" + KANJI + "]", c.norm[s - 1]))):
                    continue  # 番号の無い「付表」などが、別の語の一部である場合（直前が漢字）。番号の無い「様式」は普通の名詞
                if typ in FLOAT_TYPES and s > 0 and re.match("[" + KANJI + "]", c.norm[s - 1]) and c.norm[s - 1] not in "同本各該当下上右左次前":
                    continue
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
                if before == "(" and after == ")":
                    is_def = True  # 行の全体が「(図 1)」だけの題名
                if foreign and not (not after and not c.flat[ls:prev[-1][0]].strip(" #>*-")):
                    is_def = False  # 他の文書の識別子に続く場所は、その文書の場所。行全体が「識別子＋題名」のときだけ区切りとして扱う
                found.append((s, e, typ, num, is_def, foreign))
        found.sort()
        return found

    # -- 改正履歴 --
    def _find_history(self):
        """改正履歴の範囲。見出しの語（基準来歴、改正履歴、改訂記録 など）から次の見出しの手前まで。"""
        for pos, off, scope, t, title in self.heads:
            if HISTORY_RX.match(squeeze(title)):
                # 次の項番号の見出しか、範囲の切り替え（別表・添付資料などの題名）まで。どちらも無ければ文書の末尾まで続くとみなす。
                # 見出し記号の無い見出しの後ろに出る見出し記号つきの行では終わらせない（ページの柱が見出しに変換されることがあり、
                # 改正の記録は複数のページにわたる）
                ends = [x for x in (self._section_end(scope, t, (pos, off)),) if x]
                self.history.append(((pos, off), min(ends) if ends else None))
        for pos, off, level, title in self.md_heads:
            if HISTORY_RX.match(squeeze(title)):
                ends = [x for x in (self._md_end((pos, off)), self._next_scope((pos, off))) if x]
                self.history.append(((pos, off), min(ends) if ends else None))
        for c in self.chunks:
            for off, head in self.line_heads[c.pos]:
                ls, le = c.line(off)
                if HISTORY_LINE_RX.match(squeeze(c.flat[ls:le].strip(" #>*-"))) and not c.norm[ls:le].lstrip().startswith("#"):
                    start = (c.pos, off)
                    ends = [x for x in (next(((p, o) for p, o, _, _ in self.md_heads if (p, o) > start), None), self._next_scope(start),
                                        next(((h[0], h[1]) for h in self.heads if (h[0], h[1]) > start), None)) if x]
                    self.history.append((start, min(ends) if ends else None))
            for m in HISTORY_CELL_RX.finditer(c.norm):
                start = (c.pos, m.start("w"))
                ends = [x for x in (self._next_scope(start), next(((h[0], h[1]) for h in self.heads if (h[0], h[1]) > start), None)) if x]
                self.history.append((start, min(ends) if ends else None))

    def in_toc(self, c, off):
        return any(a <= off < b for a, b in self._toc.get(c.pos, ()))

    def in_history(self, c, off):
        if any(HISTORY_RX.match(squeeze(re.sub(r"^[#\s]+", "", part))) for part in c.crumb):
            return True
        if any(start <= (c.pos, off) and (end is None or (c.pos, off) < end) for start, end in self.history):
            return True
        ls, le = c.line(off)
        return bool(REVISION_LINE_RX.search(c.norm[ls:le]))  # 見出しが無くても、改正の記録の行の中の参照は履歴とする（附則の中の記録など）

    # -- 位置の問い合わせ --
    def _last(self, kind, pos, off):
        i = bisect.bisect_right(self._keys[kind], (pos, off)) - 1
        return getattr(self, kind)[i] if i >= 0 else None

    def scope_at(self, pos, off):
        hit = self._last("scopes", pos, off)
        return hit[2] if hit else "main"

    def section_at(self, pos, off):
        hit = self._last("heads", pos, off)
        return (hit[2], hit[3]) if hit else None

    def item_at(self, pos, off):
        return self._last("item_marks", pos, off)

    def para_at(self, pos, off):
        """位置の直前にある項の印。（条の実体の番号, 項の番号）。"""
        hit = self._last("para_marks", pos, off)
        return (hit[2], hit[3]) if hit else None

    def article_at(self, pos, off):
        """位置が属する条の実体の番号（直前にある条の見出し）。無ければ None。"""
        hit = self._last("art_marks", pos, off)
        return hit[2] if hit else None

    def _last_md(self, pos, off):
        i = bisect.bisect_right(self._md_keys, (pos, off)) - 1
        return self.md_heads[i] if i >= 0 else None

    def _next_in(self, keys, start):
        i = bisect.bisect_right(keys, start)
        return keys[i] if i < len(keys) else None

    def _next_scope(self, start):
        return self._next_in(self._keys["scopes"], start)

    def _md_end(self, start):
        """start の行が見出し記号つきなら、同じ深さ以上の次の見出しの位置。"""
        c = self.by_pos[start[0]]
        ls, le = c.line(start[1])
        m = re.match(r"[ ]*(#{1,6})", c.norm[ls:le])
        if not m:
            return None
        return next(((p, o) for p, o, lv, _ in self.md_heads if (p, o) > (start[0], ls) and lv <= len(m.group(1))), None)

    # -- 場所の続き（チャンクの範囲）--
    def _span(self, start, end, cap=3, titled=True):
        """start=(pos, off) から end=(pos, off) の手前までのチャンク（上限 cap）。

        titled は、start の行が題名だけの行になりうるか（項番号の見出し、別表などの題名）。条は、行そのものが本文なので False。
        """
        spos, soff = start
        c = self.by_pos[spos]
        order = self.order
        i = self._where[spos]
        j = self._where[order[-1] if end is None else end[0]]
        if end is not None and end[1] <= 100 and j > i:
            j -= 1  # 次の見出しがチャンクの先頭にあるなら、そのチャンクは含めない
        ls, le = c.line(soff)
        at_tail, title_only = len(c.norm) - le <= 40, le - soff <= 30 and not re.search("[。、]", c.norm[soff:le])
        span = order[i:j + 1]
        if titled and at_tail and title_only and i + 1 < len(order) and (end is None or end[0] > spos):
            # 題名だけがチャンクの末尾にある → 中身は次のチャンクから始まる
            if j < i + 1:
                span = order[i:i + 2]
            if soff > 40:
                span = span[1:]  # 題名のほかに別の内容が詰まったチャンクは外す
        keep = [p for p in span if p == span[0] or len(self.by_pos[p].body.strip()) >= 80]  # 続きのうち、柱やフッタだけの短いチャンクは外す
        return keep[:cap]

    def _section_end(self, scope, t, start):
        end = None
        for p, o, sc, k, _ in self.heads:  # 次の見出し（重なりで繰り返された前の見出しは数えない）
            if (p, o) > start and sc == scope and k > t and k[:len(t)] != t:
                end = (p, o)
                break
        nxt = self._next_scope(start)
        if nxt and (end is None or nxt < end):
            end = nxt
        md = self._md_end(start)
        if md and (end is None or md < end):
            end = md
        return end

    def section_chunks(self, scope, t):
        """項 t のチャンク。「N 項」は N-1、N-2 … の下位の項を含む。次の見出しが出るチャンクまで。"""
        table = self.sections.get(scope, {})
        defs = table.get(t) or [d for k, v in table.items() if k[:len(t)] == t for d in v]
        if not defs:
            return None
        start = min((p, o) for p, o, _ in defs)
        return self._span(start, self._section_end(scope, t, start))

    def label_chunks(self, key):
        """別表・図などのチャンク。次の題名か番号の付け直しの手前まで。"""
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
                i = self._where[pos]
                if key[0] == "図" and off <= 40 and i > 0:
                    span.insert(0, self.order[i - 1])  # 図の題名は図の下に付く。題名がチャンクの先頭にあれば 1 つ前も対象
                if key[0] == "表" and len(c.norm) - end <= 40 and i + 1 < len(self.order):
                    span.append(self.order[i + 1])
            else:
                nxt = self._next_scope((pos, off))
                md = self._md_end((pos, off))
                if md and (nxt is None or md < nxt):
                    nxt = md
                span = self._span((pos, off), nxt)
            out += [p for p in span if p not in out]
        return out[:4]

    def find_items(self, scope, t, style, n, direct=False):
        """項 t の見出しから次の項の見出しまでにある箇条 n の位置 [(pos, off, 強さ)]。チャンクの重なりで繰り返されたものは 1 つに数える。

        番号の付け直しとみなした範囲（項の中の一覧を見出しと読んだもの）も、項 t の続きとして数える。
        2 つ以上返るときは、どれを指すかが番号だけでは決まらない（Extractor._pick_item が、題名の語と見出し記号で絞る）。
        """
        defs = self.sections.get(scope, {}).get(t)
        if not defs:
            return []
        start = min((p, o) for p, o, _ in defs)
        end = next(((h[0], h[1]) for h in self.heads if (h[0], h[1]) > start and h[2] == scope and h[3] > t and h[3][:len(t)] != t), None)
        nxt = next(((p, o) for p, o, sc in self.scopes if (p, o) > start and not sc.startswith("番号の付け直し")), None)
        if nxt and (end is None or nxt < end):
            end = nxt
        distinct = []
        for m in sorted(self._items_by.get((style, n), ())):
            if direct and not (m[3] == scope and m[4] == t):
                continue  # じかの箇条（項 t の見出しの直下で数えたもの）だけ
            if start < (m[0], m[1]) and (end is None or (m[0], m[1]) < end) and not (distinct and self._repeated(distinct[-1][:2], m[:2])):
                distinct.append(m[:3])
        return distinct

    def head_is_md(self, scope, t):
        """項 t の見出しが、見出し記号（#）つきの行か。"""
        defs = self.sections.get(scope, {}).get(t)
        if not defs:
            return False
        p, o, _ = min(defs)
        c = self.by_pos[p]
        return c.norm[c.line(o)[0]:o].lstrip().startswith("#")

    def pick_article(self, key, src=None):
        """条の番号 → 条の実体の番号。（番号, 決められない理由）を返す。

        同じ番号の条が複数あるときは、参照元と同じまとまり（番号が続いている範囲）にある条、無ければ参照元より前で最も近い条を採る。
        どちらも無ければ決めない（結ばずに残す）。他の文書からの参照（src が無い）と、最初の条より前（前書き）からの参照は、
        最初に出る条とする。
        """
        insts = self.articles.get(key)
        if not insts:
            return None, "目録に無い条"
        if len(insts) == 1:
            return insts[0], ""
        if src is None:
            return insts[0], ""
        here = self.article_at(*src)
        if here is None:
            return insts[0], ""
        same = [i for i in insts if self.art_inst[i]["block"] == self.art_inst[here]["block"]]
        if len(same) == 1:
            return same[0], ""
        if same:
            insts = same
        before = [i for i in insts if self.art_inst[i]["marks"][0] <= src]
        if before:
            return before[-1], ""
        return None, "同じ番号の条が複数あり、決められない"

    def _article_end(self, start):
        """条の本文の終わり。次の条、範囲の切り替え、章・節の見出し、見出し記号つきの行のうち、最も近いもの。

        条の行そのものが見出し記号つきなら、同じ深さ以上の次の見出しまでとする（条の中の小見出しでは切らない）。
        """
        ends = [self._next_in(self._keys["art_marks"], start), self._next_scope(start), self._next_in(self.chapter_marks, start)]
        c = self.by_pos[start[0]]
        if c.norm[c.line(start[1])[0]:start[1]].lstrip().startswith("#"):
            ends.append(self._md_end(start))
        else:
            ends.append(self._next_in(self._md_keys, start))
        ends = [x for x in ends if x]
        return min(ends) if ends else None

    def inst_chunks(self, inst, para=None, src=None):
        """条の実体のチャンク。（一覧, 注記）を返す。項の指定があれば、その項の印のあるチャンク。

        見出しや項の印がチャンクの重なりで 2 か所にあるときは、参照元と同じチャンクにあるもの、無ければ後ろの側（本文の続く側）を採る。
        """
        near = lambda marks: next((m for m in marks if src is not None and m[0] == src[0]), marks[-1])
        start = near(self.art_inst[inst]["marks"])
        if para and para > 1:
            hit = self.paras.get((inst, para))
            if hit:
                return [near(hit)[0]], ""
        if para == 1:
            return [start[0]], ""
        span = self._span(start, self._article_end(start), titled=False)
        return span, ("第 %d 項の印が目録に無く、条の全体へ結んだ" % para) if para else ""

    def article_chunks(self, key, para=None, src=None):
        """条（と項）のチャンク。目録に無い、または決められないときは None。"""
        inst, _ = self.pick_article(key, src)
        return None if inst is None else self.inst_chunks(inst, para, src)[0]

    def find_sub(self, word, value, positions=None):
        """下位の指定（符号 A など）が、行頭・見出し・表の行の先頭の欄にあるチャンク。"""
        rx = re.compile(re.escape(squeeze(word)) + r"[ ]?" + re.escape(value) + r"(?![A-Za-z0-9])")
        hit = [p for p in (positions or self.order) if any(rx.match(head) for _, head in self.line_heads[p])]
        if not hit and positions:  # 「符号 B」の B が、表の行の先頭の欄にある
            cell = re.compile(r"(?m)^[ ]*\|[ ]*" + re.escape(value) + r"[ ]*\|")
            hit = [p for p in positions if cell.search(self.by_pos[p].norm)]
        return hit


def _art_match(text, p):
    """位置 p から始まる条。「(…第 25 条の 2)」のように括弧が開いていれば、後ろの「)」は括弧書きの終わりであり、枝番の条として読む。"""
    m = ART_RX.match(text, p)
    if m and not m.group("b"):
        m2 = ART_OPEN_RX.match(text, p)
        if m2 and m2.group("b") and _paren_open(text, p):
            return m2
    return m


def _paren_open(text, p):
    """位置 p が、同じ行の中で開いたままの括弧の中にあるか。相手の無い「)」（箇条の「2)」）は数えない。"""
    depth = 0
    for ch in text[text.rfind("\n", 0, p) + 1:p]:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
    return depth > 0


def _trim_particles(word):
    """文の続きを巻き込んだ語（「各点の相互差が計測共通」）から、最後の助詞より後ろ（「計測共通」）を取り出す。

    切るのは、漢字・カタカナにはさまれた助詞（が・は・を・に・で・へ）だけである（かなを含む名称を切らない）。
    """
    cut = [m.end() for m in re.finditer(r"(?<=[一-龥ァ-ヶ])[がはをにでへ](?=[一-龥ァ-ヶA-Za-z])", word)]
    return word[cut[-1]:] if cut else word


def label_key(m):
    typ = re.sub(r"\s", "", m.group("type"))
    typ, num = TYPE_ALIAS.get(typ, typ), m.group("num")
    if num is not None:
        n = kanji_int(num)
        num = str(n) if n is not None else num.replace(".", "-")
    return typ, num


# ───────────────────────── 項番号の並び（「2-1 及び 2-2 項」「5.1 から 5.7 まで」） ─────────────────────────
def _numlist_pattern():
    parts = [ELEM.replace("@", "0")]
    for i in range(1, 6):
        parts.append(r"(?:[ ]?(?P<j%d>" % i + SEP + "|" + RANGE + r")[ ]?" + ELEM.replace("@", str(i)) + r"(?:[ ]?まで)?)?")
    return "".join(parts)


NUMLIST = _numlist_pattern()
NUMLIST_RX = re.compile(r"(?<![\d.\-A-Za-z第])" + NUMLIST)
NUMLIST_AT_RX = re.compile(NUMLIST)
NESTED_RX = re.compile(r"[ ]?の[ ]?" + NUMLIST)
SINGLE_DOT_RX = re.compile(r"(?<![\d.\-A-Za-z])(?P<n>[1-9]\d?)\.[ ]?(?=" + CUE + r"|の[ ]?(?:[" + JP + r"]|[a-z][.)]|\(?\d{1,2}\))|\(\d{1,2}\)[ ]?(?:" + CUE + r"|の|に|を))")
SINGLE_AT_RX = re.compile(r"(?P<n>[1-9]\d?)\.(?=[ ]?(?:" + CUE + r"|の|を|に|及び|、|\(\d{1,2}\)))")
# 箇条の番号。「3)」「(3)」「a)」
ITEM_RX = re.compile(r"(?:(?P<a>\d{1,2})[ ]?\)|\((?P<b>\d{1,2})\)|(?P<c>[a-z])[.)](?![a-z]))")
BARE_NUM_RX = re.compile(r"(?P<num>\d{1,3}(?:[.\-]\d{1,2})?|[A-Z](?![A-Za-z0-9]))(?=[ ]?(?:" + CUE + r"|に|を|の|は|、|,|\)|及び|又は|並びに|まで|$))")
BARE_AFTER = r"[ ]?(?:では|には|にも|の|に|を|で|は|から|まで|及び|又は|並びに|、|と|\))"


def parse_numlist(m):
    """列挙・範囲を [(番号, 箇条, 接尾の語, つなぎ)] にする。箇条は None か（書き方, [番号…]）。つなぎは None、"list"、"range"。"""
    out = []
    for i in range(6):
        n = m.group("n%d" % i)
        if not n:
            break
        join = m.group("j%d" % i) if i else None
        item = (")", [int(m.group("it%d" % i))]) if m.group("it%d" % i) else (("()", [int(m.group("ip%d" % i))]) if m.group("ip%d" % i) else None)
        out.append((tuple(int(x) for x in re.split(r"[-.]", n)), item,
                    (m.group("s%d" % i) or "").strip(), None if join is None else ("range" if RANGE_RX.fullmatch(join) else "list")))
    return out


# ───────────────────────── 抽出と解決（段 1〜8） ─────────────────────────
class _Pass:
    """1 チャンクの作業台。取った範囲（used）と、カギ括弧の中（quoted）を持つ。"""

    def __init__(self, c, toc=()):
        self.c, self.text = c, c.norm
        self.used = []
        self.quoted = [(m.start("name"), m.end("name")) for m in QUOTE_RX.finditer(c.norm)]
        self.used += [(a, min(b, len(c.norm))) for a, b in toc if a < len(c.norm)]  # 目次の中の番号や条は、見出しでも参照でもない
        self.anchors = []   # 段 1・3・4 で見つけた語。段 5 で直後の場所（loc）が付く
        self.found = []     # 段 6 で見つけた単独の場所
        self.refs = []      # 段 7・8 で作った参照（「同条」が直前の参照を引くのに使う）

    def free(self, s, e):
        return not any(s < b and a < e for a, b in self.used)

    def quoted_at(self, s, e):
        return any(s < b and a < e for a, b in self.quoted)

    def claim(self, s, e):
        if s >= e or not self.free(s, e):
            return False
        self.used.append((s, e))
        return True


class Extractor:
    def __init__(self, cat, disable=()):
        self.cat = cat
        self.disable = set(disable or ())
        self.refs = []
        self.vague = []
        self.dropped = collections.Counter()
        self.detected = collections.Counter()  # チャンクの id → 参照の件数

    def run(self):
        for name, chunks in self.cat.docs.items():
            D = self.cat.doc[name]
            aliases = []  # 段 2 の結果。文書の先頭から順にたまる: {"alias", "doc", "written", "at": (pos, off)}
            for c in chunks:
                P = _Pass(c, D._toc.get(c.pos, ()))
                self.stage1_documents(D, P)
                self.stage2_alias_definitions(D, P, aliases)
                self.stage3_alias_mentions(D, P, aliases)
                self.stage4_relative(D, P)
                self.stage5_following(D, P)
                self.stage6_standalone(D, P)
                self.stage7_concretize(D, P)
                self._count_vague(D, P)
        return self.refs

    # ───── 段 1: 文書の識別子と名称を探す ─────
    def stage1_documents(self, D, P):
        c, text, cands = P.c, P.text, []
        for s, e, tok, target in D.codes[c.pos]:
            if target is None and not strict_code(tok):
                self.dropped["目録に無い符号（型式などの短い語）"] += 1
                continue  # 範囲も取らない
            cands.append((s, e, {"kind": "code", "doc": target, "written": re.sub(r"\s+", " ", tok)}))
        for s, e, owners in self.cat.find_names(c):
            if len(owners) == 1 or D.name in owners:
                cands.append((s, e, {"kind": "name", "doc": D.name if D.name in owners else next(iter(owners)), "written": text[s:e]}))
        # カギ括弧で囲んだ名称。括弧の全体を 1 つの名称として照合する。名称として扱うのは、参照の語か場所の名前が続くものだけ
        for m in QUOTE_RX.finditer(text):
            s, e, name = m.start(), m.end(), m.group("name")
            if len(squeeze(name)) < 3:
                continue
            # 「○○規定」の、廃棄の定めによる … 名称のあとの「の〜」は、同じ文の中で参照の語まで続いていればよい
            if not (QUOTE_CUE_RX.match(text, e) or self._place_at(text, QUOTE_PLACE_RX.match(text, e).end())):
                continue
            inner = [t for t in D.codes[c.pos] if s < t[0] and t[1] < e]
            known = {t[3] for t in inner if t[3] and t[3] != "?"}
            titled = sorted(set(D.titles.get(squeeze(name), [])))
            by_name = self.cat.resolve_name(name)
            if titled:
                a = {"kind": "quote", "doc": D.name, "written": name, "titled": titled}
            elif len(known) == 1 or by_name:
                a = {"kind": "quote", "doc": next(iter(known)) if len(known) == 1 else by_name, "written": name}
            elif any(t[3] is None and strict_code(t[2]) for t in inner):
                tok = next(t for t in inner if t[3] is None and strict_code(t[2]))
                a = {"kind": "code", "doc": None, "written": re.sub(r"\s+", " ", tok[2]), "quoted": True}
            elif re.search(DOC_SUFFIX + r"$", squeeze(name)):
                a = {"kind": "quote", "doc": "?", "written": name}
            else:
                continue
            cands.append((s, e, a))
        # 長い一致から取る。カギ括弧の中にある識別子や名称は使わない（括弧の全体を名称として扱った場合だけ、上で拾っている）
        for s, e, a in sorted(cands, key=lambda x: (-(x[1] - x[0]), x[0])):
            if a["kind"] != "quote" and not a.get("quoted") and P.quoted_at(s, e):
                continue
            if P.claim(s, e):
                P.anchors.append(dict(a, s=s, e=e, stage=1))

    # ───── 段 2: 略称の定義を拾う ─────
    def stage2_alias_definitions(self, D, P, aliases):
        for a in P.anchors:
            if a["kind"] not in ("code", "name", "quote") or a["doc"] == D.name:
                continue
            m = ALIAS_RX.match(P.text, a["e"])
            alias = m and (m.group("q") or m.group("b"))
            if alias and len(squeeze(alias)) >= 1 and not any(x["alias"] == alias for x in aliases):
                aliases.append({"alias": alias, "doc": a["doc"], "written": a["written"], "at": (P.c.pos, m.end())})

    # ───── 段 3: 略称で探し直す ─────
    def stage3_alias_mentions(self, D, P, aliases):
        c, text, cands = P.c, P.text, []
        for x in aliases:
            for m in re.finditer(re.escape(x["alias"]), text):
                s, e = m.span()
                if (c.pos, s) < x["at"] or P.quoted_at(s, e):
                    continue
                if s > 0 and re.match("[A-Za-z0-9ァ-ヶ" + KANJI + "]", text[s - 1]):
                    continue  # 別の語の一部（「方法」の中の「法」など）
                cands.append((s, e, {"kind": "alias", "doc": x["doc"], "written": x["written"], "alias": x["alias"]}))
        # 文書群で定義された別名（旧番号・通称）。定義した文書に限らず、どの文書の本文でも使われる
        for m in (self.cat._alias_rx.finditer(text) if self.cat._alias_rx else ()):
            s, e = m.span()
            if P.quoted_at(s, e) or (s > 0 and re.match("[A-Za-z0-9ァ-ヶ" + KANJI + "]", text[s - 1])) or re.match("[A-Za-z0-9]", text[e:e + 1]):
                continue
            cands.append((s, e, {"kind": "alias", "doc": self.cat.aliases[squeeze(m.group())], "written": re.sub(r"\s+", " ", m.group()), "alias": m.group(), "corpus": True}))
        for s, e, a in sorted(cands, key=lambda x: (-(x[1] - x[0]), x[0])):
            if P.claim(s, e):
                P.anchors.append(dict(a, s=s, e=e, stage=3))

    # ───── 段 4: 相対的な参照の語を探す ─────
    def stage4_relative(self, D, P):
        text, cands = P.text, []
        for m in REL_RX.finditer(text):
            cands.append((m.start(), m.end(), {"kind": "rel", "word": re.sub(r"\s", "", m.group("w")), "k": m.group("k")}))
        for m in ANA_RX.finditer(text):
            cands.append((m.start(), m.end(), {"kind": "ana", "written": m.group()}))
        for m in OWN_RX.finditer(text):
            cands.append((m.start(), m.end(), {"kind": "own", "doc": D.name}))
        for m in PRE_RX.finditer(text):
            cands.append((m.start(), m.end(), {"kind": "pre", "doc": D.name}))
        for s, e, a in sorted(cands, key=lambda x: (-(x[1] - x[0]), x[0])):
            if not P.quoted_at(s, e) and P.claim(s, e):
                P.anchors.append(dict(a, s=s, e=e, stage=4))

    # ───── 段 5: 段 1・3・4 の語の直後にある場所の名前を読む ─────
    def stage5_following(self, D, P):
        c, text = P.c, P.text
        P.anchors.sort(key=lambda a: a["s"])
        for i, a in enumerate(P.anchors):
            if a.get("absorbed"):
                continue
            kind = a["kind"]
            if kind == "rel":
                # 「前条第 2 項」「同条第 3 項」: 相対的な語の直後の項・号
                m = ART_TAIL_RX.match(text, a["e"])
                if m and a["word"][-1] == "条" and m.group("p") and P.free(m.start(), m.end()) and not P.quoted_at(m.start(), m.end()):
                    P.claim(m.start(), m.end())
                    paras, end = [kanji_int(m.group("p"))], m.end()
                    while len(paras) < 6:  # 「前条第 4 項及び第 5 項」: 後ろの項も同じ条の項
                        j = JOIN_RX.match(text, end)
                        m2 = PARA_RX.match(text, j.end()) if j else None
                        if not m2 or not P.free(m2.start(), m2.end()):
                            break
                        P.claim(m2.start(), m2.end())
                        paras.append(kanji_int(m2.group("p")))
                        end = m2.end()
                    a["tail"] = {"para": paras[0], "paras": paras, "end": end}
                elif a["word"] in ("前条", "次条", "本条"):
                    # 「次条から第 7 条まで」: 相対的な語で始まる条の範囲。終わりの条までを読む
                    j = JOIN_RX.match(text, a["e"])
                    m2 = ART_RX.match(text, j.end()) if j and RANGE_RX.fullmatch(j.group("j")) else None
                    if m2 and not m2.group("p") and P.free(m2.start(), m2.end()) and not P.quoted_at(m2.start(), m2.end()):
                        u = UNTIL_RX.match(text, m2.end())
                        end = u.end() if u else m2.end()
                        P.claim(m2.start(), end)
                        a["until"] = {"art": (kanji_int(m2.group("a")), kanji_int(m2.group("b")) if m2.group("b") else 0), "end": end}
                continue
            if kind == "pre":
                q = PRE_SKIP_RX.match(text, a["e"]).end()
                loc = self._read_items(P, q) or self._read_numlist(P, q, relaxed=True)
                if loc and loc["kind"] == "section" and all(len(x[0]) == 1 and not x[2] and not x[1] for x in loc["elems"]) and "." not in loc["text"]:
                    # 「上記 2 から 4 まで」: 接尾の語も点も無い 1 段の番号は、項番号か、番号だけの箇条（- 2 …）かを字面では決められない
                    loc["unsure"] = "「上記 N」の N が、項番号か箇条の番号かを決められない"
            else:
                m = SKIP_RX.match(text, a["e"])
                p = m.end()
                after_no = text[max(0, p - 2):p].strip().endswith("の")
                T = self.cat.doc.get(a.get("doc")) if a.get("doc") not in (None, "?") else None
                other = kind in ("code", "name", "alias", "quote") and a.get("doc") != D.name
                loc = self._read_locator(D, P, p, after_no, relaxed=(kind == "own"), name_in=T if (other and T is not None) else None)
            if loc is None or not P.free(loc["start"], loc["end"]) or P.quoted_at(loc["start"], loc["end"]):
                continue
            P.claim(loc["start"], loc["end"])
            a["loc"] = loc
            # 語と場所の間にはさまった語（括弧書きの中の識別子、題名のカギ括弧）は、この参照の一部である
            for b in P.anchors[i + 1:]:
                if b["s"] >= loc["start"]:
                    break
                if b["s"] >= a["e"]:
                    b["absorbed"] = True
        # 名称の直後の括弧書きに識別子がある（「休暇規程(HR-012)第 5 条」）。識別子の側を正とし、名称の側は重ねて出さない
        for i, a in enumerate(P.anchors[:-1]):
            b = P.anchors[i + 1]
            if a["kind"] in ("name", "quote", "alias") and b["kind"] == "code" and not a.get("loc") and re.fullmatch(r"[ ]?\([ ]?", text[a["e"]:b["s"]]):
                a["absorbed"] = True

    @staticmethod
    def _place_at(text, p):
        """位置 p から、別表・図・条・章などの場所の名前が始まるか。"""
        for rx in (PART_RX, FLOAT_RX, FLOAT2_RX):
            m = rx.match(text, p)
            if m and (label_key(m)[1] is not None or label_key(m)[0] in UNNUMBERED_OK):
                return True
        return bool(ART_RX.match(text, p) or CHAP_RX.match(text, p))

    def _read_locator(self, D, P, p, after_no, relaxed=False, name_in=None):
        """位置 p から始まる場所の名前（並列・範囲を含む）。無ければ None。"""
        text = P.text
        for rx in (PART_RX, FLOAT_RX, FLOAT2_RX):
            m = rx.match(text, p)
            if m:
                typ, num = label_key(m)
                if num is not None or typ in UNNUMBERED_OK:
                    return self._label_loc(text, p, m.end(), typ, num)
        loc = self._read_articles(text, p)
        if loc:
            return loc
        m = CHAP_RX.match(text, p)
        if m:
            return {"kind": "chapter", "key": (m.group("k"), kanji_int(m.group("n"))), "text": m.group(), "start": p, "end": m.end()}
        m = NOTE_AFTER_RX.match(text, p)
        if m:
            return {"kind": "note", "n": int(m.group("n")), "text": m.group().strip(), "start": p, "end": m.end()}
        loc = self._read_numlist(P, p, relaxed=relaxed, after_no=after_no)
        if loc:
            return self._with_sub(text, loc)
        m = SINGLE_AT_RX.match(text, p)
        if m and (after_no or relaxed):
            return self._with_sub(text, {"kind": "section", "elems": [((int(m.group("n")),), None, "", None)], "text": m.group(), "start": p, "end": m.end()})
        if name_in is not None and "heading_name" not in self.disable:
            return self._read_heading_name(text, p, after_no, name_in)
        return None

    @staticmethod
    def _read_heading_name(text, p, after_no, T):
        """文書の識別子や名称に続く、見出しの名称での場所の指定。○○が、参照先の文書の見出しの題名と完全に一致するときだけ場所として読む。

          「X の○○による」「「△△規定」の、○○の定めによる」   … 「の」のあとの語（末尾の「の定め・の規定・の項」は外して照合する）
          「「△△規定」に定める○○の対象とする」                 … 「に定める」のあとの語
        番号でなく見出しの名前で節や条を指すのは、番号が改正で動く文書でよく使われる書き方であり、特定の文書に限らない。
        題名の完全な一致を条件にしているので、一致しなければ文書単位の参照のままである（誤って結ぶことはない）。
        """
        ok = lambda name: bool(re.search("[" + KANJI + "ァ-ヶ]{2}", name)) and bool(T.titles.get(squeeze(name)))
        if after_no:
            q = p + len(re.match(r"[、,]?[ ]?", text[p:]).group())
            m = NAME_LOC_RX.match(text, q)
            for name in ([m.group("name"), re.sub(r"の(?:定め|規定|項|条|章|節|欄)$", "", m.group("name"))] if m else []):
                if ok(name):
                    return {"kind": "name", "name": name, "text": m.group("name"), "start": q, "end": m.end()}
            return None
        m = re.compile(r"[ ]?に(?:定め|規定す)る(?P<name>[^\s、。,()「」『』|のをにはがでと]{2,16})(?=[のをにはがでと、])").match(text, p)
        if m and ok(m.group("name")):
            return {"kind": "name", "name": m.group("name"), "text": m.group("name"), "start": m.start("name"), "end": m.end()}
        return None

    @staticmethod
    def _item_of(m):
        style = ")" if m.group("a") else ("()" if m.group("b") else "a")
        return style, (int(m.group("a") or m.group("b")) if style != "a" else ord(m.group("c")) - 96)

    def _item_run(self, text, p):
        """位置 p から始まる箇条の番号の並び（「(1) 及び (2)」「3)、4)、6)」「2) から 4) まで」）。（書き方, [番号…], 終わり）。無ければ None。"""
        m = ITEM_RX.match(text, p)
        if not m:
            return None
        style, n = self._item_of(m)
        ns, end = [n], m.end()
        while len(ns) < 12:
            j = JOIN_RX.match(text, end)
            m2 = ITEM_RX.match(text, j.end()) if j else None
            if not m2 or self._item_of(m2)[0] != style:
                break
            n2 = self._item_of(m2)[1]
            if RANGE_RX.fullmatch(j.group("j")) and 0 < n2 - ns[-1] <= 12:
                ns += list(range(ns[-1] + 1, n2))
            ns.append(n2)
            end = m2.end()
        u = UNTIL_RX.match(text, end)
        return style, ns, (u.end() if u and len(ns) > 1 else end)

    def _read_items(self, P, p):
        """「上記 3)、4)」「上記 (1) 及び (2)」: 相対的な語の直後の箇条の番号。括弧書きの終わりの「)」（「(上記 3)」）は箇条としない。"""
        text = P.text
        run = self._item_run(text, p)
        if not run:
            return None
        if run[0] == ")" and _paren_open(text, p):
            return None
        return {"kind": "item", "style": run[0], "ns": run[1], "text": text[p:run[2]], "start": p, "end": run[2]}

    def _with_sub(self, text, loc):
        """項番号 1 つの直後の「の (4)」「の a)」「(4)」: その項の中の箇条の指定を続けて読む（「3. の (4) による」「4.(4) による」）。"""
        if len(loc["elems"]) != 1 or loc["elems"][0][1] is not None:
            return loc
        m = re.compile(r"[ ]?(?:の[ ]?)?").match(text, loc["end"])
        run = self._item_run(text, m.end())
        if not run:
            return loc
        if not re.match(r"[ ]?(?:" + CUE + r"|[のにをはがでと、。)]|及び|並びに|又は|項|$)", text[run[2]:]):
            return loc
        t, _, suffix, join = loc["elems"][0]
        return dict(loc, elems=[(t, (run[0], run[1]), suffix, join)], end=run[2], text=text[loc["start"]:run[2]].strip())

    def _read_numlist(self, P, p, relaxed=False, after_no=False):
        """位置 p から始まる項番号の並び。接尾の語（項・節・章）があるか、多段の番号に「の」か参照の語が付くものだけ読む。"""
        text = P.text
        m = NUMLIST_AT_RX.match(text, p)
        if not m:
            return None
        elems = parse_numlist(m)
        suffixed, multi = any(x[2] for x in elems), len(elems[0][0]) >= 2
        cue = bool(re.match(r"[ ]?(?:を)?" + CUE, text[m.end():]))
        follow = text[m.end():m.end() + 1]
        if suffixed or (multi and (after_no or cue or relaxed)) or (relaxed and follow and follow in "のにをでとは及、,)"):
            return {"kind": "section", "elems": elems, "text": m.group().strip(), "start": p, "end": m.end()}
        return None

    def _label_loc(self, text, s, e, typ, num):
        """別表・図などの場所。並列・範囲（別表 1 及び 2、図 1〜図 3）と、下位の指定（符号 A、の 3-1-1 項）を続けて読む。"""
        elems, end = [((typ, num), None)], e
        while len(elems) < 6:
            j = JOIN_RX.match(text, end)
            if not j:
                break
            join, q = ("range" if RANGE_RX.fullmatch(j.group("j")) else "list"), j.end()
            m = next((m for m in (rx.match(text, q) for rx in (PART_RX, FLOAT_RX, FLOAT2_RX)) if m), None)
            if m and label_key(m)[0] == typ and label_key(m)[1] is not None:
                elems.append((label_key(m), join))
            else:
                m = BARE_NUM_RX.match(text, q) if num is not None else None  # 種類の語を省いた番号（別表 1 及び 2）
                if not m:
                    break
                n = kanji_int(m.group("num"))
                elems.append(((typ, str(n) if n is not None else m.group("num").replace(".", "-")), join))
            end = m.end()
        if len(elems) > 1:
            u = UNTIL_RX.match(text, end)
            end = u.end() if u and any(j == "range" for _, j in elems) else end
        loc = {"kind": "label", "elems": elems, "text": text[s:end], "start": s, "end": end}
        if len(elems) == 1:
            m = SUBLOC_RX.match(text, end)
            if m:
                loc.update(sub=(m.group("word"), m.group("val")), end=m.end(), text=text[s:m.end()])
                return loc
            m = NESTED_RX.match(text, end)
            if m and any((m.group("s%d" % i) or "") for i in range(6) if m.group("n%d" % i)):
                loc.update(nums=parse_numlist(m), end=m.end(), text=text[s:m.end()])
        return loc

    def _read_articles(self, text, p, para_only=False):
        """位置 p から始まる条・項・号の並び。「第 3 条第 1 項及び第 2 項」の第 2 項は、直前の条を引き継ぐ。"""
        m = PARA_RX.match(text, p) if para_only else _art_match(text, p)
        if not m:
            return None
        g = m.groupdict()
        first = {"art": None if para_only else (kanji_int(g["a"]), kanji_int(g["b"]) if g.get("b") else 0),
                 "para": kanji_int(g["p"]) if g.get("p") else None, "go": kanji_int(g["g"]) if g.get("g") else None, "join": None}
        elems, end = [first], m.end()
        while len(elems) < 16:
            j = JOIN_RX.match(text, end)
            if not j:
                break
            join, q = ("range" if RANGE_RX.fullmatch(j.group("j")) else "list"), j.end()
            m2 = None if para_only else _art_match(text, q)
            if m2:
                g = m2.groupdict()
                elem = {"art": (kanji_int(g["a"]), kanji_int(g["b"]) if g.get("b") else 0), "para": kanji_int(g["p"]) if g.get("p") else None,
                        "go": kanji_int(g["g"]) if g.get("g") else None}
            else:
                m2 = ART_TAIL_RX.match(text, q)
                if not m2:
                    break
                prev = elems[-1]
                elem = {"art": prev["art"], "para": kanji_int(m2.group("p")) if m2.group("p") else prev["para"],
                        "go": kanji_int(m2.group("g") or m2.group("g2")) if (m2.group("g") or m2.group("g2")) else None}
            elem["join"] = join
            elems.append(elem)
            end = m2.end()
        if any(x["join"] == "range" for x in elems):
            u = UNTIL_RX.match(text, end)
            end = u.end() if u else end
        return {"kind": "article", "elems": elems, "text": text[p:end], "start": p, "end": end}

    # ───── 段 6: 残った単独の場所の名前を探す ─────
    def stage6_standalone(self, D, P):
        c, text, cands = P.c, P.text, []
        # 見出しや題名そのもの（場所の定義）は参照ではない。先に範囲を取って、後の候補に使わせない
        for s, e, typ, num, is_def, foreign in D.label_occ[c.pos]:
            if is_def:
                P.claim(s, e)
            else:
                cands.append(self._label_loc(text, s, e, typ, num))
        listed_until = 0
        for m in ART_RX.finditer(text):
            if m.start() < listed_until:
                continue  # 前の候補の並び（○○法第 10 条、第 16 条、…）の途中。並びの先頭の語（法令の名前など）が、並びの全体に掛かる
            loc = self._read_articles(text, m.start())
            if len(loc["elems"]) > 1:
                listed_until = loc["end"]
            ls, _ = c.line(m.start())
            if (c.pos, m.start()) in D.art_head_at or (len(loc["elems"]) == 1 and not m.group("p") and not c.flat[ls:m.start()].strip(" #>*-")
                                                       and re.match(ART_HEAD_AFTER, text[m.end():])):
                P.claim(m.start(), m.end())  # 条の見出しそのもの
                continue
            cands.append(loc)
        for m in CHAP_RX.finditer(text):
            ls, _ = c.line(m.start())
            if not c.flat[ls:m.start()].strip(" #>*-") and re.match(r"[ (]|$", text[m.end():m.end() + 1] or " "):
                P.claim(m.start(), m.end())  # 章・節の見出しそのもの
                continue
            cands.append({"kind": "chapter", "key": (m.group("k"), kanji_int(m.group("n"))), "text": m.group(), "start": m.start(), "end": m.end()})
        for m in PARA_RX.finditer(text):
            if D.has_articles:
                cands.append(dict(self._read_articles(text, m.start(), para_only=True), kind="para"))
            elif not m.group("g"):
                # 条の無い文書の「第 N 項」は、項番号 N とみなす。目録で引けたときだけ採る
                cands.append({"kind": "section", "elems": [((kanji_int(m.group("p")),), None, "項", None)], "text": m.group(), "start": m.start(), "end": m.end(), "weak": True})
        for m in NUMLIST_RX.finditer(text):
            s, e = m.span()
            elems = parse_numlist(m)
            suffixed = any(x[2] for x in elems)
            multi = all(len(x[0]) >= 2 or x[2] or x[1] for x in elems) and len(elems[0][0]) >= 2
            cue = bool(re.match(r"[ ]?(?:を)?" + CUE, text[e:]))
            bare = multi and (s == 0 or text[s - 1] in " \n(、。はをにでがとも") and bool(re.match(BARE_AFTER, text[e:]))
            if not (suffixed or (cue and multi) or bare):
                continue
            if not suffixed and (c.pos, s) in D.head_at:
                continue  # 見出しそのもの
            cands.append(self._with_sub(text, {"kind": "section", "elems": elems, "text": m.group().strip(), "start": s, "end": e, "weak": not suffixed, "cue": cue}))
        for m in SINGLE_DOT_RX.finditer(text):
            if (c.pos, m.start()) not in D.head_at:
                cands.append(self._with_sub(text, {"kind": "section", "elems": [((int(m.group("n")),), None, "", None)], "text": m.group().strip(),
                                                   "start": m.start(), "end": m.end(), "weak": True, "dot": True}))
        note_defs = {d.start() + len(d.group()) - len(d.group().lstrip(" >*-")) for d in NOTE_DEF_RX.finditer(c.flat)}
        for m in NOTE_RX.finditer(text):
            if m.start() in note_defs:
                P.claim(m.start(), m.end())  # 注の定義そのもの
            else:
                cands.append({"kind": "note", "n": int(m.group("n")), "text": m.group(), "start": m.start(), "end": m.end()})
        for m in PAGE_RX.finditer(text):
            cands.append({"kind": "page", "page": int(m.group("p1") or m.group("p2")), "text": m.group(), "start": m.start(), "end": m.end()})
        # 長い一致から取る。同じ長さなら前にあるもの、それも同じなら PRIORITY の順
        for loc in sorted(cands, key=lambda x: (-(x["end"] - x["start"]), x["start"], PRIORITY[x["kind"]])):
            s, e = loc["start"], loc["end"]
            if not P.free(s, e) or P.quoted_at(s, e):
                continue
            owner = self._owner_guard(D, P, s, loc["kind"])
            if owner is not None and owner.get("drop"):
                P.claim(s, e)
                self.dropped["括弧書きの続きの条（どの文書の条か読めない）"] += 1
                continue
            if loc["kind"] == "section" and loc.get("weak"):
                # 接尾の語の無い番号は、目録で全部引けたものだけ採る（数値や型式の一部を参照と取り違えないため）
                if owner is not None:
                    if loc.get("cue") and not loc.get("dot"):
                        P.claim(s, e)
                        P.found.append({"loc": loc, "owner": owner})
                    continue
                positions, note, missing = self._lookup(D, D, c, s, loc)
                if (not positions or missing) and "決められない" not in note:
                    self.dropped["番号＋参照の語（目録で引けず捨てた）"] += 1
                    continue
            P.claim(s, e)
            P.found.append({"loc": loc, "owner": owner})
        # 文書の種類の語で終わる名詞＋参照の語。ここまでの段で取られていない語だけを見る
        own = self.cat.names.get(D.name, ())
        for m in GENERIC_DOC_RX.finditer(text):
            s, e, name = m.start("name"), m.end("name"), m.group("name")
            if GENERIC_SKIP_RX.match(name) or re.fullmatch(DOC_SUFFIX + r"|図面|図書|法令", name) or not P.free(s, e) or P.quoted_at(s, e):
                continue
            if D.in_toc(c, s) or any(name in n or n in name for n in own):
                continue
            if P.claim(s, e):
                P.found.append({"generic": name, "s": s, "e": e})

    def _owner_guard(self, D, P, s, kind=None):
        """位置 s の場所の名前の直前が「目録に無い文書らしい語（＋の）」なら、その語を返す。自分の文書の場所と決めてよければ None。

        段 5 までで文書が決まった場所はここへ来ない。来るのは、略称・通称・旧番号・法令のように、目録で文書を決められない場合である。
        戻り値は {"word": 語, "external": 手元に無い文書と決めてよいか}。
        """
        raw = P.text[max(0, s - 90):s]
        if kind in ("article", "para", "chapter"):
            # 行の折り返しをまたいで読む（「…労基\n法第 20 条」「…法律(昭和○年法\n律第○号)第 N 条」）
            raw = re.sub(r"\n[ ]*", "", P.text[max(0, s - 160):s])[-110:]
            # 「○○法第 N 条」「○○法(昭和○年法律第○号)第 N 条」: 法令の条。目録の名称と字面が重ならなければ、手元に無い文書とする
            m = LAW_BEFORE_RX.search(raw)
            name = re.sub(r"\s", "", m.group("name")) if m else ""
            if name and LAW_NAME_RX.match(name) and not OWN_RX.fullmatch(name):
                if name.startswith("同"):
                    # 「同法施行規則第 N 条」: 直前に挙げた法令に付属する文書。直前が目録の文書なら、規則では決めない
                    known = any(b["kind"] in ("code", "name", "alias", "quote") and b.get("doc") not in (None, "?") and 0 <= s - b["e"] <= 150 for b in P.anchors)
                    return {"word": name, "external": not known}
                return {"word": name, "external": not self.cat.maybe_known(name)}
        if kind in ("article", "para", "chapter") and raw.rstrip().endswith(")"):
            depth = 0
            for ch in reversed(raw.rstrip()):
                depth += (ch == ")") - (ch == "(")
                if depth == 0:
                    break
            if depth > 0:
                # 直前の括弧書きが、前のチャンクや離れた位置から続いている（「)第 6 条の 2)。」）。どの文書の条かを読めないので、参照にしない
                # （チャンクの重なりで文の後ろだけが入った場合で、文の全体は前のチャンクで扱っている）
                return {"word": "", "external": False, "drop": True}
        raw = P.text[max(0, s - 90):s]
        if kind == "label" and not raw.endswith(" "):
            # 「○○法別表第 1」「○○法附則第 N 条」: 法令の別表・附則（語と場所がじかに続くときだけ）
            m = LAW_BEFORE_RX.search(raw)
            name = re.sub(r"\s", "", m.group("name")) if m else ""
            if name and LAW_NAME_RX.match(name) and not METHOD_TAIL_RX.search(name) and not OWN_RX.fullmatch(name) and not name.startswith("同") and not raw.rstrip().endswith(")"):
                return {"word": name, "external": not self.cat.maybe_known(name)}
        m = re.search(r"(?:" + DOC_SUFFIX + r"|」|』)[ ]?(?P<paren>" + _PAREN + r")[ ]?の?[ ]?$", raw) if raw.rstrip(" の").endswith(")") else None
        if m and LAW_NUMBER_RX.search(m.group("paren")):
            # 「○○基準(平成○年○○省告示第○号)第 N 条」: 括弧書きに法令番号があるので、手元に無い文書（告示など）である
            word = re.search(r"[^\s、。,()]{1,30}$", raw[:m.start("paren")].rstrip())
            return {"word": _trim_particles(word.group()) if word else m.group().strip(), "external": True}
        raw = raw[-40:]
        m = re.search(r"(?:" + DOC_SUFFIX + r"|\d[ ]?号|」|』)[ ]?(?:\([^)\n]{1,12}\))?[ ]?の?[ ]?$", raw)
        if m:
            head = raw[:m.end()].rstrip(" の")
            # 旧番号の形（「品規 9 号」）は、語と番号の間の空白ごと取る
            word = re.search(r"[^\s、。,()「」『』|:;]{1,12}[ ]?\d{1,4}[ ]?号$", head) or re.search(r"[^\s、。,()]{1,16}$", head)
            return {"word": _trim_particles(word.group()) if word else m.group().strip(), "external": False}
        m = re.search(r"([^\s、。,()「」『』|:;]{1,16})[ ]?の[ ]?$", raw)
        if m:
            x = m.group(1)
            if re.match(r"(?:この|本|当|上記|前記|下記|後記|上述|前述|後述|次|以下|左記|右記|当該|これら|それ|同)", x) or x[-1] in ".)項条章節表図":
                return None
            if x[-1].isdigit() and PLACE_WORD_RX.fullmatch(x):
                return None  # 「別表 1 の」「3.2 の」: 場所の名前に続く場所
            return {"word": _trim_particles(x), "external": False}
        return None

    # ───── 段 7: 文書と場所を組み合わせて具体的な参照にする（段 8 の展開は _lookup の中） ─────
    def stage7_concretize(self, D, P):
        items = [("anchor", a["s"], a) for a in P.anchors if not a.get("absorbed")] + [("found", f["loc"]["start"] if "loc" in f else f["s"], f) for f in P.found]
        for kind, _, item in sorted(items, key=lambda x: x[1]):
            if kind == "found":
                self._concretize_standalone(D, P, item)
            else:
                getattr(self, "_concretize_" + item["kind"])(D, P, item)

    def _cue_after(self, P, e, reach=14):
        return bool(re.match(r"[^。\n]{0,%d}?" % reach + CUE, P.text[e:]))

    def _concretize_code(self, D, P, a):
        c, text, loc, target = P.c, P.text, a.get("loc"), a["doc"]
        s, e = a["s"], a["e"]
        end = loc["end"] if loc else e
        if target == "?":
            self._emit(D, P, s, end, "文書番号のみ" if not loc else FORM_OTHER[loc["kind"]], loc["text"] if loc else "", None, written=a["written"], scope=None,
                       reason="識別子が目録の複数の文書に当たり、決められない", stage=1)
            return
        if target is None:
            # 目録に無い識別子。規格の番号の形か、参照の語・場所の名前を伴うときだけ、外部の文書として記録する
            toks = D.codes[c.pos]
            k = next((i for i, t in enumerate(toks) if t[0] == s), len(toks))
            listed = any(t[0] >= e and not re.sub(r"[\s、,.・及び\-]|" + CODE_RX.pattern, "", text[e:t[0]]) and self._cue_after(P, t[1], 6) for t in toks[k + 1:k + 4])
            named = bool(re.match(r"[" + JP + r"]{0,10}" + DOC_SUFFIX, text[e:])) or bool(re.search(r"(?:様式|帳票|図面|図番)[ ]?$", text[max(0, s - 6):s]))
            opened = bool(re.search(r"[;:「『(\[][ ]?$", text[max(0, s - 2):s])) and self._cue_after(P, e, 4)
            # 直前が機器の名前（気圧計 BR-80 により、校正槽 ST-230 の）である符号は、文書の番号ではなく機器の管理番号である
            device = bool(re.search(r"(?:計|器|機|槽|台|弁|管|炉|装置|設備|治具|ポンプ|センサ|モータ)[ ]?$", text[max(0, s - 8):s])) and not loc and not a.get("quoted")
            if device:
                self.dropped["機器の名前に続く符号（管理番号）"] += 1
            elif (re.match(STD_PREFIX + r"(?![A-Za-z])", a["written"]) or self._cue_after(P, e, 10) or listed or named or loc or opened or a.get("quoted")
                    or self._listed_document(D, c, s)):
                self._emit(D, P, s, end, "外部の文書", loc["text"] if loc else "", None, written=a["written"], scope="external", stage=1)
            else:
                self.dropped["目録に無い符号（参照の語なし）"] += 1
            return
        if target == D.name:
            if loc is None or (loc["kind"] == "label" and any(o[0] == loc["start"] and o[4] for o in D.label_occ[c.pos])):
                self.dropped["自分の文書の識別子だけの言及"] += 1  # 「自分の識別子＋題名」は別表などの柱（題名の定義）
                return
            self._emit_located(D, P, s, D, loc, None, stage=5)
            return
        T = self.cat.doc[target]
        if loc is None:
            self._emit(D, P, s, e, "文書番号のみ", "", target, written=a["written"], stage=1)
        else:
            self._emit_located(D, P, s, T, loc, a["written"], stage=5)

    def _listed_document(self, D, c, s):
        """位置 s の識別子が、関連する文書の一覧（「関連規程類」「適用規格及び関連指示書」「引用規格」などの見出しの下）の行頭にあるか。

        一覧の行は参照の語を伴わないが、行頭の「識別子＋題名」は、その文書を見よという記載である。
        """
        ls = c.line(s)[0]
        if not re.fullmatch(r"[ >*\-|]*(?:(?:\d{1,2}|[a-z])[.)]|\(\d{1,2}\)|[○・･])?[ ]*", c.norm[ls:s]):
            return False
        head = D._last("heads", c.pos, s)
        titles = [head[4]] if head else []
        md = D._last_md(c.pos, s)
        titles += [md[3]] if md else c.crumb[-1:]
        return any(REFLIST_RX.search(squeeze(x)) for x in titles)

    def _concretize_name(self, D, P, a):
        """目録にある名称・略称。識別子と違って普通の語でもあるので、場所の名前か参照の語が続くときだけ参照とする。"""
        loc = a.get("loc")
        if a["doc"] == D.name:
            if loc is not None:
                self._emit_located(D, P, a["s"], D, loc, None, stage=5)
            return
        if a["doc"] in (None, "?"):
            # 略称の元の文書が目録に無い、または名称だけで文書を決められない
            if loc is not None or self._cue_after(P, a["e"], 10):
                external = a["kind"] == "alias" and a["doc"] is None
                self._emit(D, P, a["s"], loc["end"] if loc else a["e"], "名称のみ" if not external else "外部の文書", loc["text"] if loc else "", None, written=a["written"],
                           scope="external" if external else None, reason="" if external else "名称が目録の文書と一致しない（略称・通称・旧番号の可能性）", stage=a["stage"])
            return
        T = self.cat.doc[a["doc"]]
        if loc is not None:
            self._emit_located(D, P, a["s"], T, loc, a["written"], stage=5)
        elif NAME_CUE_RX.match(P.text, a["e"]) or (a["kind"] == "quote" and QUOTE_CUE_RX.match(P.text, a["e"])):
            self._emit(D, P, a["s"], a["e"], "名称のみ", "", a["doc"], written=a["written"], stage=a["stage"])

    _concretize_alias = _concretize_name

    def _concretize_quote(self, D, P, a):
        if a.get("titled") and not a.get("loc"):
            # 自分の文書の見出しの題名を名指ししている
            self._emit(D, P, a["s"], a["e"], "名称のみ", a["written"], D.name, a["titled"][:3], stage=1)
            return
        self._concretize_name(D, P, a)

    def _concretize_ana(self, D, P, a):
        """「同基準」「同規定」など。同じチャンクで直前に挙げた文書を指す。場所の名前が続くときだけ参照とする。"""
        loc = a.get("loc")
        if loc is None:
            return
        prev = [b for b in P.anchors if b["kind"] in ("code", "name", "alias", "quote") and b["e"] <= a["s"] and a["s"] - b["e"] <= 150 and b.get("doc") not in (None, "?")]
        ext = [r for r in P.refs if r["scope"] == "external" and r["target_document"] and r["span"][1] <= a["s"] and a["s"] - r["span"][1] <= 150]
        if ext and (not prev or prev[-1]["e"] < ext[-1]["span"][1]):
            # 直前に挙げたのが手元に無い文書（法令など）である。「同法第 N 条」は、その文書の条
            self._emit(D, P, a["s"], loc["end"], "外部の文書", loc["text"], None, written=ext[-1]["target_document"], scope="external", stage=4)
            return
        if not prev:
            self._emit(D, P, a["s"], loc["end"], "名称のみ", loc["text"], None, written=a["written"], scope=None, reason="「同…」が指す文書が、同じチャンクの中に無い", stage=4)
            return
        T = self.cat.doc[prev[-1]["doc"]]
        self._emit_located(D, P, a["s"], T, loc, None if T is D else prev[-1]["written"], stage=5)

    def _concretize_own(self, D, P, a):
        if a.get("loc") is not None:
            self._emit_located(D, P, a["s"], D, a["loc"], None, stage=5, text=P.text[a["s"]:a["loc"]["end"]].strip())

    _concretize_pre = _concretize_own

    def _concretize_rel(self, D, P, a):
        c, word, s = P.c, a["word"], a["s"]
        end = a["tail"]["end"] if a.get("tail") else a["e"]
        if word.startswith("同"):
            # 「同条」「同項」は、同じチャンクで直前に挙げた参照と同じ先
            want = word[-1]
            prev = [r for r in P.refs if r["span"][1] <= s and s - r["span"][1] <= 200 and
                    (want in (r["locator"] or "") or (want == "項" and r["form"] in ("同じ文書の項", "文書番号＋項")))]
            if not prev:
                self._emit(D, P, s, end, "相対的な参照", P.text[s:end], D.name, [], reason="先行する参照が同じチャンクの中に無く、決められない", stage=4)
                return
            r = max(prev, key=lambda r: r["span"][1])
            T = self.cat.doc.get(r["target_doc"])
            positions = list(r["_positions"]) if T else []
            base = r.get("_loc")
            if a.get("tail") and T is not None and base and base["kind"] in ("article", "para") and base["elems"][-1]["art"]:
                positions = T.article_chunks(base["elems"][-1]["art"], a["tail"]["para"], (c.pos, s) if T is D else None) or []
            self._emit(D, P, s, end, "相対的な参照", P.text[s:end], r["target_doc"], positions, written=r["target_document"], scope=r["scope"],
                       note="直前の参照（%s）と同じ先" % (r["locator"] or r["target_document"]), reason="" if positions or r["scope"] == "external" else "直前の参照の先が決まっていない", stage=7)
            return
        positions, note = self._relative(D, c, s, word, a.get("k"), a.get("tail"))
        if a.get("until") and positions:
            # 範囲の終わりの条までの、間にある条（各条の先頭のチャンク）と、終わりの条を足す
            end = a["until"]["end"]
            here = D.article_at(c.pos, s)
            cur = D.art_inst[here]["key"] if here is not None else None
            first = {"前条": (cur[0] - 1, 0), "次条": (cur[0] + 1, 0), "本条": cur}[word] if cur else None
            last = a["until"]["art"]
            if first and first < last:
                for k in sorted(k for k in D.articles if first < k <= last):
                    got = D.article_chunks(k, None, (c.pos, s)) or []
                    positions = positions + [p for p in (got if k == last else got[:1]) if p not in positions]
                note = "範囲の間の条も結んだ"
        self._emit(D, P, s, end, "相対的な参照", P.text[s:end], D.name, positions[:12], note=note if positions else "", reason="" if positions else note, stage=7)

    def _concretize_standalone(self, D, P, f):
        if "generic" in f:
            titled = sorted(set(D.titles.get(squeeze(f["generic"]), []))) if "heading_name" not in self.disable else []
            if titled:
                # 自分の文書の見出しの題名と完全に一致する（「試験片の採取は、試験片採取要領による」）。その見出しを名指ししている
                self._emit(D, P, f["s"], f["e"], "名称のみ", f["generic"], D.name, titled[:3], stage=6)
            # 一般の名称での参照。目録の名称の一部や略称でありうる語は、規則では決めずに残す（LLM に回る）
            elif self.cat.maybe_known(f["generic"]):
                self._emit(D, P, f["s"], f["e"], "名称のみ", "", None, written=f["generic"], scope=None,
                           reason="名称が目録の文書と一致しない（略称・通称・旧番号の可能性）", stage=6)
            else:
                self._emit(D, P, f["s"], f["e"], "外部の文書", "", None, written=f["generic"], scope="external", stage=6)
            return
        loc, owner = f["loc"], f["owner"]
        if owner is not None and owner["external"]:
            self._emit(D, P, loc["start"], loc["end"], "外部の文書", loc["text"], None, written=owner["word"], scope="external", stage=6)
            return
        if owner is not None:
            self._emit(D, P, loc["start"], loc["end"], "名称のみ", loc["text"], None, written=owner["word"], scope=None,
                       reason="場所の前の語が目録の文書と一致しない（略称・通称・旧番号の可能性）", stage=6)
            return
        self._emit_located(D, P, loc["start"], D, loc, None, stage=6)

    def _emit_located(self, D, P, s, T, loc, written, stage, text=None):
        """文書 T の中の場所 loc を引いて、参照を 1 件作る。"""
        positions, note, missing = ([], loc["unsure"], True) if loc.get("unsure") else self._lookup(D, T, P.c, loc["start"], loc)
        same = T is D
        form = (FORM_SAME if same else FORM_OTHER)[loc["kind"]]
        reason = ""
        if not positions:
            reason = note or "場所が目録に無い"
        self._emit(D, P, s, loc["end"], form, text or loc["text"], T.name, positions, written=written, note=note if positions else "", reason=reason, stage=stage, loc=loc)

    # ───── 段 8: 範囲と並列を目録にある場所の一覧に直し、チャンクを引く ─────
    def _lookup(self, D, T, c, off, loc):
        """場所 loc を、文書 T のチャンクの順番の一覧にする。（一覧, 注記, 引けなかった要素があるか）を返す。"""
        kind = loc["kind"]
        if kind == "note":
            cand = T.notes.get(loc["n"], [])
            hit = sorted(cand, key=lambda p: (abs(p - c.pos), p < c.pos))[:1] if T is D else cand[:1]  # 自分の文書なら最も近い注の定義
            return hit, "" if hit else "注の定義が目録に無い", not hit
        if kind == "name":
            hit = sorted(set(T.titles.get(squeeze(loc["name"]), [])))[:3]
            return hit, "" if hit else "題名の一致する見出しが無い", not hit
        if kind == "page":
            hit = [x.pos for x in T.chunks if x.page == loc["page"] and x.pos != c.pos][:3]
            return hit, "" if hit else "そのページのチャンクが無い", not hit
        if kind == "chapter":
            defs = T.chapters.get(loc["key"])
            got = [min(defs)[0]] if defs else (T.section_chunks("main", (loc["key"][1],)) if loc["key"][0] == "章" else None)
            return (got or []), "" if got else "章・節が目録に無い", not got
        if kind in ("article", "para"):
            return self._lookup_articles(D, T, c, off, loc)
        if kind == "label":
            return self._lookup_labels(D, T, c, off, loc)
        if kind == "item":
            return self._lookup_items(D, c, off, loc)
        scope = D.scope_at(c.pos, off) if T is D else "main"
        return self._lookup_sections(T, scope, loc["elems"], hint=c.norm[loc["end"]:loc["end"] + 40])

    def _lookup_items(self, D, c, off, loc):
        """「上記 3)」: 参照元と同じ範囲・同じ項の中で、参照元より前にある最も近い箇条。"""
        out, miss = [], []
        scope, sect = D.scope_at(c.pos, off), D.section_at(c.pos, off)
        here = D.article_at(c.pos, off)
        floor = D.art_inst[here]["marks"][0] if here is not None else (D.order[0], 0)  # 条のある文書では、属する条の中だけを見る
        near = D._where[c.pos] - 3                                                      # 「上記」が指すのは近くの箇条である（3 チャンク前まで）
        for n in loc["ns"]:
            marks = [m for m in D._items_by.get((loc["style"], n), ()) if floor <= (m[0], m[1]) < (c.pos, off) and D._where[m[0]] >= near
                     and m[3] == scope and (sect is None or m[4][:1] == sect[1][:1])]
            if marks:
                out += [p for p in [max(marks)[0]] if p not in out]
            else:
                miss.append(str(n))
        return out[:6], ("目録に無い箇条: " + "、".join(miss)) if miss else "", bool(miss)

    def _pick_item(self, T, scope, t, style, n, hint="", direct=False):
        """項 t の中の箇条 n を 1 つに決める。（位置の一覧, 決められなかったか）を返す。

        同じ番号の箇条が複数あるときは、参照の直後の語（「3) の導電率規格」の導電率）が題名に合うもの、
        それで決まらなければ、項の見出しが見出し記号つきの文書に限り、見出し記号つきの箇条を採る。
        """
        marks = T.find_items(scope, t, style, n, direct)
        if len(marks) <= 1:
            return marks, False
        tail = squeeze(hint)[:28]

        def titled(m):
            c = T.by_pos[m[0]]
            rest = ITEM_RX.sub("", c.norm[m[1]:c.line(m[1])[1]], count=1)
            title = re.split(r"[\s、。,:()|]", rest.strip(), maxsplit=1)[0]
            return len(title) >= 2 and title[:4] in tail

        hit = [m for m in marks if titled(m)]
        if len(hit) == 1:
            return hit, False
        if T.head_is_md(scope, t):
            hit = [m for m in marks if m[2] == 2]
            if len(hit) == 1:
                return hit, False
        return [], True

    def _lookup_sections(self, T, scope, elems, cap=5, only_scope=False, hint=""):
        out, miss, notes = [], [], []
        for t, item, suffix, join in self._expand_sections(T, scope, elems):
            got = None
            name = "-".join(map(str, t))
            for sc in ([scope, "main"] if scope != "main" and not only_scope else [scope]):
                got = T.section_chunks(sc, t)
                if got and item is not None:
                    # 項の中の箇条の指定（3-1-1 3)項、3. の (4)）。箇条が 1 か所に決まれば、そのチャンクに絞る。
                    # 同じ番号の箇条が 2 か所以上にあって絞れなければ、先頭を採らず、決められないものとして残す
                    narrowed, unclear, found = [], False, False
                    for n in item[1]:
                        hit, vague = self._pick_item(T, sc, t, item[0], n, hint)
                        unclear, found = unclear or vague, found or bool(hit)
                        narrowed += [h[0] for h in hit if h[0] not in narrowed]
                    if unclear:
                        notes.append("同じ番号の箇条が複数あり、項の全体へ結んだ")
                    elif found:
                        got = narrowed
                    else:
                        notes.append("箇条は見つからず、項の全体へ結んだ")
                if got:
                    break
                paren = hint.lstrip().startswith(")")
                if len(t) >= 2 and item is None and (paren or suffix == "項") and not any(k[:len(t) - 1] == t[:-1] and len(k) >= len(t) for k in T.sections.get(sc, {})):
                    # 「N.M) 項」「N-M) の…」と閉じ括弧つきで書かれるか、「N-M 項」と書かれ、N-M の見出しが無く、N 項の箇条 M) があれば、
                    # その箇条へ結ぶ（1 か所に決まるときだけ）。閉じ括弧の無い書き方では、N 項のじかの箇条だけを見る
                    hit, vague = self._pick_item(T, sc, t[:-1], ")", t[-1], hint, direct=not paren)
                    if not hit and not vague:
                        hit, vague = self._pick_item(T, sc, t[:-1], "()", t[-1], hint, direct=not paren)
                    if vague:
                        name += "（見出しに無く、同じ番号の箇条が複数あり、決められない）"
                        break
                    if hit:
                        got = [hit[0][0]]
                        notes.append("%s は見出しに無く、箇条 %d) へ結んだ" % ("-".join(map(str, t)), t[-1]))
                        break
            if got:
                out += [p for p in got if p not in out]
            else:
                miss.append(name)
        if miss:
            notes.append("目録に無い項: " + "、".join(miss))
        if any(join == "range" for _, _, _, join in elems):
            cap = max(cap, 12)  # 範囲（5.1 から 5.7 まで）は、名指しされた項を上限で切り落とさない
        return out[:cap], " ".join(notes), bool(miss)

    def _expand_sections(self, T, scope, elems):
        """範囲（A〜B）を、目録にある同じ深さの番号へ広げる。"""
        out = []
        for t, item, suffix, join in elems:
            if join == "range" and out:
                a = out[-1][0]
                if len(a) == len(t) and a[:-1] == t[:-1]:
                    known = set()
                    for sc in (scope, "main"):
                        known |= {k for k in T.sections.get(sc, {}) if len(k) == len(t) and k[:-1] == t[:-1] and a < k < t}
                    out += [(k, None, suffix, None) for k in sorted(known)]
            out.append((t, item, suffix, join))
        return out

    def _lookup_articles(self, D, T, c, off, loc, cap=12):
        out, miss, notes, prev = [], [], [], None
        src = (c.pos, off) if T is D else None
        here = D.article_at(c.pos, off) if T is D else None
        for elem in loc["elems"]:
            if elem["art"] is None:
                # 条を言わない「第 N 項」は、属する条の項
                if here is None:
                    miss.append("属する条が分からない")
                    continue
                art, inst, why = T.art_inst[here]["key"], here, ""
                if elem["para"] and elem["para"] > 1 and not T.paras.get((here, elem["para"])):
                    # 属する条は、参照元の直前にある条の見出しから決めている。その条に項の印が無いなら、属する条の決め方が
                    # 合っていない見込みが高い（解説や引用の中にある）ので、結ばずに残す
                    miss.append("属する条（第%d条）に第%d項の印が無い" % (art[0], elem["para"]))
                    continue
            else:
                art = elem["art"]
                inst, why = T.pick_article(art, src)
            if elem["join"] == "range" and prev and not elem["para"]:
                # 「第 A 条から第 B 条まで」: 目録にある間の条を足す（各条の先頭のチャンク）
                between = sorted(k for k in T.articles if prev < k < art)
                for k in between:
                    out += [p for p in (T.article_chunks(k, None, src) or [])[:1] if p not in out]
                if between:
                    notes.append("範囲の間の条も結んだ")
            if inst is None:
                miss.append("第%d条%s（%s）" % (art[0], "の%d" % art[1] if art[1] else "", why))
            else:
                got, note = T.inst_chunks(inst, elem["para"], src)
                out += [p for p in got if p not in out]
                if note and note not in notes:
                    notes.append(note)
            prev = art
        if miss:
            notes.append("結べない条: " + "、".join(miss))
        if loc["kind"] == "para" and here is not None and out:
            notes.append("属する条（第 %d 条）の項とみなした" % T.art_inst[here]["key"][0])
        return out[:cap], " ".join(notes), bool(miss)

    def _lookup_labels(self, D, T, c, off, loc):
        elems = self._expand_labels(T, loc["elems"])
        if len(elems) > 1:
            out, miss = [], []
            for key, _ in elems:
                got = T.label_chunks(key)
                out += [p for p in (got or []) if p not in out]
                if not got:
                    miss.append(key[0] + (key[1] or ""))
            return out[:6], ("目録に無い題名: " + "、".join(miss)) if miss else "", bool(miss)
        key = elems[0][0]
        span = T.label_chunks(key)
        if loc.get("sub"):
            word, value = loc["sub"]
            hits = T.find_sub(word, value, span) if span else []
            if hits:
                return hits[:3], "", False
            if span:
                return span, "下位の指定は範囲の中に見つからず、場所全体へ結んだ", False
            if "sub_fallback" not in self.disable:
                # 題名（別表 1 など）が目録に無い。題名が変換で落ちることは、図や枠の中に題名がある文書で一般に起こる。
                # 下位の指定（符号 A など）の見出しが、文書の中で 1 か所（隣り合うチャンクのまとまり 1 つ）にだけあるなら、
                # 「別表 1 符号 A」が指しているのはそこだと決められるので結ぶ。2 か所以上にあれば決められないので結ばない。
                hits = T.find_sub(word, value)
                if hits and len(hits) <= 3 and all(T._where[b] - T._where[a] == 1 for a, b in zip(hits, hits[1:])):
                    return hits, "題名は目録に無く、下位の指定の見出しへ結んだ", False
            return [], "題名が目録に無い", True
        if loc.get("nums"):
            if not span:
                return [], "題名が目録に無い", True
            scope = key[0] + (key[1] or "")
            out, note, missing = self._lookup_sections(T, scope, loc["nums"], cap=4, only_scope=True)
            return (out or span), note if out else "項は見つからず、場所全体へ結んだ", False
        return (span or []), "" if span else "題名が目録に無い", not span

    def _expand_labels(self, T, elems):
        """範囲（別表 1〜別表 3）を、目録にある同じ種類の番号へ広げる。"""
        out = []
        for key, join in elems:
            if join == "range" and out and key[1] and out[-1][0][1] and key[1].isdigit() and out[-1][0][1].isdigit():
                a, b = int(out[-1][0][1]), int(key[1])
                out += [((key[0], str(n)), None) for n in range(a + 1, b) if (key[0], str(n)) in T.labels]
            out.append((key, join))
        return out

    def _relative(self, D, c, off, word, k, tail=None):
        """前条・次項・本条 など。参照元の位置から数えて決める。（チャンクの一覧, 注記）を返す。"""
        kind = word[-1]
        step = (kanji_int(k) or 1) if k else 1
        every = word.startswith("前各")
        if D.art_marks:
            here = D.article_at(c.pos, off)
            if here is None:
                return [], "条の外にあり、数えられない"
            cur, src = D.art_inst[here]["key"], (c.pos, off)
            if kind == "条":
                if every:
                    # 前各条: 同じまとまり（番号が続いている範囲）の、前にあるすべての条
                    got = [D.inst_chunks(i)[0][:1] for i in range(here) if D.art_inst[i]["block"] == D.art_inst[here]["block"]]
                    got = list(dict.fromkeys(p for g in got for p in g))
                    return got[:12], "" if got else "前の条が目録に無い"
                if word[0] == "本":
                    return D.inst_chunks(here, tail["para"] if tail else None, src)[0], ""
                want = {"前": (cur[0] - step, 0), "次": (cur[0] + 1, 0)}[word[0]]
                if word[0] == "前" and cur[1]:
                    want = (cur[0], cur[1] - 1) if cur[1] > 1 else (cur[0], 0)  # 枝番の条（第 7 条の 2）の前条
                got = D.article_chunks(want, tail["para"] if tail else None, src)
                for extra in (tail["paras"][1:] if tail and got else []):
                    got = got + [p for p in (D.article_chunks(want, extra, src) or []) if p not in got]
                if step > 1 and word[0] == "前":
                    got = [p for n in range(step, 0, -1) for p in (D.article_chunks((cur[0] - n, 0), None, src) or [])]
                    got = list(dict.fromkeys(got))
                return (got or []), "" if got else "数えた先の条が目録に無い"
            if kind == "項":  # 条の中の項。行頭の番号で数える（第 1 項は条の先頭）
                para = D.para_at(c.pos, off)
                n = para[1] if para and para[0] == here else 1
                at = lambda i: D.inst_chunks(here, i, src)[0] if (i == 1 or D.paras.get((here, i))) else []
                if every:
                    got = list(dict.fromkeys(p for i in range(1, n) for p in at(i)))
                    return got, "" if got else "前の項が無い"
                want = {"前": n - step, "次": n + 1}.get(word[0], n)
                got = at(want) if want >= 1 else None
                if step > 1 and word[0] == "前":
                    got = list(dict.fromkeys(p for i in range(max(1, n - step), n) for p in at(i)))
                return (got or []), ("第 %d 項を指すとみなした" % want) if got else "数えた先の項が目録に無い"
            return [D.art_inst[here]["marks"][-1][0]], "号の位置までは数えず、属する条の先頭へ"  # 号は条の中（同じチャンクならリンクなし）
        item = D.item_at(c.pos, off)
        sect = D.section_at(c.pos, off)
        in_item = bool(item) and (sect is None or (item[0], item[1]) >= max((p, o) for p, o, _ in D.sections[sect[0]][sect[1]]))
        if kind == "項" and in_item and sect is not None:
            # 項番号の見出しのある文書の「前項・次項」は、見出しの項で数える。箇条として数えるのは、参照のある行の行頭が番号の箇条
            # （「2)前項の圧力から…」）のときだけである。「a. … 次項の入替えを行う」や、箇条の並びのあとの段落の「前項」は、見出しの項を指す
            ls = c.line(off)[0]
            in_item = item[0] == c.pos and item[1] >= ls and item[5] in (")", "()") and not c.flat[ls:item[1]].strip(" >*-|")
        if kind in "項号" and in_item:
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

    # ───── 記録 ─────
    def _emit(self, D, P, s, e, form, locator, target_doc, positions=None, written=None, scope="auto", note="", reason="", stage=0, loc=None):
        """参照を 1 件記録する。

        source / source_doc / source_position  参照元のチャンク
        quote            参照を含む原文の一文
        form             型（文書番号＋項、同じ文書の項、別表・図・添付資料、相対的な参照、外部の文書 など）
        locator          場所を示す語（原文の表記）
        target_document  参照先の文書の表記（原文のまま。同じ文書なら None）
        target_doc       参照先の文書（目録にあるファイル名。決まらなければ None）
        scope            same_document / other_document / external（手元に無い文書）/ None（規則では文書を決められない）
        level            chunk（チャンクへ結ぶ）/ document（文書へ結ぶ）/ in_chunk（同じチャンクの中で済む）/ none（結ばない）
        targets          参照先のチャンク [{"id", "position"}]。target_ids は、その id だけの配列
        reason           規則で結べなかった理由（結べたとき、外部の文書と決まったときは空）
        history          参照元が改正履歴の中にあるか
        stage            見つけた段（1・3・4 = 語だけ、5 = 直後の番号、6 = 単独の番号、7 = 相対的な参照の具体化）
        """
        c = P.c
        T = self.cat.doc.get(target_doc) if target_doc else None
        positions = list(positions or []) if T else []
        same = target_doc == D.name
        in_chunk = bool(positions) and same and all(p == c.pos for p in positions)
        links = [p for p in positions if not (same and p == c.pos)]
        if scope == "auto":
            scope = "external" if target_doc is None else ("same_document" if same else "other_document")
        level = "chunk" if links else ("in_chunk" if in_chunk else ("document" if (T is not None and not same) else "none"))
        if level == "none" and scope not in ("external",) and not reason:
            reason = note or "参照先を決められない"
        ref = {
            "source": c.id, "source_doc": D.name, "source_position": c.position, "quote": c.quote(s, e), "form": form, "locator": locator or "",
            "target_document": written, "target_doc": target_doc, "scope": scope, "level": level,
            "targets": [{"id": T.by_pos[p].id, "position": T.by_pos[p].position} for p in links] if T else [],
            "target_ids": [T.by_pos[p].id for p in links] if T else [],
            "reason": reason if level in ("none", "document") else "", "note": note, "history": D.in_history(c, s), "stage": stage,
            # 文書だけを指す参照のうち、文書の中の読むべき場所を探す意味があるもの（同じ文に参照の語がある）。
            # 発行目録の行や関連する文書の一覧の識別子は、文書へ結ぶだけで足りる
            "places": level == "document" and bool(re.match(r"[^。\n]{0,60}?" + CUE, P.text[e:])),
            "span": (s, e), "_loc": loc, "_positions": positions,
        }
        P.refs.append(ref)
        self.refs.append(ref)
        self.detected[c.id] += 1

    def _count_vague(self, D, P):
        """番号の無い「上記・下記・次の・以下の」。隣り合う記述を指すので結ばず、件数だけ数える。"""
        for m in VAGUE_RX.finditer(P.text):
            if not any(a["s"] <= m.start() < a["e"] and a.get("loc") for a in P.anchors) and not P.quoted_at(m.start(), m.end()) \
                    and not re.match(r"[ ]?(?:第[ ]?)?\d", P.text[m.end():]):
                self.vague.append({"source": P.c.id, "source_doc": D.name, "source_position": P.c.position, "quote": P.c.quote(m.start(), m.end())[:80]})


FORM_SAME = {"section": "同じ文書の項", "article": "同じ文書の項", "para": "同じ文書の項", "chapter": "同じ文書の項", "item": "同じ文書の項", "label": "別表・図・添付資料",
             "note": "注", "name": "名称のみ", "page": "その他"}
FORM_OTHER = {"section": "文書番号＋項", "article": "文書番号＋項", "para": "文書番号＋項", "chapter": "文書番号＋項", "label": "文書番号＋別表・図",
              "note": "注", "name": "文書番号＋見出し名", "page": "その他"}


# ───────────────────────── 入口 ─────────────────────────
def find_references(chunks, disable=()):
    """チャンクの一覧から、規則で参照を抽出して解決する。

    chunks: [{"id", "fileName", "position", "text", "page_number"（任意）, "section"（任意）}]。目録は、渡したチャンクだけから作る。
    戻り値:
      references  参照の一覧（Extractor._emit の説明のとおり）
      chunks      チャンクの id → {"references": 規則が検出した参照の数, "cue": 本文に参照の語があるか,
                                    "llm": 規則の参照が掛かっていない文で、LLM の抽出に回す価値のあるものがあるか（llm_worthy）}
      vague       番号の無い「上記・下記」など（結ばない。件数の確認用）
      dropped     参照としなかった語の件数（理由ごと）
      catalogue   目録の要約（文書ごとの識別子、名称、見出しの数 など。点検用）
    """
    unknown = set(disable or ()) - set(OPTIONAL_RULES)
    if unknown:
        raise ValueError("unknown rule: %s" % ", ".join(sorted(unknown)))
    cat = Catalogue([c for c in chunks if c.get("fileName") is not None and c.get("id") is not None], disable)
    ex = Extractor(cat, disable)
    refs = ex.run()
    out = []
    for r in refs:
        out.append({k: v for k, v in r.items() if k not in ("span", "_loc", "_positions")})
    spans = collections.defaultdict(list)
    for r in refs:
        spans[r["source"]].append(r["span"])
    info = {}
    for name, cs in cat.docs.items():
        D = cat.doc[name]
        for c in cs:
            toc = D._toc.get(c.pos, ())
            info[c.id] = {"references": ex.detected.get(c.id, 0), "cue": _has_cue(c.norm),
                          "llm": llm_worthy(c, spans.get(c.id, ()), lambda off, c=c, D=D: D.in_history(c, off), lambda off, toc=toc: any(a <= off < b for a, b in toc))}
    catalogue = {name: {"identifiers": sorted("-".join(f) for f in cat.codes[name]), "names": sorted(cat.names[name]),
                        "sections": {sc: len(tb) for sc, tb in D.sections.items()}, "labels": sorted(k[0] + (k[1] or "") for k in D.labels),
                        "articles": len(D.articles), "notes": len(D.notes), "history_ranges": len(D.history), "rejected_heading_candidates": len(D.rejected)}
                 for name, D in cat.doc.items()}
    return {"version": RULE_VERSION, "references": out, "chunks": info, "vague": ex.vague, "dropped": dict(ex.dropped), "catalogue": catalogue}
