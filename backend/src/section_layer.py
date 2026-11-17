# -*- coding: utf-8 -*-
"""章・節の層の後処理（段 3 の B4）。LLM を呼ばず、分割時の見出しの経路だけから作る。何度流しても同じ結果になる。

  N2 (:Section {id, fileName, title, path, level, form, number, control})
  E7 (Document)-[:HAS_SECTION]->(Section)、(Section)-[:HAS_SECTION]->(Section)   見出しの木
  E8 (Chunk)-[:IN_SECTION {primary}]->(Section)                                    重なる節すべてへ。PART_OF は変えない
       主所属（primary）は 1 チャンクに 1 つ。本文の先頭の行が見出しの行なら、その見出しの節（M4）。
       そうでなければ従来どおり、チャンクの先頭位置を含む最も深い節
  E9 (Chunk)-[:REFERS_TO {level: "section", method: "rule", via, quote, source_method, standard, unresolved_locator}]->(Section)
       既存のチャンク → チャンク（または文書）の REFERS_TO（B1）を節へ付け直す。チャンク → チャンクの線は残す。結び先の決め方は次の順:
         1. 一文の文書番号の直後に番号がある（「HS-VTR-T001の3-5項」）: 文書層の先（Document -REFERS_TO-> Standard <-IS_VERSION_OF-
            Document）の文書の、その番号の節（via = standard、standard = 文書番号）
         2. locator の番号（M1。「3-5項」「上記2項」「2-1及び2-2項」「別表2」「別紙-1」「3)」）: 参照先の文書の、その番号の節。
            参照先の文書は、一文か locator に文書番号があれば文書層の先（M2、via = standard。LLM が選んだ文書は使わない）、
            無ければ LLM が選んだチャンクの文書（via = number）。ただし locator が自分の文書を指す語で始まるとき
            （「上記2項」「前記3-1」「本基準の…」）は、一文に他の文書の番号があっても参照元の文書で解く（1 は使わない）
         3. 番号が無いか解けない: LLM が選んだチャンクの主所属の節（via = chunk）。LLM のリンクが参照先の節の一覧から節を選んで
            張られていれば（リレーションの target_sections）、チャンクの主所属の節ではなく、その節を結び先にする。
            locator に番号があったのに解けなければ unresolved_locator に locator を残す。一文の文書番号の文書と LLM が選んだ文書が
            違えば（同じ文書番号の別の版は同じとみなす）、線を作らない
  E10 (Chunk)-[:REFERS_TO {level: "section", via: "standard", method: "rule"}]->(Section)                            M3
       文書層の Document -REFERS_TO-> Standard の quotes に、文書番号の直後の番号（「HS-VTR-T001の3-5項」）があれば、
       その一文を本文に含むチャンクから、参照先の文書のその番号の節へ。E9 と同じ節なら 1 本にまとめる

見出しの経路は、分割時に保存した配列 Chunk.section_path（S0 以降）を使い、無ければ文字列 Chunk.section（「文書名 > 2. 要領 > 3-1 計算」）を
「 > 」で切って使う。先頭の要素は文書名（分割時の題名）なので落とす。
文書層（build_document_layer）の後に流す（E9 の 1・2 と E10 は文書層の結果を読む）。文書層が無ければ従来どおりの結び先になる。
使い方: build_section_layer(graph) -> {"summary": {...}}
"""
import collections
import hashlib
import json
import logging
import re

from src.shared.common_fn import batches, execute_graph_query
from src import document_layer_rules as R
from src.document_layer import chunk_rows

METHOD = "rule"
_HEADING_LINE = re.compile(r"^[ ]*#{1,6}[ ]*\S")


def _path_of(row):
    if row.get("section_path"):
        try:
            p = json.loads(row["section_path"]) if isinstance(row["section_path"], str) else list(row["section_path"])
            return [x for x in p if x]
        except Exception:
            pass
    s = row.get("section") or ""
    if not s:
        return []
    parts = [x.strip() for x in s.split(" > ")]
    parts = parts[1:] if len(parts) > 1 else parts   # 先頭は文書名
    if parts and parts[-1].endswith("…"):
        parts[-1] = parts[-1][:-1]
    return [x for x in parts if x]


_RANK = {"章": 1, "節": 2, "項": 3, "号": 4, "目": 5}


