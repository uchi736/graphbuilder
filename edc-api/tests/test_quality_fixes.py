"""抽出品質の手当て（2026-11-09）の単体試験。LLM・埋め込みサーバ無しで動く。

確かめること:
  1. 行つなぎ（api.join_wrapped_lines）: PDF の行の途中改行だけをつなぎ、表・見出し・箇条・
     文末の改行は残す
  2. 同名一致（normalize_name / find_same_name）: 大文字小文字・区切り・全角半角の違いを無視する
  3. 候補の数（candidate_top_k）: 語彙が小さければ全部、大きければ上位、25 個が上限
  4. 応答の読み取り（parse_choice_letter）: 「None of the above」の N を候補と取り違えない
  5. リレーションの正規化（SchemaCanonicalizer）: 同名は LLM に問わず語彙に揃え、
     語彙外は語彙全部を候補に見せる。自由発見で足した名前の表記ゆれも 1 つにまとまる
  6. 型の正規化（TypeCanonicalizer）: 同名は LLM に問わず語彙に揃える
  7. 抽出の経路（api.extract_kg）: 折り返しで割れた規格名が 1 つの入力にまとまり、
     他文書の項を 1 つのエンティティにする規則が抽出のプロンプトに入る
実行: コンテナ内で  python tests/test_quality_fixes.py
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
for _k in ("CANON_MERGE_THRESHOLD", "EDC_MAX_TOKENS", "CANON_FULL_VOCAB_MAX", "CANON_TOP_K",
           "EDC_JOIN_WRAPPED_LINES"):
    os.environ.pop(_k, None)

import edc.utils.llm_utils as L  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


# ------------------------------------------------------------
# 偽の LLM と埋め込み
# ------------------------------------------------------------
CALLS = []
VERIFY_ANSWER = {"mode": "target", "target": None}
OIE_ANSWER = ("[['性能計測', 'TestItem', 'governed_by', 'JIS B 8340', 'Standard'], "
              "['空気量', 'measurementitem', 'Measured-By', 'オリフィス', 'Instrument']]")
SD_ANSWER = ("governed_by：主語の試験が目的語の規格に従う。\n"
             "Measured-By：主語の計測項目が目的語の機器で計測される。")


def _verify_answer(prompt):
    choices = dict((name, letter) for letter, name in re.findall(r"^([A-Z])\. '(.+?)':", prompt, flags=re.M))
    none_letter = re.search(r"^([A-Z])\. None of the above", prompt, flags=re.M).group(1)
    mode = VERIFY_ANSWER["mode"]
    if mode == "none_text":
        return "None of the above."
    if mode == "target" and VERIFY_ANSWER["target"] in choices:
        return choices[VERIFY_ANSWER["target"]]
    return none_letter


class _FakeCompletions:
    def create(self, model, messages, temperature=0, max_tokens=None):
        prompt = messages[-1]["content"]
        if "semantic graph" in prompt:
            content = OIE_ANSWER
        elif "write a description" in prompt:
            content = SD_ANSWER
        elif "Choices" in prompt:
            content = _verify_answer(prompt)
        else:
            content = ""
        CALLS.append({"prompt": prompt, "answer": content})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")])


_fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
L._vllm_client = _fake_client
L.get_vllm_client = lambda: _fake_client


class FakeEmbedder:
    """文字の出現数ベクトル。意味は持たないが、類似度の順位は決まる。"""
    prompts = {}

    def encode(self, text, prompt_name=None, prompt=None):
        def vec(s):
            c = Counter(s)
            v = [float(c.get(chr(0x3000 + i), 0) + c.get(chr(0x4E00 + i * 37), 0)) for i in range(256)]
            v += [float(c.get(ch, 0)) for ch in "abcdefghijklmnopqrstuvwxyz_"]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            return [x / n + 1e-3 for x in v]
        return vec(text) if isinstance(text, str) else [vec(t) for t in text]


L.VllmEmbedder.__init__ = lambda self: setattr(self, "prompts", {})
L.VllmEmbedder.encode = FakeEmbedder.encode

import api  # noqa: E402
from edc.schema_canonicalization import (  # noqa: E402
    SchemaCanonicalizer, normalize_name, find_same_name, candidate_top_k, parse_choice_letter,
)
from edc.type_canonicalization import TypeCanonicalizer  # noqa: E402

SC_TEMPLATE = open(os.path.join(api._ROOT, "prompt_templates", "sc_template.txt"), encoding="utf-8").read()
TC_TEMPLATE = open(os.path.join(api._ROOT, "prompt_templates", "tc_template.txt"), encoding="utf-8").read()
J = api.join_wrapped_lines

# ------------------------------------------------------------
print("[1] 行つなぎ")
chunk = ("[TS-A104C > 2. 単独性能確認試験要領]\n"
         "2-4 上記性能計測により、流量及び揚程をJIS B 8340「ターボ形ブロワ・圧縮機の試験\n"
         "及び検査方法」により求める。\n\n"
         "2-5 手順B  ⇒  手順Aの順序の実施も認める。\n\n"
         "### 2-6 インペラ性能計測時に於ける運転条件\n"
         "性能計測時のポンプ運転条件は、TS-A101の3-5項に依る。\n")
out = J(chunk)
check("「ターボ形ブロワ・圧縮機の試験及び検査方法」" in out, "折り返しで割れた規格名がつながる")
check("\n及び検査方法" not in out, "「及び検査方法」が行頭に残らない")
check(out.count("\n\n") == chunk.count("\n\n"), "空行（段落の区切り）は残る")
check("\n### 2-6 インペラ" in out and "運転条件\n性能計測時" in out, "見出し（# 行）の前後の改行は残る")
check(out.startswith("[TS-A104C > 2. 単独性能確認試験要領]\n2-4"), "閉じ括弧で終わる行の改行は残る")

table = "| 計測項目 | 計測機器 |\n|---|---|\n| 吸込温度 | 熱電対 |\n| 吐出圧力 | 圧力計 |"
check(J(table) == table, "表（| で始まる行）は触らない")
check(J("計測値は次の表に記録する\n| 項目 | 値 |") == "計測値は次の表に記録する\n| 項目 | 値 |", "表の直前の行もつながない")
long = "ポンプの単独試運転では吸込温度と吐出温度を熱電対により計測し、その値を試験成績書に記録"
check(J(long + "\nする。") == long + "する。", "句読点で終わらない行は次の行とつながる")
check(J(long + "する。\n次に圧力を計測する。") == long + "する。\n次に圧力を計測する。", "句点で終わる行は改行を残す")
check(J(long + "、\n次に圧力を計測する。") == long + "、\n次に圧力を計測する。", "読点で終わる行も改行を残す（指定どおり）")
check(J(long + "\n2-5 次の試験") == long + "\n2-5 次の試験", "次の行が番号見出し（2-5）なら改行を残す")
check(J(long + "\n(1) 次の試験") == long + "\n(1) 次の試験", "次の行が (1) なら改行を残す")
check(J(long + "\n・吐出温度") == long + "\n・吐出温度", "次の行が箇条記号なら改行を残す")
check(J(long + "\n①吐出温度") == long + "\n①吐出温度", "次の行が丸数字なら改行を残す")
check(J(long + "\n第3条 適用") == long + "\n第3条 適用", "次の行が第3条なら改行を残す")
check(J(long + "\n3-5項に依る。") == long + "3-5項に依る。", "項番で始まる続きの行はつながる（番号見出しと区別）")
check(J(long + "\n2.5以上とする。") == long + "2.5以上とする。", "小数で始まる続きの行はつながる")
check(J("単独性能確認試験要領\n" + long + "する。") == "単独性能確認試験要領\n" + long + "する。",
      "短い題目行は次の本文につながない")
check(J("This is an English line that wraps at the\nright margin.") ==
      "This is an English line that wraps at the\nright margin.", "英文の行は連結しない")
check(J("計測値は JIS B 8340 に定める方法により流量と揚程を求めることとし、その手順は TS-A101\nの3-5項に依る。")
      .endswith("TS-A101の3-5項に依る。"), "半角で終わる行と日本語で始まる行はつながる")
check(J("短い「括弧の\n続き」") == "短い「括弧の続き」", "閉じていない括弧があれば短い行でもつながる")
check(J(long + "\r\nする。") == long + "する。", "CRLF の改行も扱う")
# 本番チャンク（70 件・1,221 行）の監査で見つかった誤結合の型
rev = "本基準はポンプの単独試運転の判定条件(X00124)に基づき制定する\nE改正 01.1.1 一部見直し"
check(J(rev) == rev, "改訂来歴の行（E改正…）にはつながない")
row = "承 認 / 審 査 / 作 成 / 担 当 / 規定類番号\n改 訂 符 号 / 発 行 / 配 布"
check(J(row) == row, "「 / 」区切りの表の行は触らない")
leader = "①方式A ・・・・・・・・・・・・ 100°C以下\n方式Bの場合は入口温度に依る"
check(J(leader) == leader, "点線の目次・欄の行は触らない")
label = long + "\nA"
check(J(label) == label, "英字だけの短い行（欄の値）にはつながない")
docs = ("TS-A101(最新版)\nポンプ単独試運転要領及び判定基準\nTS-A102(最新版)\n"
        "A4シリーズ型ポンプの初号機に関する\nインペラ単独性能確認試験要領及び判定基準\nTS-A104(最新版)")
check(J(docs) == ("TS-A101(最新版)\nポンプ単独試運転要領及び判定基準\nTS-A102(最新版)\n"
                  "A4シリーズ型ポンプの初号機に関するインペラ単独性能確認試験要領及び判定基準\nTS-A104(最新版)"),
      "関連文書一覧: 題名の折り返しはつなぎ、題名を次の文書番号の行へはつながない")
check(J("改行なし") == "改行なし" and J("") == "", "改行の無い文字列・空文字はそのまま")
# 短い行だけの入力（欄が 1 行ずつの表・OCR の表・項目の並び）: 比の基準に下限（表示幅 30）を設けた
cells = "計測項目\n計測機器\n吸込温度\n熱電対\n吐出圧力\n圧力計"
check(J(cells) == cells, "短い欄が 1 行ずつ並ぶ入力はつながない")
cells2 = "項目 判定基準\n軸受温度 80℃以下\n振動 20μm以下"
check(J(cells2) == cells2, "欄の名前と値が 1 行ずつの短い表はつながない")
cells3 = "被覆アーク溶接\n自動溶接\n確認する\n引張強さ"
check(J(cells3) == cells3, "本文が無く短い行だけなら、句読点の無い行どうしもつながない")
# 閉じていない括弧でつなぐのは、括弧が 2 行以内で閉じるときだけ
check(J("規格「ターボ形ブロワの\n計測項目\n計測機器\n吸込温度") == "規格「ターボ形ブロワの\n計測項目\n計測機器\n吸込温度",
      "括弧が 2 行以内で閉じなければ、後ろの短い行をつながない")
check(J("規格「ターボ形ブロワ・\n圧縮機の試験\n及び検査方法」") == "規格「ターボ形ブロワ・圧縮機の試験及び検査方法」",
      "括弧が 2 行以内で閉じれば短い行でもつながる")
check(J("規格「ターボ形ブロワ・\n圧縮機の試験\n及び検査\n方法」") == "規格「ターボ形ブロワ・\n圧縮機の試験\n及び検査\n方法」",
      "括弧が閉じるのが 3 行先なら短い行はつながない")
# 1 文字ずつ空白で区切った欄見出し（「合 計」「定 期 的」）は表の一部として触らない
spaced = long + "\n合 計"
check(J(spaced) == spaced, "1 文字ずつ空白で区切った欄見出しにはつながない")
spaced2 = "初品および\n定 期 的"
check(J(spaced2) == spaced2, "「初品および⏎定 期 的」をつながない")
# 句点の無い要求文の終わり（「こと」で終わり、次行が漢字で始まる）はつながない。仮名で続く折り返しはつなぐ
koto = "部品の寸法検査及び外観の検査を治具の更新ごとに初品について実施すること\n旧型の場合6ヶ月又は50個ごとに検査する。"
check(J(koto) == koto, "「…実施すること⏎旧型の場合…」をつながない")
check(J(long + "すること\nとする。") == long + "することとする。", "「…すること⏎とする。」はつながる")
# 本文の中の長い行の折り返しは、下限を設けても従来どおりつながる
check(J(chunk) == chunk.replace("試験\n及び検査方法", "試験及び検査方法"),
      "試験チャンクでは規格名の折り返し 1 か所だけをつなぐ（下限を設けても変わらない）")
os.environ["EDC_JOIN_WRAPPED_LINES"] = "0"
check(api._prepare_text(chunk) == chunk, "EDC_JOIN_WRAPPED_LINES=0 で行つなぎを止められる")
os.environ.pop("EDC_JOIN_WRAPPED_LINES")
check(api._prepare_text(chunk) == out, "既定では行つなぎが働く")

# ------------------------------------------------------------
print("[2] 同名一致")
VOCAB17 = {
    "DEFINES": "主語(条項・文書)が目的語(用語)の定義を与える",
    "MENTIONS": "主語が目的語に言及する(定義ではない単純な言及)",
    "REFERS_TO": "主語が目的語(他条項・文書・規格)を参照する",
    "SUBORDINATE_TO": "主語が目的語の下位・従属である(章節などの階層)",
    "IS_A": "主語が目的語の一種である(上位下位・分類)",
    "PART_OF": "主語が目的語の構成要素・一部である",
    "MEASURED_AT": "計測項目が計測される箇所・部位・機器",
    "MEASURED_BY": "計測項目を計測する計測機器・計測手段",
    "HAS_VALUE": "計測項目/条件が持つ数値・設定値・許容値・単位",
    "APPLIES_TO": "試験/基準/手順が適用される機器・型式",
    "HAS_STEP": "試験/手順が含むステップ・操作",
    "PRECEDES": "手順が別の手順に先行する(時系列)",
    "REQUIRES": "試験/手順が成立するために必要な条件・前提・準備",
    "JUDGED_BY": "試験項目の合否を決める判定基準・許容値",
    "GOVERNED_BY": "根拠となる規格・承認図・指示書・上位規程",
    "PERFORMED_BY": "試験/検査/承認を実施する部門・組織・担当",
    "RECORDED_IN": "結果・計測値が記録される様式・文書",
}
check(normalize_name("governed_by") == normalize_name("GOVERNED_BY") == normalize_name("Governed-By")
      == normalize_name("governed by") == normalize_name("ＧＯＶＥＲＮＥＤ＿ＢＹ"), "表記ゆれが同じ照合キーになる")
check(find_same_name("governed_by", VOCAB17) == "GOVERNED_BY", "governed_by → GOVERNED_BY")
check(find_same_name("applies-to", VOCAB17) == "APPLIES_TO", "applies-to → APPLIES_TO")
check(find_same_name("GOVERNED_BY", VOCAB17) == "GOVERNED_BY", "完全一致はそのまま")
check(find_same_name("is_allowed", VOCAB17) is None, "語彙に無い名前は None")
check(find_same_name("", VOCAB17) is None, "空の名前は None")

# ------------------------------------------------------------
print("[3] 候補の数")
check(candidate_top_k(17) == 17, "17 種類なら全部（17）")
check(candidate_top_k(22) == 22, "22 種類なら全部（22）")
check(candidate_top_k(25) == 25, "25 種類なら全部（25）")
check(candidate_top_k(40) == 15, "40 種類なら上位 15")
check(candidate_top_k(3) == 3, "3 種類なら 3")
os.environ["CANON_TOP_K"] = "40"
check(candidate_top_k(60) == 25, "上位の数を大きくしても 25 が上限（選択肢の記号 A〜Y）")
os.environ.pop("CANON_TOP_K")
os.environ["CANON_FULL_VOCAB_MAX"] = "5"
check(candidate_top_k(17) == 15, "CANON_FULL_VOCAB_MAX で全部を見せる上限を変えられる")
os.environ.pop("CANON_FULL_VOCAB_MAX")

# ------------------------------------------------------------
print("[4] 応答の読み取り")
letters = [chr(ord("A") + i) for i in range(17)]  # A〜Q、該当なしは R
check(parse_choice_letter("B", letters) == "B", "B")
check(parse_choice_letter("**C**", letters) == "C", "**C**")
check(parse_choice_letter("Answer: D", letters) == "D", "Answer: D")
check(parse_choice_letter("(E) 'GOVERNED_BY'", letters) == "E", "(E) 'GOVERNED_BY'")
check(parse_choice_letter("O. 'GOVERNED_BY'", letters) == "O", "O. 'GOVERNED_BY'")
check(parse_choice_letter("None of the above.", letters) is None, "None of the above を N と取り違えない")
check(parse_choice_letter("The answer is B", letters) is None, "文章の頭文字を記号とみなさない")
check(parse_choice_letter("I think B", letters) is None, "英文の I を 9 番目の候補とみなさない")
check(parse_choice_letter("B GOVERNED_BY", letters) == "B", "記号の後に候補名が続く応答は読める")
check(parse_choice_letter("D is the closest", letters) == "D", "記号の後に空白と英文が続く応答も読める")
check(parse_choice_letter("B governed_by", letters) == "B", "記号の後に小文字の候補名が続く応答も読める")
check(parse_choice_letter("A\n\nbecause the subject is a test", letters) == "A", "記号の後に改行と理由が続く応答も読める")
check(parse_choice_letter("I'm not sure", letters) is None, "I'm の I を 9 番目の候補とみなさない")
check(parse_choice_letter("I", letters) == "I" and parse_choice_letter("I. 'GOVERNED_BY'", letters) == "I",
      "I だけ、または I. 候補名 の応答は 9 番目の候補として読む")
check(parse_choice_letter("N/A", letters) is None and parse_choice_letter("None", letters) is None,
      "N/A・None は該当なし")
check(parse_choice_letter("R", letters) is None, "該当なしの記号（R）は None")
check(parse_choice_letter("", letters) is None and parse_choice_letter(None, letters) is None, "空の応答は None")

# ------------------------------------------------------------
print("[5] リレーションの正規化")
emb = FakeEmbedder()
sc = SchemaCanonicalizer(dict(VOCAB17), emb, verify_openai_model="vllm")
CALLS.clear()
t, cand = sc.canonicalize("性能計測はJIS B 8340による", ["性能計測", "governed_by", "JIS B 8340"],
                          {"governed_by": "主語の試験が目的語の規格に従う。"}, SC_TEMPLATE, enrich=False)
check(t == ["性能計測", "GOVERNED_BY", "JIS B 8340"], f"governed_by は GOVERNED_BY に揃う: {t}")
check(not CALLS, "同名一致では LLM を呼ばない")
t, _ = sc.canonicalize("x", ["a", "applies_to", "b"], {}, SC_TEMPLATE, enrich=False)
check(t == ["a", "APPLIES_TO", "b"], "定義文が無くても同名なら写る（従来は落ちていた）")

CALLS.clear()
VERIFY_ANSWER.update(mode="target", target="JUDGED_BY")
t, cand = sc.canonicalize("各点80℃以下であること", ["軸受温度", "must_satisfy", "各点80℃以下"],
                          {"must_satisfy": "主語が目的語の合否条件を満たす必要がある。"}, SC_TEMPLATE, enrich=False)
shown = re.findall(r"^[A-Z]\. '(.+?)':", CALLS[-1]["prompt"], flags=re.M) if CALLS else []
check(len(shown) == 17 and set(shown) == set(VOCAB17), f"語彙 17 種類が全部候補に出る: {len(shown)}")
check(t == ["軸受温度", "JUDGED_BY", "各点80℃以下"], f"LLM が選んだ語彙に写る: {t}")
check(len(cand) == 17, "候補と類似度も 17 件返る")

VERIFY_ANSWER.update(mode="none_text")
t, _ = sc.canonicalize("x", ["a", "is_allowed", "b"], {"is_allowed": "主語が目的語において許容される。"},
                       SC_TEMPLATE, enrich=False)
check(t is None, "「None of the above.」の応答は 14 番目の候補でなく該当なしとして落ちる")

sc2 = SchemaCanonicalizer(dict(VOCAB17), emb, verify_openai_model="vllm")
VERIFY_ANSWER.update(mode="none")
t, _ = sc2.canonicalize("x", ["n=2台目", "precedes", "n=1台目"], {"precedes": "主語の手順が先に行われる。"},
                        SC_TEMPLATE, enrich=True)
check(t[1] == "PRECEDES" and "precedes" not in sc2.schema_dict, "enrich でも precedes を別名で足さず PRECEDES に揃う")
t, _ = sc2.canonicalize("x", ["要領", "has_section", "2. 単独性能確認試験要領"], {"has_section": "主語が目的語の節を持つ。"},
                        SC_TEMPLATE, enrich=True)
check(t[1] == "has_section" and "has_section" in sc2.schema_dict, "本当に新しい名前は従来どおり語彙に足される")
CALLS.clear()
t, _ = sc2.canonicalize("x", ["要領", "HAS-SECTION", "3. 判定"], {"HAS-SECTION": "主語が目的語の節を持つ。"},
                        SC_TEMPLATE, enrich=True)
check(t[1] == "has_section" and not CALLS, "足した名前の表記ゆれ（HAS-SECTION）は LLM を呼ばず 1 つにまとまる")

sc3 = SchemaCanonicalizer({}, emb, verify_openai_model="vllm")
t, _ = sc3.canonicalize("x", ["a", "uses", "b"], {"uses": "主語が目的語を使う。"}, SC_TEMPLATE, enrich=True)
t2, _ = sc3.canonicalize("x", ["c", "USES", "d"], {"USES": "主語が目的語を使う。"}, SC_TEMPLATE, enrich=True)
check(t[1] == "uses" and t2[1] == "uses" and list(sc3.schema_dict) == ["uses"], "語彙の無い自由発見でも表記ゆれは 1 つにまとまる")

big = {f"REL_{i:02d}": f"定義{i}" for i in range(40)}
sc4 = SchemaCanonicalizer(big, emb, verify_openai_model="vllm")
CALLS.clear()
VERIFY_ANSWER.update(mode="none")
sc4.canonicalize("x", ["a", "new_rel", "b"], {"new_rel": "定義3"}, SC_TEMPLATE, enrich=False)
shown = re.findall(r"^[A-Z]\. '(.+?)':", CALLS[-1]["prompt"], flags=re.M) if CALLS else []
check(len(shown) == 15, f"語彙が 40 種類なら上位 15 を候補に出す: {len(shown)}")

# ------------------------------------------------------------
print("[6] 型の正規化")
TYPES = {"MeasurementItem": "計測項目", "Instrument": "計測機器", "Standard": "根拠規格", "Section": "章節"}
tc = TypeCanonicalizer(dict(TYPES), emb, verify_openai_model="vllm")
CALLS.clear()
ty, _ = tc.canonicalize("x", "空気量", "measurementitem", TC_TEMPLATE, enrich=False)
check(ty == "MeasurementItem" and not CALLS, "measurementitem → MeasurementItem（LLM を呼ばない）")
ty, _ = tc.canonicalize("x", "JIS B 8340", "Standard", TC_TEMPLATE, enrich=False)
check(ty == "Standard", "完全一致はそのまま")
CALLS.clear()
VERIFY_ANSWER.update(mode="none_text")
ty, _ = tc.canonicalize("x", "検査方法", "Method", TC_TEMPLATE, enrich=False)
shown = re.findall(r"^[A-Z]\. '(.+?)':", CALLS[-1]["prompt"], flags=re.M) if CALLS else []
check(ty is None and len(shown) == 4, "語彙外の型は語彙全部を候補に出し、None of the above は該当なし")

# ------------------------------------------------------------
print("[7] 抽出の経路（偽の LLM）")
CALLS.clear()
VERIFY_ANSWER.update(mode="none")
r = api._to_response(api.extract_kg(text=chunk, doctype="試運転_検査基準", chunk_method="line", enrich_schema=False))
oie = [c for c in CALLS if "semantic graph" in c["prompt"]]
inputs = [c["prompt"].split("Text:")[-1].strip() for c in oie]
check(r["n_input_chunks"] == 5, f"行分割でも規格名は 1 行にまとまる（6→5 入力）: {r['n_input_chunks']}")
check(not any(x.startswith("及び検査方法") for x in inputs), "「及び検査方法」で始まる入力は無い")
check(oie and "cites a clause of another document" in oie[0]["prompt"], "他文書の項を 1 つにする規則がプロンプトに入る")
rels = {(t["subject"], t["relation"], t["object"]) for t in r["triplets"]}
check(("性能計測", "GOVERNED_BY", "JIS B 8340") in rels, "governed_by は落ちずに GOVERNED_BY で残る")
check(("空気量", "MEASURED_BY", "オリフィス") in rels, "Measured-By は MEASURED_BY で残る")
check(any(t.get("subject_type") == "MeasurementItem" for t in r["triplets"]), "型 measurementitem も MeasurementItem に揃う")
check(not r["dropped"], f"語彙外として落ちる組は無い: {r['dropped'][:2]}")
check(r["vocabulary_closed"] is True, "語彙は閉じたまま")

CALLS.clear()
r = api._to_response(api.extract_kg(text=chunk, doctype="社内規程_就業規則", chunk_method="line", enrich_schema=False))
oie = [c for c in CALLS if "semantic graph" in c["prompt"]]
check(oie and "Domain rules" not in oie[0]["prompt"], "他の文書タイプ（社内規程_就業規則）には規則が入らない")

print()
print("FAILED: %d" % len(FAILS) if FAILS else "ALL PASSED")
sys.exit(1 if FAILS else 0)
