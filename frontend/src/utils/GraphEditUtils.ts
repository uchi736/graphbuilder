import type { Relationship } from '@neo4j-nvl/base';
import {
  ExtendedNode,
  GraphEditCandidate,
  GraphEditLedgerRow,
  GraphEditRelationshipExpected,
  GraphEditRelationshipTarget,
  GraphEditSelection,
  GraphEditVocabulary,
  NeoNode,
  NeoRelationship,
  Scheme,
  UserCredentials,
} from '../types';
import { processGraphData } from './Utils';
import { RULE_METHOD } from './DocumentRelations';

// グラフの手動修正の画面で使う定数と純粋な関数（docs/graph-edit/DESIGN.md の 2 章と 6 章）

// 編集の切り替えを出す表示の種類
export const GRAPH_EDIT_VIEWPOINTS = ['showGraphView', 'tableView', 'chatInfoView'];

export const EMPTY_GRAPH_EDIT_SELECTION: GraphEditSelection = { nodeIds: [] };

// 予約の名前（バックエンドの /graph_edit/vocabulary が返す一覧と合わせて使う）
const DEFAULT_RESERVED_LABELS = [
  'Chunk',
  '__Community__',
  'Session',
  'Message',
  'SchemaMeta',
  'ProcessedChunk',
  'GraphProvenance',
  'CondFact',
  'Cond',
  '__GraphEdit__',
  '__GraphEditOp__',
  '__GraphLock__',
  // 本番の規則で作る層（章・節）と、取り込みが型の名前を付け替える予約の名前（backend の validate.RESERVED_LABELS と同じ）
  'Section',
  '__KGBuilder__',
];
const DEFAULT_RESERVED_RELATIONSHIP_TYPES = [
  'HAS_ENTITY',
  'PART_OF',
  'NEXT_CHUNK',
  'FIRST_CHUNK',
  'SIMILAR',
  'IN_COMMUNITY',
  'PARENT_COMMUNITY',
  'NEXT',
  'LAST_MESSAGE',
  'REFERS_TO',
  'ALIAS_OF',
  'WHEN',
  'MENTIONS',
  'HAS_SECTION',
  'IN_SECTION',
];
const ENTITY_LABEL = '__Entity__';
const DOCUMENT_LABEL = 'Document';
// 取り込みが型の名前を付け替える予約の名前（小文字。backend の common_fn.RESERVED_NODE_TYPES と同じ）。種類には選べない
const RESERVED_TYPE_NAMES_LOWER = [
  'document',
  'chunk',
  'session',
  'message',
  '__entity__',
  '__community__',
  '__kgbuilder__',
  'section',
];
// 規則で作ったノード（文書層の文書番号 Standard・型式 Model。method='rule'）。backend の validate.entity_block と同じ判定
const RULE_MADE_REASON =
  '規則により生成したノード（文書番号・型式）は後処理（文書層の生成）のたびに再生成されるため、名前・ラベル・プロパティの変更と削除はできない。統合先（名前とラベルは変更されない）とリレーションの端点には使用できる。';
export const isRuleMadeNode = (node?: { properties?: Record<string, any> } | null) =>
  node?.properties?.method === RULE_METHOD;

const LABEL_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const RELATIONSHIP_TYPE_PATTERN = /^[A-Z][A-Z0-9_]*$/;

// 右クリックの操作の一覧を、グラフ表示のダイアログ（z-index は modal）より手前に出す
export const GRAPH_EDIT_POPOVER_Z_INDEX = 'calc(var(--z-index-alias-modal) + 1)';

export interface ReservedNames {
  labels: Set<string>;
  relationshipTypes: Set<string>;
}

export const buildReservedNames = (vocabulary: GraphEditVocabulary | null): ReservedNames => ({
  labels: new Set([...DEFAULT_RESERVED_LABELS, ...(vocabulary?.reserved_labels ?? [])]),
  relationshipTypes: new Set([
    ...DEFAULT_RESERVED_RELATIONSHIP_TYPES,
    ...(vocabulary?.reserved_relationship_types ?? []),
  ]),
});

export const isSystemPropertyKey = (key: string) => key.startsWith('_');

// 操作の履歴に残す user。接続画面で入力したユーザー名、無ければログインのメール。
// バックエンドの環境変数で接続したとき（connection が backendApi）は両方とも空になりうる。
export const graphEditUser = (credentials: UserCredentials | null) => credentials?.userName || credentials?.email || '';

export const entityName = (node?: { properties?: Record<string, any> } | null): string => {
  const value = node?.properties?.id;
  return value === undefined || value === null ? '' : String(value);
};

export const visibleLabels = (labels: string[] = []) => labels.filter((label) => label !== ENTITY_LABEL);

