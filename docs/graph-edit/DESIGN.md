# グラフの手動修正と修正台帳　設計メモ（1 期）

方針（了承済みの既定案）: https://claude.ai/artifact/8jpyymYwsmpHsgBiyNEwvG
1 期は本番の作業ツリー（`~/ai-stack/llm-graph-builder`）に反映した（C6、2026-11-08。backend の再起動と画面の配備は、取り込みの終了と利用者の了承の後に行う）。以後の変更は本番の作業ツリーで行う。開発に使ったブランチ `feature/graph-edit`（作業ツリー `~/ai-stack/llm-graph-builder-dev`）は参照用に残し、書き換えない。
本番の他の作業（文書の更新 C8、グラフ設定の組み替えと後処理の順 C7、抽出方式 C5、設定と数 C9）と合わせた所は、2.2・2.3・2.6・3 章・5.1・6 章・7 章に書く。

この文書は、画面（frontend）とバックエンド（backend）を並行して作るための約束事である。番号 M1〜M10・A2・B1〜B9・V0〜V5 は方針のページと同じ。

---

## 1. 試験の場

| 物 | 名前 | 接続 | 備考 |
|---|---|---|---|
| 試験用 Neo4j | コンテナ `neo4j-gbdev`（ネットワーク `llm-graph-builder_net` 上の別名 `database-gbdev`） | `neo4j://database-gbdev:7687`、ホストからは `bolt://127.0.0.1:7691`、ユーザー `neo4j` / パスワード `gbdev-test-only` | neo4j-kgb を 10/31 に APOC で書き出して読み込んだ写し（文書 7、チャンク 184、エンティティ 671）。メモリ上限 6GB |
| 書き出しと読み込み直し | `/home/uchida/ai-stack/gbdev-testbed/`（`export_db.sh`・`restore_gbdev.sh`。10/31 の写しの書き出しは `gbdev_1031`） | | 壊したら `restore_gbdev.sh gbdev_1031` で戻す。本番の写しで試すときは `export_db.sh neo4j-kgb <パスワード> kgb_c6` で書き出し、`restore_gbdev.sh kgb_c6` で読み込む（scratchpad にあった原本と読み込み直しの手順は失われたので作り直した） |
| 本番のコードの試験用バックエンド | コンテナ `backend-c6test`（画面で確かめるときだけ起動） | `http://127.0.0.1:8452`（ホストのみ） | 本番の `backend/` を読み取り専用で `/code` に載せ、`/code/.env` を試験用の接続先に差し替える。環境変数は稼働中の backend の写しで、接続先だけ試験用。gunicorn 2 ワーカー、メモリ上限 4GB。起動の手順は `DEV_UI.md` |
| 開発用バックエンド（参照用） | コンテナ `backend-gbdev` | `http://127.0.0.1:8450`（ホストのみ） | dev の作業ツリーの `backend/` を載せたもの。本番のコードの確かめには使わない |

守ること:
- 稼働中の `backend`・`frontend`・`neo4j-kgb`・`neo4j-kgb2`（database2）に書き込まない。試験の要求に接続情報を付けるときは、必ず `database-gbdev` を指す。
- 試験は本番の backend コンテナの中で流さない（`docker exec` で入らない）。本番の backend を読み取り専用で載せた一時コンテナから流す（7 章）。
- 一時コンテナには必ず `--memory` を付ける（統合メモリで学習や他の常駐を圧迫する）。GPU を使うコンテナは起動しない。
- git の commit / push はしない。

## 2. 用語とデータの形

### 2.1 表記の正規化（正規化キー）

```python
def norm_key(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "")
    s = re.sub(r"\s+", "", s)
    return s.casefold()
```

全角半角・空白・英字の大小だけをそろえる（第 2 段の段 A）。括弧・中黒・ハイフン類・末尾の「材」は落とさない。置き場は `backend/src/graph_edit/normalize.py`。

### 2.2 予約の名前

- 予約ラベル（付け外しの対象にしない。ノードがこれを持つと編集の対象外）: `Chunk`, `__Community__`, `Session`, `Message`, `PROTECTED_MANAGEMENT_LABELS` の全部（`__GraphEdit__`, `__GraphEditOp__`, `__GraphLock__` と、章・節の層の `Section`、graph-evidence の根拠の記録 `Evidence`（11/9）を含む）, `__KGBuilder__`。並びの正本は `validate.RESERVED_LABELS`（`Chunk` … `__GraphLock__`, `Section`, `Evidence`, `__KGBuilder__`）。
- `Document`: 文書ノード（`fileName` を持つ）は編集の対象外。エンティティ（`__Entity__` を持つ）に付いた `Document` は外してよいが、新たに付けることはできない。
- `__Entity__`: 必ず残す。外せない。
- 取り込みが型の名前を付け替える予約の名前（`common_fn.RESERVED_NODE_TYPES` の `document`・`chunk`・`session`・`message`・`__entity__`・`__community__`・`__kgbuilder__`・`section`）は、大小を問わず種類に選べない（`Document` は上の規則による）。付け替えた後の名前（`DocumentRef`・`SectionRef` など）は普通の種類として選べる。
- 予約のリレーション種類（手動で作成・付け替え・削除しない）: `HAS_ENTITY`, `PART_OF`, `NEXT_CHUNK`, `FIRST_CHUNK`, `SIMILAR`, `IN_COMMUNITY`, `PARENT_COMMUNITY`, `NEXT`, `LAST_MESSAGE`, `REFERS_TO`, `ALIAS_OF`, `WHEN`, `MENTIONS`, `HAS_SECTION`, `IN_SECTION`。正本は `validate.RESERVED_RELATIONSHIP_TYPES` で、`graphDB_dataAccess` もここから読む。
- 文書層のリレーション種類（`IS_VERSION_OF`・`LISTS`・`ABOLISHES`・`APPLIES_TO`・`REPLACES`）は予約にしない（`APPLIES_TO` は EDC の述語でもある）。スキーマの統合の付け替えの候補からだけ外す（`constants.LAYER_RELATIONSHIP_TYPES` を `graphDB_dataAccess.get_nodelabels_relationships` が使う。C7）。
- ラベルとリレーション種類の名前の形: `^[A-Za-z_][A-Za-z0-9_]*$`（ラベル）、`^[A-Z][A-Z0-9_]*$`（リレーション種類）。Cypher には識別子として埋め込むので、この検査を通らない値は受け付けない。
- 編集の対象のエンティティ: `__Entity__` を持ち、予約ラベルを持たず、`fileName` を持たないノード。予約ラベルの検査を `fileName` の検査より前に行い、章・節とチャンクには予約の理由を返す。
- 規則で作ったノード（`method = 'rule'` のエンティティ。文書層の文書番号 `Standard`・型式 `Model`）: 名前・種類・プロパティの変更と削除はできない。文書層が id を鍵に作り直すので、改名や削除をしても次の後処理で同じ名前のノードができるためである。統合先（名前と種類はそのまま）・リレーションの端・別物の登録には使える。統合元にはできない（後処理が統合元を作り直し、統合が黙って崩れるため）。判定はラベルではなく `method` で行う（LLM 由来の `Standard`・`Model` は普通のエンティティとして直せる）。規則で作ったリレーション（`method = 'rule'`）は、種類が予約でなくても手で変更・削除できない。
- 編集の対象でないときの失敗（`RESERVED`）には、画面にそのまま出す日本語の理由（`data.reason`）を付ける。

