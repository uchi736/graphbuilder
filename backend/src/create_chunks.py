import logging
import re
from langchain_core.documents import Document
from langchain_neo4j import Neo4jGraph
from langchain_text_splitters import TokenTextSplitter

from src.document_sources.youtube import get_calculated_timestamps, get_chunks_with_timestamps
from src.shared.common_fn import get_value_from_env

logging.basicConfig(format="%(asctime)s - %(message)s", level="INFO")

MD_HEADING_PATTERN = re.compile(r"^#{1,3}\s", re.MULTILINE)


def normalize_markdown_headers(text: str) -> str:
    """Markdown見出しを正規化する（#の後にスペースがない日本語見出し等を修正）。"""
    text = re.sub(r'^(###)([^\s#])', r'\1 \2', text, flags=re.MULTILINE)
    text = re.sub(r'^(##)([^#\s])', r'\1 \2', text, flags=re.MULTILINE)
    text = re.sub(r'^(#)([^#\s])', r'\1 \2', text, flags=re.MULTILINE)
    return text


def extract_h1_title(text: str) -> str:
    """先頭の # (H1) タイトルを抽出する（正規化済みテキストを想定）。"""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return ""


class CreateChunksofDocument:
    """
    Class to handle splitting a list of documents (pages) into smaller chunks.
    """

    def __init__(self, pages: list[Document], graph: Neo4jGraph):
        """
        Initialize the chunk creator.

        Args:
            pages (list[Document]): List of langchain Document objects representing pages.
            graph (Neo4jGraph): Neo4j graph connection object.
        """
        self.pages = pages
        self.graph = graph

    def split_file_into_chunks(self, token_chunk_size: int, chunk_overlap: int, email: str):
        """
        Split a list of documents (pages) into chunks of fixed token size.

        Args:
            token_chunk_size (int): Number of tokens per chunk.
            chunk_overlap (int): Number of tokens to overlap between chunks.
            email (str): User email for chunk limiting logic.

        Returns:
            list[Document]: List of langchain Document chunks.
        """
        logging.info("Split file into smaller chunks")
        text_splitter = TokenTextSplitter(chunk_size=token_chunk_size, chunk_overlap=chunk_overlap)
        max_token_chunk_size = get_value_from_env("MAX_TOKEN_CHUNK_SIZE", 10000, "int")
        chunk_to_be_created = int(max_token_chunk_size / token_chunk_size)
        normalized_email = (email or "").strip().lower() or None
        is_neo4j_user = bool(normalized_email and normalized_email.endswith("@neo4j.com"))

        chunks = []
        first_metadata = self.pages[0].metadata

        if 'page' in first_metadata:
            # PDF or paginated document
            for i, document in enumerate(self.pages):
                page_number = i + 1
                for chunk in text_splitter.split_documents([document]):
                    chunks.append(Document(page_content=chunk.page_content, metadata={'page_number': page_number}))
        elif 'length' in first_metadata:
            # YouTube transcript or similar
            if len(self.pages) == 1 or (len(self.pages) > 1 and self.pages[1].page_content.strip() == ''):
                match = re.search(r'(?:v=)([0-9A-Za-z_-]{11})\s*', self.pages[0].metadata.get('source', ''))
                youtube_id = match.group(1) if match else None
                chunks_without_time_range = text_splitter.split_documents([self.pages[0]])
                if youtube_id:
                    chunks = get_calculated_timestamps(chunks_without_time_range, youtube_id)
                else:
                    chunks = chunks_without_time_range
            else:
                chunks_without_time_range = text_splitter.split_documents(self.pages)
                chunks = get_chunks_with_timestamps(chunks_without_time_range)
        else:
            markdown_enabled = get_value_from_env("MARKDOWN_CHUNKING", "true", "bool")
            has_headings = any(
                MD_HEADING_PATTERN.search(normalize_markdown_headers(document.page_content))
                for document in self.pages
            )
            if markdown_enabled and has_headings:
                logging.info("Markdown headings detected; chunking by sections with breadcrumbs")
                chunks = self.split_markdown_with_breadcrumbs(text_splitter)
            else:
                logging.info("No metadata found for pages, proceeding with normal chunking")
                chunks = text_splitter.split_documents(self.pages)

        logging.info('Total chunks created: %d', len(chunks))
        if not is_neo4j_user and len(chunks) > chunk_to_be_created:
            chunks = chunks[:chunk_to_be_created]
            logging.info('Non Neo4j user - limiting chunks to %d from %d', chunk_to_be_created, len(chunks))

        return chunks

    def split_markdown_with_breadcrumbs(self, text_splitter):
        """Markdown文書を見出し単位で分割し、各チャンク先頭に見出しパンくずを付与する。

        長いセクションをトークン数で再分割すると、2番目以降のサブチャンクは
        「どの章の話か」の文脈なしで埋め込み・抽出されてしまう。
        パンくず [H1 > H2 > H3] を本文側に付与することで、埋め込み・BM25・
        エンティティ抽出のすべてに文脈が効く（メタデータだけではベクトル化されない）。
        """
        from langchain_text_splitters import MarkdownHeaderTextSplitter
        md_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[("##", "h2"), ("###", "h3")],
            strip_headers=False,
        )
        chunks = []
        for document in self.pages:
            normalized = normalize_markdown_headers(document.page_content)
            h1_title = extract_h1_title(normalized)
            for section in md_splitter.split_text(normalized):
                headings = [h1_title, section.metadata.get("h2"), section.metadata.get("h3")]
                breadcrumb = " > ".join(h for h in headings if h)
                sub_chunks = text_splitter.split_documents(
                    [Document(page_content=section.page_content)]
                )
                for sub in sub_chunks:
                    content = sub.page_content
                    if breadcrumb:
                        content = f"[{breadcrumb}]\n{content.lstrip()}"
                    chunks.append(Document(page_content=content))
        return chunks