# -*- coding: utf-8 -*-
"""文書層の後処理（段 3 の B2・B3）と文書台帳。LLM を呼ばず、規則だけで次を作る。何度流しても同じ結果になる。

  N1 文書番号を表すエンティティ  (:__Entity__:Standard {id: 正規形, method: "rule", names, aliases, external, ...台帳の属性})
  E1 (Document)-[:IS_VERSION_OF {rev}]->(Standard)                 ファイル名と先頭の識別子から
  E7 (Standard)-[:HAS_CURRENT_VERSION {rev, up_to_date}]->(Document) 手元の版のうち現行版（版が 1 つでもその版）
  名寄せ 正規形が一致する既存のエンティティ（LLM 抽出の VTR-T001・Hs-Vtr-T001・V-00862 など）を Standard に物理統合する
  E2 (Document)-[:REFERS_TO {method:"rule", chunks, quotes, scope, via}]->(Standard)
       参照抽出の規則（chunk_reference_rules）の other_document / external の参照と、既存のチャンク間 REFERS_TO（文書をまたぐもの）を文書単位に束ねる。
       文書番号でも外部の識別子でも当たらない名称は、台帳の名称・略称・旧名称・旧番号・正本の表の別名で引く（via = "alias"）
  E3 (Document)-[:LISTS {rev, date}]->(Standard)                    発行目録の行
  E4 (Document 新)-[:REPLACES {old_rev, new_rev}]->(Document 旧)   両方の版のファイルがある時だけ。無ければ Standard.revisions に残す
  E5 (Document)-[:ABOLISHES {date}]->(Standard)                     廃止通知票
  E6 (Document)-[:APPLIES_TO {source}]->(:__Entity__:Model {id})     「1. 適用」の型式

文書台帳の属性（Standard のプロパティ。11/10 追加）:
  official_name  正式名称（正本の表 > 発行目録の行 > 文書の題名 の順に採る）
  kind           種類（規程・規則・基準・要領・標準・方案 など。名称の語尾）
  abbreviations  略称・通称（「X（以下「Y」という。）」の X が文書のとき、「この基準は、通称を「Y」といい」、表紙の欄「| 通称 | Y |」など）
  former_names   旧名称（「旧名称「X」」「X から改称」、来歴の「名称変更（A から B に変更）」から現行の名称を置き換えて作る）
  former_numbers 旧番号（表紙の欄「| 旧番号 | X |」、旧番号の対照表、来歴の「新規制定（X）」「X を全面見直しの上」）
  aliases        LLM 抽出の表記ゆれを名寄せした元の表記（前の構築の値も引き継ぐ）
  enacted        制定日（来歴の初版の日付、YYYY-MM-DD）/ latest_rev・latest_rev_date 知られている最新の版（来歴・目録・通知票・正本の表）
  held_rev       手元の現行版の符号 / up_to_date 手元の現行版が知られている最新の版か
  正本の表から: department（所管部門）/ parent_doc（上位文書）/ registry_aliases（別名）/ registry_note / registry_updated_by・registry_updated_at / registry（表にあるか）

正本の表（src/document_registry.py、設定 DOCUMENT_REGISTRY_DSN。既定は空で使わない）の値は、文書から取り出した値より優先する。

統合前の状態と、参照の一文の解決の内訳は台帳 logs/document_layer/<時刻>.json に書く（取り込み直しでも元に戻る）。
使い方: build_document_layer(graph) -> {"summary": {...}, "ledger": パス}
       plan_document_layer(rows, cfg, ...) -> グラフを読まずに計画だけ作る（試験・件数の確認用）
"""
import collections
import datetime
import json
import logging
import os
import re

from src import document_layer_rules as R

METHOD = "rule"
_DOC_NAME = re.compile(r"(規程|規定|規則|基準|要領|要綱|細則|標準|仕様書|方案|規格|指針|手順書|マニュアル)")
LEDGER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "document_layer")
_HISTORY_RX = re.compile(r"来歴|改訂記録|改訂履歴|改正履歴|通知票|改正記録")
TOP_N = 10


def execute_graph_query(graph, query, params=None):
    # 遅れて読む（plan_document_layer をグラフ・backend の依存なしで試験できるように）
    from src.shared.common_fn import execute_graph_query as _q
    return _q(graph, query, params=params) if params is not None else _q(graph, query)


def _rows(graph):
    return execute_graph_query(graph, """
        MATCH (c:Chunk)-[:PART_OF]->(d:Document)
        RETURN c.id AS id, d.fileName AS fileName, c.position AS position, c.text AS text,
               c.page_number AS page_number, c.section AS section
        ORDER BY d.fileName, c.position""")


def _squeeze_name(text):
    return re.sub(r"\s+", "", R.nfkc(text or "")).upper()


def _clean_name(text):
    """名称の表示形: NFKC にし、空白を 1 つに。和文の字の間の空白（PDF の改行の名残）は詰める。"""
    t = re.sub(r"\s+", " ", R.nfkc(text or "")).strip().strip("|").strip()
    return re.sub(r"(?<=[぀-ヿ㐀-鿿])\s(?=[぀-ヿ㐀-鿿])", "", t)


