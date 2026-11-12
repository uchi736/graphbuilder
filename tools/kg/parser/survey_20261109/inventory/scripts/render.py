import json, sys
from pathlib import Path
import pypdfium2 as pdfium
for fn, pg in json.loads(sys.argv[1]):
    pdf = pdfium.PdfDocument(f"/in/full56/{fn}")
    pdf[pg - 1].render(scale=1.2).to_pil().save(f"/out/png/{Path(fn).stem}_p{pg}.png")
