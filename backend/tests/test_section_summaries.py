# -*- coding: utf-8 -*-
"""節の要点（後処理 build_section_summaries）と、参照追跡の一覧選択（reference_doc_select = summary_list）の試験。DB・LLM・ネットワークなし。

  要点の作成: LLM と DB を差し替え、子 → 章の順、作り直しの省略、控えからの書き戻し、失敗の飛ばし、打ち切り、同時数の上限を確かめる
             打ち切りの後も浅い段は書き戻すこと、HTTP のエラーと設定の誤りの扱い、complete、控えの掃除と貸し出し、同時に 1 本の鍵、書き戻しだけの実行
  一覧選択: 候補の作り方（リランカーの上位 20 件）、番号の解析、範囲外・空・失敗で rerank に戻る、上限、2 段（章 → 節）、文字数予算
             本文の点数の下限、要点の無い文書、2 回目のたどりの数と LLM の呼び出し、1 つの質問の中での LLM の失敗の記憶、行の取り分
  設定: 既定の検索（参照をたどらない 4 方式）が変わらないこと、参照追跡の方式の既定も rerank のままであること
  要点が既定の検索の文脈に出ないこと（文脈の照会の形で確かめる）
実行: docker run --rm --memory=4g --network none -v $PWD/backend:/code -w /code llm-graph-builder-backend:latest \
        python -m pytest -q -p no:cacheprovider tests/test_section_summaries.py
"""
import ast
import collections
import fcntl
import logging
import os
import re
import sys
import threading
import time

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("VLLM_EMBEDDING_ENDPOINT", "http://127.0.0.1:9/v1,dummy")

from langchain_core.documents import Document  # noqa: E402

from src import section_summaries as SS  # noqa: E402
from src import rerank_retriever as rr  # noqa: E402
from src.shared import constants as C  # noqa: E402


# ============================================================
# 要点の作成
# ============================================================
class FakeGraph:
    """build_section_summaries が流す照会を、辞書の上で真似る。"""

    def __init__(self, sections, bodies):
        self.sections = {s["id"]: dict(s) for s in sections}   # id -> {file_name, path, level, parent, summary, summary_src, ...}
        self.bodies = bodies                                     # id -> [本文, ...]（主所属のチャンクの位置の順）
        self.cache = {}                                          # src -> {summary, model, at, fileName}
        self.writes = []
        self.queries = []
        self.lease = []                                          # グラフの貸し出しの持ち主の列（照会ごとに先頭から 1 つずつ。空なら貸し出しなし）

    def run(self, query, params=None):
        params = params or {}
        self.queries.append(query)
        if query == SS.CACHE_INDEX_QUERY:
            return []
        if query == SS.LEASE_HOLDER_QUERY:
            owner = self.lease.pop(0) if self.lease else None
            return [{"owner": owner}] if owner else []
        if query == SS.CACHE_ANY_QUERY:
            return [{"src": k} for k in list(self.cache)[:1]]
        if query == SS.SECTIONS_QUERY:
            files = params.get("files")
            return [{"id": sid, "file_name": s["file_name"], "path": s.get("path"), "title": s.get("title"), "number": None,
                     "level": s.get("level"), "parent": s.get("parent"), "summary": s.get("summary"), "summary_src": s.get("summary_src"),
                     "text": s.get("text")}
                    for sid, s in self.sections.items() if files is None or s["file_name"] in files]
        if query == SS.PRIMARY_BODY_QUERY:
            return [{"sid": sid, "text": t, "position": i} for sid, texts in self.bodies.items() if sid in self.sections
                    for i, t in enumerate(texts)]
        if query == SS.CACHE_LOOKUP_QUERY:
            return [{"src": src, "rejected": False, **self.cache[src]} for src in params["srcs"] if src in self.cache]
        if query == SS.WRITE_QUERY:
            for row in params["rows"]:
                self.writes.append(row)
                if row["id"] in self.sections:
                    self.sections[row["id"]].update(summary=row["summary"], summary_model=row["model"], summary_at=row["at"],
                                                    summary_src=row["src"])
                    self.cache[row["src"]] = {"summary": row["summary"], "model": row["model"], "at": row["at"], "fileName": row["fileName"]}
            return []
        if query == SS.REJECT_QUERY:
            for row in params["rows"]:
                self.sections[row["id"]].pop("summary", None)
                self.sections[row["id"]].update(summary_src=row["src"], summary_model=row["model"], summary_at=row["at"])
                self.cache[row["src"]] = {"summary": "", "rejected": True, "model": row["model"], "at": row["at"], "fileName": row["fileName"]}
            return []
        if query == SS.CLEAR_QUERY:
            for sid in params["ids"]:
                for key in ("summary", "summary_model", "summary_at", "summary_src"):
                    self.sections[sid].pop(key, None)
            return []
        if query == SS.CACHE_PRUNE_QUERY:
            live = {s.get("summary_src") for s in self.sections.values() if s.get("summary_src")}
            dead = [k for k in self.cache if k not in live and k not in set(params["keep"])]
            for k in dead:
                del self.cache[k]
            return [{"removed": len(dead)}]
        raise AssertionError("unexpected query: " + query[:80])

    def rebuild_section_layer(self):
        """章・節の層の後処理と同じく、節を作り直す（要点の属性が消える）。"""
        for s in self.sections.values():
            for key in ("summary", "summary_model", "summary_at", "summary_src"):
                s.pop(key, None)


def world():
    sections = [
        {"id": "c1", "file_name": "HS-VTR-T001E.pdf", "path": ["3 試験要領"], "level": 1, "parent": None},
        {"id": "s11", "file_name": "HS-VTR-T001E.pdf", "path": ["3 試験要領", "3-5 潤滑油供給条件"], "level": 2, "parent": "c1"},
        {"id": "s12", "file_name": "HS-VTR-T001E.pdf", "path": ["3 試験要領", "3-6 計測"], "level": 2, "parent": "c1"},
        {"id": "c2", "file_name": "HS-VTR-T001E.pdf", "path": ["4 判定"], "level": 1, "parent": None},
        {"id": "empty", "file_name": "HS-VTR-T001E.pdf", "path": ["付属"], "level": 1, "parent": None},
    ]
    bodies = {"c1": ["3. 試験要領の総則。"], "s11": ["油の供給圧力は 0.25 MPa、温度は 45 ℃ とする。" * 3],
              "s12": ["回転速度と圧力比を計測する。"], "c2": ["判定は 4-1 項による。"]}
    return FakeGraph(sections, bodies)


class FakeSummarize:
    def __init__(self, fail=(), error=None):
        self.calls = []
        self.fail = set(fail)
        self.error = error
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def __call__(self, user_text):
        with self.lock:
            self.calls.append(user_text)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.01)
            if self.error is not None:
                raise self.error
            if any(key in user_text for key in self.fail):
                raise RuntimeError("boom")
            head = user_text.split("\n", 1)[0]
            return f"要点：{head} の要点。\n2 行目", "google/gemma-4-26B-A4B-it"
        finally:
            with self.lock:
                self.active -= 1


def test_build_makes_children_first_and_chapters_with_child_summaries():
    g, llm = world(), FakeSummarize()
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0)["summary"]
    assert out["sections"] == 5 and out["made"] == 4 and out["empty"] == 1 and out["failed"] == 0
    assert out["made_with_children"] == 1 and out["model"] == "google/gemma-4-26B-A4B-it" and out["seconds"] >= 0
    # 子（3-5・3-6）が章（3）より先に作られ、章の入力に子の要点が入る
    chapter_input = next(c for c in llm.calls if c.startswith("[HS-VTR-T001E 3 試験要領]"))
    assert "### 下位の節の要点" in chapter_input and "3-5 潤滑油供給条件: [HS-VTR-T001E 3 試験要領 > 3-5 潤滑油供給条件] の要点。2 行目" in chapter_input
    assert llm.calls.index(chapter_input) > max(i for i, c in enumerate(llm.calls) if "3-5 潤滑油供給条件]" in c.split("\n")[0])
    s = g.sections["s11"]
    assert s["summary"] == "[HS-VTR-T001E 3 試験要領 > 3-5 潤滑油供給条件] の要点。2 行目"   # 前置き「要点：」と改行を落とす
    assert s["summary_model"] == "google/gemma-4-26B-A4B-it" and re.match(r"\d{4}-\d\d-\d\dT", s["summary_at"])
    assert s["summary_src"] == SS.summary_src(SS.summary_input(dict(s, file_name="HS-VTR-T001E.pdf"), "\n".join(g.bodies["s11"]), []))
    assert "summary" not in g.sections["empty"]      # 本文も子も無い節は作らない
    # 章（level 1）でも子が無ければ本文だけ
    c2 = next(c for c in llm.calls if c.startswith("[HS-VTR-T001E 4 判定]"))
    assert "下位の節" not in c2


