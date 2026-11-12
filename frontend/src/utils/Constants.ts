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
  参照追跡: 'graph_vector_fulltext_ref',
  'entity search+vector': 'entity_vector',
  unavailableChatMode: 'ファイルの選択中はチャットモードを変更できない',
  selected: '選択中',
  'global search+vector+fulltext': 'global_vector',
};
export const chatModeReadableLables: Record<string, string> = {
  vector: 'vector',
  graph: 'graph',
  graph_vector: 'graph+vector',
  fulltext: 'fulltext',
  graph_vector_fulltext: 'graph+vector+fulltext',
  graph_vector_fulltext_ref: '参照追跡',
  entity_vector: 'entity search+vector',
  unavailableChatMode: 'ファイルの選択中はチャットモードを変更できない',
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
        description: 'ベクトルインデックスを利用して、テキストチャンクの意味的類似検索を行う。',
      },
      {
        mode: chatModeLables.graph,
        description: '質問を Cypher クエリに変換し、グラフデータベースから正確にデータを取得する。',
      },
      {
        mode: chatModeLables['graph+vector'],
        description: 'ベクトルインデックスとグラフのリレーションを組み合わせ、文脈を強化した意味検索を行う。',
      },
      {
        mode: chatModeLables.fulltext,
        description: '全文インデックスを利用した高速なキーワード検索を行う。',
      },
      {
        mode: chatModeLables['graph+vector+fulltext'],
        description: 'ベクトル・グラフ・全文インデックスを統合し、網羅的な検索結果を提供する。',
      },
      {
        mode: chatModeLables.参照追跡,
        description: 'graph+vector+fulltext に、本文中の参照（項・別表・他文書）の追跡を加えた検索を行う。',
      },
      {
        mode: chatModeLables['entity search+vector'],
        description: 'エンティティノードのベクトルインデックスを利用し、エンティティ中心の検索を行う。',
      },
      {
        mode: chatModeLables['global search+vector+fulltext'],
        description: 'コミュニティノードのベクトル・全文インデックスを利用し、グラフ全体から文脈に即した回答を生成する。',
      },
    ];

export const chunkSize = import.meta.env.VITE_CHUNK_SIZE ? Number(import.meta.env.VITE_CHUNK_SIZE) : 1 * 1024 * 1024;
export const tokenchunkSize = import.meta.env.VITE_TOKENS_PER_CHUNK
  ? Number(import.meta.env.VITE_TOKENS_PER_CHUNK)
  : 512;
export const chunkOverlap = import.meta.env.VITE_CHUNK_OVERLAP ? Number(import.meta.env.VITE_CHUNK_OVERLAP) : 50;
export const chunksToCombine = import.meta.env.VITE_CHUNK_TO_COMBINE
  ? Number(import.meta.env.VITE_CHUNK_TO_COMBINE)
  : 1;
export const defaultTokenChunkSizeOptions = [256, 512, 1024];
export const defaultChunkOverlapOptions = [0, 25, 50, 100];
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
  // このリポジトリの既定（バックエンドの EMBEDDING_PROVIDER=vllm、EMBEDDING_MODEL=cl-nagoya/ruri-v3-310m）
  {
    provider: 'vllm',
    model: 'cl-nagoya/ruri-v3-310m',
    dimension: 768,
    label: 'vLLM cl-nagoya/ruri-v3-310m',
    value: 'vllm-cl-nagoya/ruri-v3-310m',
  },
];
export const DEFAULT_EMBEDDING_MODEL: EmbeddingModelOption = {
  provider: 'sentence-transformer',
  model: 'all-MiniLM-L6-v2',
  dimension: 384,
  label: 'Sentence Transformer all-MiniLM-L6-v2',
  value: 'sentence-transformer-all-MiniLM-L6-v2',
};
// 埋め込みモデルの表示用の選択肢。一覧に無いモデル（バックエンドの EMBEDDING_PROVIDER / EMBEDDING_MODEL）も設定どおりに表す。
// 既定（MiniLM）に落とすと、追加指示・チャンク分割の設定の「キャンセル」が保存済みの設定を MiniLM に書き換えてしまう
export const embeddingOptionOf = (config: {
  provider: string;
  model: string;
  dimension: number;
}): EmbeddingModelOption =>
  embeddingModels.find((opt) => opt.provider === config.provider && opt.model === config.model) ?? {
    provider: config.provider,
    model: config.model,
    dimension: config.dimension,
    label: `${config.provider} ${config.model}`,
    value: `${config.provider}-${config.model}`,
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
  provideAdditionalInstructions: 'エンティティ抽出の追加指示',
  analyzeInstructions: '指示を解析',
  helpInstructions: '重要トピックへの着目など、エンティティ抽出に関する具体的な指示を入力してください。',
  importDropzoneSpan: 'JSONドキュメント',
};

