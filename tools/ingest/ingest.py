#!/usr/bin/env python3
"""フォルダの文書を Graph Builder に一括で取り込む（アップロード → 抽出 → 後処理）。

用途     : 画面を開いたままにせず、数十本の文書を一括で取り込む場合に使用する。
留意事項 : 既存のグラフは削除しない。取り込み済み（Completed）の文書は対象外とする（--force で再取り込みする）。
           再取り込みでは、抽出の開始時に前の版のチャンク・その文書のみのエンティティ・規則により生成した章・節と文書層のリレーションを削除する
           （他の文書と共有するエンティティと、文書番号・型式のエンティティは保持する。削除した件数は run.jsonl の replaced に記録する）。
           抽出が連続して失敗した場合は停止する（常駐の LLM が停止している可能性が高い。LLM の再起動は行わない）。
使い方   :
    python3 tools/ingest/ingest.py <フォルダ または ファイル...> [--model gemma4] [--extraction-method standard|edc] [--force] [--dry-run]
            [--edc-doctype auto|none|<文書種別名>] [--edc-schema-mode fixed|fixed_extended]
            [--backend http://localhost:8440] [--allowed-nodes "A,B"] [--instructions "..."]
            [--tasks materialize_entity_similarities,enable_hybrid_search_and_fulltext_search_in_bloom]
    記録は tools/ingest/runs/<日時>/run.jsonl（1 文書 1 行）と summary.json に出力する。
前提     : ホストの python3 と curl のみで動作する。Neo4j の接続先は backend/.env（NEO4J_URI/USERNAME/PASSWORD/DATABASE）から読み込む。
           埋め込みは backend と同じ ruri（vllm）を使用する。
"""
import argparse, datetime, glob, json, os, pathlib, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SUPPORTED = {".pdf", ".docx", ".pptx", ".xls", ".xlsx", ".txt", ".md", ".html", ".jpeg", ".jpg", ".png", ".svg"}
DEFAULT_TASKS = "materialize_text_chunk_similarities,enable_hybrid_search_and_fulltext_search_in_bloom,link_chunk_references,build_document_layer,build_section_layer,attach_provenance,materialize_entity_similarities"
# EDC の初期スキーマの指定（backend の src/entities/source_extract_params.py の edc_doctype・edc_schema_mode と同じ値）
EDC_SCHEMA_MODES = ("fixed", "fixed_extended")
EDC_DOCTYPE_KEYWORDS = ("auto", "none")