def test_chapter_input_ends_with_the_chapter_note():
    g, llm = world(), FakeSummarize()
    SS.build_section_summaries(run=g.run, summarize=llm, backoff=0)
    chapter_input = next(c for c in llm.calls if c.startswith("[HS-VTR-T001E 3 試験要領]"))
    assert chapter_input.endswith(SS.CHAPTER_NOTE)


def test_clean_summary_drops_meta_answers():
    # 11/9 の kgb4 で出た答え（要約）。断り・前置き・箇条書きの文を除き、要点の文だけを残す
    assert SS.clean_summary("要点：油の供給圧力を定める。\n温度も定める。3 文目。") == "油の供給圧力を定める。温度も定める。"
    assert SS.clean_summary("対象の入力テキスト（3項の内容）が提供されていません。解析対象となる「3項」の文章を貼り付けていただければ、即座に作成いたします。") == ""
    assert SS.clean_summary("ご提示いただいた「下位の節の要点」を、指示された形式にまとめると以下のようになります。 "
                            "VTR-T004C初号機の合否判定について、HS-VTR-T005に基づく試運転方法を定める。") == \
        "VTR-T004C初号機の合否判定について、HS-VTR-T005に基づく試運転方法を定める。"
    assert SS.clean_summary("ご提示いただいたテキストは、表題のみであり、具体的な規定が含まれておりません。 対象となる**「1節」の本文**をご提示いただければ作成します。 * **内容：** 対象") == ""
    assert SS.usable_summary("油の供給圧力を定める。") and not SS.usable_summary("ご提示いただいた各節の要点を整理します。 1 節: 定める。")
    assert not SS.usable_summary("") and not SS.usable_summary(None)


def test_has_body_ignores_heading_with_other_number_style():
    row = {"path": ["番号の付け直し2", "2 ガス流路部切断検査要領"]}
    assert not SS.has_body(row, "2.ガス流路部切断検査要領 \n\n3項")
    assert not SS.has_body({"path": ["5-1 材料試験"]}, "5-1 材料試験、検査成績書\n###") and not SS.has_body({"path": ["題"]}, "##")
    assert SS.has_body({"path": ["4 判定"]}, "判定は 4-1 項による。")


def test_build_rejects_meta_answers_and_regenerates_stored_ones():
    g = world()
    g.sections["s12"].update(summary="ご提示いただいた本文が含まれておりません。", summary_src="x")
    calls = []

    def summarize(user_text):
        calls.append(user_text)
        if "3-6 計測]" in user_text.split("\n")[0]:
            return "対象のテキストが入力されていません。貼り付けていただければ作成いたします。", "m"
        return "油の供給圧力を定める。", "m"

    out = SS.build_section_summaries(run=g.run, summarize=summarize, backoff=0)["summary"]
    assert out["rejected"] == 1 and out["failed"] == 0 and "summary" not in g.sections["s12"]
    assert len([c for c in calls if "3-6 計測]" in c.split("\n")[0]]) == 1       # 断りの答えは送り直さない
    chapter_input = next(c for c in calls if c.startswith("[HS-VTR-T001E 3 試験要領]"))
    assert "3-6 計測:" not in chapter_input
    # 入力が同じなら次の回も聞き直さない。章・節の層の作り直しの後も、控えの印から分かる
    calls.clear()
    out = SS.build_section_summaries(run=g.run, summarize=summarize, backoff=0)["summary"]
    assert calls == [] and out["rejected"] == 1 and out["kept"] == 3
    g.rebuild_section_layer()
    out = SS.build_section_summaries(run=g.run, summarize=summarize, backoff=0)["summary"]
    assert calls == [] and out["rejected"] == 1 and out["restored"] == 3 and g.sections["s12"].get("summary_src")


def test_build_input_is_heading_path_and_first_3000_chars():
    g = world()
    g.bodies["s12"] = ["あ" * 2000, "い" * 2000]
    llm = FakeSummarize()
    SS.build_section_summaries(run=g.run, summarize=llm, backoff=0)
    text = next(c for c in llm.calls if "3-6 計測]" in c.split("\n")[0])
    head, body = text.split("\n", 1)
    assert head == "[HS-VTR-T001E 3 試験要領 > 3-6 計測]" and body == "あ" * 2000 + "\n" + "い" * 999


def test_build_uses_section_text_when_no_primary_chunk():
    # 目録で切り出した小さな節（主所属のチャンクが無い）は、章・節の層が切り出した節の本文 Section.text から作る
    g = world()
    g.sections["empty"]["text"] = "付属の表の本文。"
    g.sections["empty"]["text"] = "付属の表の本文で、試験の記録の様式を定める。"
    g.sections["blank"] = {"id": "blank", "file_name": "HS-VTR-T001E.pdf", "path": ["題"], "level": 1, "parent": None, "text": "##"}
    # 見出しの題と # を除くと中身が 10 字に満たない節は、本文が無いとみなす（「##」だけの節で LLM が断り文を返した）
    g.sections["heading"] = {"id": "heading", "file_name": "HS-VTR-T001E.pdf", "path": ["5-1 材料試験"], "level": 1, "parent": None,
                             "text": "5-1 材料試験、検査成績書\n###", "summary": "前の決め方で作った要点", "summary_src": "old"}
    llm = FakeSummarize()
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0)["summary"]
    assert "[HS-VTR-T001E 付属]\n付属の表の本文で、試験の記録の様式を定める。" in llm.calls and g.sections["empty"]["summary"]
    assert out["made"] == 5 and out["empty"] == 2 and out["cleared"] == 1
    assert "summary" not in g.sections["blank"] and "summary" not in g.sections["heading"]


def test_build_skips_unchanged_and_restores_after_section_layer_rebuild():
    g = world()
    SS.build_section_summaries(run=g.run, summarize=FakeSummarize(), backoff=0)
    before = {k: v.get("summary") for k, v in g.sections.items()}
    llm = FakeSummarize()
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0)["summary"]
    assert llm.calls == [] and out["kept"] == 4 and out["made"] == 0
    # 章・節の層の作り直しで要点が消えても、入力が同じなら控えから書き戻す（LLM を呼ばない）
    g.rebuild_section_layer()
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0)["summary"]
    assert llm.calls == [] and out["restored"] == 4 and out["made"] == 0
    assert {k: v.get("summary") for k, v in g.sections.items()} == before
    # 本文が変わった節（と、その子の要点を入れる章）だけ作り直す
    g.bodies["s12"] = ["計測の本文を改訂した。"]
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0)["summary"]
    assert out["made"] == 1 and out["kept"] == 3 and len(llm.calls) == 1
    # 章の入力の子の要点は同じ文（FakeSummarize は見出しから作る）なので章は据え置き。控えの古い 3-6 の入力は掃除される
    assert out["pruned"] == 1


def test_build_failure_is_skipped_and_others_continue():
    g = world()
    llm = FakeSummarize(fail=("3-5 潤滑油供給条件]",))
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0)["summary"]
    assert out["failed"] == 1 and out["made"] == 3
    assert "summary" not in g.sections["s11"] and g.sections["s12"]["summary"]
    # 章は、作れた子の要点だけで作る
    chapter_input = next(c for c in llm.calls if c.startswith("[HS-VTR-T001E 3 試験要領]"))
    assert "3-6 計測:" in chapter_input and "3-5 潤滑油供給条件:" not in chapter_input
    assert len([c for c in llm.calls if "3-5 潤滑油供給条件]" in c.split("\n")[0]]) == 3   # 2 回まで送り直す