### 2.3 システムのプロパティ（エンティティ）

| プロパティ | 意味 |
|---|---|
| `_v` | 版の番号（整数）。手動の操作と、取り込みの保存（A2）のたびに 1 増やす。手動の操作の外でエンティティの HAS_ENTITY かリレーションが消えるとき（B1・B2・取り込み直しの削除 `document_replace.clear_previous_chunks`）、種類名が書き換わるとき（スキーマの統合）、文書層の作成が規則の `Standard`・`Model` を書くときにも、その分を 1 増やす。取り消しの判定に使う（取り消しが、後で消えたリレーションや旧い種類名を作り直さないため） |
| `_curated` | 手動の操作が一度でも入ったら `true`。B1・B2・取り込み直しの削除で消さない印 |
| `aliases` | 別名の一覧（文字列のリスト）。表示と検索用。正本は修正台帳 |

- 先頭が `_` のプロパティは画面で通常のプロパティとして表示しない（詳細欄の「システム」の折りたたみに出す）。
- リレーションには記録用のプロパティを付けない（検索で LLM に渡す行に全プロパティが出るため）。

### 2.4 修正台帳（管理用ノード）

ラベル `__GraphEdit__`。一意制約 `CREATE CONSTRAINT graph_edit_uid IF NOT EXISTS FOR (g:__GraphEdit__) REQUIRE g.uid IS UNIQUE`。エンティティともリレーションともつながない。

| kind | uid | プロパティ | 意味 |
|---|---|---|---|
| `alias` | `alias:` + norm_key(alias) | `alias`, `target`（代表のエンティティの id） | 別名を代表に読み替える |
| `tombstone_node` | `tnode:` + norm_key(name) | `name` | この名前のエンティティを作らない |
| `tombstone_rel` | `trel:` + norm_key(src) + `\|` + TYPE + `\|` + norm_key(tgt) | `src`, `type`, `tgt` | このリレーションを作らない |
| `type_override` | `type:` + entity_id | `entity`, `labels`（型ラベルのリスト） | エンティティの種類を固定する |
| `curated` | `cur:` + entity_id | `entity`, `keys`（リスト）, `values_json` | 手動で修正したプロパティ。抽出に上書きさせない |
| `manual_rel` | `mrel:` + src_id + `\|` + TYPE + `\|` + tgt_id | `src`, `type`, `tgt`, `props_json` | 手動で追加したリレーション。保存の後に無ければ作り直す |
| `distinct` | `dist:` + 2 つの norm_key を辞書順に `\|` で連結 | `a`, `b` | 同じでないと人が判断した組 |

共通のプロパティ: `kind`, `uid`, `created_at`（ISO 文字列）, `op_id`（作った操作）。

不変条件（編集の API が同じトランザクションの中で守る）:
1. 別名と同じ id のエンティティは存在しない。エンティティをある名前に改名・統合で残すときは、その名前の `alias` を消す。
2. エンティティの改名（old→new）では、`target=old` の `alias` を `new` に付け替え、`alias old→new` を追加する。`type_override`・`curated`・`manual_rel`・`tombstone_rel` の中の old も new に書き換える（uid も作り直す）。
3. 統合（sources→target）では、各 source の id について 2 と同じ書き換えをし、`alias source→target` を追加する。
4. エンティティの削除では、`tombstone_node(id)` を追加し（抑止ありのとき）、`target=id` の `alias` をすべて `tombstone_node(alias)` に置き換える。`type_override`・`curated` は消し、その id を端点に持つ `manual_rel` は消す（写しは操作の履歴に残る）。
5. 別名（`alias`）と削除の記録（`tombstone_node`）は正規化キーで抽出の名前に当たる。書く名前（改名の旧い名前、統合で使われなくなる名前、抑止ありの削除の名前と別名）と正規化キーが同じ、操作の対象以外の生きたエンティティ（例「N=1台目」に対する「n=1台目」）があれば、書かずに `CONFLICT` で断る（`data.conflicts` にそのエンティティ、`data.reason` に日本語の理由）。書くと、そのエンティティの今後の抽出を奪う（読み替える・落とす）ため。同じものなら一緒に統合（削除なら一緒に削除）させる。抑止なしの削除は記録を書かないので断らない。
6. 改名・統合で残す名前が、操作の対象以外の生きたエンティティの別名（正規化キーが同じ `alias` の行）なら `CONFLICT`（`data.alias_of` に別名の持ち主）。黙って別名を付け替えない。
7. 2・3 の `tombstone_rel` と `distinct` の名前の書き換えは、正規化キーではなく元の id の完全一致で照合する（正規化キーが同じ別のエンティティの行を巻き込まない）。
8. `ledger_delete` で `alias` の行を消すときは、代表のエンティティの写しを取り、`aliases` を台帳にそろえ直し、`_v` を 1 増やす（取り消しで `aliases` も戻る）。

### 2.5 操作の履歴

