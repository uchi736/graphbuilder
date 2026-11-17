import { Banner, Dialog, Flex, IconButtonArray, LoadingSpinner, useDebounceValue } from '@neo4j-ndl/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BasicNode,
  BasicRelationship,
  EntityType,
  ExtendedNode,
  ExtendedRelationship,
  GraphEditContextMenuRequest,
  GraphEditSelection,
  GraphEditStructureChange,
  GraphType,
  GraphViewModalProps,
  OptionType,
  Scheme,
} from '../../types';
import { InteractiveNvlWrapper } from '@neo4j-nvl/react';
import NVL from '@neo4j-nvl/base';
import type { HitTargets, Node, Relationship } from '@neo4j-nvl/base';
import {
  ArrowPathIconOutline,
  FitToScreenIcon,
  InformationCircleIconOutline,
  MagnifyingGlassMinusIconOutline,
  MagnifyingGlassPlusIconOutline,
  ExploreIcon,
  PencilIconOutline,
} from '@neo4j-ndl/react/icons';
import { IconButtonWithToolTip } from '../UI/IconButtonToolTip';
import { filterData, getCheckboxConditions, graphTypeFromNodes, processGraphData } from '../../utils/Utils';
import { useCredentials } from '../../context/UserCredentials';

import { getGraphSchema, graphQueryAPI } from '../../services/GraphQuery';
import { graphLabels, nvlOptions, queryMap } from '../../utils/Constants';
import CheckboxSelection from './CheckboxSelection';

import ResultOverview from './ResultOverview';
import { ResizePanelDetails } from './ResizePanel';
import GraphPropertiesPanel from './GraphPropertiesPanel';
import SchemaViz from '../Graph/SchemaViz';
import { extractGraphSchemaFromRawData } from '../../utils/Utils';
import GraphEditPanel from './GraphEdit/GraphEditPanel';
import { getNeighborhood } from '../../services/GraphEdit';
import {
  DisplayGraphChange,
  EMPTY_GRAPH_EDIT_SELECTION,
  GRAPH_EDIT_VIEWPOINTS,
  mergeDisplayGraph,
  nextNodeSelection,
} from '../../utils/GraphEditUtils';
import {
  buildDocumentRelationGraph,
  collectDocumentRelationProperties,
  countDocumentRelationKinds,
  documentRelationScheme,
  documentRelationScope,
  DocumentViewScope,
  isDocumentRelationship,
} from '../../utils/DocumentRelations';
import DocumentScopeControl from './DocumentScopeControl';
import DocumentRelationPanel from './DocumentRelationPanel';

// 名前で検索したエンティティを追加した後、表示範囲を合わせるまでの待ち時間（新しいノードの配置が安定するまで）
const NEIGHBORHOOD_FIT_DELAY_MS = 800;
// 文書間の関係のみの表示の説明（ヘッダーの説明欄に表示する）。未取り込み文書への参照を表示しない場合と表示する場合で切り替える。
// 色の名前は DocumentRelations.ts の描画の色（RELATION_STYLES・EXTERNAL_DOCUMENT_COLOR）に対応する
const documentRelationsInfo = (includeExternal: boolean) =>
  `文書番号ノードを経由する参照を、文書間の矢印 1 本に集約して表示する。濃い青の矢印は文書間の参照（REFERS_TO）、${
    includeExternal ? '薄い灰色の矢印は未取り込み文書への参照（REFERS_TO）、' : ''
  }青灰色の矢印は発行目録への掲載（LISTS、発行目録から掲載文書への向き）${
    includeExternal ? '、灰色のノードは未取り込み文書の文書番号ノード' : ''
  }である。矢印をクリックすると、経由した文書番号と版を右側の欄に表示する。`;