def test_build_aborts_when_llm_does_not_answer():
    g = world()
    g.sections.update({f"x{i}": {"id": f"x{i}", "file_name": "B.pdf", "path": [f"{i} 節"], "level": 1, "parent": None} for i in range(20)})
    g.bodies.update({f"x{i}": ["回転速度と圧力比を計測する本文。"] for i in range(20)})
    llm = FakeSummarize(error=requests.ConnectionError("refused"))
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0, retries=0)["summary"]
    assert out["aborted"] is True and out["made"] == 0
    assert len(llm.calls) < 24, "打ち切った後は LLM を呼ばない"


def test_build_concurrency_is_capped_at_three():
    g = world()
    g.sections.update({f"x{i}": {"id": f"x{i}", "file_name": "B.pdf", "path": [f"{i} 節"], "level": 1, "parent": None} for i in range(30)})
    g.bodies.update({f"x{i}": ["回転速度と圧力比を計測する本文。"] for i in range(30)})
    llm = FakeSummarize()
    out = SS.build_section_summaries(run=g.run, summarize=llm, concurrency=10, backoff=0)["summary"]
    assert out["concurrency"] == 3 and 1 < llm.peak <= 3


def test_build_limited_to_files_does_not_prune():
    g = world()
    g.cache["old"] = {"summary": "古い", "model": "m", "at": "t", "fileName": "X.pdf"}
    out = SS.build_section_summaries(run=g.run, summarize=FakeSummarize(), file_names=["HS-VTR-T001E.pdf"], backoff=0)["summary"]
    assert out["made"] == 4 and out["pruned"] == 0 and "old" in g.cache


def test_chat_payload_and_template_fallback(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_CONFIG_GEMMA4", "google/gemma-4-26B-A4B-it,http://llm:8000/v1,secret-key-not-in-errors")
    sent = []

    class Resp:
        def __init__(self, code, content="[1]"):
            self.status_code, self.ok, self._content = code, code < 400, content

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code}")

        def json(self):
            return {"choices": [{"message": {"content": self._content}}]}

    def post(url, json=None, headers=None, timeout=None):
        sent.append((url, json, headers))
        return Resp(400) if "chat_template_kwargs" in json else Resp(200, "<think>考え</think>[2, 1]")

    monkeypatch.setattr(SS.requests, "post", post)
    monkeypatch.setitem(SS._LLM_STATE, "template_kwargs", True)
    text, name = SS.chat([{"role": "user", "content": "q"}], model_key="gemma4", max_tokens=64, timeout=5)
    assert text == "[2, 1]" and name == "google/gemma-4-26B-A4B-it"
    url, body, headers = sent[0]
    assert url == "http://llm:8000/v1/chat/completions" and body["temperature"] == 0 and body["max_tokens"] == 64
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and headers["Authorization"].endswith("secret-key-not-in-errors")
    assert "chat_template_kwargs" not in sent[1][1] and SS._LLM_STATE["template_kwargs"] is False
    monkeypatch.setenv("LLM_MODEL_CONFIG_BROKEN", "only-a-name-secret")
    with pytest.raises(RuntimeError) as err:
        SS.llm_config("broken")
    assert "secret" not in str(err.value)


# ============================================================
# 一覧選択（部品）
# ============================================================
def items(n, doc="HS-VTR-T001E.pdf", parent_of=None, summary=True):
    out = []
    for i in range(1, n + 1):
        out.append(SS.section_item({"id": f"s{i}", "file_name": doc, "path": [f"{i} 節{i}"], "level": 1 if not parent_of else 2,
                                    "parent": (parent_of or {}).get(f"s{i}"), "summary": f"節{i}の要点" if summary else "",
                                    "head": f"節{i}の本文の先頭"}))
    return out


def test_parse_picks():
    assert SS.parse_picks("[3, 1, 3, 9]", 5, 4) == [2, 0]
    assert SS.parse_picks("```json\n[2,1]\n```", 5, 4) == [1, 0]
    assert SS.parse_picks("番号は 4 と 2 です", 5, 4) == [3, 1]          # JSON でなければ数字を拾う（X16 と同じ）
    assert SS.parse_picks("[1, 2, 3, 4, 5]", 5, 2) == [0, 1]
    assert SS.parse_picks("[]", 5, 4) == [] and SS.parse_picks("[0, 7]", 5, 4) == [] and SS.parse_picks("", 5, 4) == []


def test_list_messages_show_question_source_already_and_numbered_candidates():
    msgs = SS.list_messages("油の温度は", "3-5 項: 運転条件は HS-VTR-T001 による。", ["- HS-RU-003H 2 要領: 要点"], items(2), 4)
    assert msgs[0]["content"] == SS.LIST_SYSTEM
    user = msgs[1]["content"]
    assert user.startswith("### 質問\n油の温度は\n\n### 参照元の節")
    assert "HS-VTR-T001 による" in user and "- HS-RU-003H 2 要領: 要点" in user
    assert "1. [HS-VTR-T001E 1 節1] 節1の要点\n2. [HS-VTR-T001E 2 節2] 節2の要点" in user
    assert user.endswith("最大 4 件の番号を JSON の配列で返せ。")
    # 要点の無い節は本文の先頭を出す
    assert "1. [HS-VTR-T001E 1 節1] 節1の本文の先頭" in SS.list_messages("q", "", [], items(1, summary=False), 4)[1]["content"]


def test_select_by_list_lists_reranker_top_20_and_keeps_llm_order():
    secs = items(25)
    seen = {}

    def rerank(q, texts):
        seen["query"], seen["texts"] = q, texts
        return [float(int(re.search(r"節(\d+)の", t).group(1))) for t in texts]      # 番号の大きい節ほど高い

    def ask(msgs):
        seen["user"] = msgs[1]["content"]
        return "[2, 1, 30]"

    out = SS.select_by_list("質問", "質問 参照の一文", "源", [], secs, rerank=rerank, ask=ask, max_picks=4)
    assert seen["query"] == "質問 参照の一文" and seen["texts"][0] == "1 節1 節1の要点"   # 見出しの経路＋要点
    assert "1. [HS-VTR-T001E 25 節25]" in seen["user"] and "20. [HS-VTR-T001E 6 節6]" in seen["user"] and "節5]" not in seen["user"]
    assert [s["id"] for s in out["picked"]] == ["s24", "s25"] and out["stage"] == 1 and out["candidates"] == 20
    assert out["raw"] == ["[2, 1, 30]"]


@pytest.mark.parametrize("answer,reason", [("[]", "empty"), ("候補にない", "empty"), ("[0, 99]", "out_of_range")])
def test_select_by_list_fails_on_empty_or_out_of_range(answer, reason):
    with pytest.raises(SS.SelectionFailed) as err:
        SS.select_by_list("q", "q", "", [], items(3), rerank=lambda q, t: [0.0] * len(t), ask=lambda m: answer)
    assert err.value.reason == reason and err.value.raw == answer


def test_select_by_list_fails_when_llm_fails_and_survives_reranker_failure():
    def boom(_msgs):
        raise requests.Timeout("slow")

    with pytest.raises(SS.SelectionFailed) as err:
        SS.select_by_list("q", "q", "", [], items(3), rerank=lambda q, t: [0.0] * len(t), ask=boom)
    assert err.value.reason == "llm_failed"
    with pytest.raises(SS.SelectionFailed) as err:
        SS.select_by_list("q", "q", "", [], [], rerank=lambda q, t: [], ask=lambda m: "[1]")
    assert err.value.reason == "no_sections"

    def bad_rerank(q, t):
        raise RuntimeError("reranker down")

    out = SS.select_by_list("q", "q", "", [], items(3), rerank=bad_rerank, ask=lambda m: "[3]")
    assert [s["id"] for s in out["picked"]] == ["s3"]   # 文書の順のまま一覧にする


