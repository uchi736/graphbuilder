# -*- coding: utf-8 -*-
"""規則（src/chunk_reference_rules.py）の単体の試験。型ごとの例文で確かめる。例文はすべて作り物である（実物の文書の文は使っていない）。

LLM もグラフも使わない。標準ライブラリだけで動く。
使い方: cd /code && python /tests/t_rules.py   （写しの src が /code/src に見えていること。tests/run.sh がそう動かす）
"""
import os
import sys

sys.path.insert(0, os.environ.get("CODE_DIR", "/code"))
from src import chunk_reference_rules as R  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("OK  " if cond else "NG  ") + name, "" if cond else detail)


def mk(doc, texts, crumbs=None):
    stem = doc.rsplit(".", 1)[0]
    return [{"id": "%s#%d" % (stem, i + 1), "fileName": doc, "position": i + 1, "text": "[%s]\n%s" % ((crumbs or {}).get(i + 1, stem), t)} for i, t in enumerate(texts)]


def run(rows, **kw):
    return R.find_references(rows, **kw)


def pick(res, source, text):
    """参照元のチャンクと、場所の語（または文書の表記・原文）の一部で 1 件選ぶ。"""
    mine = [r for r in res["references"] if r["source"] == source]
    hits = [r for r in mine if text in (r["locator"] or "")] or [r for r in mine if text in (r["target_document"] or "")] or [r for r in mine if text in r["quote"]]
    return hits[0] if hits else {"targets": [], "level": None, "scope": None, "target_doc": None, "reason": "（参照が無い）", "form": None, "history": None, "locator": None, "target_document": None}


def ids(ref):
    return [t["id"] for t in ref["targets"]]


# ───────── 1. 第 N 条の形（人事・情報セキュリティの規定など） ─────────
REG = mk("就業規則.pdf", [
    "# 就業規則\n第1章 総則\n(目的)\n第1条 この規則は、従業員の就業に関する事項を定める。\n(適用範囲)\n第2条 この規則は、第3条に定める従業員に適用する。\n2 前項の規定にかかわらず、嘱託については別表第1による。",
    "(定義)\n第3条 従業員とは、第2章の手続により採用された者をいう。\n(休暇)\n第4条 年次有給休暇は、前条の従業員に与える。手続は休暇規程(HR-012)第5条による。\n第5条 前2条に定めるもののほか、必要な事項は別に定める。",
    "第2章 採用\n第6条 採用は、第2条第1項及び第2項の定めによる。\n第7条 第3条から第5条までの規定は、嘱託に準用する。同条第2項の定めも同様とする。次条の届出を要する。",
    "第8条 届出は、前各条の定めに従う。本条の定めは、情報セキュリティ管理規程(IS-001。以下「管理規程」という。)第9条に優先しない。管理規程第10条及び第11条も同じ。",
    "別表第1\n嘱託の勤務時間\n区分A 週3日",
]) + mk("HR-012_休暇規程.pdf", [
    "# 休暇規程\n第1条 この規程は、就業規則第4条に基づき、休暇の手続を定める。",
    "第5条 休暇の申請は、所定の届出による。\n第6条 ISO 27001に定める管理策に従う。次条の定めにも従う。\n第7条 記録は3年間保存する。",
]) + mk("IS-001_情報セキュリティ管理規程.pdf", [
    "# 情報セキュリティ管理規程\n第9条 利用者は、パスワードを他人に知らせてはならない。\n第10条 端末は施錠する。",
    "第11条 持出しは、所属長の許可を受ける。",
])
res = run(REG)
r = pick(res, "就業規則#1", "第3条")
check("条: 同じ文書の条（第3条）→ その条のチャンク", ids(r) == ["就業規則#2"] and r["scope"] == "same_document", r)
r = pick(res, "就業規則#1", "前項")
check("相対: 前項（条の中の項）は同じチャンクの中で済み、リンクを作らない", r["level"] == "in_chunk" and not r["targets"], r)
r = pick(res, "就業規則#1", "別表第1")
check("別表: 別表第1 → 題名のあるチャンク", ids(r) == ["就業規則#5"] and r["form"] == "別表・図・添付資料", r)
r = pick(res, "就業規則#2", "第2章")
check("章: 第2章 → 章の見出しのあるチャンク", ids(r) == ["就業規則#3"], r)
r = pick(res, "就業規則#2", "前条")
check("相対: 前条 → 1 つ前の条（同じチャンクなのでリンクなし）", r["level"] == "in_chunk" and r["form"] == "相対的な参照", r)
r = pick(res, "就業規則#2", "第5条")
check("識別子＋条: 名称(識別子)第5条 → 他の文書の条", r["target_doc"] == "HR-012_休暇規程.pdf" and ids(r) == ["HR-012_休暇規程#2"], r)
check("識別子＋条: 括弧書きの識別子を重ねて出さない", sum(1 for x in res["references"] if x["source"] == "就業規則#2" and x["target_doc"] == "HR-012_休暇規程.pdf") == 1)
r = pick(res, "就業規則#3", "第2条第1項及び第2項")
check("並列: 第2条第1項及び第2項（後ろの項は前の条を引き継ぐ）", ids(r) == ["就業規則#1"], r)
r = pick(res, "就業規則#3", "第3条から第5条まで")
check("範囲: 第3条から第5条まで → 間の条も含む", ids(r) == ["就業規則#2"] and "まで" in r["locator"], r)
r = pick(res, "就業規則#3", "同条第2項")
check("相対: 同条第2項 → 直前に挙げた条", ids(r) == ["就業規則#2"], r)
r = pick(res, "就業規則#3", "次条")
check("相対: 次条 → 次の条のチャンク", ids(r) == ["就業規則#4"], r)
r = pick(res, "就業規則#4", "前各条")
check("相対: 前各条 → 前にあるすべての条", set(ids(r)) == {"就業規則#1", "就業規則#2", "就業規則#3"}, r)
r = pick(res, "就業規則#4", "第9条")
check("略称の定義の直後: 名称(識別子。以下「略称」という。)第9条", r["target_doc"] == "IS-001_情報セキュリティ管理規程.pdf" and ids(r) == ["IS-001_情報セキュリティ管理規程#1"], r)
r = pick(res, "就業規則#4", "第10条及び第11条")
check("略称: 略称＋第10条及び第11条 → 定義した文書の条（並列）", r["target_doc"] == "IS-001_情報セキュリティ管理規程.pdf" and set(ids(r)) == {"IS-001_情報セキュリティ管理規程#1", "IS-001_情報セキュリティ管理規程#2"}, r)
r = pick(res, "HR-012_休暇規程#1", "第4条")
check("名称＋条: 目録にある名称＋第4条 → その文書の条", r["target_doc"] == "就業規則.pdf" and ids(r) == ["就業規則#2"], r)
r = pick(res, "HR-012_休暇規程#2", "ISO 27001")
check("外部: 規格の番号は外部の文書として記録し、結ばない", r["scope"] == "external" and not r["targets"] and r["reason"] == "", r)
check("条の見出しそのものは参照にしない", not any(x["source"] == "就業規則#1" and x["locator"] in ("第1条", "第2条", "第1章", "1章") for x in res["references"]),
      [x["locator"] for x in res["references"] if x["source"] == "就業規則#1"])

