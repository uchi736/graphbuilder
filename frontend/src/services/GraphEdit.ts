import api from '../API/Index';
import {
  GraphEditApiResponse,
  GraphEditDeleteResult,
  GraphEditDescriptionMode,
  GraphEditEntityHit,
  GraphEditErrorCode,
  GraphEditHistoryRow,
  GraphEditLedgerRow,
  GraphEditMergeResult,
  GraphEditNeighborhood,
  GraphEditNodeDetail,
  GraphEditNodeRef,
  GraphEditNodeResult,
  GraphEditOpResult,
  GraphEditPropertyValue,
  GraphEditRelationshipExpected,
  GraphEditRelationshipResult,
  GraphEditUidResult,
  GraphEditUndoResult,
  GraphEditVocabulary,
} from '../types';
import { conflictNodeName, conflictNodes } from '../utils/GraphEditUtils';

// グラフの手動修正の入口（docs/graph-edit/DESIGN.md の 3 章）。
// 接続情報（uri, userName, password, database）は API/Index の interceptor が付ける。

// 失敗のコードの日本語の文言。コードそのものはバックエンドの英語のまま比較に使う。
const GRAPH_EDIT_ERROR_MESSAGES: Record<GraphEditErrorCode, string> = {
  NOT_FOUND:
    '対象のエンティティまたはリレーションが見つかりません。他の操作で削除された可能性があります。表示を更新してください。',
  STALE:
    '表示中の内容が最新ではありません（他の操作で名前または版が変更されました）。表示を更新してから、もう一度操作してください。',
  CONFLICT: '同じ名前、または正規化すると同じになる名前のエンティティが既に存在します。',
  RESERVED: '予約されたラベル・リレーションタイプ・ノードは編集できません。',
  INVALID: '入力が正しくありません（名前の形式が不正、または項目が不足しています）。',
  BUSY: '文書の取り込み結果を保存中です。しばらく待ってから、もう一度操作してください。',
  NOT_UNDOABLE: 'この操作は取り消せません。',
  NETWORK: 'バックエンドとの通信に失敗しました。',
  UNKNOWN: '操作に失敗しました。',
};

// バックエンドが返すコード（NETWORK と UNKNOWN は画面側で付ける）
const KNOWN_ERROR_CODES: GraphEditErrorCode[] = (Object.keys(GRAPH_EDIT_ERROR_MESSAGES) as GraphEditErrorCode[]).filter(
  (code) => code !== 'NETWORK' && code !== 'UNKNOWN'
);

class GraphEditError extends Error {
  constructor(
    public readonly code: GraphEditErrorCode,
    public readonly detail: string,
    public readonly data?: any
  ) {
    super(detail ? `${code}: ${detail}` : code);
    this.name = 'GraphEditError';
  }
}

// バックエンドの error（"CODE: 詳細"）を GraphEditError に直す
const parseGraphEditError = (error?: string | null, data?: any): GraphEditError => {
  const text = (error ?? '').trim();
  const matched = /^([A-Z_]+)\s*:\s*([\s\S]*)$/.exec(text);
  if (matched && KNOWN_ERROR_CODES.includes(matched[1] as GraphEditErrorCode)) {
    return new GraphEditError(matched[1] as GraphEditErrorCode, matched[2].trim(), data);
  }
  if (KNOWN_ERROR_CODES.includes(text as GraphEditErrorCode)) {
    return new GraphEditError(text as GraphEditErrorCode, '', data);
  }
  return new GraphEditError('UNKNOWN', text, data);
};

const conflictNames = (data: any): string[] =>
  conflictNodes(data)
    .map(conflictNodeName)
    .filter((name) => name !== '');

// 画面に出す日本語の文。詳細（英語のことがある）は括弧に添える。
export const describeGraphEditError = (error: unknown): string => {
  if (error instanceof GraphEditError) {
    const parts = [GRAPH_EDIT_ERROR_MESSAGES[error.code]];
    if (error.code === 'CONFLICT') {
      // 正規化キーが同じ別のエンティティがある・他のエンティティの別名と同じ名前のときは、理由（日本語）が付く
      if (error.data?.reason) {
        parts.push(`理由: ${String(error.data.reason)}`);
      }
      const names = conflictNames(error.data);
      if (names.length > 0) {
        parts.push(`候補: ${names.join('、')}`);
      }
    }
    if ((error.code === 'NOT_UNDOABLE' || error.code === 'RESERVED') && error.data?.reason) {
      parts.push(`理由: ${String(error.data.reason)}`);
    }
    if (error.detail) {
      parts.push(`（詳細: ${error.detail}）`);
    }
    return parts.join(' ');
  }
  const message = error instanceof Error ? error.message : String(error ?? '');
  return message ? `${GRAPH_EDIT_ERROR_MESSAGES.UNKNOWN}（詳細: ${message}）` : GRAPH_EDIT_ERROR_MESSAGES.UNKNOWN;
};

// 既存の入口（/merge_duplicate_nodes など）が返す error の文字列を日本語にする
export const describeGraphEditFailure = (error?: string | null, data?: any): string =>
  describeGraphEditError(parseGraphEditError(error, data));

