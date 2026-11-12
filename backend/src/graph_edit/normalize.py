"""表記の正規化（設計メモ 2.1）。

全角半角・空白・英字の大小だけをそろえる。括弧・中黒・ハイフン類・末尾の「材」は落とさない。
"""
import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def norm_key(name) -> str:
    """正規化キー: NFKC → 空白をすべて除く → casefold。"""
    s = unicodedata.normalize("NFKC", "" if name is None else str(name))
    s = _WHITESPACE.sub("", s)
    return s.casefold()


def rel_type_key(rel_type) -> str:
    """保存のときと同じ形のリレーションの種類（langchain_neo4j の add_graph_documents と同じく、空白を _ に替えて大文字、バッククォートを除く）。"""
    return str(rel_type or "").replace(" ", "_").upper().replace("`", "")


def rel_key(src, rel_type, tgt) -> str:
    """削除したリレーションの照合キー: 正規化キー(始点) | 種類 | 正規化キー(終点)。"""
    return f"{norm_key(src)}|{rel_type_key(rel_type)}|{norm_key(tgt)}"