def _normalize_path(path):
    """見出しの経路を、番号の形で決まる階層に直す。
    Markdown の見出しの深さは字の大きさから付くので、「3-1 > 3-2」（本当は兄弟）や「合否判定方法 > 協議・通知票」（本当は別の付属）になる。
    規則: 番号付きの見出しは 章 < 節 < 項 < 号 < 目 の順でしか深くならない（同じ形は兄弟、浅い形が来たら戻る）。
          番号の無い見出し（付属の表・用紙・印: 管理）は最上位に置く。節の番号の先頭が直前の章と違えば（3-1 の前が章 2）、章をまたいだ兄弟として最上位に置く。"""
    out = []   # [(title, rank)]
    for title in path:
        form, number = R.heading_form(title)
        rank = _RANK.get(form)
        if rank is None:
            out = [(title, 0)]          # 番号の無い見出しは最上位から始める
            continue
        while out and out[-1][1] >= rank:   # 同じ形か深い形の見出しは兄弟・祖先なので戻る
            out.pop()
        if form == "節" and out and out[-1][1] == 1:
            chap = out[-1][0]
            cform, cnum = R.heading_form(chap)
            if cnum and number.split("-")[0] != cnum:
                out = []                  # 章が違う節は、その章の下ではない
        out.append((title, rank))
    return [t for t, _ in out]


def _heading_primary(c, hits, sections):
    """M4: チャンクの本文の先頭の行（空行を除く）が見出しの行なら、その見出しの節の id を返す。無ければ None（従来の決め方に任せる）。

    見出しの行 = 見出し記号（#）で始まる行か、目録が見出しと認めた位置を含む行。その行に始まる節（hits のうち、範囲の始まりが
    その行にあるもの）で、題名の先頭 4 字が行に含まれるものを採る（同じ番号の見出しが 2 回出て先の節に寄せられた場合に、
    題名の違う節を主所属にしないため）。同じ行に複数あれば最も深い節。
    分割時のパンくず（[文書 > 見出し]）は本文に含まないので、チャンクの先頭位置（offset 0）と見出しの位置（「### 」の後ろ）の
    ずれで直前の節が主所属になる問題（K4）を直す。"""
    flat = c.flat or ""
    i = len(flat) - len(flat.lstrip())
    if i >= len(flat):
        return None
    e = flat.find("\n", i)
    e = len(flat) if e < 0 else e
    line = flat[i:e]
    on_line = [(s0, sid) for s0, sid in hits if s0[0] == c.pos and (i <= s0[1] <= e or s0[1] == 0)]
    if not on_line:
        return None
    if not _HEADING_LINE.match(line) and not any(s0[1] > 0 for s0, _ in on_line):
        return None   # 見出し記号の無い行で、目録の見出しの位置も無い（Markdown の根の位置 0 だけ）
    key_line = R.squeeze(line)
    ok = []
    for s0, sid in on_line:
        sec = sections.get(sid)
        if not sec:
            continue
        key = R.squeeze(sec.get("title") or "")[:4]
        if key and key not in key_line:
            continue
        ok.append((sec.get("level") or 0, s0, sid))
    return max(ok)[2] if ok else None


def _section_id(file_name, path):
    return hashlib.sha1(("\x1f".join([file_name] + list(path))).encode()).hexdigest()


def _trim_title(text, limit=40):
    """目録の見出しの文字列（行の残り）から題名だけを取る。2 つ以上の空白・句点・「1)」などの箇条の始まりで切る。"""
    t = R.nfkc(text or "").strip()
    # 2 つ以上の空白・句点・読点・次の項番号（「3-1 」）・箇条の始まり（「1) 」「a) 」）・ページの印（「 1 」）で切る
    t = re.split(r"\s{2,}|。|、|(?<=\S)\s(?=\d{1,2}-\d{1,2}\s)|(?<=\S)\s(?=\d{1,2}[.)）]\s*\S)|(?<=\S)\s(?=[a-z][.)）])|(?<=\S)\s(?=\d{1,3}\s)", t)[0].strip()
    # 題名の後に文が続くとき（「聴音検査 エアー運転時…を行う」）は、助詞を含む語の前の空白で切る
    m = re.match(r"^(\S{2,}(?:\s\S{1,3})?)\s(?=\S{0,12}[はをがにでへと])", t)
    if m and len(t) - len(m.group(1)) >= 6:
        t = m.group(1)
    return t[:limit].strip(" -")


