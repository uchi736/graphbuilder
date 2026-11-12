# -*- coding: utf-8 -*-
"""由来情報と安定キーの後処理。LLM を呼ばず、規則だけで付ける。何度流しても同じ結果になる（冪等）。

付けるもの
  P1 (Chunk)-[r:HAS_ENTITY]->(e)
       r.mention          チャンクの本文で、エンティティの名前を含む最初の文（200 字以内）。見つからなければ null
       r.mention_found    見つかったか（true / false）
       r.mention_source   見つかった所。body（本文）/ header（先頭のパンくずの行「[文書名 > 節]」）。見つからなければ null
  P2 (a:__Entity__)-[r]->(b:__Entity__)（抽出したリレーション。規則の層・構造の線は除く。EXCLUDED_RELATIONSHIP_TYPES を参照）
       r.source_chunk_id  根拠のチャンクの id。両端に HAS_ENTITY を持つチャンクの中から選ぶ
       r.evidence         根拠の文（200 字以内）
       r.evidence_kind    same_sentence（両端の名前が同じ文に出る）/ window（前後 1 文の範囲に出る）/
                          chunk_only（両端に HAS_ENTITY を持つチャンクはあるが、名前がその範囲に出ない）/
                          none（両端に HAS_ENTITY を持つチャンクが無い）
       r.provenance_method 'rule'
     チャンクの選び方: same_sentence → window → chunk_only の順に優先し、同じ順位の中では本文の文をパンくずの行より、
       文書名・位置の早いチャンクを先に採る。chunk_only の evidence は、どちらかの端の名前を含む文（無ければ null）とする。
     ※ 既定の検索（graph_vector・graph_vector_fulltext・entity_vector と参照追跡）は、エンティティ間のリレーションのプロパティを
       embedding 以外すべて文脈の「Relationships:」の行に書き出す（shared/constants.py の VECTOR_GRAPH_SEARCH_QUERY_SUFFIX と
       LOCAL_COMMUNITY_SEARCH_QUERY_SUFFIX）。ここで付ける 4 つのプロパティが文脈に出ると既定の検索の結果が変わるため、
       書き出しの側がこの 4 つを除外している場合に限って書く（relation_write_decision。環境変数 PROVENANCE_RELATION_PROPERTIES）。
       書かない場合も、集計（evidence_kind の内訳）は返す。
  P3 (Chunk)
       c.stable_key   文書名#節#節の中での順位（primary の節ごとに position の順、1 始まり）。節の無いチャンクは 文書名#-#文書内の順位
       c.doc_ordinal  文書内の順位（position の順、1 始まり）
     節の部分は Section.number があれば番号、無ければ path の末尾の題（NFKC で正規化し、空白を除き、「#」は「＃」に置き換え、40 字まで）。
       番号が経路の先頭の見出しの番号から始まらない節（「別表2」「番号の付け直し1」の下の付け直しの番号など）は、先頭の見出しの番号
       （番号が無ければ題）を「/」で前に付ける（例「別表2過給機性能試験及び性能換算法/1」）。それでも文書内で重なる節は、
       節の最初の位置の順に 2 番目以降へ「~2」「~3」を付ける。
     チャンクの id は本文のハッシュのため、変換器を替えると全部変わる。安定キーは文書名と節の番号と節の中での順位から作るので、
       節が同じで分割が同じなら変わらない。分けるときは右から 2 回「#」で切る（文書名に「#」があっても崩れない）。
     stable_key には索引 chunk_stable_key を張る（無ければ作る）。

文の区切り: 「。」「．」「！」「？」と改行（表の行は 1 行を 1 文とみなす）。数字に挟まれた「．」（小数点）では切らない。
  句点の直後の閉じ括弧（」』）など）は前の文に含める。
照合: NFKC で正規化し、大文字小文字と空白を無視する。エンティティの名前は id と aliases（id を先に照合する）。

使い方: attach_provenance(graph, dry_run=False, file_names=None) -> {"summary": {...}}
  dry_run=True は読み取りだけを行い、付けたとしたらの集計を返す（書き込まない。索引も作らない）。
  file_names を渡すと、その文書のチャンク・HAS_ENTITY と、その文書のチャンクに HAS_ENTITY を持つエンティティのリレーションだけを対象にする
  （根拠のチャンクは他の文書のものも候補にする）。
"""
import collections
import json
import logging
import os
import re
import unicodedata

