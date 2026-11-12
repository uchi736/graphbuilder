# -*- coding: utf-8 -*-
"""今のグラフの文書番号ノード（:Standard、method = rule）を、正本の表 document_registry の初期値として入れる。

入れるもの: doc_number（Standard の id）・official_name・kind・note（文書から取れた略称・旧名称・旧番号・外部かどうかの控え）。updated_by = 'seed:graph'。
入れないもの: current_rev（空のままにする。入れると、新しい版を取り込んでも表の版に固定されるため）・department・aliases・parent_doc（人が書く）。
既にある行は変えない（人の修正を上書きしない）。--refresh-seed を付けると、updated_by が 'seed:graph' のまま（人が触っていない）行だけ書き直す。

いつ: 文書層の後処理（build_document_layer）を流した後、表に無い文書番号が増えたとき。
使い方（ai-stack/llm-graph-builder で）: bash tools/kg/registry/run.sh seed_from_graph.py [--dry-run] [--refresh-seed]
  接続先: Neo4j は NEO4J_HTTP（既定 http://127.0.0.1:7475、neo4j-kgb）と backend/.env の NEO4J_USERNAME・NEO4J_PASSWORD、
          Postgres は DOCUMENT_REGISTRY_WRITER_DSN（~/ai-stack/secrets/document-registry.env）。どちらも画面に出さない。
"""
import argparse
import base64
import json
import os
import sys
import urllib.request


def env_file(path):
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def neo4j_standards(url, db, user, password):
    q = """MATCH (s:Standard) WHERE s.method = 'rule'
           RETURN s.id AS id, s.official_name AS official_name, s.kind AS kind, s.external AS external,
                  s.abbreviations AS abbreviations, s.former_names AS former_names, s.former_numbers AS former_numbers,
                  s.latest_rev AS latest_rev, s.held_rev AS held_rev ORDER BY s.id"""
    req = urllib.request.Request("%s/db/%s/tx/commit" % (url.rstrip("/"), db), data=json.dumps({"statements": [{"statement": q}]}).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "Authorization": "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode()).decode()})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors"):
        raise SystemExit("Neo4j の照会に失敗した: %s" % body["errors"][0].get("code"))
    res = body["results"][0]
    return [dict(zip(res["columns"], row["row"])) for row in res["data"]]


def note_of(s):
    parts = []
    for key, label in (("abbreviations", "略称"), ("former_names", "旧名称"), ("former_numbers", "旧番号")):
        if s.get(key):
            parts.append("%s: %s" % (label, "、".join(s[key])))
    if s.get("external"):
        parts.append("手元に無い文書")
    if s.get("held_rev"):
        parts.append("手元の現行版 %s" % s["held_rev"])
    return ("グラフから（seed）: " + " / ".join(parts)) if parts else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-seed", action="store_true")
    a = ap.parse_args()
    be = env_file(os.environ.get("BACKEND_ENV", "/backend.env"))
    rows = neo4j_standards(os.environ.get("NEO4J_HTTP", "http://127.0.0.1:7475"), be.get("NEO4J_DATABASE", "neo4j"),
                           be.get("NEO4J_USERNAME", "neo4j"), be["NEO4J_PASSWORD"])
    print("グラフの Standard（method = rule）: %d 件" % len(rows))
    if a.dry_run:
        for s in rows:
            print(" ", s["id"], "|", s.get("official_name"), "|", s.get("kind"), "|", note_of(s))
        return
    import psycopg
    dsn = os.environ["DOCUMENT_REGISTRY_WRITER_DSN"]
    added = refreshed = 0
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            for s in rows:
                cur.execute("""INSERT INTO document_registry (doc_number, official_name, kind, note, updated_by)
                               VALUES (%s, %s, %s, %s, 'seed:graph') ON CONFLICT (doc_number) DO NOTHING""",
                            (s["id"], s.get("official_name"), s.get("kind"), note_of(s)))
                added += cur.rowcount
                if a.refresh_seed and cur.rowcount == 0:
                    cur.execute("""UPDATE document_registry SET official_name = %s, kind = %s, note = %s
                                   WHERE doc_number = %s AND updated_by = 'seed:graph'""",
                                (s.get("official_name"), s.get("kind"), note_of(s), s["id"]))
                    refreshed += cur.rowcount
            cur.execute("SELECT count(*), count(*) FILTER (WHERE updated_by <> 'seed:graph') FROM document_registry")
            total, human = cur.fetchone()
    print("追加 %d 件、書き直し %d 件。表の行 %d 件（うち人が直した行 %d 件）" % (added, refreshed, total, human))


if __name__ == "__main__":
    sys.exit(main())