// 後処理のジョブ。title は /post_processing の tasks に送る名前で、変えない。label は画面の表示名。
// order はバックエンドが流す順番（backend/score.py の post_processing と同じ。backend/tests/test_post_processing_order.py で突き合わせる）
export type PostProcessingGroup = 'index' | 'layer' | 'llm';
export interface PostProcessingJob {
  title: string;
  label: string;
  group: PostProcessingGroup;
  order: number;
  uses_llm: boolean;
  description: string;
}
export const POST_PROCESSING_GROUPS: { id: PostProcessingGroup; label: string; description: string }[] = [
  {
    id: 'index',
    label: 'インデックス',
    description: '検索に使用するインデックスと埋め込みを作成する。LLM は使用しない。',
  },
  {
    id: 'layer',
    label: '層の構築',
    description: '文書と章・節の構造、由来情報と安定キーを規則により生成する。LLM は使用しない。',
  },
  {
    id: 'llm',
    label: 'LLM による整備',
    description: 'LLM を使用して、参照リンクの生成・スキーマの統合・節の要点の作成・コミュニティの生成を行う。処理に時間を要する。',
  },
];
export const POST_PROCESSING_JOBS: PostProcessingJob[] = [
  {
    title: 'link_chunk_references',
    label: 'チャンク間の参照リンク',
    group: 'llm',
    order: 1,
    uses_llm: true,
    description:
      'チャンクの本文から他の節・他の文書への参照（「〜による」「別に定める〜基準」「表-1 参照」など）を抽出し、参照元のチャンクから参照先のチャンクへ REFERS_TO を生成する。参照先の節を特定できない場合は、参照先の文書へ生成する。文書をまたぐ質問で、参照元の節が委ねている参照先を追跡できるようにする。生成方式はバックエンドの CHUNK_REFERENCE_MODE で決定され、llm（既定）と hybrid は LLM を使用する。文書層と章・節の層がこの結果を使用するため、最初に実行する。',
  },
  {
    title: 'graph_schema_consolidation',
    label: 'ラベルとリレーションタイプの統合',
    group: 'llm',
    order: 2,
    uses_llm: true,
    description:
      'LLM を使用して、大規模なグラフスキーマの多数のノードラベルとリレーションタイプを、より少数の適切なものに統合し、抽出済み・既存のグラフに適用する。グラフスキーマを設定している場合は選択できない。バックエンドの GRAPH_SCHEMA_CONSOLIDATION_ENABLED が false の場合は処理を行わない。',
  },
  {
    title: 'build_document_layer',
    label: '文書層',
    group: 'layer',
    order: 3,
    uses_llm: false,
    description:
      '文書層を規則のみで生成する（LLM は使用しない）。ファイル名と本文から文書番号ノード（Standard）を生成し、各文書を IS_VERSION_OF で文書番号ノードに関連付ける。同じ文書番号を指すエンティティは 1 つに統合し、文書間の参照 REFERS_TO・発行目録の LISTS・適用先の型式 APPLIES_TO を生成する。',
  },
  {
    title: 'merge_entity_variants',
    label: '表記ゆれの統合',
    group: 'layer',
    order: 4,
    uses_llm: false,
    description:
      '名前の違いが空白・ハイフン・大文字小文字・全角半角のみで、種類（ラベル）も同じエンティティを規則のみで 1 つに統合する（LLM は使用しない）。言及（HAS_ENTITY）の多いものを残し、リレーションは付け替え、重複するリレーションは 1 本にまとめ、統合した側の名前は残す側の別名（aliases）に追加する。種類が異なるもの、文書層の文書番号、手動の修正で別物として登録した組は統合しない。統合前の状態はバックエンドの logs/entity_variants に記録する。文書層の後に実行する。既定では実行しない。',
  },
  {
    title: 'build_section_layer',
    label: '章・節の層',
    group: 'layer',
    order: 5,
    uses_llm: false,
    description:
      '章・節の層を規則のみで生成する（LLM は使用しない）。分割時の見出しの経路から見出しノード（Section）の木構造を生成し、各チャンクを最も深い見出しに IN_SECTION で関連付け、参照リンクの参照先を章・節へ付け替える。文書への PART_OF は変更しない。',
  },
  {
    title: 'attach_provenance',
    label: '由来情報と安定キー',
    group: 'layer',
    order: 6,
    uses_llm: false,
    description:
      '由来情報と安定キーを規則のみで付与する（LLM は使用しない）。チャンクからエンティティへの HAS_ENTITY に、エンティティ名を含む文（mention）を付与する。各チャンクに、文書名・章・節の番号・節内の順位から成る安定キー（stable_key）を付与する。安定キーは変換器を替えてチャンク ID が変わっても、章・節と分割が同じであれば変わらない。エンティティ間のリレーションの根拠の文（evidence）は、既定の検索の文脈から除外する設定がある場合のみ付与する。章・節の層を使用するため、章・節の層の後に実行する。',
  },
  {
    title: 'build_section_summaries',
    label: '節の要点',
    group: 'llm',
    order: 7,
    uses_llm: true,
    description:
      '章・節の層の見出しノード（Section）ごとに、見出しの経路と本文の先頭から検索用の要点（1〜2 文・80 字以内）を LLM で作成し、Section の summary に書き込む。章は下位の節の要点も使用する。本文が変わっていない節は作り直さない（章・節の層を作り直した後も、控えから書き戻す）。参照追跡の検索で、文書のみを指す参照の参照先を節の要点の一覧から選択する場合（検索設定「文書のみを指す参照の参照先の選択方法」）に使用する。使用する LLM はバックエンドの SECTION_SUMMARY_MODEL（既定 gemma4）で決定される。章・節の層を使用するため、章・節の層の後に実行する。',
  },
  {
    title: 'materialize_text_chunk_similarities',
    label: 'チャンクの類似リンク',
    group: 'index',
    order: 8,
    uses_llm: false,
    description:
      '埋め込みが類似するチャンク同士を、k 近傍法と類似度のしきい値（バックエンドの KNN_MIN_SCORE）で選択し、SIMILAR で関連付ける。',
  },
  {
    title: 'enable_hybrid_search_and_fulltext_search_in_bloom',
    label: '全文インデックスとベクトルインデックス',
    group: 'index',
    order: 9,
    uses_llm: false,
    description:
      '全文インデックス（チャンクの本文、エンティティの名前と説明、コミュニティの要約）とベクトルインデックス（チャンク・エンティティ・コミュニティ）を再作成する。キーワード検索とハイブリッド検索に使用する。再作成中は、チャットの検索結果が欠落する場合がある。',
  },
  {
    title: 'materialize_entity_similarities',
    label: 'エンティティの埋め込み',
    group: 'index',
    order: 10,
    uses_llm: false,
    description:
      '埋め込みのないエンティティに埋め込みを付与する。エンティティの類似検索と重複ノードの候補抽出に使用する。文書層で統合したエンティティも対象とするため、文書層の後に実行する（バックエンドの ENTITY_EMBEDDING が true の場合のみ動作する）。',
  },
  {
    title: 'enable_communities',
    label: 'コミュニティの生成',
    group: 'llm',
    order: 11,
    uses_llm: true,
    description:
      'エンティティの集合（コミュニティ）を生成し、LLM で要約を付与する。GraphRAG のローカル検索・グローバル検索に使用する。GDS が有効な場合のみ選択できる。',
  },
];
export const RETRY_OPIONS = [
  'start_from_beginning',
  'delete_entities_and_start_from_beginning',
  'start_from_last_processed_position',
];
// 再処理の選び方の表示名と説明。キーは RETRY_OPIONS の値（バックエンドの retry_condition）
export const RETRY_OPTION_LABELS: Record<string, string> = {
  start_from_beginning: '最初から',
  delete_entities_and_start_from_beginning: 'エンティティを削除して最初から',
  start_from_last_processed_position: '続きから',
};
export const RETRY_OPTION_HELP: Record<string, string> = {
  start_from_beginning:
    'ファイルを再読込し、分割から再実行する。前の版のチャンク、その文書のチャンクからのみ参照されるエンティティ、規則により生成した章・節と文書層のリレーションは削除される（規則により生成した文書番号・型式のエンティティと、手動で修正したエンティティは保持される）。失敗・取り消しのファイルで選択できる。',
  delete_entities_and_start_from_beginning:
    'チャンクは保持し、この文書のチャンクからのみ参照されるエンティティを削除した後、すべてのチャンクで再抽出する。他の文書と共有するエンティティ、規則により生成した文書番号・型式のエンティティ、手動で修正したエンティティは保持される。失敗・取り消し以外のファイルでは、この選択肢のみ選択できる（抽出失敗チャンクのある完了済みのファイルでは「続きから」も選択できる）。',
  start_from_last_processed_position:
    'チャンクとエンティティを保持し、最後に処理したチャンクの位置から抽出を再開する。失敗・取り消しのファイルで選択できる。抽出失敗チャンク（抽出処理が例外で中断したチャンク。件数はファイル一覧のステータス列に表示される）のある完了済みのファイルでも選択でき、その場合は該当チャンクのみを再抽出する。再開できるのは前回と同じ抽出方式の場合のみである。抽出方式を変更する場合は、失敗・取り消しのファイルでは「最初から」または「エンティティを削除して最初から」、完了済みのファイルでは「エンティティを削除して最初から」を選択する。',
};
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
  // B7: 描画部品が利用状況を社外（Segment）へ送る設定を止める
  disableTelemetry: true,
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
  selectCheckbox: 'グラフを表示するには、表示対象を 1 つ以上選択してください',
  totalRelationships: 'リレーション総数',
  nodeSize: 30,
  docChunk: '文書・チャンク',
  community: 'コミュニティ',
  noNodesRels: 'ノード・リレーションがありません',
  neighborView: 'neighborView',
  chunksInfo: '対象文書のチャンクをすべて表示する',
  showSchemaView: 'showSchemaView',
  renderSchemaGraph: 'データベーススキーマのグラフ',
  generatedGraphFromUserSchema: 'ユーザー定義スキーマから生成されたグラフ',
};

// 凡例を畳むまでの件数。種類が多くても全部見えるよう大きくしている（本家は 25）
export const RESULT_STEP_SIZE = 500;

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
  chunkingConfiguration: 'チャンク分割の設定',
  graphPatternTuple: 'グラフパターン',
  selectedPatterns: '選択中のパターン',
  dataImporterSchema: 'Data Importerのスキーマ',
};

export const LLMDropdownLabel = {
  disabledModels: '無効化されているモデルは開発版で利用できる。その他のモデル: ',
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
  faithfulness: '回答が提供された情報をどの程度正確に反映しているか',
  answer_relevancy: '回答がユーザーの質問にどの程度適切に答えているか',
  rouge_score: '生成された回答が参照回答と語句レベルでどの程度一致しているか',
  semantic_score: '生成された回答が参照回答の意味をどの程度正しく捉えているか',
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