const GraphViewModal: React.FunctionComponent<GraphViewModalProps> = ({
  open,
  inspectedName,
  setGraphViewOpen,
  viewPoint,
  nodeValues,
  relationshipValues,
  selectedRows,
  allDocuments,
}) => {
  const nvlRef = useRef<NVL>(null);
  const [node, setNode] = useState<ExtendedNode[]>([]);
  const [relationship, setRelationship] = useState<ExtendedRelationship[]>([]);
  const [allNodes, setAllNodes] = useState<ExtendedNode[]>([]);
  const [allRelationships, setAllRelationships] = useState<Relationship[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [status, setStatus] = useState<'unknown' | 'success' | 'danger'>('unknown');
  const [statusMessage, setStatusMessage] = useState<string>('');
  const { userCredentials, isReadOnlyUser } = useCredentials();
  const [scheme, setScheme] = useState<Scheme>({});
  const [newScheme, setNewScheme] = useState<Scheme>({});
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery] = useDebounceValue(searchQuery, 300);
  const [graphType, setGraphType] = useState<GraphType[]>([]);
  const [disableRefresh, setDisableRefresh] = useState<boolean>(false);
  const [selected, setSelected] = useState<{ type: EntityType; id: string } | undefined>(undefined);
  const [mode, setMode] = useState<boolean>(false);
  const graphQueryAbortControllerRef = useRef<AbortController>();
  const [openGraphView, setOpenGraphView] = useState<boolean>(false);
  const [schemaNodes, setSchemaNodes] = useState<OptionType[]>([]);
  const [schemaRels, setSchemaRels] = useState<OptionType[]>([]);
  const [viewCheck, setViewcheck] = useState<string>('enhancement');
  const [showLargeDatasetWarning, setShowLargeDatasetWarning] = useState<boolean>(false);
  // 編集モードの状態（docs/graph-edit/DESIGN.md の 6 章）。選択は React の状態として保持する。
  const [editMode, setEditMode] = useState<boolean>(false);
  const [editSelection, setEditSelection] = useState<GraphEditSelection>(EMPTY_GRAPH_EDIT_SELECTION);
  const [contextMenu, setContextMenu] = useState<GraphEditContextMenuRequest | null>(null);
  const canEdit = !isReadOnlyUser && GRAPH_EDIT_VIEWPOINTS.includes(viewPoint);
  // 操作の結果を反映する際に、非同期処理の完了後も最新の表示状態を参照するための複製
  const fitTimerRef = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(fitTimerRef.current), []);
  const latestGraphRef = useRef<{
    allNodes: ExtendedNode[];
    allRelationships: Relationship[];
    scheme: Scheme;
    graphType: GraphType[];
  }>({ allNodes: [], allRelationships: [], scheme: {}, graphType: [] });
  useEffect(() => {
    latestGraphRef.current = { allNodes, allRelationships, scheme, graphType };
  }, [allNodes, allRelationships, scheme, graphType]);
  // 編集モードで名前検索により追加したノード（要素 ID）。文書で絞り込んでいる場合も表示に含める
  const [editPinnedIds, setEditPinnedIds] = useState<string[]>([]);
  // 絞り込み表示: 凡例や検索で選択したノード（と隣接ノード）のみを描画する。false の場合は従来どおり全ノードを描画して強調表示する
  const [focusMode, setFocusMode] = useState<boolean>(true);
  const [includeNeighbors, setIncludeNeighbors] = useState<boolean>(false);
  // 文書で絞り込み: 選択した文書（Document ノードの id）と、そのチャンク・チャンクのエンティティのみを描画する。空の場合は全文書
  const [docFilter, setDocFilter] = useState<string[]>([]);
  // 表示範囲（全文書のグラフのみ）。documents の場合は文書間の関係のみを描画する
  const [viewScope, setViewScope] = useState<DocumentViewScope>('all');
  const [includeExternal, setIncludeExternal] = useState<boolean>(true);
  const relationsMode = Boolean(allDocuments) && viewPoint === graphLabels.showGraphView && viewScope === 'documents';
  // 文書間の関係のみの表示で描画するノードとリレーション。全ノードの表示の状態（node・relationship）とは別に保持する
  const [relationNodes, setRelationNodes] = useState<ExtendedNode[]>([]);
  const [relationRels, setRelationRels] = useState<ExtendedRelationship[]>([]);
  // 文書層のリレーションのプロパティ（版の記号など）。backend が返す場合にのみ保持する
  const relationshipPropsRef = useRef<Record<string, Record<string, unknown>>>({});
  const viewNodes = relationsMode ? relationNodes : node;
  const viewRels = relationsMode ? relationRels : relationship;
  const setViewNodes = relationsMode ? setRelationNodes : setNode;
  const setViewRels = relationsMode ? setRelationRels : setRelationship;
  const relationScheme = useMemo(() => documentRelationScheme(relationNodes), [relationNodes]);
  const scopeIds = useMemo(() => {
    if (docFilter.length === 0) {
      return null;
    }
    const docIds = new Set(docFilter);
    const chunkIds = new Set<string>();
    relationship.forEach((r) => {
      if (r.caption === 'PART_OF' && docIds.has(r.to)) {
        chunkIds.add(r.from);
      }
    });
    const ids = new Set<string>([...docIds, ...chunkIds]);
    relationship.forEach((r) => {
      if (r.caption === 'HAS_ENTITY' && chunkIds.has(r.from)) {
        ids.add(r.to);
      }
    });
    relationship.forEach((r) => {
      if (r.caption === 'IN_COMMUNITY' && ids.has(r.from)) {
        ids.add(r.to);
      }
    });
    // 段 3: 文書層（文書番号・型式のエンティティ）と章・節の層（Section の木）も、選んだ文書の範囲に含める
    relationship.forEach((r) => {
      if (docIds.has(r.from) && ['IS_VERSION_OF', 'REFERS_TO', 'LISTS', 'ABOLISHES', 'APPLIES_TO', 'REPLACES'].includes(r.caption ?? '')) {
        ids.add(r.to);
      }
    });
    let grew = true;
    while (grew) {
      grew = false;
      relationship.forEach((r) => {
        if (r.caption === 'HAS_SECTION' && ids.has(r.from) && !ids.has(r.to)) {
          ids.add(r.to);
          grew = true;
        }
      });
    }
    return ids;
  }, [docFilter, relationship]);
  // 文書間の関係のみの表示での文書による絞り込み（選択した文書に接するリレーションと、その端点のノード）
  const relationScope = useMemo(
    () => (relationsMode ? documentRelationScope(relationNodes, relationRels, docFilter) : null),
    [relationsMode, relationNodes, relationRels, docFilter]
  );
  const documentList = useMemo(() => {
    // 文書間の関係のみの表示では、描画している全文書を一覧にする（チャンク数は読み込んだ全リレーションから集計する）
    const docs = (relationsMode ? relationNodes : node).filter((n) => n.labels?.includes(graphLabels.document));
    const chunkCount: Record<string, number> = {};
    (relationsMode ? allRelationships : relationship).forEach((r) => {
      if (r.caption === 'PART_OF') {
        chunkCount[r.to] = (chunkCount[r.to] ?? 0) + 1;
      }
    });
    return docs
      .map((d) => ({
        id: d.id,
        name: String((d.properties as any)?.fileName ?? d.caption ?? d.id),
        chunks: chunkCount[d.id] ?? 0,
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [relationsMode, relationNodes.length, allRelationships.length, node.length, relationship.length]);
  const { visibleNodes, visibleRels } = useMemo(() => {
    const pinned = new Set(editPinnedIds);
    const nodeScope = relationsMode ? relationScope?.nodeIds : scopeIds;
    const inScope = (id: string) => !nodeScope || nodeScope.has(id) || pinned.has(id);
    const baseNodes = nodeScope ? viewNodes.filter((n) => inScope(n.id)) : viewNodes;
    // 文書間の関係のみの表示では、選択した文書に接するリレーションのみを描画する
    const baseRels = relationScope
      ? viewRels.filter((r) => relationScope.relationshipIds.has(r.id))
      : nodeScope
        ? viewRels.filter((r) => inScope(r.from) && inScope(r.to))
        : viewRels;
    const selNodes = baseNodes.filter((n) => n.selected);
    const selRels = baseRels.filter((r) => r.selected);
    if (!focusMode || (selNodes.length === 0 && selRels.length === 0)) {
      return { visibleNodes: baseNodes, visibleRels: baseRels };
    }
    if (selNodes.length > 0) {
      const selIds = new Set(selNodes.map((n) => n.id));
      const ids = new Set(selIds);
      if (includeNeighbors) {
        baseRels.forEach((r) => {
          if (selIds.has(r.from) || selIds.has(r.to)) {
            ids.add(r.from);
            ids.add(r.to);
          }
        });
      }
      return {
        visibleNodes: baseNodes.filter((n) => ids.has(n.id)),
        visibleRels: baseRels.filter((r) => ids.has(r.from) && ids.has(r.to)),
      };
    }
    const ids = new Set<string>();
    selRels.forEach((r) => {
      ids.add(r.from);
      ids.add(r.to);
    });
    return { visibleNodes: baseNodes.filter((n) => ids.has(n.id)), visibleRels: selRels };
  }, [viewNodes, viewRels, focusMode, includeNeighbors, scopeIds, relationScope, relationsMode, editPinnedIds]);
  useEffect(() => {
    if (focusMode && visibleNodes.length > 0 && visibleNodes.length < viewNodes.length) {
      const t = setTimeout(() => nvlRef.current?.fit(visibleNodes.map((n) => n.id), {}), 300);
      return () => clearTimeout(t);
    }
  }, [visibleNodes.length, focusMode, includeNeighbors, docFilter]);
  // 選択と検索語を解除する。ノードの大きさは変えない（文書ノードの大きさ 40 を維持する。どちらの表示範囲でも同じ）
  const clearSelection = () => {
    setSearchQuery('');
    setViewNodes((prev) => prev.map((n) => ({ ...n, selected: false })));
    setViewRels((prev) => prev.map((r) => ({ ...r, selected: false })));
  };

  const graphQuery: string =
    graphType.includes('DocumentChunk') && graphType.includes('Entities')
      ? queryMap.DocChunkEntities
      : graphType.includes('DocumentChunk')
        ? queryMap.DocChunks
        : graphType.includes('Entities')
          ? queryMap.Entities
          : '';

  // fit graph to original position
  const handleZoomToFit = () => {
    nvlRef.current?.fit(
      allNodes.map((node) => node.id),
      {}
    );
  };
  // Unmounting the component
  useEffect(() => {
    const timeoutId = setTimeout(() => {
      handleZoomToFit();
    }, 10);
    return () => {
      if (nvlRef.current) {
        nvlRef.current?.destroy();
      }
      setGraphType([]);
      clearTimeout(timeoutId);
      setScheme({});
      setNode([]);
      setRelationship([]);
      setAllNodes([]);
      setAllRelationships([]);
      setSearchQuery('');
      setSelected(undefined);
    };
  }, []);

  useEffect(() => {
    let updateGraphType;
    if (mode) {
      updateGraphType = graphTypeFromNodes(node);
    } else {
      updateGraphType = graphTypeFromNodes(allNodes);
    }
    if (Array.isArray(updateGraphType)) {
      setGraphType(updateGraphType);
    }
  }, [allNodes]);

  const fetchData = useCallback(async () => {
    graphQueryAbortControllerRef.current = new AbortController();
    try {
      let nodeRelationshipData;
      if (viewPoint === graphLabels.showGraphView) {
        nodeRelationshipData = await graphQueryAPI(
          graphQuery,
          selectedRows?.map((f) => f.name),
          graphQueryAbortControllerRef.current.signal,
          !!allDocuments
        );
      } else if (viewPoint === graphLabels.showSchemaView) {
        nodeRelationshipData = await getGraphSchema();
      } else {
        nodeRelationshipData = await graphQueryAPI(
          graphQuery,
          [inspectedName ?? ''],
          graphQueryAbortControllerRef.current.signal
        );
      }
      return nodeRelationshipData;
    } catch (error: any) {
      if (error.code === 'ERR_CANCELED' || error.name === 'CanceledError' || error.name === 'AbortError') {
        const cancelErr = new Error('Request was cancelled.');
        (cancelErr as any).isCancelled = true;
        throw cancelErr;
      }
      console.error('Error fetching graph data:', error);
      if (error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
        throw new Error(
          'リクエストがタイムアウトしました。データ量が多く、プレビューできない可能性があります。選択する文書を絞り込んで再度お試しください。'
        );
      }
      throw new Error(
        error.response?.data?.error || error.message || 'グラフデータの取得に失敗しました。もう一度お試しください。'
      );
    }
  }, [viewPoint, selectedRows, graphQuery, inspectedName, userCredentials, allDocuments]);

  // Api call to get the nodes and relations
  const graphApi = async (mode?: string) => {
    try {
      const result = await fetchData();

      // Validate the response structure
      if (!result) {
        throw new Error('サーバーから応答がありません。リクエストがタイムアウトした可能性があります。');
      }

      if (!result.data) {
        throw new Error('サーバーから不正な形式の応答を受信しました。');
      }

      if (!result.data.data) {
        throw new Error(`応答にデータが含まれていません。${result.data.error || 'もう一度お試しください。'}`);
      }

      const { nodes = [], relationships = [] } = result.data.data;

      if (nodes.length === 0) {
        setLoading(false);
        setStatus('danger');
        const documentCount = viewPoint === graphLabels.showGraphView ? selectedRows?.length || 0 : 1;
        setStatusMessage(
          documentCount > 1
            ? `選択した ${documentCount} 件の文書にグラフデータが見つかりませんでした。文書が処理中であるか、抽出可能なエンティティが含まれていない可能性があります。`
            : `${inspectedName} のグラフデータが見つかりませんでした。文書が処理中であるか、抽出可能なエンティティが含まれていない可能性があります。`
        );
        return;
      }

      const neoNodes = nodes;
      const nodeIds = new Set(neoNodes.map((node: any) => node.element_id));
      const neoRels = relationships
        .map((f: Relationship) => f)
        .filter((rel: any) => nodeIds.has(rel.end_node_element_id) && nodeIds.has(rel.start_node_element_id));
      const { finalNodes, finalRels, schemeVal } = processGraphData(neoNodes, neoRels);
      // processGraphData はリレーションのプロパティを保持しないため、文書層のリレーションのプロパティを別に保持する
      relationshipPropsRef.current = collectDocumentRelationProperties(neoRels);

      if (mode === 'refreshMode') {
        initGraph(graphType, finalNodes, finalRels, schemeVal);
      } else {
        setNode(finalNodes);
        setRelationship(finalRels);
        setNewScheme(schemeVal);
        setLoading(false);
      }
      setAllNodes(finalNodes);
      setAllRelationships(finalRels);
      setScheme(schemeVal);
      latestGraphRef.current = {
        ...latestGraphRef.current,
        allNodes: finalNodes,
        allRelationships: finalRels,
        scheme: schemeVal,
      };
      setDisableRefresh(false);
      setShowLargeDatasetWarning(false);
    } catch (error: any) {
      if (error?.isCancelled) {
        setLoading(false);
        return;
      }
      console.error('Graph API error:', error);
      setLoading(false);
      setStatus('danger');
      setStatusMessage(error.message || 'グラフの読み込み中に予期しないエラーが発生しました。');
    }
  };

  useEffect(() => {
    if (open) {
      setLoading(true);
      setStatus('unknown');
      setStatusMessage('');
      setGraphType([]);
      setNode([]);
      setRelationship([]);
      setAllNodes([]);
      setAllRelationships([]);
      setScheme({});
      setDocFilter([]);
      setEditPinnedIds([]);
      setViewScope('all');
      setIncludeExternal(true);
      setRelationNodes([]);
      setRelationRels([]);
      relationshipPropsRef.current = {};
      if (viewPoint === graphLabels.showGraphView && selectedRows && selectedRows.length > 8 && !allDocuments) {
        setShowLargeDatasetWarning(true);
      }
      if (viewPoint !== graphLabels.chatInfoView) {
        graphApi();
      } else {
        const { finalNodes, finalRels, schemeVal } = processGraphData(nodeValues ?? [], relationshipValues ?? []);
        setAllNodes(finalNodes);
        setAllRelationships(finalRels);
        setScheme(schemeVal);
        setNode(finalNodes);
        setRelationship(finalRels);
        setNewScheme(schemeVal);
        setLoading(false);
      }
    }
  }, [open]);

  useEffect(() => {
    const query = debouncedQuery.toLowerCase();
    const updatedNodes = viewNodes.map((nodeVal) => {
      // 検索語が空の場合は選択のみを解除し、ノードの大きさは変えない（文書ノードの大きさを維持する）
      if (query === '') {
        return {
          ...nodeVal,
          selected: false,
        };
      }
      const { id, properties, caption } = nodeVal;
      const propertiesMatch = properties?.id?.toLowerCase().includes(query);
      const match = id.toLowerCase().includes(query) || propertiesMatch || caption?.toLowerCase().includes(query);

      if (match) {
        console.log({ id, caption });
      }
      return {
        ...nodeVal,
        selected: match,
      };
    });
    const matchedNodes = updatedNodes.filter((n) => n.selected);
    console.log(`Total matches: ${matchedNodes.length} out of ${viewNodes.length} nodes`);
    const updatedRelationships = viewRels.map((rel) => {
      return {
        ...rel,
        selected: false,
      };
    });
    setViewNodes(updatedNodes);
    setViewRels(updatedRelationships);
  }, [debouncedQuery]);

  const mouseEventCallbacks = useMemo(
    () => ({
      onNodeClick: (clickedNode: Node, _hitTargets: HitTargets, event: MouseEvent) => {
        if (editMode) {
          const additive = event.ctrlKey || event.metaKey || event.shiftKey;
          setEditSelection((previous) => nextNodeSelection(previous, clickedNode.id, additive));
          setContextMenu(null);
          return;
        }
        if (selected?.id !== clickedNode.id || selected?.type !== 'node') {
          setSelected({ type: 'node', id: clickedNode.id });
        }
      },
      onRelationshipClick: (clickedRelationship: Relationship) => {
        if (editMode) {
          setEditSelection({ nodeIds: [], relationshipId: clickedRelationship.id });
          setContextMenu(null);
          return;
        }
        if (selected?.id !== clickedRelationship.id || selected?.type !== 'relationship') {
          setSelected({ type: 'relationship', id: clickedRelationship.id });
        }
      },
      onCanvasClick: () => {
        if (editMode) {
          setEditSelection(EMPTY_GRAPH_EDIT_SELECTION);
          setContextMenu(null);
          return;
        }
        if (selected !== undefined) {
          setSelected(undefined);
        }
      },
      onNodeRightClick: (clickedNode: Node, _hitTargets: HitTargets, event: MouseEvent) => {
        if (editMode) {
          setContextMenu({ x: event.clientX, y: event.clientY, type: 'node', id: clickedNode.id });
        }
      },
      onRelationshipRightClick: (clickedRelationship: Relationship, _hitTargets: HitTargets, event: MouseEvent) => {
        if (editMode) {
          setContextMenu({ x: event.clientX, y: event.clientY, type: 'relationship', id: clickedRelationship.id });
        }
      },
      onPan: true,
      onZoom: true,
      onDrag: true,
    }),
    [selected, editMode]
  );

  // 4 項目すべてを明示する。描画部品は作成時にのみ読み込むため、編集の切り替えでは描画部品を再作成する（key）。
  const interactionOptions = useMemo(
    () => ({
      selectOnClick: !editMode,
      drawShadowOnHover: false,
      selectOnRelease: false,
      excludeNodeMargin: false,
    }),
    [editMode]
  );

  const editNodeIds = useMemo(() => new Set(editSelection.nodeIds), [editSelection.nodeIds]);
  // 描画するのは絞り込み（文書・凡例・検索）の結果。編集モードでは、選択の表示のみを編集の選択で上書きする
  const displayNodes = useMemo(
    () =>
      (editMode
        ? visibleNodes.map((nodeVal) => ({ ...nodeVal, selected: editNodeIds.has(nodeVal.id) }))
        : visibleNodes),
    [editMode, visibleNodes, editNodeIds]
  );
  const displayRelationships = useMemo(
    () =>
      (editMode
        ? visibleRels.map((rel) => ({ ...rel, selected: rel.id === editSelection.relationshipId }))
        : visibleRels),
    [editMode, visibleRels, editSelection.relationshipId]
  );

  // 渡したデータで表示を差し替える（リレーションが 0 件でも差し替える。統合や削除の後の取り直しで 0 件になりうる）
  const initGraph = (
    graphType: GraphType[],
    finalNodes: ExtendedNode[],
    finalRels: Relationship[],
    schemeVal: Scheme
  ) => {
    const { filteredNodes, filteredRelations, filteredScheme } = filterData(
      graphType,
      finalNodes ?? [],
      finalRels ?? [],
      schemeVal
    );
    setNode(filteredNodes);
    setRelationship(filteredRelations);
    setNewScheme(filteredScheme);
  };

  // 編集の操作の結果を表示の状態（node と allNodes、relationship と allRelationships、scheme）に差し込む
  const applyDisplayChange = (change: DisplayGraphChange) => {
    const { current } = latestGraphRef;
    const next = mergeDisplayGraph(
      { nodes: current.allNodes, relationships: current.allRelationships, scheme: current.scheme },
      change
    );
    latestGraphRef.current = {
      ...current,
      allNodes: next.nodes,
      allRelationships: next.relationships,
      scheme: next.scheme,
    };
    setAllNodes(next.nodes);
    setAllRelationships(next.relationships);
    setScheme(next.scheme);
    if (viewPoint === graphLabels.chatInfoView) {
      setNode(next.nodes);
      setRelationship(next.relationships);
      setNewScheme(next.scheme);
      return;
    }
    initGraph(current.graphType, next.nodes, next.relationships, next.scheme);
  };

  // 名前で探したエンティティと 1 ホップ先を表示に加える（replace のときは、そのエンティティのリレーションを置き換える）
  const showEntityNeighborhood = async (elementId: string, replace = false) => {
    const neighborhood = await getNeighborhood(elementId);
    applyDisplayChange({
      nodes: neighborhood.nodes,
      relationships: neighborhood.relationships,
      replaceEntityRelationshipsOf: replace ? [elementId] : undefined,
    });
    // 追加したエンティティと 1 ホップ先に表示範囲を合わせる。NVL はノードを後から追加しても全体表示を再実行しないため、
    // 表示範囲を合わせない場合は画面の外に配置されることがある。新しいノードの配置が安定するまで待機する。
    const nodeIds = [elementId, ...neighborhood.nodes.map((node) => node.element_id)];
    setEditPinnedIds((previous) => [...new Set([...previous, ...nodeIds])]);
    clearTimeout(fitTimerRef.current);
    fitTimerRef.current = setTimeout(() => {
      nvlRef.current?.fit([...new Set(nodeIds)], {});
    }, NEIGHBORHOOD_FIT_DELAY_MS);
  };

  // 要素 ID が変わる操作（統合・削除・付け替え）の後に表示を再取得する。
  // tableView・showGraphView は既存の更新処理、chatInfoView は返却値を反映し、残存するエンティティを neighborhood で再取得する。
  const handleStructureChange = async (change: GraphEditStructureChange) => {
    const isChatInfoView = viewPoint === graphLabels.chatInfoView;
    if (isChatInfoView) {
      applyDisplayChange({
        nodes: change.changedNodes,
        relationships: change.addedRelationships,
        removeNodeIds: change.removedNodeIds,
        removeRelationshipIds: change.removedRelationshipIds,
      });
    } else {
      setDisableRefresh(true);
      setMode(true);
      await graphApi('refreshMode');
    }
    if (change.survivorElementId) {
      await showEntityNeighborhood(change.survivorElementId, isChatInfoView);
    }
  };

  const resetEditState = () => {
    setEditSelection(EMPTY_GRAPH_EDIT_SELECTION);
    setContextMenu(null);
    setEditPinnedIds([]);
  };

  const toggleEditMode = () => {
    setEditMode((previous) => !previous);
    resetEditState();
    setSelected(undefined);
    setSearchQuery('');
  };

  // 文書間の関係のみの表示の描画データを、読み込んだ全ノードと全リレーションから生成する
  const deriveRelationGraph = (external: boolean) => {
    const graph = buildDocumentRelationGraph(allNodes, allRelationships, {
      includeExternal: external,
      relationshipProperties: relationshipPropsRef.current,
    });
    setRelationNodes(graph.nodes);
    setRelationRels(graph.relationships);
  };

  // 表示範囲の切り替え。切り替え前の表示の選択と検索語を解除し（ノードの大きさは変えない）、
  // 文書間の関係のみに切り替える場合は編集モードを終了する
  const handleViewScopeChange = (scope: DocumentViewScope) => {
    if (scope === viewScope) {
      return;
    }
    clearSelection();
    setSelected(undefined);
    if (scope === 'documents') {
      setEditMode(false);
      resetEditState();
      deriveRelationGraph(includeExternal);
    }
    setViewScope(scope);
  };

  const handleIncludeExternalChange = (value: boolean) => {
    clearSelection();
    setSelected(undefined);
    setIncludeExternal(value);
    if (relationsMode) {
      deriveRelationGraph(value);
    }
  };

  // グラフの再読込（更新ボタン）の後は、文書間の関係のみの表示の描画データも再生成する
  useEffect(() => {
    if (relationsMode) {
      deriveRelationGraph(includeExternal);
    }
  }, [allNodes, allRelationships]);

  const selectedItem = useMemo(() => {
    if (selected === undefined) {
      return undefined;
    }
    if (selected.type === 'node') {
      return viewNodes.find((nodeVal) => nodeVal.id === selected.id);
    }
    return viewRels.find((relationshipVal) => relationshipVal.id === selected.id);
  }, [selected, viewRels, viewNodes]);

  if (!open) {
    return <></>;
  }

  const headerTitle =
    viewPoint === graphLabels.showGraphView || viewPoint === graphLabels.chatInfoView
      ? viewPoint === graphLabels.showGraphView && allDocuments
        ? `全文書のグラフ（${selectedRows?.length ?? 0} 件）`
        : graphLabels.generateGraph
      : viewPoint === graphLabels.showSchemaView
        ? graphLabels.renderSchemaGraph
        : `${graphLabels.inspectGeneratedGraphFrom} ${inspectedName}`;

  const checkBoxView = viewPoint !== graphLabels.chatInfoView;
  // 文書間の関係のみの表示の説明。未取り込み文書への参照の表示の有無で切り替える
  const relationsInfo = documentRelationsInfo(includeExternal);
  // 描画対象がない場合の判定。文書間の関係のみの表示では、描画する文書の有無で判定する
  const noGraphData = relationsMode
    ? relationNodes.length === 0
    : node.length === 0 && relationship.length === 0 && graphType.length !== 0;
  const overviewScopeIds = relationScope ? relationScope.nodeIds : relationsMode ? null : scopeIds;

  const handleCheckboxChange = (graph: GraphType) => {
    const currentIndex = graphType.indexOf(graph);
    const newGraphSelected = [...graphType];
    if (currentIndex === -1) {
      newGraphSelected.push(graph);
    } else {
      newGraphSelected.splice(currentIndex, 1);
    }
    initGraph(newGraphSelected, allNodes, allRelationships, scheme);
    setSearchQuery('');
    setGraphType(newGraphSelected);
    setSelected(undefined);
    if (nvlRef.current && nvlRef?.current?.getScale() > 1) {
      handleZoomToFit();
    }
  };

  // Callback
  const nvlCallbacks = {
    onLayoutComputing(isComputing: boolean) {
      if (!isComputing) {
        handleZoomToFit();
      }
    },
  };

  // To handle the current zoom in function of graph
  const handleZoomIn = () => {
    nvlRef.current?.setZoom(nvlRef.current.getScale() * 1.3);
  };

  // To handle the current zoom out function of graph
  const handleZoomOut = () => {
    nvlRef.current?.setZoom(nvlRef.current.getScale() * 0.7);
  };

  // Refresh the graph with nodes and relations if file is processing
  const handleRefresh = () => {
    setDisableRefresh(true);
    setMode(true);
    graphApi('refreshMode');
  };

  // when modal closes reset all states to default（フォークで足した表示範囲・文書間の関係の状態は開く時に初期化する）
  const onClose = () => {
    graphQueryAbortControllerRef?.current?.abort();
    setStatus('unknown');
    setStatusMessage('');
    setGraphViewOpen(false);
    setScheme({});
    setGraphType([]);
    setNode([]);
    setRelationship([]);
    setAllNodes([]);
    setAllRelationships([]);
    setSearchQuery('');
    setSelected(undefined);
    setEditMode(false);
    resetEditState();
  };

  const handleSchemaView = async (rawNodes: any[], rawRelationships: any[]) => {
    const { nodes, relationships } = extractGraphSchemaFromRawData(rawNodes, rawRelationships);
    setSchemaNodes(nodes as any);
    setSchemaRels(relationships as any);
    setViewcheck('viz');
    setOpenGraphView(true);
  };

  return (
    <>
      <Dialog
        modalProps={{
          className: 'h-[90%]',
          id: 'default-menu',
        }}
        size='unset'
        isOpen={open}
        hasDisabledCloseButton={false}
        onClose={onClose}
        htmlAttributes={{
          'aria-labelledby': 'form-dialog-title',
        }}
      >
        <Dialog.Header htmlAttributes={{ id: 'graph-title' }}>
          {headerTitle}
          {viewPoint !== graphLabels.chatInfoView && (
            // 全文書のグラフでは説明が 1 行（全ノード）と 2 行（文書間の関係のみ）で変わるため、2 行分の高さを確保して下の操作の行を動かさない
            <div
              style={
                allDocuments && viewPoint === graphLabels.showGraphView
                  ? { display: 'flex', alignItems: 'flex-start', minHeight: 40 }
                  : { display: 'flex', alignItems: 'center' }
              }
            >
              <span>
                <InformationCircleIconOutline className='n-size-token-6' />
              </span>
              <span className='n-body-small ml-1'>{relationsMode ? relationsInfo : graphLabels.chunksInfo}</span>
            </div>
          )}
          {/* 表示範囲の部品は左詰めとし、状態による横の移動を避ける（種類ごとの件数は下の行に表示する） */}
          <Flex
            className='w-full'
            alignItems='center'
            flexDirection='row'
            justifyContent='flex-start'
            flexWrap='wrap'
            columnGap='9'
          >
            {checkBoxView && (
              <CheckboxSelection
                graphType={graphType}
                loading={loading || relationsMode}
                handleChange={handleCheckboxChange}
                {...getCheckboxConditions(allNodes)}
              />
            )}
            {allDocuments && viewPoint === graphLabels.showGraphView && (
              <DocumentScopeControl
                scope={viewScope}
                onScopeChange={handleViewScopeChange}
                includeExternal={includeExternal}
                onIncludeExternalChange={handleIncludeExternalChange}
                isDisabled={loading || status !== 'unknown'}
                kindCounts={relationsMode ? countDocumentRelationKinds(visibleRels) : null}
              />
            )}
            {/* <SchemaDropdown isDisabled={!selectedNodes.length || !selectedRels.length} onSchemaSelect={handleSchemaSelect} /> */}
          </Flex>
        </Dialog.Header>
        <Dialog.Content className='flex flex-col n-gap-token-4 w-full grow overflow-auto border! border-palette-neutral-border-weak!'>
          {showLargeDatasetWarning && selectedRows && (
            <Banner
              name='large-dataset-warning'
              description={`${selectedRows.length} 件の文書をプレビューしています。データ量が多い場合は読み込みに時間を要し、タイムアウトすることがあります。選択する文書は 4〜8 件を推奨します。`}
              type='warning'
              usage='inline'
            />
          )}
          <div className='bg-white relative w-full h-full max-h-full border! border-palette-neutral-border-weak!'>
            {loading ? (
              <div className='my-40 flex! items-center justify-center'>
                <LoadingSpinner size='large' />
              </div>
            ) : status !== 'unknown' ? (
              <div className='my-40 flex! items-center justify-center'>
                <Banner name='graph banner' description={statusMessage} type={status} usage='inline' />
              </div>
            ) : noGraphData ? (
              <div className='my-40 flex! items-center justify-center'>
                <Banner name='graph banner' description={graphLabels.noNodesRels} type='danger' usage='inline' />
              </div>
            ) : !relationsMode && graphType.length === 0 && checkBoxView ? (
              <div className='my-40 flex! items-center justify-center'>
                <Banner name='graph banner' description={graphLabels.selectCheckbox} type='danger' usage='inline' />
              </div>
            ) : (
              <>
                <div className='flex' style={{ height: '100%' }}>
                  <div className='bg-palette-neutral-bg-default relative' style={{ width: '100%', flex: '1' }}>
                    <InteractiveNvlWrapper
                      key={editMode ? 'graph-edit' : relationsMode ? 'graph-relations' : 'graph-view'}
                      nodes={displayNodes}
                      rels={displayRelationships}
                      nvlOptions={nvlOptions}
                      ref={nvlRef}
                      mouseEventCallbacks={{ ...mouseEventCallbacks }}
                      interactionOptions={interactionOptions}
                      nvlCallbacks={nvlCallbacks}
                    />
                    {editMode && (
                      <div
                        className='absolute top-4 left-4 px-2 py-1 rounded n-body-small n-bg-palette-primary-bg-weak n-text-palette-primary-text'
                      >
                        編集モード（Ctrl または Shift を押しながらクリックで複数選択、右クリックで操作の一覧を表示）
                        {visibleNodes.length < node.length &&
                          `。絞り込み中（${node.length} 件中 ${visibleNodes.length} 件を表示。絞り込み条件を変更する場合は編集を終了する）`}
                      </div>
                    )}
                    <IconButtonArray orientation='vertical' isFloating={true} className='absolute top-4 right-4'>
                      {canEdit && !relationsMode && (
                        <IconButtonWithToolTip
                          label='編集'
                          text={editMode ? '編集を終了' : 'グラフを編集'}
                          onClick={toggleEditMode}
                          placement='left'
                          className={editMode ? 'ndl-active' : ''}
                        >
                          <PencilIconOutline className='n-size-token-7' />
                        </IconButtonWithToolTip>
                      )}
                      <IconButtonWithToolTip
                        label='スキーマ表示'
                        text='スキーマ表示'
                        onClick={() => handleSchemaView(viewNodes, viewRels)}
                        placement='left'
                      >
                        <ExploreIcon className='n-size-token-7' />
                      </IconButtonWithToolTip>
                    </IconButtonArray>
                    <IconButtonArray orientation='vertical' isFloating={true} className='absolute bottom-4 right-4'>
                      {viewPoint !== 'chatInfoView' && (
                        <IconButtonWithToolTip
                          label='更新'
                          text='グラフを更新'
                          onClick={handleRefresh}
                          placement='left'
                          disabled={disableRefresh}
                        >
                          <ArrowPathIconOutline className='n-size-token-7' />
                        </IconButtonWithToolTip>
                      )}
                      <IconButtonWithToolTip label='拡大' text='拡大' onClick={handleZoomIn} placement='left'>
                        <MagnifyingGlassPlusIconOutline className='n-size-token-7' />
                      </IconButtonWithToolTip>
                      <IconButtonWithToolTip label='縮小' text='縮小' onClick={handleZoomOut} placement='left'>
                        <MagnifyingGlassMinusIconOutline className='n-size-token-7' />
                      </IconButtonWithToolTip>
                      <IconButtonWithToolTip
                        label='全体表示'
                        text='全体表示'
                        onClick={handleZoomToFit}
                        placement='left'
                      >
                        <FitToScreenIcon className='n-size-token-7' />
                      </IconButtonWithToolTip>
                    </IconButtonArray>
                  </div>
                  <ResizePanelDetails open={true}>
                    {editMode ? (
                      <GraphEditPanel
                        nodes={allNodes}
                        relationships={allRelationships}
                        scheme={scheme}
                        selection={editSelection}
                        setSelection={setEditSelection}
                        contextMenu={contextMenu}
                        setContextMenu={setContextMenu}
                        onNodeChanged={(changedNode) => applyDisplayChange({ nodes: [changedNode] })}
                        onRelationshipChanged={(changedRelationship) =>
                          applyDisplayChange({ relationships: [changedRelationship] })
                        }
                        onStructureChanged={handleStructureChange}
                        onShowNeighborhood={(elementId) => showEntityNeighborhood(elementId)}
                      />
                    ) : isDocumentRelationship(selectedItem) ? (
                      <DocumentRelationPanel relationship={selectedItem} />
                    ) : selectedItem !== undefined ? (
                      <GraphPropertiesPanel
                        inspectedItem={selectedItem as BasicNode | BasicRelationship}
                        newScheme={relationsMode ? relationScheme : newScheme}
                      />
                    ) : (
                      <ResultOverview
                        nodes={viewNodes}
                        relationships={viewRels}
                        newScheme={relationsMode ? relationScheme : newScheme}
                        searchQuery={searchQuery}
                        setSearchQuery={setSearchQuery}
                        setNodes={setViewNodes}
                        setRelationships={setViewRels}
                        focusMode={focusMode}
                        setFocusMode={setFocusMode}
                        includeNeighbors={includeNeighbors}
                        setIncludeNeighbors={setIncludeNeighbors}
                        onClearSelection={clearSelection}
                        visibleNodeCount={visibleNodes.length}
                        documents={documentList}
                        docFilter={docFilter}
                        setDocFilter={setDocFilter}
                        scopeIds={overviewScopeIds}
                        scopeRelationshipIds={relationScope ? relationScope.relationshipIds : null}
                      />
                    )}
                  </ResizePanelDetails>
                </div>
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog>
      {openGraphView && (
        <SchemaViz
          open={openGraphView}
          setGraphViewOpen={setOpenGraphView}
          viewPoint={viewPoint}
          nodeValues={schemaNodes}
          relationshipValues={schemaRels}
          view={viewCheck}
        />
      )}
    </>
  );
};
export default GraphViewModal;
