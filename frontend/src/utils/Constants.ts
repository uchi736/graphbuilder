import { NvlOptions } from '@neo4j-nvl/base';
import { GraphType, OptionType, PatternOption } from '../types';
import { getDateTime, getDescriptionForChatMode } from './Utils';
import chatbotmessages from '../assets/ChatbotMessages.json';
import schemaExamples from '../assets/newSchema.json';
export const APP_SOURCES =
  import.meta.env.VITE_REACT_APP_SOURCES && import.meta.env.VITE_REACT_APP_SOURCES !== ''
    ? (import.meta.env.VITE_REACT_APP_SOURCES?.split(',') as string[])
    : ['s3', 'local', 'wiki', 'youtube', 'web'];

export const llms = import.meta.env?.VITE_LLM_MODELS?.trim()
  ? (import.meta.env.VITE_LLM_MODELS.split(',') as string[])
  : [
      'gemini_3.5_flash',
      'openai_gpt_5.5',
      'openai_gpt_5.4_mini',
      'gemini_3.1_pro_preview',
      'diffbot',
      'groq_llama3.1_8b',
      'anthropic_claude_4.7_opus',
      'anthropic_claude_4.5_haiku',
      'llama4_maverick',
      'bedrock_nova_pro_v1',
      'fireworks_deepseek_v4_flash',
      'fireworks_qwen3_6',
      'fireworks_gpt_oss',
      'fireworks_kimi_k2p6',
      'fireworks_glm_5.1',
      'edc',
    ];

export const prodllms = import.meta.env.VITE_LLM_MODELS_PROD?.trim()
  ? (import.meta.env.VITE_LLM_MODELS_PROD.split(',') as string[])
  : ['gemini_3.5_flash', 'openai_gpt_5.4_mini', 'diffbot', 'anthropic_claude_4.5_haiku'];

export const chatModeLables = {
  vector: 'vector',
  graph: 'graph',
  'graph+vector': 'graph_vector',
  fulltext: 'fulltext',
  'graph+vector+fulltext': 'graph_vector_fulltext',
  'entity search+vector': 'entity_vector',
  unavailableChatMode: 'ファイル選択中はチャットモードを変更できません',
  selected: '選択中',
  'global search+vector+fulltext': 'global_vector',
};
export const chatModeReadableLables: Record<string, string> = {
  vector: 'vector',
  graph: 'graph',
  graph_vector: 'graph+vector',
  fulltext: 'fulltext',
  graph_vector_fulltext: 'graph+vector+fulltext',
  entity_vector: 'entity search+vector',
  unavailableChatMode: 'ファイル選択中はチャットモードを変更できません',
  selected: '選択中',
  global_vector: 'global search+vector+fulltext',
};
export const chatModes = import.meta.env?.VITE_CHAT_MODES?.trim()
  ? import.meta.env.VITE_CHAT_MODES?.split(',').map((mode: string) => ({
      mode: mode.trim(),
      description: getDescriptionForChatMode(mode.trim()),
    }))
  : [
      {
        mode: chatModeLables.vector,
        description: 'ベクトルインデックスを使ってテキストチャンクの意味的類似検索を行います。',
      },
      {
        mode: chatModeLables.graph,
        description: '質問をCypherクエリに変換し、グラフデータベースから正確にデータを取得します。',
      },
      {
        mode: chatModeLables['graph+vector'],
        description: 'ベクトルインデックスとグラフの関係を組み合わせ、文脈を強化した意味検索を行います。',
      },
      {
        mode: chatModeLables.fulltext,
        description: '全文インデックスを使った高速なキーワード検索を行います。',
      },
      {
        mode: chatModeLables['graph+vector+fulltext'],
        description: 'ベクトル・グラフ・全文インデックスを統合し、網羅的な検索結果を提供します。',
      },
      {
        mode: chatModeLables['entity search+vector'],
        description: 'エンティティノードのベクトルインデックスを使い、エンティティ中心の検索を行います。',
      },
      {
        mode: chatModeLables['global search+vector+fulltext'],
        description: 'コミュニティノードのベクトル・全文インデックスを使い、グラフ全体から文脈に即した回答を生成します。',
      },
    ];

