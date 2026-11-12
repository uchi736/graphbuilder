"""実物の試験: 章・節への参照の付け直し（M1〜M4。同じ参照の結び先のまとめは既定で無効）を、本番グラフの書き出しで流す（グラフにはつながない）。

入力: ../refsec/refs_dump.json（チャンク 70・章・節への参照 36・文書層の参照 15、X18 の点検対象）、
      ../refsec/refs_review.json（36 件の判定）、night1109_links.json（本番の LLM の参照リンク 52 本と位置の語）。
      11 月 9 日の作り直しの前に本番から書き出し直した控えが night1109_refs_dump_pre_rebuild.json（refs_dump.json と同じ内容であることを確かめた）。
      新しいコードを当てた章・節への参照の線（人の再判定 X20 の材料）は night1109_refs_after_m1m6.json（既定と、まとめを有効にした場合）。
      作り直した後のグラフでは LLM の参照リンクが変わるので、この試験の入力は作り直し前の書き出しのまま使う。
確かめること:
  - 変更前の付け直しを書き出しから再現すると、本番の 36 件と一致する（入力が本番と同じであることの確かめ）
  - 誤り・広すぎるの 20 件のうち、位置の語に章の番号があるもの 7 件（点検表 4・5・10・11・21・27・33）が正しい章へ付く
  - 文書番号の手がかりのある 2 件（14・25）は、まとめを有効にしたときだけ、同じ参照の結び先をまとめた節へ付く
  - 正しかった 16 件が、正しい節（判定の理由に合う節）から外れない（外れうるものは「要再判定」の印を付ける）
  - 文書層の一文の節番号（「HS-VTR-T001の3-5項」）から、参照元のチャンクが HS-VTR-T001E の 3-5 へ付く（M3）
  - 判定の見込み（新しい節を、点検表の理由に照らして Claude が付けたもの。人の再判定ではない）の移り変わりを、
    既定（まとめ無効）と、まとめを有効にした場合（section_layer.MERGE_LLM_TARGETS = True）の 2 通りで表に出す
  - 点検表の正解のチャンク（refs_review.json の correct_target）を、新しい節（下位 3 段まで）がどれだけ含むかを数え、
    含む数が減った項目には必ず「要再判定」の印があることを確かめる
実行（ネットワークなし・backend を読み取り専用で載せた一時コンテナ）:
  docker run --rm --memory=4g --cpus=2 --network none -e PYTHONPATH=/code -v <repo>/backend:/code:ro \
    -v <repo>/tools/kg:/kg:ro --entrypoint sh llm-graph-builder-backend -c "cd /code && python /kg/retrieval_tests/t_section_refs_real.py"
"""
import collections
import importlib.machinery
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REFSEC = os.path.join(os.path.dirname(HERE), "refsec")
sys.path.insert(0, os.environ.get("CODE_DIR", "/code"))
from src import document_layer_rules as R  # noqa: E402
from src import section_layer as SL  # noqa: E402

cfg = R.load_config()
DUMP = json.load(open(os.path.join(REFSEC, "refs_dump.json"), encoding="utf-8"))
REVIEW = json.load(open(os.path.join(REFSEC, "refs_review.json"), encoding="utf-8"))
LINKS = json.load(open(os.path.join(HERE, "night1109_links.json"), encoding="utf-8"))["links"]
ROWS = [{"id": c["id"], "fileName": c["fileName"], "position": c["position"], "section": c.get("heading"), "section_path": None,
         "text": c["text"], "page_number": None} for c in DUMP["chunks"]]
# 文書層: 版（Document -IS_VERSION_OF-> Standard）と、文書 → 文書番号の参照と一文（Document -REFERS_TO-> Standard）
VERSIONS = sorted({(r["target_file"], r["standard"], R.number_of_file(r["target_file"], cfg)[1]) for r in DUMP["document_refs"]})
DOC_QUOTES = [(r["source_file"], r["standard"], r["quotes"]) for r in DUMP["document_refs"]]