def test_select_by_list_two_stage_chapters_then_sections():
    # 章 4 つ × 節 10 = 44 節（閾値 30 を超える）
    rows = []
    for c in range(1, 5):
        rows.append({"id": f"c{c}", "file_name": "D.pdf", "path": [f"{c} 章{c}"], "level": 1, "parent": None, "summary": f"章{c}の要点"})
        for k in range(1, 11):
            rows.append({"id": f"c{c}s{k}", "file_name": "D.pdf", "path": [f"{c} 章{c}", f"{c}-{k} 節"], "level": 2, "parent": f"c{c}",
                         "summary": f"{c}-{k} の要点"})
    secs = [SS.section_item(r) for r in rows]
    prompts = []

    def ask(msgs):
        prompts.append(msgs[1]["content"])
        return "[3]" if len(prompts) == 1 else "[2, 1]"

    out = SS.select_by_list("q", "q", "", [], secs, rerank=lambda q, t: [0.0] * len(t), ask=ask, max_picks=4, two_stage_min=30)
    assert out["stage"] == 2 and out["chapters"] == ["c3"]
    assert "章を、役立つ順に" in prompts[0] and "4. [D 4 章4] 章4の要点" in prompts[0] and "-1 節" not in prompts[0]
    assert "3 章3 > 3-1 節" in prompts[1] and "2 章2" not in prompts[1]          # 2 段目は選んだ章の節だけ
    assert [s["id"] for s in out["picked"]] == ["c3s1", "c3"]
    # 閾値以下なら 1 段
    out = SS.select_by_list("q", "q", "", [], secs, rerank=lambda q, t: [0.0] * len(t), ask=lambda m: "[1]", two_stage_min=100)
    assert out["stage"] == 1 and out["chapters"] == []


# ============================================================
# 一覧選択（検索の中）
# ============================================================
QUOTE = "運転条件は HS-VTR-T001 による。"
SOURCE_TEXT = "3-5 初号機の性能計測。初号機の性能計測を行う。" + QUOTE + "計測の順序は別に定める。"


class FakeNeo:
    """検索が流す照会を真似る。文書のみを指す参照 1 つ（参照元 src → 文書 T001.pdf）と、チャンクへの参照 1 つ。"""

    class _Emb:
        def embed_query(self, text):
            return [0.0]

    def __init__(self, extra_links=()):
        self.embedding = self._Emb()
        self.calls = []
        self.doc_chunks = [  # 従来の選び方の候補（文書のチャンク）
            {"id": "d1", "text": "★ 文書の中でリランクが最も高いチャンク", "position": 1, "file_name": "T001.pdf", "doc_source": "T001.pdf"},
            {"id": "d2", "text": "別のチャンク", "position": 2, "file_name": "T001.pdf", "doc_source": "T001.pdf"},
        ]
        self.sections = [
            {"id": "S1", "file_name": "T001.pdf", "path": ["3 試験"], "title": "3 試験", "number": "3", "level": 1, "parent": None,
             "summary": "試験の総則。", "head": "3. 試験"},
            {"id": "S2", "file_name": "T001.pdf", "path": ["3 試験", "3-5 運転条件"], "title": "3-5 運転条件", "number": "3-5", "level": 2,
             "parent": "S1", "summary": "油の供給圧力と温度を定める。", "head": "油の供給"},
            {"id": "S3", "file_name": "T001.pdf", "path": ["3 試験", "3-6 計測"], "title": "3-6 計測", "number": "3-6", "level": 2,
             "parent": "S1", "summary": "計測の順序を定める。", "head": "計測"},
        ]
        self.section_chunks = {  # 節 → 主所属のチャンク（own）
            "S1": [{"id": "p1", "text": "3. 試験の総則", "position": 10}],
            "S2": [{"id": "p2a", "text": "油の供給圧力は 0.25 MPa ☆", "position": 11}, {"id": "p2b", "text": "★ 温度は 45 ℃", "position": 12}],
            "S3": [{"id": "p3", "text": "計測の順序", "position": 13}],
        }
        self.links = [
            {"source": "src", "source_text": SOURCE_TEXT, "source_heading": "3-5 初号機", "quote": QUOTE, "locator": "", "form": "文書",
             "method": "rule", "source_section": "body", "kind": "document", "id": None, "text": None, "position": None,
             "file_name": "T001.pdf", "doc_source": None},
        ] + list(extra_links)

    def query(self, q, params=None):
        params = params or {}
        self.calls.append(q)
        if q == rr.REFERENCE_LINK_QUERY:
            return [dict(l) for l in self.links if l["source"] in params["ids"]]
        if q == rr.REFERENCE_DOCUMENT_CHUNKS_QUERY:
            return [dict(c) for c in self.doc_chunks if c["id"] not in params["skip"]]
        if q == rr.REFERENCE_DOC_SECTIONS_QUERY:
            return [dict(s) for s in self.sections if s["file_name"] in params["docs"]]
        if q == rr.SECTION_PICK_CHUNKS_QUERY:
            out = []
            for sid in params["sections"]:
                for c in self.section_chunks.get(sid, []):
                    if c["id"] not in params["skip"]:
                        out.append({"section_id": sid, "file_name": "T001.pdf", "doc_source": "T001.pdf", "tier": c.get("tier", 3), **c})
            return out
        if q == rr.ALREADY_SECTIONS_QUERY:
            return [{"id": i, "file_name": "HS-RU-003H.pdf", "section_id": "A" + i, "path": ["2 要領"], "title": "2 要領", "number": None,
                     "summary": "初号機の要領。", "chunk_section": None} for i in params["ids"]]
        raise AssertionError("unexpected query: " + q[:80])


def fake_rerank(query, texts):
    return [0.95 if "★" in t else 0.6 if "☆" in t else 0.4 for t in texts]


def retriever(neo, **settings):
    base = rr.resolve_retrieval_settings({"reference_max": 2, "reference_select": "link", "reference_depth": 1, **settings})
    return rr.RerankGraphRetriever(neo_db=neo, candidate_db=None, retrieval_query="", settings=base)


def chosen(text="選んだチャンクの本文"):
    return [(Document(page_content=text, metadata={"chunk_id": "src", "source": "RU.pdf", "position": 1}), 0.9)]


def follow(neo, monkeypatch, answer="[2, 1]", **settings):
    monkeypatch.setattr(rr, "_rerank", fake_rerank)
    asked = []

    def list_chat(msgs):
        asked.append(msgs)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(SS, "list_chat", list_chat)
    r = retriever(neo, **settings)
    picks = []
    r._link_picks("油の温度は", chosen(), int(r.settings["reference_max"]), {"src"}, picks)
    return picks, asked


def test_rerank_mode_is_unchanged_and_does_not_call_llm(monkeypatch):
    neo = FakeNeo()
    picks, asked = follow(neo, monkeypatch)
    assert [p["chunk_id"] for p in picks] == ["d1"] and asked == []
    assert not any(k in picks[0] for k in ("selection", "selected_section", "llm_raw"))
    assert rr.REFERENCE_DOC_SECTIONS_QUERY not in neo.calls


def test_summary_list_picks_sections_and_one_chunk_each(monkeypatch):
    neo = FakeNeo()
    picks, asked = follow(neo, monkeypatch, reference_doc_select="summary_list", reference_max=1)
    # 一覧は「質問＋参照の一文」のリランカーの順（同点は文書の順）。[2, 1] = 3-5 運転条件、3 試験
    assert [p["chunk_id"] for p in picks] == ["p2b", "p1"], "節ごとにリランカーで 1 件（★ の p2b）"
    assert picks[0]["selection"] == "summary_list" and picks[0]["llm_raw"] == "[2, 1]" and picks[0]["selection_stage"] == 1
    assert picks[0]["selected_section"] == {"id": "S2", "label": "3 試験 > 3-5 運転条件", "file_name": "T001.pdf"}
    assert [s["id"] for s in picks[0]["selected_sections"]] == ["S2", "S1"]
    assert picks[0]["extra"] is False and picks[1]["extra"] is True
    user = asked[0][1]["content"]
    assert "### 参照元の節（この節が参照している文書の中から選ぶ）\n3-5 初号機: 3-5 初号機の性能計測。初号機の性能計測を行う。" + QUOTE in user
    assert "- HS-RU-003H 2 要領: 初号機の要領。" in user          # 既に手元にある節
    assert "1. [T001 3 試験] 試験の総則。" in user and "2. [T001 3 試験 > 3-5 運転条件] 油の供給圧力と温度を定める。" in user