def sections_from_catalogue(rows, cfg, memo=None):
    """参照抽出の規則（北野・天笠の 8 段の段 0 目録）が見つけた項番号の見出しから、文書ごとの節の表を作る。
    字数分割の文書（Markdown の見出しが無い）でも使える。
    memo: 目録を文書層と共有する辞書（chunk_reference_rules.catalogue_of）
    返り値: sections {id: {...}}, chunk_sections {chunk_id: [(section_id, primary)]}"""
    from src.chunk_reference_rules import catalogue_of
    cat = catalogue_of([r for r in rows if r.get("text") is not None], memo)
    sections, chunk_sections = {}, {}
    forms = {1: "章", 2: "節", 3: "項", 4: "号"}
    # Markdown の経路にだけある「番号の無い最上位の見出し」（付属の用紙: 計測要領・合否判定方法・協議・通知票 など）を、
    # 位置付きの根として目録の見出しに混ぜる。目録の「番号の付け直し N」の範囲は、直前のこの根の下に置く
    md_roots = collections.defaultdict(list)   # fileName -> [(pos, title)]
    seen = set()
    for r in rows:
        path = _path_of(r)
        if not path:
            continue
        top = path[0]
        form, _ = R.heading_form(top)
        if form is None and (r["fileName"], top) not in seen:
            seen.add((r["fileName"], top))
            md_roots[r["fileName"]].append((r["position"], top))
    for name, D in cat.doc.items():
        # (pos, off, scope, key, title) を位置の順に
        heads = sorted(list(D.heads) + [(pos, 0, "__md__:" + title, (), title) for pos, title in md_roots.get(name, [])], key=lambda h: (h[0], h[1]))
        if not heads:
            continue
        # 範囲（scope）ごとの親ノード（main 以外: 添付資料・別表 2 など）
        scope_root = {}
        current_md = {"sid": None, "title": None}
        def root_of(scope):
            if scope == "main":
                return None
            if scope.startswith("__md__:"):
                return None
            if current_md["sid"]:
                return current_md["sid"]           # 付け直しの範囲は、直前の付属の見出しの下
            if scope in scope_root:
                return scope_root[scope]
            sid = _section_id(name, [scope])
            sections[sid] = {"id": sid, "fileName": name, "title": scope, "path": [scope], "level": 1, "form": "付属", "number": None,
                             "control": R.is_control_heading(scope, cfg), "parent": None, "first_position": None, "source": "catalogue"}
            scope_root[scope] = sid
            return sid
        by_key = {}
        head_sids = []   # 見出しごとの節の id（下でチャンクの所属の範囲を作るのに使う）
        for pos, off, scope, key, title in heads:
            if scope.startswith("__md__:"):
                sid = _section_id(name, [title])
                sections[sid] = {"id": sid, "fileName": name, "title": _trim_title(title), "path": [title], "level": 1, "form": "付属", "number": None,
                                 "control": R.is_control_heading(title, cfg), "parent": None, "first_position": pos, "offset": 0, "source": "markdown"}
                current_md = {"sid": sid, "title": title}
                head_sids.append(sid)
                continue
            if scope == "main" and len(key) == 1:
                current_md = {"sid": None, "title": None}   # 本文の章が始まったら付属の範囲は終わり
            root = root_of(scope)
            scope_tag = [current_md["title"]] if current_md["sid"] else ([scope] if scope != "main" else [])
            sid = _section_id(name, scope_tag + ["-".join(map(str, key))])
            head_sids.append(sid)
            if sid in sections:
                continue   # 同じ番号が 2 回出る（例の並記）: 先のものを採る
            parent = by_key.get((scope, key[:-1])) if len(key) > 1 else root
            path = list(scope_tag) + ["-".join(map(str, k)) + " " + _trim_title(sections[by_key[(scope, k)]]["title_raw"]) if (scope, k) in by_key else "" for k in [key[:i] for i in range(1, len(key))]]
            path = [x for x in path if x] + ["-".join(map(str, key)) + " " + _trim_title(title)]
            sections[sid] = {"id": sid, "fileName": name, "title": _trim_title(title) or "-".join(map(str, key)), "title_raw": title, "path": path,
                             "level": len(key) + (1 if root else 0), "form": forms.get(len(key), "目"), "number": "-".join(map(str, key)),
                             "control": R.is_control_heading(title, cfg), "parent": parent, "first_position": pos, "offset": off, "source": "catalogue"}
            by_key[(scope, key)] = sid
            if root and sections[root]["first_position"] is None:
                sections[root]["first_position"] = pos
        # チャンクの所属: 見出しの位置で区切った範囲に重なる節すべて（primary は、チャンクの先頭を含む最も深い節）
        spans = []
        for i, (h, sid) in enumerate(zip(heads, head_sids)):
            end = heads[i + 1][:2] if i + 1 < len(heads) else (10 ** 9, 0)
            spans.append((h[:2], end, sid))
        for c in D.chunks:
            start, stop = (c.pos, 0), (c.pos, len(c.flat))
            hits = []
            for (s0, e0, sid) in spans:
                if s0 < stop and e0 > start:
                    hits.append((s0, sid))
            if not hits:
                continue
            primary = max((h for h in hits if h[0] <= start), default=hits[0])[1]
            primary = _heading_primary(c, hits, sections) or primary   # M4: 先頭の行の見出しの節を主所属に
            chunk_sections[c.id] = [(sid, sid == primary) for _, sid in hits]
        # 節の本文: 見出しの位置から次の見出しの直前までを、チャンクをまたいでつなぐ（揃えた後の本文）。長い節は上限で切る
        flat_of = {c.pos: c.flat for c in D.chunks}
        order = sorted(flat_of)
        for (s0, e0, sid) in spans:
            buf = []
            for pos in order:
                if pos < s0[0] or pos > e0[0]:
                    continue
                a = s0[1] if pos == s0[0] else 0
                b = e0[1] if pos == e0[0] else len(flat_of[pos])
                if b > a:
                    buf.append(flat_of[pos][a:b])
            # 隣り合うチャンクは分割時の重なり（chunk_overlap）で同じ字句を持つので、前の末尾と次の先頭の重なりを 1 つにする
            joined = ""
            for piece in buf:
                if joined:
                    k = 0
                    for L in range(min(300, len(joined), len(piece)), 20, -1):
                        if joined.endswith(piece[:L]):
                            k = L
                            break
                    piece = piece[k:]
                joined = (joined + ("\n" if joined and piece else "") + piece) if piece else joined
            text = joined.strip()
            sections[sid]["text"] = text[:6000]
            sections[sid]["text_len"] = len(text)
    for sec in sections.values():
        sec.pop("title_raw", None)
    return sections, chunk_sections


