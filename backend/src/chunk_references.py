"""チャンク間の参照リンク（REFERS_TO）を作る後処理。

本文に「○○規程の 3-5 項による」「別表 1 による」「図 2 参照」「前条の定めにより」のように、
別の箇所を見るよう求める記述があるとき、その記述のあるチャンクから参照先のチャンクへリレーションを張る。
チャンク同士のリレーションは NEXT_CHUNK（並び順）と SIMILAR（類似度）しか無く、参照はどちらにも現れない。

張り方は 2 つから選ぶ（環境変数 CHUNK_REFERENCE_MODE。既定は llm）。
  llm     LLM で抽出と特定を行う（下の手順 1〜4。method = 'llm'）
  rule    規則だけ。LLM を 1 回も呼ばない（method = 'rule'）
規則による抽出と解決は chunk_reference_rules.py にある（識別子と場所の名前の目録を作り、パターンで拾って目録で引く。
特定の文書番号や言い回しを決め打ちした規則は持たない）。

LLM の手順。判断は LLM とリランカーに任せる。
  1) 抽出: チャンクごとに、参照の記述を LLM が抜き出す（原文、参照先の文書、文書内の位置、見に行く内容）
  2) 文書の特定: 参照先の表記が手元のどの文書かを決める。文書が多いときは候補を絞ってから LLM に選ばせる
  3) チャンクの特定: 参照先の文書の中から、LLM が参照先を選ぶ（下の (a)）
  4) 書き込み: 文書ごとに (:Chunk)-[:REFERS_TO {method: 'llm'}]->(:Chunk) を書く。チャンクまで絞れない場合は
     (:Chunk)-[:REFERS_TO]->(:Document)。手元に無い文書への参照は Chunk.external_references に表記を残す

結び先の選び方。
  (a) 結び先の一覧: 参照先の文書の節の木を「節の経路 ｜ 要点（Section.summary）か本文の先頭」の一覧で見せ、LLM に節を選ばせる。
      選んだ節に主所属するチャンク（下位の節を含む）へ REFERS_TO を張り、リレーションに選んだ節（target_sections・target_section_paths）を残す。
      章・節の層（section_layer）は、その節をそのまま節への参照の結び先にする。節の層がまだ無い文書は、チャンクの見出しの経路で節の木を作る。
      節の木が作れない文書（見出しが無い）は、チャンクの本文の先頭の一覧から選ばせる
  (b) 参照でない文を落とす: 抜き出した一文（と、本文でその一文がある行）に、
      参照の言い回し（参照・準拠・に従う・に定める など）も、位置の語（項番号・別表・図-1・上記 など）も、文書番号・文書の名前も無ければ落とす
      （「試験回転数は…吸入温度により変化する。」の「により」は原因を表すだけ）
  (c) 社外の文書: 参照先の文書の表記に文書番号があれば文書番号の規則（document_numbers.json）で
      手元の文書に直し、手元に無い番号なら手元に無い文書とする（LLM に選ばせない）。番号の無い文書の名前（「形式別単独運転要領」）が
      手元のどの文書の題名とも合わなければ、手元に無い文書とし、手元のチャンクに結ばない
  (d) 参照元の中にある箇所: 位置の語の図・表・項の番号が、参照元のチャンク自身の見出しや図の題の行に
      すべてあれば（「(P2/5 図-1 参照)」と同じチャンクに「図-1」）、どこにも結ばない
抽出と特定の結果はチャンクに保存し、やり直しのときは変わっていない分を使い回す（文書が増減したときだけ引き直す）。
結び先の選び方の印（LINK_KEY）が保存と違う回は、保存した抽出の結果を使い、(b)〜(d) の判定と文書・節の特定だけをやり直す（抽出は呼び直さない）。
失敗の扱い: 抽出か特定に失敗したチャンクは、既存のリンクを消さずに残し、次回の対象にする。失敗の件数は戻り値とログに出す。
REFERS_TO という型は他の仕組み（別の参照グラフなど）も使うので、消すのは自分が書いたもの（method = 'llm' と 'rule'）だけにする。
method の無い REFERS_TO には触れない。rule では、LLM が同じ参照元から同じ先へ張ったリンクを消し、規則のリンクだけを残す。
"""
import hashlib
import json
import logging
import math
import os
import re
import unicodedata
import uuid
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional

from src import document_layer_rules
from src.chunk_reference_rules import find_references
from src.document_layer_rules import annex_key, heading_form, parse_locator
from src.shared.common_fn import batches

# プロンプトや保存の形を変えたら上げる。チャンクに保存した結果は、版が違えば使わない
EXTRACT_VERSION = "4"
METHOD = "llm"
RULE_METHOD = "rule"
MODES = ("rule", "llm")
DEFAULT_MODE = "llm"  # 設定が無いときの張り方
SELF = "__self__"

# LLM に見せる候補の数と分量、失敗の扱い
SHORTLIST = 8              # 参照 1 件ごとに見せるチャンクの数（節の木が無い文書）
DOCUMENT_SHORTLIST = 12    # 参照 1 件ごとに見せる文書の数
PREFILTER = 80             # リランカーに掛ける前に、字面の重なりで残す候補の数
FORCED = 4                 # 位置の語をそのまま含むので、リランカーの順位に関係なく見せるチャンクの数
NEIGHBOR_CHARS = 2500      # 同じ文書の中の参照で、参照元の前後から必ず見せる字数
LISTING_CHARS = 9000       # 一覧の全体の字数
SECTION_CHUNKS = 6         # 選んだ節 1 つから結ぶチャンクの数
OUTLINE_MAX = 40           # 節がこれより多い文書は、一覧に出す節を絞る
OUTLINE_KEEP = 25          # 絞るときに、リランカーの上位から残す節の数
NAME_MATCH = 0.8           # 字面で候補に入れる文書名の近さ（最長の共通部分が短い方に占める割合）
QUOTE_OVERLAP = 0.5        # 抜き出した一文が本文にそのまま無いとき、本文と重なっていなければならない割合
LLM_TIMEOUT = 300.0        # LLM 1 回の待ち時間（秒）
MAX_FAILURE_RATE = 0.5     # 失敗したチャンクがこの割合を超えた文書は、何も書き込まない
ABORT_AFTER = 5            # LLM への問い合わせがこの回数続けて失敗したら止める

EXTRACT_PROMPT = """次の本文は、文書「{doc}」の一部である。本文の中から、読み手に別の箇所を見るよう求めている記述（参照）をすべて抜き出せ。

参照に含めるもの:
- 他の文書（規格、規程、法令、図面、様式など）を名指しして、そこに従う、そこを見る、と述べている記述
- 同じ文書の別の章・条・項、別表・別紙・付録・添付資料、図、表、ページを名指ししている記述
- 「前条」「次項」「上記 2 項」「同条第 3 項」のように、番号や単位の付いた別の箇所を相対的に指している記述

参照に含めないもの:
- 「下記」「上記」「次の」「以下」とだけ書かれ、番号も名前も無い記述
- 「〜による」「〜により」が、手段・原因・担当者を表すだけで、見に行く先を示していない記述
- この本文自身の表題、見出し、文書番号、欄の名前
- 改訂の来歴（いつ、どこを、どう改めたかの記録）と、目次

参照ごとに次を答えよ。
- quote: 参照を含む原文を 1 文。本文からそのまま写す
- target_document: 参照先の文書の名前や番号。原文の表記のまま。同じ文書の中を指す場合は null。
  別表・別紙・付録・添付資料などが文書の名前なしで書かれている場合も、同じ文書の一部として null にする
- locator: 参照先の位置を示す語（章・条・項の番号、別表・図・表の番号、ページ、「前条」など）。原文の表記のまま。文書だけを指す場合は null
- topic: 参照先で確かめる内容を 10〜30 字で

最後に JSON を 1 つだけ出力する。参照が無ければ {{"references": []}} とする。
{{"references": [{{"quote": "", "target_document": null, "locator": null, "topic": ""}}]}}

本文:
{text}
"""

DOCUMENT_PROMPT = """文書「{source_doc}」に次の記述がある。

記述: {quote}

この記述の中の「{name}」が指している文書を、下の候補から選べ。候補は手元にある文書である。
表記のゆれ（区切りの有無、全角と半角、版を表す記号の有無）は同じ文書とみなす。
- 候補の中にあれば、その番号を答える
- 参照元の文書「{source_doc}」自身、またはその付属物（別表、別紙、付録、添付資料など）を指している場合は S と答える
- 候補に無い文書（別の規格、法令、別の文書など）を指している場合は 0 と答える
番号または S を 1 つだけ出力する。説明は書かない。

{listing}
"""

CHUNK_PROMPT = """文書「{source_doc}」に次の記述がある。

記述: {quote}
参照先の文書: {target_doc}
参照先の位置: {locator}
確かめる内容: {topic}

次の文書片は、参照先の文書「{target_doc}」の一部で、文書の中での順に並んでいる。{source_note}
この記述が見るよう求めている内容そのものが書かれている文書片の番号を答えよ。
- 参照先の位置（章・条・項、別表、図など）が示されている場合は、その見出しや表題と中身がある文書片を選ぶ
- 同じ位置を参照しているだけの文書片、目次、改訂の来歴は選ばない
- 中身が複数の文書片にまたがる場合は、3 つまで選んでよい
- 該当する文書片が無い場合は 0 と答える
番号だけをカンマ区切りで 1 行に出力する。説明は書かない。

{listing}
"""