# ───────── 2. 3-5 項の形（ハイフンの項番号） ─────────
HY = mk("KA-T001C.pdf", [
    "KA-T001 試運転要領\n1. 適用\nこの要領は試運転に適用する。\n2. 試験\n2-1 性能試験\n性能を計測する。\n2-2 全力運転試験\n最高回転数で運転する。",
    "2-3 計測項目\n2-1及び2-2項に示す運転状態で計測する。但し2-2項については温度を除く。上記2項の結果を記録する。\n3. 判定\n3-1 性能\n判定は図-2による。",
    "3-2 振動\n許容値は別表1 符号Bによる。条件はKA-T002の3-5項に依る。KA T002 の性能試験による。\n(注1) 軸受の直前の値を示す。\n図-2 判定の範囲",
    "3-3 油温\n(注1)に示す値とする。2-1〜2-3の順に行う。1) 前項の圧力を確かめる。",
    "別表1\n符号A 1 台目\n符号B 2 台目",
]) + mk("KA-T002A.pdf", [
    "KA-T002 計測要領\n1. 適用\n計測に適用する。\n2. 準備\n2-1 性能試験\n準備をする。",
    "3. 条件\n3-4 冷却\n水を流す。\n3-5 潤滑油供給条件\n圧力を保つ。",
])
res = run(HY)
r = pick(res, "KA-T001C#2", "2-1及び2-2項")
check("並列: 2-1及び2-2項 → 両方の項のチャンク", ids(r) == ["KA-T001C#1"], r)
r = pick(res, "KA-T001C#2", "上記2項")
check("接頭の語: 上記2項 → 2 項（下位の 2-1・2-2 を含む）", "KA-T001C#1" in ids(r), r)
r = pick(res, "KA-T001C#2", "図-2")
check("図: 図-2による → 図の題名のあるチャンク", "KA-T001C#3" in ids(r), r)
r = pick(res, "KA-T001C#3", "別表1 符号B")
check("別表＋下位の指定: 別表1 符号B → 別表の中のその行のチャンク", ids(r) == ["KA-T001C#5"], r)
r = pick(res, "KA-T001C#3", "3-5項")
check("識別子＋項: X の 3-5 項 → 他の文書のその項", r["target_doc"] == "KA-T002A.pdf" and ids(r) == ["KA-T002A#2"] and r["form"] == "文書番号＋項", r)
r = pick(res, "KA-T001C#3", "性能試験")
check("識別子＋見出しの名称: X の性能試験による → 題名の一致する見出し（識別子の空白のゆれも同じ文書）", r["target_doc"] == "KA-T002A.pdf" and ids(r) == ["KA-T002A#1"] and r["form"] == "文書番号＋見出し名", r)
r = pick(run(HY, disable=["heading_name"]), "KA-T001C#3", "KA T002")
check("識別子＋見出しの名称を外すと、文書単位の参照になる", r["level"] == "document" and r["target_doc"] == "KA-T002A.pdf" and not r["targets"], r)
r = pick(res, "KA-T001C#4", "(注1)")
check("注: 最も近い注の定義へ", ids(r) == ["KA-T001C#3"] and r["form"] == "注", r)
r = pick(res, "KA-T001C#4", "2-1〜2-3")
check("範囲: 2-1〜2-3 → 間の 2-2 も含めて引く", ids(r) == ["KA-T001C#1", "KA-T001C#2"], r)
check("数値の範囲（2〜3 点、3% など）を参照にしない", not any(x["locator"] in ("2~3", "3") for x in run(mk("KA-X900A.pdf", ["1. 計測\n2〜3点を計測する。誤差は3%以内とする。4,4A形に適用する。"]))["references"]))

# ───────── 3. 3.5 の形（小数点の項番号。見出し記号つきの Markdown） ─────────
DOT = mk("AB-QC-101C.md", [
    "# AB-QC-101 受入検査基準\n\n## 目次\n\n1. 適用範囲 ..... 1\n2. 引用文書 ..... 1\n3. 検査の方法 ..... 2",
    "## 1. 適用範囲\n\nこの基準は、購入品の受入検査について定める。\n\n## 2. 引用文書\n\nJIS Z 9015-1 に準拠する。AB-QC-102 及び AB QC-103 を引用する。QC-KAN-012により保管する。",
    "## 3. 検査の方法\n\n### 3.1 抜取\n\n抜取は 3.2 による。検査水準は AB-QC-102 の 4.1 による。記録は AB-QC-103 5.2 に定める様式で残す。図 1〜図 3 に示す。\n\n### 3.2 判定\n\n判定は別表 1 及び 2 による。不合格品は 3.1 及び 3.3 に従って処置する。",
    "### 3.3 処置\n\n処置は前項の判定に基づく。「3.1 による」を「3.2 による」に改める。ＡＢ－ＱＣ－１０２の別表１を参照。AB-QC-102D 4.1 を参照。7. による。",
    "## 4. 改訂履歴\n\n| 版 | 内容 |\n|---|---|\n| A | 3.2 項の判定を見直し。AB-QC-102 の 4.1 に合わせた |\n| B | 別表 1 を追加 |",
    "## 別表 1 抜取数\n\n| 区分 | 数 |\n|---|---|\n| A | 3 |\n\n## 別表 2 判定数\n\n| 区分 | 数 |\n|---|---|\n| A | 1 |\n\n図 1 抜取の流れ\n\n図 2 判定の流れ\n\n図 3 処置の流れ",
]) + mk("AB-QC-102D.md", [
    "# AB-QC-102 抜取検査要領\n\n## 4. 検査水準\n\n### 4.1 通常の水準\n\n通常は水準 II とする。\n\n### 4.2 きつい水準\n\n連続して不合格のときは水準 III とする。",
    "## 別表 1 水準の表\n\n| 水準 | 数 |\n|---|---|\n| II | 5 |",
]) + mk("AB-QC-103A.md", ["# AB-QC-103 検査記録要領\n\n## 5. 記録\n\n### 5.1 保管\n\n5 年間保管する。\n\n### 5.2 様式\n\n記録は所定の様式に残す。"])
res = run(DOT)
cat = res["catalogue"]["AB-QC-101C.md"]
r = pick(res, "AB-QC-101C#3", "3.2")
check("小数点: 3.2 による → 同じチャンクの中で済む", r["level"] == "in_chunk", r)
r = pick(res, "AB-QC-101C#3", "4.1")
check("識別子＋小数点: X の 4.1 による → 他の文書の項", r["target_doc"] == "AB-QC-102D.md" and ids(r) == ["AB-QC-102D#1"], r)
r = pick(res, "AB-QC-101C#3", "5.2")
check("識別子＋空白＋番号: X 5.2 に定める（「の」が無い）→ 番号を識別子の枝番として飲み込まない", r["target_doc"] == "AB-QC-103A.md" and ids(r) == ["AB-QC-103A#1"], r)
r = pick(res, "AB-QC-101C#3", "図 1")
check("範囲: 図 1〜図 3 → 間の図 2 も含む", ids(r) == ["AB-QC-101C#6"] and r["locator"].replace(" ", "") == "図1〜図3", r)
r = pick(res, "AB-QC-101C#3", "別表 1 及び 2")
check("並列: 別表 1 及び 2（種類の語を省いた番号）", ids(r) == ["AB-QC-101C#6"], r)
r = pick(res, "AB-QC-101C#3", "3.1 及び 3.3")
check("並列: 3.1 及び 3.3 → 別のチャンクにある 3.3 へ", ids(r) == ["AB-QC-101C#4"], r)
r = pick(res, "AB-QC-101C#4", "前項")
check("相対: 前項（項番号の文書）→ 1 つ前の項", ids(r) == ["AB-QC-101C#3"], r)
check("カギ括弧の中の番号は参照にしない（「3.1 による」を「3.2 による」に改める）",
      not any(x["source"] == "AB-QC-101C#4" and (x["locator"] or "").replace(" ", "") in ("3.1", "3.2") for x in res["references"]),
      [x["locator"] for x in res["references"] if x["source"] == "AB-QC-101C#4"])