# ---- E9・E10: 参照リンクの節への付け直し ----
# 同じ参照元・同じ節へ複数のリンクが付け直されたときに残す決め方の順（文書番号と番号 > 番号 > LLM の結び先）
_VIA_RANK = {"chunk": 0, "number": 1, "standard": 2}
# 参照の言い回し（「…による」「…を参照」「…に準ずる」など）。文書番号だけを挙げた一覧の行と、参照の文を分けるのに使う
_REFERENCE_VERB = re.compile(r"(によ[るりっ]|に依[るりっ]|に従|参照|準ず|準拠|基づ|基く|に定め|の通り|のとおり|を適用|適用する)")
# locator が参照元自身の文書を指す語で始まるか（「上記2項」「前記3-1」「本基準」「本規格」）。「上記規格」のように
# 他の文書を指す使い方を拾わないよう、「上記」「前記」は直後に番号が続くときだけとする
_SELF_LOCATOR = re.compile(r"^\s*(?:(?:上記|前記)\s*(?:第\s*)?\d|本基準|本規格|本書|本要領|本標準)")
# 節の照会（検索側 SECTION_REFERENCE_QUERY）がたどる下位の節の深さ
_SUBSECTION_DEPTH = 3


class SectionIndex:
    """節の表を、番号・付属の題名の先頭の印・範囲（本文か、どの付属の資料か）で引く。"""

    def __init__(self, sections):
        self.sections = sections
        self.by_number = collections.defaultdict(list)   # (fileName, 形, 番号) -> [sid]（作った順）
        self.annex = collections.defaultdict(list)       # (fileName, 付属の印「別表2」) -> [sid]
        for sid, sec in sections.items():
            if sec.get("number"):
                self.by_number[(sec["fileName"], sec.get("form"), sec["number"])].append(sid)
            else:
                key = R.annex_key(sec.get("title")) or R.annex_key((sec.get("path") or [""])[0])
                if key:
                    self.annex[(sec["fileName"], key)].append(sid)

    def scope(self, sid):
        """節の範囲。最上位の祖先が番号の無い節（付属の資料・用紙）ならその id、番号のある節（本文）なら "main"。"""
        cur, seen = sid, set()
        while cur and cur not in seen:
            seen.add(cur)
            parent = (self.sections.get(cur) or {}).get("parent")
            if not parent:
                break
            cur = parent
        top = self.sections.get(cur) or {}
        return "main" if top.get("number") else cur

    def find(self, file_name, target, near=None):
        """参照の対象（parse_locator の 1 件。号を除く）を、文書 file_name の節の id に解く。解けなければ None。
        同じ番号の節が本文と付属の資料の両方にあるとき（本文の「2.」と別表 2 の中の「2.」）は、参照元の節（near、同じ文書のとき）と
        同じ範囲のものを先に採り、無ければ本文のものを採る。"""
        if not file_name:
            return None
        form, number = target["form"], target["number"]
        if form == "付属":
            cands = list(self.annex.get((file_name, target["label"])) or [])
        else:
            cands = list(self.by_number.get((file_name, form, number)) or [])
            if not cands and form == "項" and number and number.count("-") == 2:
                cands = list(self.by_number.get((file_name, "節", number.rsplit("-", 1)[0])) or [])   # 項の節が無ければ、それを含む節
        if not cands:
            return None
        near_ok = near and (self.sections.get(near) or {}).get("fileName") == file_name
        want = self.scope(near) if near_ok else "main"
        for sid in cands:
            if self.scope(sid) == want:
                return sid
        for sid in cands:
            if self.scope(sid) == "main":
                return sid
        return cands[0]