class DumpGraph:
    """書き出しから、付け直しが読む照会に答える。links_chunk_only なら変更前の照会（結び先がチャンクのリンクだけ）の形で返す。"""

    def query(self, cypher, params=None):
        assert not any(w in cypher for w in ("CREATE ", "MERGE ", "DELETE ", " SET ", "REMOVE ")), "書き込みの照会"
        if "MATCH (c:Chunk)-[:PART_OF]->(d:Document)" in cypher and "c.section_path" in cypher:
            return ROWS
        if "IS_VERSION_OF" in cypher:
            return [{"f": f, "st": st, "rev": rev} for f, st, rev in VERSIONS]
        if "r.quotes AS quotes" in cypher:
            return [{"f": f, "st": st, "quotes": q} for f, st, q in DOC_QUOTES]
        if "WHERE b:Chunk OR b:Document" in cypher:
            return [dict(l) for l in LINKS]
        if "MATCH (a:Chunk)-[r:REFERS_TO]->(b:Chunk)" in cypher:      # 変更前の照会
            return [{"src": l["src"], "tgt": l["tgt"], "tgt_file": l["tgt_file"], "locator": l["locator"], "quote": l["quote"], "method": l["method"]}
                    for l in LINKS if l["tgt"]]
        raise AssertionError("想定外の照会: " + cypher[:80])


def _new():
    return SL.build_section_layer(DumpGraph(), write=False)


def _old():
    path = os.path.join(os.environ.get("CODE_DIR", "/code"), "src", "section_layer.py.bak.night1109")
    if not os.path.exists(path):
        return None
    mod = importlib.machinery.SourceFileLoader("old_section_layer", path).load_module()
    return mod.build_section_layer(DumpGraph(), write=False)


NEW = _new()
SECTIONS = NEW["sections"]


def label(sid):
    s = SECTIONS[sid]
    return s["fileName"].rsplit(".", 1)[0], ((s["number"] + " ") if s["number"] else "") + s["title"]


def new_sections_of(index, merge=False):
    """点検表の 1 件（参照元・一文・変更前の節）について、同じ参照のリンクだけを流したときの新しい節。merge は MERGE_LLM_TARGETS の値。"""
    return sorted(label(sid) for sid in new_section_ids(index, merge))


def new_section_ids(index, merge=False):
    item = DUMP["section_refs"][index]
    old_primary = OLD["chunk_section"] if OLD else {}
    same = [l for l in LINKS if l["src"] == item["source"] and (l["quote"] or "")[:120] == item["quote"]]
    own = [l for l in same if l["tgt"] and old_primary.get(l["tgt"]) == item["section_id"]] or same
    files = {l["tgt_file"] for l in own}
    links = [l for l in same if l["tgt_file"] in files]

    class G(DumpGraph):
        def query(self, cypher, params=None):
            if "WHERE b:Chunk OR b:Document" in cypher:
                return [dict(l) for l in links]
            if "r.quotes AS quotes" in cypher:
                return []
            return super().query(cypher, params)
    _s, chunk_sections = SL.sections_from_catalogue(ROWS, cfg)
    saved, SL.MERGE_LLM_TARGETS = SL.MERGE_LLM_TARGETS, merge
    try:
        refs, _ = SL.resolve_section_refs(G(), ROWS, SECTIONS, chunk_sections, NEW["chunk_section"], cfg)
    finally:
        SL.MERGE_LLM_TARGETS = saved
    return sorted({k[1] for k in refs if k[0] == item["source"]})


OLD = _old()

