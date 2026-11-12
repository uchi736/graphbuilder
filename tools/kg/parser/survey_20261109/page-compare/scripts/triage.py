"""E4B で領域の種類を判定する(onprem_regions.py の TRIAGE_PROMPT をそのまま使う)。1 本ずつ順に送る。"""
import base64, io, json, sys, time, re
from pathlib import Path
import requests
from PIL import Image
sys.path.insert(0, "/proto")
import importlib.util
spec = importlib.util.spec_from_file_location("proto", "/proto/onprem_regions.py")
# onprem_regions は fitz(PyMuPDF)を読み込むので、プロンプトだけを文字列として取り出す
src = open("/proto/onprem_regions.py", encoding="utf-8").read()
TRIAGE_PROMPT = re.search(r'TRIAGE_PROMPT = """(.*?)"""', src, re.S).group(1)
ACTION_BY_KIND = eval(re.search(r"ACTION_BY_KIND = (\{.*?\})", src, re.S).group(1))

W = Path("/w")
picks = json.load(open(W / "triage" / "picks.json", encoding="utf-8"))
url = sys.argv[1]; model = sys.argv[2]
out = []
for p in picks:
    img = Image.open(f"/img/{p['stem']}/{p['stem']}_p{p['page']:03d}.png").convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = p["bbox"]; pad = 0.005
    c = img.crop((max(0, int((x0 - pad) * w)), max(0, int((y0 - pad) * h)), min(w, int((x1 + pad) * w)), min(h, int((y1 + pad) * h))))
    name = f"{p['stem']}_p{p['page']:03d}_{p['idx']}_{p['class']}.png"
    c.save(W / "triage" / name)
    buf = io.BytesIO(); c.save(buf, format="PNG")
    body = {"model": model, "temperature": 0, "max_tokens": 200,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": TRIAGE_PROMPT.format(cls=p["class"])}]}]}
    t0 = time.time()
    r = requests.post(url.rstrip("/") + "/chat/completions", json=body, timeout=300)
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"].strip()
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        tri = json.loads(m.group(0)) if m else {}
    except Exception:
        tri = {}
    rec = dict(p, crop=name, crop_px=list(c.size), raw=raw, triage=tri, sec=round(time.time() - t0, 1),
               action_by_rule=ACTION_BY_KIND.get(tri.get("種類", ""), tri.get("扱い", "")))
    out.append(rec)
    print(name, rec["sec"], "s", raw.replace("\n", " ")[:200], flush=True)
json.dump(out, open(W / "triage" / f"triage_{model.split('/')[-1]}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