ラベル `__GraphEditOp__`。一意制約 `op_id`。プロパティ: `op_id`（uuid4 文字列）, `seq`（整数。`__GraphLock__` の `seq` を増やして採番）, `op`（`rename` など）, `user`, `at`, `summary`（日本語 1 行）, `payload_json`（要求の引数）, `snapshot_json`（操作前の写し）, `ledger_before_json`, `ledger_after_json`（触れた台帳の行の前後）, `versions_after_json`（{entity_id: _v}）, `undone`（bool）, `undo_of`（取り消しの操作なら元の op_id）。

写し（snapshot）の形: 触れたエンティティごとに `{id, labels, props(embedding を除く), rels: [{dir:"out"|"in", type, other_id, other_is_chunk:bool, props}], chunk_ids: [HAS_ENTITY の元のチャンクの id]}`。相手は名前（エンティティの id）かチャンクの id で持つ（要素 ID は統合や削除で変わるため使わない）。

### 2.6 排他

ノード `(:__GraphLock__ {name:'graph'})` を 1 つ置く（無ければ作る）。
- 編集の操作は 1 つの書き込みトランザクションで行い、最初に `MERGE (l:__GraphLock__ {name:'graph'}) SET l.seq = coalesce(l.seq,0)+1 RETURN l.seq, l.lease_until` を実行する。これでノードの書き込み鍵がトランザクションの間保たれ、編集どうしが直列になる。`lease_until > timestamp()` なら取り込みの最中なので、`BUSY` で断る。
- 複数の自動コミットに分かれてグラフを書き換える処理は、貸し出し（lease）を使う。置き場は `backend/src/graph_edit/lease.py`（`replay.py` からも使える）。1 文で `MATCH (l:__GraphLock__ {name:'graph'}) WHERE coalesce(l.lease_until,0) < timestamp() SET l.lease_owner=$owner, l.lease_until=timestamp()+120000 RETURN l` を実行し、取れなければ 1 秒おきに待つ。待つ上限は `LEASE_MAX_WAIT_SECONDS`（3600 秒。60 回で諦めて失敗させない）。終わったら `SET l.lease_until = 0`。
- 持っている間は、別のスレッドが 30 秒おきに期限を延ばす（`hold_lease` / `start_heartbeat`。1 つの問い合わせが 120 秒を超えても切れない。プロセスが落ちれば延長が止まり 120 秒で切れる。延長し続ける上限は 4 時間）。取り込みの保存では、さらに各段（再適用・保存・HAS_ENTITY）の区切りでも延ばす。
- 貸し出しを取る処理（owner の頭）:

| 処理 | owner | 取る範囲 |
|---|---|---|
| 取り込みの保存（A2、`main.processing_chunks`） | `extract:` | 台帳の読み込みから保存の後の処理まで |
| 取り込み直しの削除（同じ名前のファイルでの抽出し直し。`document_replace.clear_previous_chunks` を `main.processing_source` が `run_with_lease` で呼ぶ） | `reingest:` | 規則の層（節と文書層のエッジ）の削除、その文書のチャンクからだけ指されるエンティティの削除と残すエンティティの `_v`、その文書のチャンクの削除 |
| 文書の削除（B2、`graphDB_dataAccess.delete_file_from_graph`） | `delete:` | 規則の層の削除（`document_replace.delete_rule_layer`）、削除の問い合わせ、空のコミュニティの掃除 |
| 再処理の削除（B1、`main.set_status_retry`） | `reprocess:` | `QUERY_TO_DELETE_EXISTING_ENTITIES` |
| スキーマの統合（`post_processing.graph_schema_consolidation`） | `schema_consolidation` | ラベル・種類名の書き換えと台帳の書き換え。LLM の呼び出しの間は取らない |
| 文書層の作成（`/post_processing` の `build_document_layer`。`score.py` が `run_with_lease` で呼ぶ） | `document_layer` | 文書層の作成の全体（`Standard`・`Model` の書き込みと `_v`、名寄せの物理統合、文書層のエッジ、文書カード） |
| 章・節の層の作成（`/post_processing` の `build_section_layer`。同上） | `section_layer` | 章・節の層の作成の全体 |
| コミュニティの作成（`communities.create_communities`） | `communities` | コミュニティの消去、投影と Leiden の書き込み（`e.communities`）、`IN_COMMUNITY`・順位・重みの作成。LLM の要約と埋め込みの段では取らない |

- `run_with_lease(graph, owner, func, *args)`（`lease.py`）は、関数を貸し出しを持ったまま動かす包みである（関数が失敗しても貸し出しは返す）。取り込み直しの削除と、文書層・章節の層の作成は、これを `asyncio.to_thread` で別のスレッドから呼び、貸し出しを待つ間もイベントループを止めない。
- `document_replace.py` は `graph_edit` を import しない。貸し出しは呼び出し側（`main.processing_source`）で取る（文書の更新の試験 `test_document_replace.py` が `common_fn` を最小の代わりに差し替えて読み込むため）。
- 参照リンク（`link_chunk_references`）はチャンクどうしのリレーションだけを書き、エンティティに触れないので取らない。スキーマの統合は上の表のとおり自分で取る。

## 3. 編集の API（バックエンド）

置き場: パッケージ `backend/src/graph_edit/`（`normalize.py`, `ledger.py`, `ops.py`, `replay.py`, `lease.py`, `embedding.py`, `validate.py`, `history.py`, `read.py`, `api.py`, `router.py`）。17 の入口は `router.py`（FastAPI の `APIRouter`）に置き、`score.py` には import の 1 行と `app.include_router(graph_edit_router)` の 1 行だけを足す（`/health`・`/extraction_methods`・設定と数の入口の後）。既存の 3 つの入口（孤立ノードの削除・重複の一覧・重複の統合）と埋め込みモデルの変更は、経路の照合の都合で上書きできないので、`score.py` のその場を小さく直す。

共通の約束:
- すべて `POST`、`Form` の引数と `credentials: Neo4jCredentials = Depends(get_neo4j_credentials)`。応答は `create_api_response('Success'|'Failed', data=..., message=..., error=...)`（HTTP は常に 200）。
- JSON の引数は文字列で受けて `json.loads` する。
- 失敗の `error` は英語のコード（下の表）で始め、`: ` の後に詳細を付ける。画面はコードを日本語に写す。