from src.shared.common_fn import execute_graph_query

METHOD = "rule"
MAX_TEXT = 200
SECTION_TITLE_LIMIT = 40
STABLE_KEY_INDEX = "chunk_stable_key"
NO_SECTION = "-"
EVIDENCE_KINDS = ("same_sentence", "window", "chunk_only", "none")
RELATION_PROPERTY_KEYS = ("source_chunk_id", "evidence", "evidence_kind", "provenance_method")
# 既定の検索の書き出し（エンティティ間のリレーションのプロパティを文脈に出す問い合わせ）。constants の変数名
RETRIEVAL_RENDER_QUERIES = ("VECTOR_GRAPH_SEARCH_QUERY_SUFFIX", "LOCAL_COMMUNITY_SEARCH_QUERY_SUFFIX")

# 由来を付けないリレーションの種類。グラフの手動修正の予約の種類（graph_edit/validate.py の RESERVED_RELATIONSHIP_TYPES:
# HAS_ENTITY・PART_OF・NEXT_CHUNK・FIRST_CHUNK・SIMILAR・IN_COMMUNITY・PARENT_COMMUNITY・NEXT・LAST_MESSAGE・REFERS_TO・ALIAS_OF・
# WHEN・MENTIONS・HAS_SECTION・IN_SECTION）に、文書層の種類（document_replace.py の RULE_DOCUMENT_EDGES）を足す。
# APPLIES_TO は種類では除かない: 文書層の APPLIES_TO は Document から張られ method='rule' を持つので下の条件で外れ、
# エンティティ間の APPLIES_TO は抽出したもの（11 月 9 日の本番で 50 本、すべて method なし）だからである。
LAYER_RELATIONSHIP_TYPES = ["IS_VERSION_OF", "REFERS_TO", "LISTS", "ABOLISHES", "REPLACES", "HAS_SECTION", "IN_SECTION"]
_FALLBACK_RESERVED = ["HAS_ENTITY", "PART_OF", "NEXT_CHUNK", "FIRST_CHUNK", "SIMILAR", "IN_COMMUNITY", "PARENT_COMMUNITY",
                      "NEXT", "LAST_MESSAGE", "REFERS_TO", "ALIAS_OF", "WHEN", "MENTIONS", "HAS_SECTION", "IN_SECTION"]


def excluded_relationship_types():
    try:
        from src.graph_edit.validate import RESERVED_RELATIONSHIP_TYPES
        reserved = list(RESERVED_RELATIONSHIP_TYPES)
    except Exception:   # 読み込めない環境（単体の試験など）では同じ一覧の写しを使う
        reserved = _FALLBACK_RESERVED
    return sorted(set(reserved) | set(LAYER_RELATIONSHIP_TYPES))


EXCLUDED_RELATIONSHIP_TYPES = excluded_relationship_types()


# ---------------------------------------------------------------- 照合の正規化

def normalize_with_positions(text):
    """照合用に正規化する（NFKC・小文字・空白を除く）。正規化後の文字列と、各文字の元の文字列での位置の表を返す。
    1 字ずつ NFKC にかけ、半角の濁点（ﾞ）のような結合文字は直前の字と合成する（文字列全体に NFKC をかけた場合と同じ字になる）。"""
    out, pos = [], []
    for i, ch in enumerate(text or ""):
        for c in unicodedata.normalize("NFKC", ch).casefold():
            if c.isspace():
                continue
            if out and unicodedata.combining(c):
                composed = unicodedata.normalize("NFC", out[-1] + c)
                if len(composed) == 1:
                    out[-1] = composed
                    continue
            out.append(c)
            pos.append(i)
    return "".join(out), pos


def normalize(text):
    return normalize_with_positions(text)[0]


# 文書番号と項番を 1 つにした名前（EDC の規則、例「HS-VTR-T001 3-5項」）。本文では「HS-VTR-T001の3-5項」と書かれるので、
# 「の」でつないだ形も照合に使う（11/9）。先頭の語は英字と数字を両方含む文書番号の形に限る
_DOC_CLAUSE = re.compile(r"^(?P<doc>[^\s]*[A-Za-zＡ-Ｚａ-ｚ][^\s]*[0-9０-９][^\s]*)[\s　]+(?P<rest>[0-9０-９別付].*)$")


