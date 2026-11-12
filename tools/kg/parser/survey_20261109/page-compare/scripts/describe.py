"""26B で図を文章化する(onprem_regions.py の DESCRIBE_PROMPT と nearest_caption の規則をそのまま使う)。1 本ずつ。"""
import base64, io, json, re, sys, time, glob
from pathlib import Path
import requests
from PIL import Image
src = open("/proto/onprem_regions.py", encoding="utf-8").read()
DESCRIBE_PROMPT = eval(re.search(r"DESCRIBE_PROMPT = (\{.*?\n\})", src, re.S).group(1))
W = Path("/w")
url, model = sys.argv[1], sys.argv[2]
jobs = [("HS-VTR-T002F", 7, 8), ("HS-VTR-T002F", 4, 18)]
out = []
for stem, page, idx in jobs:
    blocks = json.load(open(glob.glob(f"/w/nemotron/{stem}/*_p{page:03d}.json")[0], encoding="utf-8"))["blocks"]
    cap = ""
    for j in list(range(idx + 1, min(idx + 3, len(blocks)))) + list(range(idx - 1, max(idx - 3, -1), -1)):
        if blocks[j].get("class") == "Caption":
            cap = re.sub(r"\*\*", "", blocks[j].get("text") or "").split("Fig.")[0].split("Table")[0].strip(); break
    crop = Image.open(glob.glob(f"/w/triage/{stem}_p{page:03d}_{idx}_*.png")[0]).convert("RGB")
    prompt = DESCRIBE_PROMPT["グラフ"].format(cap=cap or "図")
    buf = io.BytesIO(); crop.save(buf, format="PNG")
    body = {"model": model, "temperature": 0, "max_tokens": 700,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": prompt}]}]}
    t0 = time.time()
    r = requests.post(url.rstrip("/") + "/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"].strip()
    rec = {"stem": stem, "page": page, "idx": idx, "caption_assigned": cap, "prompt": prompt, "text": txt, "sec": round(time.time() - t0, 1)}
    out.append(rec)
    print(json.dumps(rec, ensure_ascii=False, indent=1), flush=True)
json.dump(out, open(W / "triage" / "describe_26b.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