| コード | 意味 |
|---|---|
| `NOT_FOUND` | 要素 ID のノードかリレーションが無い |
| `STALE` | 要素 ID はあるが、期待する名前（`expected_id`）や版（`expected_v`）と違う。表示を取り直す |
| `CONFLICT` | 改名先と同じ名前か、正規化すると同じになる名前のエンティティがある。改名の旧い名前・統合で使われなくなる名前・抑止ありの削除の名前と正規化すると同じ、別のエンティティがある（2.4 の 5）。残す名前が他のエンティティの別名（2.4 の 6）。`data.conflicts` に候補、後の 2 つは `data.reason` に日本語の理由 |
| `RESERVED` | 予約のラベル・種類・ノード、規則で作ったノードの変更と削除、規則で作ったリレーション。`data.reason` に日本語の理由 |
| `INVALID` | 名前の形が不正、引数の不足など |
| `BUSY` | 取り込みの保存の最中 |
| `NOT_UNDOABLE` | 取り消しの条件を満たさない（後で同じエンティティが変わった、など）。`data.reason` に理由 |

ノードを返すときの形は `graph_query.process_node` と同じ（`element_id`, `labels`（__Entity__ を除く）, `properties`（embedding/text/summary を除く））。リレーションは `{element_id, type, start_node_element_id, end_node_element_id, properties}`。

| 番号 | 入口 | 引数 | 処理 | 返す data |
|---|---|---|---|---|
| — | `/graph_edit/node_detail` | `element_id` | 全ラベル、全プロパティ、隣のリレーション（プロパティ付き、100 本まで）、HAS_ENTITY の元のチャンク数と文書名、台帳の行（別名・種類の固定・修正した項目） | `{node, relationships, neighbors, chunk_count, documents, ledger:{aliases, type_override, curated_keys}, editable:bool, merge_target_only:bool, reason}`。`merge_target_only` は規則で作ったノードのときだけ `true`（統合先・リレーションの端・別物の登録にだけ使える。画面の統合のダイアログが使う） |
| — | `/graph_edit/search_entities` | `q`, `limit`(既定 20) | `id` の部分一致と正規化キーの一致。編集の対象のエンティティだけ | `[{node, chunk_count, documents}]` |
| — | `/graph_edit/neighborhood` | `element_id` | そのエンティティと 1 ホップ先（チャンク・文書を除く） | `{nodes, relationships}`（`graph_query` と同じ形） |
| — | `/graph_edit/vocabulary` | なし | 今のグラフの型ラベル（予約を除く）、リレーション種類（予約を除く）、EDC の語彙（`/edc_schemas` の CSV の型名と述語名） | `{labels, relationship_types, edc_labels, edc_relationship_types, reserved_labels, reserved_relationship_types}` |
| M1 | `/graph_edit/rename` | `element_id`, `expected_id`, `new_id`, `user` | 2.4 の不変条件 1・2、埋め込みの作り直し、`_v`・`_curated`、件数 | `{node, op_id}` |
| M2 | `/graph_edit/set_labels` | `element_id`, `expected_id`, `labels`(JSON) | 型ラベルを置き換える（__Entity__ は残す）。`type_override` | `{node, op_id}` |
| M3 | `/graph_edit/update_props` | `element_id`, `expected_id`, `props`(JSON。値 null は削除) | 値は文字列・数値・真偽のみ。名前は文字か `_` で始まり文字・数字・`_` だけ（日本語の文字は可。空白・記号は不可）、100 文字まで。`id`・`embedding`・`aliases`・`fileName`・`text`・`summary`（画面で非表示の項目）・先頭 `_` は不可。`curated`。description が変われば埋め込みを作り直す | `{node, op_id}` |
| M4 | `/graph_edit/merge` | `target_element_id`, `target_expected_id`, `sources`(JSON `[{element_id, expected_id}]`), `keep_id`, `labels`(JSON), `description_mode`(`target`/`concat`/`custom`), `description`, `user` | 写しを取り、`apoc.refactor.mergeNodes([target]+sources, {properties:'discard', mergeRels:true, produceSelfRel:false, preserveExistingSelfRels:false})`。残ったノードの自分自身へのリレーションを消す。`keep_id` が target の id と違えば統合の後に改名する。ラベル、説明、`aliases` を設定し、2.4 の不変条件 3。埋め込みの作り直し、件数。規則で作ったノードは統合先にだけでき、`keep_id` と種類はそのノードのままに限る（違えば `RESERVED`）。統合元に規則で作ったノードがあれば `RESERVED`。規則で作ったノードの `aliases` は、文書層の値を残して台帳の別名を足す | `{node, op_id, merged_ids}` |
| M5 | `/graph_edit/delete_nodes` | `nodes`(JSON `[{element_id, expected_id}]`), `suppress`(既定 true), `user` | 写しを取り、DETACH DELETE。2.4 の不変条件 4（`suppress=false` なら tombstone は作らない）。件数 | `{deleted_ids, op_id}` |
| M6 | `/graph_edit/create_relationship` | `source_element_id`, `source_expected_id`, `target_element_id`, `target_expected_id`, `type`, `props`(JSON), `user` | `apoc.merge.relationship(a, type, {}, props, b)`。同じ組の `tombstone_rel` があれば消す。`manual_rel` を追加。両端の `_v`・`_curated` | `{relationship, op_id}` |
| M7 | `/graph_edit/delete_relationship` | `element_id`, `expected`(JSON `{src, type, tgt}`), `suppress`(既定 true), `user` | 写しを取り削除。`tombstone_rel`。同じ組の `manual_rel` は消す | `{op_id}` |
| M8 | `/graph_edit/update_relationship` | `element_id`, `expected`(JSON), `new_type`（省略可）, `reverse`(bool), `props`(JSON、省略可), `user` | 種類か向きが変わるときは、新しいリレーションをプロパティを写して作り旧いものを消す（要素 ID が変わる）。台帳には旧い組の `tombstone_rel` と新しい組の `manual_rel`。プロパティだけのときはその場で SET | `{relationship, op_id}` |
| M9 | `/graph_edit/history` | `limit`(既定 50), `before_seq` | 新しい順。各行に `undoable` と理由 | `[{op_id, seq, op, user, at, summary, undone, undoable, reason}]` |
| M9 | `/graph_edit/undo` | `op_id`, `user` | その操作が触れたエンティティの今の `_v` が `versions_after` と同じで、後の操作に取り消されていないときだけ、写しと台帳を元に戻す。戻した名前（正規化キー）が、他の生きたエンティティの別名か削除の記録に当たるときは戻さない（`NOT_UNDOABLE`）。戻したエンティティは `_v` を 1 増やす。取り消しも操作の履歴に残す | `{op_id, restored_ids}` |
| M10 | `/graph_edit/ledger` | `kind`（省略で全部）, `limit`, `offset` | 台帳の行 | `[{uid, kind, ...}]` |
| M10 | `/graph_edit/ledger_delete` | `uid`, `user` | 台帳の 1 行を消す（例: 削除の記録を取り消して、再び抽出されるようにする）。`alias` の行なら 2.4 の 8 | `{uid, op_id}` |
| M10 | `/graph_edit/mark_distinct` | `a_element_id`, `b_element_id`, `user` | `distinct` を追加 | `{uid, op_id}` |