def _external_locator(text, cfg):
    """locator が手元に無い文書の識別子（JIS B 8340、QC-KAI-027 など）を含むか。含むなら、その中の番号は手元の文書の節ではない。"""
    s = R.squeeze(text)
    for p in cfg.get("external_prefixes", []):
        if re.search(r"(?<![A-Z])" + re.escape(p.upper()) + r"[A-Z\-]*\d{3,}", s):
            return True
    return False


def _body_key(text):
    """文書層の一文を本文と突き合わせる形（検索側の DOCUMENT_LAYER_REFERENCE_QUERY と同じく、半角・全角の空白と改行を除く）。"""
    return (text or "").replace(" ", "").replace("\n", "").replace("　", "")


def _document_layer(graph):
    """文書層の結果（文書番号 → 版の文書、文書 → 参照する文書番号と一文）を読む。文書層が無ければ空。"""
    versions = execute_graph_query(graph, """
        MATCH (d:Document)-[r:IS_VERSION_OF]->(st:Standard)
        RETURN d.fileName AS f, st.id AS st, r.rev AS rev ORDER BY st, f""")
    own, docs_of = {}, collections.defaultdict(list)
    for v in versions:
        own[v["f"]] = v["st"]
        docs_of[v["st"]].append((v.get("rev") or "", v["f"]))
    latest = {st: sorted(vs, reverse=True)[0][1] for st, vs in docs_of.items()}   # 版の符号の最も新しい文書
    refers = execute_graph_query(graph, """
        MATCH (d:Document)-[r:REFERS_TO]->(st:Standard)
        RETURN d.fileName AS f, st.id AS st, r.quotes AS quotes ORDER BY f, st""")
    return own, latest, refers