r = pick(res, "AB-QC-101C#4", "別表1")
check("表記ゆれ: 全角の識別子と全角の数字（ＡＢ－ＱＣ－１０２の別表１）", r["target_doc"] == "AB-QC-102D.md" and ids(r) == ["AB-QC-102D#2"], r)
r = [x for x in res["references"] if x["source"] == "AB-QC-101C#4" and x["target_document"] == "AB-QC-102D"]
check("表記ゆれ: 改正記号つきの識別子（AB-QC-102D）も同じ文書", len(r) == 1 and r[0]["target_doc"] == "AB-QC-102D.md" and ids(r[0]) == ["AB-QC-102D#1"], r)
r = [x for x in res["references"] if x["source"] == "AB-QC-101C#2" and x["target_doc"] == "AB-QC-103A.md"]
check("表記ゆれ: ハイフンを空白で書いた識別子（AB QC-103）、文書だけの参照は文書へ結ぶ", len(r) == 1 and r[0]["level"] == "document" and r[0]["form"] == "文書番号のみ", r)
r = pick(res, "AB-QC-101C#2", "JIS Z 9015-1")
check("外部: JIS の番号", r["scope"] == "external", r)
r = pick(res, "AB-QC-101C#2", "QC-KAN-012")
check("外部: 目録に無い識別子＋参照の語 → 外部の文書（理由なし。LLM に回さない）", r["scope"] == "external" and r["reason"] == "", r)
check("単独の「7. による」は、目録に 7 項が無いので参照にしない", not any(x["source"] == "AB-QC-101C#4" and (x["locator"] or "").startswith("7") for x in res["references"]))
check("目次の行は見出しとして目録に入れない（1・2・3 項は本文のチャンクにある）",
      ids(pick(run(DOT + mk("AB-QC-109A.md", ["# AB-QC-109 補足\n\n## 1. 適用\n\nAB-QC-101 の 3. の方法による。"])), "AB-QC-109A#1", "3.")) == ["AB-QC-101C#3", "AB-QC-101C#4"],
      pick(run(DOT + mk("AB-QC-109A.md", ["# AB-QC-109 補足\n\n## 1. 適用\n\nAB-QC-101 の 3. の方法による。"])), "AB-QC-109A#1", "3."))
hist = [x for x in res["references"] if x["source"] == "AB-QC-101C#5"]
check("改正履歴: 履歴の中の参照も張る", any(ids(x) == ["AB-QC-101C#3"] for x in hist) and any(ids(x) == ["AB-QC-102D#1"] for x in hist) and any(ids(x) == ["AB-QC-101C#6"] for x in hist), hist)
check("改正履歴: 履歴の中の参照は history で見分けられる", hist and all(x["history"] for x in hist) and not any(x["history"] for x in res["references"] if x["source"] != "AB-QC-101C#5"))
check("目録: 識別子は改正記号の有無を両方持つ", cat["identifiers"] == ["AB-QC-101", "AB-QC-101C"], cat)

# ───────── 4. 表の欄、相対的な参照、決められない参照 ─────────
MIX = mk("QK-11B.md", [
    "# QK-11 検査員認定規定\n\n## 第 1 条（目的）\n\nこの規定は、検査員の認定を定める。\n\n## 第 2 条（区分）\n\n| 規定 | 条 | 記号 |\n|---|---|---|\n| QK-12 | 第 3 条 | S-1 |\n\n帳票規定の第 6 条による。旧基準 12 号の 3-5 項による。",
    "## 第 3 条（手続）\n\n申請は、「検査記録管理規定」に定める様式による。「存在しない管理規程」による。同条第 2 項は適用しない。別表 9 による。同規定の第 3 条も同じ。",
]) + mk("QK-12A.md", ["# 検査記録管理規定\n\n| 項目 | 内容 |\n|---|---|\n| 文書番号 | QK-12 |\n\n## 第 3 条（様式）\n\n様式は別に定める。"])
res = run(MIX)
r = pick(res, "QK-11B#1", "第 3 条")
check("表の欄: | 識別子 | 第 3 条 | → 自分の文書の条ではなく、その識別子の文書の条", r["target_doc"] == "QK-12A.md" and ids(r) == ["QK-12A#1"], r)
r = pick(res, "QK-11B#1", "第 6 条")
check("通称: 「帳票規定の第 6 条」は自分の文書の条へ結ばず、理由を付けて残す", not r["targets"] and r["scope"] is None and r["target_document"] == "帳票規定" and r["reason"], r)
r = pick(res, "QK-11B#1", "3-5 項")
check("旧番号: 「旧基準 12 号の 3-5 項」も同じ", not r["targets"] and r["scope"] is None and r["reason"], r)
r = pick(res, "QK-11B#2", "検査記録管理規定")
check("カギ括弧の名称＋に定める○○: ○○が参照先の見出しの題名（第 3 条（様式））と一致 → その条のチャンク", r["target_doc"] == "QK-12A.md" and ids(r) == ["QK-12A#1"]
      and r["form"] == "文書番号＋見出し名", r)