export const chunkSize = import.meta.env.VITE_CHUNK_SIZE ? Number(import.meta.env.VITE_CHUNK_SIZE) : 1 * 1024 * 1024;
export const tokenchunkSize = import.meta.env.VITE_TOKENS_PER_CHUNK
  ? Number(import.meta.env.VITE_TOKENS_PER_CHUNK)
  : 100;
export const chunkOverlap = import.meta.env.VITE_CHUNK_OVERLAP ? Number(import.meta.env.VITE_CHUNK_OVERLAP) : 20;
export const chunksToCombine = import.meta.env.VITE_CHUNK_TO_COMBINE
  ? Number(import.meta.env.VITE_CHUNK_TO_COMBINE)
  : 1;
export const defaultTokenChunkSizeOptions = [50, 100, 200, 400, 1000];
export const defaultChunkOverlapOptions = [10, 20, 30, 40, 50];
export const defaultChunksToCombineOptions = [1, 2, 3, 4, 5, 6];

export interface EmbeddingModelOption {
  provider: string;
  model: string;
  dimension: number;
  label: string;
  value: string;
}

export const embeddingModels: EmbeddingModelOption[] = [
  {
    provider: 'openai',
    model: 'text-embedding-3-large',
    dimension: 3072,
    label: 'OpenAI text-embedding-3-large',
    value: 'openai-text-embedding-3-large',
  },
  {
    provider: 'openai',
    model: 'text-embedding-3-small',
    dimension: 1536,
    label: 'OpenAI text-embedding-3-small',
    value: 'openai-text-embedding-3-small',
  },
  {
    provider: 'openai',
    model: 'text-embedding-ada-002',
    dimension: 1536,
    label: 'OpenAI text-embedding-ada-002',
    value: 'openai-text-embedding-ada-002',
  },
  {
    provider: 'gemini',
    model: 'gemini-embedding-001',
    dimension: 3072,
    label: 'Gemini gemini-embedding-001',
    value: 'gemini-gemini-embedding-001',
  },
  {
    provider: 'gemini',
    model: 'text-embedding-005',
    dimension: 768,
    label: 'Gemini text-embedding-005',
    value: 'gemini-text-embedding-005',
  },
  {
    provider: 'titan',
    model: 'amazon.titan-embed-text-v2:0',
    dimension: 1024,
    label: 'Titan amazon.titan-embed-text-v2:0',
    value: 'titan-amazon.titan-embed-text-v2:0',
  },
  {
    provider: 'titan',
    model: 'amazon.titan-embed-text-v1',
    dimension: 1536,
    label: 'Titan amazon.titan-embed-text-v1',
    value: 'titan-amazon.titan-embed-text-v1',
  },
  {
    provider: 'sentence-transformer',
    model: 'all-MiniLM-L6-v2',
    dimension: 384,
    label: 'Sentence Transformer all-MiniLM-L6-v2',
    value: 'sentence-transformer-all-MiniLM-L6-v2',
  },
];
export const DEFAULT_EMBEDDING_MODEL: EmbeddingModelOption = {
  provider: 'sentence-transformer',
  model: 'all-MiniLM-L6-v2',
  dimension: 384,
  label: 'Sentence Transformer all-MiniLM-L6-v2',
  value: 'sentence-transformer-all-MiniLM-L6-v2',
};

export const timeperpage = import.meta.env.VITE_TIME_PER_PAGE ? Number(import.meta.env.VITE_TIME_PER_PAGE) : 50;
export const timePerByte = 0.2;
export const largeFileSize = import.meta.env.VITE_LARGE_FILE_SIZE
  ? Number(import.meta.env.VITE_LARGE_FILE_SIZE)
  : 5 * 1024 * 1024;