// 説明（description）が文字列ならその値、それ以外は空文字
export const descriptionText = (node?: { properties?: Record<string, any> } | null): string => {
  const value = node?.properties?.description;
  return typeof value === 'string' ? value : '';
};

// 候補の表示名。名前が空なら要素 ID
export const candidateName = (candidate: GraphEditCandidate) => candidate.id || candidate.element_id;

export const toCandidate = (node: ExtendedNode | NeoNode): GraphEditCandidate => ({
  element_id: 'element_id' in node ? node.element_id : node.id,
  id: entityName(node),
  labels: visibleLabels(node.labels),
});

// 表示中のノードが編集の対象になりうるか。空文字なら対象、そうでなければ理由。
// 正しい判定はバックエンド（node_detail の editable）が持つ。ここでは表示を出し分けるだけ。
export const nodeEditBlockReason = (node: ExtendedNode | undefined, reserved: ReservedNames): string => {
  if (node === undefined) {
    return 'このノードは表示中のグラフに存在しない。';
  }
  const labels = node.labels ?? [];
  if (labels.some((label) => reserved.labels.has(label))) {
    return 'チャンク・章・節・コミュニティなどの管理用ノードは編集できない。編集できるのはエンティティのノードである。';
  }
  if (labels.includes(DOCUMENT_LABEL) && (node.properties?.fileName || labels.length === 1)) {
    return '文書のノードは編集できない。編集できるのはエンティティのノードである。';
  }
  if (isRuleMadeNode(node)) {
    return RULE_MADE_REASON;
  }
  return '';
};

// 統合（統合先として）とリレーションの端に使えるか。空文字なら使える（規則で作ったノードも使える）
export const nodeLinkBlockReason = (node: ExtendedNode | undefined, reserved: ReservedNames): string => {
  const reason = nodeEditBlockReason(node, reserved);
  return reason === RULE_MADE_REASON ? '' : reason;
};

export const relationshipEditBlockReason = (
  relationship: Relationship | undefined,
  source: ExtendedNode | undefined,
  target: ExtendedNode | undefined,
  reserved: ReservedNames
): string => {
  if (relationship === undefined) {
    return 'このリレーションは表示中のグラフに存在しない。';
  }
  if (reserved.relationshipTypes.has(relationship.caption ?? '')) {
    return `リレーションタイプ ${relationship.caption} は予約されているため、手動で変更できない。`;
  }
  if (nodeLinkBlockReason(source, reserved) !== '' || nodeLinkBlockReason(target, reserved) !== '') {
    return '編集できるのはエンティティ同士のリレーションのみである。';
  }
  return '';
};

// リレーションの修正・削除で送る、表示中の端点の名前と種類
export const relationshipExpected = (relationship: GraphEditRelationshipTarget): GraphEditRelationshipExpected => ({
  src: relationship.source.id,
  type: relationship.type,
  tgt: relationship.target.id,
});

export const toRelationshipTarget = (
  relationship: Relationship,
  source: ExtendedNode,
  target: ExtendedNode
): GraphEditRelationshipTarget => ({
  element_id: relationship.id,
  type: relationship.caption ?? '',
  source: toCandidate(source),
  target: toCandidate(target),
});

// react-select の選択肢（今のグラフ・EDC の語彙の 2 群）
export interface NameOption {
  value: string;
  label: string;
}

export interface NameOptionGroup {
  label: string;
  options: NameOption[];
}

const uniqueSorted = (values: string[]) =>
  [...new Set(values.filter((value) => value))].sort((a, b) => a.localeCompare(b));

const groupOptions = (current: string[], edc: string[], exclude: (name: string) => boolean): NameOptionGroup[] => {
  const currentNames = uniqueSorted(current).filter((name) => !exclude(name));
  const currentSet = new Set(currentNames);
  const edcNames = uniqueSorted(edc).filter((name) => !exclude(name) && !currentSet.has(name));
  const groups: NameOptionGroup[] = [];
  if (currentNames.length > 0) {
    groups.push({ label: '現在のグラフ', options: currentNames.map((name) => ({ value: name, label: name })) });
  }
  if (edcNames.length > 0) {
    groups.push({ label: 'EDC の語彙', options: edcNames.map((name) => ({ value: name, label: name })) });
  }
  return groups;
};

// 種類（ラベル）の選択肢。Document は今そのエンティティに付いているときだけ残せる（新たには付けられない）。
export const labelOptionGroups = (
  vocabulary: GraphEditVocabulary | null,
  reserved: ReservedNames,
  currentLabels: string[] = [],
  extraLabels: string[] = []
): NameOptionGroup[] => {
  const keepDocument = currentLabels.includes(DOCUMENT_LABEL);
  return groupOptions(
    [...(vocabulary?.labels ?? []), ...currentLabels, ...extraLabels],
    vocabulary?.edc_labels ?? [],
    (name) => name === ENTITY_LABEL || reserved.labels.has(name) || (name === DOCUMENT_LABEL && !keepDocument)
  );
};