SOURCE_MARK = "（参照元の記述は、この位置にある文書片に書かれている）"

OUTLINE_PROMPT = """文書「{source_doc}」に次の記述がある。

記述: {quote}
参照元の節: {source_path}
記述の前の本文: {before}
参照先の文書: {target_doc}
参照先の位置: {locator}
確かめる内容: {topic}

次の一覧は、参照先の文書「{target_doc}」の節を、文書の中での順に並べたものである。各行は「番号 節の経路 ｜ 節の内容の説明」である。{source_note}
この記述が見るよう求めている内容そのものが書かれている節の番号を答えよ。
- 参照先の位置（章・条・項、別表・別紙・添付資料、図、表など）が示されている場合は、その見出しや表題の節を選ぶ
- 「上記例」「上記」「前項」「添付資料」のように番号の無い参照は、参照元の節との位置関係と内容の説明から選ぶ
- 記述が節の一部だけを指している場合は、その部分に当たる下位の節を選ぶ。指していない兄弟の節を含む上位の節は選ばない
- 中身が複数の節にまたがる場合は、3 つまで選んでよい
- 記述と同じ参照を書いているだけの節（同じ文言の見出しや、同じ文書を名指ししているだけの行）は選ばない
- 記述が文書を名指ししているだけで、文書の中の特定の箇所を指していない場合（関連する文書の一覧の行など）は 0 と答える
- 該当する節が無い場合も 0 と答える
番号だけをカンマ区切りで 1 行に出力する。説明は書かない。

{listing}
"""

OUTLINE_SOURCE_NOTE = "★ の付いた節に、参照元の記述が書かれている。"

# 結び先の選び方の判定や指示を変えたら上げる。保存した結果は、印が違えば判定と特定をやり直す（抽出は使い回す）
LINK_VERSION = "5"
# 保存した結果に付ける、結び先の選び方の印（節の一覧・(b)・(c)・(d) を行う形）
LINK_KEY = "v%s-Lo-n1-e1-i1" % LINK_VERSION
# 参照でない文の判定（b）。位置の語（項番号・別表・図の番号・上記 など）。小数（168.3）は位置の語にしない
_POSITION_CUE = re.compile(
    r"第\s*\d+\s*[章条項節号]|\d+\s*(?:章|条|項(?!目)|節)|\d+(?:\s*[-‐－−]\s*\d+)+|\d+\.\d+\.\d+"
    r"|別表|別紙|別添|別図|付表|付図|付録|附録|付属書|附属書|添付"
    r"|[図表]\s*[-‐－−]?\s*\d|上記|前記|下記|前条|次条|前項|次項|同条|同項|本条|本項|ページ|頁|P\s*\d+\s*/\s*\d+")
# 参照の言い回しのうち、それだけで参照と分かるもの（「による」「により」は手段・原因にも使うので入れない）
_REFERENCE_CUE = re.compile(r"参照|準拠|準ず|準じ|に従|に定め|のとおり|の通り|を適用|基づ|基き|基く|に記載|に規定")
# 手元に無い文書の識別子の形（JIS B 8340、QC-AB-012、X-00123）
_IDENTIFIER = document_layer_rules._IDENT_RX
_DOC_KINDS = ["規程", "規定", "規則", "基準", "要領", "要綱", "細則", "標準", "仕様書", "方案", "規格", "指針", "手順書", "マニュアル", "指示書", "承認図"]
# 番号の無い文書の名前（「形式別単独運転要領」）。助詞「の」や区切りを含まない、種類の語で終わる語
_DOC_PHRASE = re.compile(r"(?<![本当同])([^\s\d、。,，.．「」『』()（）\[\]［］:：;；・のはがをにでと]{2,24}?(?:%s))" % "|".join(_DOC_KINDS))
_SELF_PREFIX = re.compile(r"(?:本|この|当|同|上記|下記|前記|次の|以下の)\s*$")
# 参照元の中にある箇所（d）の番号: 図・表、付属（別表など）
_LABEL = re.compile(r"(?P<kind>[図表]|別表|別紙|付表|付図|付録)\s*[-‐－−]?\s*(?P<n>\d+)(?!\d)")
_DASH = r"\s*[-‐－−ー]\s*"

class LinkAborted(RuntimeError):
    """LLM に続けてつながらないなど、これ以上進めても無駄な状態。ここまでの書き込みは残る"""


def _norm(text: str) -> str:
    """照合用の正規化。全角半角を揃え、空白と Markdown の飾りを除く"""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"<br\s*/?>", "", text, flags=re.I)
    return re.sub(r"[\s*_|`\\]", "", text)


def _squash(text: str) -> str:
    """名前の照合用の正規化。全角半角を揃え、記号と空白を除いて小文字にする"""
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", text or "")).lower()


def _longest(a: str, b: str) -> int:
    """a と b の最長の共通部分の字数"""
    return SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b)).size