export const tooltips = {
  generateGraph: '選択したファイルからグラフを生成',
  deleteFile: '削除するファイルを選択してください',
  showGraph: '生成されたグラフをプレビュー',
  bloomGraph: 'Bloomでグラフを可視化',
  deleteSelectedFiles: '削除対象のファイル',
  previewGraphSelectedFiles: 'グラフをプレビューするファイル',
  documentation: 'ドキュメント',
  github: 'GitHub Issues',
  theme: 'ライト / ダークモード',
  settings: 'エンティティ抽出設定',
  chat: 'チャットを開始',
  sources: 'ファイルをアップロード',
  deleteChat: '削除',
  maximise: '最大化',
  copy: 'クリップボードにコピー',
  copied: 'コピーしました',
  stopSpeaking: '読み上げを停止',
  textTospeech: 'テキスト読み上げ',
  createSchema: 'テキストからスキーマを定義',
  useExistingSchema: 'データベースからスキーマを取得',
  clearChat: 'チャット履歴をクリア',
  continue: '続行',
  clearGraphSettings: '設定済みのグラフスキーマをクリア',
  applySettings: 'グラフスキーマを適用',
  openChatPopout: 'チャット',
  downloadChat: '会話をダウンロード',
  visualizeGraph: 'グラフスキーマを可視化',
  additionalInstructions: 'スキーマ向け指示を解析',
  predinedSchema: '定義済みスキーマ',
  dataImporterJson: 'Data Importer JSON',
};
export const PRODMODELS = ['gemini_3.5_flash', 'openai_gpt_5.4_mini', 'diffbot', 'anthropic_claude_4.5_haiku'];
export const modelTooltipMap: Record<string, string> = {
  'gemini_3.5_flash': 'gemini-3.5-flash',
};
export const buttonCaptions = {
  exploreGraphWithBloom: 'グラフを探索',
  showPreviewGraph: 'グラフをプレビュー',
  deleteFiles: 'ファイルを削除',
  generateGraph: 'グラフを生成',
  dropzoneSpan: 'ドキュメント・画像・非構造化テキスト',
  youtube: 'Youtube',
  gcs: 'GCS',
  amazon: 'Amazon S3',
  noLables: 'データベースにラベルが見つかりません',
  dropYourCreds: 'Neo4j認証情報ファイルをここにドロップ',
  analyze: 'テキストを解析してグラフスキーマを抽出',
  connect: '接続',
  disconnect: '切断',
  submit: '送信',
  connectToNeo4j: 'Neo4jに接続',
  cancel: 'キャンセル',
  details: '詳細',
  continueSettings: '続行',
  clearSettings: 'スキーマをクリア',
  ask: '質問',
  applyGraphSchema: '適用',
  provideAdditionalInstructions: 'エンティティ抽出の処理設定',
  analyzeInstructions: '指示を解析',
  helpInstructions: '重要トピックへの着目など、エンティティ抽出に関する具体的な指示を入力してください。',
  importDropzoneSpan: 'JSONドキュメント',
};

export const POST_PROCESSING_JOBS: { title: string; description: string }[] = [
  {
    title: 'materialize_text_chunk_similarities',
    description: `ナレッジグラフ内のチャンク同士のつながりを精緻化します。k近傍法と類似度しきい値(KNN_MIN_SCORE
                0.8)を用いて、意味的に類似度の高いチャンクを特定してリンクします。これにより、より相互接続された
                知識表現が得られ、検索結果の精度と関連性が向上します。`,
  },
  {
    title: 'enable_hybrid_search_and_fulltext_search_in_bloom',
    description: `ナレッジグラフ内の検索機能を最適化します。データベースラベルの全文インデックスを再構築し、
                情報をより高速・効率的に取得できるようにします。特に大規模なナレッジグラフで効果が大きく、
                キーワード検索の高速化とクエリ性能全体の改善につながります。`,
  },
  {
    title: 'materialize_entity_similarities',
    description: `エンティティの意味を捉えた数値表現(埋め込み)を生成し、エンティティ分析を強化します。
                類似エンティティのクラスタリング、重複の検出、類似度ベースの検索などが可能になります。`,
  },
  {
    title: 'enable_communities',
    description: 'エンティティ間のコミュニティ生成を有効にし、GraphRAGのローカル/グローバル検索を利用できるようにします。',
  },
  {
    title: 'graph_schema_consolidation',
    description:
      '大規模なグラフスキーマに対してLLMを使い、多数のノードラベルとリレーションシップタイプをより少なく適切なものに統合し、抽出済み・既存のグラフに適用します。',
  },
];
export const RETRY_OPIONS = [
  'start_from_beginning',
  'delete_entities_and_start_from_beginning',
  'start_from_last_processed_position',
];
export const batchSize: number = Number(import.meta.env.VITE_BATCH_SIZE ?? '2');