def name_variants(raw):
    """1 つの名前から照合に使う元の形（正規化前）を作る。元の名前を先に、文書番号＋項番なら「の」でつないだ形を後に。"""
    out = [raw]
    m = _DOC_CLAUSE.match(raw.strip())
    if m:
        out.append(m.group("doc") + "の" + m.group("rest"))
    return out


def entity_names(name, aliases=None):
    """照合に使う名前（正規化済み）。id を先に、aliases を後に並べる。空と重複は除く。"""
    names = []
    for raw in [name] + (list(aliases) if isinstance(aliases, (list, tuple)) else ([aliases] if aliases else [])):
        if raw is None:
            continue
        for variant in name_variants(str(raw)):
            n = normalize(variant)
            if n and n not in names:
                names.append(n)
    return names


# ---------------------------------------------------------------- 文の切り出し

_SENTENCE_END = "。．！？"
_CLOSERS = "」』）)］】〉》\"'”’"


def _is_digit(ch):
    return unicodedata.normalize("NFKC", ch).isdigit()


def _strip_span(text, start, end):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if end > start else None


def split_sentences(text, offset=0):
    """本文を文に分け、[(開始, 終了), ...]（元の文字列の位置。前後の空白は含めない）を返す。
    区切りは「。」「．」「！」「？」と改行。数字に挟まれた「．」では切らない。句点の直後の閉じ括弧と句点の連続は前の文に含める。"""
    text = text or ""
    spans = []
    start, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            span = _strip_span(text, start, i)
            if span:
                spans.append(span)
            start = i + 1
        elif ch in _SENTENCE_END:
            if ch == "．" and 0 < i < n - 1 and _is_digit(text[i - 1]) and _is_digit(text[i + 1]):
                i += 1
                continue
            j = i + 1
            while j < n and (text[j] in _CLOSERS or text[j] in _SENTENCE_END):
                j += 1
            span = _strip_span(text, start, j)
            if span:
                spans.append(span)
            start = j
            i = j
            continue
        i += 1
    span = _strip_span(text, start, n)
    if span:
        spans.append(span)
    return [(s + offset, e + offset) for s, e in spans]


def split_header(text):
    """先頭のパンくずの行（分割時に付ける「[文書名 > 節]」の行）を本文と分ける。(パンくずの位置 or None, 本文の開始位置) を返す。"""
    text = text or ""
    nl = text.find("\n")
    if nl < 0:
        return None, 0
    first = text[:nl].strip()
    if len(first) >= 2 and first.startswith("[") and first.endswith("]"):
        return _strip_span(text, 0, nl), nl + 1
    return None, 0


class Sentence:
    __slots__ = ("index", "start", "end", "block", "norm", "pos")

    def __init__(self, index, start, end, block, text):
        self.index, self.start, self.end, self.block = index, start, end, block
        self.norm, self.pos = normalize_with_positions(text[start:end])

    def find(self, names):
        """names のいずれかが文に出れば、元の文字列での (開始, 終了) を返す。names の順に照合する。"""
        for name in names:
            k = self.norm.find(name)
            if k >= 0:
                return self.start + self.pos[k], self.start + self.pos[k + len(name) - 1] + 1
        return None


class ChunkText:
    """チャンクの本文を文に分けたもの。本文の文（block='body'）とパンくずの行（block='header'）を持つ。"""

    def __init__(self, chunk_id, text):
        self.id = chunk_id
        self.text = text or ""
        header, body_start = split_header(self.text)
        self.sentences = [Sentence(i, s, e, "body", self.text)
                          for i, (s, e) in enumerate(split_sentences(self.text[body_start:], offset=body_start))]
        if header:
            self.sentences.append(Sentence(len(self.sentences), header[0], header[1], "header", self.text))

    def hits(self, names):
        """文ごとに、names が最初に出る位置。[(Sentence, 開始, 終了), ...]（本文の文の順、パンくずの行は最後）"""
        out = []
        if not names:
            return out
        for sent in self.sentences:
            found = sent.find(names)
            if found:
                out.append((sent, found[0], found[1]))
        return out

    def mention(self, names):
        """エンティティの名前を含む最初の文。(文の抜粋, 見つかった所) を返す。見つからなければ (None, None)。
        id を先に全文で探し、無ければ aliases で探す。本文の文をパンくずの行より先に探す。"""
        for name in names:
            for block in ("body", "header"):
                for sent in self.sentences:
                    if sent.block != block:
                        continue
                    found = sent.find([name])
                    if found:
                        return excerpt(self.text, sent.start, sent.end, found), block
        return None, None