既存の入口の置き換え（M10）:
- `/merge_duplicate_nodes`: 各行を `merge`（target=first、keep_id=first の id、labels=first のラベル、description_mode=`target`）で処理する。応答の `data` の形は今と同じ（`[{totalMerged}]`）。行が重なる（A←B、B←C）と前の行の統合で first か source が消えるので、そういう行と、統合が断られた行（`CONFLICT` など）は飛ばして続け、応答の最上位の `skipped`（`[{row, first_element_id, reason, error}]`）と `message` に添える。source の一部だけが消えた行も、残りを統合せずに飛ばす（画面で選んだ組と違う統合にしない）。`BUSY` のときだけ止める。
- `/delete_unconnected_nodes`: `delete_nodes`（`suppress=false`）で処理する。予約ラベルのノード・文書・`__Entity__` の無いノード・規則で作ったノードは消さず、`data.skipped` に理由と一緒に返す。
- `/get_duplicate_nodes`: `distinct` に当たる組を候補から除く。

埋め込みの作り直し: 文は `id + " " + coalesce(description, "")`（`post_processing.update_embeddings` と同じ）。`common_fn.load_embedding_model(EMBEDDING_PROVIDER, EMBEDDING_MODEL)`（環境変数）で埋め込み、次元が 768（索引の次元）でなければ書かない。トランザクションの前に計算し、中で `db.create.setNodeVectorProperty` で書く。失敗したら `embedding` を消す（null）。

件数の更新: 触れたエンティティを HAS_ENTITY で指すチャンクの文書について `graphDBdataAccess.update_node_relationship_count(file_name)` を呼ぶ（トランザクションの後）。

## 4. 保存の直前と直後の再適用（A2）

差し込む所: `backend/src/main.py` の `processing_chunks` の中、`handle_backticks_nodes_relationship_id_type` の直後と、`merge_relationship_between_chunk_and_entites` の直後。標準の抽出と EDC の抽出はどちらもここを通る。

```python
lease = acquire_lease(graph, owner=f"extract:{file_name}")   # 取れるまで待つ（2.6）
heartbeat = start_heartbeat(graph, lease)                    # 持っている間、30 秒おきに延長
ledger = load_ledger(graph)                      # 台帳の全行（数千行まで想定）
canon = lookup_canonical_labels(graph, ids)      # 読み替え先の型ラベル
cleaned_graph_documents, stats = apply_ledger_before_save(cleaned_graph_documents, ledger, canon)
renew_lease(graph, lease)
save_graphDocuments_in_neo4j(...)
renew_lease(graph, lease)
merge_relationship_between_chunk_and_entites(...)
renew_lease(graph, lease)
post_save_ledger(graph, cleaned_graph_documents, ledger, stats)  # 手動のリレーションの作り直し、_v の加算
heartbeat.stop(); release_lease(graph, lease)
```

`apply_ledger_before_save` の順序（純粋な関数。Neo4j を呼ばない）:
1. 各ノードの名前を `map_id(name)` で読み替える。`k = norm_key(name)`。`k` が `tombstone_node` にあれば落とす。`alias` にあれば代表の id にする。読み替えた後の id に `type_override` があれば、`node.type` をその先頭のラベルにする。読み替えたノードは、代表の型ラベル（`canon`）の先頭にそろえる。
2. 各リレーションの `source` と `target`（標準の抽出では、ノードの一覧とは別のオブジェクト）にも同じ読み替えと型の書き換えを当てる。どちらかの端が落ちたらリレーションも落とす。`(norm_key(src), TYPE, norm_key(tgt))` が `tombstone_rel` にあれば落とす。読み替えで始点と終点が同じになったら落とす。
3. `curated` にあるエンティティのノードから、その項目のプロパティを外す。
4. 同じ文書（GraphDocument）の中で、読み替えで同じ id になったノードは 1 つにまとめる。
5. 統計を返す: `{aliased, dropped_nodes, dropped_rels, protected_props, type_overridden}`。

`post_save_ledger`:
- このバッチのエンティティの `_v` を 1 増やす。
- `manual_rel` のうち、端点がこのバッチのエンティティに含まれるもので、今グラフに無いものを作り直す。

B8: `merge_relationship_between_chunk_and_entites` の HAS_ENTITY の作成は、`apoc.merge.node([type],{id})` をやめ、`MATCH (n:__Entity__ {id: data.node_id})` で引く（無ければ張らない）。

## 5. 前提の不具合の修正（工程 S1）

