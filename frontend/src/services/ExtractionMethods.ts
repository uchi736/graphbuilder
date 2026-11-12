import api from '../API/Index';

// 文書種別（EDC が語彙を切り替える単位）。件数は共通の語彙と文書種別ごとの語彙の和集合で、定義ファイルを読み込めない場合は null
export interface ExtractionDoctype {
  name: string;
  description?: string;
  node_label_count?: number | null;
  relationship_type_count?: number | null;
}

// backend の現在の EDC 設定（参照のみ）。判定できない項目は null。
// doctype は LLM_MODEL_CONFIG_EDC の 2 番目の値（auto・none・文書種別の名称）、enrich_schema は EDC_ENRICH_SCHEMA、
// fold_unknown_relations は EDC_FOLD_UNKNOWN_RELATIONS、drop_sentence_names は EDC_DROP_SENTENCE_NAMES の値である
export interface EdcSettings {
  doctype: string | null;
  doctype_mode: 'auto' | 'none' | 'fixed' | null;
  enrich_schema: boolean | null;
  schema_mode: 'fixed' | 'fixed_extended' | null;
  fold_unknown_relations: boolean | null;
  unknown_relations: 'fold' | 'keep' | null;
  drop_sentence_names: boolean | null;
}

// 共通の語彙（backbone）の件数と、定義ファイルを読み込めたかどうか
export interface EdcVocabularySummary {
  available: boolean;
  error: string | null;
  backbone_node_label_count: number | null;
  backbone_relationship_type_count: number | null;
}

// 抽出方式の選択肢（GET /extraction_methods）。EDC は利用可否・EDC 側の LLM・文書種別・backend の EDC 設定も返る。
// settings・vocabulary・doctypes_source と文書種別の件数は、backend の再起動前（旧版）の応答には含まれない
export interface ExtractionMethodOption {
  id: string;
  label: string;
  available: boolean;
  llm?: string | null;
  llm_id?: string | null;
  llm_status?: string | null;
  reason?: string | null;
  // 使用できない理由の詳細（接続できない場合は例外の名前、依存サービスの停止の場合は /health の状態の原文）。ポインタを合わせると表示する
  reason_detail?: string | null;
  doctypes?: ExtractionDoctype[];
  doctypes_source?: 'edc' | 'registry' | null;
  settings?: EdcSettings | null;
  vocabulary?: EdcVocabularySummary | null;
}

export const getExtractionMethods = async (): Promise<ExtractionMethodOption[]> => {
  const response = await api.get('/extraction_methods', { timeout: 15000 });
  if (response.data?.status !== 'Success' || !Array.isArray(response.data?.data)) {
    throw new Error(response.data?.error ?? response.data?.message ?? '抽出方式の一覧を取得できませんでした');
  }
  return response.data.data as ExtractionMethodOption[];
};
