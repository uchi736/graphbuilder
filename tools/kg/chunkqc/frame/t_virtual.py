import sys, json
sys.path.insert(0, "/work/src")
import frame_pages
none = json.load(open("/work/none_pages.json"))
folder = {"RHAT": "/docs/RHAT", "TPS": "/docs/TPS", "VTRT": "/docs/試運転要領", "試運転要領": "/docs/試運転要領"}
by = {}
for r in none:
    by.setdefault((r["d"], r["f"]), []).append(r["p"])
for (d, f), pages in sorted(by.items()):
    res = frame_pages.analyze(f"{folder[d]}/{f}")
    for p in res["pages"]:
        if p["page"] in pages:
            md = p.get("markdown") or ""
            print(f"## {f[:30]:30s} p{p['page']:2d} kind={p['kind']:5s} virtual={p.get('virtual', False)!s:5s} share={p.get('body_share', '-')} chars={len(md)} tables={md.count('|---')}")
            print("   " + md[:260].replace("\n", " ⏎ "))
    if False:
        p = res["pages"][0]
        print("=== FULL RU-003 p1 ==="); print(p.get("markdown", "")[:2500]); print("=== END ===")
