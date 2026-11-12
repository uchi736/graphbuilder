# -*- coding: utf-8 -*-
"""文書層の規則の単体試験。10 月の予備検証（56 本、tools/kg/hier/kg-final/recount.json）の数と突き合わせる。
実行: cd backend && python3 -m pytest tests/test_document_layer_rules.py -q   （標準ライブラリだけ。書き出しが無い環境では 56 本の試験は飛ばす）"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from src import document_layer_rules as R  # noqa: E402

DUMP = os.environ.get("KG_DUMP") or os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools", "kg", "hs", "runs", "full56_edc_quality", "graph_dump_linked.json")
cfg = R.load_config()


def test_normalize_number():
    assert R.normalize_number("ＨＳ ＶＴＲ－Ｔ００１E", cfg) == ("HS-VTR-T001", "E")
    assert R.normalize_number("HS VTR-T003", cfg) == ("HS-VTR-T003", None)
    assert R.number_of_file("VTR-T004C.pdf", cfg) == ("HS-VTR-T004", "C")
    assert R.number_of_file("VTR-T_発行目録.pdf", cfg) == (None, None)
    assert R.number_of_file("RU-012廃止通知票1.pdf", cfg) == ("HS-RU-012", None)
    # 名寄せの判定は strict: 要求事項の id や文の一部は寄せない
    assert R.normalize_number("Hs-Vtr-T001", cfg, strict=True) == ("HS-VTR-T001", None)
    assert R.normalize_number("Vtr-T005B", cfg, strict=True) == ("HS-VTR-T005", "B")
    assert R.normalize_number("REQ-VTR-T001-1a-1", cfg, strict=True) == (None, None)
    assert R.normalize_number("HS-VTR-T002による", cfg, strict=True) == (None, None)


def test_external():
    assert R.is_external_identifier("QC-KAI-027", cfg)
    assert R.is_external_identifier("JIS B 8340", cfg)
    assert R.external_id("V-00862") == "V00862" and R.external_id("JIS B 8340") == "JIS B 8340"
    assert not R.is_external_identifier("船舶安全法", cfg)
    assert not R.is_external_identifier("法", cfg)


def test_catalog_two_formats():
    ru = "HS-RU-002 / RU・RH・AT14形過給機の タービン軸(心棒)素材品質要求基準 / 59.4.2 / A / 99.1.28 / B01.09.21 / C / 02.04.01 / D / 06.02.07 / E / 06.12.14 / F / 07.04.25 / G / H / J / K"
    rows = R.parse_catalog(ru, cfg)
    assert rows[0]["number"] == "HS-RU-002" and (rows[0]["latest_rev"], rows[0]["latest_date"]) == ("F", "07.04.25")
    vtr = "HS-VTR 過給機 Ａ 98.5.9 Ｂ 02.06.12 Ｃ Ｄ Ｅ    -T001 単独試運転要領及び判定基準 Ｌ Ｍ Ｎ  HS-VTR TPL.TPS形過給機の初号機に関する Ａ 03.06.06 Ｂ 04.03.03 Ｃ Ｄ    -T005 ｺﾝﾌﾟﾚｯｻ単独性能確認試験要領及び判定基準 Ｌ Ｍ"
    rows = R.parse_catalog(vtr, cfg)
    assert [r["number"] for r in rows] == ["HS-VTR-T001", "HS-VTR-T005"]
    assert (rows[1]["latest_rev"], rows[1]["latest_date"]) == ("B", "04.03.03")
    assert "単独試運転要領及び判定基準" in rows[0]["name"]


def test_history_notice_applies_heading():
    h = "## 4. 基準来歴\n0改正:98.10.21 新規調整\n担当 回品管G 赤羽\nA改正:02.06.12  電子文書に移行\nC改正:04.03.03 性能判定方法資料の添付"
    assert [(x["rev"], x["date"]) for x in R.parse_history(h, cfg)] == [("0", "98.10.21"), ("A", "02.06.12"), ("C", "04.03.03")]
    h2 = "７．基準来歴    ０改正：H6. 8. 4新規調整    担当   回技部 福沢    Ａ改正：96. 1.20全面見直し   担当   回技部 大槻"
    assert [(x["rev"], x["date"]) for x in R.parse_history(h2, cfg)] == [("0", "H6.8.4"), ("A", "96.1.20")]
    n = "規定類番号 改 訂 符 号 □ 新制 ■ 改訂 □ 廃止 HS-RU-001 B C 協議・通知票 04 年01 月 07 日"
    assert R.parse_notice(n, cfg) == {"kind": "改訂", "number": "HS-RU-001", "old_rev": "B", "new_rev": "C", "date": "04.01.07"}
    n2 = "02 年09 月 27 日 / 番号 / HS-RU-012-01 □ 新制 □ 改訂 ■ 廃止 HS-RU-012 0 廃止 協議・通知票"
    assert R.parse_notice(n2, cfg)["kind"] == "廃止" and R.parse_notice(n2, cfg)["number"] == "HS-RU-012"
    t = "## 1. 適用\n1-1  RU.RH.RH3.AT形過給機の初号機に関するコンプレッサ単独性能\n## 2. 単独性能"
    assert R.parse_applies(t, cfg) == ["AT", "RH", "RH3", "RU"]
    assert R.heading_form("3-1 .サージ誤差計算 a=1.65") == ("節", "3-1")
    assert R.heading_form("2. 単独性能確認試験要領") == ("章", "2")
    assert R.heading_form("コンプレッサ性能計測要領") == (None, None)
    assert R.is_control_heading("4. 基準来歴", cfg) and not R.is_control_heading("1. 適用", cfg)


@pytest.mark.skipif(not os.path.exists(DUMP), reason="56 本の書き出しが無い")
def test_counts_on_56_docs():
    """recount.json（10 月）の数: is_version_of 48、lists 48、abolishes 1、replaces 29、applies_docs 34。"""
    d = json.load(open(DUMP, encoding="utf-8"))
    files = sorted({c["fileName"] for c in d["chunks"]})
    isv = sum(1 for f in files if R.number_of_file(f, cfg)[0])
    assert isv == 48
    by_file = {}
    for c in d["chunks"]:
        by_file.setdefault(c["fileName"], []).append(c)
    lists = 0
    abol = 0
    repl = 0
    applies_docs = 0
    for f, cs in by_file.items():
        cs.sort(key=lambda c: c["position"])
        if cfg["catalog_file_pattern"] in f:
            for c in cs:
                lists += len(R.parse_catalog(c["text"], cfg))
        notices = [R.parse_notice(c["text"], cfg) for c in cs]
        notices = [n for n in notices if n]
        abol += sum(1 for n in notices if n["kind"] == "廃止")
        repl += sum(1 for n in notices if n["kind"] == "改訂")
        if any(R.parse_applies(c["text"], cfg) for c in cs[:2]):
            applies_docs += 1
    assert lists >= 46, lists          # recount.json: 48（空き番号の行の扱いで ±2）
    assert abol == 1, abol
    assert 25 <= repl <= 35, repl      # recount.json: 29（通知票の形の揺れ）
    assert applies_docs >= 28, applies_docs   # recount.json: 34（recount は見出しのブロックから取るので多い）


def test_numbers_in_and_catalog_file():
    """先頭に番号が複数並ぶ目録は、どの番号の版にもしない（11/7 の取り込み直しで目録が HS-VTR-T001 の版になった不具合）。"""
    from src import document_layer_rules as R
    cfg = R.load_config()
    head = "[VTR-T_発行目録 > 発行目録]\nHS-VTR-T001 過給機単独試運転要領 E 03.06.06\nHS-VTR-T002 VTR4シリーズ形過給機 F 04.03.03"
    assert [n for n, _ in R.numbers_in(head, cfg)] == ["HS-VTR-T001", "HS-VTR-T002"]
    assert R.is_catalog_file("VTR-T_発行目録.pdf", head, cfg)
    assert not R.is_catalog_file("HS-VTR-T001E.pdf", "過給機単独試運転要領", cfg)
    assert R.numbers_in("文書番号 HS-VTR-T003D 改訂", cfg) == [("HS-VTR-T003", "D")]
    assert R.numbers_in("", cfg) == []
