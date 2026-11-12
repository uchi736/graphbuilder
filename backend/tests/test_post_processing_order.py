# -*- coding: utf-8 -*-
'''後処理（/post_processing）の流す順番の試験。score.py は読み込まず、構文木から「'ジョブ名' in tasks」の並びを取り出して比べる。
画面の POST_PROCESSING_JOBS（frontend/src/utils/Constants.ts）の order とも突き合わせる。標準ライブラリだけで動く。
実行: python3 -B backend/tests/test_post_processing_order.py   （pytest があれば python3 -m pytest backend/tests/test_post_processing_order.py -q）'''
import ast
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SCORE = os.path.join(os.path.dirname(HERE), 'score.py')
CONSTANTS = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'frontend', 'src', 'utils', 'Constants.ts')

# 参照リンク → スキーマ統合 → 文書層 → 表記ゆれの統合 → 章・節の層 → 由来情報と安定キー → 節の要点 → チャンクの類似 → 索引 → エンティティの埋め込み → コミュニティ
EXPECTED = [
    'link_chunk_references',
    'graph_schema_consolidation',
    'build_document_layer',
    'merge_entity_variants',
    'build_section_layer',
    'attach_provenance',
    'build_section_summaries',
    'materialize_text_chunk_similarities',
    'enable_hybrid_search_and_fulltext_search_in_bloom',
    'materialize_entity_similarities',
    'enable_communities',
]


def backend_order():
    tree = ast.parse(open(SCORE, encoding='utf-8').read())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'post_processing')
    first_line = {}
    for node in ast.walk(fn):
        if (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.In)
                and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)
                and isinstance(node.comparators[0], ast.Name) and node.comparators[0].id == 'tasks'):
            first_line.setdefault(node.left.value, node.lineno)
    return [task for task, _ in sorted(first_line.items(), key=lambda kv: kv[1])]


def frontend_order():
    text = open(CONSTANTS, encoding='utf-8').read()
    body = text[text.index('export const POST_PROCESSING_JOBS'):]
    body = body[:body.index('];')]
    pairs = re.findall("title: '([a-z_]+)',[^}]*?order: ([0-9]+),", body)
    return [title for title, _ in sorted(pairs, key=lambda p: int(p[1]))]


def test_backend_order():
    assert backend_order() == EXPECTED, backend_order()


def test_frontend_order_matches_backend():
    if not os.path.exists(CONSTANTS):  # バックエンドのコンテナの中（/code）には画面のソースが無い
        return
    assert frontend_order() == EXPECTED, frontend_order()


if __name__ == '__main__':
    test_backend_order()
    test_frontend_order_matches_backend()
    print('ok')
