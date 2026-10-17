import sys, os, logging, re
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
sys.path.insert(0, "/code"); os.environ.setdefault("CHUNK_TOKENIZER", "/code/tokenizers/ruri-v3-310m/tokenizer.json")
from src.document_sources.pdf_markdown import load_pdf_as_markdown
from src.create_chunks import CreateChunksofDocument, chunk_length_function
length = chunk_length_function()
for path in sys.argv[1:]:
    docs = load_pdf_as_markdown(path)
    print("=" * 100); print(path, "frame_meta:", docs[0].metadata.get("frame_meta"))
    md = docs[0].page_content
    print("--- markdown head (1800 chars)"); print(md[:1800])
    chunks = CreateChunksofDocument(docs, None).split_file_into_chunks(512, 50, "t@example.com")
    print(f"--- chunks: {len(chunks)}")
    for i, c in enumerate(chunks):
        body = re.sub(r"^\[[^\]]*\]\n", "", c.page_content, count=1)
        print(f"[{i:02d}] p{c.metadata.get('page_number')} tok={length(c.page_content):3d} kind={c.metadata.get('kind','-')} sec={c.metadata.get('section')!r:60s} | {body[:70]!r}")
