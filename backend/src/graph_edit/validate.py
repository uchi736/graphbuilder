"""予約の名前・名前の形・引数の検査と、失敗のコード（設計メモ 2.2 と 3 章）。

失敗は EditError で投げ、入口で create_api_response('Failed', error="CODE: 詳細", data=...) に直す。
コードは英語のまま画面に渡し、画面が日本語に写す。詳細（detail）は英語、画面にそのまま出す理由（reason）は日本語で書く。
"""
import json
import math
import re

from src.shared.constants import GRAPH_EDIT_MANAGEMENT_LABELS, PROTECTED_MANAGEMENT_LABELS, PROTECTED_RELATIONSHIP_TYPES
from src.shared.common_fn import RESERVED_NODE_TYPES

# 失敗のコード（設計メモ 3 章の表）
NOT_FOUND = "NOT_FOUND"
STALE = "STALE"
CONFLICT = "CONFLICT"
RESERVED = "RESERVED"
INVALID = "INVALID"
BUSY = "BUSY"
NOT_UNDOABLE = "NOT_UNDOABLE"

ENTITY_LABEL = "__Entity__"
DOCUMENT_LABEL = "Document"
GRAPH_EDIT_LABEL, GRAPH_EDIT_OP_LABEL, GRAPH_LOCK_LABEL = GRAPH_EDIT_MANAGEMENT_LABELS


def _unique(items):
    seen = set()
    return [x for x in items if not (x in seen or seen.add(x))]


# 規則で作る層（文書層 document_layer.py・章節の層 section_layer.py）。章・節のノード Section はエンティティでない。
# 文書番号 Standard・型式 Model のうち method='rule' のものは後処理が作り直す（規則で作ったノード。設計メモ 2.2）
RULE_METHOD = "rule"
RULE_LAYER_LABELS = ["Section"]
RULE_LAYER_RELATIONSHIP_TYPES = ["HAS_SECTION", "IN_SECTION"]
RULE_MADE_REASON = ("規則により生成したノード（文書番号・型式）は後処理（文書層の生成）のたびに再生成されるため、名前・ラベル・プロパティの"
                    "変更と削除はできない。統合先（名前とラベルは変更されない）とリレーションの端点には使用できる。")
# 予約ラベル（付け外しの対象にしない。ノードがこれを持つと編集の対象外）。__KGBuilder__ は取り込みが型の名前を付け替える予約の名前
RESERVED_LABELS = _unique(["Chunk", "__Community__", "Session", "Message"] + PROTECTED_MANAGEMENT_LABELS
                          + RULE_LAYER_LABELS + ["__KGBuilder__"])
# 予約のリレーション種類（手動で作成・付け替え・削除しない）。graphDB_dataAccess もこの一覧を読み込んで使う
RESERVED_RELATIONSHIP_TYPES = _unique(["HAS_ENTITY", "PART_OF", "NEXT_CHUNK", "FIRST_CHUNK", "SIMILAR", "IN_COMMUNITY",
                                       "PARENT_COMMUNITY"] + PROTECTED_RELATIONSHIP_TYPES + RULE_LAYER_RELATIONSHIP_TYPES)

LABEL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RELATIONSHIP_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")

# M3 で手で書き換えられないプロパティ（先頭が _ のものも不可）。aliases は台帳から作る、fileName は文書ノードの印
FORBIDDEN_PROPERTY_KEYS = {"id", "embedding", "aliases", "fileName"}
# 画面の詳細・写しに出さないプロパティ（graph_query.process_node と同じ）。M3 でも書き換えさせない
HIDDEN_PROPERTY_KEYS = {"embedding", "text", "summary"}
# M3 のプロパティ名の形: 文字か _ で始まり、文字・数字・_ だけ（日本語の文字も可。空白・記号・バッククォートは不可）
PROPERTY_NAME_PATTERN = re.compile(r"^[^\W\d][\w]*$")
MAX_PROPERTY_NAME_LENGTH = 100

MAX_NAME_LENGTH = 1000


