"""LLM・埋め込みサーバ無しで、API の抽出経路を端から端まで通す試験。

偽の LLM（段ごとに決まった答えを返す）と偽の埋め込み（文字の出現数ベクトル）で
api.extract_kg を動かし、次を確かめる:
  1. 文書タイプ別の手本と追加指示がプロンプトに入る
  2. 抽出・定義の出力上限が 4096（EDC_MAX_TOKENS）で渡る
  3. 全角コロンの定義が読める（読めないと MEASURED_BY に写らず落ちる）
  4. enrich_schema=False で語彙外のリレーションは落ち、dropped に定義付きで返る
  5. 寄せルールは既定で無効。CANON_MERGE_THRESHOLD を設定したときだけ働く
  6. 文書タイプのノイズ規則で様式の欄名・記号のトリプルが落ちる
  7. 出力上限で切れた呼び出しが n_truncated に数えられる
  8. 他の文書タイプは従来の手本のまま、ノイズ規則も掛からない
実行: コンテナ内で  python tests/test_offline.py
"""
import math
import os
import re
import sys
from collections import Counter
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LLM_PROVIDER", "vllm")
os.environ.setdefault("VLLM_MODEL", "fake")
os.environ.pop("CANON_MERGE_THRESHOLD", None)
os.environ.pop("EDC_MAX_TOKENS", None)

import edc.utils.llm_utils as L  # noqa: E402

CALLS = []

OIE_NORMAL = ("[['吸込温度', 'MeasurementItem', 'measured_by', '熱電対', 'Instrument'], "
              "['過給機', 'Equipment', 'undergoes', '単独試運転', 'TestItem'], "
              "['改正回数', 'Section', 'has_value', 'A', 'Parameter']]")
OIE_TRUNC = ("[['吸込温度計測', 'MeasurementItem', 'measured_by', '熱電対', 'Instrument'], "
             "['吐出温度', 'Measure")
SD_ANSWER = ("measured_by：主語の計測項目が、目的語の計測機器で計測される。\n"
             "undergoes: 主語が目的語の工程を受ける。\n"
             "has_value: 主語が目的語の値を持つ。")
TARGET = {"measured_by": "MEASURED_BY", "has_value": "HAS_VALUE"}


def _answer(prompt):
    if "semantic graph" in prompt:
        return (OIE_TRUNC, "length") if "TRUNC" in prompt.split("Text:")[-1] else (OIE_NORMAL, "stop")
    if "write a description" in prompt:
        return SD_ANSWER, "stop"
    if "Choices" in prompt:
        choices = dict((name, letter) for letter, name in re.findall(r"^([A-Z])\. '(.+?)':", prompt, flags=re.M))
        none_letter = re.search(r"^([A-Z])\. None of the above", prompt, flags=re.M).group(1)
        m = re.search(r"The relation '(.+?)' in the triplet", prompt)
        want = TARGET.get(m.group(1)) if m else None
        return (choices.get(want, none_letter) if want else none_letter), "stop"
    return "", "stop"


class _FakeCompletions:
    def create(self, model, messages, temperature=0, max_tokens=None):
        prompt = messages[-1]["content"]
        content, finish = _answer(prompt)
        CALLS.append({"prompt": prompt, "max_tokens": max_tokens, "finish": finish})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])


_fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
L._vllm_client = _fake_client
L.get_vllm_client = lambda: _fake_client


def _fake_embed_init(self):
    self.prompts = {}


def _fake_encode(self, text, prompt_name=None, prompt=None):
    def vec(s):
        c = Counter(s)
        v = [float(c.get(chr(0x3000 + i), 0) + c.get(chr(0x4E00 + i * 37), 0)) for i in range(256)]
        v += [float(c.get(ch, 0)) for ch in "abcdefghijklmnopqrstuvwxyz_"]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n + 1e-3 for x in v]
    return vec(text) if isinstance(text, str) else [vec(t) for t in text]


L.VllmEmbedder.__init__ = _fake_embed_init
L.VllmEmbedder.encode = _fake_encode

# 偽の埋め込みは意味を持たないので、候補の上位 5 件に正解が入る保証が無い。
# この試験は配線を確かめるのが目的なので、語彙の全件を候補として LLM に見せる。
from edc.schema_canonicalization import SchemaCanonicalizer  # noqa: E402
_orig_retrieve = SchemaCanonicalizer.retrieve_similar_relations
SchemaCanonicalizer.retrieve_similar_relations = lambda self, q, top_k=5: _orig_retrieve(self, q, top_k=100)

import api  # noqa: E402

TEXT = "過給機の単独試運転では、吸込温度を熱電対により計測する。改正回数 A B"
FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def run(**kw):
    CALLS.clear()
    return api._to_response(api.extract_kg(text=TEXT, doctype="試運転_検査基準", chunk_method="recursive", **kw))


