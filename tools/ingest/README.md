# 一括取り込み（tools/ingest）

| 作業 | いつ | 守るもの | 手順 |
|---|---|---|---|
| フォルダを取り込む | 数十本の文書をまとめて入れるとき。画面を開いたままにしたくないとき | 既存のグラフは消さない。取り込み済みは飛ばす | 下の 1 |
| 再取り込み | 抽出の設定を変更した後、同じ文書を再度取り込むとき。文書の新しい版（同じファイル名）を取り込むとき | 画面の「ファイルを削除」で事前に削除しなくても、`--force` で上書きできる（Document の状態が Completed でも再抽出する）。抽出の開始時に、前の版のチャンク、その文書のみのエンティティ、規則により生成した章・節と文書層のリレーションを削除する。他の文書と共有するエンティティと、文書番号・型式のエンティティ（Standard・Model）は保持される。削除した件数は run.jsonl の `replaced` に記録される。削除の対象となる件数は `tools/ingest/verify_replace.sh <文書名>` で事前に集計できる | 下の 2 |
| 止まったときの再開 | 常駐の LLM が止まって抽出が続けて失敗したとき | LLM は再起動しない（別の作業が使っていることがある） | 下の 3 |
| 抽出失敗チャンクの再抽出 | 取り込みの出力に「注意：抽出失敗チャンク N 件」が表示された場合。画面のファイル一覧のステータス列に「抽出失敗チャンク N 件」が表示された場合 | 他のチャンクの抽出結果は保持し、記録したチャンクのみを再抽出する。記録したチャンクが再度失敗したのみであれば Completed のままとなる。EDC や埋め込み（ruri）が使用できない場合などは Failed になるが、記録と処理済みの位置は保持されるため、原因を解消した後に「続きから」を再実行する | 下の 4 |

## 1. フォルダを取り込む

```bash
cd ~/ai-stack/llm-graph-builder
python3 tools/ingest/ingest.py ~/docs/RHAT --dry-run      # 対象と飛ばす物を見る
python3 tools/ingest/ingest.py ~/docs/RHAT                # アップロード → 抽出 → 後処理
```

- 対応する拡張子は画面と同じ（pdf, docx, pptx, xls, xlsx, txt, md, html, 画像）。`--recursive` で下の階層も対象にする
- 抽出の設定は画面の既定と同じ（チャンク 512／重なり 50、埋め込みは ruri）。変えるときは `--token-chunk-size` `--chunk-overlap` `--allowed-nodes` `--instructions`
- `--extraction-method edc` でスキーマ統制の抽出（EDC）。アップロードの前に EDC を使用できるかを確認し、使用できない場合はアップロードせずに停止する。旧来の `--model edc` も同じ扱い
- EDC の初期スキーマは取り込みごとに指定できる（`--extraction-method edc` の場合のみ有効。省略時は backend の設定に従う）
  - `--edc-doctype auto|none|<文書種別名>`: auto は文書ごとに先頭から文書種別を判定、none は文書種別を指定しない（語彙を限定せずに抽出する）、それ以外は登録済みの文書種別の名前で固定する。登録済みでない名前を指定した場合は、アップロードの前に停止する（登録済みの一覧は `curl -s :8440/extraction_methods` の edc の doctypes）
  - `--edc-schema-mode fixed|fixed_extended`: fixed は文書種別の語彙に写せないリレーションを落とす、fixed_extended は語彙に写せないリレーションも追加する
  - 指定は /extract の `edc_doctype`・`edc_schema_mode` として送り、run.jsonl と summary.json に記録する。標準の抽出（standard）で指定した場合は注意を出して無視する
  - 例: `python3 tools/ingest/ingest.py ~/docs/RHAT --extraction-method edc --edc-doctype auto --edc-schema-mode fixed`
- 後処理の既定（`--tasks`）は、チャンクの類似リンク・索引・参照リンク・文書層・章・節の層・由来情報と安定キー（attach_provenance）・エンティティの埋め込み。backend は名前の順ではなく決まった順番で流す（attach_provenance は章・節の層の後）
- 記録は `tools/ingest/runs/<日時>/run.jsonl`（1 文書 1 行）と `summary.json`

## 2. 再取り込み

```bash
python3 tools/ingest/ingest.py ~/docs/RHAT --force
```

## 3. 止まったときの再開

連続して 3 回失敗すると停止する（`--max-failures` で変更できる）。常駐の LLM が動作していることを確認した後、同じコマンドを再実行する。取り込み済み（Completed）の文書は対象外となり、それ以外の文書のみを処理する。