def resolve_section_refs(graph, rows, sections, chunk_sections, chunk_section, cfg):
    """E9（チャンク間の参照リンクの付け直し）と E10（文書層の一文の節番号、M3）。グラフには書かない。
    返り値: ({(参照元のチャンク, 節): {"quote", "via", "method", "standard", "unresolved_locator"}}, 集計)"""
    file_of = {r["id"]: r["fileName"] for r in rows}
    index = SectionIndex(sections)
    own, latest, doc_refs = _document_layer(graph)

    def file_for(src_file, canon):
        """文書番号の文書。参照元自身の番号なら参照元の文書、それ以外は文書層で版の付いた文書（最新の版）。無ければ None。"""
        if own.get(src_file) == canon:
            return src_file
        return latest.get(canon)

    section_refs = {}
    pending = {}   # LLM の結び先で節を決める参照: (参照元, 一文, 位置の語, 参照先の文書) -> {"tgts": [チャンク], ...}
    stats = collections.Counter()

    # 節のチャンク（下位 _SUBSECTION_DEPTH 段まで、副所属を含む。検索側の SECTION_REFERENCE_QUERY と同じ範囲）。
    # 参照先の節のチャンクが参照元のチャンクだけなら、検索は何も足さないので線を張らない（「上記2-1と同一の…」のように同じチャンク内を指す参照）
    children = collections.defaultdict(list)
    for sid0, sec0 in sections.items():
        if sec0.get("parent"):
            children[sec0["parent"]].append(sid0)
    members_of_section = collections.defaultdict(set)
    for cid0, lst0 in chunk_sections.items():
        for sid0, _p0 in lst0 or []:
            members_of_section[sid0].add(cid0)
    _members_cache = {}

    def members(sid):
        if sid not in _members_cache:
            seen, frontier = {sid}, [sid]
            for _ in range(_SUBSECTION_DEPTH):
                frontier = [c for f in frontier for c in children.get(f, []) if c not in seen]
                seen.update(frontier)
            _members_cache[sid] = set().union(*(members_of_section.get(x, set()) for x in seen))
        return _members_cache[sid]

    def put(src, sid, quote, via, method, standard=None, unresolved=None):
        if not (members(sid) - {src}):
            stats["self_only_skipped"] += 1
            return
        key = (src, sid)
        entry = {"quote": (quote or "")[:120], "via": via, "method": method, "standard": standard,
                 "unresolved_locator": unresolved if via == "chunk" else None}
        old = section_refs.get(key)
        if old is None:
            section_refs[key] = entry
        elif _VIA_RANK[via] > _VIA_RANK[old["via"]]:
            entry["standard"] = entry["standard"] or old.get("standard")
            section_refs[key] = entry
        else:
            old["standard"] = old.get("standard") or standard
            if old["via"] == "chunk" and not old.get("unresolved_locator") and entry["unresolved_locator"]:
                old["unresolved_locator"] = entry["unresolved_locator"]

    refs = execute_graph_query(graph, """
        MATCH (a:Chunk)-[r:REFERS_TO]->(b)
        WHERE b:Chunk OR b:Document
        RETURN a.id AS src, CASE WHEN b:Chunk THEN b.id ELSE null END AS tgt,
               CASE WHEN b:Chunk THEN coalesce(b.fileName, head([(b)-[:PART_OF]->(bd:Document) | bd.fileName])) ELSE b.fileName END AS tgt_file,
               r.locator AS locator, r.quote AS quote, r.method AS method,
               r.target_sections AS target_sections, r.target_section_paths AS target_section_paths""")
    # 何度流しても同じ結果になるよう、読んだ順に頼らず並べ直す（同じ節へのリンクが複数あるとき、一文はこの順の先のものを採る）
    refs = sorted(refs, key=lambda r: (r["src"] or "", r.get("tgt") or "", r.get("tgt_file") or "", r.get("locator") or "", r.get("quote") or ""))
    path_index = {}
    for sid0, sec0 in sections.items():
        path_index.setdefault((sec0["fileName"], " > ".join(sec0.get("path") or [sec0.get("title") or ""])), sid0)

    def llm_sections(ref, tgt_file):
        """LLM が一覧から選んだ節のうち、今の節の表にあるもの。id が変わっていれば（目録が変わった）経路で引き直す。"""
        out = []
        ids = list(ref.get("target_sections") or [])
        paths = list(ref.get("target_section_paths") or [])
        for i, sid0 in enumerate(ids):
            if sid0 in sections and sections[sid0]["fileName"] == tgt_file:
                found = sid0
            else:
                found = path_index.get((tgt_file, paths[i] if i < len(paths) else ""))
            if found and found not in out:
                out.append(found)
        return out

    for ref in refs:
        src, tgt, tgt_file = ref["src"], ref.get("tgt"), ref.get("tgt_file")
        src_file = file_of.get(src)
        if not src_file:
            continue
        loc = R.nfkc(ref.get("locator") or "")
        quote = ref.get("quote") or ""
        text = quote + " / " + loc
        near = chunk_section.get(src)
        targets = [] if _external_locator(loc, cfg) else R.parse_locator(loc)
        if any(t["form"] != "号" for t in targets):
            targets = [t for t in targets if t["form"] != "号"]   # 「2-1 の 1)」は 2-1 の節で足りる
        # locator が自分の文書を指す語で始まり（「上記2項」）、locator 自身に文書番号が無ければ、一文の他の文書の番号は使わない
        self_ref = bool(_SELF_LOCATOR.match(loc)) and not R.document_numbers_in(loc, cfg)
        numbers = [] if self_ref else R.document_numbers_in(text, cfg)
        doc_files = {}
        for canon in numbers:
            f = file_for(src_file, canon)
            if f and f not in doc_files:
                doc_files[f] = canon
        tgt_std = own.get(tgt_file)
        # 参照先の文書（M2）: 一文か locator に文書番号があれば、その番号の文書。LLM が選んだ文書は使わない
        target_file, standard = (src_file if self_ref else tgt_file), None
        if doc_files:
            same_std = [f for f, c in doc_files.items() if tgt_std and c == tgt_std]
            if tgt_file in doc_files:
                standard = doc_files[tgt_file]
            elif same_std:
                target_file, standard = same_std[0], tgt_std   # LLM が同じ文書番号の別の版を選んだ: 文書層の版で解く
            elif len(doc_files) == 1:
                target_file, standard = next(iter(doc_files.items()))
            else:
                target_file = None          # 文書番号が 2 つ以上あり、LLM の文書がどれでもない: 番号では決めない
        elif numbers and tgt_std not in numbers:
            target_file = None              # 手元に無い文書の番号: その中の番号は手元の節ではない
        resolved = []
        # 1. 文書番号の直後の番号（「HS-VTR-T001の3-5項」）。locator が自分の文書を指すときは使わない
        for canon, t in ([] if self_ref else R.numbers_after_document_numbers(text, cfg)):
            f = file_for(src_file, canon)
            sid = index.find(f, t, near if f == src_file else None)
            if sid:
                resolved.append((sid, "standard" if f != src_file else "number", canon if f != src_file else None))
        # 2. locator の番号（M1）
        if not resolved and targets:
            for t in targets:
                if t["form"] == "号":
                    sid = near if target_file == src_file else None   # 同じ節の中の箇条: 参照元の節（参照先が同じ文書のときだけ）
                else:
                    sid = index.find(target_file, t, near if target_file == src_file else None)
                if sid:
                    resolved.append((sid, "standard" if standard and target_file != src_file else "number", standard if target_file != src_file else None))
        if resolved:
            stats["resolved_by_number"] += 1
            if any(via == "standard" for _, via, _ in resolved):
                stats["resolved_by_standard"] += 1
            for sid, via, st in resolved:
                put(src, sid, quote, via, ref.get("method"), st)
            continue
        # 3. LLM が選んだチャンクの節（同じ参照の結び先をまとめてから、下で節を決める）
        if targets:
            stats["unresolved_locator"] += 1
        if not tgt:
            continue
        if doc_files and not targets and not _REFERENCE_VERB.search(R.nfkc(quote)):
            # 一文が他の文書の番号（と名称）を挙げているだけで、位置の語も参照の言い回しも無い（関連基準の一覧の行など）。
            # 文書の中の箇所を指していないので節には結ばない。文書へのたどりは文書層が受け持つ
            stats["document_list_skipped"] += 1
            continue
        if doc_files and tgt_file not in doc_files and tgt_std not in doc_files.values():
            # 一文の文書番号の文書と LLM が選んだ文書が違う（同じ文書番号の別の版なら違うとはみなさない）。文書へのたどりは文書層が受け持つ
            stats["dropped_by_standard"] += 1
            continue
        group = pending.setdefault((src, quote, loc, tgt_file), {"tgts": [], "sids": [], "method": ref.get("method"), "standard": standard,
                                                                 "unresolved": loc if targets else None})
        chosen = llm_sections(ref, tgt_file)
        if chosen:
            # LLM が節の一覧から選んだ節。チャンクの主所属の節（下位の節に割れる）ではなく、選んだ節をそのまま使う
            for sid0 in chosen:
                if sid0 not in group["sids"]:
                    group["sids"].append(sid0)
        elif tgt not in group["tgts"]:
            group["tgts"].append(tgt)
    for (src, quote, _loc, _tf), group in pending.items():
        tgts = [t for t in group["tgts"] if chunk_section.get(t)]
        if group["sids"]:
            stats["llm_section_targets"] += 1
        sids = list(group["sids"]) + [sid for sid in dict.fromkeys(chunk_section[t] for t in tgts) if sid not in group["sids"]]
        for sid in sids:
            put(src, sid, quote, "chunk", group["method"], group["standard"], unresolved=group["unresolved"])

    # E10（M3）: 文書層の一文に文書番号の直後の番号があれば、一文を本文に含むチャンクから参照先の文書のその節へ
    bodies = collections.defaultdict(list)
    for r in rows:
        bodies[r["fileName"]].append((r["id"], _body_key(r.get("text"))))
    for dr in doc_refs:
        f, st = dr["f"], dr["st"]
        tf = latest.get(st)
        if not tf or tf == f:
            continue
        for q in dr.get("quotes") or []:
            for canon, t in R.numbers_after_document_numbers(q, cfg):
                if canon != st:
                    continue
                sid = index.find(tf, t)
                if not sid:
                    stats["standard_unresolved"] += 1
                    continue
                key_q = _body_key(q)
                sources = [cid for cid, body in bodies.get(f, []) if key_q and key_q in body]
                for cid in sources:
                    put(cid, sid, q, "standard", "document_layer", st)
                stats["standard_section_refs"] += 1 if sources else 0
    summary = {"chunk_refs": len(refs), "section_refs": len(section_refs),
               "resolved_by_number": stats["resolved_by_number"], "resolved_by_standard": stats["resolved_by_standard"],
               "unresolved_locator": stats["unresolved_locator"], "dropped_by_standard": stats["dropped_by_standard"],
               "standard_section_refs": stats["standard_section_refs"], "standard_unresolved": stats["standard_unresolved"],
               "self_only_skipped": stats["self_only_skipped"],
               "document_list_skipped": stats["document_list_skipped"], "llm_section_targets": stats["llm_section_targets"],
               "section_refs_by_via": dict(collections.Counter(v["via"] for v in section_refs.values()))}
    return section_refs, summary