| 番号 | 置き場 | 修正 |
|---|---|---|
| B1 | `shared/constants.py` の `QUERY_TO_DELETE_EXISTING_ENTITIES` | `(e)<-[:HAS_ENTITY]-(:Chunk)-[:PART_OF]->(d2:Document) WHERE d2 <> d` が無いエンティティだけを消す。`e._curated = true` と、規則が作った共有のエンティティ（`method = 'rule'` のもの、文書から規則のエッジ `r.method = 'rule'` で指されるもの。C8）は消さない。消すエンティティの隣の残るエンティティは `_v` を 1 増やす。貸し出しを取る（2.6） |
| B2 | `graphDB_dataAccess.delete_file_from_graph` | 下の「B2 の削除の手順」。エンティティも消す場合・消さない場合の両方 |
| B3 | 同じ名前の再アップロード（本家では追記になる） | 本番では文書の更新（C8）の `document_replace.clear_previous_chunks` が、抽出の始めに前の版を消す。5.1 の 1 |
| B4 | `post_processing.create_vector_index` / `create_fulltext_index` | DROP をやめ、`IF NOT EXISTS` で作る。次元が違うときは既存の `/drop_create_vector_index` を使う（変更しない） |
| B5 | `QA_integration.initialize_neo4j_vector`、`rerank_retriever.build_candidate_db` | `Neo4jVector.from_existing_graph` をやめ、既存の索引を開くだけの作り方（`Neo4jVector.from_existing_index`、または同等の引数でのコンストラクタ）にする。取り出すチャンクが変わらないことを V5 で確かめる |
| B7 | `frontend/src/utils/Constants.ts` の `nvlOptions` | `disableTelemetry: true` |
| B8 | `make_relationships.merge_relationship_between_chunk_and_entites` | 4 章のとおり |
| B9 | `shared/constants.py` の `QUERY_TO_GET_LAST_PROCESSED_CHUNK_POSITION` | `MATCH (d)<-[:PART_OF]-(c:Chunk) WHERE c.embedding IS NULL` で、その文書のチャンクに絞る |

B2 の削除の手順（文書の集合 D）:
0. 貸し出しの中で、最初に規則の層（その文書の規則の節 `Section {method:'rule'}` と、文書層のエッジ）を `document_replace.delete_rule_layer` で消す（C8）。
1. 消すチャンク: D のどれかに PART_OF し、D 以外の文書に PART_OF しないチャンク。
2. 消すエンティティ（エンティティも消す場合だけ）: 消すチャンクから HAS_ENTITY で指され、残るチャンクからは指されず、`_curated` でなく、規則が作ったもの（`method = 'rule'`、他の文書から規則のエッジで指されるもの）でないもの。
3. 消すリレーション（エンティティも消す場合だけ）: 両端が `__Entity__` で残るエンティティ、種類が予約でない、`manual_rel` に無い、消すチャンクの中に両端を一緒に指すチャンクがあり、残るチャンクの中には無いもの。端が残るかは 2 と同じ条件で判定する（規則が作ったものも残るエンティティに入るので、端が規則で残るエンティティでも、出どころの消えたリレーションは消える）。
4. 3→2→1 の順に消し、最後に文書ノードを消す。共有のチャンクは文書との PART_OF だけが消える。
4a. 残すエンティティのうち HAS_ENTITY かリレーションを失うもの（消すチャンクから指されるもの、消すエンティティの隣のもの。エンティティを消さない場合は消すチャンクから指されるもの）の `_v` を 1 増やす。全体を貸し出しの中で行う（2.6）。
5. 原本のファイルの削除と、空になったコミュニティの掃除は今のまま。
6. 取り残しの掃除（C8 の `sweep_deleted_documents`）は呼ばない。この手順は PART_OF の無いチャンクも文書ノードも残さないためである（関数は一度きりの掃除と C8 の試験のために残す）。抽出された型 `Section` のエンティティ（`__Entity__:Section`）は普通のエンティティとして 2 の判定に従う。

孤立ノードの一覧（`graphDB_dataAccess.list_unconnected_nodes`）: 一覧と件数の両方から `method = 'rule'` のノードを外す（文書・チャンクにだけつながるので孤立に数えられ、消すと後処理まで層が欠ける）。`Section` は C7 で `PROTECTED_MANAGEMENT_LABELS` に入るので、ラベルでも外れる。

dev の作業ツリーにある `edc_extraction` の自己参照の拡張（「この規定」などを自己参照に加えるもの）は、抽出の結果が変わり評価の再現性に響くので、1 期の反映に含めない。

スキーマの統合（`post_processing.graph_schema_consolidation`）: LLM の応答の読み替えのうち、元の名前が今のグラフにあり、新しい名前が予約でなく識別子に埋め込めるものだけを使う。書き換えは貸し出しの中で行い、リレーションの種類は `apoc.refactor.setType` で替える（プロパティを残す）。書き換えたエンティティ（ラベルを替えたもの、種類を替えたリレーションの両端）の `_v` を 1 増やし、台帳の `type_override` の `labels`、`manual_rel`・`tombstone_rel` の `type`（uid も作り直す）を同じ読み替えで書き換える（`ledger.rename_schema_types_in_ledger`）。

### 5.1 本番への反映で合わせたこと（C6、2026-11-08）

共有のファイルは、基の版（`git show HEAD:<path>`）・本番・dev の三方向で写しの上で合成し、構文と衝突の印を確かめてから置き換えた。衝突は `graphDB_dataAccess.py`（`delete_file_from_graph` の 2 か所）、`shared/constants.py`（先頭の管理用ラベルと `QUERY_TO_DELETE_EXISTING_ENTITIES` の 3 か所）、`GraphViewModal.tsx`（2 か所）だけで、次のとおり解いた。

