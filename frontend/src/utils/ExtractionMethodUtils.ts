import { capitalizeWithUnderscore } from './Utils';

// 抽出方式（標準の LLM 抽出か EDC か）。LLM の選択とは別に持つ
export const EXTRACTION_METHOD_STANDARD = 'standard';
export const EXTRACTION_METHOD_EDC = 'edc';
export const EXTRACTION_METHOD_STORAGE_KEY = 'selectedExtractionMethod';
// 抽出方式の表示名。backend の extraction_methods.LABELS と同じ値にする（backend から一覧を取得する前と、取得できない場合に使用する）
export const EXTRACTION_METHOD_LABELS: Record<string, string> = {
  [EXTRACTION_METHOD_STANDARD]: '標準',
  [EXTRACTION_METHOD_EDC]: 'EDC（スキーマ統制）',
};
// 一覧のモデル列の絞り込みで EDC の行だけを出す値（モデル名と重ならない値にする）
export const EDC_MODEL_FILTER = 'extraction_method:edc';
// まだ抽出していない行。これから使う方式（画面の選択）で出す
const PENDING_STATUSES = ['None', 'Uploading', 'New', 'Waiting', 'Ready to Reprocess'];
// LLM の名前として出さない値（旧来の model=edc と、EDC の LLM が分からなかったときの edc-default）
const UNKNOWN_MODELS = ['edc', 'edc-default'];

export const initialExtractionMethod = (): string => {
  return localStorage.getItem(EXTRACTION_METHOD_STORAGE_KEY) === EXTRACTION_METHOD_EDC
    ? EXTRACTION_METHOD_EDC
    : EXTRACTION_METHOD_STANDARD;
};

export const saveExtractionMethod = (method: string) => {
  localStorage.setItem(EXTRACTION_METHOD_STORAGE_KEY, method);
};

// EDC の初期スキーマの指定（取り込みごとに送る。空は backend の設定に従う）。抽出方式と同じくブラウザに保存する
export const EDC_OPTIONS_STORAGE_KEY = 'edcExtractionOptions';
export const EDC_SCHEMA_MODES = ['fixed', 'fixed_extended'] as const;
export type EdcSchemaMode = (typeof EDC_SCHEMA_MODES)[number];
export interface EdcExtractionOptions {
  // '' = backend の設定に従う、'auto' = 文書ごとに判定、'none' = 文書種別なし（語彙を使わず発見）、それ以外 = 文書種別の名前で固定
  doctype: string;
  // '' = backend の設定に従う
  schemaMode: '' | EdcSchemaMode;
}
export const EMPTY_EDC_OPTIONS: EdcExtractionOptions = { doctype: '', schemaMode: '' };

export const initialEdcOptions = (): EdcExtractionOptions => {
  try {
    const raw = localStorage.getItem(EDC_OPTIONS_STORAGE_KEY);
    if (!raw) {
      return EMPTY_EDC_OPTIONS;
    }
    const parsed = JSON.parse(raw) as Partial<EdcExtractionOptions>;
    const schemaMode = (EDC_SCHEMA_MODES as readonly string[]).includes(parsed.schemaMode ?? '')
      ? (parsed.schemaMode as EdcSchemaMode)
      : '';
    return { doctype: typeof parsed.doctype === 'string' ? parsed.doctype : '', schemaMode };
  } catch {
    return EMPTY_EDC_OPTIONS;
  }
};

export const saveEdcOptions = (options: EdcExtractionOptions) => {
  localStorage.setItem(EDC_OPTIONS_STORAGE_KEY, JSON.stringify(options));
};

// 抽出方式の表示名に補足を付与する。表示名が全角の括弧で終わる場合は、括弧を重ねずに括弧の中へ「、」で続ける
// 例: 標準 → 標準（LLM：gemma4）、EDC（スキーマ統制） → EDC（スキーマ統制、LLM：gemma4）
export const withMethodDetail = (label: string, detail: string): string => {
  if (label.endsWith('）')) {
    return `${label.slice(0, -1)}、${detail}）`;
  }
  return `${label}（${detail}）`;
};

// 行の抽出方式。未抽出の行は画面の選択、方式の記録が無い旧い行は model=edc なら EDC、ほかは標準
export const effectiveExtractionMethod = (
  row: { status?: string; extraction_method?: string; model?: string },
  selected: string
): string => {
  if (row.status && PENDING_STATUSES.includes(row.status)) {
    return selected;
  }
  if (row.extraction_method) {
    return row.extraction_method;
  }
  return (row.model ?? '').toLowerCase() === EXTRACTION_METHOD_EDC ? EXTRACTION_METHOD_EDC : EXTRACTION_METHOD_STANDARD;
};

// モデル列の表示。標準は LLM 名、EDC は「LLM 名 / EDC」。LLM が分からないときは「LLM 不明」
export const modelColumnLabel = (model: string | undefined, method: string): string => {
  const name = (model ?? '').trim();
  let shown = '';
  if (name !== '' && !UNKNOWN_MODELS.includes(name.toLowerCase())) {
    shown = capitalizeWithUnderscore(name);
  } else if (name !== '') {
    shown = 'LLM 不明';
  }
  return method === EXTRACTION_METHOD_EDC ? `${shown || 'LLM 不明'} / EDC` : shown;
};
