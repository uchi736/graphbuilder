# 枠を表として読む経路のチャンク（chunk_check.json）を読み、同じ物差しで数える。読むだけ。
# 旧版（analyze_chunks.py）との違い: 管理のチャンク（kind=control）は枠の語を含んで当然なので枠混入から外す。
# 行の残存は、枠のセル（番号・配布先・来歴・承認）の行を分母から除いた「本文の行」で数える。
# 番号見出しは、本文に現れる章・節・項・別表の行のうち、Markdown の見出しになった割合で数える。
import json, re, glob, os, sys, unicodedata, collections, statistics
import pymupdf
sys.path.insert(0, "/code/src/document_sources")
import frame_pages
D = json.load(open("/work/chunk_check.json", encoding="utf-8")); chunks = D["chunks"]
def norm(s): return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))
FRAME_LINE = re.compile(r"^\s*(配布先|承認|審査|作成|控|原|合計|番|号|改正回数|改正番号\s*\d*|来\s?歴|年\s?月\s?日|HS|回[汎管品生製技][^\s]{0,4}\s*\d?|'?\d{2}\.\d{2}\.\d{2}|(ICM|IHK)?(株式会社)?(石川島汎用機械|IHI回転機械)(株式会社)?|QC-KAI-001別紙\d)\s*$")
sys.path.insert(0, "/code/src/document_sources"); import number_headings as NH
def title_like(h):
    for pat, item in ((NH.CHAPTER, False), (NH.SECTION, False), (NH.TABLE_NOTE, False), (NH.ITEM, True), (NH.SUBITEM, True)):
        m = pat.match(h)
        if m:
            t = m.groups()[-1] or ""
            return NH._is_heading_title(t, item=item)
    return False
HEAD = re.compile(r"^\s*(\d{1,2}[.．]\s*[^\d\s].{0,30}|\d{1,2}[-－]\d{1,2}\s*\S.{0,30}|[（(]?\d{1,2}[)）]\s*\S.{0,30}|(別表|別紙|付表)\s*[-－]?\s*\d|注\s*\d{0,2}\s*\S{0,30})\s*$")
MD_HEAD = re.compile(r"^#{2,6}\s+(.*)$")
def category(folder, name):
    if folder == "VTRT": return "目録" if "目録" in name else "試運転・検査"
    if "目録" in name: return "目録"
    m = re.search(r"HS-(RU|TP)-?0?([0-9１]{2,3})", name)
    if m:
        n = int(unicodedata.normalize("NFKC", m.group(2)))
        if m.group(1) == "RU": return "加工・処理" if n in (11, 14, 15, 16) else "素材・部品"
        if n in (2, 6, 7, 16, 17, 25): return "加工・処理"
        if n in (21, 23): return "受検・証明"
        return "素材・部品"
    if "VTR-P" in name or "廃止" in name or "V02673" in name: return "目録・通知・その他"
    return "報告書"