def _json_of(raw: str) -> Optional[dict]:
    """LLM の出力から、references を持つ最後の JSON を取り出す（前置きに書式の例が出ても、答えは最後に来る）。

    見つからなければ None（失敗として扱う）。
    """
    decoder = json.JSONDecoder()
    raw = raw or ""
    found, end = None, 0
    for match in re.finditer(r"\{", raw):
        if match.start() < end:
            continue
        try:
            obj, length = decoder.raw_decode(raw[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("references"), list):
            found, end = obj, match.start() + length
    return found


def _last_line(raw: str) -> str:
    lines = [line.strip() for line in (raw or "").strip().splitlines() if line.strip()]
    return re.sub(r"[*`。.．]+$", "", re.sub(r"^[*`]+", "", lines[-1])).strip() if lines else ""


def _numbers_of(raw: str, upper: int) -> Optional[List[int]]:
    """回答の最後の行が番号と区切りだけのときに番号を読む。0 や「なし」は空、読めない形は None（失敗として扱う）"""
    line = _last_line(raw)
    if not line:
        return None
    if re.fullmatch(r"[\[「(（]?\s*(該当)?(なし|無し|ない|none|None|NONE|N/A)\s*[\]」)）]?", line):
        return []
    if not re.fullmatch(r"[\s\d,、，・\[\]]+", line):
        return None
    numbers = []
    for token in re.findall(r"\d+", line):
        n = int(token)
        if 1 <= n <= upper and n not in numbers:
            numbers.append(n)
    return numbers


def _choice_of(raw: str, upper: int):
    """文書の特定の回答を読む。番号 1 つ、S（参照元の文書自身）、0（手元に無い）のどれか。読めない形は None"""
    match = re.fullmatch(r"[\[「(（]?\s*([Ss]|\d{1,4})\s*[\]」)）]?", _last_line(raw))
    if not match:
        return None
    token = match.group(1)
    if token in ("S", "s"):
        return SELF
    n = int(token)
    return n if 0 <= n <= upper else None


def _default_ask() -> Callable[[str], str]:
    from src.llm import get_llm
    model = os.environ.get("CHUNK_REFERENCE_MODEL") or os.environ.get("GRAPH_CLEANUP_MODEL") or "gemma4"
    llm, _, _ = get_llm(model)

    def ask(prompt: str) -> str:
        try:
            content = llm.invoke(prompt, timeout=LLM_TIMEOUT).content
        except TypeError:
            # 待ち時間の指定を受け付けないモデルの場合
            content = llm.invoke(prompt).content
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

    return ask


def _bigrams(text: str) -> set:
    text = _norm(text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


class _Corpus:
    """1 つの文書のチャンクを、字面で粗く絞るための索引（文字 2 字の集合と、その珍しさ）"""

    def __init__(self, texts: List[str]):
        self.normalized = [_norm(text) for text in texts]
        self.grams = [{t[i:i + 2] for i in range(len(t) - 1)} for t in self.normalized]
        counts: Dict[str, int] = {}
        for grams in self.grams:
            for gram in grams:
                counts[gram] = counts.get(gram, 0) + 1
        total = max(len(texts), 1)
        self.weight = {gram: math.log(1 + total / count) for gram, count in counts.items()}

    def top(self, query: str, keep: int, must: Optional[str] = None) -> List[int]:
        """位置の語をそのまま含むチャンクを先に残し、残りを珍しさで重み付けした重なりの順で埋める"""
        grams = _bigrams(query)
        score = lambda i: sum(self.weight.get(gram, 0.0) for gram in grams & self.grams[i])
        order = sorted(range(len(self.grams)), key=lambda i: -score(i))
        key = _norm(must or "")
        first = [i for i in order if len(key) >= 2 and key in self.normalized[i]][:keep]
        taken = set(first)
        return first + [i for i in order if i not in taken][:keep - len(first)]


def _rank(query: str, texts: List[str], keep: int, rerank, corpus: Optional[_Corpus] = None, must: Optional[str] = None) -> List[int]:
    """候補を keep 件に絞り、その添字を返す。候補が多いときは字面で粗く絞ってからリランカーに掛ける"""
    indices = list(range(len(texts)))
    if len(indices) <= keep:
        return indices
    corpus = corpus or _Corpus(texts)
    if len(indices) > PREFILTER:
        indices = corpus.top(query, PREFILTER, must)
    try:
        scores = rerank(query, [texts[i] or "-" for i in indices])
        order = sorted(zip(indices, scores), key=lambda x: -x[1])
        return [i for i, _ in order[:keep]]
    except Exception as exc:
        logging.warning("chunk references: rerank failed (%s); falling back to character overlap", exc)
        allowed = set(indices)
        return [i for i in corpus.top(query, len(texts), must) if i in allowed][:keep]


def _name_match(name: str, file_name: str):
    """参照の表記とファイル名の字面の近さ。（最長の共通部分の字数, それが短い方に占める割合, 長さの差）を返す"""
    a, b = _squash(name), _squash(os.path.splitext(file_name or "")[0])
    if not a or not b:
        return 0, 0.0, 0
    size = _longest(a, b)
    return size, size / min(len(a), len(b)), abs(len(a) - len(b))


def _mentions(term: str, body: str) -> bool:
    """LLM が返した文書名や位置の語が、本文に実際に書かれているか。

    「別表 1 及び別表 2」から位置「別表 2」を取り出す場合のように、そのままの並びでは無いことがあるので、
    そのまま含まれない場合は、語の中の数字と、2 字以上の語がすべて本文にあれば認める。
    """
    key = _norm(term)
    if not key or key in body:
        return True
    numbers = re.findall(r"\d+", key)
    words = re.findall(r"[^\W\d_]{2,}", key)
    return bool(numbers or words) and all(n in body for n in numbers) and all(w in body for w in words)


def extract_references(doc: str, text: str, ask: Callable[[str], str]) -> Optional[dict]:
    """手順 1。本文から参照の記述を抜き出す。LLM の出力が読めなければ None を返す（失敗）

    原文に無い quote、本文に無い文書名や位置（LLM が作り変えたもの）は捨て、dropped に理由を残す。
    """
    obj = _json_of(ask(EXTRACT_PROMPT.format(doc=doc, text=text)))
    if obj is None:
        return None
    body = _norm(text)
    clean = lambda v: (str(v).strip() or None) if v not in (None, "", "null") else None
    kept, dropped = [], []
    for ref in obj["references"]:
        if not isinstance(ref, dict):
            continue
        quote = str(ref.get("quote") or "").strip()
        target, locator = clean(ref.get("target_document")), clean(ref.get("locator"))
        key = _norm(quote)
        if not key:
            continue
        if key not in body:
            if _longest(key, body) < max(8, int(len(key) * QUOTE_OVERLAP)):
                dropped.append({"quote": quote, "reason": "原文が本文に無い"})
                continue
        missing = [term for term in (target, locator) if term and not _mentions(term, body)]
        if missing:
            dropped.append({"quote": quote, "reason": "本文に無い語: " + " / ".join(missing)})
            continue
        kept.append({"quote": quote, "target_document": target, "locator": locator, "topic": clean(ref.get("topic")) or ""})
    return {"references": kept, "dropped": dropped}


def document_candidates(name: str, quote: str, others: List[dict], rerank) -> List[dict]:
    """文書の特定で LLM に見せる候補。文書が多いときは、字面の近い文書とリランカーの上位で絞る"""
    keep = DOCUMENT_SHORTLIST
    if len(others) <= keep:
        # 一覧の並びを表記ごとに変える（番号と文書の並びがたまたま一致して、誤りを見分けられなくなるのを防ぐ）
        return sorted(others, key=lambda d: hashlib.md5((name + d["fileName"]).encode("utf-8")).hexdigest())
    # 字面の近い文書を先に入れる（番号が 1 字違いの文書を取り違えないため）。共通部分の長い順、長さの差の小さい順に並べ、
    # 枠は半分までにする。残りは必ずリランカーの順で埋める
    matches = []
    for index, document in enumerate(others):
        size, ratio, gap = _name_match(name, document["fileName"])
        if size >= 3 and ratio >= NAME_MATCH:
            matches.append((-size, gap, index))
    lexical = [index for _, _, index in sorted(matches)[:keep // 2]]
    ranked = _rank(f"{name} {quote}", [f"{d['fileName']}: {d['head']}" for d in others], keep, rerank)
    return [others[i] for i in (lexical + [i for i in ranked if i not in lexical])[:keep]]


def resolve_document(name: str, ref: dict, source_doc: str, documents: List[dict], ask, rerank):
    """手順 2。参照先の表記を、取り込み済みの文書のファイル名に直す。

    戻り値: ファイル名、SELF（参照元の文書自身）、None（手元に無い）。回答が読めないときは例外を出す。
    """
    candidates = document_candidates(name, ref.get("quote") or "", [d for d in documents if d["fileName"] != source_doc], rerank)
    listing = "\n".join(f"[{i + 1}] {d['fileName']}: {d['head']}" for i, d in enumerate(candidates)) or "（候補なし）"
    choice = _choice_of(ask(DOCUMENT_PROMPT.format(source_doc=source_doc, quote=ref.get("quote") or "", name=name, listing=listing)), len(candidates))
    if choice is None:
        raise ValueError(f"unreadable answer for document '{name}'")
    if choice == SELF:
        return SELF
    return candidates[choice - 1]["fileName"] if choice else None


def _query_of(ref: dict) -> str:
    """候補を絞る検索の問い（位置の語・確かめる内容・一文）"""
    return " ".join(x for x in (ref.get("locator"), ref.get("topic"), ref.get("quote")) if x)


def _locator_keys(locator: Optional[str]) -> List[str]:
    """位置の語そのものと、その中の「3-5」「2.3.1」のような複数の段を持つ番号（2 字以上のもの）"""
    locator = unicodedata.normalize("NFKC", locator or "")
    return [key for key in [_norm(locator)] + re.findall(r"\d+(?:[-.]\d+)+", locator) if len(key) >= 2]


def resolve_chunks(ref: dict, source: dict, target_doc: str, chunks: List[dict], ask, rerank, corpus: Optional[_Corpus] = None) -> List[dict]:
    """手順 3。参照先の文書のチャンクから、参照先を選ぶ。回答が読めないときは例外を出す。

    同じ文書の中の参照では、参照元の前後のチャンクを一定の分量だけ必ず候補に入れ、参照元の位置を一覧に示す
    （「前条」「上記 2 項」のような相対的な参照を解けるようにする）。
    """
    same = target_doc == source["fileName"]
    candidates = [c for c in chunks if c["id"] != source["id"]]
    if not candidates:
        return []
    texts = [c["text"] or "" for c in candidates]
    if corpus is not None and same:
        corpus = None  # 参照元を除いたので、文書全体の索引とは並びが合わない
    picked = set(_rank(_query_of(ref), texts, SHORTLIST, rerank, corpus, ref.get("locator")))
    # 位置の語、または「3-5」「2.3.1」のように複数の段を持つ番号をそのまま含むチャンクは、リランカーの順位に関係なく候補に入れる
    # （見出しがチャンクの途中に埋もれていると、リランカーの上位に来ないことがある）
    keys = _locator_keys(ref.get("locator"))
    if keys:
        forced = [i for i, text in enumerate(texts) if i not in picked and any(key in _norm(text) for key in keys)]
        picked.update(forced[:FORCED])
    if same:
        # 参照元の前後を、字数で決めた分量だけ入れる（チャンクが小さいと、個数では前の条まで届かない）
        window = NEIGHBOR_CHARS
        position = source["position"] or 0
        for direction in (-1, 1):
            used = 0
            for i in sorted((i for i, c in enumerate(candidates) if ((c["position"] or 0) - position) * direction > 0),
                            key=lambda i: abs((candidates[i]["position"] or 0) - position)):
                if used >= window:
                    break
                picked.add(i)
                used += len(texts[i])
    shortlist = sorted((candidates[i] for i in picked), key=lambda c: c["position"] or 0)
    # 一覧が LLM の入力に収まるよう、長い文書片は頭の部分だけを見せる
    limit = max(300, LISTING_CHARS // max(len(shortlist), 1))
    lines, marked = [], not same
    for i, c in enumerate(shortlist):
        if not marked and (c["position"] or 0) > (source["position"] or 0):
            lines.append(SOURCE_MARK)
            marked = True
        text = c["text"] or ""
        lines.append(f"[{i + 1}]\n{text if len(text) <= limit else text[:limit] + '…'}")
    if not marked:
        lines.append(SOURCE_MARK)
    note = "参照元の記述がある文書片は一覧に含めていないが、その位置を示してある。" if same else ""
    prompt = CHUNK_PROMPT.format(source_doc=source["fileName"], quote=ref["quote"], target_doc=target_doc,
                                 locator=ref.get("locator") or "指定なし", topic=ref.get("topic") or "指定なし",
                                 source_note=note, listing="\n\n".join(lines))
    numbers = _numbers_of(ask(prompt), len(shortlist))
    if numbers is None:
        raise ValueError("unreadable answer for chunk selection")
    return [shortlist[n - 1] for n in numbers[:3]]


def _body(text: str) -> str:
    """チャンクの本文。先頭に付けたパンくずの行（[文書名 > 見出し]）を除く。

    パンくずは見出しの文面を写したものなので、そこから参照を抜き出すと、関係のないチャンクが参照元になる。
    """
    return re.sub(r"^\[[^\]\n]*\]\n", "", text or "")


def _head(text: str) -> str:
    """文書の冒頭。先頭のパンくずの行を除く"""
    return re.sub(r"\s+", " ", _body(text))[:160]


def _mode(value: Optional[str] = None) -> str:
    """参照の張り方。llm = LLM で張る（既定）。rule = 規則だけ（LLM を呼ばない）"""
    mode = (value or os.environ.get("CHUNK_REFERENCE_MODE") or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        logging.warning("chunk references: unknown CHUNK_REFERENCE_MODE '%s'; using '%s'", mode, DEFAULT_MODE)
        return DEFAULT_MODE
    return mode


def _rule_pass(rows: List[dict]) -> Dict:
    """規則による抽出と解決（LLM もグラフも使わない）。記録の形を、LLM の記録にそろえて返す"""
    found = find_references(rows)
    records = [dict(r, locator=r["locator"] or None, topic="", reused=False, method=RULE_METHOD) for r in found["references"]]
    return {"records": records, "dropped": found["dropped"]}


def _ledger_config():
    """文書番号の規則（document_layer_rules）と、その設定（document_numbers.json）"""
    try:
        return document_layer_rules, document_layer_rules.load_config()
    except Exception as exc:  # 設定が読めなくても参照リンクは止めない（文書番号による判定だけを行わない）
        logging.warning("chunk references: the document number rules could not be read (%s)", exc)
        return None, None


def _context(quote: str, body: str) -> str:
    """一文と、本文でその一文がある行（LLM が行の途中で一文を切った場合に、同じ行の「上記例では」などを見落とさないため）"""
    key = _norm(quote)
    lines = []
    for line in (body or "").splitlines():
        n = _norm(line)
        if key and n and len(n) >= 2 and (key in n or n in key):
            lines.append(line)
    return "\n".join([quote] + lines)


def _document_like(name: str, R=None, cfg=None) -> bool:
    """文書の表記らしいか（文書番号・識別子・種類の語で終わる名前・『』で括った名前）"""
    t = unicodedata.normalize("NFKC", name or "").strip()
    if not t:
        return False
    if (R and cfg and R.document_numbers_in(t, cfg)) or _IDENTIFIER.search(t):
        return True
    if re.search(r"(%s)\s*[』」]?\s*$" % "|".join(_DOC_KINDS), t) or t.startswith(("『", "「")):
        return True
    return False


def is_reference_like(ref: dict, body: str, R=None, cfg=None) -> bool:
    """(b) 抜き出した一文が参照らしいか。一文とその行に、参照の言い回し・位置の語・文書番号・文書の名前のどれかがあれば参照とみなす"""
    if (ref.get("quote") or "").lstrip().startswith("#"):
        return False  # 見出しの行（抽出の指示でも、本文自身の見出しは参照に含めない）
    text = unicodedata.normalize("NFKC", _context(ref.get("quote") or "", body))
    if _POSITION_CUE.search(text) or _REFERENCE_CUE.search(text) or _IDENTIFIER.search(text):
        return True
    if R and cfg and R.document_numbers_in(text, cfg):
        return True
    if any(len(m.group(1)) >= 6 for m in _DOC_PHRASE.finditer(text)):
        return True
    return _document_like(ref.get("target_document") or "", R, cfg)


def in_source_chunk(ref: dict, body: str) -> bool:
    """(d) 位置の語の図・表・付属・項の番号が、参照元のチャンク自身の見出しや図の題の行（行の頭）にすべてあるか。
    一文の行は数えない。他の文書を名指ししている参照と、番号の無い参照（上記例・添付資料）は判定しない（False）"""
    if ref.get("target_document"):
        return False
    locator = unicodedata.normalize("NFKC", ref.get("locator") or "")
    labels = [re.escape(m.group("kind")) + r"\s*[-‐－−ー]?\s*" + m.group("n") for m in _LABEL.finditer(locator)]
    numbers = [t["number"] for t in parse_locator(locator) if t["form"] in ("節", "項") and t["number"] and "-" in t["number"]]
    labels += [_DASH.join(n.split("-")) for n in numbers]
    patterns = [re.compile(r"^\s*(?:[#|*>・\-]\s*)*" + label + r"(?![\d])") for label in labels]
    if not patterns:
        return False
    key = _norm(ref.get("quote") or "")
    lines = []
    for line in unicodedata.normalize("NFKC", body or "").splitlines():
        n = _norm(line)
        # 一文の行は除く（一文を含む行と、複数の行にまたがる一文の一部の行）。「図-1」だけの題の行は一文の一部の字面でも残す
        if not n or (key and (key in n or (n in key and len(n) >= max(6, len(key) // 2)))):
            continue
        lines.append(line)
    return all(any(p.search(line) for line in lines) for p in patterns)


def _title_match(name: str, text: str) -> bool:
    """文書の名前が、手元の文書の題名（ファイル名か冒頭）に現れるか。最長の共通部分が名前の 8 割以上で 4 字以上"""
    a, b = _squash(name), _squash(text)
    if len(a) < 2 or not b:
        return False
    size = _longest(a, b)
    return size >= min(4, len(a)) and size / len(a) >= 0.8


def _file_numbers(documents: List[dict], R=None, cfg=None) -> Dict[str, List[str]]:
    """手元の文書の文書番号（正規化したもの）-> ファイル名の一覧"""
    numbers: Dict[str, List[str]] = {}
    if R and cfg:
        for d in documents:
            canon = R.number_of_file(d["fileName"], cfg)[0]
            if canon:
                numbers.setdefault(canon, []).append(d["fileName"])
    return numbers


def ledger_document(ref: dict, source_doc: str, documents: List[dict], R=None, cfg=None, numbers: Optional[Dict[str, List[str]]] = None):
    """(c) 参照先の文書を、文書番号の規則と手元の文書の題名で決める。LLM に選ばせる前に行う。
    numbers は _file_numbers(documents, R, cfg) の結果（渡さなければここで作る）。
    戻り値: ("file", ファイル名) / ("self", None) / ("external", 表記) / None（決めない。LLM に選ばせる）"""
    name = ref.get("target_document")
    quote = unicodedata.normalize("NFKC", ref.get("quote") or "")
    if numbers is None:
        numbers = _file_numbers(documents, R, cfg)
    # 手元の文書の題名: ファイル名と、冒頭の最初の見出し（冒頭の本文まで見ると、本文に書かれた社外の文書の名前に合ってしまう）
    title_of = lambda head: (re.match(r"\s*#*\s*([^#]{0,80})", head or "").group(1) or "").strip()[:60]
    titled = lambda n: any(_title_match(n, d["fileName"]) or _title_match(n, title_of(d.get("head"))) for d in documents)
    if name:
        t = unicodedata.normalize("NFKC", name)
        canons = R.document_numbers_in(t, cfg) if (R and cfg) else []
        if canons:
            files = [f for c in canons for f in numbers.get(c, [])]
            if not files:
                return ("external", name)
            if source_doc in files:
                return ("self", None)
            return ("file", files[0]) if len(set(files)) == 1 else None
        if re.search(r"別表|別紙|別添|付表|付図|付録|附録|付属|添付", t) or (R and R.is_self_reference(t)):
            return None
        ident = _IDENTIFIER.search(t)
        if ident and not any(_squash(ident.group(0)) in _squash(d["fileName"] + " " + (d.get("head") or "")) for d in documents):
            return ("external", name)
        if _document_like(t, R, cfg) and not ident and not titled(t):
            return ("external", name)
        return None
    # 文書の表記が無い参照: 一文に自分の文書の位置の語が無く、文書の名前だけがあり、それが手元の題名に無ければ社外の文書
    if _POSITION_CUE.search(quote):
        return None
    phrases = [m.group(1) for m in _DOC_PHRASE.finditer(quote)
               if len(m.group(1)) >= 6 and not _SELF_PREFIX.search(quote[:m.start(1)])]
    if phrases and not any(titled(p) for p in phrases):
        return ("external", phrases[0])
    return None


class _Outline:
    """1 つの文書の節の木（章・節の層の Section、無ければチャンクの見出しの経路から作る）と、チャンクの所属"""

    def __init__(self, sections: List[dict], members: Dict[str, List[tuple]], positions: Dict[str, int]):
        self.sections = {s["id"]: s for s in sections}
        self.order = [s["id"] for s in sorted(sections, key=lambda s: (s.get("first_position") if s.get("first_position") is not None else 10 ** 9,
                                                                            s.get("offset") or 0, s.get("level") or 0))]
        self.members = members        # 節 -> [(チャンク, 主所属か)]
        self.positions = positions    # チャンク -> 位置
        self.children: Dict[str, List[str]] = {}
        for s in sections:
            if s.get("parent") and s["parent"] in self.sections:
                self.children.setdefault(s["parent"], []).append(s["id"])

    def path(self, sid: str) -> str:
        sec = self.sections[sid]
        return " > ".join(sec.get("path") or [sec.get("title") or ""])

    def subtree(self, sid: str, depth: int = 3) -> List[str]:
        out, frontier = [sid], [sid]
        for _ in range(depth):
            frontier = [c for f in frontier for c in self.children.get(f, []) if c not in out]
            out += frontier
        return out

    def chunks_of(self, sid: str, exclude: Optional[str] = None, cap: int = 6, loose: bool = False) -> List[str]:
        """節に主所属するチャンク（下位の節を含む）。無ければ節に掛かるチャンク。参照元は除く"""
        primary = {c for s in self.subtree(sid) for c, p in self.members.get(s, []) if p}
        found = sorted(primary - {exclude}, key=lambda c: self.positions.get(c) or 0)
        if not found and (loose or exclude not in primary):
            # 主所属のチャンクが無い小さな節（前の節のチャンクの中にある）: 節に掛かるチャンク。
            # 参照元が主所属の節（中身が参照元のチャンクにある）は、端が掛かるだけの隣のチャンクに結ばない
            found = sorted({c for c, _ in self.members.get(sid, [])} - {exclude}, key=lambda c: self.positions.get(c) or 0)
        return found[:cap]

    def source_sections(self, chunk_id: str) -> set:
        return {sid for sid, lst in self.members.items() for c, p in lst if c == chunk_id and p}

    def top(self, sid: str) -> str:
        cur, seen = sid, set()
        while cur in self.sections and self.sections[cur].get("parent") in self.sections and cur not in seen:
            seen.add(cur)
            cur = self.sections[cur]["parent"]
        return cur

    def by_locator(self, locator: str, source_id: Optional[str] = None) -> List[str]:
        """位置の語の章・節・項・付属の番号（「3-5項」「上記2-1～2-3」「別表2」「別紙-1」）を、この文書の節に引く（章・節の層の番号の規則と同じ読み方）。
        同じ番号の節が本文と付属の資料の両方にあれば、参照元と同じ範囲（同じ最上位の節）のものを先に、無ければ本文（番号のある最上位の節）のものを採る。
        引けない番号があれば空（LLM に選ばせる）"""
        targets = [t for t in parse_locator(locator or "") if t["form"] != "号"]
        if not targets:
            return []
        own_tops = {self.top(sid) for sid in self.source_sections(source_id)} if source_id else set()
        found = []
        for t in targets:
            if t["form"] == "付属":
                cands = [sid for sid in self.order if not self.sections[sid].get("number")
                         and (annex_key(self.sections[sid].get("title")) or annex_key((self.sections[sid].get("path") or [""])[0])) == t["label"]
                         and self.sections[sid].get("parent") not in self.sections]
            else:
                cands = [sid for sid in self.order if (self.sections[sid].get("form"), self.sections[sid].get("number")) == (t["form"], t["number"])]
                if not cands and t["form"] == "項" and t["number"] and t["number"].count("-") == 2:
                    cands = [sid for sid in self.order if (self.sections[sid].get("form"), self.sections[sid].get("number")) == ("節", t["number"].rsplit("-", 1)[0])]
            if not cands:
                return []
            main = [sid for sid in cands if self.sections.get(self.top(sid), {}).get("number")]
            same = [sid for sid in cands if self.top(sid) in own_tops]
            pick = (same or main or cands)[0]
            if pick not in found:
                found.append(pick)
        return found


def _strip_heading(text: str, title: str) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    body = re.sub(r"\s+", " ", " ".join(lines)).strip()
    t = re.sub(r"\s+", " ", title or "").strip()
    return body[len(t):].strip() if t and body.startswith(t) else body


def _chunk_path(row: dict) -> List[str]:
    """チャンクの見出しの経路（section を「 > 」で切り、先頭の文書名を落とす）"""
    parts = [p.strip() for p in (row.get("section") or "").split(" > ") if p.strip()]
    return parts[1:] if len(parts) > 1 else []


def load_outlines(graph, by_doc: Dict[str, List[dict]]) -> Dict[str, _Outline]:
    """文書ごとの節の木。章・節の層（Section, method = 'rule'）があれば使い、無い文書はチャンクの見出しの経路から作る"""
    sections: Dict[str, List[dict]] = {}
    members: Dict[str, List[tuple]] = {}
    try:
        for row in graph.query(
                "MATCH (s:Section) WHERE s.method = 'rule' OPTIONAL MATCH (p:Section)-[:HAS_SECTION]->(s) "
                "RETURN s.id AS id, s.fileName AS fileName, s.path AS path, s.title AS title, s.level AS level, s.first_position AS first_position, "
                "s.offset AS offset, s.summary AS summary, s.text AS text, coalesce(s.control, false) AS control, p.id AS parent, "
                "s.form AS form, s.number AS number"):
            sections.setdefault(row["fileName"], []).append(dict(row))
        for row in graph.query("MATCH (c:Chunk)-[r:IN_SECTION]->(s:Section) WHERE s.method = 'rule' "
                               "RETURN c.id AS chunk, s.id AS section, coalesce(r.primary, false) AS primary"):
            members.setdefault(row["section"], []).append((row["chunk"], bool(row["primary"])))
    except Exception as exc:
        logging.warning("chunk references: sections could not be read (%s); using the heading paths of the chunks", exc)
        sections, members = {}, {}
    outlines = {}
    for name, chunks in by_doc.items():
        positions = {c["id"]: c["position"] for c in chunks}
        if sections.get(name):
            outlines[name] = _Outline(sections[name], members, positions)
            continue
        # 章・節の層がまだ無い文書（取り込みの直後）: チャンクの見出しの経路で節の木を作る
        made: Dict[str, dict] = {}
        own: Dict[str, List[tuple]] = {}
        for c in chunks:
            path = _chunk_path(c)
            parent = None
            for depth in range(len(path)):
                sid = "path:" + hashlib.sha1("\x1f".join([name] + path[:depth + 1]).encode("utf-8")).hexdigest()[:16]
                if sid not in made:
                    form, number = heading_form(path[depth])
                    made[sid] = {"id": sid, "fileName": name, "path": path[:depth + 1], "title": path[depth], "level": depth + 1,
                                 "form": form, "number": number,
                                 "first_position": c["position"], "offset": 0, "summary": None, "text": None, "control": False, "parent": parent}
                parent = sid
            if parent:
                own.setdefault(parent, []).append((c["id"], True))
                if not made[parent]["text"]:
                    made[parent]["text"] = _body(c["text"])[:600]
        if made:
            outlines[name] = _Outline(list(made.values()), own, positions)
    return outlines


def _describe(outline: _Outline, sid: str, texts: Dict[str, str]) -> str:
    sec = outline.sections[sid]
    summary = (sec.get("summary") or "").strip()
    if summary:
        return "要点: " + re.sub(r"\s+", " ", summary)
    text = sec.get("text")
    if not text:
        firsts = outline.chunks_of(sid, cap=1)
        text = _body(texts.get(firsts[0], "")) if firsts else ""
    return "本文: " + _strip_heading(text, sec.get("title") or "")


def _before(quote: str, body: str, limit: int = 300) -> str:
    """参照元の本文のうち、記述の直前の部分と記述のある行の全体（「但し…による」の「但し」が何についての但し書きか、
    行の途中で切られた記述の続き「…求める。上記例では…」を LLM が読めるように）"""
    key = _norm(quote or "")[:12]
    lines = (body or "").splitlines()
    for i, line in enumerate(lines):
        if key and key in _norm(line):
            text = re.sub(r"\s+", " ", " ".join(x for x in lines[:i] if x.strip())).strip()
            text = ("…" + text[-limit:]) if len(text) > limit else text
            whole = re.sub(r"\s+", " ", line).strip()
            if _norm(whole) != _norm(quote) and len(whole) <= 400:
                text = (text + " ／ 記述のある行: " if text else "記述のある行: ") + whole
            return text or "（なし）"
    return "（なし）"


def _section_targets(outline: _Outline, sids: List[str], exclude: str, loose: bool = False, by: Optional[str] = None):
    """選んだ節から結び先のチャンクを集める。節の中身が参照元のチャンクだけの節（参照元の中を指す参照）は落とす。
    戻り値: (結び先のチャンク [{"id", "position"}], 節 [{"id", "path"(, "by")}], チャンク -> [節])"""
    chosen, by_chunk = [], {}
    for sid in sids:
        found = outline.chunks_of(sid, exclude=exclude, cap=SECTION_CHUNKS, loose=loose)
        if not found:
            continue
        chosen.append(dict({"id": sid, "path": outline.path(sid)}, **({"by": by} if by else {})))
        for c in found:
            by_chunk.setdefault(c, [])
            if sid not in by_chunk[c]:
                by_chunk[c].append(sid)
    targets = sorted(by_chunk, key=lambda c: outline.positions.get(c) or 0)
    return [{"id": c, "position": outline.positions.get(c)} for c in targets], chosen, by_chunk


def resolve_sections(ref: dict, source: dict, target_doc: str, outline: _Outline, texts: Dict[str, str], ask, rerank, ledger=None):
    """手順 3。参照先の文書の節の一覧から、LLM が参照先の節を選ぶ。回答が読めないときは例外を出す。
    ledger は _ledger_config() の結果（渡さなければここで読む）。
    戻り値: (結び先のチャンク [{"id", "position"}], 節 [{"id", "path"}], チャンク -> [節])"""
    same = target_doc == source["fileName"]
    numbered = outline.by_locator(ref.get("locator") or "", source["id"] if same else None)
    if numbered:
        # 位置の語の番号で節が決まる（章・節の層の番号の規則と同じ）。LLM は呼ばない。参照元が主所属の節でも、
        # 節に掛かる他のチャンク（見出しの行が前のチャンクの末尾にある）があれば結ぶ（章・節の層の判定と揃える）
        return _section_targets(outline, numbered, source["id"], loose=True, by="number")
    quote_loc = unicodedata.normalize("NFKC", " ".join(x for x in (ref.get("quote"), ref.get("locator")) if x))
    # 改訂の来歴・協議の記録などの管理の節は、記述がそれを名指ししていなければ一覧に出さない
    ids = [sid for sid in outline.order
           if not outline.sections[sid].get("control") or _squash(outline.sections[sid].get("title") or "")[:4] in _squash(quote_loc)]
    # 見出しが記述と同じ文言の節（「1. 試運転方法及び性能換算方法……TS-A105による」が別の文書にも書かれている）は、同じ参照の繰り返しで中身ではない
    # 題名が参照先の文書自身の番号を名指ししている節（「1. 試運転方法……TS-A105による」が TS-A105 の中にある）は、
    # 本文の別の箇所を指す案内の行で中身ではない
    R, cfg = ledger or _ledger_config()
    own = R.number_of_file(target_doc, cfg)[0] if (R and cfg) else None
    if own:
        ids = [sid for sid in ids if own not in R.document_numbers_in(outline.sections[sid].get("title") or "", cfg)]
    qkey = _squash(ref.get("quote") or "")
    ids = [sid for sid in ids if not (len(qkey) >= 8 and len(_squash(outline.sections[sid].get("title") or "")) >= 8
                                      and SequenceMatcher(None, qkey, _squash(outline.sections[sid].get("title") or ""), autojunk=False).ratio() >= 0.8)]
    if not ids:
        return [], [], {}
    mark = outline.source_sections(source["id"]) if same else set()
    if len(ids) > OUTLINE_MAX:
        # 節が多い文書: リランカーの上位、位置の語を経路に含む節、参照元の節と前後、それらの祖先だけを見せる
        descs = [outline.path(sid) + " " + _describe(outline, sid, texts)[:200] for sid in ids]
        keep = set(ids[i] for i in _rank(_query_of(ref), descs, OUTLINE_KEEP, rerank))
        keys = _locator_keys(ref.get("locator"))
        keep.update(sid for sid in ids if keys and any(k in _norm(outline.path(sid)) for k in keys))
        for sid in [s for s in ids if s in mark]:
            i = ids.index(sid)
            keep.update(ids[max(0, i - 2):i + 3])
        for sid in list(keep):
            parent = outline.sections[sid].get("parent")
            while parent and parent in outline.sections and parent not in keep:
                keep.add(parent)
                parent = outline.sections[parent].get("parent")
        ids = [sid for sid in ids if sid in keep]
    paths = [outline.path(sid) for sid in ids]
    limit = max(60, (LISTING_CHARS - sum(len(p) + 8 for p in paths)) // max(len(ids), 1))
    lines = []
    for i, (sid, path) in enumerate(zip(ids, paths)):
        desc = _describe(outline, sid, texts)
        desc = desc if len(desc) <= limit else desc[:limit] + "…"
        lines.append(f"[{i + 1}] {path}{' ★' if sid in mark else ''} ｜ {desc}")
    source_path = re.sub(r"^\[|\]$", "", (source.get("text") or "").split("\n", 1)[0]) if (source.get("text") or "").startswith("[") else (source.get("section") or "")
    prompt = OUTLINE_PROMPT.format(source_doc=source["fileName"], quote=ref["quote"], target_doc=target_doc,
                                   source_path=source_path or "（不明）", before=_before(ref["quote"], _body(source.get("text") or "")),
                                   locator=ref.get("locator") or "指定なし", topic=ref.get("topic") or "指定なし",
                                   source_note=OUTLINE_SOURCE_NOTE if mark else "", listing="\n".join(lines))
    numbers = _numbers_of(ask(prompt), len(ids))
    if numbers is None:
        raise ValueError("unreadable answer for section selection")
    return _section_targets(outline, [ids[n - 1] for n in numbers[:3]], source["id"])


def _stored_external(stored: Optional[str]) -> List[str]:
    """チャンクに保存した LLM の結果のうち、手元に無い文書への参照の表記"""
    try:
        refs = json.loads(stored)["refs"] if stored else []
    except (TypeError, ValueError, KeyError):
        return []
    out = []
    for ref in refs:
        name = ref.get("external_name") or ref.get("target_document")
        if ref.get("scope") == "external" and name and name not in out:
            out.append(name)
    return out


def link_chunk_references(graph, file_names: Optional[List[str]] = None, ask=None, rerank=None,
                          write: bool = True, reextract: bool = False, reresolve: bool = False, mode: Optional[str] = None) -> Dict:
    """参照を抽出して REFERS_TO を張る。

    mode（既定は環境変数 CHUNK_REFERENCE_MODE、無ければ llm）:
      llm     LLM で張る。規則のリンクが残っていれば消す
      rule    規則だけ。LLM を 1 回も呼ばない。LLM が以前に書いた結果には触れない（規則と同じ先へのリンクだけ消す）
    規則の分は、毎回すべてのチャンクで計算し直して書き直す（速く、結果が入力だけで決まるので、古いリンクが残らない）。
    書き直しは、今回の分を先に書いてから古い分を消す順で行う（途中で切れても、規則のリンクが全部消えたままにならない）。
    LLM の分は、前回の結果がチャンクに保存されていれば使い回す。引き直すのは、新しいチャンク、結び先の選び方の印が変わったチャンク、
    結び先のチャンクが無くなった参照、手元に無い文書への参照（文書の一覧が変わったとき）、前回失敗した参照だけである。
    file_names を渡すと、LLM の分は、その文書のチャンクと、引き直しが必要なチャンクだけを対象にする。
    reextract は抽出から、reresolve は文書とチャンクの特定からやり直す。
    戻り値は、参照と結び先、失敗の一覧（確認用）。
    """
    mode = _mode(mode)
    screened = {"not_reference": 0, "in_chunk": 0, "external_by_ledger": 0, "document_by_ledger": 0, "outline": 0, "by_number": 0}
    state = {"llm": 0, "streak": 0, "ask": ask, "rerank": rerank}

    def counted(prompt: str) -> str:
        if state["ask"] is None:
            state["ask"] = _default_ask()  # LLM への接続は、最初に必要になったときに作る（rule では作らない）
        state["llm"] += 1
        try:
            answer = state["ask"](prompt)
        except Exception as exc:
            state["streak"] += 1
            if state["streak"] >= ABORT_AFTER:
                raise LinkAborted(f"the LLM failed {state['streak']} times in a row: {exc}") from exc
            raise
        state["streak"] = 0
        return answer

    def ranked(query: str, texts: List[str]) -> List[float]:
        if state["rerank"] is None:
            from src.rerank_retriever import _rerank
            state["rerank"] = _rerank
        return state["rerank"](query, texts)

    if write:
        graph.query("CREATE INDEX chunk_id_lookup IF NOT EXISTS FOR (c:Chunk) ON (c.id)")
    rows = graph.query(
        "MATCH (c:Chunk)-[:PART_OF]->(d:Document) "
        "OPTIONAL MATCH (c)-[r:REFERS_TO]->(t) WHERE r.method IN [$method, $rule] AND NOT t:Section "
        "WITH c, d, sum(CASE WHEN r.method = $method THEN 1 ELSE 0 END) AS links, sum(CASE WHEN r.method = $rule THEN 1 ELSE 0 END) AS rule_links "
        "RETURN c.id AS id, c.text AS text, c.position AS position, d.fileName AS fileName, "
        "c.page_number AS page_number, c.section AS section, c.external_references AS external, "
        "c.reference_extraction AS stored, coalesce(c.reference_unresolved, false) AS unresolved, links, rule_links "
        "ORDER BY fileName, position", params={"method": METHOD, "rule": RULE_METHOD})
    by_doc: Dict[str, List[dict]] = {}
    for row in rows:
        by_doc.setdefault(row["fileName"], []).append(row)
    old_rule_links = sum((row.get("rule_links") or 0) for row in rows)
    old_llm_links = sum((row.get("links") or 0) for row in rows)

    result = {"mode": mode, "chunks": 0, "reused": 0, "skipped": 0, "failed": [], "skipped_documents": [], "references": [], "dropped": [],
              "aborted": None, "rule": None}
    rule_removed, rule_error = 0, None
    if mode == "rule":
        _link_by_rules(graph, rows, write, old_rule_links, old_llm_links, result)
        rule_error = result.pop("rule_error", None)
        result["chunks"] = len(rows)  # 規則は毎回すべてのチャンクを見る
    else:
        if write and old_rule_links:
            # 章・節の層（section_layer）が張る節への REFERS_TO（method = 'rule'）は、こちらの規則のリンクではないので消さない
            graph.query("MATCH (:Chunk)-[r:REFERS_TO]->(t) WHERE r.method = $method AND NOT t:Section DELETE r", params={"method": RULE_METHOD})
            rule_removed = old_rule_links
        _link_by_llm(graph, by_doc, file_names, counted, ranked, write, reextract, reresolve, screened, result)

    result["llm_calls"] = state["llm"]
    result["summary"] = {
        "mode": mode,
        "chunks": result["chunks"], "reused": result["reused"], "unchanged": result["skipped"],
        "references": len(result["references"]),
        "links": sum(len(r["targets"]) for r in result["references"]),
        "external": sum(1 for r in result["references"] if r["scope"] == "external"),
        "dropped": len(result["dropped"]), "failed_chunks": len(result["failed"]),
        "skipped_documents": result["skipped_documents"], "llm_calls": state["llm"], "aborted": result["aborted"],
        # 結び先の選び方の印と、この回に判定・特定した参照の数（使い回した参照は数えない）
        "link_style": {"options": LINK_KEY, **screened},
    }
    if result["rule"] is not None:
        result["summary"]["rule"] = {k: v for k, v in result["rule"].items() if k != "dropped"}
    if rule_removed:
        result["summary"]["rule_links_removed"] = rule_removed
    if rule_error:
        result["summary"]["rule_error"] = rule_error
    logging.info("chunk references: %s", json.dumps(result["summary"], ensure_ascii=False))
    return result


def _link_by_rules(graph, rows: List[dict], write: bool, old_rule_links: int, old_llm_links: int, result: Dict) -> None:
    """規則の分（mode = rule）。規則の側の不具合は、後処理の全体を止めずに aborted と rule_error に残す"""
    try:
        rule = _rule_pass(rows)
    except Exception as exc:
        logging.exception("chunk references: the rule pass failed: %s", exc)
        result["rule_error"] = str(exc)[:200]
        result["aborted"] = f"the rule pass failed: {result['rule_error']}"
        return
    records = rule["records"]
    result["references"] += records
    rule_edges: set = set()
    rule_external: Dict[str, List[str]] = {}
    for r in records:
        for t in r["targets"]:
            rule_edges.add((r["source"], t["id"]))
        if r["level"] == "document":
            rule_edges.add((r["source"], r["target_doc"]))
        if r["scope"] == "external" and r["target_document"] and r["target_document"] not in rule_external.setdefault(r["source"], []):
            rule_external[r["source"]].append(r["target_document"])
    if write:
        _write_rules(graph, records, rows, rule_external, old_rule_links, old_llm_links)
    result["rule"] = {
        "references": len(records), "links": sum(len(r["targets"]) for r in records), "document_links": sum(1 for r in records if r["level"] == "document"),
        "edges": len(rule_edges),  # 書くリレーションの数（同じ参照元から同じ先へは 1 本にまとめる）
        "in_chunk": sum(1 for r in records if r["level"] == "in_chunk"), "external": sum(1 for r in records if r["scope"] == "external"),
        "unresolved": sum(1 for r in records if r["reason"]), "from_history": sum(1 for r in records if r["history"]), "dropped": rule["dropped"]}
    logging.info("chunk references: rules done (%s)", json.dumps(result["rule"], ensure_ascii=False))


def _link_by_llm(graph, by_doc: Dict[str, List[dict]], file_names: Optional[List[str]], counted, ranked, write: bool,
                 reextract: bool, reresolve: bool, screened: Dict[str, int], result: Dict) -> None:
    """LLM の分（mode = llm）。文書ごとに抽出・判定・特定をして書き込む。LLM に続けてつながらない場合は aborted に残して止める"""
    chunk_ids = {row["id"] for chunks in by_doc.values() for row in chunks}
    documents = [{"fileName": name, "head": _head(chunks[0]["text"])} for name, chunks in by_doc.items()]
    docs_hash = hashlib.sha1("\n".join(sorted(by_doc)).encode("utf-8")).hexdigest()[:16]
    texts_by_id = {row["id"]: row["text"] or "" for chunks in by_doc.values() for row in chunks}
    outlines: Dict[str, _Outline] = load_outlines(graph, by_doc)
    R, ledger_cfg = ledger = _ledger_config()
    file_numbers = _file_numbers(documents, R, ledger_cfg)
    corpora: Dict[str, _Corpus] = {}

    def corpus_of(name: str) -> _Corpus:
        if name not in corpora:
            corpora[name] = _Corpus([c["text"] or "" for c in by_doc[name]])
        return corpora[name]

    def load(source: dict) -> Optional[dict]:
        if reextract or not source.get("stored"):
            return None
        try:
            stored = json.loads(source["stored"])
        except (TypeError, ValueError):
            return None
        if stored.get("v") != EXTRACT_VERSION or stored.get("mode", METHOD) != METHOD:
            return None  # 版の違う保存と、llm 以外の張り方で保存したものは使わない
        return stored

    def needs_work(source: dict, stored: Optional[dict]) -> bool:
        """保存した結果をそのまま使えないチャンクか"""
        if stored is None or reresolve or source["unresolved"]:
            return True
        if stored.get("opts") != LINK_KEY:
            return True  # 結び先の選び方の印が変わった。保存した抽出の結果で、判定と特定をやり直す
        if stored.get("links", 0) > (source.get("links") or 0):
            return True  # 結んだ先が消えた（文書の削除、書き込みの中断）
        return stored.get("docs") != docs_hash and any(r.get("scope") in ("external", None) for r in stored["refs"])

    resolved_names: Dict[tuple, object] = {}
    try:
        for doc_name, chunks in by_doc.items():
            loaded = [(c, load(c)) for c in chunks]
            in_scope = not file_names or doc_name in file_names
            sources = [(c, stored) for c, stored in loaded if needs_work(c, stored) and (in_scope or stored is not None)]
            result["skipped"] += len(chunks) - len(sources)
            if not sources:
                continue
            records, done, partial, failed = [], [], [], []
            for source, stored in sources:
                result["chunks"] += 1
                try:
                    if stored is not None:
                        found = stored["refs"]
                        result["reused"] += 1
                    else:
                        extraction = extract_references(doc_name, _body(source["text"]), counted)
                        if extraction is None:
                            raise ValueError("no JSON with references in the answer")
                        found = extraction["references"]
                        result["dropped"] += [dict(d, source_doc=doc_name, source_position=source["position"]) for d in extraction["dropped"]]
                except LinkAborted:
                    raise
                except Exception as exc:
                    logging.warning("chunk references: extraction failed on %s#%s: %s", doc_name, source["position"], exc)
                    failed.append({"source_doc": doc_name, "source_position": source["position"], "stage": "extract", "error": str(exc)[:200]})
                    continue
                refs, errors = [], 0
                stale = stored is not None and stored.get("opts") != LINK_KEY
                body = _body(source["text"])
                for ref in found:
                    record = {"source": source["id"], "source_doc": doc_name, "source_position": source["position"],
                              "quote": ref["quote"], "target_document": ref.get("target_document"), "locator": ref.get("locator"),
                              "topic": ref.get("topic") or "", "target_doc": None, "targets": [], "scope": None, "reused": False,
                              "method": METHOD, "sections": [], "target_sections": {}}
                    kept = None
                    if stored is not None and not reresolve and not stale:
                        scope, targets = ref.get("scope"), ref.get("targets") or []
                        if scope in ("same_document", "other_document") and ref.get("target_doc") in by_doc and (
                                (targets and all(t in chunk_ids for t in targets)) or (not targets and stored.get("docs") == docs_hash)):
                            kept = (scope, ref["target_doc"], targets)
                        elif scope == "external" and stored.get("docs") == docs_hash:
                            kept = ("external", None, [])
                        elif scope in ("not_reference", "in_chunk"):
                            kept = (scope, None, [])
                    try:
                        screen = None
                        if kept is None:
                            # (b)・(d)・(c)
                            if not is_reference_like(ref, body, R, ledger_cfg):
                                screen = ("not_reference", None)
                            elif in_source_chunk(ref, body):
                                screen = ("in_chunk", None)
                            else:
                                screen = ledger_document(ref, doc_name, documents, R, ledger_cfg, file_numbers)
                        if kept is not None:
                            record["scope"], record["target_doc"], record["reused"] = kept[0], kept[1], True
                            positions = {c["id"]: c["position"] for c in by_doc.get(kept[1], [])} if kept[1] else {}
                            record["targets"] = [{"id": t, "position": positions.get(t)} for t in kept[2]]
                            record["sections"] = [{"id": s[0], "path": s[1]} for s in ref.get("sections") or []]
                            record["external_name"] = ref.get("external_name")
                            record["target_sections"] = {t: list(v) for t, v in (ref.get("target_sections") or {}).items() if t in kept[2]}
                        elif screen is not None and screen[0] in ("not_reference", "in_chunk"):
                            record["scope"] = screen[0]
                            screened[screen[0]] += 1
                            result["dropped"].append({"quote": record["quote"], "source_doc": doc_name, "source_position": source["position"],
                                                      "reason": "参照の言い回しも位置の語も無い" if screen[0] == "not_reference" else "参照元のチャンクの中の箇所"})
                        elif screen is not None and screen[0] == "external":
                            record["scope"] = "external"
                            record["external_name"] = screen[1]  # 手元に無い文書の表記（保存する抽出の結果の target_document は書き換えない）
                            screened["external_by_ledger"] += 1
                        else:
                            name = record["target_document"]
                            if screen is not None:
                                target = SELF if screen[0] == "self" else screen[1]  # 文書番号の規則で手元の文書に決まった
                                screened["document_by_ledger"] += 1
                            elif name is None:
                                target = SELF
                            else:
                                key = (source["id"], _norm(name))
                                if key not in resolved_names:
                                    resolved_names[key] = resolve_document(name, ref, doc_name, documents, counted, ranked)
                                target = resolved_names[key]
                            if target is None:
                                record["scope"] = "external"
                            else:
                                record["target_doc"] = doc_name if target == SELF else target
                                record["scope"] = "same_document" if record["target_doc"] == doc_name else "other_document"
                                outline = outlines.get(record["target_doc"])
                                if outline is not None:
                                    picked, record["sections"], record["target_sections"] = resolve_sections(
                                        ref, source, record["target_doc"], outline, texts_by_id, counted, ranked, ledger)
                                    screened["by_number" if any(x.get("by") == "number" for x in record["sections"]) else "outline"] += 1
                                    record["targets"] = picked
                                else:
                                    picked = resolve_chunks(ref, source, record["target_doc"], by_doc[record["target_doc"]], counted, ranked,
                                                            corpus_of(record["target_doc"]))
                                    record["targets"] = [{"id": c["id"], "position": c["position"]} for c in picked]
                    except LinkAborted:
                        raise
                    except Exception as exc:
                        logging.warning("chunk references: resolution failed on %s#%s: %s", doc_name, source["position"], exc)
                        record["scope"], record["error"] = None, str(exc)[:200]
                        errors += 1
                    records.append(record)
                    refs.append({"quote": record["quote"], "target_document": record["target_document"], "locator": record["locator"],
                                 "topic": record["topic"], "scope": record["scope"], "target_doc": record["target_doc"],
                                 "targets": [t["id"] for t in record["targets"]],
                                 **({"external_name": record["external_name"]} if record.get("external_name") else {}),
                                 **({"sections": [[s["id"], s["path"]] for s in record["sections"]], "target_sections": record["target_sections"]}
                                    if record["sections"] else {})})
                entry = {"id": source["id"], "refs": refs}
                if errors:
                    # 特定に失敗した参照がある。既存のリンクには触れず、抽出の結果だけを残して次回に回す
                    failed.append({"source_doc": doc_name, "source_position": source["position"], "stage": "resolve", "error": f"{errors} references"})
                    partial.append(entry)
                else:
                    done.append(entry)

            result["failed"] += failed
            result["references"] += records
            if failed and len(failed) > MAX_FAILURE_RATE * len(sources):
                # LLM が止まっているときに、既存のリンクを消してしまわないための歯止め
                logging.error("chunk references: %d of %d chunks failed in %s; nothing is written for this document", len(failed), len(sources), doc_name)
                result["skipped_documents"].append(doc_name)
                continue
            if write:
                _write(graph, records, done, partial, docs_hash)
            logging.info("chunk references: %s done (%d chunks, %d references, %d failed)", doc_name, len(sources), len(records), len(failed))
    except LinkAborted as exc:
        logging.error("chunk references aborted: %s", exc)
        result["aborted"] = str(exc)


# 参照先の照合の形（結び先がチャンクか文書か）
_CHUNK_TARGET = "(t:Chunk {id: row.target})"
_DOCUMENT_TARGET = "(t:Document {fileName: row.target})"


def _write_rules(graph, records: List[dict], rows: List[dict], rule_external: Dict[str, List[str]], old_rule_links: int, old_llm_links: int) -> None:
    """規則のリンク（method = 'rule'）を、今回の結果に置き換える。規則の結果は入力だけで決まるので、やり直しても同じになる。

    消すのは自分が書いたもの（method = 'rule'）だけで、method の無い REFERS_TO（別の仕組みが張ったもの）には触れない。
    LLM のリンクのうち、規則と同じ参照元から同じ先へ張ってあるものは消す（重ねて張らない）。
    改正履歴の中からの参照は source_section = 'revision_history' と書き、検索で扱いを分けられるようにする。
    """
    to_chunks, to_docs, seen = [], [], set()
    for r in records:
        base = {"source": r["source"], "quote": r["quote"], "locator": r["locator"] or "", "scope": r["scope"], "form": r["form"],
                "section": "revision_history" if r["history"] else "body"}
        targets = [(t["id"], to_chunks) for t in r["targets"]] + ([(r["target_doc"], to_docs)] if r["level"] == "document" else [])
        for target, bucket in targets:
            if (r["source"], target) not in seen:  # 同じ参照元から同じ先へは 1 本だけ
                seen.add((r["source"], target))
                bucket.append(dict(base, target=target))
    # 先に今回の分を書き（同じリンクがあれば今回の印を付け直す）、書き終えてから、今回の印の無い古いリンクを消す。
    # 途中で切れても、古い規則のリンクが残る（全部消えたままにならない）。残った古いリンクは、次の実行で消える
    run = uuid.uuid4().hex[:12]
    buckets = ((to_chunks, _CHUNK_TARGET), (to_docs, _DOCUMENT_TARGET))
    if old_llm_links:
        for bucket, target in buckets:
            for batch in batches(bucket):
                graph.query("UNWIND $rows AS row MATCH (s:Chunk {id: row.source})-[r:REFERS_TO]->" + target + " WHERE r.method = $method DELETE r",
                            params={"rows": batch, "method": METHOD})
    for bucket, target in buckets:
        for batch in batches(bucket):
            graph.query(
                "UNWIND $rows AS row MATCH (s:Chunk {id: row.source}), " + target + " "
                "MERGE (s)-[r:REFERS_TO {method: $method, quote: row.quote, locator: row.locator}]->(t) "
                "SET r.scope = row.scope, r.form = row.form, r.source_section = row.section, r.run = $run",
                params={"rows": batch, "method": RULE_METHOD, "run": run})
    if old_rule_links:
        graph.query("MATCH (:Chunk)-[r:REFERS_TO]->(t) WHERE r.method = $method AND NOT t:Section AND coalesce(r.run, '') <> $run DELETE r",
                    params={"method": RULE_METHOD, "run": run})
    # 手元に無い文書への参照の表記。規則の分と、LLM が以前に保存した分を合わせる。値が変わるチャンクだけ書く
    changed = []
    for row in rows:
        external = list(rule_external.get(row["id"], []))
        external += [name for name in _stored_external(row.get("stored")) if name not in external]
        if external != list(row.get("external") or []):
            changed.append({"id": row["id"], "external": external})
    for batch in batches(changed):
        graph.query("UNWIND $rows AS row MATCH (c:Chunk {id: row.id}) "
                    "SET c.external_references = CASE WHEN size(row.external) > 0 THEN row.external ELSE null END", params={"rows": batch})


def _write(graph, references: List[dict], done: List[dict], partial: List[dict], docs_hash: str) -> None:
    """特定まで終えたチャンク（done）について、自分が書いた REFERS_TO（method = 'llm'）を消して書き直す（やり直しても増えない）。

    特定に失敗した参照のあるチャンク（partial）は、抽出の結果だけを保存し、既存のリンクには触れない。
    途中で切れても次回に拾えるよう、消す前に「未解決」の印を付け、書き終えてから本来の値に直す。
    """
    by_source: Dict[str, List[dict]] = {}
    for r in references:
        by_source.setdefault(r["source"], []).append(r)
    done_ids = [d["id"] for d in done]
    to_chunks, to_docs, final = [], [], []
    def stamp(entry, links):
        return json.dumps({"v": EXTRACT_VERSION, "docs": docs_hash, "links": links, "refs": entry["refs"], "mode": METHOD, "opts": LINK_KEY},
                          ensure_ascii=False)

    for entry in done:
        records = by_source.get(entry["id"], [])
        edges = set()  # 同じ文・同じ位置・同じ結び先は 1 本にまとまる
        for r in records:
            base = {"source": r["source"], "quote": r["quote"], "locator": r["locator"] or "", "topic": r["topic"], "scope": r["scope"]}
            paths = {s["id"]: s["path"] for s in r.get("sections") or []}
            for t in r["targets"]:
                sids = (r.get("target_sections") or {}).get(t["id"]) or []
                # 節の一覧から選んだ節（章・節の層が節への参照の結び先にする）。節の木の無い文書では空
                to_chunks.append(dict(base, target=t["id"], sections=sids, paths=[paths.get(s, "") for s in sids]))
                edges.add((t["id"], base["quote"], base["locator"]))
            if r["scope"] == "other_document" and r["target_doc"] and not r["targets"]:
                to_docs.append(dict(base, target=r["target_doc"]))
                edges.add((r["target_doc"], base["quote"], base["locator"]))
        links = len(edges)
        external = []
        for r in records:
            name = r.get("external_name") or r["target_document"]
            if r["scope"] == "external" and name and name not in external:
                external.append(name)
        final.append({"id": entry["id"], "unresolved": False, "external": external, "stored": stamp(entry, links)})
    pending = [{"id": entry["id"], "unresolved": True, "stored": stamp(entry, 0)} for entry in partial]
    if pending:
        graph.query("UNWIND $rows AS row MATCH (c:Chunk {id: row.id}) "
                    "SET c.reference_extraction = row.stored, c.reference_unresolved = row.unresolved", params={"rows": pending})
    if not done_ids:
        return
    # 同じ参照元・結び先・一文・位置の語は 1 本のリレーションになる（MERGE）。選んだ節はまとめて持たせる
    merged: Dict[tuple, dict] = {}
    for row in to_chunks:
        key = (row["source"], row["target"], row["quote"], row["locator"])
        if key not in merged:
            merged[key] = dict(row, sections=list(row["sections"]), paths=list(row["paths"]))
            continue
        for sid, path in zip(row["sections"], row["paths"]):
            if sid not in merged[key]["sections"]:
                merged[key]["sections"].append(sid)
                merged[key]["paths"].append(path)
    to_chunks = list(merged.values())
    graph.query("MATCH (c:Chunk) WHERE c.id IN $ids SET c.reference_unresolved = true", params={"ids": done_ids})
    graph.query("MATCH (c:Chunk)-[r:REFERS_TO]->() WHERE c.id IN $ids AND r.method = $method DELETE r",
                params={"ids": done_ids, "method": METHOD})
    sections = (", r.target_sections = CASE WHEN size(row.sections) > 0 THEN row.sections ELSE null END, "
                "r.target_section_paths = CASE WHEN size(row.sections) > 0 THEN row.paths ELSE null END")
    for bucket, target, extra in ((to_chunks, _CHUNK_TARGET, sections), (to_docs, _DOCUMENT_TARGET, "")):
        graph.query(
            "UNWIND $rows AS row MATCH (s:Chunk {id: row.source}), " + target + " "
            "MERGE (s)-[r:REFERS_TO {method: $method, quote: row.quote, locator: row.locator}]->(t) SET r.topic = row.topic, r.scope = row.scope" + extra,
            params={"rows": bucket, "method": METHOD})
    graph.query(
        "UNWIND $rows AS row MATCH (c:Chunk {id: row.id}) "
        "SET c.reference_extraction = row.stored, c.reference_unresolved = row.unresolved, "
        "c.external_references = CASE WHEN size(row.external) > 0 THEN row.external ELSE null END",
        params={"rows": final})
