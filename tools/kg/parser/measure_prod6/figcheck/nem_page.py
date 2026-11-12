import sys
sys.path.insert(0, "/code")
from src.document_sources.pdf_regions import pdfpage
from src.document_sources.pdf_regions.converter import RegionsConfig, _prepare
from src.document_sources.pdf_regions.nemotron import NemotronClient, table_rows
pdf, pno = sys.argv[1], int(sys.argv[2])
cfg = RegionsConfig().normalized()
with pdfpage.PDFIUM_LOCK:
    doc = pdfpage.open_document(pdf)
    page = doc[pno - 1]
    inp = _prepare(page, pno, cfg)
    page.close(); doc.close()
cli = NemotronClient("http://localhost:8022/v1", timeout=120, retries=0, concurrency=1) if 'concurrency' in NemotronClient.__init__.__code__.co_varnames else NemotronClient("http://localhost:8022/v1", timeout=120, retries=0)
blocks = cli.parse(inp.png, tag="probe")
for b in blocks:
    print("---", b.kind if hasattr(b,'kind') else b, getattr(b, 'bbox', None))
    print(getattr(b, 'text', '')[:1500])