paths = {os.path.basename(p): p for d in ("RHAT", "TPS", "VTRT") for p in glob.glob(f"/docs/{d}/*.pdf")}
paths.update({os.path.basename(p): p for p in glob.glob("/docs/試運転要領/*.pdf")})
rows = []; ex = {"frame": [], "table": [], "tiny": [], "control": [], "good": []}; metas = {}
for r in D["rows"]:
    if "chunks" not in r: continue
    name = r["file"]; cs = chunks[name]; cat = category(r["folder"], name)
    metas[name] = chunks.get(name + "::meta")
    p = paths.get(name) or paths.get(name.replace("VTR-T003D", "VTR-T003 D"))
    raw = ""; frame_text = ""; kinds = collections.Counter()
    if p:
        with pymupdf.open(p) as doc: raw = "".join(pg.get_text() for pg in doc)
        fr = frame_pages.analyze(p)
        for pg in fr["pages"]:
            kinds[pg["kind"] + ("(virtual)" if pg.get("virtual") else "")] += 1
            frame_text += "\n".join((pg.get("meta") or {}).get("cells") or [])
    frame_norm = norm(frame_text)
    raw_lines = [l for l in raw.splitlines() if len(norm(l)) >= 6]
    lines = [norm(l) for l in raw_lines]
    alltext = norm("".join(re.sub(r"^\[[^\]]*\]\n", "", c["text"], count=1) for c in cs))
    def kept(line):
        n = norm(line)
        if n in alltext: return True
        toks = [norm(t) for t in line.split() if len(norm(t)) >= 2]
        return bool(toks) and all(t in alltext for t in toks)
    body_raw = [l for l in raw_lines if not FRAME_LINE.match(norm(l)) and norm(l) not in frame_norm]
    body_lines = [norm(l) for l in body_raw]
    cover_all = sum(1 for l in raw_lines if kept(l)) / max(len(raw_lines), 1)
    cover_body = sum(1 for l in body_raw if kept(l)) / max(len(body_raw), 1)
    missing = [norm(l) for l in body_raw if not kept(l)][:3]
    raw_heads = {norm(l) for l in raw.splitlines() if HEAD.match(l) and not FRAME_LINE.match(norm(l))}
    heads_titled = {h for h in raw_heads if title_like(h)}
    md_heads = set()
    for c in cs:
        for l in c["text"].splitlines():
            m = MD_HEAD.match(l)
            if m: md_heads.add(norm(m.group(1)))
    promoted = sum(1 for h in raw_heads if any(h == mh or h in mh or mh in h for mh in md_heads))
    promoted_t = sum(1 for h in heads_titled if any(h == mh or h in mh or mh in h for mh in md_heads))
    nf = 0; nt = 0; ntf = 0; tiny = 0; ncontrol = 0
    for c in cs:
        body = re.sub(r"^\[[^\]]*\]\n", "", c["text"], count=1)
        if c.get("kind") == "control":
            ncontrol += 1
            if len(ex["control"]) < 2: ex["control"].append((name, c["section"], body[:300]))
            continue
        fl = sum(1 for l in body.splitlines() if FRAME_LINE.match(l))
        if fl >= 2:
            nf += 1
            if len(ex["frame"]) < 4: ex["frame"].append((name, c["section"], body[:420]))
        if "|---" in body:
            nt += 1
            if re.search(r"\|[^\n]*(承認|審査|来\s?歴|改正回数|配布先)[^\n]*\|", body):
                ntf += 1
                if len(ex["table"]) < 2: ex["table"].append((name, c["section"], body[:420]))
        if c["tokens"] < 80:
            tiny += 1
            if len(ex["tiny"]) < 4: ex["tiny"].append((name, c["section"], body[:160]))
        elif 200 <= c["tokens"] <= 512 and fl == 0 and len(ex["good"]) < 1 and "RU-002" in name: ex["good"].append((name, c["section"], body[:420]))
    rows.append({"cat": cat, "file": name, "chunks": len(cs), "control": ncontrol, "frame": nf, "tables": nt, "tables_with_frame": ntf, "tiny": tiny,
                 "heads": len(raw_heads), "promoted": promoted, "heads_t": len(heads_titled), "promoted_t": promoted_t, "cover_all": cover_all, "cover_body": cover_body, "missing": missing, "kinds": dict(kinds),
                 "meta": {k: v for k, v in (metas[name] or {}).items() if k in ("doc_number", "revision", "title")} if metas.get(name) else None})
agg = collections.defaultdict(collections.Counter); cov = collections.defaultdict(list); cova = collections.defaultdict(list)
for x in rows:
    a = agg[x["cat"]]; a["files"] += 1; a["chunks"] += x["chunks"]; a["control"] += x["control"]; a["frame"] += x["frame"]; a["tables"] += x["tables"]; a["tf"] += x["tables_with_frame"]; a["tiny"] += x["tiny"]; a["heads"] += x["heads"]; a["promoted"] += x["promoted"]; a["heads_t"] += x["heads_t"]; a["promoted_t"] += x["promoted_t"]; cov[x["cat"]].append(x["cover_body"]); cova[x["cat"]].append(x["cover_all"])
def line(k, a, c, ca):
    body = a["chunks"] - a["control"]
    print(f"{k:12s} {a['files']:3d} {a['chunks']:5d} {a['control']:4d} {a['frame']:4d}({100*a['frame']/max(body,1):.0f}%) {a['tables']:3d} {a['tf']:3d} {a['tiny']:3d}({100*a['tiny']/max(body,1):.0f}%) {a['promoted']:3d}/{a['heads']:<4d}({100*a['promoted']/max(a['heads'],1):.0f}%) {a['promoted_t']:3d}/{a['heads_t']:<3d}({100*a['promoted_t']/max(a['heads_t'],1):.0f}%) {100*statistics.median(c):.0f}% {100*statistics.median(ca):.0f}%")
print(f"{'種類':12s} 文書 チャンク 管理 枠の独立行が2以上(本文) 表 うち枠混線 細切れ(本文) 番号の形の行→節 題の形の行→節 本文行の残存 全行の残存")
for k, a in agg.items(): line(k, a, cov[k], cova[k])
tot = collections.Counter()
for a in agg.values():
    for k, v in a.items(): tot[k] += v
line("全体", tot, [v for k in cov for v in cov[k]], [v for k in cova for v in cova[k]])
low = sorted([x for x in rows if x["cover_body"] < 0.95], key=lambda x: x["cover_body"]); print("本文行の残存が 95% 未満:", [(x["file"][:22], round(x["cover_body"], 2)) for x in low][:14])
kinds = collections.Counter()
for x in rows: kinds.update(x["kinds"])
print("ページの区分:", dict(kinds))
nums = sum(1 for x in rows if x["meta"] and x["meta"].get("doc_number")); titles = sum(1 for x in rows if x["meta"] and x["meta"].get("title"))
print(f"文書番号が読めた文書 {nums}/{len(rows)}、題名が読めた文書 {titles}/{len(rows)}")
json.dump({"rows": rows, "examples": ex}, open("/work/analysis.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for k, v in ex.items():
    for name, sec, body in v: print(f"\n### 例 {k}: {name} [{sec}]\n{body}")