# 新しい節（文書名, 「番号 題名」の先頭）と、判定の見込み。見込みは点検表の理由に照らして付けた（人の再判定ではない）。
# 新しい節は、同じ参照（参照元・一文）の線をすべて並べたもの。判定の見込みは、その点検表の項目の線（変更前の線の結び先のチャンクから
# 作られる線）について付けた。「要再判定」は、節が変わって正解の含みや範囲が変わったため、人が見直すべきもの
T1, T2, T3, T4, T5 = "HS-VTR-T001E", "HS-VTR-T002F", "VTR-T003D", "VTR-T004C", "VTR-T005B"
RE = "要再判定: "
EXPECTED = {
    0: ([(T3, "4 基準来歴")], "広すぎる", "別紙-1 の節が無く解けない（unresolved_locator に残す）"),
    1: ([(T4, "ABB方式")], "誤り", "番号なし（添付資料）"),
    2: ([(T4, "コンプレッサ性能計測要領")], "正しい", "別表1 の節が無く LLM の結び先のまま"),
    3: ([(T3, "2 コンプレサ性能管理曲線")], "正しい", "主所属の直しで 1 章から 2 章（作成及び管理、T003D#2）へ"),
    4: ([(T4, "2 単独性能確認試験要領")], "正しい", "M1 章 2"),
    5: ([(T5, "2 単独性能確認試験要領")], "正しい", "M1 章 2"),
    6: ([(T5, "初号機 合否判定方法")], "広すぎる", "番号なし（「2)」は文自身の箇条の番号）"),
    7: ([(T5, "ABB方式")], "誤り", "番号なし（「3)」は文自身の箇条の番号）"),
    8: ([(T5, "コンプレッサ性能計測要領")], "正しい", "別表1 の節が無く LLM の結び先のまま"),
    9: ([(T5, "2-1 "), (T5, "2-2 "), (T5, "2-3 ")], "正しい", "範囲「2-1~2-3」を 3 節に広げた"),
    10: ([(T5, "2 単独性能確認試験要領"), (T5, "3 性能管理判定基準")], "正しい", "M1 章 2・3"),
    11: ([(T5, "2 単独性能確認試験要領"), (T5, "3 性能管理判定基準")], "正しい", "M1 章 2・3"),
    12: ([(T3, "2 コンプレサ性能管理曲線")], "正しい", "主所属の直し"),
    13: ([(T5, "コンプレッサ性能計測要領")], "誤り", "番号なし（添付資料）"),
    18: ([(T3, "2 コンプレサ性能管理曲線")], "正しい", RE + "主所属の直しで 1 適用 から 2 章へ。正解 T003D#1・#2 のうち #1 が外れ、#3 が入る"),
    19: ([(T1, "2-1 性能試験"), (T1, "2-2 全力運転試験")], "正しい", "2-2 の線も足した"),
    20: ([(T2, "3 性能換算法")], "誤り", "参照ではない（図の中の語）"),
    21: ([(T2, "2 単独性能確認試験要領")], "正しい", "M1 章 2"),
    22: ([(T2, "2 テスト方法")], "誤り", "番号なし（上記例）"),
    23: ([(T2, "別表2 過給機性能試験")], "正しい", RE + "M1 付属の節を題名で引いた。別表2 の根の節（#11〜#17）で、正解外が 1 件から 5 件に増える（広すぎるになりうる）"),
    26: ([(T2, "4-1 "), (T2, "4-2 ")], "正しい", "4-2 の線も足した"),
    27: ([(T2, "2 単独性能確認試験要領"), (T2, "3 性能管理判定基準")], "正しい", "M1 章 2・3"),
    28: ([(T2, "2 単独性能確認試験要領"), (T2, "3 性能管理判定基準")], "正しい", "M1 章 2・3"),
    29: ([(T3, "2 コンプレサ性能管理曲線")], "正しい", "主所属の直し"),
    31: ([(T2, "コンプレッサ性能計測要領")], "正しい", "別表1 の節が無く LLM の結び先のまま"),
    33: ([(T2, "2 単独性能確認試験要領")], "正しい", "M1 章 2"),
}
# 同じ参照の結び先のまとめ（MERGE_LLM_TARGETS）で結果が分かれる 10 件。既定（無効）では、LLM の結び先の線は従来どおりチャンクごとの
# 主所属の節へ付くので、M4 で主所属が見出しの節に移った分だけ行き先が変わる
_T2_OFF = [(T2, "2 テスト方法"), (T2, "3-2 圧力比")]
EXPECTED_MERGE_OFF = {
    14: (_T2_OFF, "広すぎる", "この項目の線（#13 から）は 3-2 のまま（#16・#17 を含む）"),
    15: ([(T5, "過給機性能試験及び性能換算法"), (T5, "3 圧力比")], "正しい", RE + "主所属の直しで 2 章から 3 章 圧力比（#12）へ。正解のチャンクだけを含む"),
    16: ([(T5, "過給機性能試験及び性能換算法"), (T5, "3 圧力比")], "正しい", "この項目の線（根の節）は変わらない"),
    17: (_T2_OFF, "正しい", RE + "#12 の主所属の直しで 1 章から 2 テスト方法（#12・#13 と #14）へ。正解の含みは同じ"),
    24: ([(T1, "1 適 用"), (T1, "2-1 性能試験")], "正しい", "この項目の線（2-1）は変わらない"),
    25: ([(T1, "1 適 用"), (T1, "2-1 性能試験")], "誤り", RE + "#1 の主所属の直しで、文書の根の節から 1 適用 へ。性能試験の節ではない"),
    30: (_T2_OFF, "誤り", "1 章から 2 テスト方法へ（正解の #13・#14 を含むが、初号機合否判定方法の節ではない）"),
    32: (_T2_OFF, "誤り", "この項目の線は 3-2 のまま"),
    34: (_T2_OFF, "誤り", "この項目の線は 3-2 のまま"),
    35: (_T2_OFF, "正しい", RE + "1 章から 2 テスト方法へ。点検表 34 の理由が指す節（型式区分の表 #12）で、#13・#14 も含む"),
}
EXPECTED_MERGE_ON = {
    14: ([(T2, "1 基本的考え方")], "正しい", "同じ参照の結び先 T002F#12・#13 を 1 節に（点検表 17 と同じ節で、正しいと判定済み）"),
    15: ([(T5, "2 仕様吸入状態温度")], "正しい", "変わらない"),
    16: ([(T5, "2 仕様吸入状態温度")], "正しい", "根の節から、空気量（#11）と圧力比（#12）を含む 2 章へ"),
    17: ([(T2, "1 基本的考え方")], "正しい", "変わらない"),
    24: ([(T1, "2-1 性能試験")], "正しい", "変わらない"),
    25: ([(T1, "2-1 性能試験")], "正しい", RE + "同じ参照の結び先 T001E#1〜#3 を 2-1 にまとめた（点検表 24 と同じ節）。正解 6 チャンクのうち 3 しか含まない"),
    30: ([(T2, "1 基本的考え方")], "誤り", "番号なし（添付資料）"),
    32: ([(T2, "1 基本的考え方")], "誤り", "番号なし（添付資料）。正解の含みは 2/4 から 1/4 に減る"),
    34: ([(T2, "1 基本的考え方")], "広すぎる", "点検表 35 と同じ節（型式区分の表 #12 を含むが #11・#13 も含む）"),
    35: ([(T2, "1 基本的考え方")], "広すぎる", "変わらない"),
}


