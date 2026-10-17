# Docling のレイアウト要素（種類と位置）を 1 ページ目だけ書き出す。文字は取り出すだけ。
import sys, collections
from docling.document_converter import DocumentConverter
conv = DocumentConverter()
for path in ("/tmp/lt/T001.pdf", "/tmp/lt/RU002.pdf"):
    res = conv.convert(path); doc = res.document
    print("=====", path)
    cnt = collections.Counter()
    rows = []
    for item, level in doc.iterate_items():
        label = str(getattr(item, "label", type(item).__name__))
        prov = getattr(item, "prov", None) or []
        page = prov[0].page_no if prov else None
        bb = prov[0].bbox if prov else None
        text = getattr(item, "text", "") or ""
        if hasattr(item, "export_to_markdown") and not text:
            try: text = item.export_to_markdown(doc)[:80]
            except Exception: text = ""
        cnt[label] += 1
        if page == 1: rows.append((round(bb.t) if bb else -1, label, [round(bb.l), round(bb.t), round(bb.r), round(bb.b)] if bb else None, text.replace("\n", " ")[:60]))
    print("  種類:", dict(cnt))
    for r in sorted(rows, key=lambda x: -x[0])[:45]: print(f"   {r[1]:18s} {str(r[2]):26s} | {r[3]}")