def build_section_layer(graph, write=True, memo=None):
    cfg = R.load_config()
    rows = chunk_rows(graph)
    # 第 1 の源: 規則の目録（全文書）。第 2 の源: Markdown の見出しの経路（目録に見出しの無い文書だけ）
    sections, chunk_sections = sections_from_catalogue(rows, cfg, memo)
    files_from_catalogue = {s["fileName"] for s in sections.values()}
    for r in rows:
        if r["fileName"] in files_from_catalogue:
            continue
        path = _normalize_path(_path_of(r))
        if not path:
            continue
        parent = None
        for depth, title in enumerate(path):
            sid = _section_id(r["fileName"], path[:depth + 1])
            if sid not in sections:
                form, number = R.heading_form(title)
                sections[sid] = {"id": sid, "fileName": r["fileName"], "title": title, "path": path[:depth + 1], "level": depth + 1,
                                 "form": form, "number": number, "control": R.is_control_heading(title, cfg), "parent": parent,
                                 "first_position": r["position"]}
            parent = sid
        chunk_sections[r["id"]] = [(parent, True)]
    summary ={"chunks": len(rows), "chunks_with_section": len(chunk_sections), "sections": len(sections),
               "sections_from_catalogue": sum(1 for s in sections.values() if s.get("source") == "catalogue"),
               "files_with_sections": len({s["fileName"] for s in sections.values()}), "control_sections": sum(1 for s in sections.values() if s["control"])}
    chunk_section = {cid: next((sid for sid, p in lst if p), lst[0][0]) for cid, lst in chunk_sections.items()}

    # E9・E10: 参照リンクを節へ付け直す（番号と文書番号で決まるものは規則で、決まらないものは LLM の結び先のチャンクの節へ）
    section_refs, ref_summary = resolve_section_refs(graph, rows, sections, chunk_sections, chunk_section, cfg)
    summary.update(ref_summary)
    if not write:
        return {"summary": summary, "sections": sections, "chunk_section": chunk_section, "section_refs": section_refs}

    # 抽出された型「Section」のエンティティは予約ラベルの付け替え（A2）で SectionRef にする（規則のノードと混ぜない）
    execute_graph_query(graph, "MATCH (e:__Entity__:Section) WHERE e.method IS NULL OR e.method <> $m REMOVE e:Section SET e:SectionRef", params={"m": METHOD})
    # 冪等: 規則が作った節の層だけを毎回作り直す
    execute_graph_query(graph, "MATCH (c:Chunk)-[r:REFERS_TO]->(s:Section) WHERE s.method = $m DELETE r", params={"m": METHOD})
    execute_graph_query(graph, "MATCH (s:Section) WHERE s.method = $m DETACH DELETE s", params={"m": METHOD})
    for batch in batches(list(sections.values())):
        execute_graph_query(graph, """
            UNWIND $rows AS row
            CREATE (s:Section {id: row.id})
            SET s.fileName = row.fileName, s.title = row.title, s.path = row.path, s.level = row.level, s.form = row.form,
                s.number = row.number, s.control = row.control, s.first_position = row.first_position, s.offset = row.offset,
                s.source = row.source, s.text = row.text, s.text_len = row.text_len, s.method = $m""",
            params={"m": METHOD, "rows": [{k: v for k, v in s.items() if k not in ("parent",)} for s in batch]})
    execute_graph_query(graph, """
        UNWIND $rows AS row
        MATCH (s:Section {id: row.id}) MATCH (d:Document {fileName: row.fileName})
        MERGE (d)-[:HAS_SECTION]->(s)""", params={"rows": [{"id": s["id"], "fileName": s["fileName"]} for s in sections.values() if not s["parent"]]})
    execute_graph_query(graph, """
        UNWIND $rows AS row
        MATCH (s:Section {id: row.id}) MATCH (p:Section {id: row.parent})
        MERGE (p)-[:HAS_SECTION]->(s)""", params={"rows": [{"id": s["id"], "parent": s["parent"]} for s in sections.values() if s["parent"]]})
    for batch in batches([{"c": c, "s": sid, "p": primary} for c, lst in chunk_sections.items() for sid, primary in lst]):
        execute_graph_query(graph, """
            UNWIND $rows AS row
            MATCH (c:Chunk {id: row.c}) MATCH (s:Section {id: row.s})
            MERGE (c)-[r:IN_SECTION]->(s) SET r.primary = row.p""", params={"rows": batch})
    for batch in batches([{"c": k[0], "s": k[1], "quote": v["quote"], "via": v["via"], "src_method": v["method"],
                            "standard": v.get("standard"), "unresolved": v.get("unresolved_locator")} for k, v in section_refs.items()]):
        execute_graph_query(graph, """
            UNWIND $rows AS row
            MATCH (c:Chunk {id: row.c}) MATCH (s:Section {id: row.s})
            MERGE (c)-[r:REFERS_TO {level: "section"}]->(s)
            SET r.method = $m, r.quote = row.quote, r.via = row.via, r.source_method = row.src_method,
                r.standard = row.standard, r.unresolved_locator = row.unresolved""",
            params={"m": METHOD, "rows": batch})
    logging.info("section layer: %s", json.dumps(summary, ensure_ascii=False))
    return {"summary": summary}