## 4. 抽出失敗チャンクの再抽出

抽出失敗チャンクとは、抽出処理（LLM または EDC）が例外で中断したチャンク（出力上限到達など）である。ファイル全体を失敗にしないよう処理は継続され、件数と ID が Document の `skipped_chunks`・`skipped_chunk_ids` に記録される。件数は run.jsonl の `skipped_chunks`、画面のファイル一覧のステータス列、「グラフ設定 > 保守 > 設定値と検証件数」の表に表示される。LLM の出力を解釈できず空の結果になったチャンクは集計しない。

1. 画面のファイル一覧で、その文書の「再処理」ボタンをクリックする
2. 再処理オプションのダイアログで「Start From Last Processed Position」（続きから）を選択して続行し、「グラフを生成」をクリックする。全チャンクの処理を完了した文書（Completed）では、記録したチャンクのみを再抽出し、成功したチャンクは記録から除外される。抽出方式は、その文書を取り込んだ時の方式で再開する（グラフ設定の「取り込み」タブで選択中の抽出方式は使用しない）
3. 画面から再抽出すると、後処理は既定どおり自動で実行される。失敗した場合のみ「グラフ設定 > グラフ整備」で再実行する

- 同じチャンクが再度すべて失敗しても、文書は Completed のまま記録が保持される（出力上限到達は同じ内容で繰り返しやすい。繰り返し残る場合は下の症状と対処を参照する）
- 処理済みの件数が総数に達していない文書（途中で失敗・取り消しになった文書）を続きから再開すると、記録したチャンクに加えて、未処理のチャンクと抽出結果のないチャンクも再抽出する。LLM が停止したまま続きからを選択しても、文書は Failed のままで、記録したチャンクのみで Completed にはならない
- EDC で出力が上限に達した分は例外にならないため記録されない（backend のログの `hit the output limit` で確認できる）

## 症状と対処

| 症状 | 原因 | 対処 |
|---|---|---|
| `backend に接続できない、または Neo4j に接続できない` | backend が止まっている、または backend/.env の接続先が違う | `docker ps` で backend と neo4j-kgb を確認。接続先を変えるなら `--neo4j-uri` など |
| 抽出が `Failed` で続く | 常駐の LLM（gemma4）が止まっている | gpu-admit status で確認。学習中は待つ |
| 1 文書が `Failed` で他は通る | その文書の変換に失敗（文字層なしの PDF など） | `docker logs backend` でその文書名を探す |
| 再取り込みした文書の IS_VERSION_OF・APPLIES_TO・章・節がない | 再取り込みで前の版の規則の層を削除した（後処理で再生成する前） | 後処理 build_document_layer・build_section_layer を実行する（`--tasks` を空にしていなければ自動で実行される） |
| `EDC が現在使用できない: …` で停止する | EDC の依存先（常駐の gemma4・ruri の vLLM）が止まっている | gpu-admit status で確認。LLM の再起動はしない |
| `注意：抽出失敗チャンク N 件` が表示される | そのチャンクで LLM の出力が上限に達した、または LLM・EDC がエラーを返した | 下の 4 で再抽出する。繰り返し残る場合は `docker logs backend` で `Skipping chunk due to` を検索し、チャンクの長さや出力上限を見直す |
| `backend が抽出方式の指定に対応していない` で止まる | backend が抽出方式の分離より前の版のまま（再起動前） | backend の再起動を待つ |

## 設計の要点

- 画面と同じ API（/upload → /extract → /post_processing）を 1 本ずつ呼ぶだけで、backend に手を入れていない
- Neo4j の接続先は backend/.env を読む（backend から見た名前なので `neo4j://database:7687`）
- 元は評価用の `tools/kg/hs/run_hs_method.py`。評価用はグラフを空にしてから入れるが、こちらは消さない

## 段 3 の後処理（文書層・章・節の層、11 月 7 日）