def test_summary_list_respects_max_and_char_budget(monkeypatch):
    neo = FakeNeo()
    picks, _ = follow(neo, monkeypatch, answer="[1, 2, 3]", reference_doc_select="summary_list", reference_doc_max=2)
    assert len(picks) == 2
    # 文字数予算: 1 件目は従来と同じく必ず足し、2 件目以降は本文の合計が予算に収まる分だけ
    neo = FakeNeo()
    picks, _ = follow(neo, monkeypatch, answer="[1, 2, 3]", reference_doc_select="summary_list", char_budget=1000)
    assert len(picks) == 3
    neo = FakeNeo()
    monkeypatch.setattr(rr, "_rerank", fake_rerank)
    monkeypatch.setattr(SS, "list_chat", lambda m: "[1, 2, 3]")
    r = retriever(neo, reference_doc_select="summary_list", char_budget=1000)
    picks = []
    r._link_picks("油の温度は", chosen("x" * 995), 2, {"src"}, picks)
    assert [p["chunk_id"] for p in picks] == ["p1"]


@pytest.mark.parametrize("answer,reason", [("[]", "empty"), ("[7, 9]", "out_of_range"), (requests.Timeout("slow"), "llm_failed")])
def test_summary_list_falls_back_to_rerank(monkeypatch, answer, reason):
    neo = FakeNeo()
    picks, asked = follow(neo, monkeypatch, answer=answer, reference_doc_select="summary_list")
    assert [p["chunk_id"] for p in picks] == ["d1"] and len(asked) == 1     # 従来の選び方と同じチャンク
    assert picks[0]["selection"] == "rerank" and picks[0]["selection_fallback"] == reason


def test_summary_list_falls_back_without_sections_or_when_all_taken(monkeypatch):
    neo = FakeNeo()
    neo.sections = []
    picks, asked = follow(neo, monkeypatch, reference_doc_select="summary_list")
    assert [p["chunk_id"] for p in picks] == ["d1"] and asked == [] and picks[0]["selection_fallback"] == "no_sections"
    neo = FakeNeo()
    neo.section_chunks = {}
    picks, _ = follow(neo, monkeypatch, reference_doc_select="summary_list")
    assert [p["chunk_id"] for p in picks] == ["d1"] and picks[0]["selection_fallback"] == "all_taken"


def test_summary_list_prefers_primary_chunks_and_falls_back_to_secondary(monkeypatch):
    neo = FakeNeo()
    # S2: 主所属（段 3）と副所属（段 1、★ でリランクは高い）。主所属の段だけから選ぶ
    neo.section_chunks["S2"] = [{"id": "p2a", "text": "油の供給圧力 ☆", "position": 11, "tier": 3},
                                {"id": "x2", "text": "★ 前の節のチャンク", "position": 9, "tier": 1}]
    # S3: 主所属のチャンクが無く、副所属だけ（目録で切り出した小さな節）
    neo.section_chunks["S3"] = [{"id": "x3", "text": "計測の順序を含むチャンク", "position": 9, "tier": 1}]
    picks, _ = follow(neo, monkeypatch, answer="[2, 3]", reference_doc_select="summary_list")
    assert [p["chunk_id"] for p in picks] == ["p2a", "x3"]


def test_summary_list_skips_chunks_already_in_context(monkeypatch):
    neo = FakeNeo()
    monkeypatch.setattr(rr, "_rerank", fake_rerank)
    monkeypatch.setattr(SS, "list_chat", lambda m: "[2]")
    r = retriever(neo, reference_doc_select="summary_list")
    picks = []
    r._link_picks("油の温度は", chosen(), 2, {"src", "p2b"}, picks)
    assert [p["chunk_id"] for p in picks] == ["p2a"]


def test_summary_list_leaves_chunk_links_alone(monkeypatch):
    link = {"source": "src", "source_text": SOURCE_TEXT, "source_heading": "3-5", "quote": "詳細は 3-6 項による。", "locator": "3-6",
            "form": "項", "method": "rule", "source_section": "body", "kind": "chunk", "id": "k1", "text": "チャンクへの参照先",
            "position": 3, "file_name": "T001.pdf", "doc_source": "T001.pdf"}
    neo = FakeNeo(extra_links=[link])
    neo.links = neo.links[1:]
    picks, asked = follow(neo, monkeypatch, reference_doc_select="summary_list")
    assert [p["chunk_id"] for p in picks] == ["k1"] and asked == [] and "selection" not in picks[0]


def test_summary_list_second_hop_follows_document_references(monkeypatch):
    # 1 回目: チャンクへの参照 → k1。2 回目: k1 の中の文書のみを指す参照を一覧選択でたどる（従来の 2 回目はたどらない）
    hop1 = {"source": "src", "source_text": SOURCE_TEXT, "source_heading": "3-5", "quote": "詳細は 3-6 項による。", "locator": "3-6",
            "form": "項", "method": "rule", "source_section": "body", "kind": "chunk", "id": "k1", "text": "チャンクへの参照先",
            "position": 3, "file_name": "T001.pdf", "doc_source": "T001.pdf"}
    neo = FakeNeo(extra_links=[hop1])
    neo.links[0]["source"] = "k1"
    picks, asked = follow(neo, monkeypatch, answer="[2]", reference_doc_select="summary_list", reference_depth=2, reference_min_score=0.1)
    assert [(p["chunk_id"], p["hop"]) for p in picks] == [("k1", 1), ("p2b", 2)] and picks[1]["selection"] == "summary_list"
    neo = FakeNeo(extra_links=[hop1])
    neo.links[0]["source"] = "k1"
    picks, asked = follow(neo, monkeypatch, reference_depth=2, reference_min_score=0.1)
    assert [p["chunk_id"] for p in picks] == ["k1"] and asked == []


def test_trace_carries_selection_only_for_summary_list():
    class Doc:
        def __init__(self, source, ids):
            self.page_content = "Text Content:\n" + "\n----\n".join(ids)
            self.metadata = {"source": source, "chunkdetails": [{"id": i} for i in ids]}

    r = rr.RerankGraphRetriever(neo_db=None, candidate_db=None, retrieval_query="", settings=rr.resolve_retrieval_settings({}))
    base = {"text": "t", "doc_source": "T001.pdf", "position": 1, "score": 0.5, "chunk_score": 0.4, "hop": 1, "select": "link"}
    trace = [dict(base, chunk_id="a"), dict(base, chunk_id="b", selection="summary_list", llm_raw="[1]", selected_section={"id": "S2"},
                                           selected_sections=[{"id": "S2"}], selection_stage=1, extra=True)]
    docs = [Doc("T001.pdf", ["a", "b"])]
    r._present_references(docs, [], trace)
    a, b = docs[0].metadata["references"]
    assert set(a) == set(rr.REFERENCE_TRACE_KEYS)
    assert b["selection"] == "summary_list" and b["llm_raw"] == "[1]" and b["selected_section"] == {"id": "S2"} and "extra" not in b


def test_reference_context_shows_text_around_the_quote():
    text = "あ" * 300 + "前の文。" + QUOTE + "後ろの文。" + "い" * 300
    out = rr.reference_context(text, "3-5 項", QUOTE, chars=10)
    assert out == "3-5 項: …" + "あ" * 6 + "前の文。" + QUOTE + "後ろの文。" + "い" * 5 + "…"
    spaced = "前の文。運転条件は\nHS-VTR-T001 による。後ろ"
    assert rr.reference_context(spaced, "", QUOTE, chars=4) == "前の文。運転条件は HS-VTR-T001 による。後ろ"
    assert rr.reference_context("無関係", "見出し", QUOTE) == "見出し: " + QUOTE


