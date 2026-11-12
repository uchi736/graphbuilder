"""抽出に失敗したチャンク（抽出が例外で止まったチャンク）の記録（T16）。

LLM（または EDC）の抽出で例外になったチャンクは、ファイル全体を失敗にしないよう飛ばされる
（llm.get_graph_document_list と edc_extraction.get_graph_from_edc）。これまで飛ばしたことは backend のログの警告にしか残らず、
文書は Completed になっていた。ここで飛ばしたチャンクの ID を集め、Document ノードに次の 2 つを書く。
    skipped_chunks     飛ばしたチャンクの数（0 なら飛ばしていない）
    skipped_chunk_ids  飛ばしたチャンクの ID の一覧
この値は、ファイル一覧の状態の欄、/extract の応答の skipped_chunks、取り込みツール（tools/ingest/ingest.py）、
「保守 > 設定と数」の表に出る。数えるのは例外になった抽出だけで、LLM の出力を解釈できず空の結果になったチャンクは数えない。

抽出し直し: 再処理の「続きから」（start_from_last_processed_position）は、記録したチャンクを必ず一覧に入れる
（main.get_chunkId_chunkDoc_list。他の文書と共有するエンティティへの HAS_ENTITY が残っていても入る）。全部のチャンクを
処理し終えた文書（Completed）では、記録したチャンクだけを選ぶ。記録したチャンクだけのバッチがまた全部失敗しても文書は失敗にせず、
記録を残して終える（main.processing_source）。画面の再処理の窓は、記録のある Completed の文書でも「続きから」を出す。

集め方: processing_source が begin() で入れ物を作る（続きからは前回の一覧で始める）。各バッチの直前に forget() で
そのバッチの ID を外し、抽出が note() で失敗した ID を足し、save() で文書に書く。取り消しや例外で止まっても、
まだ処理していないバッチの記録は残る。入れ物は ContextVar に置く。要求ごとに別のタスクで動くので、
同時に動く別の文書の取り込みとは混ざらない。begin() を呼んでいない所（試験や別の道具からの抽出）では note() は何もしない。
"""
import contextvars
import logging

_box = contextvars.ContextVar("skipped_chunk_ids", default=None)

# 記録済みの一覧を読む。1 行に 1 件で返す（Neo4jGraph は sanitize のとき 128 件以上のリストを結果から落とすため、
# リストのまま返すと読めない）。今の文書のチャンクでない ID（分割し直す前の古い ID など）は捨てる
PREVIOUS_IDS_QUERY = """
MATCH (d:Document {fileName: $f})
OPTIONAL MATCH (d)<-[:PART_OF]-(c:Chunk)
WITH d, collect(c.id) AS mine
WITH [id IN coalesce(d.skipped_chunk_ids, []) WHERE id IN mine] AS ids
UNWIND range(0, size(ids) - 1) AS i
RETURN ids[i] AS id ORDER BY i
"""


def begin(previous=None):
    """入れ物を作る。続きからの再処理では前回の一覧（previous）で始め、処理し直すバッチの分だけ forget() で外す"""
    _box.set(list(dict.fromkeys(c for c in (previous or []) if c)))


def forget(chunk_ids):
    """これから処理し直すチャンクの ID を一覧から外し、外した ID を返す（そのバッチで抽出がまた失敗すれば note() で入り直す）"""
    box = _box.get()
    if box is None:
        return []
    drop = set(chunk_ids or [])
    removed = [c for c in box if c in drop]
    box[:] = [c for c in box if c not in drop]
    return removed


def note(chunk_ids):
    """飛ばしたチャンクの ID を足す。結合したチャンクなら、まとめた全部の ID を渡す"""
    box = _box.get()
    if box is None:
        return
    for c in chunk_ids or []:
        if c and c not in box:
            box.append(c)


def current():
    """今の一覧の写し"""
    box = _box.get()
    return list(box) if box else []


def previous_ids(graph, file_name):
    """文書に記録済みの一覧（続きからの再処理の前に読む）"""
    rows = graph.query(PREVIOUS_IDS_QUERY, {"f": file_name}, session_params={"database": graph._database})
    return [r["id"] for r in rows if r.get("id")]


def save(graph, file_name):
    """今の一覧を文書に書き、件数を返す。書けなくても取り込みは止めない（ログに残す）"""
    ids = current()
    try:
        graph.query("MATCH (d:Document {fileName: $f}) SET d.skipped_chunks = $n, d.skipped_chunk_ids = $ids",
                    {"f": file_name, "n": len(ids), "ids": ids}, session_params={"database": graph._database})
    except Exception as e:
        logging.warning(f"{file_name}: 抽出に失敗したチャンクの記録を書けなかった: {e}")
    if ids:
        logging.warning(f"{file_name}: 抽出に失敗したチャンク {len(ids)} 件（Document.skipped_chunk_ids に記録）")
    return len(ids)