def excerpt(text, start, end, focus=None, limit=MAX_TEXT):
    """text[start:end] を limit 字以内にして返す。長いときは focus（元の文字列での (開始, 終了)）の周りを残し、切った側に「…」を付ける。"""
    if end - start <= limit:
        return text[start:end]
    fs, fe = focus if focus else (start, start)
    fs = min(max(start, fs), end)
    fe = min(max(fs, fe), end)
    room = limit - 2   # 両側の「…」の分
    if fe - fs >= room:
        a, b = fs, fs + room
    else:
        a = max(start, fs - (room - (fe - fs)) // 2)
        b = min(end, a + room)
        a = max(start, b - room)
    out = text[a:b]
    if a > start:
        out = "…" + out
    if b < end:
        out = out + "…"
    return out[:limit]


# ---------------------------------------------------------------- リレーションの根拠

_KIND_RANK = {kind: i for i, kind in enumerate(EVIDENCE_KINDS)}


def evidence_in_chunk(chunk, names_a, names_b):
    """1 つのチャンクの中で、両端の名前が出る所を探す。(kind, evidence, 並べ替えの鍵) を返す。
    kind は same_sentence / window / chunk_only。鍵は小さいほど優先する。"""
    hits_a, hits_b = chunk.hits(names_a), chunk.hits(names_b)
    best = None
    for sa, a0, a1 in hits_a:
        for sb, b0, b1 in hits_b:
            if sa.block != sb.block:
                continue
            if sa.index == sb.index:
                kind = "same_sentence"
                start, end = sa.start, sa.end
            elif sa.block == "body" and abs(sa.index - sb.index) == 1:
                kind = "window"
                first, last = (sa, sb) if sa.index < sb.index else (sb, sa)
                start, end = first.start, last.end
            else:
                continue
            key = (_KIND_RANK[kind], 0 if sa.block == "body" else 1, min(sa.index, sb.index))
            if best is None or key < best[2]:
                focus = (min(a0, b0), max(a1, b1))
                best = (kind, excerpt(chunk.text, start, end, focus), key)
    if best:
        return best
    for sent, s0, s1 in sorted(hits_a + hits_b, key=lambda h: (h[0].block != "body", h[0].index)):
        return "chunk_only", excerpt(chunk.text, sent.start, sent.end, (s0, s1)), (_KIND_RANK["chunk_only"], 0, sent.index)
    return "chunk_only", None, (_KIND_RANK["chunk_only"], 1, 0)


def relation_evidence(chunks, names_a, names_b):
    """両端に HAS_ENTITY を持つチャンク（chunks: 文書名・位置の順に並べた ChunkText）から根拠を選ぶ。
    {"kind", "chunk_id", "evidence"} を返す。チャンクが無ければ kind は none。"""
    best = None
    for order, chunk in enumerate(chunks):
        kind, evidence, key = evidence_in_chunk(chunk, names_a, names_b)
        key = (key[0], key[1], order, key[2])
        if best is None or key < best[0]:
            best = (key, {"kind": kind, "chunk_id": chunk.id, "evidence": evidence})
    if best is None:
        return {"kind": "none", "chunk_id": None, "evidence": None}
    return best[1]


def relation_write_decision(mode=None):
    """エンティティ間のリレーションに由来のプロパティ（RELATION_PROPERTY_KEYS）を書くか。(書くか, 理由) を返す。
    mode（省略時は環境変数 PROVENANCE_RELATION_PROPERTIES、既定 auto）:
      auto  既定の検索の書き出し（constants の RETRIEVAL_RENDER_QUERIES）が 4 つのプロパティ名をすべて除外の対象として
            書いている場合だけ書く（書き出しを直す前に流しても、既定の検索の文脈は変わらない）
      true  常に書く（書き出しを直していなければ、既定の検索の文脈に根拠の文が出て結果が変わる）
      false 書かない"""
    mode = str(mode if mode is not None else os.environ.get("PROVENANCE_RELATION_PROPERTIES", "auto")).strip().lower()
    if mode in ("true", "1", "yes", "on"):
        return True, "PROVENANCE_RELATION_PROPERTIES=true"
    if mode in ("false", "0", "no", "off"):
        return False, "PROVENANCE_RELATION_PROPERTIES=false"
    try:
        from src.shared import constants as C
    except Exception as e:
        return False, f"既定の検索の書き出しを確かめられない（{type(e).__name__}）"
    missing = []
    for name in RETRIEVAL_RENDER_QUERIES:
        query = getattr(C, name, "") or ""
        if "keys(r)" not in query:
            continue
        lacking = [k for k in RELATION_PROPERTY_KEYS if f"'{k}'" not in query]
        if lacking:
            missing.append(f"{name}: {', '.join(lacking)}")
    if missing:
        return False, "既定の検索の書き出しが由来のプロパティを除外していない（" + "; ".join(missing) + "）"
    return True, "既定の検索の書き出しが由来のプロパティを除外している"


# ---------------------------------------------------------------- 安定キー

def _key_part(text, limit=None):
    t = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).replace("#", "＃")
    return t[:limit] if limit else t