r = pick(run(MIX + mk("QK-13A.md", ["# QK-13 補足\n\n記録は、「検査記録管理規定」による。保管は、「検査記録管理規定」に定める期間とする。"])), "QK-13A#1", "検査記録管理規定")
check("カギ括弧の名称: 目録の名称と一致 → その文書（文書単位）", r["target_doc"] == "QK-12A.md" and r["level"] == "document" and r["form"] == "名称のみ", r)
r = pick(res, "QK-11B#2", "存在しない管理規程")
check("カギ括弧の名称: 目録に無い名称 → 理由を付けて残す（LLM に回す分）", r["scope"] is None and r["reason"] and not r["targets"], r)
r = pick(res, "QK-11B#2", "同条")
check("相対: 先行する参照の無い「同条」は結ばない", not r["targets"] and r["reason"], r)
r = pick(res, "QK-11B#2", "別表 9")
check("題名の無い別表 → 理由を付けて残す", not r["targets"] and r["reason"] == "題名が目録に無い" and r["scope"] == "same_document", r)
r = pick(run(mk("KC-301A.md", ["# KC-301 手順\n\n## 1. 適用\n\n次の別表 1 による。試験機の別表 1 は使わない。", "## 別表 1 一覧\n\n| 項目 | 値 |\n|---|---|\n| A | 1 |"])), "KC-301A#1", "別表 1")
check("「次の別表 1」は自分の文書の別表（「X の」の X が指示の語なら、所有者の分からない場所にしない）", ids(r) == ["KC-301A#2"], r)
r = pick(res, "QK-11B#2", "同規定の第 3 条")
check("「同規定」＋条 → 直前に挙げた文書の条", r["target_doc"] == "QK-12A.md" and ids(r) == ["QK-12A#1"], r)

# ───────── 5. 題名の落ちた別表（sub_fallback）と、その歯止め ─────────
SUB = mk("KB-501A.md", ["# KB-501 計測要領\n\n## 2. 計測\n\n1 台目は別表 1 符号 A による。", "## 計測点\n\n### 符号 A 1 台目\n\n4 点を計測する。\n\n### 符号 B 2 台目\n\n2 点を計測する。"])
r = pick(run(SUB), "KB-501A#1", "符号 A")
check("題名の落ちた別表: 下位の指定の見出しが 1 か所だけなら結ぶ", ids(r) == ["KB-501A#2"], r)
r = pick(run(SUB, disable=["sub_fallback"]), "KB-501A#1", "符号 A")
check("題名の落ちた別表: 規則を外すと結ばず、理由が残る", not r["targets"] and r["reason"] == "題名が目録に無い", r)
SUB2 = SUB + mk("KB-501A.md", ["dummy"])[:0] + [{"id": "KB-501A#3", "fileName": "KB-501A.md", "position": 3, "text": "[KB-501A]\n## 中間\n\n" + "説明の文が続く。" * 12},
                                                 {"id": "KB-501A#4", "fileName": "KB-501A.md", "position": 4, "text": "[KB-501A]\n## 別の表\n\n### 符号 A 予備機\n\n1 点を計測する。"}]
r = pick(run(SUB2), "KB-501A#1", "符号 A")
check("題名の落ちた別表: 下位の指定が離れた 2 か所にあれば結ばない", not r["targets"] and r["reason"], r)

# ───────── 6. その他 ─────────
check("参照の語の有無（パンくずの行は見ない）", R.has_reference_cue("[A > 別表 1 による]\n本文") is False and R.has_reference_cue("[A]\n別表 1 による。") is True)
check("参照の語: 「下図に示す」「次により」のような、番号も名前も無い言い方は数えない", R.has_reference_cue("[A]\n下図に示す点を計測する。次により実施する。") is False
      and R.has_reference_cue("[A]\n下図に示す点を計測し、要領書による。") is True)
info = run(DOT)["chunks"]
check("チャンクごとの検出の数と参照の語の有無", info["AB-QC-101C#3"]["references"] >= 5 and info["AB-QC-101C#3"]["cue"] and info["AB-QC-103A#1"]["references"] == 0)
try:
    run(DOT, disable=["no_such_rule"])
    check("知らない規則の名前は例外", False)
except ValueError:
    check("知らない規則の名前は例外", True)
a = run(REG + HY + DOT + MIX)["references"]
b = run(list(reversed(REG + HY + DOT + MIX)))["references"]
key = lambda r: (r["source"], r["quote"], r["locator"], r["target_doc"], tuple(t["id"] for t in r["targets"]), r["scope"])
check("入力のチャンクの並びを逆にしても、結果は同じ", sorted(map(key, a), key=str) == sorted(map(key, b), key=str), (len(a), len(b)))
check("標準ライブラリだけで動く（src の他のモジュールを読み込まない）", not [m for m in sys.modules if m.startswith("src.") and m != "src.chunk_reference_rules"], [m for m in sys.modules if m.startswith("src.")])

# ───────── 7. 見ていない文書で見つかった型（変換後の形で確かめる。例文は作り物） ─────────
PAD = "補足の説明を続ける。" * 10

# 7-1 他の法令の条は、自分の文書の条ではない。項の印は箇条書きの記号つき（- 2 …）、条の直後に空白の無い見出し（第2条この規則は）
LAW = mk("就業規則.pdf", [
    "# 就業規則\n\n- 第1条 この就業規則(以下「規則」という。)は、労働基準法(以下「労基法」という。)第89条に基づき、就業に関する事項を定める。\n\n- 2 この規則に定めのない事項は、労基法その他の法令の定めによる。" + PAD,
    "第2条この規則は、すべての従業員に適用する。\n\n- 第3条 会社は、労基法第15条に従い、労働条件を明示する。均等 法第9条第2項、第3項にも従う。\n\n- 2 前項の明示は、書面による。\n\n- 3 第1項の書面は、第5条第2項の様式による。" + PAD,
    "- 第4条 解雇は、労働契約法(平成19年法律第128号)第16条による。高年齢者等の雇用の安定等に関する法律第9条を確かめる。同法第10条の2に定める措置を講ずる。"
    "雇止めは、有期労働契約の基準(平成15年厚生労働省告示第357号)第2条による。\n\n- 第5条 届出は、本規則第3条による。第2条の従業員が行う。第9条の定めは置かない。\n\n- 2 様式は別に定める。\n\n- 1 この解説は条文ではない。\n\n- 2 解説の 2 番目。" + PAD,
])
res = run(LAW)
for word, loc in (("労基法", "第15条"), ("法", "第9条第2項"), ("労働契約法", "第16条"), ("高年齢者等の雇用の安定等に関する法律", "第9条"), ("有期労働契約の基準", "第2条")):
    r = next((x for x in res["references"] if x["locator"].replace(" ", "").startswith(loc) and (x["target_document"] or "").endswith(word)), None)
    check("法令の条: %s%s は自分の文書の条へ結ばず、手元に無い文書として記録する" % (word, loc), r is not None and r["scope"] == "external" and not r["targets"] and not r["reason"], r)