// Graph Constants
export const document = `+ [docs]`;

export const chunks = `+ collect { MATCH p=(c)-[:NEXT_CHUNK]-() RETURN p } // chunk-chain
+ collect { MATCH p=(c)-[:SIMILAR]-() RETURN p } // similar-chunks`;

export const entities = `+ collect { OPTIONAL MATCH (c:Chunk)-[:HAS_ENTITY]->(e), p=(e)-[*0..1]-(:!Chunk) RETURN p}`;

export const docEntities = `+ [docs] 
+ collect { MATCH (c:Chunk)-[:HAS_ENTITY]->(e), p=(e)--(:!Chunk) RETURN p }`;

export const docChunks = `+[chunks]
+collect {MATCH p=(c)-[:FIRST_CHUNK]-() RETURN p} //first chunk
+ collect { MATCH p=(c)-[:NEXT_CHUNK]-() RETURN p } // chunk-chain
+ collect { MATCH p=(c)-[:SIMILAR]-() RETURN p } // similar-chunk`;

export const chunksEntities = `+ collect { MATCH p=(c)-[:NEXT_CHUNK]-() RETURN p } // chunk-chain

+ collect { MATCH p=(c)-[:SIMILAR]-() RETURN p } // similar-chunks
//chunks with entities
+ collect { OPTIONAL MATCH p=(c:Chunk)-[:HAS_ENTITY]->(e)-[*0..1]-(:!Chunk) RETURN p }`;

export const docChunkEntities = `+[chunks]
+collect {MATCH p=(c)-[:FIRST_CHUNK]-() RETURN p} //first chunk
+ collect { MATCH p=(c)-[:NEXT_CHUNK]-() RETURN p } // chunk-chain
+ collect { MATCH p=(c)-[:SIMILAR]-() RETURN p } // similar-chunks
//chunks with entities
+ collect { OPTIONAL MATCH p=(c:Chunk)-[:HAS_ENTITY]->(e)-[*0..1]-(:!Chunk) RETURN p }`;

export const nvlOptions: NvlOptions = {
  allowDynamicMinZoom: true,
  disableWebGL: true,
  maxZoom: 3,
  minZoom: 0.05,
  relationshipThreshold: 0.55,
  useWebGL: false,
  instanceId: 'graph-preview',
  initialZoom: 1,
};

export const queryMap: {
  Document: string;
  Chunks: string;
  Entities: string;
  DocEntities: string;
  DocChunks: string;
  ChunksEntities: string;
  DocChunkEntities: string;
} = {
  Document: 'document',
  Chunks: 'chunks',
  Entities: 'entities',
  DocEntities: 'docEntities',
  DocChunks: 'docChunks',
  ChunksEntities: 'chunksEntities',
  DocChunkEntities: 'docChunkEntities',
};

// export const graphQuery: string = queryMap.DocChunkEntities;
export const graphView: OptionType[] = [
  { label: 'レキシカルグラフ', value: queryMap.DocChunks },
  { label: 'エンティティグラフ', value: queryMap.Entities },
  { label: 'ナレッジグラフ', value: queryMap.DocChunkEntities },
];

export const intitalGraphType = (isGDSActive: boolean): GraphType[] => {
  return isGDSActive
    ? ['DocumentChunk', 'Entities', 'Communities'] // GDS is active, include communities
    : ['DocumentChunk', 'Entities']; // GDS is inactive, exclude communities
};

