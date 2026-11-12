# 夜間作業 2026-11-09（ユーザー指示「夜の間に全部終わらせといて。gemma4 でグラフ構築も含めて。あと 56 本も試しといて」）

## 範囲
1. EDC の改修: 正規化の大文字小文字・候補数、文書番号＋項番の分断、改行による断片（EDC リポジトリ）
2. 参照追跡の精度向上 M1〜M6（承認済みの方針 GWPP8Ho32YdS11yK9Ke1dS）: section_layer / rerank_retriever
3. 由来情報（適用整理 3）と安定キー（同 4）: backend/src/provenance.py（後処理 attach_provenance）、ingest.py に EDC の初期スキーマ指定
4. graph-evidence（同 5）: 根拠を Neo4j に Evidence ノード＋TOUCHED、失敗も記録、利用者 ID、30 日削除→要約行
5. 本番グラフ（neo4j-kgb、6 文書）を gemma4 × EDC（試運転_検査基準・固定）で作り直す。1 本目は画面から（EDC 設定の通し確認）、残りは ingest.py
6. （取りやめ）標準抽出の比較はしない。ユーザー「EDC のほうが精度いいのはわかってるんだから EDC で作れ」（02:40）
7. HS 56 本を gemma4 × EDC（文書種別は自動判定）で neo4j-kgb4（database4）へ。採点（10 本）・問い 23 本の 4 条件・参照追跡の効果
8. 報告（HTML）・メモ・実験台帳

## 守ること
- GPU コンテナは起動しない（gemma4・ruri・リランカーは常駐を使う）。gemma4 への同時要求は 3 本まで（EDC の並列 2）
- 本番 DB は作り直しの前に APOC で控え。戻し方を README に書く
- 既定の検索は変えない（参照追跡は独立方式のまま）
- 各ファイルの控えは <file>.bak.night1109
- 一時コンテナは --memory を付ける

## 進捗
- [x] A 改修（ワークフロー、11 体・78 分。反証レビュー→手直し済み。親が backend の除外 4 か所・照合の「の」形・試験の期待値を追加）
- [x] B 配備（03:50 EDC 再構築・backend 再起動・graph-evidence 再構築・画面再構築。EVIDENCE_GRAPH_WRITE=true）
- [x] C 本番 6 文書の作り直し（03:55 消去、04:01 画面で VTR-T004C、04:06〜16 ingest.py 5 本、04:20 doc_ref_entities 追加。採点 直接 33・種類一致 32）
- [x] D 標準抽出 6 文書（取りやめ）
- [x] E 56 本（kgb4）: 04:16〜05:33 取り込み 56/56、05:34〜05:43 採点と問いの評価。05:43 backend 再起動→章・節の層と由来情報を作り直し、05:50 参照追跡を全問再実行（X21）
- [x] F 評価と報告: 81 問（X20）・56 本（X21）を内容で判定し直し（判定役 2 体＋裁定）、報告 HTML を公開（URL は下）

## 実行中（04:25、すべて終了）
- E 56 本（bfz464vd9）、X20 81 問 2 条件（blzn8q3it）、章・節の参照 39 本の判定（wu391yb2n）
- 取り込み後に backend 再起動が要る変更: 文書ノードに edc_doctype・edc_schema_mode を記録（main.py ほか .bak.night1109c）。本番 6 文書は手で記録済み

## 終了時の状態（06:00 頃）
- backend は 05:43 に再起動済み（文書ノードへの edc_doctype 記録・同じチャンクだけの節参照を張らない、が有効）
- 本番 neo4j-kgb: gemma4 × EDC の 6 文書。章・節への参照 37 本（X20 最終 正しい 22・広すぎる 8・誤り 7、判定は人の確認前）
- 評価用 neo4j-kgb4: 56 本。評価が終わったので停止（docker start neo4j-kgb4 で戻る）。neo4j-gbdev も停止
- 画面: 案内表示は初回だけ・下部に EDC の文書種別とモード（frontend 作り直し済み、控え .bak.night1109）
- 実験台帳 X20・X21、メモ（project_gb_integration_2026_11_08・project_graph_evidence_dify・project_gb_roadmap_2026_09）更新済み
- 報告 Artifact: https://claude.ai/artifact/MgpbEdvrKexhaKMntkVX8C（06:30 公開）