| 作業 | いつ | 守るもの | How |
|---|---|---|---|
| 文書層を作る（B2・B3） | 取り込み（抽出）の後。既定の後処理に含まれるため、ingest.py では自動で実行される。画面では後処理の一覧の「build_document_layer」 | LLM も GPU も使用しない。規則により生成したリレーションは削除して再生成する（冪等）。名寄せの統合前の状態は backend/logs/document_layer/ の台帳に記録される | `curl -X POST :8440/post_processing ... tasks='["build_document_layer"]'`（verify_stage1.sh の参照リンクの呼び方と同じ形） |
| 章・節の層を作る（B4） | 同上。入力は分割時の見出しの経路（Markdown 分割、Chunk.section_path）であるため、字数分割の文書には章・節を生成しない | 規則により生成した Section（method=rule）のみ削除して再生成する。抽出された型「Section」のエンティティは、ラベルを SectionRef に変更して保持する | `tasks='["build_section_layer"]'` |
| 由来情報と安定キーを付ける（attach_provenance、11 月 9 日） | 章・節の層の後。既定の後処理に含まれるため、ingest.py では自動で実行される。画面では後処理の一覧の「由来情報と安定キー」 | LLM も GPU も使用しない。毎回付け直す（冪等）。HAS_ENTITY に言及の文（`mention`・`mention_found`・`mention_source`）、チャンクに安定キー（`stable_key` = 文書名#節#節の中での順位、`doc_ordinal`）と索引 chunk_stable_key を付ける。エンティティ間のリレーションの根拠の文（`source_chunk_id`・`evidence`・`evidence_kind`・`provenance_method`）は、既定の検索の文脈がこの 4 つを除外している場合だけ付ける（既定の検索の結果を変えないため。環境変数 `PROVENANCE_RELATION_PROPERTIES` = auto（既定）/ true / false） | `tasks='["attach_provenance"]'`。書き込まずに集計だけ見るには `docker exec -i -w /code backend python3 -c 'from src.shared.common_fn import create_graph_database_connection as c; from src.entities.user_credential import Neo4jCredentials as N; import os, json; from src.provenance import attach_provenance as a; g=c(N(uri=os.environ["NEO4J_URI"], userName=os.environ["NEO4J_USERNAME"], password=os.environ["NEO4J_PASSWORD"], database=os.environ.get("NEO4J_DATABASE","neo4j"))); print(json.dumps(a(g, dry_run=True)["summary"], ensure_ascii=False, indent=1))'` |
| 合否の集計 | 上の 2 つを実行した後 | 参照のみ | `tools/ingest/verify_stage3.sh`（文書層・章・節の層・副作用）、`docker cp tools/kg/refs/gold_refs.json backend:/tmp/gold_refs.json && docker exec -i -e GOLD_REFS=/tmp/gold_refs.json -w /code backend python3 - < tools/kg/refs/score_doclayer.py`（文書どうしの REFERS_TO を正解 15 組と照合、合格は一致 13 以上・余分 3 以下） |
| 文書番号の形を足す | 新しい系列（HS-VTR 以外）の文書を入れる時 | 規則のコードは触らない | `backend/document_numbers.json` の series に正規表現と正規形を足し、`docker exec -e KG_DUMP=/tmp/graph_dump_linked.json -w /code backend python3 -m pytest tests/test_document_layer_rules.py -q` を通す |

| 症状 | 原因 | 対処 |
|---|---|---|
| 文書どうしの REFERS_TO が 0 | 参照抽出の規則の目録に文書番号がない（ファイル名にも先頭の本文にも番号がない） | ファイル名に文書番号を含めるか、document_numbers.json に系列を追加する |
| Section が 0 | 字数分割で取り込んだ（section_path がない） | PDF_CHUNK_STRATEGY=markdown で再取り込みする（--force） |
| 名寄せ後に検索の結果が変わった | 統合で埋め込みがなくなった | 後処理 materialize_entity_similarities を実行する（既定の順序では文書層の後に実行される） |
| 後処理の応答の error に `attach_provenance failed` | 由来情報の段で例外（他の段は流し終えている） | `docker logs backend` で `attach_provenance failed` を検索する。由来情報が無くても検索は動く。原因を直した後に `tasks='["attach_provenance"]'` で再実行する |
| リレーションに `evidence` が付かない | 既定の検索の文脈が由来のプロパティを除外していない（集計の `relation_properties_reason` に理由が出る） | 既定の検索の文脈を変えないための仕様。付ける場合は、backend/src/shared/constants.py の 2 つの書き出し（VECTOR_GRAPH_SEARCH_QUERY_SUFFIX・LOCAL_COMMUNITY_SEARCH_QUERY_SUFFIX）で 4 つのプロパティ名を除外してから流す |
| `stable_key` が `文書名#-#順位` ばかりになる | 章・節の層が無い（字数分割の文書、または build_section_layer を流していない） | build_section_layer を流してから attach_provenance を流す |
