"""チャンク間の参照リンク（REFERS_TO）を作る後処理。

本文に「○○規程の 3-5 項による」「別表 1 による」「図 2 参照」「前条の定めにより」のように、
別の箇所を見るよう求める記述があるとき、その記述のあるチャンクから参照先のチャンクへリレーションを張る。
チャンク同士のリレーションは NEXT_CHUNK（並び順）と SIMILAR（類似度）しか無く、参照はどちらにも現れない。

張り方は 3 つから選ぶ（環境変数 CHUNK_REFERENCE_MODE）。
  hybrid  規則で結べる分は規則で結び（method = 'rule'）、規則で結べなかった分だけを LLM に回す（method = 'llm'）
  rule    規則だけ。LLM を 1 回も呼ばない
  llm     LLM だけ（下の手順 1〜4 を全チャンクに行う。従来の動き）
既定は llm である。設定を足さずにこのファイルを置き換えても、張り方は変わらない。hybrid・rule へは、設定を書いて切り替える
（切り替えた回は、LLM に渡す仕事が変わるチャンクを引き直し、規則と同じ先への LLM のリンクを消す）。
規則による抽出と解決は chunk_reference_rules.py にある（識別子と場所の名前の目録を作り、パターンで拾って目録で引く。
特定の文書番号や言い回しを決め打ちした規則は持たない）。hybrid で LLM に回すのは、(a) 規則が参照として検出したが参照先を
決められなかったもの、(b) 文書だけを指す参照のうち、同じ文に参照の語があり、文書の中の読むべき場所を選ぶ意味があるもの、
(c) 規則の参照が 1 つも掛かっていない文に、文書らしい語＋参照の語（または「参照」）があるチャンク、の 3 つだけである（plan_llm_work）。

LLM の手順。判断は LLM とリランカーに任せる。
  1) 抽出: チャンクごとに、参照の記述を LLM が抜き出す（原文、参照先の文書、文書内の位置、見に行く内容）
  2) 文書の特定: 参照先の表記が手元のどの文書かを決める。文書が多いときは候補を絞ってから LLM に選ばせる
  3) チャンクの特定: 参照先の文書の中から候補を絞り、LLM が参照先のチャンクを選ぶ
  4) 書き込み: 文書ごとに (:Chunk)-[:REFERS_TO {method: 'llm'}]->(:Chunk) を書く。チャンクまで絞れない場合は
     (:Chunk)-[:REFERS_TO]->(:Document)。手元に無い文書への参照は Chunk.external_references に表記を残す

抽出と特定の結果はチャンクに保存し、やり直しのときは変わっていない分を使い回す（文書が増減したときだけ引き直す）。
失敗の扱い: 抽出か特定に失敗したチャンクは、既存のリンクを消さずに残し、次回の対象にする。失敗の件数は戻り値とログに出す。
REFERS_TO という型は他の仕組み（別の参照グラフなど）も使うので、消すのは自分が書いたもの（method = 'llm' と 'rule'）だけにする。
method の無い REFERS_TO には触れない。規則と LLM が同じ参照元から同じ先を指したときは、規則のリンクだけを残す。
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

from src.chunk_reference_rules import OPTIONAL_RULES, RULE_VERSION, find_references

# プロンプトや保存の形を変えたら上げる。チャンクに保存した結果は、版が違えば使わない
EXTRACT_VERSION = "4"
METHOD = "llm"
RULE_METHOD = "rule"
MODES = ("hybrid", "rule", "llm")
DEFAULT_MODE = "llm"  # 設定が無いときの張り方。従来の動き（LLM だけ）を保つ
SELF = "__self__"

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


class LinkAborted(RuntimeError):
    """LLM に続けてつながらないなど、これ以上進めても無駄な状態。ここまでの書き込みは残る"""


def _setting(name: str, default, cast=str):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _norm(text: str) -> str:
    """照合用の正規化。全角半角を揃え、空白と Markdown の飾りを除く"""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"<br\s*/?>", "", text, flags=re.I)
    return re.sub(r"[\s*_|`\\]", "", text)


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
    timeout = _setting("CHUNK_REFERENCE_LLM_TIMEOUT", 300.0, float)

    def ask(prompt: str) -> str:
        try:
            content = llm.invoke(prompt, timeout=timeout).content
        except TypeError:
            # 待ち時間の指定を受け付けないモデルの場合
            content = llm.invoke(prompt).content
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

    return ask


def _default_rerank() -> Callable[[str, List[str]], List[float]]:
    from src.rerank_retriever import _rerank
    return _rerank


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
    prefilter = _setting("CHUNK_REFERENCE_PREFILTER", 80, int)
    corpus = corpus or _Corpus(texts)
    if len(indices) > prefilter:
        indices = corpus.top(query, prefilter, must)
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
    a = re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", name or "")).lower()
    b = re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", os.path.splitext(file_name or "")[0])).lower()
    if not a or not b:
        return 0, 0.0, 0
    size = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b)).size
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
    overlap = _setting("CHUNK_REFERENCE_QUOTE_OVERLAP", 0.5, float)
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
            longest = SequenceMatcher(None, key, body, autojunk=False).find_longest_match(0, len(key), 0, len(body)).size
            if longest < max(8, int(len(key) * overlap)):
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
    keep = _setting("CHUNK_REFERENCE_DOCUMENT_SHORTLIST", 12, int)
    if len(others) <= keep:
        # 一覧の並びを表記ごとに変える（番号と文書の並びがたまたま一致して、誤りを見分けられなくなるのを防ぐ）
        return sorted(others, key=lambda d: hashlib.md5((name + d["fileName"]).encode("utf-8")).hexdigest())
    # 字面の近い文書を先に入れる（番号が 1 字違いの文書を取り違えないため）。共通部分の長い順、長さの差の小さい順に並べ、
    # 枠は半分までにする。残りは必ずリランカーの順で埋める
    matches = []
    for index, document in enumerate(others):
        size, ratio, gap = _name_match(name, document["fileName"])
        if size >= 3 and ratio >= _setting("CHUNK_REFERENCE_NAME_MATCH", 0.8, float):
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


def resolve_chunks(ref: dict, source: dict, target_doc: str, chunks: List[dict], ask, rerank, corpus: Optional[_Corpus] = None) -> List[dict]:
    """手順 3。参照先の文書のチャンクから、参照先を選ぶ。回答が読めないときは例外を出す。

    同じ文書の中の参照では、参照元の前後のチャンクを一定の分量だけ必ず候補に入れ、参照元の位置を一覧に示す
    （「前条」「上記 2 項」のような相対的な参照を解けるようにする）。
    """
    same = target_doc == source["fileName"]
    candidates = [c for c in chunks if c["id"] != source["id"]]
    if not candidates:
        return []
    keep = _setting("CHUNK_REFERENCE_SHORTLIST", 8, int)
    texts = [c["text"] or "" for c in candidates]
    query = " ".join(x for x in (ref.get("locator"), ref.get("topic"), ref["quote"]) if x)
    if corpus is not None and same:
        corpus = None  # 参照元を除いたので、文書全体の索引とは並びが合わない
    picked = set(_rank(query, texts, keep, rerank, corpus, ref.get("locator")))
    # 位置の語、または「3-5」「2.3.1」のように複数の段を持つ番号をそのまま含むチャンクは、リランカーの順位に関係なく候補に入れる
    # （見出しがチャンクの途中に埋もれていると、リランカーの上位に来ないことがある）
    locator = unicodedata.normalize("NFKC", ref.get("locator") or "")
    keys = [key for key in [_norm(locator)] + re.findall(r"\d+(?:[-.]\d+)+", locator) if len(key) >= 2]
    if keys:
        forced = [i for i, text in enumerate(texts) if i not in picked and any(key in _norm(text) for key in keys)]
        picked.update(forced[:_setting("CHUNK_REFERENCE_FORCED", 4, int)])
    if same:
        # 参照元の前後を、字数で決めた分量だけ入れる（チャンクが小さいと、個数では前の条まで届かない）
        window = _setting("CHUNK_REFERENCE_NEIGHBOR_CHARS", 2500, int)
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
    budget = _setting("CHUNK_REFERENCE_LISTING_CHARS", 9000, int)
    limit = max(300, budget // max(len(shortlist), 1))
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
    """参照の張り方。llm = LLM だけ（従来の動き。既定）。hybrid = 規則が先、残りだけ LLM。rule = 規則だけ（LLM を呼ばない）"""
    mode = (value or os.environ.get("CHUNK_REFERENCE_MODE") or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        logging.warning("chunk references: unknown CHUNK_REFERENCE_MODE '%s'; using '%s'", mode, DEFAULT_MODE)
        return DEFAULT_MODE
    return mode


def _flag(name: str, default: bool) -> bool:
    return _setting(name, default, lambda v: v.strip().lower() in ("1", "true", "yes", "on"))


def _rule_pass(rows: List[dict]) -> Dict:
    """規則による抽出と解決（LLM もグラフも使わない）。記録の形を、LLM の記録にそろえて返す"""
    disable = [x.strip() for x in (os.environ.get("CHUNK_REFERENCE_RULE_DISABLE") or "").split(",") if x.strip() in OPTIONAL_RULES]
    found = find_references([{"id": r["id"], "fileName": r["fileName"], "position": r["position"], "text": r["text"],
                              "page_number": r.get("page_number"), "section": r.get("section")} for r in rows], disable=disable)
    records = [{"source": r["source"], "source_doc": r["source_doc"], "source_position": r["source_position"], "quote": r["quote"],
                "target_document": r["target_document"], "locator": r["locator"] or None, "topic": "", "target_doc": r["target_doc"],
                "targets": r["targets"], "scope": r["scope"], "reused": False, "method": RULE_METHOD, "form": r["form"], "level": r["level"],
                "reason": r["reason"], "history": r["history"], "stage": r["stage"], "places": r.get("places", r["level"] == "document")}
               for r in found["references"]]
    return {"records": records, "chunks": found["chunks"], "dropped": found["dropped"], "vague": len(found["vague"])}


def plan_llm_work(records: List[dict], chunks: Dict[str, dict], document_places: bool = True) -> Dict[str, dict]:
    """hybrid で LLM に回す分を、チャンクごとに決める。回すのは次の 3 つだけである。

    (a) unresolved     規則が参照として検出したが、参照先を決められなかったもの（略称・通称・旧番号、題名の無い表 など）
    (b) document_only  文書だけを指す参照のうち、同じ文に参照の語があるもの。文書の中の読むべき場所を LLM が選ぶ
                       （CHUNK_REFERENCE_DOCUMENT_PLACES=false で止められる。発行目録の行や関連文書の一覧の識別子は回さない）
    (c) no_detection   規則の参照が 1 つも掛かっていない文に、文書らしい語＋参照の語（または「参照」）があるチャンク。抽出から LLM が行う
                       （手段や原因を表すだけの「により」は回さない。規則が検出した文の参照は、LLM の結果から除く）
    改正履歴の中の参照は回さない（LLM の抽出でも、改訂の来歴は参照に含めない決まりである）。
    """
    plan: Dict[str, dict] = {}
    for r in records:
        if r["history"]:
            continue
        why = "unresolved" if r["reason"] else ("document_only" if (r["level"] == "document" and document_places and r.get("places", True)) else None)
        if why:
            plan.setdefault(r["source"], {"full": False, "refs": []})["refs"].append(
                {"why": why, "quote": r["quote"], "target_document": r["target_document"], "locator": r["locator"], "known_doc": r["target_doc"]})
    for chunk_id, info in chunks.items():
        if info.get("llm", info["cue"] and not info["references"]):
            plan.setdefault(chunk_id, {"full": False, "refs": []})["full"] = True
    return plan


def _task_key(task: dict) -> str:
    """LLM に渡す仕事の中身の要約。前回と同じなら、保存した結果を使い回す。規則の版が変われば、仕事も変わったものとして扱う"""
    return hashlib.sha1(json.dumps([RULE_VERSION, task], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _stored_external(stored: Optional[str]) -> List[str]:
    """チャンクに保存した LLM の結果のうち、手元に無い文書への参照の表記"""
    try:
        refs = json.loads(stored)["refs"] if stored else []
    except (TypeError, ValueError, KeyError):
        return []
    out = []
    for ref in refs:
        if ref.get("scope") == "external" and ref.get("target_document") and ref["target_document"] not in out:
            out.append(ref["target_document"])
    return out


def link_chunk_references(graph, file_names: Optional[List[str]] = None, ask=None, rerank=None,
                          write: bool = True, reextract: bool = False, reresolve: bool = False, mode: Optional[str] = None) -> Dict:
    """参照を抽出して REFERS_TO を張る。

    mode（既定は環境変数 CHUNK_REFERENCE_MODE、無ければ llm）:
      llm     LLM だけ（従来の動き）。規則のリンクが残っていれば消す
      hybrid  規則で結べる分を規則で結び（method = 'rule'）、残りだけを LLM に回す（method = 'llm'）
      rule    規則だけ。LLM を 1 回も呼ばない。LLM が以前に書いた結果には触れない（規則と同じ先へのリンクだけ消す）
    規則の分は、毎回すべてのチャンクで計算し直して書き直す（速く、結果が入力だけで決まるので、古いリンクが残らない）。
    書き直しは、今回の分を先に書いてから古い分を消す順で行う（途中で切れても、規則のリンクが全部消えたままにならない）。
    LLM の分は、前回の結果がチャンクに保存されていれば使い回す。引き直すのは、新しいチャンク、LLM に渡す仕事が変わったチャンク、
    結び先のチャンクが無くなった参照、手元に無い文書への参照（文書の一覧が変わったとき）、前回失敗した参照だけである。
    file_names を渡すと、LLM の分は、その文書のチャンクと、引き直しが必要なチャンクだけを対象にする。
    reextract は抽出から、reresolve は文書とチャンクの特定からやり直す。
    戻り値は、参照と結び先、失敗の一覧（確認用）。LLM に続けてつながらない場合も、規則の分は書き終えている。
    """
    mode = _mode(mode)
    max_failure = _setting("CHUNK_REFERENCE_MAX_FAILURE_RATE", 0.5, float)
    # hybrid では LLM の結果が要るチャンクが文書に 1〜3 個しか無いことが多く、割合だけで見ると 1 回の読めない答えで文書が丸ごと飛ぶ。
    # 失敗の件数がこの数に届かないうちは、文書を飛ばさない（失敗したチャンクは既存のリンクを消さずに残し、次回に回す）
    min_failures = 1 if mode == "llm" else _setting("CHUNK_REFERENCE_MIN_FAILURES", 3, int)
    abort_after = _setting("CHUNK_REFERENCE_ABORT_AFTER", 5, int)
    document_places = _flag("CHUNK_REFERENCE_DOCUMENT_PLACES", True)
    state = {"llm": 0, "streak": 0, "ask": ask, "rerank": rerank}

    def counted(prompt: str) -> str:
        if state["ask"] is None:
            state["ask"] = _default_ask()  # LLM への接続は、最初に必要になったときに作る（rule では作らない）
        state["llm"] += 1
        try:
            answer = state["ask"](prompt)
        except Exception as exc:
            state["streak"] += 1
            if state["streak"] >= abort_after:
                raise LinkAborted(f"the LLM failed {state['streak']} times in a row: {exc}") from exc
            raise
        state["streak"] = 0
        return answer

    def ranked(query: str, texts: List[str]) -> List[float]:
        if state["rerank"] is None:
            state["rerank"] = _default_rerank()
        return state["rerank"](query, texts)

    if write:
        graph.query("CREATE INDEX chunk_id_lookup IF NOT EXISTS FOR (c:Chunk) ON (c.id)")
    rows = graph.query(
        "MATCH (c:Chunk)-[:PART_OF]->(d:Document) "
        "OPTIONAL MATCH (c)-[r:REFERS_TO]->() WHERE r.method IN [$method, $rule] "
        "WITH c, d, sum(CASE WHEN r.method = $method THEN 1 ELSE 0 END) AS links, sum(CASE WHEN r.method = $rule THEN 1 ELSE 0 END) AS rule_links "
        "RETURN c.id AS id, c.text AS text, c.position AS position, d.fileName AS fileName, "
        "c.page_number AS page_number, c.section AS section, c.external_references AS external, "
        "c.reference_extraction AS stored, coalesce(c.reference_unresolved, false) AS unresolved, links, rule_links "
        "ORDER BY fileName, position", params={"method": METHOD, "rule": RULE_METHOD})
    by_doc: Dict[str, List[dict]] = {}
    for row in rows:
        by_doc.setdefault(row["fileName"], []).append(row)
    chunk_ids = {row["id"] for row in rows}
    documents = [{"fileName": name, "head": _head(chunks[0]["text"])} for name, chunks in by_doc.items()]
    docs_hash = hashlib.sha1("\n".join(sorted(by_doc)).encode("utf-8")).hexdigest()[:16]
    corpora: Dict[str, _Corpus] = {}
    old_rule_links = sum((row.get("rule_links") or 0) for row in rows)
    old_llm_links = sum((row.get("links") or 0) for row in rows)

    result = {"mode": mode, "chunks": 0, "reused": 0, "skipped": 0, "failed": [], "skipped_documents": [], "references": [], "dropped": [],
              "documents": {}, "aborted": None, "rule": None, "llm_routed": None}

    # ── 規則の分。LLM より先に済ませる（LLM が止まっていても、規則のリンクは張られる）
    plan: Dict[str, dict] = {}
    rule_edges: set = set()
    rule_external: Dict[str, List[str]] = {}
    rule_quotes: Dict[str, List[str]] = {}
    rule_removed = 0
    rule, rule_error = None, None
    if mode != "llm":
        try:
            rule = _rule_pass(rows)
        except Exception as exc:
            # 規則の側の不具合で後処理の全体を止めない。hybrid なら、この回は LLM だけで張る。規則のリンクには触れず、
            # 前回までに保存した LLM の結果はそのまま使い回す（全チャンクの抽出をやり直さない）。張ってある規則のリンクと同じ先は重ねて張らない
            logging.exception("chunk references: the rule pass failed: %s", exc)
            rule_error = str(exc)[:200]
            if mode == "hybrid":
                mode = result["mode"] = "llm"
                if old_rule_links:
                    for row in graph.query("MATCH (s:Chunk)-[r:REFERS_TO]->(t) WHERE r.method = $method "
                                           "RETURN s.id AS source, coalesce(t.id, t.fileName) AS target", params={"method": RULE_METHOD}):
                        rule_edges.add((row["source"], row["target"]))
            else:
                result["aborted"] = f"the rule pass failed: {rule_error}"
    if rule is None:
        if rule_error is None and write and old_rule_links:
            graph.query("MATCH (:Chunk)-[r:REFERS_TO]->() WHERE r.method = $method DELETE r", params={"method": RULE_METHOD})
            rule_removed = old_rule_links
    else:
        records = rule["records"]
        result["references"] += records
        for r in records:
            rule_quotes.setdefault(r["source"], []).append(_norm(r["quote"]))
            for t in r["targets"]:
                rule_edges.add((r["source"], t["id"]))
            if r["level"] == "document":
                rule_edges.add((r["source"], r["target_doc"]))
            if r["scope"] == "external" and r["target_document"] and r["target_document"] not in rule_external.setdefault(r["source"], []):
                rule_external[r["source"]].append(r["target_document"])
        if mode == "hybrid":
            plan = plan_llm_work(records, rule["chunks"], document_places)
        if write:
            _write_rules(graph, records, rows, rule_external, old_rule_links, old_llm_links)
        why = lambda key: sum(1 for task in plan.values() if (task["full"] if key == "no_detection" else any(r["why"] == key for r in task["refs"])))
        result["rule"] = {
            "references": len(records), "links": sum(len(r["targets"]) for r in records), "document_links": sum(1 for r in records if r["level"] == "document"),
            "edges": len(rule_edges),  # 書くリレーションの数（同じ参照元から同じ先へは 1 本にまとめる）
            "in_chunk": sum(1 for r in records if r["level"] == "in_chunk"), "external": sum(1 for r in records if r["scope"] == "external"),
            "unresolved": sum(1 for r in records if r["reason"]), "from_history": sum(1 for r in records if r["history"]), "dropped": rule["dropped"]}
        result["llm_routed"] = {"chunks": len(plan), "unresolved": why("unresolved"), "document_only": why("document_only"), "no_detection": why("no_detection"), "sent": 0}
        logging.info("chunk references: rules done (%s)", json.dumps(result["rule"], ensure_ascii=False))

    def corpus_of(name: str) -> _Corpus:
        if name not in corpora:
            corpora[name] = _Corpus([c["text"] or "" for c in by_doc[name]])
        return corpora[name]

    def task_of(source: dict) -> dict:
        """このチャンクで LLM がする仕事。llm では抽出から全部、hybrid では規則の残りだけ"""
        return {"full": True, "refs": []} if mode == "llm" else plan.get(source["id"], {"full": False, "refs": []})

    def load(source: dict) -> Optional[dict]:
        if reextract or not source.get("stored"):
            return None
        try:
            stored = json.loads(source["stored"])
        except (TypeError, ValueError):
            return None
        if stored.get("v") != EXTRACT_VERSION:
            return None
        if rule_error:
            return stored  # 規則が失敗した回は、前回までの保存（hybrid のものも）をそのまま使う
        if stored.get("mode", "llm") != mode:
            return None
        if mode != "llm" and stored.get("task") != _task_key(task_of(source)):
            return None  # 規則の結果が変わり、LLM に渡す仕事が変わった
        return stored

    def needs_work(source: dict, stored: Optional[dict]) -> bool:
        """保存した結果をそのまま使えないチャンクか"""
        if stored is None or reresolve or source["unresolved"]:
            return True
        if stored.get("links", 0) > (source.get("links") or 0):
            return True  # 結んだ先が消えた（文書の削除、書き込みの中断）
        if any((source["id"], target) not in rule_edges for target in stored.get("covered") or []):
            return True  # 規則と同じ先なので張らなかったリンクがあり、規則がその先を張らなくなった。保存した結び先から張り直す（LLM は呼ばない）
        return stored.get("docs") != docs_hash and any(r.get("scope") in ("external", None) for r in stored["refs"])

    resolved_names: Dict[tuple, object] = {}
    try:
        for doc_name, chunks in (by_doc.items() if mode != "rule" else []):
            loaded = [(c, load(c)) for c in chunks]
            in_scope = not file_names or doc_name in file_names
            sources = [(c, stored) for c, stored in loaded if needs_work(c, stored) and (in_scope or stored is not None)]
            result["skipped"] += len(chunks) - len(sources)
            if not sources:
                continue
            records, done, partial, failed, worked = [], [], [], [], 0
            for source, stored in sources:
                result["chunks"] += 1
                task = task_of(source)
                if task["full"] or task["refs"] or (stored is not None and stored["refs"]):
                    worked += 1  # LLM の結果が要るチャンク（規則だけで済んだチャンクは、失敗の割合に数えない）
                try:
                    if stored is not None:
                        found = stored["refs"]
                        result["reused"] += 1
                    else:
                        found = []
                        if task["full"]:
                            extraction = extract_references(doc_name, _body(source["text"]), counted)
                            if extraction is None:
                                raise ValueError("no JSON with references in the answer")
                            # 規則が検出した文の参照は、規則の側で扱っている（結んだか、下で LLM に回す）。LLM の結果からは除く
                            taken = rule_quotes.get(source["id"], [])
                            found += [f for f in extraction["references"]
                                      if not any(q and (q in _norm(f["quote"]) or _norm(f["quote"]) in q) for q in taken)]
                            result["dropped"] += [dict(d, source_doc=doc_name, source_position=source["position"]) for d in extraction["dropped"]]
                        # 規則が検出した参照のうち、LLM に回す分。抽出は済んでいるので、文書とチャンクの特定だけを行う
                        found += [{"quote": r["quote"], "target_document": r["target_document"], "locator": r["locator"], "topic": "",
                                   "origin": RULE_METHOD, "why": r["why"], "known_doc": r["known_doc"]} for r in task["refs"]]
                        if result["llm_routed"] is not None and (task["full"] or task["refs"]):
                            result["llm_routed"]["sent"] += 1
                except LinkAborted:
                    raise
                except Exception as exc:
                    logging.warning("chunk references: extraction failed on %s#%s: %s", doc_name, source["position"], exc)
                    failed.append({"source_doc": doc_name, "source_position": source["position"], "stage": "extract", "error": str(exc)[:200]})
                    continue
                refs, errors = [], 0
                for ref in found:
                    record = {"source": source["id"], "source_doc": doc_name, "source_position": source["position"],
                              "quote": ref["quote"], "target_document": ref.get("target_document"), "locator": ref.get("locator"),
                              "topic": ref.get("topic") or "", "target_doc": None, "targets": [], "scope": None, "reused": False,
                              "method": METHOD, "origin": ref.get("origin") or METHOD, "why": ref.get("why")}
                    kept = None
                    if stored is not None and not reresolve:
                        scope, targets = ref.get("scope"), ref.get("targets") or []
                        if scope in ("same_document", "other_document") and ref.get("target_doc") in by_doc and (
                                (targets and all(t in chunk_ids for t in targets)) or (not targets and stored.get("docs") == docs_hash)):
                            kept = (scope, ref["target_doc"], targets)
                        elif scope == "external" and stored.get("docs") == docs_hash:
                            kept = ("external", None, [])
                    try:
                        if kept is not None:
                            record["scope"], record["target_doc"], record["reused"] = kept[0], kept[1], True
                            positions = {c["id"]: c["position"] for c in by_doc.get(kept[1], [])} if kept[1] else {}
                            record["targets"] = [{"id": t, "position": positions.get(t)} for t in kept[2]]
                        else:
                            name = record["target_document"]
                            known = ref.get("known_doc")
                            if known and known in by_doc:
                                target = SELF if known == doc_name else known  # 文書は規則が決めた。LLM はチャンクだけを選ぶ
                            elif name is None:
                                target = SELF
                            else:
                                key = (source["id"], _norm(name))
                                if key not in resolved_names:
                                    resolved_names[key] = resolve_document(name, ref, doc_name, documents, counted, ranked)
                                    result["documents"][f"{doc_name}#{source['position']} | {name}"] = resolved_names[key]
                                target = resolved_names[key]
                            if target is None:
                                record["scope"] = "external"
                            else:
                                record["target_doc"] = doc_name if target == SELF else target
                                record["scope"] = "same_document" if record["target_doc"] == doc_name else "other_document"
                                picked = resolve_chunks(ref, source, record["target_doc"], by_doc[record["target_doc"]], counted, ranked,
                                                        corpus_of(record["target_doc"]))
                                record["targets"] = [{"id": c["id"], "position": c["position"]} for c in picked]
                    except LinkAborted:
                        raise
                    except Exception as exc:
                        logging.warning("chunk references: resolution failed on %s#%s: %s", doc_name, source["position"], exc)
                        record["scope"], record["error"] = None, str(exc)[:200]
                        errors += 1
                    # 規則が同じ参照元から同じ先へ張っているものは、重ねて張らない
                    record["covered_by_rule"] = [t["id"] for t in record["targets"] if (source["id"], t["id"]) in rule_edges]
                    record["covered_document"] = bool(record["scope"] == "other_document" and record["target_doc"] and not record["targets"]
                                                      and (source["id"], record["target_doc"]) in rule_edges)
                    records.append(record)
                    refs.append({"quote": record["quote"], "target_document": record["target_document"], "locator": record["locator"],
                                 "topic": record["topic"], "scope": record["scope"], "target_doc": record["target_doc"],
                                 "targets": [t["id"] for t in record["targets"]],
                                 "origin": record["origin"], "why": record["why"], "known_doc": ref.get("known_doc")})
                entry = {"id": source["id"], "refs": refs, "task": _task_key(task),
                         "covered": sorted({t for r in records[-len(refs):] for t in r["covered_by_rule"] + ([r["target_doc"]] if r["covered_document"] else [])})
                         if refs else []}
                if errors:
                    # 特定に失敗した参照がある。既存のリンクには触れず、抽出の結果だけを残して次回に回す
                    failed.append({"source_doc": doc_name, "source_position": source["position"], "stage": "resolve", "error": f"{errors} references"})
                    partial.append(entry)
                else:
                    done.append(entry)

            result["failed"] += failed
            result["references"] += records
            if failed and len(failed) >= min_failures and len(failed) > max_failure * max(worked, 1):
                # LLM が止まっているときに、既存のリンクを消してしまわないための歯止め
                logging.error("chunk references: %d of %d chunks failed in %s; nothing is written for this document", len(failed), worked, doc_name)
                result["skipped_documents"].append(doc_name)
                continue
            if write:
                _write(graph, records, done, partial, docs_hash, mode, rule_edges, rule_external)
            logging.info("chunk references: %s done (%d chunks, %d references, %d failed)", doc_name, len(sources), len(records), len(failed))
    except LinkAborted as exc:
        logging.error("chunk references aborted: %s", exc)
        result["aborted"] = str(exc)

    if mode == "rule":
        result["chunks"] = len(rows)  # 規則は毎回すべてのチャンクを見る
    result["llm_calls"] = state["llm"]
    result["summary"] = {
        "mode": mode,
        "chunks": result["chunks"], "reused": result["reused"], "unchanged": result["skipped"],
        "references": len(result["references"]),
        "links": sum(len(r["targets"]) - len(r.get("covered_by_rule") or []) for r in result["references"]),
        "external": sum(1 for r in result["references"] if r["scope"] == "external"),
        "dropped": len(result["dropped"]), "failed_chunks": len(result["failed"]),
        "skipped_documents": result["skipped_documents"], "llm_calls": state["llm"], "aborted": result["aborted"],
    }
    if result["rule"] is not None:
        # 規則が張った分と、LLM に回したチャンクの数（理由ごと。1 つのチャンクが複数の理由を持つことがある）
        result["summary"]["rule"] = {k: v for k, v in result["rule"].items() if k != "dropped"}
        result["summary"]["llm_routed"] = result["llm_routed"]
    if rule_removed:
        result["summary"]["rule_links_removed"] = rule_removed
    # rule は LLM の保存を書かないので、切り替えの判定は hybrid と llm のときだけ行う
    switched = 0 if mode == "rule" else sum(1 for row in rows if row.get("stored") and f'"mode": "{mode}"' not in row["stored"]
                                            and not (mode == "llm" and '"mode"' not in row["stored"]))
    if switched and not rule_error:
        # 張り方を切り替えた回。前回は別の張り方で保存されていたチャンクの数と、切り替える前のリンクの本数を残す
        result["summary"]["mode_switch"] = {"chunks_stored_in_other_mode": switched, "llm_links_before": old_llm_links, "rule_links_before": old_rule_links}
    if rule_error:
        result["summary"]["rule_error"] = rule_error
    logging.info("chunk references: %s", json.dumps(result["summary"], ensure_ascii=False))
    return result


def _batches(rows: List[dict], size: int = 500):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


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
    if old_llm_links:
        for batch in _batches(to_chunks):
            graph.query("UNWIND $rows AS row MATCH (s:Chunk {id: row.source})-[r:REFERS_TO]->(t:Chunk {id: row.target}) WHERE r.method = $method DELETE r",
                        params={"rows": batch, "method": METHOD})
        for batch in _batches(to_docs):
            graph.query("UNWIND $rows AS row MATCH (s:Chunk {id: row.source})-[r:REFERS_TO]->(t:Document {fileName: row.target}) WHERE r.method = $method DELETE r",
                        params={"rows": batch, "method": METHOD})
    for batch in _batches(to_chunks):
        graph.query(
            "UNWIND $rows AS row MATCH (s:Chunk {id: row.source}), (t:Chunk {id: row.target}) "
            "MERGE (s)-[r:REFERS_TO {method: $method, quote: row.quote, locator: row.locator}]->(t) "
            "SET r.scope = row.scope, r.form = row.form, r.source_section = row.section, r.run = $run",
            params={"rows": batch, "method": RULE_METHOD, "run": run})
    for batch in _batches(to_docs):
        graph.query(
            "UNWIND $rows AS row MATCH (s:Chunk {id: row.source}), (t:Document {fileName: row.target}) "
            "MERGE (s)-[r:REFERS_TO {method: $method, quote: row.quote, locator: row.locator}]->(t) "
            "SET r.scope = row.scope, r.form = row.form, r.source_section = row.section, r.run = $run",
            params={"rows": batch, "method": RULE_METHOD, "run": run})
    if old_rule_links:
        graph.query("MATCH (:Chunk)-[r:REFERS_TO]->() WHERE r.method = $method AND coalesce(r.run, '') <> $run DELETE r",
                    params={"method": RULE_METHOD, "run": run})
    # 手元に無い文書への参照の表記。規則の分と、LLM が以前に保存した分を合わせる。値が変わるチャンクだけ書く
    changed = []
    for row in rows:
        external = list(rule_external.get(row["id"], []))
        external += [name for name in _stored_external(row.get("stored")) if name not in external]
        if external != list(row.get("external") or []):
            changed.append({"id": row["id"], "external": external})
    for batch in _batches(changed):
        graph.query("UNWIND $rows AS row MATCH (c:Chunk {id: row.id}) "
                    "SET c.external_references = CASE WHEN size(row.external) > 0 THEN row.external ELSE null END", params={"rows": batch})


def _write(graph, references: List[dict], done: List[dict], partial: List[dict], docs_hash: str,
           mode: str = "llm", rule_edges: Optional[set] = None, rule_external: Optional[Dict[str, List[str]]] = None) -> None:
    """特定まで終えたチャンク（done）について、自分が書いた REFERS_TO（method = 'llm'）を消して書き直す（やり直しても増えない）。

    特定に失敗した参照のあるチャンク（partial）は、抽出の結果だけを保存し、既存のリンクには触れない。
    途中で切れても次回に拾えるよう、消す前に「未解決」の印を付け、書き終えてから本来の値に直す。
    規則が同じ参照元から同じ先へ張っているリンク（rule_edges）は書かない。
    """
    rule_edges = rule_edges or set()
    rule_external = rule_external or {}
    by_source: Dict[str, List[dict]] = {}
    for r in references:
        by_source.setdefault(r["source"], []).append(r)
    done_ids = [d["id"] for d in done]
    to_chunks, to_docs, final = [], [], []
    stamp = lambda entry, links: json.dumps({"v": EXTRACT_VERSION, "docs": docs_hash, "links": links, "refs": entry["refs"],
                                             "mode": mode, "task": entry.get("task"), "covered": entry.get("covered") or []}, ensure_ascii=False)
    for entry in done:
        records = by_source.get(entry["id"], [])
        edges = set()  # 同じ文・同じ位置・同じ結び先は 1 本にまとまる
        for r in records:
            base = {"source": r["source"], "quote": r["quote"], "locator": r["locator"] or "", "topic": r["topic"], "scope": r["scope"]}
            for t in r["targets"]:
                if (r["source"], t["id"]) in rule_edges:
                    continue
                to_chunks.append(dict(base, target=t["id"]))
                edges.add((t["id"], base["quote"], base["locator"]))
            if r["scope"] == "other_document" and r["target_doc"] and not r["targets"] and (r["source"], r["target_doc"]) not in rule_edges:
                to_docs.append(dict(base, target=r["target_doc"]))
                edges.add((r["target_doc"], base["quote"], base["locator"]))
        links = len(edges)
        external = list(rule_external.get(entry["id"], []))
        for r in records:
            if r["scope"] == "external" and r["target_document"] and r["target_document"] not in external:
                external.append(r["target_document"])
        final.append({"id": entry["id"], "unresolved": False, "external": external, "stored": stamp(entry, links)})
    pending = [{"id": entry["id"], "unresolved": True, "stored": stamp(entry, 0)} for entry in partial]
    if pending:
        graph.query("UNWIND $rows AS row MATCH (c:Chunk {id: row.id}) "
                    "SET c.reference_extraction = row.stored, c.reference_unresolved = row.unresolved", params={"rows": pending})
    if not done_ids:
        return
    graph.query("MATCH (c:Chunk) WHERE c.id IN $ids SET c.reference_unresolved = true", params={"ids": done_ids})
    graph.query("MATCH (c:Chunk)-[r:REFERS_TO]->() WHERE c.id IN $ids AND r.method = $method DELETE r",
                params={"ids": done_ids, "method": METHOD})
    graph.query(
        "UNWIND $rows AS row MATCH (s:Chunk {id: row.source}), (t:Chunk {id: row.target}) "
        "MERGE (s)-[r:REFERS_TO {method: $method, quote: row.quote, locator: row.locator}]->(t) SET r.topic = row.topic, r.scope = row.scope",
        params={"rows": to_chunks, "method": METHOD})
    graph.query(
        "UNWIND $rows AS row MATCH (s:Chunk {id: row.source}), (t:Document {fileName: row.target}) "
        "MERGE (s)-[r:REFERS_TO {method: $method, quote: row.quote, locator: row.locator}]->(t) SET r.topic = row.topic, r.scope = row.scope",
        params={"rows": to_docs, "method": METHOD})
    graph.query(
        "UNWIND $rows AS row MATCH (c:Chunk {id: row.id}) "
        "SET c.reference_extraction = row.stored, c.reference_unresolved = row.unresolved, "
        "c.external_references = CASE WHEN size(row.external) > 0 THEN row.external ELSE null END",
        params={"rows": final})