# ============================================================
# 設定の既定値
# ============================================================
def test_default_settings_keep_rerank(monkeypatch):
    for env in ("CHAT_REFERENCE_DOC_SELECT", "CHAT_REFERENCE_DOC_MAX", "CHAT_REFERENCE_DOC_TWO_STAGE_SECTIONS"):
        monkeypatch.delenv(env, raising=False)
    s = rr.default_retrieval_settings()
    assert (s["reference_doc_select"], s["reference_doc_max"], s["reference_doc_two_stage_sections"]) == ("rerank", 4, 30)
    # 参照追跡の方式の既定値もこの段では rerank のまま（評価で合格したら切り替える）
    ref = C.CHAT_MODE_CONFIG_MAP[C.CHAT_VECTOR_GRAPH_FULLTEXT_REF_MODE]["retrieval_defaults"]
    assert "reference_doc_select" not in ref
    assert rr.resolve_retrieval_settings(None, mode_defaults=ref)["reference_doc_select"] == "rerank"
    # 既定の検索（参照をたどらない 4 方式）: 参照をたどらない（reference_max = 0）まま
    for mode in (C.CHAT_VECTOR_MODE, C.CHAT_FULLTEXT_MODE, C.CHAT_VECTOR_GRAPH_MODE, C.CHAT_VECTOR_GRAPH_FULLTEXT_MODE):
        d = C.CHAT_MODE_CONFIG_MAP[mode]["retrieval_defaults"]
        assert d == {"reference_max": 0}, mode
        resolved = rr.resolve_retrieval_settings(None, mode_defaults=d)
        assert resolved["reference_max"] == 0 and resolved["reference_doc_select"] == "rerank"
    # 画面の上書きで切り替えられる。選択肢に無い値・範囲外は無視する。環境変数の不正な値は既定値に戻す
    s = rr.resolve_retrieval_settings({"reference_doc_select": "summary_list", "reference_doc_max": 2}, mode_defaults=ref)
    assert s["reference_doc_select"] == "summary_list" and s["reference_doc_max"] == 2 and s["reference_max"] == 2
    s = rr.resolve_retrieval_settings({"reference_doc_select": "llm", "reference_doc_max": 99}, mode_defaults=ref)
    assert s["reference_doc_select"] == "rerank" and s["reference_doc_max"] == 4
    monkeypatch.setenv("CHAT_REFERENCE_DOC_SELECT", "nonsense")
    assert rr.default_retrieval_settings()["reference_doc_select"] == "rerank"


def test_new_settings_are_appended_after_existing_ones():
    keys = [spec[0] for spec in rr.RETRIEVAL_SETTING_SPECS]
    assert keys[-3:] == ["reference_doc_select", "reference_doc_max", "reference_doc_two_stage_sections"]
    assert keys.index("reference_document_layer") == len(keys) - 4
    spec = {item["key"]: item for item in rr.retrieval_settings_spec()}
    assert [c["value"] for c in spec["reference_doc_select"]["choices"]] == ["rerank", "summary_list"]
    assert spec["reference_doc_select"]["group"] == "参照の追跡"


# ============================================================
# 要点は既定の検索の文脈に出ない
# ============================================================
def test_summary_does_not_reach_the_default_context():
    # チャンクを検索する方式の文脈の照会は、どの属性名としても summary を読まない（コミュニティの要約はコミュニティの方式だけ）
    for query in (C.VECTOR_GRAPH_SEARCH_QUERY, rr.build_expansion_query(rr.resolve_retrieval_settings({})), C.VECTOR_SEARCH_QUERY):
        assert "summary" not in query.lower()
    # ノードの行は id（無ければ name・title・id・description で終わる名前の属性）と description だけ。要点の属性名はどれにも当たらない
    m = re.search(r'k =~ "([^"]+)"', C.VECTOR_GRAPH_SEARCH_QUERY_SUFFIX)
    assert m and not [k for k in ("summary", "summary_model", "summary_at", "summary_src") if re.search(m.group(1).replace("(?i)", ""), k, re.I)]
    # 展開の起点は HAS_ENTITY の先（エンティティ）。章・節の層は Section へ HAS_ENTITY を張らない
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "section_layer.py"), encoding="utf-8").read()
    assert "HAS_ENTITY" not in src
    # 控えのノードは後処理・件数・スキーマの対象外
    assert "SectionSummary" in C.PROTECTED_MANAGEMENT_LABELS and "&!`SectionSummary`" in C.PROTECTED_LABELS_CYPHER


# ============================================================
# 反証役の指摘（11/9）への手直し: 要点の作成
# ============================================================
def flat_world(n, file_name="B.pdf"):
    sections = [{"id": f"x{i}", "file_name": file_name, "path": [f"{i} 節"], "level": 1, "parent": None} for i in range(n)]
    return FakeGraph(sections, {f"x{i}": ["回転速度と圧力比を計測する本文。"] for i in range(n)})


def http_error(code):
    r = requests.Response()
    r.status_code = code
    r.url = "http://llm:8000/v1/chat/completions"
    return requests.HTTPError(f"{code} Client Error for url: {r.url}", response=r)


def test_abort_still_restores_shallower_levels_from_the_cache():
    g = world()
    SS.build_section_summaries(run=g.run, summarize=FakeSummarize(), backoff=0)
    before = {sid: s.get("summary") for sid, s in g.sections.items()}
    g.rebuild_section_layer()
    # 新しい文書（深い節 9 つ）を足し、LLM は接続できない。古い文書の章（c1・c2）も控えから戻ること
    g.sections["nc"] = {"id": "nc", "file_name": "NEW.pdf", "path": ["1 章"], "level": 1, "parent": None}
    for i in range(9):
        g.sections[f"n{i}"] = {"id": f"n{i}", "file_name": "NEW.pdf", "path": ["1 章", f"1-{i} 節"], "level": 2, "parent": "nc"}
        g.bodies[f"n{i}"] = [f"新しい節 {i} の本文。十分な長さの本文である。"]
    llm = FakeSummarize(error=requests.ConnectionError("down"))
    g.queries.clear()
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0, concurrency=1)["summary"]
    assert out["aborted"] is True and out["complete"] is False and f"{SS.ABORT_AFTER} times in a row" in out["abort_reason"]
    assert out["restored"] == 4 and all(g.sections[sid].get("summary") == before[sid] for sid in ("c1", "s11", "s12", "c2"))
    assert len(llm.calls) == SS.ABORT_AFTER and out["failed"] == 2 and out["pending"] == 7    # 2 節で 6 回、3 節目の 2 回目で打ち切り
    assert out["pruned"] == 0 and SS.CACHE_PRUNE_QUERY not in g.queries                     # 打ち切った回は掃除しない


@pytest.mark.parametrize("code,retried", [(404, False), (401, False), (403, False), (503, True), (429, True)])
def test_http_errors_of_the_endpoint_count_toward_abort(code, retried):
    g = flat_world(30)
    llm = FakeSummarize(error=http_error(code))
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0, concurrency=1)["summary"]
    assert out["aborted"] is True and out["complete"] is False and str(code) in out["abort_reason"]
    assert len(llm.calls) == SS.ABORT_AFTER                       # 30 節 × 3 回 = 90 回の空回りをしない
    assert out["failed"] == (2 if retried else SS.ABORT_AFTER) and out["failed"] + out["pending"] == 30


def test_input_errors_are_skipped_without_retry_and_reported():
    g = flat_world(12)
    llm = FakeSummarize(error=http_error(400))
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0)["summary"]
    assert out["aborted"] is False and out["failed"] == 12 and len(llm.calls) == 12   # その節の入力による失敗: 送り直さず、打ち切りにも数えない
    assert out["complete"] is False                                                   # 全部失敗したら後処理は Failed


def test_missing_model_config_sends_nothing_but_restores(monkeypatch):
    g = world()
    SS.build_section_summaries(run=g.run, summarize=FakeSummarize(), backoff=0)
    before = {sid: s.get("summary") for sid, s in g.sections.items()}
    g.rebuild_section_layer()
    g.bodies["s12"] = ["計測の本文を改訂した。"]
    monkeypatch.setenv("SECTION_SUMMARY_MODEL", "no_such_model")
    monkeypatch.delenv("LLM_MODEL_CONFIG_NO_SUCH_MODEL", raising=False)
    monkeypatch.setattr(SS.requests, "post", lambda *a, **k: pytest.fail("the LLM must not be called"))
    out = SS.build_section_summaries(run=g.run, backoff=0)["summary"]
    assert out["aborted"] is True and out["complete"] is False and "LLM_MODEL_CONFIG_NO_SUCH_MODEL" in out["abort_reason"]
    # 入力が変わらない節（s11・c2）は書き戻す。本文が変わった s12 と、その子の要点を入れる章 c1 は LLM が要る（pending）
    assert out["restored"] == 2 and out["pending"] == 2 and out["failed"] == 0
    assert g.sections["s11"]["summary"] == before["s11"] and g.sections["c2"]["summary"] == before["c2"]


def test_complete_tolerates_a_few_failures_only():
    out = SS.build_section_summaries(run=flat_world(20).run, summarize=FakeSummarize(fail=("[B 3 節]",)), backoff=0)["summary"]
    assert out["failed"] == 1 and out["made"] == 19 and out["complete"] is True          # 1/20 は FAIL_RATIO（1 割）以下
    out = SS.build_section_summaries(run=flat_world(20).run, summarize=FakeSummarize(fail=("[B 1",)), backoff=0)["summary"]
    assert out["failed"] == 11 and out["complete"] is False                              # 1・10〜19 の 11 節
    out = SS.build_section_summaries(run=world().run, summarize=FakeSummarize(), backoff=0)["summary"]
    assert out["complete"] is True and out["aborted"] is False and out["abort_reason"] is None