def expected_for(merge):
    out = dict(EXPECTED)
    out.update(EXPECTED_MERGE_ON if merge else EXPECTED_MERGE_OFF)
    return dict(sorted(out.items()))


def _matches(got, expected):
    return len(got) == len(expected) and all(any(f == ef and name.startswith(en) for f, name in got) for ef, en in expected)


# ───────────────────────── 試験 ─────────────────────────
def test_変更前の付け直しが本番の36件を再現する():
    if OLD is None:
        print("    （控え section_layer.py.bak.night1109 が無いので飛ばす）")
        return
    got = {(k[0], k[1]) for k in OLD["section_refs"]}
    want = {(r["source"], r["section_id"]) for r in DUMP["section_refs"]}
    assert got == want, (len(got), len(want), len(got ^ want))


def test_節の数とidは変わらない():
    if OLD is None:
        return
    assert set(OLD["sections"]) == set(NEW["sections"]) and len(NEW["sections"]) == 116


def test_36件の新しい節():
    assert sorted(expected_for(False)) == sorted(expected_for(True)) == list(range(36))
    bad = []
    for merge in (False, True):
        for index, (expected, _verdict, _note) in expected_for(merge).items():
            got = new_sections_of(index, merge)
            if not _matches(got, expected):
                bad.append((merge, index, got, expected))
    assert not bad, bad


def test_番号で直るはずの7件と文書番号の2件():
    for merge in (False, True):
        for index in (4, 5, 10, 11, 21, 27, 33):
            got = new_sections_of(index, merge)
            assert all(not name.startswith("1-2 初号機とは") for _f, name in got), (index, got)
            assert any(name.startswith("2 単独性能確認試験要領") for _f, name in got), (index, got)
    # 文書番号の手がかりの 2 件は、まとめを有効にしたときだけ 1 節にまとまる（既定では従来どおりチャンクごとの主所属の節）
    assert new_sections_of(14, True) == [(T2, "1 基本的考え方")]
    assert new_sections_of(25, True) == [(T1, "2-1 性能試験")]
    assert len(new_sections_of(14, False)) == 2 and len(new_sections_of(25, False)) == 2


def test_文書層の一文の節番号から節へ():
    refs = NEW["section_refs"]
    hits = [(k[0], label(k[1]), v["via"], v.get("standard")) for k, v in refs.items() if v.get("via") == "standard"]
    file_of = {c["id"]: (c["fileName"], c["position"]) for c in DUMP["chunks"]}
    got = sorted((file_of[src], lab, st) for src, lab, _via, st in hits)
    assert got == [(("VTR-T004C.pdf", 3), (T1, "3-5 潤滑油供給条件"), "HS-VTR-T001"),
                   (("VTR-T005B.pdf", 3), (T1, "3-5 潤滑油供給条件"), "HS-VTR-T001")], got
    s = NEW["summary"]
    assert s["standard_section_refs"] == 2 and s["standard_unresolved"] == 1   # 付表6 は現行の版に無い


def test_主所属は見出しの節():
    file_of = {c["id"]: (c["fileName"], c["position"]) for c in DUMP["chunks"]}
    prim = {file_of[cid]: label(sid) for cid, sid in NEW["chunk_section"].items()}
    assert prim[("HS-VTR-T001E.pdf", 9)] == (T1, "3-5 潤滑油供給条件")      # 見出し「### 3-5 潤滑油供給条件」のチャンク
    assert prim[("HS-VTR-T001E.pdf", 10)] == (T1, "3-6 潤滑油温度")
    assert prim[("VTR-T004C.pdf", 2)] == (T4, "2 単独性能確認試験要領")
    assert prim[("HS-VTR-T002F.pdf", 16)][1].startswith("3 性能換算法")   # 見出しの番号が同じで題名の違う節には付けない