1. 取り込み直し（同じ名前のファイルでの抽出し直し）は、本番の `backend/src/document_replace.py`（C8）の `clear_previous_chunks` で行う。消す条件は C8 の判定（他の文書と共有のもの・規則が作ったもの・`_curated`・他の文書から規則のエッジで指されるものを残す）に、`_v` の上げ方（残すエンティティのうち HAS_ENTITY を失うものと、消すエンティティの隣のもの）を足したもの。貸し出し `reingest:<文書名>` は、`main.processing_source` の呼び出し側で `asyncio.to_thread(run_with_lease, …)` によって取る。dev の計画にあった `main.clear_previous_chunks` の置き換えと、`get_chunkId_chunkDoc_list` の別スレッド化は行わない（関数が C8 で移り、チャンクの一覧づくりは貸し出しを待たなくなったため）。
2. 文書の削除は B2 に、C8 の `delete_rule_layer`（貸し出しの中、削除の前）と規則のエンティティを残す条件（`method = 'rule'`、規則のエッジで指される）を足したもの。残す条件は、エンティティを残す判定と、消すリレーションの端が残るかの判定の 2 か所に同じものを入れる。C8 の取り残しの掃除（sweep）と、章・節のノードを消す追加の 1 文は入れない（抽出された `__Entity__:Section` まで消すため。規則の節は `delete_rule_layer` が消す）。
3. 再処理の削除（B1）は dev の問い合わせに C8 の規則の 2 条件を足した 1 つの問い合わせにする。
4. 予約のリレーション種類に足すのは `HAS_SECTION`・`IN_SECTION` だけ。文書層のリレーション種類は `constants.LAYER_RELATIONSHIP_TYPES` でスキーマの統合の候補から外すだけにする（C7）。`constants.py` の先頭は、dev の `GRAPH_EDIT_MANAGEMENT_LABELS` の行を先に、C7 の `Section` の行を後に置く（`validate.RESERVED_LABELS` の並びが `…, __GraphLock__, Section, __KGBuilder__` になる）。
5. 文書層の作成（`document_layer.py`）は、規則の `Standard` の書き込み、名寄せの後の `MATCH (s:Standard {id: $no}) SET s.method = $m, s._v = coalesce(s._v, 0) + 1 REMOVE s.embedding`（C7 の埋め込みの消去と合わせた 1 行）、型式 `Model` の書き込みで `_v` を 1 上げる。文書層と章・節の層の作成は、`/post_processing` が `run_with_lease` の中で行う（C7 が並べた順: 参照リンク → スキーマの統合 → 文書層 → 章・節の層 → 類似 → 索引 → 埋め込み → コミュニティ。参照リンクとスキーマの統合の段は包まない）。
6. `score.py` の入口の並びは `/health` → `/extraction_methods`（C5）→ 設定と数の入口（C9、`try` で包む）→ `app.include_router(graph_edit_router)`（C6）。
7. 修正台帳は、グラフ設定の「保守」タブの中のタブ 32「修正台帳」に置く（上のタブは C7 の 4 つだけ。6 章）。
8. 試験は本番の backend を読み取り専用で載せた一時コンテナから試験用 Neo4j に向けて流す（7 章）。dev の試験は、予約の一覧（`Section`・`__KGBuilder__`・`HAS_SECTION`・`IN_SECTION`）と、写しの件数の上書き（`GBDEV_PRISTINE`）と、C5 の抽出方式の引数（B9 の試験の代わりの `processing_chunks`）を本番に合わせた。貸し出しの長い待ちの試験（`test_review_lease_heartbeat_and_long_wait`）は、`time.sleep` の差し替えがプロセス全体に効くので、貸し出しを取りに行くスレッドの待ちだけを数える形にした（`score.py` を import すると ragas の利用状況の送信のスレッドが 1 秒おきに `time.sleep` を呼び、試験ファイルを続けて流すと数がずれるため）。

## 6. 画面（frontend、工程 S3）

- 新しい service `frontend/src/services/GraphEdit.ts`: 3 章の入口ごとの関数。`api.post('/graph_edit/xxx', formData)`（接続情報は interceptor が付ける）。`status === 'Failed'` なら `GraphEditError(code, detail, data)` を投げる。コードの日本語の文言の表を同じファイルに置く。
- 型: `frontend/src/types.ts` に `GraphEditNodeDetail` などを足す。
- グラフ表示 `GraphViewModal.tsx`:
  - 右上のボタン列に「編集」の切り替え（`!isReadOnlyUser` で、viewPoint が `showGraphView`・`tableView`・`chatInfoView` のときだけ）。
  - 編集の状態では `selectOnClick:false` にし、選択を React の状態で持つ。Ctrl か Shift を押しながらのクリックで追加選択。`interactionOptions` は 4 項目すべてを明示する。`mouseEventCallbacks` の `useMemo` の依存配列に、中で参照する状態を足す。
  - ノードとリレーションの右クリックに操作の一覧（NDL の Popover を `anchorPosition` で開く）: 「編集」「統合の対象に追加」「選んだ 2 つの間にリレーションを追加」「削除」。リレーションでは「種類を変更」「向きを反転」「削除」。
  - 右の詳細欄: 編集の状態では新しい `GraphEditPanel` を出す。開くときに `/graph_edit/node_detail` で全プロパティを取り直す（近傍の表示はプロパティが id と title だけなので、取り直すまで編集させない）。名前・種類（`/graph_edit/vocabulary` から選ぶ）・説明・その他の文字列プロパティの編集、統合（選択中の 2 つ以上。残す名前・種類・説明のまとめ方を選ぶダイアログ）、削除（「再び抽出されても作らない」の確認付き。既定は入）。
  - 改名で `CONFLICT` が返ったら、統合のダイアログを開いて提案する。
  - 「エンティティを名前で探す」入力欄: `/graph_edit/search_entities` の結果から選ぶと `/graph_edit/neighborhood` で近傍を表示に加える。
  - 操作の後は、返ったノード・リレーションで表示の状態（node と allNodes、relationship と allRelationships、scheme）を差し替える。統合・削除・付け替えのように要素 ID が変わるものは、表示を取り直す（tableView/showGraphView は既存の更新、chatInfoView は neighborhood で取り直す）。`initGraph` の「リレーションが 0 件なら差し替えない」条件を直す。
  - 先頭が `_` のプロパティは `GraphPropertiesTable` で通常の表示から外す。
  - 本番の絞り込み（文書・凡例・検索。C3/C4）と組み合わせる。描くのは絞り込みの結果（`visibleNodes`・`visibleRels`）から作った `displayNodes`・`displayRelationships` で、編集の状態では選択の印だけを編集の選択で上書きする。「エンティティを名前で探す」で加えたノードは `editPinnedIds` として文書の絞り込みの外でも残す（編集を終える・閉じる・開き直すと忘れる）。編集の状態では絞り込みの欄（ResultOverview）は出ないが、絞り込みはかかったままで、表示の件数を編集のバッジに出す。
  - 規則で作ったノード（2.2）: 右クリックの「編集」「削除」は無効で、理由を出す。「統合の対象に追加」と、選んだ 2 つの間のリレーションの追加は使える。複数選択に含むときは注意の表示を出し、2 つ以上なら統合を、1 つでも含めば削除を無効にする。統合のダイアログでは、規則で作ったノードを統合先に固定し、残す名前と種類を変えさせない（`node_detail` の `merge_target_only`）。章・節（`Section`）は、どの操作も無効で理由を出す。