// 既存の入口を呼んで投げられた例外を日本語にする
export const describeCaughtGraphEditFailure = (error: unknown): string =>
  describeGraphEditFailure(error instanceof Error ? error.message : String(error));

export const isGraphEditError = (error: unknown, code: GraphEditErrorCode): error is GraphEditError =>
  error instanceof GraphEditError && error.code === code;

type FormValue = string | number | boolean | null | undefined;

const postGraphEdit = async <T>(path: string, params: Record<string, FormValue>): Promise<T> => {
  const formData = new FormData();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) {
      formData.append(key, String(value));
    }
  });
  let body: GraphEditApiResponse<T>;
  try {
    const response = await api.post<GraphEditApiResponse<T>>(`/graph_edit/${path}`, formData);
    body = response.data;
  } catch (error: any) {
    const status = error?.response?.status;
    const detail = status ? `HTTP ${status} ${path}` : String(error?.message ?? path);
    throw new GraphEditError('NETWORK', detail);
  }
  if (body?.status === 'Failed') {
    throw parseGraphEditError(body.error ?? body.message, body.data);
  }
  if (body?.status !== 'Success') {
    throw new GraphEditError('UNKNOWN', `unexpected response from ${path}`);
  }
  return body.data as T;
};

// 読み取り
export const getNodeDetail = (elementId: string) =>
  postGraphEdit<GraphEditNodeDetail>('node_detail', { element_id: elementId });

// 件数の上限はバックエンドの既定（20 件）
export const searchEntities = (q: string) => postGraphEdit<GraphEditEntityHit[]>('search_entities', { q });

export const getNeighborhood = (elementId: string) =>
  postGraphEdit<GraphEditNeighborhood>('neighborhood', { element_id: elementId });

export const getVocabulary = () => postGraphEdit<GraphEditVocabulary>('vocabulary', {});

// M1 名前の変更
export const renameEntity = (params: { element_id: string; expected_id: string; new_id: string; user: string }) =>
  postGraphEdit<GraphEditNodeResult>('rename', params);

// M2 種類（ラベル）の変更。user は 3 章の表に無いが、操作の履歴の user を埋めるために送る
export const setEntityLabels = (params: { element_id: string; expected_id: string; labels: string[]; user: string }) =>
  postGraphEdit<GraphEditNodeResult>('set_labels', { ...params, labels: JSON.stringify(params.labels) });

// M3 プロパティの修正（値 null は削除）。user は M2 と同じ扱い
export const updateEntityProps = (params: {
  element_id: string;
  expected_id: string;
  props: Record<string, GraphEditPropertyValue>;
  user: string;
}) => postGraphEdit<GraphEditNodeResult>('update_props', { ...params, props: JSON.stringify(params.props) });

// M4 統合
export const mergeEntities = (params: {
  target_element_id: string;
  target_expected_id: string;
  sources: GraphEditNodeRef[];
  keep_id: string;
  labels: string[];
  description_mode: GraphEditDescriptionMode;
  description: string;
  user: string;
}) =>
  postGraphEdit<GraphEditMergeResult>('merge', {
    ...params,
    sources: JSON.stringify(params.sources),
    labels: JSON.stringify(params.labels),
  });

// M5 エンティティの削除
export const deleteEntities = (params: { nodes: GraphEditNodeRef[]; suppress: boolean; user: string }) =>
  postGraphEdit<GraphEditDeleteResult>('delete_nodes', { ...params, nodes: JSON.stringify(params.nodes) });

// M6 リレーションの追加
export const createRelationship = (params: {
  source_element_id: string;
  source_expected_id: string;
  target_element_id: string;
  target_expected_id: string;
  type: string;
  user: string;
}) => postGraphEdit<GraphEditRelationshipResult>('create_relationship', params);

// M7 リレーションの削除
export const deleteRelationship = (params: {
  element_id: string;
  expected: GraphEditRelationshipExpected;
  suppress: boolean;
  user: string;
}) => postGraphEdit<GraphEditOpResult>('delete_relationship', { ...params, expected: JSON.stringify(params.expected) });

// M8 リレーションの種類・向きの修正
export const updateRelationship = (params: {
  element_id: string;
  expected: GraphEditRelationshipExpected;
  new_type?: string;
  reverse: boolean;
  user: string;
}) =>
  postGraphEdit<GraphEditRelationshipResult>('update_relationship', {
    ...params,
    expected: JSON.stringify(params.expected),
  });

// M9 操作の履歴と取り消し
export const getEditHistory = (params: { limit?: number; before_seq?: number } = {}) =>
  postGraphEdit<GraphEditHistoryRow[]>('history', params);

export const undoEditOperation = (params: { op_id: string; user: string }) =>
  postGraphEdit<GraphEditUndoResult>('undo', params);

// M10 修正台帳の行と、別物の登録
export const getLedgerRows = (params: { kind?: string; limit?: number; offset?: number } = {}) =>
  postGraphEdit<GraphEditLedgerRow[]>('ledger', params);

export const deleteLedgerRow = (params: { uid: string; user: string }) =>
  postGraphEdit<GraphEditUidResult>('ledger_delete', params);

export const markDistinct = (params: { a_element_id: string; b_element_id: string; user: string }) =>
  postGraphEdit<GraphEditUidResult>('mark_distinct', params);
