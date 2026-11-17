import logging
from pathlib import Path
import chardet
from langchain_community.document_loaders import UnstructuredFileLoader
from langchain_core.documents import Document
from langchain_core.document_loaders import BaseLoader

class ListLoader(BaseLoader):
    """
    A wrapper to make a list of Documents compatible with BaseLoader.
    """
    def __init__(self, documents):
        self.documents = documents

    def load(self):
        """
        Returns the list of documents.
        """
        return self.documents

class PdfPageLoader(BaseLoader):
    """PDF をページごとの文字列で読む（pypdfium2。AGPL の部品を使わない）。
    1 ページを 1 つの Document にし、metadata に source と page（0 始まり）を入れる。
    文字層の無いページは空の文字列になる。"""

    def __init__(self, file_path):
        self.file_path = str(file_path)

    def load(self):
        # pdfium はスレッド安全ではないので、pdf_regions と同じプロセスに 1 つの錠の中で読む
        from src.document_sources.pdf_regions import pdfpage
        documents = []
        with pdfpage.PDFIUM_LOCK:
            doc = pdfpage.open_document(self.file_path)
            try:
                for i in range(len(doc)):
                    page = doc[i]
                    try:
                        textpage = page.get_textpage()
                        try:
                            text = textpage.get_text_range() or ""
                        finally:
                            textpage.close()
                    finally:
                        page.close()
                    documents.append(Document(page_content=text.replace("\r\n", "\n").replace("\r", "\n"),
                                              metadata={"source": self.file_path, "page": i}))
            finally:
                doc.close()
        return documents


def detect_encoding(file_path):
    """
    Detects the file encoding to avoid UnicodeDecodeError.

    Args:
        file_path (str or Path): Path to the file.

    Returns:
        str: Detected encoding (default "utf-8" if not found).
    """
    with open(file_path, 'rb') as f:
        raw_data = f.read(4096)
        result = chardet.detect(raw_data)
        return result['encoding'] or "utf-8"

def convert_pdf_via_edc(file_path):
    """
    EDC APIの /convert でPDFをページ毎テキストに変換する（PaddleX OCR、スキャンPDF対応）。

    EDC_PDF_CONVERT が無効・EDC未設定・変換失敗時は None を返し、
    呼び出し側はページごとの読み込み（PdfPageLoader）にフォールバックする。
    """
    from src.edc_extraction import parse_edc_config
    from src.shared.common_fn import get_value_from_env
    if not get_value_from_env("EDC_PDF_CONVERT", "false", "bool"):
        return None
    edc_config = parse_edc_config()
    if edc_config is None:
        logging.warning("EDC_PDF_CONVERT is enabled but LLM_MODEL_CONFIG_EDC is not set")
        return None
    base_url = edc_config[0]
    timeout = get_value_from_env("EDC_CONVERT_TIMEOUT", 300, "int")
    try:
        import httpx
        with open(file_path, "rb") as f:
            response = httpx.post(
                f"{base_url}/convert",
                files={"file": (Path(file_path).name, f, "application/pdf")},
                timeout=float(timeout),
            )
        response.raise_for_status()
        pages = response.json().get("pages") or []
        if not pages:
            return None
        logging.info("EDC /convert succeeded: %d pages from %s", len(pages), Path(file_path).name)
        return [
            Document(page_content=page_text, metadata={"source": str(file_path), "page": i})
            for i, page_text in enumerate(pages)
        ]
    except Exception as exc:
        logging.warning("EDC /convert failed (%s); falling back to the page loader", exc)
        return None