def test_prune_keeps_summaries_while_the_section_layer_is_rebuilt_midway():
    g = world()
    SS.build_section_summaries(run=g.run, summarize=FakeSummarize(), backoff=0)
    before = {sid: s.get("summary") for sid, s in g.sections.items()}
    g.cache["dead"] = {"summary": "どの節とも一致しない古い要点。", "model": "m", "at": "t", "fileName": "X.pdf"}
    g.bodies["s12"] = ["計測の本文を改訂した。"]
    inner = FakeSummarize()

    def summarize(user_text):
        if not inner.calls:
            g.rebuild_section_layer()     # 作成の途中で、別の要求の章・節の層が節を作り直した（summary_src が消える）
        return inner(user_text)

    out = SS.build_section_summaries(run=g.run, summarize=summarize, backoff=0)["summary"]
    # s12 だけ作る（章 c1 の入力の子の要点は同じ文。FakeSummarize は見出しから作る）。s11・c1・c2 は読んだ時点では揃っていた
    assert out["made"] == 1 and out["kept"] == 3
    assert "dead" not in g.cache and out["pruned"] == 2           # 消すのは古い控え（dead と、s12 の改訂前の入力）だけ
    assert "summary" not in g.sections["s11"] and "summary" not in g.sections["c1"]   # 作り直しで消えたまま。控えは残っている
    llm = FakeSummarize()
    out = SS.build_section_summaries(run=g.run, summarize=llm, backoff=0)["summary"]
    assert llm.calls == [] and out["restored"] == 3 and out["kept"] == 1
    assert {sid: g.sections[sid].get("summary") for sid in ("s11", "c1", "c2")} == {sid: before[sid] for sid in ("s11", "c1", "c2")}


def test_prune_is_skipped_while_the_graph_lease_is_held():
    g = world()
    SS.build_section_summaries(run=g.run, summarize=FakeSummarize(), backoff=0)
    g.cache["dead"] = {"summary": "古い要点。", "model": "m", "at": "t", "fileName": "X.pdf"}
    g.lease = ["provenance#1", "section_layer#2"]                 # 読む時点は別の処理、掃除の時点は章・節の層が貸し出し中
    out = SS.build_section_summaries(run=g.run, summarize=FakeSummarize(), backoff=0)["summary"]
    assert "section_layer#2" in out["prune_skipped"] and out["pruned"] == 0 and "dead" in g.cache
    assert out["complete"] is True


def test_waits_for_the_section_layer_before_reading_sections(monkeypatch):
    g = world()
    g.lease = ["section_layer#a", "section_layer#a"]
    slept = []
    monkeypatch.setattr(SS.time, "sleep", lambda sec: slept.append(sec))
    out = SS.build_section_summaries(run=g.run, summarize=FakeSummarize(), backoff=0)["summary"]
    assert slept.count(2.0) == 2 and out["made"] == 4
    lease_reads = [i for i, q in enumerate(g.queries) if q == SS.LEASE_HOLDER_QUERY]
    assert g.queries.index(SS.SECTIONS_QUERY) > lease_reads[2]    # 貸し出しが返ってから節を読む


def test_builds_in_one_container_run_one_at_a_time(monkeypatch, tmp_path):
    monkeypatch.setattr(SS, "_BUILD_LOCK_PATH", str(tmp_path / "summaries.lock"))
    g = world()
    started = threading.Event()

    class Slow(FakeSummarize):
        def __call__(self, user_text):
            started.set()
            time.sleep(0.05)
            return super().__call__(user_text)

    first, second, results = Slow(), FakeSummarize(), {}
    t = threading.Thread(target=lambda: results.setdefault("a", SS.build_section_summaries(run=g.run, summarize=first, backoff=0)["summary"]))
    t.start()
    assert started.wait(5)
    results["b"] = SS.build_section_summaries(run=g.run, summarize=second, backoff=0)["summary"]
    t.join(10)
    assert results["a"]["made"] == 4 and results["b"]["lock_wait_seconds"] > 0
    assert second.calls == [] and results["b"]["kept"] == 4         # 2 本目は 1 本目の終了を待ち、作った要点をそのまま使う
    # 待ちの上限を過ぎたら例外（後処理は Failed）
    monkeypatch.setattr(SS, "BUILD_LOCK_WAIT", 0.0)
    with open(tmp_path / "summaries.lock", "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match="still running"):
            SS.build_section_summaries(run=g.run, summarize=FakeSummarize(), backoff=0)


def test_restore_only_writes_back_from_the_cache_without_the_llm():
    g = world()
    out = SS.build_section_summaries(run=g.run, restore_only=True)["summary"]
    assert out["sections"] == 0 and SS.SECTIONS_QUERY not in g.queries    # 要点を作ったことの無い DB では節も読まない
    SS.build_section_summaries(run=g.run, summarize=FakeSummarize(), backoff=0)
    before = {sid: s.get("summary") for sid, s in g.sections.items()}
    g.cache["dead"] = {"summary": "古い要点。", "model": "m", "at": "t", "fileName": "X.pdf"}
    g.rebuild_section_layer()
    g.bodies["s12"] = ["計測の本文を改訂した。"]
    g.queries.clear()
    out = SS.build_section_summaries(run=g.run, summarize=lambda t: pytest.fail("no LLM in restore_only"), restore_only=True)["summary"]
    assert out["mode"] == "restore_only" and out["restored"] == 2 and out["pending"] == 2 and out["complete"] is True
    assert g.sections["s11"]["summary"] == before["s11"] and "summary" not in g.sections["s12"]
    assert SS.CACHE_INDEX_QUERY not in g.queries and SS.CACHE_PRUNE_QUERY not in g.queries and "dead" in g.cache


def test_score_restores_summaries_after_the_section_layer_without_the_summary_task():
    # 既定の後処理（章・節の層あり・節の要点なし）でも、層の作り直しで消えた要点を控えから書き戻す。節の要点の段は complete で判定する
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "score.py"), encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "post_processing")

    def task_of(test):
        return test.left.value if (isinstance(test, ast.Compare) and isinstance(test.ops[0], ast.In)
                                   and isinstance(test.left, ast.Constant)) else None

    node = next(n for n in ast.walk(fn) if isinstance(n, ast.If) and task_of(n.test) == "build_section_summaries")
    assert "'complete', True" in ast.get_source_segment(src, node.body[-1]) or "complete" in ast.get_source_segment(src, node)
    other = node.orelse[0]
    assert isinstance(other, ast.If) and task_of(other.test) == "build_section_layer"
    calls = [c for c in ast.walk(other) if isinstance(c, ast.Call) and any(k.arg == "restore_only" for k in c.keywords)]
    assert calls and all(k.value.value is True for c in calls for k in c.keywords if k.arg == "restore_only")


