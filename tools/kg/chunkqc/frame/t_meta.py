import sys, glob
sys.path.insert(0, "/work/src")
import frame_pages
for path in ["/docs/試運転要領/HS-VTR-T001E.pdf", "/docs/RHAT/HS-RU-002F.pdf", glob.glob("/docs/TPS/HS-TP-008*.pdf")[0], glob.glob("/docs/RHAT/HS-RU-010*.pdf")[0], glob.glob("/docs/TPS/HS-TP-021*.pdf")[0], glob.glob("/docs/TPS/HS-TP-002*.pdf")[0]]:
    res = frame_pages.analyze(path)
    p = next((p for p in res["pages"] if p["kind"] == "body"), None)
    m = (p or {}).get("meta") or {}
    print(f"{path.split('/')[-1][:22]:22s} number={m.get('number')} rev={m.get('revision')} title={m.get('title')!r} history={len(m.get('history') or [])} {[(h['revision'], h['date']) for h in (m.get('history') or [])][:6]}")