def load_document_content(file_path):
    """
    Loads document content from a file, handling PDFs and text encoding.

    Args:
        file_path (str or Path): Path to the file.

    Returns:
        tuple: (loader, raw_flag)
            loader: Document loader instance.
            raw_flag (bool): True if the loader returns raw page Documents
                (skip unstructured page grouping).
    """
    file_extension = Path(file_path).suffix.lower()
    encoding_flag = False
    if file_extension == '.pdf':
        from src.shared.common_fn import get_value_from_env
        if (str(get_value_from_env("PDF_CHUNK_STRATEGY", "token")).strip().lower() == "markdown"
                and get_value_from_env("MARKDOWN_CHUNKING", "true", "bool")):
            # Markdown に変換し、.md と同じ経路で分割する。
            # 変換器は regions（文字層＋Nemotron Parse、src/document_sources/pdf_regions）。
            # regions が失敗したとき（None・例外。ページ数が PDF_REGIONS_MAX_PAGES を超えるときも None）は、
            # 文字層だけの簡易変換（pdf_regions/plain.py）に戻す。どちらも AGPL の部品を使わない。
            # 文字の層が無い PDF は簡易変換でも None が返り、下の従来の読み込み（EDC の OCR・ページごとの読み込み）に進む。
            name = Path(file_path).name
            for loader_name, failed, empty in (
                    ("load_pdf_as_markdown_regions", "PDF to Markdown conversion by regions failed (%s)",
                     "PDF %s: the regions converter returned nothing; falling back to the text-layer converter"),
                    ("load_pdf_as_markdown_plain",
                     "PDF to Markdown conversion on the text layer failed (%s); falling back to the default loader", None)):
                try:
                    from src.document_sources.pdf_regions import graph_builder
                    markdown_pages = getattr(graph_builder, loader_name)(file_path)
                except Exception as exc:
                    logging.warning(failed, exc)
                    markdown_pages = None
                if markdown_pages:
                    return ListLoader(markdown_pages), True
                if empty:
                    logging.warning(empty, name)
        edc_pages = convert_pdf_via_edc(file_path)
        if edc_pages is not None:
            return ListLoader(edc_pages), False
        loader = PdfPageLoader(file_path)
        return loader, encoding_flag
    if file_extension == ".md":
        # Markdownは見出し構造を保持するため生テキストで読む
        # （UnstructuredFileLoaderは要素分解時に # マーカーを落とし、見出しパンくずチャンキングが効かなくなる）
        encoding = detect_encoding(file_path)
        with open(file_path, encoding=encoding, errors="replace") as f:
            content = f.read()
        loader = ListLoader([Document(page_content=content, metadata={"source": str(file_path), "markdown": True})])
        return loader, True
    if file_extension == ".txt":
        encoding = detect_encoding(file_path)
        logging.info("Detected encoding for text file: %s", encoding)
        if encoding.lower() == "utf-8":
            loader = UnstructuredFileLoader(file_path, mode="elements", autodetect_encoding=True)
            return loader, encoding_flag
        with open(file_path, encoding=encoding, errors="replace") as f:
            content = f.read()
        loader = ListLoader([Document(page_content=content, metadata={"source": file_path})])
        encoding_flag = True
        return loader, encoding_flag
    loader = UnstructuredFileLoader(file_path, mode="elements", autodetect_encoding=True)
    return loader, encoding_flag

def get_documents_from_file_by_path(file_path, file_name):
    """
    Loads documents from a file by its path and returns file name, pages, and extension.

    Args:
        file_path (str or Path): Path to the file.
        file_name (str): Name of the file.

    Returns:
        tuple: (file_name, pages, file_extension)

    Raises:
        Exception: If file does not exist or reading fails.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        logging.info('File %s does not exist', file_name)
        raise Exception(f'File {file_name} does not exist')
    logging.info('file %s processing', file_name)
    try:
        loader, raw_flag = load_document_content(file_path)
        file_extension = file_path.suffix.lower()
        if file_extension == ".pdf" or raw_flag:
            pages = loader.load()
        else:
            unstructured_pages = loader.load()
            pages = get_pages_with_page_numbers(unstructured_pages)
    except Exception as exc:
        raise Exception(f'Error while reading the file content or metadata, {exc}')
    return file_name, pages, file_extension

def get_pages_with_page_numbers(unstructured_pages):
    """
    Groups unstructured pages into logical pages with page numbers and metadata.

    Args:
        unstructured_pages (list): List of Document objects.

    Returns:
        list: List of Document objects with page numbers and metadata.
    """
    pages = []
    page_number = 1
    page_content = ''
    metadata = {}
    for idx, page in enumerate(unstructured_pages):
        if 'page_number' in page.metadata:
            if page.metadata['page_number'] == page_number:
                page_content += page.page_content
                metadata = {
                    'source': page.metadata['source'],
                    'page_number': page_number,
                    'filename': page.metadata['filename'],
                    'filetype': page.metadata['filetype']
                }
            if page.metadata['page_number'] > page_number:
                page_number += 1
                pages.append(Document(page_content=page_content))
                page_content = ''
            if page == unstructured_pages[-1]:
                pages.append(Document(page_content=page_content))
        elif page.metadata.get('category') == 'PageBreak' and page != unstructured_pages[0]:
            page_number += 1
            pages.append(Document(page_content=page_content, metadata=metadata))
            page_content = ''
            metadata = {}
        else:
            page_content += page.page_content
            metadata_with_custom_page_number = {
                'source': page.metadata['source'],
                'page_number': 1,
                'filename': page.metadata['filename'],
                'filetype': page.metadata['filetype']
            }
            if page == unstructured_pages[-1]:
                pages.append(Document(page_content=page_content, metadata=metadata_with_custom_page_number))
    return pages
