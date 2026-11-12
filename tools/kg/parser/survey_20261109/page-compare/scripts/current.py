"""今の変換(load_pdf_as_markdown)の結果と、枠の処理(frame_pages)の生の結果を保存する。"""
import json, logging, sys, subprocess
from pathlib import Path
sys.path.insert(0, "/code")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
from src.document_sources.pdf_markdown import load_pdf_as_markdown
src, out = Path(sys.argv[1]), Path(sys.argv[2])
for name in sys.argv[3:]:
    stem = Path(name).stem
    docs = load_pdf_as_markdown(str(src / name))
    if not docs:
        print(name, "None"); continue
    d = docs[0]
    (out / f"{stem}.md").write_text(d.page_content, encoding="utf-8")
    json.dump(d.metadata, open(out / f"{stem}.meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    proc = subprocess.run([sys.executable, "-m", "src.document_sources.frame_pages", str(src / name)], cwd="/code", capture_output=True, text=True)
    (out / f"{stem}.frames.json").write_text(proc.stdout, encoding="utf-8")
    print(name, len(d.page_content), "chars")