export const relationshipTypeOptionGroups = (
  vocabulary: GraphEditVocabulary | null,
  reserved: ReservedNames
): NameOptionGroup[] =>
  groupOptions(vocabulary?.relationship_types ?? [], vocabulary?.edc_relationship_types ?? [], (name) =>
    reserved.relationshipTypes.has(name)
  );

export const validateLabels = (labels: string[], reserved: ReservedNames, currentLabels: string[] = []): string => {
  if (labels.length === 0) {
    return 'ラベルを 1 つ以上選択してください。';
  }
  const invalid = labels.filter((label) => !LABEL_NAME_PATTERN.test(label));
  if (invalid.length > 0) {
    return `ラベル名は、先頭を英字または _ とし、英数字と _ のみで入力してください：${invalid.join('、')}`;
  }
  const reservedHits = labels.filter((label) => label === ENTITY_LABEL || reserved.labels.has(label));
  if (reservedHits.length > 0) {
    return `予約されているラベルは選択できません：${reservedHits.join('、')}`;
  }
  const renamedHits = labels.filter(
    (label) =>
      RESERVED_TYPE_NAMES_LOWER.includes(label.toLowerCase()) &&
      label !== DOCUMENT_LABEL &&
      !currentLabels.includes(label)
  );
  if (renamedHits.length > 0) {
    return `取り込み時に名前が置き換えられる予約ラベルは選択できません：${renamedHits.join('、')}`;
  }
  if (labels.includes(DOCUMENT_LABEL) && !currentLabels.includes(DOCUMENT_LABEL)) {
    return 'ラベル Document は新たに付与できません。';
  }
  return '';
};

export const validateRelationshipType = (type: string, reserved: ReservedNames): string => {
  if (type === '') {
    return 'リレーションタイプを選択または入力してください。';
  }
  if (!RELATIONSHIP_TYPE_PATTERN.test(type)) {
    return 'リレーションタイプは、先頭を英大文字とし、英大文字・数字・_ のみで入力してください（例：APPLIES_TO）。';
  }
  if (reserved.relationshipTypes.has(type)) {
    return `リレーションタイプ ${type} は予約されているため選択できません。`;
  }
  return '';
};

// バックエンドが返すリレーション（graph_query と同じ形）を描画部品の形にする（processGraphData と同じ写し方）
const toDisplayRelationship = (relationship: NeoRelationship): Relationship => ({
  id: relationship.element_id,
  from: relationship.start_node_element_id,
  to: relationship.end_node_element_id,
  caption: relationship.type,
});

interface DisplayGraph {
  nodes: ExtendedNode[];
  relationships: Relationship[];
  scheme: Scheme;
}

export interface DisplayGraphChange {
  nodes?: NeoNode[];
  relationships?: NeoRelationship[];
  removeNodeIds?: string[];
  removeRelationshipIds?: string[];
  // このノードを端点に持つエンティティどうしのリレーションを、入ってきたもので置き換える
  replaceEntityRelationshipsOf?: string[];
}

const reservedRelationshipTypes = new Set(DEFAULT_RESERVED_RELATIONSHIP_TYPES);

// 表示の状態（全ノード・全リレーション・色）に、操作の結果を差し込む
export const mergeDisplayGraph = (current: DisplayGraph, change: DisplayGraphChange): DisplayGraph => {
  const { finalNodes, schemeVal } = processGraphData((change.nodes ?? []) as unknown as ExtendedNode[], []);
  const scheme: Scheme = { ...current.scheme };
  Object.entries(schemeVal).forEach(([label, color]) => {
    if (scheme[label] === undefined) {
      scheme[label] = color;
    }
  });
  const removedNodeIds = new Set(change.removeNodeIds ?? []);
  const incomingNodes = new Map(finalNodes.map((node) => [node.id, node]));
  const nodes = current.nodes
    .filter((node) => !removedNodeIds.has(node.id))
    .map((node) => {
      const replacement = incomingNodes.get(node.id);
      if (replacement === undefined) {
        return node;
      }
      incomingNodes.delete(node.id);
      return { ...node, ...replacement };
    });
  nodes.push(...incomingNodes.values());

  const nodeIds = new Set(nodes.map((node) => node.id));
  const removedRelationshipIds = new Set(change.removeRelationshipIds ?? []);
  const replaceOf = new Set(change.replaceEntityRelationshipsOf ?? []);
  const incomingRelationships = new Map(
    (change.relationships ?? []).map(toDisplayRelationship).map((relationship) => [relationship.id, relationship])
  );
  const isReplaced = (relationship: Relationship) =>
    replaceOf.size > 0 &&
    !reservedRelationshipTypes.has(relationship.caption ?? '') &&
    (replaceOf.has(relationship.from) || replaceOf.has(relationship.to)) &&
    !incomingRelationships.has(relationship.id);
  const relationships = current.relationships
    .filter((relationship) => !removedRelationshipIds.has(relationship.id) && !isReplaced(relationship))
    .map((relationship) => {
      const replacement = incomingRelationships.get(relationship.id);
      if (replacement === undefined) {
        return relationship;
      }
      incomingRelationships.delete(relationship.id);
      return { ...relationship, ...replacement };
    });
  relationships.push(...incomingRelationships.values());
  return {
    nodes,
    relationships: relationships.filter(
      (relationship) => nodeIds.has(relationship.from) && nodeIds.has(relationship.to)
    ),
    scheme,
  };
};