def read_env(path):
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def curl(args, timeout):
    p = subprocess.run(["curl", "-s", "--max-time", str(timeout)] + args, capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"status": "unreadable", "message": (p.stdout or p.stderr)[-300:]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="フォルダ、またはファイル")
    ap.add_argument("--backend", default=os.environ.get("GB_BACKEND", "http://localhost:8440"))
    ap.add_argument("--model", default="gemma4", help="LLM。標準の抽出に使用する（EDC の場合は EDC 側の LLM が使用され、記録も EDC 側の LLM になる）")
    ap.add_argument("--extraction-method", choices=["standard", "edc"], default="standard",
                    help="抽出方式。edc はスキーマ統制の抽出（EDC）。旧来の --model edc は --extraction-method edc と同じ扱い")
    ap.add_argument("--edc-doctype", default="",
                    help="EDC の文書種別（--extraction-method edc の場合のみ有効）。auto は文書ごとに先頭から判定、none は文書種別を指定しない（語彙を限定せずに抽出する）、"
                         "それ以外は登録済みの文書種別の名前。省略時は backend の設定（LLM_MODEL_CONFIG_EDC）に従う")
    ap.add_argument("--edc-schema-mode", choices=EDC_SCHEMA_MODES, default=None,
                    help="EDC の初期スキーマの運用モード（--extraction-method edc の場合のみ有効）。fixed は文書種別の語彙に写せないリレーションを落とす、"
                         "fixed_extended は語彙に写せないリレーションも追加する。省略時は backend の設定（EDC_ENRICH_SCHEMA）に従う")
    ap.add_argument("--force", action="store_true", help="取り込み済みの文書も再取り込みする")
    ap.add_argument("--dry-run", action="store_true", help="対象の一覧のみ出力する")
    ap.add_argument("--recursive", action="store_true", help="フォルダの下位の階層も対象にする")
    ap.add_argument("--allowed-nodes", default="")
    ap.add_argument("--allowed-relationships", default="")
    ap.add_argument("--instructions", default="")
    ap.add_argument("--token-chunk-size", default="")
    ap.add_argument("--chunk-overlap", default="")
    ap.add_argument("--chunks-to-combine", default="")
    ap.add_argument("--tasks", default=DEFAULT_TASKS, help="後処理。空文字の場合は後処理を実行しない")
    ap.add_argument("--embedding-provider", default="vllm")
    ap.add_argument("--embedding-model", default="cl-nagoya/ruri-v3-310m")
    ap.add_argument("--max-failures", type=int, default=3, help="連続してこの回数失敗した場合に停止する")
    ap.add_argument("--neo4j-uri"); ap.add_argument("--neo4j-user"); ap.add_argument("--neo4j-password"); ap.add_argument("--neo4j-database")
    a = ap.parse_args()
    if a.model.strip().lower() == "edc":
        # 旧来の指定。EDC は抽出方式で、LLM ではない（verify_stage1.sh などが --model edc を渡しても動くようにする）
        print("注意：--model edc は --extraction-method edc として扱う（--model は gemma4 とする）", flush=True)
        a.model, a.extraction_method = "gemma4", "edc"
    a.edc_doctype = (a.edc_doctype or "").strip()
    a.edc_schema_mode = a.edc_schema_mode or ""
    if a.edc_doctype and ("," in a.edc_doctype or "\n" in a.edc_doctype or len(a.edc_doctype) > 100):
        sys.exit(f"--edc-doctype の値が文書種別の名前の形でない（カンマ・改行を含まず 100 字以内）: {a.edc_doctype!r}")
    if (a.edc_doctype or a.edc_schema_mode) and a.extraction_method != "edc":
        # backend も EDC 以外の方式では無視する（extraction_methods.edc_options_of）。送らずに注意だけ出す
        print("注意：--edc-doctype・--edc-schema-mode は --extraction-method edc の場合のみ有効のため、無視する", flush=True)
        a.edc_doctype, a.edc_schema_mode = "", ""

    env = read_env(ROOT / "backend" / ".env")
    uri = a.neo4j_uri or env.get("NEO4J_URI", "neo4j://database:7687")
    user = a.neo4j_user or env.get("NEO4J_USERNAME", "neo4j")
    pw = a.neo4j_password or env.get("NEO4J_PASSWORD", "")
    db = a.neo4j_database or env.get("NEO4J_DATABASE", "neo4j")
    if not pw:
        sys.exit("Neo4j のパスワードを取得できない（backend/.env の NEO4J_PASSWORD または --neo4j-password で指定する）")
    cred = ["--form-string", f"uri={uri}", "--form-string", f"userName={user}", "--form-string", f"password={pw}",
            "--form-string", f"database={db}", "--form-string", "email=ingest@local"]
    emb = ["--form-string", f"embedding_provider={a.embedding_provider}", "--form-string", f"embedding_model={a.embedding_model}"]

    files = []
    for p in a.paths:
        p = pathlib.Path(p).expanduser()
        if p.is_dir():
            it = p.rglob("*") if a.recursive else p.glob("*")
            files += sorted(x for x in it if x.is_file() and x.suffix.lower() in SUPPORTED)
        elif p.is_file():
            files.append(p)
        else:
            files += [pathlib.Path(x) for x in sorted(glob.glob(str(p)))]
    if not files:
        sys.exit("対象のファイルがない")

    r = curl(["-X", "POST", f"{a.backend}/sources_list"] + cred, 60)
    if r.get("status") != "Success":
        sys.exit(f"backend に接続できない、または Neo4j に接続できない: {r.get('message')}")
    existing = {d.get("fileName"): d.get("status") for d in (r.get("data") or [])}

    plan = []
    for f in files:
        st = existing.get(f.name)
        if st == "Completed" and not a.force:
            plan.append((f, "skip: 取り込み済み"))
        elif st == "Completed":
            plan.append((f, "再取り込み（前の版のチャンク・その文書のみのエンティティ・章・節を削除した後）"))
        else:
            plan.append((f, f"取り込み（現在の状態: {st or 'なし'}）"))
    todo = [f for f, why in plan if not why.startswith("skip")]
    edc_spec = f", edc_doctype={a.edc_doctype or '（backend の設定）'}, edc_schema_mode={a.edc_schema_mode or '（backend の設定）'}" if a.extraction_method == "edc" else ""
    print(f"対象 {len(files)} 本、取り込み {len(todo)} 本、対象外 {len(files) - len(todo)} 本（model={a.model}, extraction_method={a.extraction_method}{edc_spec}, backend={a.backend}）", flush=True)
    for f, why in plan:
        print(f"  {f.name}: {why}")
    if a.dry_run or not todo:
        return
    if a.extraction_method == "edc":
        # EDC が使用できない状態で数十本をアップロードしない。抽出方式に対応していない古い backend では標準で抽出されるため、その場合も停止する
        m = curl([f"{a.backend}/extraction_methods"], 30)
        edc = next((x for x in (m.get("data") or []) if x.get("id") == "edc"), None) if m.get("status") == "Success" else None
        if edc is None:
            sys.exit("backend が抽出方式の指定に対応していない（backend の再起動前の可能性がある）。処理を停止する")
        if not edc.get("available"):
            detail = f"（詳細: {edc['reason_detail']}）" if edc.get("reason_detail") else ""
            sys.exit(f"EDC が現在使用できない: {edc.get('reason')}{detail}（常駐の LLM を確認すること。LLM の再起動は行わない）")
        print(f"EDC の LLM: {edc.get('llm') or '不明'}（EDC 側の設定で決定）", flush=True)
        if a.edc_doctype and a.edc_doctype not in EDC_DOCTYPE_KEYWORDS:
            # 登録されていない文書種別を指定したまま数十本を流さない（backend は未登録の名前を EDC にそのまま渡す）
            known = [d.get("name") for d in (edc.get("doctypes") or []) if d.get("name")]
            if known and a.edc_doctype not in known:
                sys.exit(f"--edc-doctype {a.edc_doctype!r} は登録済みの文書種別にない（登録済み: {', '.join(known)}、または auto・none）")
            if not known:
                print(f"注意：登録済みの文書種別の一覧を取得できないため、--edc-doctype {a.edc_doctype!r} を確認せずに送る", flush=True)

    out = HERE / "runs" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / "run.jsonl", "a", encoding="utf-8")
    fails = 0; rows = []
    for i, f in enumerate(todo, 1):
        t0 = time.time()
        up = curl(["-X", "POST", f"{a.backend}/upload", "-F", f"file=@{f}", "--form-string", "chunkNumber=1", "--form-string", "totalChunks=1",
                   "--form-string", f"originalname={f.name}", "--form-string", f"model={a.model}"] + cred, 600)
        if up.get("status") != "Success":
            row = {"name": f.name, "step": "upload", "status": up.get("status"), "message": str(up.get("message"))[:300], "sec": round(time.time() - t0)}
        else:
            ex = curl(["-X", "POST", f"{a.backend}/extract"] + cred + emb + [
                "--form-string", f"model={a.model}", "--form-string", f"extraction_method={a.extraction_method}", "--form-string", "source_type=local file", "--form-string", f"file_name={f.name}",
                "--form-string", "source_url=", "--form-string", f"allowedNodes={a.allowed_nodes}", "--form-string", f"allowedRelationship={a.allowed_relationships}",
                "--form-string", f"token_chunk_size={a.token_chunk_size}", "--form-string", f"chunk_overlap={a.chunk_overlap}",
                "--form-string", f"chunks_to_combine={a.chunks_to_combine}", "--form-string", "language=", "--form-string", "retry_condition=",
                "--form-string", f"additional_instructions={a.instructions}",
                "--form-string", f"edc_doctype={a.edc_doctype}", "--form-string", f"edc_schema_mode={a.edc_schema_mode}"], 3600)
            d = ex.get("data") or {}
            row = {"name": f.name, "step": "extract", "status": ex.get("status"), "model": d.get("model"), "extraction_method": d.get("extraction_method"),
                   "edc_doctype": a.edc_doctype or None, "edc_schema_mode": a.edc_schema_mode or None,
                   "chunks": d.get("total_chunks"), "nodes": d.get("nodeCount"),
                   "rels": d.get("relationshipCount"), "state": d.get("status"), "message": str(ex.get("message") or "")[:300], "sec": round(time.time() - t0)}
            if d.get("skipped_chunks") is not None:
                row["skipped_chunks"] = d.get("skipped_chunks")  # 抽出失敗チャンク（抽出処理が例外で中断したチャンク）の件数。ID は Document.skipped_chunk_ids（T16）。古い backend では無い
            if a.force or d.get("replaced"):
                row["replaced"] = d.get("replaced")  # 前の版で消した件数 {chunks, entities, sections, rule_edges}。古い backend では null（C8）
        rows.append(row); log.write(json.dumps(row, ensure_ascii=False) + "\n"); log.flush()
        ok = row["status"] == "Success"
        rp = row.get("replaced") or {}
        gone = f" | 前の版のチャンク {rp.get('chunks', 0)} 件・エンティティ {rp.get('entities', 0)} 件・章・節 {rp.get('sections', 0)} 件・文書層のリレーション {rp.get('rule_edges', 0)} 件を削除" if any(rp.values()) else ""
        skip = f" | 注意：抽出失敗チャンク {row['skipped_chunks']} 件（画面の再処理オプションで Start From Last Processed Position（続きから）を選択すると、該当チャンクのみ再抽出する）" if row.get("skipped_chunks") else ""
        print(f"[{i}/{len(todo)}] {f.name}: {row['status']} | チャンク {row.get('chunks')} | ノード {row.get('nodes')} | リレーション {row.get('rels')} | {row['sec']}s {row['message'][:80]}{gone}{skip}", flush=True)
        fails = 0 if ok else fails + 1
        if fails >= a.max_failures:
            print(f"連続して {fails} 回失敗したため停止する（常駐の LLM を確認すること。再開する場合は同じコマンドを再実行する）", flush=True)
            break

    post = None
    if a.tasks and fails < a.max_failures:
        tasks = json.dumps([t for t in a.tasks.split(",") if t])
        print(f"後処理 {tasks}", flush=True)
        post = curl(["-X", "POST", f"{a.backend}/post_processing"] + cred + emb + ["--form-string", f"tasks={tasks}"], 3600)
        print(f"  {post.get('status')} {str(post.get('message') or '')[:120]}", flush=True)
    summary = {"backend": a.backend, "model": a.model, "extraction_method": a.extraction_method,
               "edc_doctype": a.edc_doctype or None, "edc_schema_mode": a.edc_schema_mode or None,
               "files": len(files), "ingested": sum(1 for r in rows if r["status"] == "Success"),
               "failed": sum(1 for r in rows if r["status"] != "Success"), "post_processing": (post or {}).get("status"), "finished": datetime.datetime.now().isoformat(timespec="seconds"),
               "replaced": {k: sum((r.get("replaced") or {}).get(k, 0) for r in rows) for k in ("chunks", "entities", "sections", "rule_edges")},
               "skipped_chunks": sum(r.get("skipped_chunks") or 0 for r in rows)}
    json.dump(summary, open(out / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    skipped = f"、抽出失敗チャンク {summary['skipped_chunks']} 件（文書ごとの件数は上の各行に表示。画面の再処理オプションの Start From Last Processed Position（続きから）で再抽出できる）" if summary["skipped_chunks"] else ""
    print(f"完了: 取り込み {summary['ingested']}、失敗 {summary['failed']}{skipped}、記録 {out}", flush=True)
    sys.exit(1 if summary["failed"] else 0)


if __name__ == "__main__":
    main()
