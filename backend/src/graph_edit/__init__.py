"""グラフの手動修正と修正台帳（docs/graph-edit/DESIGN.md の 2〜4 章）。

- normalize: 表記の正規化（正規化キー）
- validate: 予約の名前、名前の形、引数の検査、失敗のコード（EditError）
- ledger: 修正台帳（__GraphEdit__）の uid、行の読み書き、不変条件の維持、制約と排他のノードの用意
- embedding: エンティティの埋め込みの作り直し
- history: 操作の写し・操作の履歴（__GraphEditOp__）・取り消し
- ops: 編集の操作（M1〜M8、M10）と排他（__GraphLock__）を取った書き込みトランザクション
- read: 読み取りの入口（詳細・検索・近傍・語彙・台帳の一覧）
- replay: 取り込みの保存の直前と直後の再適用（A2）
- lease: 貸し出し（lease）。取り込みの保存・文書の削除・再処理の削除・スキーマの統合・コミュニティの作成・
  取り込み直しの削除・文書層と章節の層の作成が取る
- api: router.py の入口から呼ぶ共通の処理（応答の形と失敗のコード）
- router: score.py に取り込む /graph_edit/* の入口（APIRouter）
"""
