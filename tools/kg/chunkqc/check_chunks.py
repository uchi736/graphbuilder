# 59 本の PDF を Graph Builder と同じ経路（pymupdf4llm → 見出しで分割 → 512 トークン）でチャンクにし、質を数える。
# backend コンテナの中で実行する（トークナイザと変換器が揃っている）。Neo4j にも LLM にも触れない。
import glob, json, os, re, sys, unicodedata, statistics, logging, collections
logging.disable(logging.WARNING)
sys.path.insert(0, "/code")
os.environ.setdefault("CHUNK_TOKENIZER", "/code/tokenizers/ruri-v3-310m/tokenizer.json")
import pymupdf
from langchain_core.documents import Document
from src.document_sources.pdf_markdown import load_pdf_as_markdown
from src.create_chunks import CreateChunksofDocument, chunk_length_function
length = chunk_length_function()
FRAME = ["配布先", "承認", "審査", "改正回数", "改正番号", "来歴", "来 歴", "作成", "控", "原", "合計", "年 月 日", "番", "号"]
HEAD = re.compile(r"^\s*(\d{1,2}[.．]\s*[^\d\s].{0,30}|\d{1,2}[-－]\d{1,2}\s*\S.{0,30}|[（(]\d{1,2}[)）]\s*\S.{0,30}|別表\s*\d|注\s*\d|附則|適用範囲|関連基準)\s*$")
def norm(s): return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))
rows = []; dump = {}
for folder in ("RHAT", "TPS", "VTRT"):
    for path in sorted(glob.glob(f"/tmp/hs/{folder}/*.pdf")):
        name = os.path.basename(path); row = {"folder": folder, "file": name}
        try:
            with pymupdf.open(path) as doc:
                raw = "".join(p.get_text() for p in doc); row["pages"] = len(doc)
                tables = sum(len(p.find_tables().tables) for p in doc)
        except Exception as e:
            row["error"] = f"読めない: {e}"; rows.append(row); continue
        raw_n = norm(raw); row["raw_chars"] = len(raw_n); row["tables_detected"] = tables
        heads = [l.strip() for l in raw.splitlines() if HEAD.match(l)]
        row["headings_in_text"] = len(heads)
        pages = load_pdf_as_markdown(path)
        if not pages:
            row["note"] = "文字層なし（Markdown の経路に入らない）"; rows.append(row); continue
        if isinstance(pages, Document): pages = [pages]
        chunks = CreateChunksofDocument(pages, None).split_file_into_chunks(512, 50, "test@example.com")
        bodies = []; secs = set(); n_tbl = 0; n_frame = 0; n_frame_head = 0; toks = []
        for c in chunks:
            text = c.page_content; sec = c.metadata.get("section") or ""
            body = re.sub(r"^\[[^\]]*\]\n", "", text, count=1)
            bodies.append(body); secs.add(sec); toks.append(length(text))
            if "|---" in body: n_tbl += 1
            if sum(1 for k in FRAME if k in body) >= 3: n_frame += 1
            if re.match(r"^\s*(番|号|配布先|HS\s*$)", body) or body[:120].count("配布先"): n_frame_head += 1
        kept = norm("".join(bodies))
        row.update({"chunks": len(chunks), "tok_median": int(statistics.median(toks)), "tok_min": min(toks), "tok_max": max(toks),
                    "tiny(<80tok)": sum(1 for t in toks if t < 80), "over512": sum(1 for t in toks if t > 512),
                    "sections": len(secs), "frame_chunks": n_frame, "frame_at_head": n_frame_head, "md_tables": n_tbl,
                    "retention": round(len(kept) / max(len(raw_n), 1), 3)})
        dump[name] = [{"section": c.metadata.get("section"), "page": c.metadata.get("page_number"), "tokens": length(c.page_content), "kind": c.metadata.get("kind"), "text": c.page_content} for c in chunks]
        dump[name + "::meta"] = pages[0].metadata.get("frame_meta")
        rows.append(row)
json.dump({"rows": rows, "chunks": dump}, open("/tmp/hs/chunk_check.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"{'folder':5s} {'file':30s} {'p':>3s} {'chunks':>6s} {'tok中央':>6s} {'最小':>4s} {'最大':>4s} {'細切':>4s} {'枠混入':>6s} {'節':>3s}/{'見出し':<4s} {'表md/検出':>8s} {'残存':>5s}")
for r in rows:
    if "chunks" not in r: print(f"{r['folder']:5s} {r['file'][:30]:30s} {r.get('pages','?'):>3} {r.get('note') or r.get('error')}"); continue
    print(f"{r['folder']:5s} {r['file'][:30]:30s} {r['pages']:3d} {r['chunks']:6d} {r['tok_median']:6d} {r['tok_min']:4d} {r['tok_max']:4d} {r['tiny(<80tok)']:4d} {r['frame_chunks']:6d} {r['sections']:3d}/{r['headings_in_text']:<4d} {r['md_tables']:4d}/{r['tables_detected']:<3d} {r['retention']:5.2f}")
agg = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    if "chunks" in r:
        a = agg[r["folder"]]; a["files"] += 1; a["chunks"] += r["chunks"]; a["tiny"] += r["tiny(<80tok)"]; a["frame"] += r["frame_chunks"]; a["sections"] += r["sections"]; a["heads"] += r["headings_in_text"]; a["mdt"] += r["md_tables"]; a["det"] += r["tables_detected"]; a["raw"] += r["raw_chars"]; a["kept"] += int(r["retention"] * r["raw_chars"])
for f, a in agg.items():
    print(f"== {f}: 文書 {a['files']}、チャンク {a['chunks']}、細切れ {a['tiny']}（{100*a['tiny']/a['chunks']:.0f}%）、枠混入 {a['frame']}（{100*a['frame']/a['chunks']:.0f}%）、節 {a['sections']} / 本文の見出し {a['heads']}、Markdown 表 {a['mdt']} / 検出した表 {a['det']}、残存 {100*a['kept']/max(a['raw'],1):.0f}%")
