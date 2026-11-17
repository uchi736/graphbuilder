import type { Relationship } from '@neo4j-nvl/base';
import type { ExtendedNode, ExtendedRelationship, Scheme } from '../types';

// 全文書のグラフの表示範囲。all は全ノード（従来の表示）、documents は文書間の関係のみの表示である
export type DocumentViewScope = 'all' | 'documents';

// 集約したリレーションの種類。reference は文書間の参照、listing は発行目録への掲載、external は未取り込み文書への参照である
export type DocumentRelationKind = 'reference' | 'listing' | 'external';

// 描画用に生成するリレーション。properties は詳細欄に表示する項目名と値の組である
export interface DocumentRelationship extends ExtendedRelationship {
  kind: DocumentRelationKind;
  // 文書番号ノードを経由する 2 段のリレーションを集約したものかどうか（未取り込み文書へのリレーションは false）
  collapsed: boolean;
  properties: Record<string, string | number>;
}

interface DocumentRelationOptions {
  // 未取り込み文書（文書番号ノードのみが存在する文書）への参照を含めるかどうか
  includeExternal: boolean;
  // リレーションの要素 ID ごとのプロパティ（collectDocumentRelationProperties の結果）
  relationshipProperties?: Record<string, Record<string, unknown>>;
}

interface DocumentRelationGraph {
  nodes: ExtendedNode[];
  relationships: DocumentRelationship[];
}

interface DocumentRelationScope {
  nodeIds: Set<string>;
  relationshipIds: Set<string>;
}

// 生成したリレーションの id の接頭辞。データベースの要素 ID とは重複しない
const DOCUMENT_RELATION_ID_PREFIX = 'docrel';
// 未取り込み文書の文書番号ノードの色
export const EXTERNAL_DOCUMENT_COLOR = '#b9c1c9';
// 未取り込み文書の文書番号ノードの大きさ（半径）。描画部品の既定の大きさ（25）では既定の倍率で
// 文書番号を判読できないため、文書ノードの大きさ（40）より小さい範囲で大きくする
const EXTERNAL_DOCUMENT_SIZE = 36;
// 未取り込み文書の文書番号ノードの表示名の大きさ。描画部品はノードの表示名の大きさとして 1・2・3 のみに対応する
// （1.5 などの値では文字の大きさを算出できず、表示名を描画しない）。2 では文書番号が数字の途中で 2 行に分かれるため 1 とする
const EXTERNAL_DOCUMENT_CAPTION_SIZE = 1;
// 文書ノードの描画用の表示名から除く拡張子（取り込み対象のファイル形式に限る。「Node.js」「Ver.2」のような題名の末尾は除かない）
const DOCUMENT_EXTENSION = /\.(pdf|docx?|pptx?|xlsx?|txt|md|html?|csv|json)$/i;

// 種類ごとのリレーションタイプと描画の色・線幅。描画部品は破線に対応しないため、LISTS は色と線幅で区別する。
// 表示範囲の下に表示する種類ごとの件数の凡例も、この色と線幅を使用する
export const RELATION_STYLES: Record<DocumentRelationKind, { caption: string; color: string; width: number }> = {
  reference: { caption: 'REFERS_TO', color: '#1f5a78', width: 2 },
  listing: { caption: 'LISTS', color: '#8494a7', width: 1 },
  external: { caption: 'REFERS_TO', color: '#c9d1d8', width: 1 },
};

// 規則で生成したノードとリレーションの method の値
export const RULE_METHOD = 'rule';
// 文書カードの先頭行（例：「文書番号 HS-VTR-T002 版 F 過給機性能試験」）。リレーションのプロパティがない場合に版の記号を取得する
const CARD_HEADLINE = /^文書番号 (\S+) 版 (\S+)/;

interface CollapsedRelation {
  kind: DocumentRelationKind;
  from: string;
  to: string;
  numbers: string[];
  revisions: string[];
  listedRevisions: string[];
  listedDates: string[];
  chunks: number;
  quotes: string[];
}

interface DocumentVersion {
  documentId: string;
  revision?: string;
}

type PropertyRow = [string, string | number | undefined];

const isDocumentNode = (node: ExtendedNode): boolean =>
  (node.labels ?? []).includes('Document') && Boolean(node.properties?.fileName);

// 文書番号ノード（文書層の後処理が規則により生成した Standard）かどうか
const isDocumentNumberNode = (node: ExtendedNode): boolean =>
  (node.labels ?? []).includes('Standard') && node.properties?.method === RULE_METHOD;

const documentName = (node: ExtendedNode | undefined): string =>
  String(node?.properties?.fileName ?? node?.caption ?? node?.id ?? '');