def section_label(section):
    """節の安定キーの部分（重なりの解消の前）。section: {number, path, title}"""
    number = _key_part(section.get("number"))
    path = section.get("path") or []
    path = [path] if isinstance(path, str) else [p for p in path if p]
    if number:
        if len(path) > 1:
            root = _key_part(path[0])
            root_num = re.match(r"^[(（]?(\d+)", root)
            first = re.match(r"^[(（]?(\d+)", number)
            if not (root_num and first and root_num.group(1) == first.group(1)):
                qualifier = root_num.group(1) if root_num else root[:SECTION_TITLE_LIMIT]
                return f"{qualifier}/{number}"
        return number
    title = _key_part(path[-1] if path else section.get("title"), SECTION_TITLE_LIMIT)
    if not title or title == NO_SECTION:
        title = "(" + (title or str(section.get("id") or "")[:8]) + ")"
    return title


def section_labels(sections):
    """節ごとの安定キーの部分を決める。sections: [{id, fileName, number, path, title, first_position, offset}]。{id: 部分} を返す。
    文書内で同じ部分になる節は、節の最初の位置の順に 2 番目以降へ「~2」「~3」を付ける。"""
    groups = collections.defaultdict(list)
    for s in sections:
        groups[(s.get("fileName"), section_label(s))].append(s)
    out = {}
    for (_, label), members in groups.items():
        members.sort(key=lambda s: (s.get("first_position") is None, s.get("first_position") or 0, s.get("offset") or 0, str(s["id"])))
        for k, s in enumerate(members):
            out[s["id"]] = label if k == 0 else f"{label}~{k + 1}"
    return out


def stable_keys(chunks, labels):
    """チャンクの安定キーと文書内の順位。chunks: [{id, fileName, position, section}]（section は primary の節の id、無ければ None）。
    {id: (stable_key, doc_ordinal)} を返す。"""
    by_file = collections.defaultdict(list)
    for c in chunks:
        by_file[c["fileName"]].append(c)
    out = {}
    for file_name, rows in by_file.items():
        rows.sort(key=lambda c: (c.get("position") is None, c.get("position") or 0, str(c["id"])))
        per_section = collections.Counter()
        for ordinal, c in enumerate(rows, 1):
            sid = c.get("section")
            if sid and sid in labels:
                per_section[sid] += 1
                key = f"{file_name}#{labels[sid]}#{per_section[sid]}"
            else:
                key = f"{file_name}#{NO_SECTION}#{ordinal}"
            out[c["id"]] = (key, ordinal)
    return out


