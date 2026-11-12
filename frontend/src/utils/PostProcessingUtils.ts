import { CustomFile } from '../types';

// 取り込みの後の自動の後処理（Content.tsx）と同じ絞り込み。スキーマを設定しているときはスキーマ統合を、GDS が無いときはコミュニティを外す
export const postProcessingPayload = (tasks: string[], isGdsActive: boolean, hasSelections: boolean): string[] =>
  tasks.filter(
    (task) =>
      !(hasSelections && task === 'graph_schema_consolidation') && !(!isGdsActive && task === 'enable_communities')
  );

// /post_processing が返す文書ごとの件数（graphDBdataAccess.update_node_relationship_count）
export interface PostProcessingCountRow {
  filename: string;
  chunkNodeCount?: number;
  entityNodeCount?: number;
  communityNodeCount?: number;
  chunkRelCount?: number;
  entityEntityRelCount?: number;
  communityRelCount?: number;
  nodeCount?: number;
  relationshipCount?: number;
}

// 件数をファイルの一覧に反映する（Content.tsx の自動の後処理と同じ項目）
export const mergePostProcessingCounts = (files: CustomFile[], rows: PostProcessingCountRow[]): CustomFile[] => {
  const byName = new Map(rows.map((row): [string, PostProcessingCountRow] => [row.filename, row]));
  return files.map((f) => {
    const c = byName.get(f.name ?? '');
    if (!c) {
      return f;
    }
    return {
      ...f,
      chunkNodeCount: c.chunkNodeCount ?? 0,
      entityNodeCount: c.entityNodeCount ?? 0,
      communityNodeCount: c.communityNodeCount ?? 0,
      chunkRelCount: c.chunkRelCount ?? 0,
      entityEntityRelCount: c.entityEntityRelCount ?? 0,
      communityRelCount: c.communityRelCount ?? 0,
      nodesCount: c.nodeCount ?? f.nodesCount,
      relationshipsCount: c.relationshipCount ?? f.relationshipsCount,
    };
  });
};