- グラフの設定（`GraphEnhancementDialog`）:
  - 重複統合のタブ: 統合は今の入口のまま（バックエンドで M4 の処理に替わる）。各行に「別物として登録」ボタン（`/graph_edit/mark_distinct`）。失敗を画面に出す（今は console.log だけ）。応答に `skipped` があれば、統合しなかった行と理由をトーストで出す。
  - 孤立ノードの削除のタブ: `status` が `Failed` か通信に失敗したら、トーストで出し、表から消さない。
  - `CONFLICT` に `data.reason` が付くとき（2.4 の 5・6）は、文言にその理由を添える。
  - 「保守」タブの中のタブ「修正台帳」（タブ番号 32。隣は「設定と数」30・「更新と再処理」31。上のタブは C7 の「取り込み」「グラフの整備」「検索の設定」「保守」の 4 つ）: 操作の履歴（取り消しのボタン付き。取り消せないものは理由を表示）と、台帳の行の一覧（種類で絞り込み、1 行ずつ削除）。部品 `GraphEditLedgerTab` は props を取らない。
- 文言はすべて日本語。用語は「エンティティ」「リレーション」「プロパティ」。比較に使う値（状態名など）は英語のまま。
- 描画部品 NVL の `nvlOptions` に `disableTelemetry: true`（B7）。

## 7. 試験の分担

| 試験 | 置き場 | 実行 |
|---|---|---|
| V0 | `backend/tests/test_s1_fixes.py` | 試験用 Neo4j で。読み込み直しの手順の後に流す |
| V1 | `backend/tests/test_graph_edit_ops.py` | 試験用 Neo4j で。操作ごとに成功・衝突・予約・古い要素 ID・台帳・件数・取り消し |
| V2 | `backend/tests/test_ledger_replay.py` | 純粋な関数の試験（標準の抽出の形と EDC の形）と、試験用 Neo4j での保存の試験 |
| 規則の層 | `backend/tests/test_rule_layer_policy.py` | 試験用 Neo4j で。本番だけの方針（2.2 の規則で作ったノードと章・節、取り込み直し、文書の削除、`run_with_lease`）。合成のノードだけを使うので、どの写しでも流せる |
| V3 | `frontend` の型検査とリント（変更したファイル）、画面を表示しないブラウザでの操作の試験 | node:20 のコンテナと Playwright のコンテナ |
| V5 | （`scratchpad/gbdev/v5_compare.py` は失われた） | 稼働中の backend（元のコード）と backend-gbdev（新しいコード）に同じ問いを送り、取り出すチャンクの id の集合を比較した（dev の作業ツリーでの確かめ。本番への反映では流していない） |

試験の実行（本番の作業ツリー）: 本番の backend コンテナでは流さない（メモリの上限に近く、`/code/.env` の `load_dotenv(override=True)` で本番の DB を向くおそれがある）。本番の backend を読み取り専用で載せ、`/code/.env` を試験用の接続先に差し替えた一時コンテナから流す。`gbtest.env` は `backend/.env` の `NEO4J_URI`・`NEO4J_USERNAME`・`NEO4J_PASSWORD`・`NEO4J_DATABASE` を試験用に替えた写し、`gbtest.runtime.env` は稼働中の backend の環境変数から `NEO4J_*` を除き試験用の 4 つを足したもので、どちらも権限 600 で作業場に置き、使い終えたら消す。

```sh
docker run --rm --memory=3g --memory-swap=3g --cpus=4 --network llm-graph-builder_net \
  --env-file <作業場>/gbtest.runtime.env -e PYTHONDONTWRITEBYTECODE=1 -e GBDEV_TEST_URI=neo4j://database-gbdev:7687 \
  -v /home/uchida/ai-stack/llm-graph-builder/backend:/code:ro -v <作業場>/gbtest.env:/code/.env:ro \
  -v /home/uchida/ai-stack/EDC/schemas:/edc_schemas:ro -v /home/uchida/ai-stack/llm-graph-builder/tools:/tools:ro \
  --tmpfs /code/logs -w /code llm-graph-builder-backend:latest python -m pytest -q -p no:cacheprovider \
  tests/test_document_layer_rules.py tests/test_s1_fixes.py tests/test_graph_edit_ops.py tests/test_ledger_replay.py tests/test_rule_layer_policy.py
```

- 埋め込みは稼働中の backend と同じ設定（`embed-cpu`）を使い、LLM は差し替えるので GPU も LLM も要らない。
- 写しの件数は既定で 10/31 の写し（文書 7、チャンク 184、エンティティ 671）を前提にする。別の写しで流すときは `-e GBDEV_PRISTINE='{"documents": N, "chunks": M, "entities": K}'` を渡し、`test_s1_fixes.py` は `-k 'b4 or b5 or b8 or b9 or change_embedding or reserved'` に絞る（共有のチャンクの試験は 10/31 の写しでしか成り立たない）。
- 文書の更新の試験 `tests/test_document_replace.py`（C8）は `common_fn` を差し替えて読み込むので、必ず単独のプロセスで流し、上の試験と同時に流さない（`C8_NEO4J_URI=neo4j://database-gbdev:7687` と `C8_NEO4J_PASSWORD` を渡す）。
- 各試験ファイルは自分の作ったもの（名前の正規化キーが zzge で始まるもの）だけを消し、始める前と終わった後に写しの件数を確かめる。壊したときは `restore_gbdev.sh gbdev_1031` で戻す。
- 検索の経路を通す試験（`test_embedding_is_removed_when_ruri_is_down_and_search_still_works`）は、稼働中の backend と同じ設定でリランカー（`rerank-cpu`）と埋め込み（`embed-cpu`）を CPU で呼ぶので、1 本で 3〜5 分かかる。
- 2026-11-08 の反映の確かめ（本番の作業ツリー、10/31 の写し）: 上の 5 本で 105 件がすべて通った（`test_document_layer_rules` 6、`test_s1_fixes` 35、`test_graph_edit_ops` 39、`test_ledger_replay` 12、`test_rule_layer_policy` 13。`test_document_layer_rules` の 56 本の数の試験は `tools` を `/tools` に載せたときだけ流れる）。続けて `test_document_replace.py` を単独で流し 3 件が通った。試験の後、写しはノード 1288・リレーション 2978（文書 7、チャンク 184、エンティティ 671）に戻り、管理用ノードは 0 件だった。