print("[1] 試運転_検査基準・enrich_schema=False・寄せルール既定（無効）")
r = run(enrich_schema=False)
oie = [c for c in CALLS if "semantic graph" in c["prompt"]]
sd = [c for c in CALLS if "write a description" in c["prompt"]]
check(oie and "循環ポンプ" in oie[0]["prompt"], "抽出の手本が過給機規格用に差し替わる")
check(oie and "Domain rules for test standards" in oie[0]["prompt"], "文書タイプの追加指示が入る")
check(sd and "主語の計測項目が" in sd[0]["prompt"], "定義の手本が日本語版に差し替わる")
check(oie and oie[0]["max_tokens"] == 4096, f"抽出の出力上限 = {oie[0]['max_tokens'] if oie else None}")
check(sd and sd[0]["max_tokens"] == 4096, "定義の出力上限 = 4096")
rels = {(t["subject"], t["relation"], t["object"]) for t in r["triplets"]}
check(("吸込温度", "MEASURED_BY", "熱電対") in rels, "全角コロンの定義が読め、MEASURED_BY に写る")
check(not any(t["relation"] == "undergoes" for t in r["triplets"]), "語彙外の undergoes は出力に残らない")
d = [x for x in r["dropped"] if x["relation"] == "undergoes"]
check(len(d) == 1 and d[0]["definition"] == "主語が目的語の工程を受ける。", f"undergoes が定義付きで dropped に入る: {d}")
check(r["n_noise_dropped"] == 1, f"様式の欄名のトリプルが落ちる: n_noise_dropped={r['n_noise_dropped']}")
check(r["n_truncated"] == 0 and r["n_llm_calls"] == len(CALLS), f"呼び出し数 {r['n_llm_calls']}、切れ 0")

print("[2] 寄せルールを明示的に有効化（CANON_MERGE_THRESHOLD=0.0）")
os.environ["CANON_MERGE_THRESHOLD"] = "0.0"
r = run(enrich_schema=False)
os.environ.pop("CANON_MERGE_THRESHOLD")
check(not r["dropped"], "閾値を設定すると undergoes は最近傍へ寄せられ、落ちない")

print("[3] enrich_schema を送らない（従来の既定 True）")
r = run()
check(any(t["relation"] == "undergoes" for t in r["triplets"]), "従来どおり undergoes が語彙に足される")
check("undergoes" in r["schema_"], "応答のスキーマにも足される")

print("[4] 出力上限で切れた抽出")
CALLS.clear()
r = api._to_response(api.extract_kg(text="TRUNC 吸込温度を熱電対で計測", doctype="試運転_検査基準",
                                    chunk_method="recursive", enrich_schema=False))
check(r["n_truncated"] == 1, f"n_truncated = {r['n_truncated']}")
check(any(t["subject"] == "吸込温度計測" for t in r["triplets"]), "切れる前の完全な組は残る")

print("[5] 別の文書タイプ（社内規程_就業規則）は従来どおり")
CALLS.clear()
r = api._to_response(api.extract_kg(text=TEXT, doctype="社内規程_就業規則", chunk_method="recursive",
                                    enrich_schema=False))
oie = [c for c in CALLS if "semantic graph" in c["prompt"]]
check(oie and "労働者として採用された者" in oie[0]["prompt"], "従来の手本のまま")
check(oie and "Domain rules" not in oie[0]["prompt"], "追加指示は入らない")
check(r["n_noise_dropped"] == 0, "ノイズ規則は掛からない")

print("[7] どの文書タイプにも当たらない文書（UNKNOWN）で語彙を閉じる指定が来た場合")
CALLS.clear()
r = api._to_response(api.extract_kg(text=TEXT, doctype="UNKNOWN", chunk_method="recursive", enrich_schema=False))
check(r["n_triplets"] >= 2, f"閉じる語彙が無いので自由発見に切り替わり、トリプルが残る: n_triplets={r['n_triplets']}")
check(not r["dropped"], "語彙外として落ちる組は無い")
check(r["vocabulary_closed"] is False, "応答で「語彙は閉じていない」と分かる")
r2 = run(enrich_schema=False)
check(r2["vocabulary_closed"] is True, "語彙のある文書タイプでは閉じたまま")

print("[6] 定義の読み取り単体")
dd = L.parse_relation_definition("a_b：主語が目的語に従う。\nc_d: 半角の定義\n- e_f：箇条書きの定義")
check(dd.get("a_b") == "主語が目的語に従う。" and dd.get("c_d") == "半角の定義" and dd.get("e_f") == "箇条書きの定義", f"{dd}")

print()
print("FAILED: %d" % len(FAILS) if FAILS else "ALL PASSED")
sys.exit(1 if FAILS else 0)