def parse_stable_key(key):
    """安定キーを (文書名, 節の部分, 順位) に分ける。文書名に「#」があっても崩れないよう、右から 2 回切る。"""
    doc, section, ordinal = key.rsplit("#", 2)
    return doc, section, int(ordinal)


# ---------------------------------------------------------------- グラフの読み書き

def _batches(rows, size=500):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def _chunk_texts(graph, ids):
    out = {}
    ids = sorted(ids)
    for batch in _batches(ids, 1000):
        for r in execute_graph_query(graph, """
                UNWIND $ids AS id
                MATCH (c:Chunk {id: id})
                OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
                WITH c, min(d.fileName) AS fileName
                RETURN c.id AS id, c.text AS text, coalesce(fileName, c.fileName) AS fileName, c.position AS position""",
                params={"ids": batch}):
            out[r["id"]] = r
    return out


def attach_provenance(graph, dry_run=False, file_names=None, relation_mode=None, details=False):
    files = sorted(set(file_names)) if file_names else None
    write_relations, relation_reason = relation_write_decision(relation_mode)

    # チャンク（対象の文書）と primary の節。2 つの文書に PART_OF を持つチャンク（試験用の写しに 2 件ある）と、
    # primary の節が 2 つあるチャンクは、名前の小さい方に決めて 1 行にする
    chunk_rows = execute_graph_query(graph, """
        MATCH (c:Chunk)-[:PART_OF]->(d:Document)
        WHERE $files IS NULL OR d.fileName IN $files
        WITH c, min(d.fileName) AS fileName
        OPTIONAL MATCH (c)-[p:IN_SECTION]->(s:Section) WHERE p.primary = true
        WITH c, fileName, min(s.id) AS section
        RETURN c.id AS id, fileName, c.position AS position, section""", params={"files": files})
    in_scope = {r["id"] for r in chunk_rows}
    section_rows = execute_graph_query(graph, """
        MATCH (s:Section) WHERE $files IS NULL OR s.fileName IN $files
        RETURN s.id AS id, s.fileName AS fileName, s.number AS number, s.path AS path, s.title AS title,
               s.first_position AS first_position, s.offset AS offset""", params={"files": files})

    # HAS_ENTITY（根拠のチャンクの候補のため、全部読む）
    has_entity = execute_graph_query(graph, """
        MATCH (c:Chunk)-[r:HAS_ENTITY]->(e)
        RETURN elementId(r) AS rid, c.id AS chunk, elementId(e) AS entity, e.id AS name, e.aliases AS aliases""")
    chunks_of = collections.defaultdict(set)
    names_of = {}
    for h in has_entity:
        chunks_of[h["entity"]].add(h["chunk"])
        names_of.setdefault(h["entity"], entity_names(h["name"], h["aliases"]))
    in_scope_entities = {h["entity"] for h in has_entity if h["chunk"] in in_scope}

    # エンティティ間のリレーション（抽出したもの）
    relations = execute_graph_query(graph, """
        MATCH (a:__Entity__)-[r]->(b:__Entity__)
        WHERE NOT type(r) IN $excluded AND coalesce(r.method, '') <> $m
        RETURN elementId(r) AS rid, type(r) AS type, elementId(a) AS a, elementId(b) AS b,
               a.id AS a_name, b.id AS b_name, a.aliases AS a_aliases, b.aliases AS b_aliases""",
        params={"excluded": EXCLUDED_RELATIONSHIP_TYPES, "m": METHOD})
    if files is not None:
        relations = [r for r in relations if r["a"] in in_scope_entities or r["b"] in in_scope_entities]

    # 本文は使うチャンクの分だけ読む
    needed = {h["chunk"] for h in has_entity if h["chunk"] in in_scope}
    for r in relations:
        needed |= chunks_of.get(r["a"], set()) & chunks_of.get(r["b"], set())
    rows = _chunk_texts(graph, needed)
    texts = {cid: ChunkText(cid, row["text"]) for cid, row in rows.items()}
    order = {cid: (str(row["fileName"] or ""), row["position"] if row["position"] is not None else 10 ** 9, cid)
             for cid, row in rows.items()}

    # P1 チャンクの中の言及
    mention_rows = []
    for h in has_entity:
        if h["chunk"] not in in_scope or h["chunk"] not in texts:
            continue
        mention, source = texts[h["chunk"]].mention(names_of.get(h["entity"]) or entity_names(h["name"], h["aliases"]))
        mention_rows.append({"rid": h["rid"], "chunk": h["chunk"], "name": h["name"], "mention": mention,
                             "found": mention is not None, "source": source})

    # P2 リレーションの根拠
    relation_rows = []
    for r in relations:
        candidates = sorted((chunks_of.get(r["a"], set()) & chunks_of.get(r["b"], set())) & set(texts), key=lambda c: order[c])
        names_a = names_of.get(r["a"]) or entity_names(r["a_name"], r["a_aliases"])
        names_b = names_of.get(r["b"]) or entity_names(r["b_name"], r["b_aliases"])
        ev = relation_evidence([texts[c] for c in candidates], names_a, names_b)
        relation_rows.append({"rid": r["rid"], "type": r["type"], "start": r["a_name"], "end": r["b_name"],
                              "chunk": ev["chunk_id"], "evidence": ev["evidence"], "kind": ev["kind"]})

    # P3 安定キー
    labels = section_labels(section_rows)
    keys = stable_keys(chunk_rows, labels)
    key_counts = collections.Counter(k for k, _ in keys.values())

    kinds = collections.Counter(r["kind"] for r in relation_rows)
    found = sum(1 for m in mention_rows if m["found"])
    summary = {
        "dry_run": bool(dry_run),
        "files": len({r["fileName"] for r in chunk_rows}),
        "has_entity": len(mention_rows),
        "mention_found": found,
        "mention_found_rate": round(found / len(mention_rows), 3) if mention_rows else None,
        "mention_in_header_only": sum(1 for m in mention_rows if m["source"] == "header"),
        "relations": len(relation_rows),
        "evidence_kind": {k: kinds.get(k, 0) for k in EVIDENCE_KINDS},
        "relation_evidence_rate": round((kinds["same_sentence"] + kinds["window"]) / len(relation_rows), 3) if relation_rows else None,
        "relation_properties_enabled": bool(write_relations),
        "relation_properties_written": bool(write_relations and not dry_run),
        "relation_properties_reason": relation_reason,
        "chunks": len(chunk_rows),
        "chunks_with_section_key": sum(1 for k, _ in keys.values() if parse_stable_key(k)[1] != NO_SECTION),
        "stable_key_duplicates": sum(n - 1 for n in key_counts.values() if n > 1),
    }

    if not dry_run:
        for batch in _batches(mention_rows):
            execute_graph_query(graph, """
                UNWIND $rows AS row
                MATCH ()-[r:HAS_ENTITY]->() WHERE elementId(r) = row.rid
                SET r.mention = row.mention, r.mention_found = row.found, r.mention_source = row.source""",
                params={"rows": [{k: m[k] for k in ("rid", "mention", "found", "source")} for m in batch]})
        if write_relations:
            for batch in _batches(relation_rows):
                execute_graph_query(graph, """
                    UNWIND $rows AS row
                    MATCH ()-[r]->() WHERE elementId(r) = row.rid
                    SET r.source_chunk_id = row.chunk, r.evidence = row.evidence, r.evidence_kind = row.kind, r.provenance_method = $m""",
                    params={"m": METHOD, "rows": [{k: x[k] for k in ("rid", "chunk", "evidence", "kind")} for x in batch]})
        execute_graph_query(graph, f"CREATE INDEX {STABLE_KEY_INDEX} IF NOT EXISTS FOR (c:Chunk) ON (c.stable_key)")
        for batch in _batches([{"id": cid, "key": k, "ordinal": o} for cid, (k, o) in keys.items()]):
            execute_graph_query(graph, """
                UNWIND $rows AS row
                MATCH (c:Chunk {id: row.id})
                SET c.stable_key = row.key, c.doc_ordinal = row.ordinal""", params={"rows": batch})
    logging.info("provenance: %s", json.dumps(summary, ensure_ascii=False))
    result = {"summary": summary}
    if details:
        result["details"] = {"mentions": mention_rows, "relations": relation_rows,
                             "stable_keys": {cid: {"stable_key": k, "doc_ordinal": o} for cid, (k, o) in keys.items()}}
    return result