r = pick(res, "就業規則#3", "同法")
check("法令の条: 「同法第10条の2」は直前に挙げた法令の条（枝番の後ろに助詞が続いても枝番として読む）", r["scope"] == "external" and "第10条の2" in r["locator"].replace(" ", ""), r)
check("法令の条: 規則のリンクは、自分の文書を名指しした参照（第5条第2項、本規則第3条、第2条）からだけ張られる",
      sorted((x["source"], x["locator"].replace(" ", ""), tuple(ids(x))) for x in res["references"] if x["targets"])
      == [("就業規則#2", "第5条第2項", ("就業規則#3",)), ("就業規則#3", "本規則第3条", ("就業規則#2",)), ("就業規則#3", "第2条", ("就業規則#2",))],
      [(x["source"], x["locator"], ids(x)) for x in res["references"] if x["targets"]])
r = pick(res, "就業規則#2", "前項")
check("項の印: 箇条書きの記号つきの「- 2 前項の…」を読み、前項（第 1 項）は同じチャンクの中", r["level"] == "in_chunk" and not r["reason"], r)
r = pick(res, "就業規則#2", "第5条第2項")
check("項の印: 第5条第2項 → その項の印（- 2 様式は…）のあるチャンク", ids(r) == ["就業規則#3"] and not r["note"], r)
r = pick(res, "就業規則#3", "第2条の従業員")
check("条の見出し: 条の直後に空白の無い「第2条この規則は」も、番号が続いていれば目録に入る", ids(r) == ["就業規則#2"] and res["catalogue"]["就業規則.pdf"]["articles"] == 5, (r, res["catalogue"]))
r = pick(res, "就業規則#3", "第9条の定め")
check("目録に無い条（第9条）は結ばず、理由を付けて残す", not r["targets"] and r["reason"] and r["scope"] == "same_document", r)
D = R.Catalogue(LAW).doc["就業規則.pdf"]
check("項の印: 条のあとで番号が振り直された行（解説の - 1、- 2）は項として採らない", sorted(n for (i, n) in D.paras) == [2, 2, 2, 3], sorted(D.paras))

# 7-2 枝番の条
BR = mk("賃金規程.pdf", ["# 賃金規程\n\n第7条 基本給は、別に定める。\n\n2) 号俸は別表による。" + PAD, "第7条の2 手当は、次のとおりとする。" + PAD,
                         "第8条 第7条の2に定める手当は、毎月支給する。第8条の3の規定は置かない。号俸は第7条の 2) による。(第7条の2)を参照。" + PAD])
res = run(BR)
r = pick(res, "賃金規程#3", "第7条の2に")
check("枝番: 「第7条の2に定める」は第 7 条の 2（第 7 条ではない）", ids(r) == ["賃金規程#2"], r)
r = pick(res, "賃金規程#3", "第8条の3")
check("枝番: 目録に無い枝番の条（第8条の3）は、元の条へ結ばず残す", not r["targets"] and r["reason"], r)
r = [x for x in res["references"] if x["source"] == "賃金規程#3" and "(第7条の2)" in x["quote"].replace(" ", "")]
check("枝番: 括弧書きの終わりの「(第7条の2)」は枝番、「第7条の 2) による」は第 7 条", any(ids(x) == ["賃金規程#2"] for x in r)
      and ids(pick(res, "賃金規程#3", "号俸は第7条")) == ["賃金規程#1"], (r, pick(res, "賃金規程#3", "号俸は第7条")))

# 7-3 条の範囲は、章の見出しや見出し記号つきの行で終わる
RNG = mk("服務規程.md", ["# 服務規程\n\n## 第1章 総則\n\n第1条 目的を定める。" + PAD, "第2条 適用範囲を定める。" + PAD,
                          "## 第2章 服務\n\nこの章は、服務の基本を定める。" + PAD, "第3条 第1条から第2条までの規定を守る。第2条の範囲に限る。" + PAD])
res = run(RNG)
check("条の範囲: 第1条から第2条まで → 2 つの条のチャンクだけ（次の章の前書きを含めない）", ids(pick(res, "服務規程#4", "第1条から")) == ["服務規程#1", "服務規程#2"], pick(res, "服務規程#4", "第1条から"))
check("条の範囲: 第2条 → その条のチャンクだけ", ids(pick(res, "服務規程#4", "第2条の範囲")) == ["服務規程#2"], pick(res, "服務規程#4", "第2条の範囲"))

# 7-4 同じ番号の条が複数ある（例の並記、附則の振り直し、チャンクの重なり）
DUP = mk("規程例.pdf", ["[例1]\n第19条 労働時間は8時間とする。" + PAD, "第20条 休日は、第19条の労働時間に応じて定める。" + PAD, "[例2]\n第19条 労働時間は7時間とする。" + PAD,
                        "第20条 休日は、第19条の労働時間に応じて定める。" + PAD + "\n第21条 休暇の期間は、通常の賃金を支払う。",
                        "第21条 休暇の期間は、通常の賃金を支払う。\n- 2 無給とする場合は別に定める。" + PAD, "附則\n第1条 この規程は、公布の日から施行する。第20条は翌月から、第21条第2項は翌年から適用する。" + PAD])
res = run(DUP)
check("同じ番号の条: 参照元と同じまとまりの条へ結ぶ（例 1 の第20条 → 例 1 の第19条、例 2 の第20条 → 例 2 の第19条）",
      ids(pick(res, "規程例#2", "第19条")) == ["規程例#1"] and ids(pick(res, "規程例#4", "第19条")) == ["規程例#3"], (pick(res, "規程例#2", "第19条"), pick(res, "規程例#4", "第19条")))
check("同じ番号の条: 附則からの第20条は、前にある最も近い条へ", ids(pick(res, "規程例#6", "第20条")) == ["規程例#4"], pick(res, "規程例#6", "第20条"))
check("チャンクの重なり: 末尾に冒頭 1 文だけが入ったチャンクでなく、本文の続く側へ結ぶ（第21条第2項）", ids(pick(res, "規程例#6", "第21条第2項")) == ["規程例#5"], pick(res, "規程例#6", "第21条第2項"))

# 7-5 目次の中の条や番号は、見出しでも参照でもない
TOC = [{"id": "T#1", "fileName": "T規程.pdf", "position": 1, "section": "T規程 > 目 次", "text": "[T規程 > 目 次]\n## 目 次\n\n- 第1章 総則…………10 第 1条(目的) 第 2条(適用範囲)\n第2章 採用\n第 3条(採用手続)"},
       {"id": "T#2", "fileName": "T規程.pdf", "position": 2, "text": "[T規程]\n第1条 目的を定める。" + PAD}, {"id": "T#3", "fileName": "T規程.pdf", "position": 3, "text": "[T規程]\n第2条 第1条による。" + PAD}]
res = run(TOC)
check("目次: 目次のチャンクからは参照を出さず、条は本文のチャンクで引く", not [x for x in res["references"] if x["source"] == "T#1"] and ids(pick(res, "T#3", "第1条")) == ["T#2"], res["references"])

