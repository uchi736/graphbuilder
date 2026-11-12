import logging
import os
import re
from langchain_core.documents import Document
from langchain_neo4j import Neo4jGraph
from langchain_text_splitters import TokenTextSplitter

from src.document_sources.youtube import get_calculated_timestamps, get_chunks_with_timestamps
from src.shared.common_fn import get_value_from_env

logging.basicConfig(format="%(asctime)s - %(message)s", level="INFO")

MD_HEADING_PATTERN = re.compile(r"^#{1,3}\s", re.MULTILINE)


def normalize_markdown_headers(text: str) -> str:
    """Markdown見出しを正規化する（#の後にスペースがない日本語見出し等を修正）。コードの囲みの中は変えない。"""
    out, fence = [], None
    for line in text.split("\n"):
        fence, is_fence = _fence_state(fence, line)
        if not is_fence and fence is None:
            line = re.sub(r'^(#{1,3})([^\s#])', r'\1 \2', line)
        out.append(line)
    return "\n".join(out)


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
        text_splitter = TokenTextSplitter(chunk_size=token_chunk_size, chunk_overlap=chunk_overlap, disallowed_special=())
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
            # Markdown の経路に入るかどうかは、読み込み側が付けた印だけで決める。
            # 印の無い文書は改行が空白に替わっているので、本文が # で始まっていても見出しでは分けられない
            if markdown_enabled and first_metadata.get('markdown'):
                logging.info("Markdown document; chunking by sections with breadcrumbs")
                return self.split_markdown_with_breadcrumbs(token_chunk_size, chunk_overlap, max_token_chunk_size, is_neo4j_user)
            logging.info("No metadata found for pages, proceeding with normal chunking")
            chunks = text_splitter.split_documents(self.pages)

        total = len(chunks)
        logging.info('Total chunks created: %d', total)
        if max_token_chunk_size > 0 and not is_neo4j_user and total > chunk_to_be_created:
            chunks = chunks[:chunk_to_be_created]
            logging.warning('Non Neo4j user - limiting chunks to %d from %d; the rest of the document is NOT ingested', chunk_to_be_created, total)

        return chunks

    def split_markdown_with_breadcrumbs(self, token_chunk_size: int, chunk_overlap: int, max_tokens: int = 0, unlimited: bool = False):
        """Markdown文書を見出し単位で分割し、各チャンク先頭に見出しパンくずを付与する。

        長いセクションは、段落（空行）、行、文の切れ目の順に沿って再分割する。表は行の単位で分け、各断片に列名の行を付ける。
        2番目以降のサブチャンクは「どの章の話か」の文脈なしで埋め込み・抽出されてしまうため、
        パンくず [文書名 > 見出しの経路] を本文側に付与する。埋め込み・BM25・
        エンティティ抽出のすべてに文脈が効く（メタデータだけではベクトル化されない）。

        長さは CHUNK_TOKENIZER で選んだトークナイザで数える（既定は本家と同じ gpt2）。
        PDF から変換した Markdown にはページの切れ目の印が入っている。印はページ番号に読み替えて本文から消す。
        1 文書あたりの上限（MAX_TOKEN_CHUNK_SIZE）は、チャンクの数ではなく実際のトークン数の累計で数える。0 以下は上限なし。
        """
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from src.document_sources.page_marks import PAGE_MARK_PATTERN
        from src.document_sources.number_headings import is_control_heading

        length = chunk_length_function()
        splitters = {}

        def splitter_for(size: int):
            if size not in splitters:
                splitters[size] = RecursiveCharacterTextSplitter(
                    chunk_size=size, chunk_overlap=min(chunk_overlap, size // 2), length_function=length,
                    separators=MARKDOWN_SEPARATORS, keep_separator="end",
                )
            return splitters[size]

        with_title = get_value_from_env("MARKDOWN_BREADCRUMB_TITLE", "true", "bool")
        min_tokens = get_value_from_env("MARKDOWN_MIN_CHUNK_TOKENS", "80", "int")
        chunks = []
        for document in self.pages:
            doc_chunks = []
            text = document.page_content
            if not document.metadata.get("markdown_strict"):
                # 人が書いた .md には「#見出し」と空白なしで書く流儀がある。PDF から変換した Markdown には掛けない
                text = normalize_markdown_headers(text)
            title = (document.metadata.get("title") or "") if with_title else ""
            page_number = None
            for path, body in markdown_sections(text):
                # パンくずは指定した大きさの 4 分の 1 までとし、超えるときは途中の段を省く（小さいチャンクで本文を圧迫しない）
                breadcrumb = fit_breadcrumb(title, path, token_chunk_size // 4, length)
                prefix = f"[{breadcrumb}]\n" if breadcrumb else ""
                # パンくずの分だけ本文の上限を詰める（指定した大きさを超えないようにする）
                size = max(token_chunk_size - length(prefix), 1)
                for content in merge_heading_only(split_section(body, splitter_for, length, size)):
                    marks = PAGE_MARK_PATTERN.findall(content)
                    if marks and content.lstrip().startswith("<!-- page:"):
                        page_number = int(marks[0])
                    start_page = page_number if page_number is not None else (int(marks[0]) if marks else None)
                    if marks:
                        page_number = int(marks[-1])
                    content = PAGE_MARK_PATTERN.sub("", content).strip()
                    if not content:
                        continue
                    metadata = {"section": breadcrumb} if breadcrumb else {}
                    if start_page is not None:
                        metadata["page_number"] = start_page
                    metadata["body_tokens"] = length(content)
                    if is_control_heading(path):
                        # 改正来歴・協議通知票・承認欄。本文ではなく文書の管理の部分なので、検索と抽出で区別できるように印を付ける
                        metadata["kind"] = "control"
                    metadata["_prefix"] = prefix
                    metadata["_path"] = list(path)
                    doc_chunks.append(Document(page_content=prefix + content, metadata=metadata))

            def prefix_for(path):
                breadcrumb = fit_breadcrumb(title, path, token_chunk_size // 4, length)
                return (f"[{breadcrumb}]\n" if breadcrumb else ""), breadcrumb

            chunks.extend(merge_tiny_chunks(doc_chunks, length, token_chunk_size, min_tokens, prefix_for))

        total = len(chunks)
        logging.info('Total chunks created: %d', total)
        if max_tokens and max_tokens > 0 and not unlimited:
            # 上限は本文の量で数える（パンくずは数えない）
            used = 0
            for index, chunk in enumerate(chunks):
                used += chunk.metadata.get("body_tokens", 0)
                if used > max_tokens:
                    logging.warning('MAX_TOKEN_CHUNK_SIZE=%d reached: keeping %d of %d chunks; the rest of the document is NOT ingested',
                                    max_tokens, index, total)
                    chunks = chunks[:index]
                    break
        for chunk in chunks:
            chunk.metadata.pop("body_tokens", None)
        return chunks


# 長い節を分けるときの切れ目。段落（空行）、行、文末、空白の順に試す
MARKDOWN_SEPARATORS = ["\n\n", "\n", "。", "．", "！", "？", ". ", "! ", "? ", " ", ""]

_HEADING_LINE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")  # 開始か終了かは _fence_state が決める
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_PAGE_MARK_LINE = re.compile(r"^<!-- page:\d+ -->$")
_LENGTH_FUNCTIONS = {}


def chunk_length_function():
    """チャンクの長さを数える関数を返す。

    CHUNK_TOKENIZER が tokenizer.json のパスならそのトークナイザ（埋め込みモデルのものを指すと、大きさの指定が
    モデルの実際の入力長と一致し、言語による開きが小さくなる）、tiktoken の名前ならその符号化、未設定なら本家と同じ gpt2。
    読めない場合は警告を出して gpt2 に戻る。
    """
    name = (os.environ.get("CHUNK_TOKENIZER") or "gpt2").strip()
    if name in _LENGTH_FUNCTIONS:
        return _LENGTH_FUNCTIONS[name]
    function = None
    if name.endswith(".json"):
        try:
            from tokenizers import Tokenizer
            tokenizer = Tokenizer.from_file(name)
            tokenizer.no_truncation()
            tokenizer.no_padding()
            function = lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids)
        except Exception as exc:
            logging.warning("CHUNK_TOKENIZER=%s could not be loaded (%s); chunk length is counted with gpt2 instead", name, exc)
    if function is None:
        import tiktoken
        try:
            encoding = tiktoken.get_encoding("gpt2" if name.endswith(".json") else name)
        except Exception as exc:
            logging.warning("CHUNK_TOKENIZER=%s is not a tiktoken encoding (%s); chunk length is counted with gpt2 instead", name, exc)
            encoding = tiktoken.get_encoding("gpt2")
        function = lambda text: len(encoding.encode(text, disallowed_special=()))
    _LENGTH_FUNCTIONS[name] = function
    return function


def _fence_state(fence, line: str):
    """コードの囲みの開閉を追う。戻り値は (新しい状態, この行が囲みの行か)。

    囲みは、開きと同じ記号で同じ長さ以上、後ろに空白しか無い行だけが閉じる（CommonMark の規則）。
    """
    match = _FENCE_LINE.match(line)
    if not match:
        return fence, False
    mark, rest = match.group(1), match.group(2)
    if fence is None:
        if mark[0] == "`" and "`" in rest:
            return None, False  # 「```code``` と書く」のような行内の表記は、囲みの開始ではない
        return (mark[0], len(mark)), True
    if mark[0] == fence[0] and len(mark) >= fence[1] and not rest.strip():
        return None, True
    return fence, False


def _is_blank(line: str) -> bool:
    return not line.strip() or bool(_PAGE_MARK_LINE.match(line.strip()))


def markdown_sections(text: str):
    """Markdown を見出しで節に分け、(見出しの経路, 本文) の並びを返す。

    本文は元の文字列のまま返す（空行が残るので、再分割で段落の切れ目を使える）。
    コードの囲みの中の # は見出しとみなさない。見出しだけで本文の無い節は、次の節の先頭に付ける。
    見出しの段は 1〜6 のすべてを使う。段の番号は字の大きさの順位にすぎないので、経路は現れた段だけで作る。
    """
    sections = []
    levels = []  # 各節の見出しの段。本文の無い見出しを前後どちらの節に付けるかの判断に使う
    stack = {}
    path = []
    lines = []
    pending = []
    fence = None
    current_level = 0

    def flush():
        nonlocal pending
        body = [line for line in lines if not _is_blank(line)]
        if not body or all(_HEADING_LINE.match(line) for line in body):
            # 本文の無い節（見出しだけ、ページの印だけ）。前の節より段が深い（前の節の中の項目の列挙）なら前の節の末尾に、
            # そうでなければ本文を持つ次の節の先頭に付ける
            if sections and not pending and body and current_level > levels[-1]:
                sections[-1] = (sections[-1][0], (sections[-1][1] + "\n" + "\n".join(lines)).strip("\n"))
            else:
                pending = pending + lines
            return
        sections.append((list(path), "\n".join(pending + lines).strip("\n")))
        levels.append(current_level)
        pending = []

    for line in text.split("\n"):
        fence, is_fence = _fence_state(fence, line)
        heading = _HEADING_LINE.match(line) if fence is None and not is_fence else None
        if heading and heading.group(2).strip():
            flush()
            lines = [line]
            level = len(heading.group(1))
            current_level = level
            stack = {k: v for k, v in stack.items() if k < level}
            stack[level] = heading.group(2).strip()
            path = [stack[k] for k in sorted(stack)]
        else:
            lines.append(line)
    flush()
    if any(not _is_blank(line) for line in pending):
        sections.append((list(path), "\n".join(pending).strip("\n")))
    return sections


def split_section(body: str, splitter_for, length, size: int):
    """1 つの節を上限に収まる断片に分ける。

    表（列名の行と区切りの行で始まる、| で始まる行のまとまり）は行の単位で詰め、各断片の先頭に列名と区切りの行を付ける。
    1 行が上限を超える行だけを文字の単位で分ける。表以外は段落、行、文末の順に切る。
    """
    pieces = []  # (最後の中身が表かどうか, 文字列)
    rows = body.split("\n")
    text = []
    splitter = splitter_for(size)

    def flush_text():
        joined = "\n".join(text)
        text.clear()
        if joined.strip():
            pieces.extend((False, part) for part in splitter.split_text(joined))

    i = 0
    while i < len(rows):
        is_table = (rows[i].lstrip().startswith("|") and i + 1 < len(rows)
                    and _TABLE_RULE.match(rows[i + 1]) and not _TABLE_RULE.match(rows[i]))
        if not is_table:
            text.append(rows[i])
            i += 1
            continue
        flush_text()
        header = rows[i:i + 2]
        i += 2
        current = []
        while i < len(rows) and rows[i].lstrip().startswith("|"):
            row = rows[i]
            i += 1
            if current and length("\n".join(header + current + [row])) > size:
                pieces.append((True, "\n".join(header + current)))
                current = []
            if length("\n".join(header + [row])) > size:
                if current:
                    pieces.append((True, "\n".join(header + current)))
                    current = []
                # 列名の分だけ詰めて分け、最初の断片に列名を付ける
                room = max(size - length("\n".join(header) + "\n"), size // 2)
                parts = splitter_for(room).split_text(row) or [row]
                pieces.append((True, "\n".join(header + [parts[0]])))
                pieces.extend((True, part) for part in parts[1:])
            else:
                current.append(row)
        if current or not pieces or pieces[-1][1].split("\n")[:2] != header:
            pieces.append((True, "\n".join(header + current)))
    flush_text()

    # 段落と小さな表が別々の断片になったときは、合わせて上限に収まるならつなぐ（表と前後の説明を同じチャンクに保つ）
    merged = []
    for is_table, piece in pieces:
        if merged and merged[-1][0] != is_table and length(merged[-1][1] + "\n\n" + piece) <= size:
            merged[-1] = (is_table, merged[-1][1] + "\n\n" + piece)
        else:
            merged.append((is_table, piece))
    return [piece for _, piece in merged]


def _common_path(a, b):
    out = []
    for x, y in zip(a or [], b or []):
        if x != y:
            break
        out.append(x)
    return out


def merge_tiny_chunks(chunks, length, size: int, min_tokens: int, prefix_for):
    """本文が min_tokens に満たないチャンク（題だけの節など）を、同じ文書の前のチャンクに寄せる。前に収まらなければ次に寄せる。

    寄せる先は同じ種類（本文どうし、管理どうし）で、同じ章の中（見出しの経路に共通の親がある）に限る。章をまたぐ寄せは、
    両方が小さいときだけ許す（題だけの章が続くとき）。寄せたあとの長さは size を超えない。
    小さいチャンクの本文は見出しの行ごと移すので、どの節の話かは残る。パンくずは両方に共通の親の見出しに付け替える。
    prefix_for(path) は見出しの経路から (パンくずの行, パンくず) を返す。
    """
    for chunk in chunks:
        chunk.metadata.setdefault("_prefix", "")
        chunk.metadata.setdefault("_path", [])

    def body_of(chunk):
        return chunk.page_content[len(chunk.metadata["_prefix"]):]

    def tiny(chunk):
        return chunk.metadata.get("body_tokens", length(body_of(chunk))) < min_tokens

    def same_kind(a, b):
        return a.metadata.get("kind") == b.metadata.get("kind")

    def join(first, second):
        """2 つのチャンクを 1 つにしたときの (本文, 経路, パンくずの行, パンくず)。"""
        body = body_of(first) + "\n\n" + body_of(second)
        path = _common_path(first.metadata["_path"], second.metadata["_path"])
        prefix, breadcrumb = prefix_for(path)
        return body, path, prefix, breadcrumb

    def apply(target, body, path, prefix, breadcrumb, page_number):
        target.page_content = prefix + body
        target.metadata["_prefix"] = prefix
        target.metadata["_path"] = path
        target.metadata["body_tokens"] = length(body)
        if breadcrumb:
            target.metadata["section"] = breadcrumb
        else:
            target.metadata.pop("section", None)
        if page_number is not None:
            target.metadata["page_number"] = page_number

    out = []
    pending = None  # 前に収まらず、次に寄せる待ちの小さいチャンク
    if min_tokens > 0 and len(chunks) >= 2:
        for chunk in chunks:
            if pending is not None:
                body, path, prefix, breadcrumb = join(pending, chunk)
                if same_kind(pending, chunk) and (path or tiny(chunk)) and length(prefix + body) <= size:
                    apply(chunk, body, path, prefix, breadcrumb, pending.metadata.get("page_number", chunk.metadata.get("page_number")))
                else:
                    out.append(pending)
                pending = None
            if not tiny(chunk):
                out.append(chunk)
                continue
            previous = out[-1] if out else None
            if previous is not None and same_kind(previous, chunk):
                body, path, prefix, breadcrumb = join(previous, chunk)
                if (path or tiny(previous)) and length(prefix + body) <= size:
                    apply(previous, body, path, prefix, breadcrumb, None)
                    continue
            pending = chunk
        if pending is not None:
            out.append(pending)
    else:
        out = list(chunks)
    for chunk in out:
        chunk.metadata.pop("_prefix", None)
        # 見出しの経路は、章・節の層（section_layer）の予備の入力として section_path に残す（段 3 B4）
        chunk.metadata["section_path"] = list(chunk.metadata.pop("_path", None) or [])
    return out


def merge_heading_only(parts):
    """再分割で見出しの行だけ（またはページの印だけ）になった断片を、次の断片の先頭に付ける（見出しだけのチャンクを作らない）"""
    out, carry = [], []
    for part in parts:
        body = [row for row in part.split("\n") if not _is_blank(row)]
        if not body or all(_HEADING_LINE.match(row) for row in body):
            carry.append(part)
            continue
        out.append("\n\n".join(carry + [part]))
        carry = []
    if carry:
        if out:
            out[-1] = out[-1] + "\n\n" + "\n\n".join(carry)
        else:
            out.append("\n\n".join(carry))
    return out


_EMPHASIS = [
    re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"),
    re.compile(r"(?<![0-9A-Za-z])__(?=\S)(.+?)(?<=\S)__(?![0-9A-Za-z])"),
    re.compile(r"\*(?=\S)(.+?)(?<=\S)\*"),
    re.compile(r"(?<![0-9A-Za-z])_(?=\S)(.+?)(?<=\S)_(?![0-9A-Za-z])"),
    re.compile(r"`(.+?)`"),
]


def fit_breadcrumb(title: str, path, limit: int, length) -> str:
    """パンくずを limit トークンに収める。収まらないときは途中の段を省き、文書名と末尾の見出しを残す。それでも長ければ字数で切る"""
    for candidate in (list(path), list(path[-1:]), []):
        breadcrumb = breadcrumb_of(title, candidate)
        if length(f"[{breadcrumb}]\n") <= limit:
            return breadcrumb
    breadcrumb = breadcrumb_of(title, path[-1:])
    while len(breadcrumb) > 8 and length(f"[{breadcrumb}…]\n") > limit:
        breadcrumb = breadcrumb[:max(8, int(len(breadcrumb) * 0.8))]
    return breadcrumb + "…"


def breadcrumb_of(title: str, path) -> str:
    """文書名と見出しの経路からパンくずを作る。

    見出しからは、対になった強調の印（**…**、_…_、`…`）と、記号の前の逆斜線だけを外す。
    文書名はそのまま使う（ファイル名の下線を消さない）。同じ語が続くときは 1 つにする。
    """
    items = []
    for index, item in enumerate([title] + list(path)):
        item = re.sub(r"<!--.*?-->", "", item or "").strip()
        if index > 0:
            for pattern in _EMPHASIS:
                item = pattern.sub(r"\1", item)
            item = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", item).strip()
        if item and (not items or items[-1] != item):
            items.append(item)
    return " > ".join(items)
