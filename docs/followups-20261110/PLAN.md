# 残作業 11/10（ユーザー「人が必要なとこ以外は進めて」）

人の確認が要るもの（X20・新方式の章・節の参照の判定の人による確認）は除く。本番を変える作業は控えを取ってから。

## 第 1 波（並行）
- A AGPL の部品の撤去: 新方式から pymupdf4llm へ戻す道（失敗時・300 ページ超）を pypdfium2 の文字層だけの簡易変換に置き換え、PyMuPDFLoader を pypdf/pypdfium2 に、pdf_markdown.py・frame_pages.py を外し、requirements から PyMuPDF・pymupdf4llm を外してイメージを作り直す
- B 画面と根拠の表示: 「グラフスキーマ未設定」を EDC のときは EDC の語彙を使う旨に、graph-evidence の参照展開に一覧選択の情報（選び方・選んだ節）
- C 表記ゆれの統合: 空白・ハイフン・大文字小文字・全角半角の違いだけで種類も同じエンティティを規則で 1 つにまとめる後処理（既定の後処理には入れない、手動で実行）。本番の 8 組 18 個に控えを取ってから適用

## 第 2 波
- D 文書台帳: 略称（以下「…」という）・旧名称の取り出し、現行版の印、人が直す正本の表（Postgres）、未解決の一覧と解決率
- E LLM の参照リンクの改善: 番号の無い参照（添付資料の要領で・上記例 など）の結び先を、候補の節の一覧から選ばせる
- F 一覧選択の再評価: 名称・文書だけで参照する問いの多い架空の 38 本（tools/kg/synth）で rerank と summary_list を比べる

## 進捗
- [x] A AGPL 撤去（08:4x 本番 backend を新イメージ 57a56d7634d4 で作り直し。旧イメージ bak-20261110-agpl、控え *.bak.agpl）
- [x] B 画面の文言・graph-evidence の一覧選択の表示（frontend・graph-evidence 入れ替え済み、控え .bak.ui1110・backup/graph-evidence-followup1110）
- [x] C 表記ゆれの統合（後処理 merge_entity_variants、既定外。本番で 3 組を統合 444→441、控え backup/llm-graph-builder-variants1110、台帳 backend/logs/entity_variants/）
- [x] D 文書台帳（名寄せの漏れ修正 Standard 34→31、略称・旧名称・制定日・種類、現行版の印 5、正本の表 pgvector document_registry 15 行、未解決の一覧と解決率 0.981。控え backup/llm-graph-builder-registry1110、*.bak.registry）
- [x] E LLM の参照リンクの改善（CHUNK_REFERENCE_LINK_STYLE=outline 既定、評価 正しい 30→37〜39・誤り 4→0・広すぎる 11→9〜10、本番適用。控え backup/llm-graph-builder-linkfix1110、*.bak.linkfix、評価 tools/kg/parser/eval_prod6/linkfix/）
- [x] 有効化（11/10 昼）: requirements に psycopg[binary]==3.2.3、イメージ作り直し・backend 作り直し、.env に DOCUMENT_REGISTRY_DSN（読み取り専用）、CHUNK_REFERENCE_MODEL を luna→gemma4（一時設定の戻し）、外部規格の接頭に DIN・ASTM・MIL-・AMS。控え *.bak.enable1110
- [x] F 一覧選択の再評価（X24）: 架空 38 本 90 問で差 0（一覧選択が働いたのは 2 問）。既定は rerank のまま