class EditError(Exception):
    """編集の API の失敗。code は設計メモ 3 章の表のコード、detail は英語の詳細、data は応答の data に入れる値。"""

    def __init__(self, code, detail="", data=None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.data = data

    def error_text(self):
        return f"{self.code}: {self.detail}" if self.detail else self.code


# ---- 引数の読み取り ----

def require_text(value, name):
    """前後の空白を除いた、空でない文字列。"""
    if value is None or not isinstance(value, str) or value.strip() == "":
        raise EditError(INVALID, f"'{name}' is required")
    text = value.strip()
    if len(text) > MAX_NAME_LENGTH:
        raise EditError(INVALID, f"'{name}' is too long ({len(text)} characters)")
    return text


def optional_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_json(value, name, expected_type, required=True):
    """JSON の引数（文字列で受ける）。expected_type は list か dict。"""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        if required:
            raise EditError(INVALID, f"'{name}' is required")
        return None
    if isinstance(value, (list, dict)):
        parsed = value
    else:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as e:
            raise EditError(INVALID, f"'{name}' is not valid JSON: {e}")
    if not isinstance(parsed, expected_type):
        raise EditError(INVALID, f"'{name}' must be a JSON {'array' if expected_type is list else 'object'}")
    return parsed


def parse_bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    if text == "":
        return default
    raise EditError(INVALID, f"not a boolean: {value!r}")


def parse_int(value, default, minimum=None, maximum=None, name="value"):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return default
    try:
        number = int(str(value).strip())
    except ValueError:
        raise EditError(INVALID, f"'{name}' must be an integer")
    if minimum is not None and number < minimum:
        raise EditError(INVALID, f"'{name}' must be >= {minimum}")
    if maximum is not None and number > maximum:
        number = maximum
    return number


def parse_node_refs(value, name):
    """[{element_id, expected_id}] の一覧。"""
    refs = parse_json(value, name, list)
    out = []
    for i, ref in enumerate(refs):
        if not isinstance(ref, dict):
            raise EditError(INVALID, f"'{name}[{i}]' must be an object with element_id and expected_id")
        out.append({"element_id": require_text(ref.get("element_id"), f"{name}[{i}].element_id"),
                    "expected_id": require_text(ref.get("expected_id"), f"{name}[{i}].expected_id")})
    return out


def parse_expected_relationship(value):
    """M7・M8 の expected（{src, type, tgt}）。"""
    expected = parse_json(value, "expected", dict)
    return {"src": require_text(expected.get("src"), "expected.src"),
            "type": require_text(expected.get("type"), "expected.type"),
            "tgt": require_text(expected.get("tgt"), "expected.tgt")}


# ---- 名前と値の検査 ----

def is_rule_made(props):
    """規則で作ったノード（method='rule'）か。"""
    return (props or {}).get("method") == RULE_METHOD


def entity_block(labels, props, allow_rule_made=False):
    """編集の対象のエンティティか。対象なら None、対象外なら (コード, 日本語の理由, 英語の詳細)。

    対象: __Entity__ を持ち、予約ラベルを持たず、fileName を持たないノード（設計メモ 2.2）。規則で作ったノード
    （method='rule'）は allow_rule_made のとき（統合先・リレーションの端・別物の登録）だけ対象にする。
    予約ラベルの検査を fileName の検査より前に置き、章・節（Section）とチャンクに正しい理由を返す。
    """
    labels = list(labels or [])
    props = props or {}
    reserved = [label for label in labels if label in RESERVED_LABELS]
    if reserved:
        return (RESERVED, "チャンク・章・節・コミュニティ・会話の履歴・修正台帳などの管理用ノードは編集できない。"
                          "編集できるのはエンティティのノードである。",
                f"node has a reserved label ({', '.join(reserved)})")
    if props.get("fileName") is not None:
        return RESERVED, "文書のノードは編集できない。編集できるのはエンティティのノードである。", "document nodes cannot be edited"
    if ENTITY_LABEL not in labels:
        return RESERVED, "編集できるのはエンティティ（__Entity__ のラベルを持つノード）のみである。", "node is not an entity"
    if props.get("id") is None or str(props.get("id")).strip() == "":
        return RESERVED, "名前（id）のないエンティティは編集できない。", "entity has no id"
    if is_rule_made(props) and not allow_rule_made:
        return RESERVED, RULE_MADE_REASON, "entity was made by a rule (method='rule')"
    return None


def check_entity(labels, props, element_id=None, allow_rule_made=False):
    block = entity_block(labels, props, allow_rule_made)
    if block is not None:
        code, reason, detail = block
        raise EditError(code, f"{detail}" + (f" ({element_id})" if element_id else ""), data={"reason": reason})


def type_labels_of(labels):
    """__Entity__ を除いた種類のラベル（順序はそのまま）。"""
    return [label for label in (labels or []) if label != ENTITY_LABEL]


def validate_type_labels(labels, current_labels=(), allow_empty=False):
    """新しい種類のラベルの一覧を検査して返す（__Entity__ は外す。重複は除く）。

    今のノード（統合では統合するノードのどれか）が持っているラベルは、名前の形の検査を通らなくても残してよい。
    Document は今持っているときだけ残せる（新たに付けられない）。
    """
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise EditError(INVALID, "'labels' must be a JSON array of strings")
    current = set(current_labels or ())
    cleaned = _unique([label.strip() for label in labels if label.strip() != ENTITY_LABEL])
    if any(label == "" for label in cleaned):
        raise EditError(INVALID, "empty label")
    if not cleaned and not allow_empty:
        raise EditError(INVALID, "at least one label is required")
    for label in cleaned:
        if label in RESERVED_LABELS:
            raise EditError(RESERVED, f"label {label} is reserved")
        if label == DOCUMENT_LABEL and label not in current:
            raise EditError(RESERVED, "label Document cannot be added to an entity")
        # 取り込みの normalize_reserved_type が付け替える名前は、大小を問わず種類に選ばせない
        if label.lower() in RESERVED_NODE_TYPES and label != DOCUMENT_LABEL and label not in current:
            raise EditError(RESERVED, f"label {label} is reserved (the extraction renames it to "
                                      f"{RESERVED_NODE_TYPES[label.lower()]})")
        if not LABEL_PATTERN.match(label) and label not in current:
            raise EditError(INVALID, f"invalid label name {label!r} (allowed: ^[A-Za-z_][A-Za-z0-9_]*$)")
    return cleaned


def validate_relationship_type(rel_type):
    rel_type = require_text(rel_type, "type")
    if rel_type in RESERVED_RELATIONSHIP_TYPES:
        raise EditError(RESERVED, f"relationship type {rel_type} is reserved")
    if not RELATIONSHIP_TYPE_PATTERN.match(rel_type):
        raise EditError(INVALID, f"invalid relationship type {rel_type!r} (allowed: ^[A-Z][A-Z0-9_]*$)")
    return rel_type


def is_reserved_relationship_type(rel_type):
    return rel_type in RESERVED_RELATIONSHIP_TYPES


def _check_value(key, value, allow_null):
    if value is None:
        if allow_null:
            return
        raise EditError(INVALID, f"property {key!r} must not be null")
    if isinstance(value, bool) or isinstance(value, str) or isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EditError(INVALID, f"property {key!r} must be a finite number")
        return
    raise EditError(INVALID, f"property {key!r} must be a string, a number or a boolean")


def validate_entity_props(props):
    """M3 の props（値 null は削除）。値は文字列・数値・真偽のみ。id・embedding・aliases・fileName・text・summary・先頭 _ は不可。
    名前は文字か _ で始まり、文字・数字・_ だけ（PROPERTY_NAME_PATTERN）。"""
    if not isinstance(props, dict) or not props:
        raise EditError(INVALID, "'props' must be a non-empty JSON object")
    for key, value in props.items():
        if (not isinstance(key, str) or key.strip() == "" or key != key.strip() or len(key) > MAX_PROPERTY_NAME_LENGTH
                or not PROPERTY_NAME_PATTERN.match(key)):
            raise EditError(INVALID, f"invalid property name {key!r} (letters, digits and _ only; it must not start with a digit)")
        if key in FORBIDDEN_PROPERTY_KEYS or key in HIDDEN_PROPERTY_KEYS or key.startswith("_"):
            raise EditError(INVALID, f"property {key!r} cannot be edited")
        _check_value(key, value, allow_null=True)
    return props


def validate_relationship_props(props, allow_null):
    """リレーションの props。記録用の値（先頭 _）は付けない。"""
    if props is None:
        return {}
    if not isinstance(props, dict):
        raise EditError(INVALID, "'props' must be a JSON object")
    for key, value in props.items():
        if not isinstance(key, str) or key.strip() == "" or key != key.strip() or key.startswith("_"):
            raise EditError(INVALID, f"invalid property name {key!r}")
        _check_value(key, value, allow_null=allow_null)
    return props