export const graphLabels = {
  showGraphView: 'showGraphView',
  chatInfoView: 'chatInfoView',
  generateGraph: '生成されたグラフ',
  inspectGeneratedGraphFrom: '生成されたグラフの確認:',
  document: 'Document',
  chunk: 'Chunk',
  documentChunk: 'DocumentChunk',
  entities: 'エンティティ',
  resultOverview: '結果の概要',
  totalNodes: 'ノード総数',
  noEntities: 'エンティティが見つかりません',
  selectCheckbox: 'グラフ表示には少なくとも1つチェックを入れてください',
  totalRelationships: 'リレーションシップ総数',
  nodeSize: 30,
  docChunk: 'ドキュメント & チャンク',
  community: 'コミュニティ',
  noNodesRels: 'ノード・リレーションシップがありません',
  neighborView: 'neighborView',
  chunksInfo: '一度に50チャンクまで可視化しています',
  showSchemaView: 'showSchemaView',
  renderSchemaGraph: 'データベーススキーマのグラフ',
  generatedGraphFromUserSchema: 'ユーザー定義スキーマから生成されたグラフ',
};

export const RESULT_STEP_SIZE = 25;

export const connectionLabels = {
  notConnected: '未接続',
  graphDataScience: 'Graph Data Science',
  graphDatabase: 'Graph Database',
  greenStroke: 'green',
  redStroke: 'red',
};

export const getDefaultMessage = () => {
  return [{ ...chatbotmessages.listMessages[0], datetime: getDateTime() }];
};

export const appLabels = {
  ownSchema: 'または独自のスキーマを定義',
  predefinedSchema: '定義済みスキーマを選択',
  chunkingConfiguration: 'チャンク分割設定を選択',
  graphPatternTuple: 'グラフパターン',
  selectedPatterns: '選択中のパターン',
  dataImporterSchema: 'Data Importerのスキーマ',
};

export const LLMDropdownLabel = {
  disabledModels: '無効化されているモデルは開発版で利用できます。より多くのモデルはこちら: ',
  devEnv: '開発環境',
};
export const getDefaultSchemaExamples = () => {
  return schemaExamples.map((example) => ({
    label: example.schema,
    value: JSON.stringify(example.triplet),
  }));
};

export function mergeNestedObjects(objects: Record<string, Record<string, number>>[]) {
  return objects.reduce((merged, obj) => {
    for (const key in obj) {
      if (!merged[key]) {
        merged[key] = {};
      }
      for (const innerKey in obj[key]) {
        merged[key][innerKey] = obj[key][innerKey];
      }
    }
    return merged;
  }, {});
}
export function getStoredSchema() {
  const storedSchemas = localStorage.getItem('selectedSchemas');
  if (storedSchemas) {
    const parsedSchemas = JSON.parse(storedSchemas);
    return parsedSchemas.selectedOptions;
  }
  return [];
}
export const metricsinfo: Record<string, string> = {
  faithfulness: '回答が提供された情報をどれだけ正確に反映しているか',
  answer_relevancy: '回答がユーザーの質問にどれだけ適切に答えているか',
  rouge_score: '生成された回答が参照回答と語句レベルでどれだけ一致しているか',
  semantic_score: '生成された回答が参照回答の意味をどれだけ正しく捉えているか',
  context_entity_recall: '生成回答と取得コンテキストの両方に含まれるエンティティの再現率',
};
export const EXPIRATION_DAYS = 3;
export const SKIP_AUTH = (import.meta.env.VITE_SKIP_AUTH ?? 'true') == 'true';

export const sourceOptions: PatternOption[] = [{ label: 'Person', value: 'Person' }];
export const typeOptions: PatternOption[] = [{ label: 'WORKS_FOR', value: 'WORKS_FOR' }];
export const targetOptions: PatternOption[] = [{ label: 'Company', value: 'Company' }];

export const LOCAL_KEYS = {
  source: 'customSourceOptions',
  type: 'customTypeOptions',
  target: 'customTargetOptions',
};
