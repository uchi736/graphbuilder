import api from '../API/Index';
import { getVocabulary } from './GraphEdit';

// グラフスキーマ設定欄（標準抽出用）の選択肢。現在のグラフに存在するラベルとリレーションタイプで、件数を取得できない場合 count は null
export interface SchemaOption {
  name: string;
  count: number | null;
}

export interface GraphSchemaOptions {
  labels: SchemaOption[];
  relationshipTypes: SchemaOption[];
  // 件数を取得できたか（POST /schema_options の失敗時は false）
  hasCounts: boolean;
}

interface SchemaOptionCounts {
  labels: { name: string; count: number }[];
  relationship_types: { name: string; count: number }[];
}

// POST /schema_options: ラベルごとのノード数とリレーションタイプごとのリレーション数。資格情報は API/Index のインターセプタが付与する
const getSchemaOptionCounts = async (): Promise<SchemaOptionCounts> => {
  const response = await api.post('/schema_options', new FormData());
  if (response.data?.status !== 'Success' || !response.data?.data) {
    throw new Error(response.data?.error ?? response.data?.message ?? 'schema_options');
  }
  return response.data.data as SchemaOptionCounts;
};

// 件数がある場合は件数の降順（同数は名称の昇順）、件数がない場合は名称の昇順に並べる
const toOptions = (names: string[], counts: Map<string, number> | null): SchemaOption[] => {
  // 件数を取得できた場合は、件数 0 の名称（ノード・リレーションの削除後もラベルの一覧に残る名称）を除外する
  const options = names
    .map((name) => ({ name, count: counts?.get(name) ?? null }))
    .filter((option) => counts === null || option.count !== 0);
  return options.sort((a, b) => (b.count ?? 0) - (a.count ?? 0) || a.name.localeCompare(b.name));
};

// 名称は POST /graph_edit/vocabulary から、件数は POST /schema_options から取得する。
// 件数を取得できない場合は名称のみを返し、名称を取得できない場合は件数の応答の名称を使用する。どちらも取得できない場合は例外とする
export const loadGraphSchemaOptions = async (): Promise<GraphSchemaOptions> => {
  const [vocabulary, counts] = await Promise.allSettled([getVocabulary(), getSchemaOptionCounts()]);
  const countData = counts.status === 'fulfilled' ? counts.value : null;
  if (vocabulary.status === 'rejected' && countData === null) {
    throw vocabulary.reason;
  }
  const labelNames =
    vocabulary.status === 'fulfilled' ? vocabulary.value.labels : (countData?.labels ?? []).map((l) => l.name);
  const typeNames =
    vocabulary.status === 'fulfilled'
      ? vocabulary.value.relationship_types
      : (countData?.relationship_types ?? []).map((t) => t.name);
  const labelCounts = countData ? new Map(countData.labels.map((l) => [l.name, l.count])) : null;
  const typeCounts = countData ? new Map(countData.relationship_types.map((t) => [t.name, t.count])) : null;
  return {
    labels: toOptions(labelNames, labelCounts),
    relationshipTypes: toOptions(typeNames, typeCounts),
    hasCounts: countData !== null,
  };
};