# 7-6 項の中の箇条（上記 3)、N.M)、N.M(k) 項、N. の (k)）
ITEM = mk("KX-100A.pdf", [
    "KX-100 素材品質基準\n1. 適用\nこの基準は素材に適用する。" + PAD + "\n2. 検査項目\n2.1 初品\n(1)材料強度試験\n(2)形状寸法検査\n寸法を測る。" + PAD,
    "3. 試験\n次の場合に行う。\n1)新規の場合\n2)変更の場合\n3)再開の場合" + PAD, "試験は次の項目による。\n1)成分分析\n2)硬さ試験\n3)導電率\n導電率を測る。" + PAD,
    "4. 成績表\n1)成分表\n2)証明書\n3)硬さ成績書\n4)欠陥成績書\n注1. 上記3)、4)については1)の成績表に含めることができる。抜取りは2.1(2)項に従う。3.3) の導電率規格を満足すること。3.2)項による。7 時間は※85日とする。",
])
res = run(ITEM)
r = pick(res, "KX-100A#4", "上記")
check("「上記3)、4)」は同じ項の箇条であり、3 項・4 項へ結ばない", r["level"] == "in_chunk" and not r["targets"] and "3)" in r["locator"], r)
check("「2.1(2)項」: 番号と「項」の間の括弧つきの箇条を読み、その箇条のチャンクへ", ids(pick(res, "KX-100A#4", "2.1(2)")) == ["KX-100A#1"], pick(res, "KX-100A#4", "2.1(2)"))
check("「3.3) の導電率規格」: 同じ番号の箇条が 2 か所にあるとき、直後の語が題名に合う方へ", ids(pick(res, "KX-100A#4", "3.3")) == ["KX-100A#3"], pick(res, "KX-100A#4", "3.3"))
r = pick(res, "KX-100A#4", "3.2")
check("「3.2)項による」: 同じ番号の箇条が 2 か所にあり決められないときは、先頭を採らず、理由を付けて残す", not r["targets"] and "決められない" in r["reason"], r)
check("「※85日」は注の番号ではない", not any(x["form"] == "注" and "85" in x["locator"] for x in res["references"]), [x["locator"] for x in res["references"] if x["form"] == "注"])
SUBI = mk("KY-200B.md", ["# KY-200 油管理基準\n\n## 3. 受入\n\n受入は次による。" + PAD, "### (1) 荷受け\n\n荷受けをする。" + PAD, "### (2) 検査\n\n検査では (1) 外観 (2) 数量 を確かめる。" + PAD,
                         "### (3) 保管\n\n保管する。" + PAD, "### (4) 油面\n\n油面を確かめる。" + PAD,
                         "## 4. 記録\n\n受入は 3. の (4) による。3. の (2) に定める検査を行う。KY-200 の 3.(1) を受けて定める。3. の (9) による。" + PAD])
res = run(SUBI)
check("「3. の (4)」: 項の先頭の 3 チャンクでなく、箇条 (4) の見出しのチャンクへ", ids(pick(res, "KY-200B#6", "3. の (4)")) == ["KY-200B#5"], pick(res, "KY-200B#6", "3. の (4)"))
check("「3. の (2)」: 文中にも (2) があるとき、見出し記号つきの箇条を採る", ids(pick(res, "KY-200B#6", "3. の (2)")) == ["KY-200B#3"], pick(res, "KY-200B#6", "3. の (2)"))
check("「X の 3.(1)」: 番号の直後の括弧つきの箇条", ids(pick(res, "KY-200B#6", "3.(1)")) == ["KY-200B#2"], pick(res, "KY-200B#6", "3.(1)"))
r = pick(res, "KY-200B#6", "3. の (9)")
check("「3. の (9)」: 箇条が見つからなければ項の全体へ結び、その旨を残す", ids(r)[:1] == ["KY-200B#1"] and "項の全体" in r["note"], r)

# 7-7 本文や表の数字を見出しと取り違えない。見出し記号つきの見出しは、題名が数字で始まっても採る
HEAD = mk("KZ-300C.md", ["# KZ-300 確認試験要領\n\n## 1. 適用\n\n1-1 この要領は初号機に適用する。\n\n1-2 初号機とは最初の製品をいう。" + PAD,
                         "## 2. 試験\n\n### 2-1 1 台目の計測\n\n1 台目は、4 点を計測する。\n\n| 径 | 長さ |\n|---|---|\n| 3.2 mm | 155 mm |\n| 4.8 mm | 255 mm |" + PAD,
                         "### 2-2 2 台目の計測\n\n3 台の計測が済むまで、2-1 と同じとする。" + PAD, "## 3 判定\n\n判定は 2-2 による。初号機は 2-1 による。1-2 の初号機に限る。" + PAD])
res = run(HEAD)
check("見出し: 「1 台目は…」「3 台の計測が…」「3.2 mm」を見出しにせず、2-1・2-2・3 が目録に入る", res["catalogue"]["KZ-300C.md"]["sections"] == {"main": 7}, res["catalogue"]["KZ-300C.md"])
check("見出し: 2-2 による・2-1 による・1-2 の → それぞれの見出しのチャンク", ids(pick(res, "KZ-300C#4", "2-2")) == ["KZ-300C#3"] and ids(pick(res, "KZ-300C#4", "2-1")) == ["KZ-300C#2"]
      and ids(pick(res, "KZ-300C#4", "1-2")) == ["KZ-300C#1"], [(x["locator"], ids(x)) for x in res["references"] if x["source"] == "KZ-300C#4"])

# 7-8 関連する文書の一覧と、識別子の読み取り
REL = mk("KW-015.pdf", ["KW-015 溶接補修基準\n1. 検査\n寸法はDIN1685-1の公差等級による。\n2. 関連規程類\n     QC-KAI-036 鋳造品の溶接補修要領\n     KW-005  RU･RH型鋳造品素材品質要求基準\n"
                        "     IHX 4NN372290 (最新版) :RU120A形溶射指示書"]) + mk("KW-005L.pdf", ["KW-005 鋳造品素材品質要求基準\n1. 適用\n鋳造品に適用する。"])
res = run(REL)
r = [x for x in res["references"] if x["source"] == "KW-015#1"]
check("関連規程類の一覧: 識別子の後ろの題名の英字（RU）を飲み込まず、手元の文書へ結ぶ", any(x["target_doc"] == "KW-005L.pdf" and x["level"] == "document" for x in r), r)
check("関連規程類の一覧: 参照の語が無くても、行頭の識別子（目録に無いもの）は手元に無い文書として記録する", {"QC-KAI-036", "IHX 4NN372290"} <= {x["target_document"] for x in r if x["scope"] == "external"}, r)
check("関連規程類の一覧: 題名の中の型式（RU120A）は文書として記録しない", "RU120A" not in {x["target_document"] for x in r}, r)
check("規格の番号: 接頭の語と番号の間に空白が無い形（DIN1685-1）も規格として読む", any((x["target_document"] or "").startswith("DIN1685") and x["scope"] == "external" for x in r), r)
check("一覧の行の文書だけの参照は、場所探し（LLM）に回さない印が付く", all(not x["places"] for x in r if x["level"] == "document"), r)
r = pick(run(REL + mk("KQ-1A.md", ["# KQ-1 基準\n\n判定は KW-005 による。"])), "KQ-1A#1", "KW-005")
check("「X による」の文書だけの参照は、場所探しに回す印が付く", r["level"] == "document" and r["places"] is True, r)