# 点検表の正解のチャンクを、節（下位 3 段まで）がどれだけ含むか
_CHILDREN = collections.defaultdict(list)
for _sid, _sec in SECTIONS.items():
    if _sec.get("parent"):
        _CHILDREN[_sec["parent"]].append(_sid)
_NEW_CS = SL.sections_from_catalogue(ROWS, cfg)[1]


def _chunks_under(sid, chunk_sections, children):
    tree, frontier = {sid}, [sid]
    for _ in range(3):
        frontier = [c for f in frontier for c in children.get(f, [])]
        tree |= set(frontier)
    return {c for c, lst in chunk_sections.items() if any(x in tree for x, _p in lst)}


def coverage(index, merge):
    """(変更前の線の含む正解の数, 新しい線の含む正解の数, 正解の数, 変更前の正解外の数, 新しい正解外の数)。新しい線は同じ参照の線すべての和。"""
    correct = set(REVIEW["section_refs"][index].get("correct_target") or [])
    old_sid = DUMP["section_refs"][index]["section_id"]
    old_children = collections.defaultdict(list)
    for sid, sec in OLD["sections"].items():
        if sec.get("parent"):
            old_children[sec["parent"]].append(sid)
    old_cs = {cid: [(sid, True) for sid in c["sections"]] for cid, c in ((c["id"], c) for c in DUMP["chunks"])}
    old = _chunks_under(old_sid, old_cs, old_children) if old_sid in OLD["sections"] else set()
    new = set().union(*[_chunks_under(sid, _NEW_CS, _CHILDREN) for sid in new_section_ids(index, merge)] or [set()])
    return len(old & correct), len(new & correct), len(correct), len(old - correct), len(new - correct)


def _verdict_table(merge):
    moves = collections.Counter()
    before, after = collections.Counter(), collections.Counter()
    print("    ── %s ──" % ("まとめを有効にした場合（MERGE_LLM_TARGETS = True）" if merge else "既定（まとめ無効）"))
    for index, (_expected, verdict, note) in expected_for(merge).items():
        old = REVIEW["section_refs"][index]["verdict"]
        before[old] += 1
        after[verdict] += 1
        moves[(old, verdict)] += 1
        if old != verdict or note.startswith(RE):
            print("    %2d %s → %s  %s" % (index, old, verdict, note))
    print("    変更前 %s / 見込み %s" % (dict(before), dict(after)))
    print("    要再判定 %s" % [i for i, (_e, _v, n) in expected_for(merge).items() if n.startswith(RE)])
    return moves, before, after


def test_判定の見込み():
    """移り変わりを表に出す（2 通り）。正しかったものが見込みで悪化しないこと、誤りが減ることを確かめる。"""
    if OLD is None:
        return
    for merge in (False, True):
        moves, before, after = _verdict_table(merge)
        assert moves[("正しい", "誤り")] == 0 and moves[("正しい", "広すぎる")] == 0
        assert after["正しい"] >= before["正しい"] and after["誤り"] < before["誤り"]


def test_正解の含みが減った項目には要再判定の印がある():
    """点検表の正解のチャンクを新しい線が含む数が、変更前より減った項目は、見込みの表で「要再判定」としていること。"""
    if OLD is None:
        return
    for merge in (False, True):
        table = expected_for(merge)
        down, more_extra = [], []
        for index in table:
            old_hit, new_hit, n, old_extra, new_extra = coverage(index, merge)
            if new_hit < old_hit:
                down.append(index)
            if new_extra >= old_extra + 3:
                more_extra.append(index)
        print("    %s: 正解の含みが減った %s、正解外が 3 件以上増えた %s" % ("有効" if merge else "既定", down, more_extra))
        for index in down + more_extra:
            note = table[index][2]
            if index in down and table[index][1] == "誤り":
                continue   # もともと誤りの見込みのものは、含みが減っても判定は変わらない
            assert note.startswith(RE), (merge, index, note)


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = [(name, fn) for name, fn in list(globals().items()) if name.startswith("test_") and callable(fn) and only in name]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  ok  " + name[5:], flush=True)
        except Exception:
            failed += 1
            print("  NG  " + name[5:], flush=True)
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} 件 合格")
    sys.exit(1 if failed else 0)