def test_list_chat_gives_up_when_no_slot_is_free_and_chat_releases_slots(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_CONFIG_GEMMA4", "google/gemma-4-26B-A4B-it,http://llm:8000/v1,k")
    monkeypatch.setattr(SS, "LIST_SLOT_WAIT", 0.05)
    monkeypatch.setattr(SS, "_SLOTS", threading.BoundedSemaphore(1))
    SS._SLOTS.acquire()                                          # 同じプロセスの要点の作成が枠を使っている
    monkeypatch.setattr(SS.requests, "post", lambda *a, **k: pytest.fail("no request while the slots are busy"))
    t0 = time.time()
    with pytest.raises(SS.SlotBusy):
        SS.list_chat([{"role": "user", "content": "q"}])
    assert time.time() - t0 < 1
    SS._SLOTS.release()

    def down(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(SS.requests, "post", down)
    with pytest.raises(requests.ConnectionError):
        SS.list_chat([{"role": "user", "content": "q"}])
    assert SS._SLOTS.acquire(blocking=False)                     # 失敗しても枠は返している
    SS._SLOTS.release()


# ============================================================
# 反証役の指摘（11/9）への手直し: 一覧選択
# ============================================================
def test_summary_list_applies_the_same_min_score_as_rerank(monkeypatch, caplog):
    # 参照の一文は高く（0.95）、本文はすべて下限（0.3）未満: rerank は足さない。summary_list も足さない（下限は同じ）
    neo = FakeNeo()
    for c in neo.doc_chunks:
        c["text"] = c["text"].replace("★", "")
    for v in neo.section_chunks.values():
        for c in v:
            c["text"] = c["text"].replace("★", "").replace("☆", "")
    monkeypatch.setattr(rr, "_rerank", lambda q, texts: [0.95 if "初号機" in t else 0.1 for t in texts])
    asked = []
    monkeypatch.setattr(SS, "list_chat", lambda m: (asked.append(m), "[1, 2, 3]")[1])
    picks = []
    retriever(neo)._link_picks("油の温度は", chosen(), 2, {"src"}, picks)
    assert picks == []
    caplog.set_level(logging.INFO)
    retriever(neo, reference_doc_select="summary_list")._link_picks("油の温度は", chosen(), 2, {"src"}, picks)
    assert picks == [] and len(asked) == 1 and "(below_min_score)" in caplog.text
    # 一部の節だけ下限以上: その節だけ足す
    neo = FakeNeo()
    monkeypatch.setattr(rr, "_rerank", lambda q, texts: [0.95 if ("初号機" in t or "★" in t) else 0.1 for t in texts])
    picks = []
    retriever(neo, reference_doc_select="summary_list")._link_picks("油の温度は", chosen(), 2, {"src"}, picks)
    assert [p["chunk_id"] for p in picks] == ["p2b"] and picks[0]["selection"] == "summary_list"
    assert [s["id"] for s in picks[0]["selected_sections"]] == ["S1", "S2", "S3"] and picks[0]["extra"] is False


def test_summary_list_needs_summaries_and_records_coverage(monkeypatch):
    neo = FakeNeo()
    for sec in neo.sections:
        sec["summary"] = None
    picks, asked = follow(neo, monkeypatch, reference_doc_select="summary_list")
    assert [p["chunk_id"] for p in picks] == ["d1"] and asked == [] and picks[0]["selection_fallback"] == "no_summaries"
    neo = FakeNeo()
    neo.sections[2]["summary"] = None
    picks, asked = follow(neo, monkeypatch, reference_doc_select="summary_list")
    assert picks[0]["selection"] == "summary_list" and picks[0]["summary_coverage"] == "2/3" and len(asked) == 1
    assert "summary_coverage" in rr.REFERENCE_SELECTION_KEYS


def second_hop_world():
    """1 回目に足すチャンク（p1・p2b・p3）の中に、別の文書 T002.pdf だけを指す参照がある。"""
    neo = FakeNeo()
    for cid in ("p2b", "p1", "p3"):
        neo.links.append(dict(neo.links[0], source=cid, file_name="T002.pdf"))
    neo.sections += [dict(sec, id="Z" + sec["id"], file_name="T002.pdf", parent=("Z" + sec["parent"]) if sec["parent"] else None)
                     for sec in list(neo.sections)]
    for k, v in list(neo.section_chunks.items()):
        neo.section_chunks["Z" + k] = [dict(c, id="z" + c["id"]) for c in v]
    return neo


def test_second_hop_adds_one_per_parent(monkeypatch):
    neo = second_hop_world()
    monkeypatch.setattr(rr, "_rerank", fake_rerank)
    monkeypatch.setattr(SS, "list_chat", lambda m: "[1, 2, 3]")
    picks = []
    retriever(neo, reference_doc_select="summary_list", reference_depth=2, reference_min_score=0.1)._link_picks(
        "油の温度は", chosen(), 2, {"src"}, picks)
    assert [p["chunk_id"] for p in picks if p["hop"] == 1] == ["p1", "p2b", "p3"]
    hop2 = [p for p in picks if p["hop"] == 2]
    assert hop2 and set(collections.Counter(p["from_chunk_id"] for p in hop2).values()) == {1}   # 親 1 つにつき 1 件


def test_second_hop_does_not_call_the_llm_for_parents_over_the_budget(monkeypatch):
    neo = second_hop_world()
    asked = []
    monkeypatch.setattr(rr, "_rerank", fake_rerank)
    monkeypatch.setattr(SS, "list_chat", lambda m: (asked.append(m), "[1, 2, 3]")[1])
    hop1 = sum(len(neo.section_chunks[k][-1 if k == "S2" else 0]["text"]) for k in ("S1", "S2", "S3"))   # p1・p2b・p3
    extra_limit = 1000 - int(1000 * rr.REFERENCE_EXTRA_LINE_SHARE)
    r = retriever(neo, reference_doc_select="summary_list", reference_depth=2, reference_min_score=0.1, char_budget=1000)
    picks = []
    r._link_picks("油の温度は", chosen("x" * (extra_limit - hop1)), 2, {"src"}, picks)   # 1 回目の 3 件で行の取り分の手前まで埋まる
    assert [(p["chunk_id"], p["hop"], p["extra"]) for p in picks] == [("p1", 1, False), ("p2b", 1, True), ("p3", 1, True), ("zp1", 2, False)]
    assert len(asked) == 2        # 1 回目と、extra でない親 p1 の 2 回目だけ。予算の尽きた extra の親 p2b・p3 では LLM を呼ばない


def test_llm_failure_is_remembered_within_one_question(monkeypatch):
    # 文書のみを指す参照が 2 つ（T001.pdf と T002.pdf）。1 つ目で LLM が時間切れなら、2 つ目は LLM を呼ばずに rerank
    neo = FakeNeo()
    neo.links.append(dict(neo.links[0], file_name="T002.pdf", quote="計測の順序は HS-VTR-T002 による。"))
    neo.sections += [dict(sec, id="Z" + sec["id"], file_name="T002.pdf") for sec in list(neo.sections)]
    picks, asked = follow(neo, monkeypatch, answer=requests.Timeout("slow"), reference_doc_select="summary_list")
    assert len(asked) == 1 and [p["chunk_id"] for p in picks] == ["d1", "d2"]
    assert [p["selection_fallback"] for p in picks] == ["llm_failed", "llm_failed_earlier"]


def test_extras_leave_room_for_lines_and_the_target_picks(monkeypatch):
    neo = FakeNeo()
    neo.section_chunks = {"S1": [{"id": "p1", "text": "あ" * 200, "position": 10}],
                          "S2": [{"id": "p2b", "text": "★" + "い" * 199, "position": 12}],
                          "S3": [{"id": "p3", "text": "う" * 200, "position": 13}]}
    target = Document(page_content="従" * 300, metadata={"chunk_id": "tgt", "source": "T009.pdf", "position": 1, "via_reference": True})
    monkeypatch.setattr(rr.RerankGraphRetriever, "_referenced", lambda self, q, ch, limit: [(target, 0.5)])
    monkeypatch.setattr(rr, "_rerank", fake_rerank)
    monkeypatch.setattr(SS, "list_chat", lambda m: "[1, 2, 3]")
    r = retriever(neo, reference_doc_select="summary_list", char_budget=1000)
    # 本文 100 + 従来の選び方の分 300 + p1 200 + p2b 200 = 800 ≤ 850（上限の 85%）。p3 を足すと 1000 になり、行の分が残らない
    trace = r._follow_links("油の温度は", chosen("x" * 100), 2, with_target=True)
    assert [e["chunk_id"] for e in trace] == ["p1", "p2b", "tgt"]        # 並びは従来どおり、一文で選んだ分 → 従来の選び方の分
    trace = r._follow_links("油の温度は", chosen("x" * 100), 2, with_target=False)
    assert [e["chunk_id"] for e in trace] == ["p1", "p2b", "p3"]


def test_line_budget_warns_when_chunk_texts_fill_the_budget(monkeypatch, caplog):
    monkeypatch.setattr(rr, "_rerank", lambda q, texts: [0.5] * len(texts))
    doc = Document(page_content="本" * 1200 + "\n----\nEntities:\nA\n----\nRelationships:\nA-B", metadata={"source": "a"})
    caplog.set_level(logging.WARNING)
    out = rr._apply_budget_reranked("q", [doc], 1000)
    assert "Entities" not in out[0].page_content and "no entity/relationship lines are kept" in caplog.text