// 文書ノードの描画用の表示名。拡張子を除いたファイル名とする（例：VTR-T004C.pdf → VTR-T004C）
const documentCaption = (node: ExtendedNode): string => documentName(node).replace(DOCUMENT_EXTENSION, '');

const numberName = (node: ExtendedNode | undefined): string =>
  String(node?.properties?.id ?? node?.caption ?? node?.id ?? '');

const textOf = (value: unknown): string | undefined => {
  if (typeof value === 'string') {
    return value.trim() === '' ? undefined : value.trim();
  }
  if (typeof value === 'number') {
    return String(value);
  }
  return undefined;
};

const numberOf = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0);

// 引用はチャンクの本文（Markdown）から切り出したものであるため、表示用に HTML のタグ・行頭の見出しの記号を除き、改行と空白をまとめる。
// タグは英字で始まる形のみを対象とし、不等号を含む本文（例：「Δp < 5 kPa かつ T > 300」）は除かない
const cleanQuote = (quote: string): string =>
  quote
    .replace(/<\/?[A-Za-z][A-Za-z0-9-]*(\s[^<>]*)?\/?>/g, '')
    .replace(/^\s*#{1,6}\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim();

const quotesOf = (value: unknown): string[] => {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((quote): quote is string => typeof quote === 'string')
    .map(cleanQuote)
    .filter((quote) => quote !== '');
};

const addUnique = (values: string[], value: string | undefined) => {
  if (value !== undefined && !values.includes(value)) {
    values.push(value);
  }
};

const joinValues = (values: string[]): string | undefined => (values.length > 0 ? values.join('、') : undefined);

// 文書カードの先頭行から版の記号を取得する。カードの文書番号が経由した文書番号と一致する場合に限る
const revisionFromCard = (documentNode: ExtendedNode | undefined, number: string): string | undefined => {
  const card = documentNode?.properties?.card;
  if (typeof card !== 'string') {
    return undefined;
  }
  const match = CARD_HEADLINE.exec(card);
  return match !== null && match[1] === number ? match[2] : undefined;
};

// 詳細欄に表示する項目名と値の組を生成する。値のない項目は除外する。
// 詳細欄の項目名の列は幅が固定（既定の欄の幅で約 110px）のため、項目名は 6 文字以内とする（7 文字は末尾が省略される）
const describeRelation = (
  entry: CollapsedRelation,
  nodeById: Map<string, ExtendedNode>,
  documentIds: Set<string>
): Record<string, string | number> => {
  const sourceName = documentName(nodeById.get(entry.from));
  const target = nodeById.get(entry.to);
  const quotes = entry.quotes.length > 0 ? entry.quotes.join(' ／ ') : undefined;
  const chunks = entry.chunks > 0 ? `${entry.chunks} 件` : undefined;
  let rows: PropertyRow[];
  switch (entry.kind) {
    case 'reference':
      rows = [
        ['区分', '文書間の参照'],
        ['参照元の文書', sourceName],
        ['参照先の文書', documentName(target)],
        ['文書番号', joinValues(entry.numbers)],
        ['参照先の版', joinValues(entry.revisions)],
        ['根拠チャンク', chunks],
        ['引用', quotes],
      ];
      break;
    case 'listing':
      rows = documentIds.has(entry.to)
        ? [
            ['区分', '発行目録への掲載'],
            ['目録', sourceName],
            ['掲載文書', documentName(target)],
            ['文書番号', joinValues(entry.numbers)],
            ['目録上の版', joinValues(entry.listedRevisions)],
            ['目録上の日付', joinValues(entry.listedDates)],
            ['掲載文書の版', joinValues(entry.revisions)],
          ]
        : [
            ['区分', '発行目録への掲載（未取り込み文書）'],
            ['目録', sourceName],
            ['文書番号', numberName(target)],
            ['目録上の版', joinValues(entry.listedRevisions)],
            ['目録上の日付', joinValues(entry.listedDates)],
          ];
      break;
    default:
      rows = [
        ['区分', '未取り込み文書への参照'],
        ['参照元の文書', sourceName],
        ['文書番号', numberName(target)],
        ['根拠チャンク', chunks],
        ['引用', quotes],
      ];
      break;
  }
  return Object.fromEntries(rows.filter((row): row is [string, string | number] => row[1] !== undefined));
};

// 文書で絞り込む範囲。選択した文書、選択した文書と直接接続する文書・文書番号ノード、選択した文書に接するリレーションを返す。
// 選択した文書が描画対象にない場合は null（絞り込みなし）を返す
export const documentRelationScope = (
  nodes: ExtendedNode[],
  relationships: Relationship[],
  documentFilter: string[] | undefined
): DocumentRelationScope | null => {
  const documentIds = new Set(nodes.filter(isDocumentNode).map((node) => node.id));
  const selected = new Set((documentFilter ?? []).filter((id) => documentIds.has(id)));
  if (selected.size === 0) {
    return null;
  }
  const nodeIds = new Set<string>(selected);
  const relationshipIds = new Set<string>();
  relationships.forEach((rel) => {
    if (selected.has(rel.from) || selected.has(rel.to)) {
      relationshipIds.add(rel.id);
      nodeIds.add(rel.from);
      nodeIds.add(rel.to);
    }
  });
  return { nodeIds, relationshipIds };
};

// 全文書のグラフのノードとリレーションから、文書間の関係のみを描画するためのノードとリレーションを生成する。
// 文書 -REFERS_TO-> 文書番号ノード <-IS_VERSION_OF- 文書 は文書間の REFERS_TO 1 本に、
// 目録 -LISTS-> 文書番号ノード <-IS_VERSION_OF- 文書 は目録から文書への LISTS 1 本に集約する。
// 取り込み済み文書の版がない文書番号ノード（未取り込み文書）は、includeExternal の場合に限り灰色のノードとして描画する
export const buildDocumentRelationGraph = (
  nodes: ExtendedNode[],
  relationships: Relationship[],
  options: DocumentRelationOptions
): DocumentRelationGraph => {
  const { includeExternal, relationshipProperties } = options;
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const documents = nodes.filter(isDocumentNode);
  const documentIds = new Set(documents.map((node) => node.id));
  const propertiesOf = (rel: Relationship): Record<string, unknown> => relationshipProperties?.[rel.id] ?? {};

  // 対象は始点が文書、終点が文書番号ノードの文書層のリレーションのみとする
  const versionsOf = new Map<string, DocumentVersion[]>();
  const ownNumbers = new Map<string, Set<string>>();
  const references: Relationship[] = [];
  const listings: Relationship[] = [];
  relationships.forEach((rel) => {
    const target = nodeById.get(rel.to);
    if (!documentIds.has(rel.from) || target === undefined || !isDocumentNumberNode(target)) {
      return;
    }
    const properties = propertiesOf(rel);
    switch (rel.caption) {
      case 'IS_VERSION_OF': {
        const revision = textOf(properties.rev) ?? revisionFromCard(nodeById.get(rel.from), numberName(target));
        versionsOf.set(rel.to, [...(versionsOf.get(rel.to) ?? []), { documentId: rel.from, revision }]);
        ownNumbers.set(rel.from, new Set([...(ownNumbers.get(rel.from) ?? []), rel.to]));
        break;
      }
      case 'REFERS_TO':
        // 規則により生成した REFERS_TO のみとする。プロパティを取得できない場合は始点と終点の種類で判定する
        if (properties.method === undefined || properties.method === RULE_METHOD) {
          references.push(rel);
        }
        break;
      case 'LISTS':
        listings.push(rel);
        break;
      default:
        break;
    }
  });

  // 未取り込み文書の文書番号ノード: external が true、または取り込み済み文書の版（IS_VERSION_OF）がないもの
  const isExternal = (numberId: string): boolean =>
    nodeById.get(numberId)?.properties?.external === true || !versionsOf.has(numberId);

  const collapsed = new Map<string, CollapsedRelation>();
  const collapse = (kind: DocumentRelationKind, from: string, to: string): CollapsedRelation => {
    const id = `${DOCUMENT_RELATION_ID_PREFIX}|${kind}|${from}|${to}`;
    const existing = collapsed.get(id);
    if (existing !== undefined) {
      return existing;
    }
    const created: CollapsedRelation = {
      kind,
      from,
      to,
      numbers: [],
      revisions: [],
      listedRevisions: [],
      listedDates: [],
      chunks: 0,
      quotes: [],
    };
    collapsed.set(id, created);
    return created;
  };
  // 文書番号ノードの先にある取り込み済み文書。自身の版は除外する（自己参照のリレーションを生成しない）
  const versionTargets = (numberId: string, sourceId: string): DocumentVersion[] =>
    (versionsOf.get(numberId) ?? []).filter((version) => version.documentId !== sourceId);

  // 文書番号ノードを経由するリレーションを集約する。未取り込み文書は externalKind、取り込み済み文書は ingestedKind とし、
  // recordDetail で種類ごとの項目（参照は根拠チャンクと引用、掲載は目録上の版と日付）を記録する
  const collapseVia = (
    rels: Relationship[],
    externalKind: DocumentRelationKind,
    ingestedKind: DocumentRelationKind,
    recordDetail: (entry: CollapsedRelation, properties: Record<string, unknown>) => void
  ) => {
    rels.forEach((rel) => {
      // 自身の文書番号へのリレーションは対象外とする
      if (ownNumbers.get(rel.from)?.has(rel.to)) {
        return;
      }
      const properties = propertiesOf(rel);
      const number = numberName(nodeById.get(rel.to));
      const record = (entry: CollapsedRelation, revision: string | undefined) => {
        addUnique(entry.numbers, number);
        addUnique(entry.revisions, revision);
        recordDetail(entry, properties);
      };
      if (isExternal(rel.to)) {
        if (includeExternal) {
          record(collapse(externalKind, rel.from, rel.to), undefined);
        }
        return;
      }
      versionTargets(rel.to, rel.from).forEach((version) => {
        record(collapse(ingestedKind, rel.from, version.documentId), version.revision);
      });
    });
  };
  collapseVia(references, 'external', 'reference', (entry, properties) => {
    entry.chunks += numberOf(properties.chunks);
    quotesOf(properties.quotes).forEach((quote) => addUnique(entry.quotes, quote));
  });
  collapseVia(listings, 'listing', 'listing', (entry, properties) => {
    addUnique(entry.listedRevisions, textOf(properties.rev));
    addUnique(entry.listedDates, textOf(properties.date));
  });

  const graphRelationships: DocumentRelationship[] = [...collapsed.entries()].map(([id, entry]) => {
    const style = RELATION_STYLES[entry.kind];
    return {
      id,
      from: entry.from,
      to: entry.to,
      type: style.caption,
      caption: style.caption,
      color: style.color,
      width: style.width,
      selected: false,
      kind: entry.kind,
      collapsed: documentIds.has(entry.to),
      properties: describeRelation(entry, nodeById, documentIds),
    };
  });

  // 描画するノードは全文書と、リレーションの終点になった未取り込み文書の文書番号ノード
  const linkedNumbers = new Set(graphRelationships.filter((rel) => !documentIds.has(rel.to)).map((rel) => rel.to));
  const graphNodes: ExtendedNode[] = [
    // 描画する表示名（captions）は拡張子を除き、円の中での途中の折り返しを避ける。caption はファイル名のまま残す（検索の照合に使用する）
    ...documents.map((node) => ({ ...node, selected: false, captions: [{ value: documentCaption(node) }] })),
    // 未取り込み文書の文書番号ノードは灰色とし、既定の倍率で文書番号を判読できる大きさで描画する
    ...nodes
      .filter((node) => linkedNumbers.has(node.id))
      .map((node) => ({
        ...node,
        selected: false,
        color: EXTERNAL_DOCUMENT_COLOR,
        size: EXTERNAL_DOCUMENT_SIZE,
        captionSize: EXTERNAL_DOCUMENT_CAPTION_SIZE,
      })),
  ];

  return { nodes: graphNodes, relationships: graphRelationships };
};

// 描画するノードの色からラベルの凡例の色を生成する
export const documentRelationScheme = (nodes: ExtendedNode[]): Scheme => {
  const scheme: Scheme = {};
  nodes.forEach((node) => {
    (node.labels ?? []).forEach((label) => {
      if (scheme[label] === undefined && typeof node.color === 'string') {
        scheme[label] = node.color;
      }
    });
  });
  return scheme;
};

// backend の応答のリレーションから、プロパティを要素 ID ごとに取り出す。
// backend がプロパティを返すのは文書層のリレーション（IS_VERSION_OF・REFERS_TO・LISTS で始点が文書のもの）のみである
export const collectDocumentRelationProperties = (
  rawRelationships: { element_id?: string; type?: string; properties?: Record<string, unknown> }[]
): Record<string, Record<string, unknown>> => {
  const result: Record<string, Record<string, unknown>> = {};
  rawRelationships.forEach((rel) => {
    if (typeof rel.element_id === 'string' && rel.properties !== undefined && rel.properties !== null) {
      result[rel.element_id] = rel.properties;
    }
  });
  return result;
};

// 集約したリレーション（buildDocumentRelationGraph が生成したもの）かどうか
export const isDocumentRelationship = (item: unknown): item is DocumentRelationship => {
  if (typeof item !== 'object' || item === null) {
    return false;
  }
  const { id } = item as { id?: unknown };
  return (
    typeof id === 'string' && id.startsWith(`${DOCUMENT_RELATION_ID_PREFIX}|`) && 'kind' in item && 'properties' in item
  );
};

// 描画中のリレーションの種類ごとの件数。結果の概要の凡例はリレーションタイプで集計し、文書間の参照と未取り込み文書への参照を
// 同じ REFERS_TO に合算するため、表示範囲の横に種類ごとの件数を別に表示する
export const countDocumentRelationKinds = (relationships: unknown[]): Record<DocumentRelationKind, number> => {
  const counts: Record<DocumentRelationKind, number> = { reference: 0, listing: 0, external: 0 };
  relationships.forEach((rel) => {
    if (isDocumentRelationship(rel) && rel.kind in counts) {
      counts[rel.kind] += 1;
    }
  });
  return counts;
};