# 7-9 改正履歴の見分け
HIS = mk("KV-400D.pdf", ["KV-400 塗装基準\n1. 適用\n塗装に適用する。" + PAD + "\n2. 塗装\n2.1 下地\n下地を整える。" + PAD,
                         "|配布先|7． 制 |基準来歴 定 02.08.12 新規制定。 1)07.06.14 2.1項に追記する。 表現を見直した。|\n|控|||"]) + mk("QV-10C.md", [
                             "# QV-10 塗装管理規定\n\n## 第 1 条（目的）\n\n目的を定める。\n\n## 別表 2 周期\n\n| 区分 | 日 |\n|---|---|\n| A | 3 |", "## 附則\n\n- C: 2021.04.20 別表 2 を追加した。\n\nこの規定の改正は、別表 2 による。"])
res = run(HIS)
check("改正履歴: 表の欄の先頭の「基準来歴」に続く参照は履歴", pick(res, "KV-400D#2", "2.1")["history"] is True, pick(res, "KV-400D#2", "2.1"))
r = [x for x in res["references"] if x["source"] == "QV-10C#2"]
check("改正履歴: 附則の中の改正の記録の行（記号: 日付 …を追加した）は履歴、同じ附則の本文は履歴でない", sorted(x["history"] for x in r) == [False, True], r)
import time  # noqa: E402
t0 = time.time()
run(mk("KU-1.md", ["## 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31\n\n本文。" + "." * 20000]))
check("数字の並ぶ見出しと長い点線の行でも、すぐに終わる（1 秒未満）", time.time() - t0 < 1.0, time.time() - t0)

# 7-10 文書をまたぐ別名（旧番号・通称）。規則 alias_table
AL = mk("QA-01G.md", ["# 帳票管理規定\n\n| 項目 | 内容 |\n|---|---|\n| 文書番号 | QA-01 |\n| 旧番号 | 品規 7 号 |\n\n## 第 6 条（訂正）\n\n訂正の方法を定める。" + PAD,
                      "## 第 10 条（廃棄）\n\n廃棄の方法を定める。" + PAD]) + mk("KA-P01D.md", [
                          "# 計測 共通基準\n\n| 項目 | 内容 |\n|---|---|\n| 文書番号 | KA-P01 |\n| 通称 | 計測共通 |\n\n## 3. 計測\n\n### 3-3 相互差\n\n相互差を定める。" + PAD]) + mk("KA-A00N.md", [
                              "# 発行目録\n\n（注 1）QA-01 は「帳票規定」と呼ぶことがある。"]) + mk("KA-T02B.md", [
                                  "# KA-T02 試験要領\n\n## 1. 記録\n\n取扱いは、帳票規定の第 6 条による。各点の相互差が計測共通の 3-3 の値を超えた点は用いない。処置は、品規 7 号の第 10 条による。"
                                  "周期は、計測共通に定める。廃棄は、「帳票管理規定」の、廃棄の定めによる。"])
res = run(AL)
check("別名: 通称＋条（帳票規定の第 6 条）→ 「X は「Y」と呼ぶ」で定義された文書の条", ids(pick(res, "KA-T02B#1", "第 6 条")) == ["QA-01G#1"], pick(res, "KA-T02B#1", "第 6 条"))
check("別名: 通称＋項（計測共通の 3-3）→ 表紙の「通称」の欄の文書の項", ids(pick(res, "KA-T02B#1", "3-3")) == ["KA-P01D#1"] and pick(res, "KA-T02B#1", "3-3")["target_document"] == "計測共通", pick(res, "KA-T02B#1", "3-3"))
check("別名: 旧番号＋条（品規 7 号の第 10 条）→ 表紙の「旧番号」の欄の文書の条", ids(pick(res, "KA-T02B#1", "第 10 条")) == ["QA-01G#2"], pick(res, "KA-T02B#1", "第 10 条"))
r = pick(res, "KA-T02B#1", "計測共通に定める")
check("別名: 通称だけ（計測共通に定める）→ 文書へ結ぶ", r["target_doc"] == "KA-P01D.md" and r["level"] == "document", r)
r = pick(res, "KA-T02B#1", "廃棄の定め")
check("名称＋定めの名前: 「△△規定」の、廃棄の定めによる → 題名の一致する条（第 10 条（廃棄））", ids(r) == ["QA-01G#2"] and r["form"] == "文書番号＋見出し名", r)
res = run(AL, disable=["alias_table"])
r = pick(res, "KA-T02B#1", "第 10 条")
check("別名: 規則 alias_table を外すと結ばず、表記（品規 7 号）を付けて残す", not r["targets"] and r["reason"] and r["target_document"] == "品規 7 号", r)
r = pick(res, "KA-T02B#1", "3-3")
check("通称の表記: 文の続きを巻き込まない（「各点の相互差が計測共通」でなく「計測共通」）", r["target_document"] == "計測共通" and not r["targets"], r)

# 7-11 一般の名称での参照と、LLM に回す文の判定
GEN = mk("KS-600A.md", ["# KS-600 運転基準\n\n## 1. 準備\n\n給油量は、機種ごとの組付指示書による。洗浄は次の方法による。検知液により漏れを確かめる。" + PAD,
                        "## 2. 試験片採取要領\n\n試験片を採る。" + PAD, "## 3. 試験\n\n採取は、試験片採取要領による。温度により変化する。" + PAD,
                        "## 4. 提出\n\n詳細は客先の図を参照のこと。" + PAD, "## 5. 改訂履歴\n\n| 版 | 内容 |\n|---|---|\n| A | 購入仕様書の版を参照し見直した |"])
res = run(GEN)
r = pick(res, "KS-600A#1", "組付指示書")
check("一般の名称: 文書の種類の語で終わる名詞＋参照の語（組付指示書による）は、手元に無い文書として記録する", r["scope"] == "external" and r["target_document"] == "組付指示書" and not r["targets"], r)
check("一般の名称: 「次の方法による」「検知液により」は記録しない", [x["target_document"] for x in res["references"] if x["source"] == "KS-600A#1"] == ["組付指示書"], [x for x in res["references"] if x["source"] == "KS-600A#1"])
check("一般の名称: 自分の文書の見出しの題名と一致するとき（試験片採取要領による）は、その見出しへ結ぶ", ids(pick(res, "KS-600A#3", "試験片採取要領")) == ["KS-600A#2"], pick(res, "KS-600A#3", "試験片採取要領"))
info = res["chunks"]
check("LLM に回す文: 手段・原因の「により」だけのチャンクは回さない。規則の参照が無い文に「○○図を参照」があるチャンクは回す。履歴だけのチャンクは回さない",
      (info["KS-600A#1"]["llm"], info["KS-600A#3"]["llm"], info["KS-600A#4"]["llm"], info["KS-600A#5"]["llm"]) == (False, False, True, False), {k: v["llm"] for k, v in info.items()})