// 操作の履歴の op と、修正台帳の kind の日本語の名前
export const GRAPH_EDIT_OP_LABELS: Record<string, string> = {
  rename: '名前の変更',
  set_labels: 'ラベルの変更',
  update_props: 'プロパティの修正',
  merge: '統合',
  delete_nodes: 'エンティティの削除',
  create_relationship: 'リレーションの追加',
  delete_relationship: 'リレーションの削除',
  update_relationship: 'リレーションの修正',
  undo: '取り消し',
  ledger_delete: '修正台帳の行の削除',
  mark_distinct: '非同一指定の登録',
};

export const GRAPH_EDIT_LEDGER_KIND_LABELS: Record<string, string> = {
  alias: '別名',
  tombstone_node: '削除の記録（エンティティ）',
  tombstone_rel: '削除の記録（リレーション）',
  type_override: 'ラベルの固定',
  curated: '修正したプロパティ',
  manual_rel: '追加したリレーション',
  distinct: '非同一指定',
};

const listText = (value: any) => (Array.isArray(value) ? value.map(String).join('、') : String(value ?? ''));

// 修正台帳の 1 行の中身を、kind ごとに 1 行の日本語にする
export const describeLedgerRow = (row: GraphEditLedgerRow): string => {
  switch (row.kind) {
    case 'alias':
      return `${row.alias ?? ''} → ${row.target ?? ''}`;
    case 'tombstone_node':
      return `${row.name ?? ''} を作成しない`;
    case 'tombstone_rel':
      return `${row.src ?? ''} -[${row.type ?? ''}]-> ${row.tgt ?? ''} を作成しない`;
    case 'type_override':
      return `${row.entity ?? ''} のラベル：${listText(row.labels)}`;
    case 'curated':
      return `${row.entity ?? ''} の項目：${listText(row.keys)}`;
    case 'manual_rel':
      return `${row.src ?? ''} -[${row.type ?? ''}]-> ${row.tgt ?? ''}`;
    case 'distinct':
      return `${row.a ?? ''} と ${row.b ?? ''} は非同一`;
    default: {
      const rest = Object.fromEntries(
        Object.entries(row).filter(([key]) => !['uid', 'kind', 'created_at', 'op_id'].includes(key))
      );
      return JSON.stringify(rest);
    }
  }
};

export const formatGraphEditTime = (value?: string): string => {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ja-JP');
};

// 選択の切り替え（Ctrl か Shift を押しながらのクリックで追加・解除）
export const nextNodeSelection = (
  previous: GraphEditSelection,
  nodeId: string,
  additive: boolean
): GraphEditSelection => {
  if (!additive) {
    return { nodeIds: [nodeId] };
  }
  const exists = previous.nodeIds.includes(nodeId);
  return {
    nodeIds: exists ? previous.nodeIds.filter((id) => id !== nodeId) : [...previous.nodeIds, nodeId],
  };
};

// /graph_edit/rename などが CONFLICT のときの data.conflicts の各ノードと、その名前
export const conflictNodes = (data: any): any[] =>
  (Array.isArray(data?.conflicts) ? data.conflicts : []).map((item: any) => item?.node ?? item);

export const conflictNodeName = (node: any): string => String(node?.properties?.id ?? node?.id ?? '');

// data.conflicts を統合の候補に直す
export const conflictCandidates = (data: any): GraphEditCandidate[] =>
  conflictNodes(data)
    .filter((node) => node && typeof node.element_id === 'string')
    .map((node) => ({
      element_id: node.element_id,
      id: conflictNodeName(node),
      labels: visibleLabels(node.labels ?? []),
    }));