def pick_external_ids(forms, existing=()):
    """手元に無い文書の識別子の表記ゆれ（external_key が同じもの）ごとに、Standard の id を 1 つ選ぶ。
    forms: {表記: 出現回数}。existing: すでにグラフにある規則の Standard の id。
    選び方: 既存の id がちょうど 1 つならそれ（作り直しても id を変えない）。無いか複数なら、出現の多い表記、同じなら長い表記（空白入りの読みやすい形）、
    それも同じなら文字列の順。戻り値: {鍵: id}"""
    groups = collections.defaultdict(collections.Counter)
    for f, n in forms.items():
        groups[R.external_key(f)][R.external_id(f)] += n
    ex = collections.defaultdict(set)
    for e in existing:
        ex[R.external_key(e)].add(e)
    out = {}
    for key, cnt in groups.items():
        olds = sorted(ex.get(key, ()))
        if len(olds) == 1:
            out[key] = olds[0]
            continue
        cands = sorted(cnt.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
        if olds:   # 既存が複数（11/10 以前の HZTL 20559 と HZTL20559）: 既存の中から同じ規則で選ぶ
            olds_cnt = {o: cnt.get(R.external_id(o), 0) for o in olds}
            out[key] = sorted(olds, key=lambda o: (-olds_cnt[o], -len(o), o))[0]
        else:
            out[key] = cands[0][0]
    return out


def _load_registry():
    from src.document_registry import load_registry
    return load_registry()


def plan_document_layer(rows, cfg, chunk_links=(), entities=(), rule_standards=(), registry=None):
    """グラフを読まずに文書層の計画を作る。
    rows: チャンク [{"id", "fileName", "position", "text", "section"}]
    chunk_links: 文書をまたぐチャンク間 REFERS_TO [{"src", "src_doc", "tgt_doc", "quote"}]
    entities: 名寄せの候補（規則の Standard 以外のエンティティ）[{"id", "labels", "eid", "description"}]
    rule_standards: すでにある規則の Standard [{"id", "eid", "aliases", "external"}]（id を変えないため・表記ゆれの古い Standard を寄せるため）
    registry: 正本の表の行の一覧（document_registry.load_registry の戻り値）または None"""
    by_file = collections.defaultdict(list)
    for r in rows:
        by_file[r["fileName"]].append(r)
    for cs in by_file.values():
        cs.sort(key=lambda c: (c.get("position") is None, c.get("position") or 0))
    summary = {"documents": len(by_file), "chunks": len(rows)}

    # ---- N1/E1: ファイルごとの文書番号と版 ----
    standards = {}

    def S(no, external=False):
        s = standards.setdefault(no, {"names": set(), "aliases": set(), "external": external, "revisions": {},
                                      "abbreviations": set(), "former_names": set(), "former_numbers": set(),
                                      "catalog_names": [], "title_names": set(), "quoted_names": collections.Counter(),
                                      "latest_hints": [], "registry": None})
        if not external:
            s["external"] = False
        return s
    version_of = {}   # fileName -> (no, rev)
    for f, cs in by_file.items():
        no, rev = R.number_of_file(f, cfg)
        head = cs[0]["text"][:300] if cs else ""
        if not no and not R.is_catalog_file(f, head, cfg):
            # ファイル名に番号が無いときは先頭の文から拾う。ただし番号が 1 種類だけのときに限る
            # （目録や関連文書の一覧のように先頭に複数並ぶと、どれがその文書の番号か決められない。分割を変えると先頭のチャンクも変わる）
            found = R.numbers_in(head, cfg)
            no, rev = found[0] if len(found) == 1 else (None, None)
        if no:
            S(no)
            version_of[f] = (no, rev)
    summary["is_version_of"] = len(version_of)

    # ---- 参照抽出の規則（段 0 の目録と、文書をまたぐ参照）----
    from src.chunk_reference_rules import find_references
    res = find_references(rows)
    for f, cat in res["catalogue"].items():
        for name in cat.get("names", []):
            if f in version_of and name and not R.is_control_heading(name, cfg) and _DOC_NAME.search(name):
                S(version_of[f][0])["names"].add(name)
                S(version_of[f][0])["title_names"].add(name)

    # 手元に無い文書の識別子の表記ゆれ: 鍵ごとに id を 1 つ選ぶ（V-00862 と V00862、HZTL 20559 と HZTL20559）
    ext_forms = collections.Counter()
    for ref in res["references"]:
        w = ref.get("target_document")
        if ref["scope"] == "external" and w and not R.normalize_number(w, cfg, strict=True)[0] and R.is_external_identifier(w, cfg):
            ext_forms[w] += 1
    for e in entities:
        if not R.normalize_number(e["id"], cfg, strict=True)[0] and R.is_external_identifier(e["id"], cfg):
            ext_forms.setdefault(e["id"], 0)
    existing_ext = [s["id"] for s in rule_standards if s.get("external")]
    for sid in existing_ext:
        ext_forms.setdefault(sid, 0)
    ext_id = pick_external_ids(ext_forms, existing_ext)

    def external_standard_id(text):
        return ext_id.get(R.external_key(text)) or R.external_id(text)

    # ---- E3〜E6: 目録・来歴・通知票・適用 ----
    lists = []     # (fileName, no, rev, date, name)
    notices = []   # (fileName, dict)
    histories = {} # fileName -> [rows]
    applies = {}   # fileName -> [models]
    abbr_found, former_found = [], []   # (fileName, dict)
    for f, cs in by_file.items():
        is_catalog = R.is_catalog_file(f, cs[0]["text"] if cs else "", cfg)
        if is_catalog:
            for c in cs:
                for row in R.parse_catalog(c["text"], cfg):
                    S(row["number"])
                    if row.get("name"):
                        standards[row["number"]]["names"].add(row["name"])
                        standards[row["number"]]["catalog_names"].append(row["name"])
                    if row.get("latest_rev"):
                        standards[row["number"]]["latest_hints"].append((row["latest_rev"], row.get("latest_date")))
                    lists.append((f, row["number"], row["latest_rev"], row["latest_date"], row.get("name")))
        own_no = version_of.get(f, (None,))[0]
        for i, c in enumerate(cs):
            text = c["text"] or ""
            n = R.parse_notice(text, cfg)
            if n:
                notices.append((f, n))
                S(n["number"])
            if f in version_of and "来歴" in text and not standards[version_of[f][0]].get("enacted_hint"):
                standards[version_of[f][0]]["enacted_hint"] = R.parse_enacted(text[text.find("来歴"):])
            if f in version_of and ("来歴" in text):
                h = R.parse_history(text, cfg)
                if h:
                    histories.setdefault(f, [])
                    for x in h:
                        if x["rev"] not in {y["rev"] for y in histories[f]}:
                            histories[f].append(x)
            in_history = bool(_HISTORY_RX.search(text))
            for a in R.parse_abbreviations(text, cfg, cover=(i == 0)):
                abbr_found.append((f, a))
            for x in R.parse_former_names(text, cfg, own_number=own_no, in_history=in_history, cover=(i == 0)):
                former_found.append((f, x))
        for c in cs[:3]:
            models = R.parse_applies(c["text"], cfg)
            if models:
                applies[f] = models
                break
    for f, h in histories.items():
        no = version_of[f][0]
        for x in h:
            standards[no]["revisions"][x["rev"]] = x["date"]
    for f, n in notices:
        if n["kind"] == "改訂" and n["new_rev"]:
            standards[n["number"]]["revisions"].setdefault(n["new_rev"], n["date"])
            standards[n["number"]]["latest_hints"].append((n["new_rev"], n["date"]))
    summary.update({"lists": len(lists), "notices": len(notices), "histories": len(histories), "applies": sum(len(m) for m in applies.values())})

    def standard_of_text(text):
        """文中の文書番号・外部の識別子から Standard の id（台帳に無ければ None）。"""
        nums = R.document_numbers_in(text, cfg)
        if nums:
            return nums[-1] if nums[-1] in standards else None
        for m in R._IDENT_RX.finditer(R.nfkc(text)):
            if R.is_external_identifier(m.group(0), cfg):
                sid = external_standard_id(m.group(0))
                return sid if sid in standards else None
        return None

    name_owner = collections.defaultdict(set)
    for no, s in standards.items():
        for nm in s["names"]:
            name_owner[_squeeze_name(nm)].add(no)

    # ---- 略称・旧名称・旧番号 ----
    stats = collections.Counter()
    unresolved_abbr = []
    for f, a in abbr_found:
        own = version_of.get(f, (None,))[0]
        ante = a["antecedent"]
        target = None
        if a["how"] == "cover" or R.is_self_reference(ante, cfg):
            target = own
        elif ante:
            target = standard_of_text(ante)
            if not target and R.looks_like_document_name(ante, cfg):
                owners = name_owner.get(_squeeze_name(ante), set())
                if len(owners) == 1:
                    target = next(iter(owners))
                else:
                    unresolved_abbr.append({"file": f, "antecedent": ante, "abbr": a["abbr"], "how": a["how"]})
                    stats["abbr_unresolved"] += 1
                    continue
        if not target:
            stats["abbr_terms_skipped"] += 1   # 文書ではない語の定義（「単独運転装置（以下「運転台」という。）」）
            continue
        standards[target]["abbreviations"].add(_clean_name(a["abbr"]))
        stats["abbr_found"] += 1
    for f, x in former_found:
        own = version_of.get(f, (None,))[0]
        target = own
        if x.get("target"):
            t_no, _ = R.normalize_number(x["target"], cfg)
            target = t_no if t_no in standards else standard_of_text(x["target"])
        if not target:
            stats["former_unresolved"] += 1
            continue
        s = standards[target]
        if x["kind"] == "number":
            s["former_numbers"].add(re.sub(r"\s+", " ", R.nfkc(x["former"])).strip())
        elif x["kind"] == "name":
            s["former_names"].add(_clean_name(x["former"]))
        elif x["kind"] == "rename_word":
            made = {nm.replace(x["new_word"], x["former"]) for nm in [_official_name(s)] if nm and x["new_word"] in nm}
            if not made:
                continue
            s["former_names"].update(made)
        stats["former_" + x["kind"]] += 1

    # 一文が指す手元に無い文書の Standard を先に作る（正本の表だけにある文書かどうかを、下で数えるため）
    for ref in res["references"]:
        w = ref.get("target_document")
        if ref["scope"] != "external" or not w:
            continue
        num, _ = R.normalize_number(w, cfg, strict=True)
        if num:
            S(num, external=num not in standards or standards[num]["external"])
        elif R.is_external_identifier(w, cfg):
            sid = external_standard_id(w)
            S(sid, external=True)
            nm = R.name_near_identifier(ref.get("quote"), w, cfg)   # 「QC-KAI-027初品管理規定」の名称（正式名称の候補・別名として引く）
            if nm:
                standards[sid]["quoted_names"][_clean_name(nm)] += 1

    # ---- 正本の表（あれば表の値を優先）----
    registry_rows = 0
    registry_created = 0
    for row in registry or []:
        raw = (row.get("doc_number") or "").strip()
        if not raw:
            continue
        no, _ = R.normalize_number(raw, cfg, strict=True)
        if not no:
            no = external_standard_id(raw) if R.is_external_identifier(raw, cfg) else re.sub(r"\s+", " ", R.nfkc(raw)).strip()
        if no not in standards:
            registry_created += 1
            S(no, external=True)
        standards[no]["registry"] = row
        registry_rows += 1
    summary["registry_rows"] = registry_rows
    summary["registry_created"] = registry_created

    # ---- 参照の一文ごとの解決 ----
    alias_index = collections.defaultdict(set)   # 鍵 -> Standard の id

    def add_alias(key, no):
        if key and len(key) >= 3:
            alias_index[key].add(no)
    for no, s in standards.items():
        add_alias(R.external_key(no), no)
        reg = s["registry"] or {}
        words = set(s["names"]) | s["abbreviations"] | s["former_names"] | s["former_numbers"] | set(reg.get("aliases") or []) | set(s["quoted_names"])
        if reg.get("official_name"):
            words.add(reg["official_name"])
        for w in words:
            add_alias(_squeeze_name(w), no)
            if re.search(r"\d", w):
                add_alias(R.external_key(w), no)

    def by_alias(text):
        hits = alias_index.get(_squeeze_name(text), set()) | (alias_index.get(R.external_key(text), set()) if re.search(r"\d", text or "") else set())
        return next(iter(hits)) if len(hits) == 1 else None

    resolution = collections.Counter()
    unmatched = {"name": collections.Counter(), "identifier": collections.Counter(), "other": collections.Counter()}
    unmatched_examples = {}
    doc_refs = collections.defaultdict(lambda: {"chunks": set(), "quotes": [], "scope": None, "via": set()})

    def add_ref(key, ref, via):
        doc_refs[key]["chunks"].add(ref["source"])
        doc_refs[key]["via"].add(via)
        q = (ref.get("quote") or "").strip()
        if q and q not in doc_refs[key]["quotes"] and len(doc_refs[key]["quotes"]) < 3:
            doc_refs[key]["quotes"].append(q[:120])

    for ref in res["references"]:
        src = ref["source_doc"]
        if ref["scope"] == "same_document":
            continue
        w = (ref.get("target_document") or "").strip()
        if ref["scope"] == "other_document":
            resolution["resolved_document"] += 1
            if ref.get("target_doc") in version_of:
                no = version_of[ref["target_doc"]][0]
                doc_refs[(src, no)]["scope"] = "other_document"
                add_ref((src, no), ref, "number" if R.document_numbers_in(w, cfg) else "name")
            continue
        no, via = None, "number"
        if ref["scope"] == "external" and w:
            num, _ = R.normalize_number(w, cfg, strict=True)
            if num:
                S(num, external=num not in standards or standards[num]["external"])
                no = num
                resolution["external_created" if standards[num]["external"] else "resolved_document"] += 1
            elif R.is_external_identifier(w, cfg):
                no, via = external_standard_id(w), "identifier"
                S(no, external=True)
                resolution["external_created"] += 1
        if no is None and w and ref["scope"] is None:
            # 規則（chunk_reference_rules）が文書を決められなかった一文でも、表記に台帳の文書番号があればそれへ結ぶ
            # （同じ文書番号の版が 2 つ取り込まれていると、目録の識別子が 2 つの文書に当たり「複数」で決められない）
            nums = [x for x in R.document_numbers_in(w, cfg) if x in standards]
            if len(nums) == 1:
                no = nums[0]
                resolution["external_created" if standards[no]["external"] else "resolved_document"] += 1
        if no is None and w:
            # 文書番号でも外部の識別子でもない（名称だけ、接頭が設定に無い識別子、規則で決められなかったもの）: 台帳の名称・略称・旧名称・旧番号・表の別名で引く
            no, via = by_alias(w), "alias"
            if no:
                resolution["resolved_alias"] += 1
            elif ref["scope"] is None and "複数" in (ref.get("reason") or ""):
                resolution["ambiguous"] += 1
                continue
            else:
                shape = "name" if R.looks_like_document_name(w, cfg) else ("identifier" if re.search(r"\d", w) and re.search(r"[A-Za-z]", w) else "other")
                resolution["unmatched_" + shape] += 1
                key = _clean_name(w)
                unmatched[shape][key] += 1
                unmatched_examples.setdefault((shape, key), {"source_doc": src, "quote": (ref.get("quote") or "")[:120]})
                continue
        if no is None:
            resolution["unmatched_other"] += 1
            continue
        key = (src, no)
        doc_refs[key]["scope"] = "external" if standards[no]["external"] else "other_document"
        add_ref(key, ref, via)
    # 既存のチャンク間 REFERS_TO（LLM や規則が張ったもの）で文書をまたぐものも根拠に加える
    for r in chunk_links:
        if r["tgt_doc"] in version_of:
            key = (r["src_doc"], version_of[r["tgt_doc"]][0])
            doc_refs[key]["scope"] = doc_refs[key]["scope"] or "other_document"
            doc_refs[key]["chunks"].add(r["src"])
            doc_refs[key]["via"].add("chunk_link")
            q = (r.get("quote") or "").strip()
            if q and q not in doc_refs[key]["quotes"] and len(doc_refs[key]["quotes"]) < 3:
                doc_refs[key]["quotes"].append(q[:120])
    # 自分の文書番号への参照は除く
    doc_refs = {k: v for k, v in doc_refs.items() if version_of.get(k[0], (None,))[0] != k[1]}
    # 目録が一覧に載せた文書との関係は LISTS で表す。目録のチャンクから一覧の各行へ張られたチャンク間リンクがあっても、文書どうしの REFERS_TO にはしない
    listed = {(f, no) for f, no, _rev, _date, _name in lists}
    summary["refers_to_listed_skipped"] = sum(1 for k in doc_refs if k in listed)
    doc_refs = {k: v for k, v in doc_refs.items() if k not in listed}
    summary["refers_to"] = len(doc_refs)
    summary["refers_to_external"] = sum(1 for v in doc_refs.values() if v["scope"] == "external")
    summary["refers_to_alias"] = sum(1 for v in doc_refs.values() if "alias" in v["via"])

    total = sum(resolution.values())
    resolved = resolution["resolved_document"] + resolution["external_created"] + resolution["resolved_alias"]
    summary["resolution"] = {
        "references": total, **{k: resolution[k] for k in ("resolved_document", "external_created", "resolved_alias", "ambiguous",
                                                         "unmatched_name", "unmatched_identifier", "unmatched_other")},
        "rate": round(resolved / total, 3) if total else None,
        "unmatched_names_top": unmatched["name"].most_common(TOP_N),
        "unmatched_identifiers_top": unmatched["identifier"].most_common(TOP_N),
        "unmatched_other_top": unmatched["other"].most_common(TOP_N),
    }
    resolution_detail = {shape: [{"text": k, "count": n, **unmatched_examples[(shape, k)]} for k, n in cnt.most_common()]
                         for shape, cnt in unmatched.items()}
    resolution_detail["unresolved_abbreviations"] = unresolved_abbr

    # ---- 名寄せの対象（既存のエンティティで、正規形が一致するもの）----
    merge_plan = collections.defaultdict(list)   # no -> [ent]
    for e in entities:
        no, _ = R.normalize_number(e["id"], cfg, strict=True)
        if not no and R.is_external_identifier(e["id"], cfg):
            no = ext_id.get(R.external_key(e["id"]))   # 外部の識別子（V-00862 → V00862）。11/10 まで名寄せが拾わなかった
        if no and no in standards:
            merge_plan[no].append(e)
            standards[no]["aliases"].add(e["id"])
    # 規則の Standard の表記ゆれ（11/10 以前に別々に作られた HZTL 20559 と HZTL20559）: 選んだ id へ寄せる
    for s in rule_standards:
        if s["id"] in standards:
            standards[s["id"]]["aliases"].update(a for a in (s.get("aliases") or []) if a)
            continue
        if s.get("external"):
            no = ext_id.get(R.external_key(s["id"]))
            if no and no in standards and no != s["id"]:
                merge_plan[no].append({"id": s["id"], "labels": ["__Entity__", "Standard"], "eid": s["eid"], "description": None, "rule_variant": True})
                standards[no]["aliases"].add(s["id"])
                standards[no]["aliases"].update(a for a in (s.get("aliases") or []) if a)
    for no, s in standards.items():
        s["aliases"].discard(no)
    summary["merge_candidates"] = sum(len(v) for v in merge_plan.values())
    summary["merge_rule_variants"] = sum(1 for v in merge_plan.values() for e in v if e.get("rule_variant"))

    # ---- 台帳の属性の確定（正本の表 > 文書）と現行版 ----
    by_no = collections.defaultdict(list)
    for f, (no, rev) in version_of.items():
        by_no[no].append((rev or "", f))
    current = {}   # no -> {"file", "rev", "up_to_date"}
    for no, s in standards.items():
        reg = s["registry"] or {}
        s["official_name"] = (reg.get("official_name") or "").strip() or _official_name(s)
        s["kind"] = (reg.get("kind") or "").strip() or (R.document_kind(s["official_name"], cfg) if s["official_name"] else None)
        revs = [(r, d) for r, d in s["revisions"].items()] + list(s["latest_hints"]) + [(r, None) for r, _f in by_no.get(no, []) if r]
        latest = max(revs, key=lambda x: (R.rev_rank(x[0]), R.iso_date(x[1]) or "")) if revs else (None, None)
        s["latest_rev"] = (reg.get("current_rev") or "").strip() or latest[0]
        s["latest_rev_date"] = R.iso_date(s["revisions"].get(s["latest_rev"]) or (latest[1] if latest[0] == s["latest_rev"] else None)) if s["latest_rev"] else None
        s["enacted"] = R.iso_date(s["revisions"].get("0")) or s.get("enacted_hint")
        held = by_no.get(no)
        if held:
            want = (reg.get("current_rev") or "").strip().upper()
            pick = [x for x in held if want and (x[0] or "0").upper() == want]
            rev, f = pick[0] if pick else max(held, key=lambda x: (R.rev_rank(x[0]), R.iso_date(s["revisions"].get(x[0])) or "", x[1]))
            up = R.rev_rank(rev) >= R.rev_rank(s["latest_rev"]) if s["latest_rev"] else None
            current[no] = {"file": f, "rev": rev or None, "up_to_date": up}
    summary["current_versions"] = len(current)
    summary["current_not_latest"] = sum(1 for c in current.values() if c["up_to_date"] is False)
    summary["ledger_fields"] = {
        "official_names": sum(1 for s in standards.values() if s["official_name"]),
        "kinds": sum(1 for s in standards.values() if s["kind"]),
        "abbreviations": sum(len(s["abbreviations"]) for s in standards.values()),
        "former_names": sum(len(s["former_names"]) for s in standards.values()),
        "former_numbers": sum(len(s["former_numbers"]) for s in standards.values()),
        "enacted": sum(1 for s in standards.values() if s["enacted"]),
        "abbr_terms_skipped": stats["abbr_terms_skipped"], "abbr_unresolved": stats["abbr_unresolved"], "former_unresolved": stats["former_unresolved"],
    }
    return {"summary": summary, "standards": standards, "version_of": version_of, "doc_refs": doc_refs, "lists": lists, "notices": notices,
            "applies": applies, "merge_plan": dict(merge_plan), "current": current, "by_no": dict(by_no), "by_file": by_file,
            "resolution_detail": resolution_detail}


def _official_name(s):
    """正式名称: 発行目録の行の名称 > 文書の題名（目録の名称のうち種類の語を含む最長のもの）> 外部の文書なら一文で識別子に添えた名称（最も多いもの）。"""
    if s["catalog_names"]:
        return _clean_name(collections.Counter(s["catalog_names"]).most_common(1)[0][0])
    titles = sorted(s["title_names"], key=lambda n: (-len(n), n))
    if titles:
        return _clean_name(titles[0])
    if s["quoted_names"]:
        return sorted(s["quoted_names"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return None


def _standard_row(no, v):
    reg = v["registry"] or {}
    return {"id": no, "names": sorted(v["names"]), "external": v["external"], "aliases": sorted(v["aliases"]),
            "revisions": json.dumps(v["revisions"], ensure_ascii=False) if v["revisions"] else None,
            "description": ("手元に無い文書 " if v["external"] else "文書番号 ") + no + (": " + sorted(v["names"])[0] if v["names"] else (": " + v["official_name"] if v.get("official_name") else "")),
            "official_name": v.get("official_name"), "kind": v.get("kind"),
            "abbreviations": sorted(v["abbreviations"]), "former_names": sorted(v["former_names"]), "former_numbers": sorted(v["former_numbers"]),
            "enacted": v.get("enacted"), "latest_rev": v.get("latest_rev"), "latest_rev_date": v.get("latest_rev_date"),
            "department": reg.get("department") or None, "parent_doc": reg.get("parent_doc") or None,
            "registry_aliases": sorted(set(reg.get("aliases") or [])), "registry_note": reg.get("note") or None,
            "registry_updated_by": reg.get("updated_by") or None, "registry_updated_at": reg.get("updated_at") or None,
            "registry": bool(v["registry"])}


def build_document_layer(graph, write=True, config_path=None, registry=None):
    cfg = R.load_config(config_path)
    rows = _rows(graph)
    chunk_links = execute_graph_query(graph, """
        MATCH (a:Chunk)-[r:REFERS_TO]->(b:Chunk) WHERE a.fileName <> b.fileName
        RETURN a.id AS src, a.fileName AS src_doc, b.fileName AS tgt_doc, r.quote AS quote""")
    ents = execute_graph_query(graph, """
        MATCH (e:__Entity__) WHERE NOT e:Document AND NOT e:Chunk AND (e.method IS NULL OR e.method <> $m)
        RETURN e.id AS id, labels(e) AS labels, elementId(e) AS eid, e.description AS description""", params={"m": METHOD})
    rule_standards = execute_graph_query(graph, """
        MATCH (s:Standard) WHERE s.method = $m
        RETURN s.id AS id, elementId(s) AS eid, s.aliases AS aliases, s.external AS external""", params={"m": METHOD})
    registry_status = "given" if registry is not None else None
    if registry is None:
        registry, registry_status = _load_registry()
    plan = plan_document_layer(rows, cfg, chunk_links=chunk_links, entities=ents, rule_standards=rule_standards, registry=registry)
    summary = plan["summary"]
    summary["registry"] = registry_status
    standards, version_of, doc_refs, lists, notices, applies, merge_plan, current = (
        plan[k] for k in ("standards", "version_of", "doc_refs", "lists", "notices", "applies", "merge_plan", "current"))
    if not write:
        return {"summary": summary, "standards": {k: _standard_row(k, v) for k, v in standards.items()},
                "refers_to": [(k[0], k[1], len(v["chunks"]), v["scope"]) for k, v in doc_refs.items()], "lists": lists, "notices": notices,
                "applies": applies, "merge_plan": merge_plan, "current": current, "resolution_detail": plan["resolution_detail"]}

    # ---- 台帳（統合前の状態と、参照の一文の解決の内訳）----
    os.makedirs(LEDGER_DIR, exist_ok=True)
    ledger_path = os.path.join(LEDGER_DIR, datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    ledger = {"merged": [], "standards": sorted(standards), "summary": summary, "resolution": plan["resolution_detail"],
              "current_versions": {no: c for no, c in sorted(current.items())}}
    for no, es in merge_plan.items():
        for e in es:
            rels = execute_graph_query(graph, """
                MATCH (e) WHERE elementId(e) = $eid
                MATCH (e)-[r]-(x) RETURN type(r) AS type, startNode(r) = e AS outgoing, labels(x) AS labels, coalesce(x.id, x.fileName) AS other, properties(r) AS props""",
                params={"eid": e["eid"]})
            ledger["merged"].append({"into": no, "id": e["id"], "labels": e["labels"], "description": e.get("description"), "relationships": [dict(r) for r in rels]})
    # 同じ id で別の型のエンティティ（抽出が作ったもの。例: 型 DocumentRef の V00858）は、下の MERGE で Standard として引き取る。台帳に残す
    adopted = execute_graph_query(graph, """
        MATCH (e:__Entity__) WHERE e.id IN $ids AND NOT e:Standard
        RETURN e.id AS id, labels(e) AS labels, elementId(e) AS eid""", params={"ids": sorted(standards)})
    ledger["adopted"] = [dict(r) for r in adopted]
    summary["adopted"] = len(adopted)
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1, default=str)

    # ---- 書き込み（冪等: 規則が張ったエッジは消してから作り直す）----
    execute_graph_query(graph, "MATCH (:Document)-[r:IS_VERSION_OF|LISTS|ABOLISHES|APPLIES_TO|REPLACES]->() WHERE r.method = $m DELETE r", params={"m": METHOD})
    execute_graph_query(graph, "MATCH (:Document)-[r:REFERS_TO]->(:Standard) WHERE r.method = $m DELETE r", params={"m": METHOD})
    execute_graph_query(graph, "MATCH (:Standard)-[r:HAS_CURRENT_VERSION]->(:Document) WHERE r.method = $m DELETE r", params={"m": METHOD})
    # 一意制約は __Entity__ の id にある。ラベルを 2 つ付けて MERGE すると、同じ id で型の違うノードに一致せず、作ろうとして制約に反する。
    # そこで id だけで MERGE し、Standard のラベルを足す（型の違うノードは引き取る）
    # 規則が書いた Standard・Model は版（_v）を 1 上げる（それより前の手動の操作の取り消しが、文書層の後に
    # Document とのエッジやプロパティを古い写しへ戻さないため。docs/graph-edit/DESIGN.md の 2.3）
    execute_graph_query(graph, """
        UNWIND $rows AS row
        MERGE (s:__Entity__ {id: row.id})
        ON CREATE SET s.method = $m, s.description = row.description
        SET s:Standard, s.method = coalesce(s.method, $m), s.names = row.names, s.external = row.external,
            s.aliases = row.aliases, s.revisions = row.revisions, s._v = coalesce(s._v, 0) + 1,
            s.official_name = row.official_name, s.kind = row.kind, s.abbreviations = row.abbreviations,
            s.former_names = row.former_names, s.former_numbers = row.former_numbers, s.enacted = row.enacted,
            s.latest_rev = row.latest_rev, s.latest_rev_date = row.latest_rev_date, s.department = row.department,
            s.parent_doc = row.parent_doc, s.registry_aliases = row.registry_aliases, s.registry_note = row.registry_note,
            s.registry_updated_by = row.registry_updated_by, s.registry_updated_at = row.registry_updated_at, s.registry = row.registry""",
        params={"m": METHOD, "rows": [_standard_row(no, v) for no, v in standards.items()]})
    # 名寄せ: 物理統合（Standard を残し、属性は Standard のものを優先、エッジは全部付け替える）。型のラベルは Standard だけにする
    merged = 0
    for no, es in merge_plan.items():
        execute_graph_query(graph, """
            MATCH (s:Standard {id: $no})
            MATCH (e) WHERE elementId(e) IN $eids AND e <> s
            WITH s, collect(e) AS others
            CALL apoc.refactor.mergeNodes([s] + others, {properties: "discard", mergeRels: true}) YIELD node
            RETURN count(node)""", params={"no": no, "eids": [e["eid"] for e in es]})
        merged += len(es)
        labels = {l for e in es for l in e["labels"]} - {"__Entity__", "Standard"}
        if labels:
            execute_graph_query(graph, "MATCH (s:Standard {id: $no}) REMOVE s:" + ":".join("`%s`" % l for l in labels), params={"no": no})
        # 寄せたエンティティの埋め込み（別の表記と説明から作ったもの）を引き継がないよう消す。後の「エンティティの埋め込み」で作り直す
        execute_graph_query(graph, "MATCH (s:Standard {id: $no}) SET s.method = $m, s._v = coalesce(s._v, 0) + 1 REMOVE s.embedding", params={"no": no, "m": METHOD})
    summary["merged"] = merged
    execute_graph_query(graph, """
        MATCH (s:Standard) WHERE s.method = $m
        WITH s, [l IN labels(s) WHERE NOT l IN ['__Entity__', 'Standard']] AS extra
        WHERE size(extra) > 0
        CALL apoc.create.removeLabels(s, extra) YIELD node
        RETURN count(node)""", params={"m": METHOD})
    execute_graph_query(graph, """
        UNWIND $rows AS row
        MATCH (d:Document {fileName: row.f}) MATCH (s:Standard {id: row.no})
        MERGE (d)-[r:IS_VERSION_OF]->(s) SET r.rev = row.rev, r.method = $m""",
        params={"m": METHOD, "rows": [{"f": f, "no": no, "rev": rev} for f, (no, rev) in version_of.items()]})
    execute_graph_query(graph, """
        UNWIND $rows AS row
        MATCH (d:Document {fileName: row.f}) MATCH (s:Standard {id: row.no})
        MERGE (s)-[r:HAS_CURRENT_VERSION]->(d) SET r.rev = row.rev, r.up_to_date = row.up, r.method = $m
        SET s.held_rev = row.rev, s.up_to_date = row.up""",
        params={"m": METHOD, "rows": [{"f": c["file"], "no": no, "rev": c["rev"], "up": c["up_to_date"]} for no, c in current.items()]})
    execute_graph_query(graph, "MATCH (s:Standard) WHERE s.method = $m AND NOT (s)-[:HAS_CURRENT_VERSION]->() REMOVE s.held_rev, s.up_to_date", params={"m": METHOD})
    execute_graph_query(graph, """
        UNWIND $rows AS row
        MATCH (d:Document {fileName: row.f}) MATCH (s:Standard {id: row.no})
        MERGE (d)-[r:REFERS_TO {method: $m}]->(s) SET r.chunks = row.chunks, r.quotes = row.quotes, r.scope = row.scope, r.via = row.via""",
        params={"m": METHOD, "rows": [{"f": k[0], "no": k[1], "chunks": len(v["chunks"]), "quotes": v["quotes"], "scope": v["scope"], "via": sorted(v["via"])}
                                      for k, v in doc_refs.items()]})
    execute_graph_query(graph, """
        UNWIND $rows AS row
        MATCH (d:Document {fileName: row.f}) MATCH (s:Standard {id: row.no})
        MERGE (d)-[r:LISTS]->(s) SET r.rev = row.rev, r.date = row.date, r.name = row.name, r.method = $m""",
        params={"m": METHOD, "rows": [{"f": f, "no": no, "rev": rev, "date": date, "name": name} for f, no, rev, date, name in lists]})
    for f, n in notices:
        if n["kind"] == "廃止":
            execute_graph_query(graph, "MATCH (d:Document {fileName: $f}) MATCH (s:Standard {id: $no}) MERGE (d)-[r:ABOLISHES]->(s) SET r.date = $date, r.method = $m",
                                params={"f": f, "no": n["number"], "date": n["date"], "m": METHOD})
    # REPLACES: 同じ文書番号の別の版のファイルが両方あるときだけ（版の符号の順。0 または符号なし < A < B < …）
    replaces = 0
    for no, vs in plan["by_no"].items():
        vs = sorted(vs, key=lambda x: (R.rev_rank(x[0]), x[1]))
        for (r1, f1), (r2, f2) in zip(vs, vs[1:]):
            execute_graph_query(graph, "MATCH (a:Document {fileName: $f2}) MATCH (b:Document {fileName: $f1}) MERGE (a)-[r:REPLACES]->(b) SET r.old_rev = $r1, r.new_rev = $r2, r.method = $m",
                                params={"f1": f1, "f2": f2, "r1": r1, "r2": r2, "m": METHOD})
            replaces += 1
    summary["replaces"] = replaces
    execute_graph_query(graph, """
        UNWIND $rows AS row
        MATCH (d:Document {fileName: row.f})
        MERGE (mo:__Entity__ {id: row.model}) ON CREATE SET mo.method = $m, mo.description = "適用先の型式 " + row.model
        SET mo:Model, mo._v = coalesce(mo._v, 0) + 1
        MERGE (d)-[r:APPLIES_TO]->(mo) SET r.source = "1. 適用", r.method = $m""",
        params={"m": METHOD, "rows": [{"f": f, "model": mo} for f, ms in applies.items() for mo in ms]})
    # ---- 文書カード（検索の 1 段目で文書を絞るための要約）と埋め込み ----
    try:
        summary["document_cards"] = build_document_cards(graph, plan["by_file"], standards, version_of, applies, cfg)
    except Exception as e:  # 埋め込みサービスが止まっていても文書層そのものは完成させる
        logging.warning("document cards skipped: %s", e)
        summary["document_cards"] = "skipped: %s" % e
    summary["ledger"] = ledger_path
    ledger["summary"] = summary
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1, default=str)
    logging.info("document layer: %s", json.dumps(summary, ensure_ascii=False))
    return {"summary": summary, "ledger": ledger_path}


def document_card_text(file_name, chunks, standards, version_of, applies, cfg):
    """文書カード: 文書番号・版・名称・適用先の型式・「1. 適用」の本文・見出しの一覧。検索の 1 段目（文書を絞る）で、質問との近さを測る相手。"""
    parts = []
    no, rev = version_of.get(file_name, (None, None))
    if no:
        names = sorted(standards.get(no, {}).get("names", []))
        parts.append("文書番号 %s%s%s" % (no, (" 版 " + rev) if rev else "", (" " + names[0]) if names else ""))
    else:
        parts.append("文書 " + re.sub(r"\.[A-Za-z0-9]+$", "", file_name))
    if applies.get(file_name):
        parts.append("適用先 " + "・".join(applies[file_name]) + " 形")
    for c in chunks[:3]:
        body = re.sub(r"^\[[^\]]*\]\n", "", c["text"] or "")   # 分割時のパンくず「[文書 > 見出し]」を外す
        scope = R.scope_text(body, cfg)
        if scope:
            parts.append("適用: " + re.sub(r"\s+", " ", scope)[:300])
            break
    heads = []
    for c in chunks:
        sec = c.get("section") or ""
        parts_sec = [x.strip() for x in sec.split(" > ")][1:]
        for h in parts_sec:
            if h and h not in heads and not R.is_control_heading(h, cfg):
                heads.append(h)
    if heads:
        parts.append("見出し: " + " / ".join(heads[:40]))
    return "\n".join(parts)


def build_document_cards(graph, by_file, standards, version_of, applies, cfg):
    from src.shared.common_fn import load_embedding_model, get_value_from_env
    rows = []
    for f, cs in by_file.items():
        rows.append({"f": f, "card": document_card_text(f, cs, standards, version_of, applies, cfg)})
    embeddings, dimension = load_embedding_model(get_value_from_env("EMBEDDING_PROVIDER", "vllm"), get_value_from_env("EMBEDDING_MODEL", "cl-nagoya/ruri-v3-310m"))
    vectors = embeddings.embed_documents([r["card"] for r in rows])
    for r, v in zip(rows, vectors):
        r["vec"] = v
    execute_graph_query(graph, "UNWIND $rows AS row MATCH (d:Document {fileName: row.f}) SET d.card = row.card, d.card_embedding = row.vec", params={"rows": rows})
    execute_graph_query(graph, "CREATE VECTOR INDEX document_card_vector IF NOT EXISTS FOR (d:Document) ON (d.card_embedding) OPTIONS {indexConfig: {`vector.dimensions`: %d, `vector.similarity_function`: 'cosine'}}" % int(dimension))
    return len(rows)