# 7-12 前項・次項（項番号の文書）
REL2 = mk("KR-700B.md", ["# KR-700 冷却水管理基準\n\n## 7. 処置\n\na. 管理値を超えた場合は、薬剤を足す。\n\nb. 2 回続けて超えた場合は、次項の入替えを行う。" + PAD, "## 8. 入替え\n\n水を入れ替える。\n\n1)弁を開く。\n\n2)前項の弁を閉じる。" + PAD])
res = run(REL2)
check("次項: 「b. … 次項の入替えを行う」は、箇条でなく次の見出しの項", ids(pick(res, "KR-700B#1", "次項")) == ["KR-700B#2"], pick(res, "KR-700B#1", "次項"))
check("前項: 行頭が番号の箇条である行の「2)前項の…」は、1 つ前の箇条（同じチャンクの中）", pick(res, "KR-700B#2", "前項")["level"] == "in_chunk", pick(res, "KR-700B#2", "前項"))

# 7-13 相対的な語で始まる条の範囲、機器の管理番号
RR = mk("監査規程.md", ["# 監査規程\n\n## 第 3 条（計画）\n\n計画は、次条から第 6 条までの定めによる。気圧計 BR-80 により計測する。" + PAD, "## 第 4 条（実施）\n\n実施する。" + PAD,
                        "## 第 5 条（報告）\n\n報告する。" + PAD, "## 第 6 条（是正）\n\n是正する。" + PAD, "## 第 7 条（記録）\n\n記録する。" + PAD])
res = run(RR)
r = pick(res, "監査規程#1", "次条")
check("範囲: 「次条から第 6 条まで」は、両端だけでなく間の条も結ぶ", ids(r) == ["監査規程#2", "監査規程#3", "監査規程#4"] and "まで" in r["locator"], r)
check("機器の名前に続く符号（気圧計 BR-80 により）は、手元に無い文書として記録しない", not any(x["scope"] == "external" for x in res["references"]), [x for x in res["references"] if x["scope"] == "external"])

# 7-14 法令の条の長い並び、「当然○○法第 N 条」、番号だけの「上記 2 から 4 まで」
LL = mk("育児規程.pdf", ["# 育児規程\n\n第10条 目的を定める。" + PAD, "第16条 申出の手続を定める。" + PAD, "第21条 期間を定める。\n\n- 2 延長は別に定める。" + PAD,
                         "第23条 休業を理由とする解雇は行わない(育児・介護休業法第10条、第16条、第16条の4、第16条の7、第16条の10、第18条の2、第20条の2、第21条第2項、第23条の2)。"
                         "この場合、当然労基法第16条に基づく届出を行う。\n\n- 2 手続は前項による。\n\n- 3 定めは置く。\n\n- 4 上記2から3までの定めに適合させる。" + PAD])
res = run(LL)
check("法令の条の並び: 「○○法第10条、第16条、…、第23条の2」は並びの全体がその法令の条であり、途中から自分の文書の条へ結ばない",
      not [x for x in res["references"] if x["source"] == "育児規程#4" and x["targets"]] and any(x["scope"] == "external" and "第23条の2" in x["locator"].replace(" ", "") for x in res["references"]),
      [(x["locator"], x["scope"], ids(x)) for x in res["references"] if x["source"] == "育児規程#4"])
check("「当然労基法第16条」: 語の先頭が「当」でも、自分の文書（当規程）と取り違えない", any(x["scope"] == "external" and x["locator"] == "第16条" for x in res["references"]),
      [(x["locator"], x["scope"], x["target_document"]) for x in res["references"] if x["source"] == "育児規程#4"])
r = pick(res, "育児規程#4", "上記")
check("「上記2から3まで」: 番号だけの形は、項番号か箇条かを決めず、理由を付けて残す", not r["targets"] and "決められない" in r["reason"], r)

# 7-15 項の印: 表の欄の数量（3 日）を項と取り違えない。重なりで行が途中で切れていても、同じ行と見分ける
PT = mk("休暇規程.pdf", ["# 休暇規程\n\n第23条 休暇を与える。\n\n- 2 前項の規定にかかわらず、次の表のとおりとする。\n\n|週の日数|日数|\n|---|---|\n|4 日|7 日|\n|3 日|5 日|\n|2 日|3 日|" + PAD + "\n\n- 3 休暇は、請求",
                         "- 3 休暇は、請求する時季に取得させる。\n\n- 4 前項の規定は、協定がある場合は適用しない。\n\n- 5 第3項の規定にかかわらず、時季を指定する。" + PAD, "第24条 第23条第3項の休暇は、時間単位で与える。" + PAD])
res = run(PT)
D = R.Catalogue(PT).doc["休暇規程.pdf"]
check("項の印: 表の欄の「3 日」を第 3 項とせず、本文の「- 3 …」「- 4 …」「- 5 …」を項として採る", sorted(n for (i, n) in D.paras) == [2, 3, 4, 5] and [p for p, o in D.paras[(0, 4)]] == [2], dict(D.paras))
check("項の印: チャンクの末尾で切れた行（- 3 休暇は、請求）と、次のチャンクの同じ行を 1 つの項として扱う", [p for p, o in D.paras[(0, 3)]] == [1, 2], D.paras[(0, 3)])
check("項の印: 同じチャンクにある第3項への参照はリンクにせず、他のチャンクからの第23条第3項は、本文の続く側のチャンクへ結ぶ",
      pick(res, "休暇規程#2", "第3項")["level"] == "in_chunk" and ids(pick(res, "休暇規程#3", "第23条第3項")) == ["休暇規程#2"], (pick(res, "休暇規程#2", "第3項"), pick(res, "休暇規程#3", "第23条第3項")))
a = run(LAW + BR + DUP + ITEM + SUBI + HEAD + AL)["references"]
b = run(list(reversed(LAW + BR + DUP + ITEM + SUBI + HEAD + AL)))["references"]
check("この節の例文でも、入力のチャンクの並びを逆にして結果が同じ", sorted(map(key, a), key=str) == sorted(map(key, b), key=str), (len(a), len(b)))

print("%d 件中 %d 件が OK" % (len(results), sum(results)))
print("ALL OK" if all(results) else "SOME FAILED")
sys.exit(0 if all(results) else 1)
