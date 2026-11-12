import { Button, Checkbox, Flex, IconButton, TextInput, Typography } from '@neo4j-ndl/react';
import { MagnifyingGlassIconOutline } from '@neo4j-ndl/react/icons';
import { LegendsChip } from './LegendsChip';
import { ExtendedNode, ExtendedRelationship, Scheme } from '../../types';
import { graphLabels, RESULT_STEP_SIZE } from '../../utils/Constants';
import type { Relationship } from '@neo4j-nvl/base';
import { ShowAll } from '../UI/ShowAll';
import { sortAlphabetically } from '../../utils/Utils';
import { Dispatch, SetStateAction } from 'react';

interface OverViewProps {
  nodes: ExtendedNode[];
  relationships: ExtendedRelationship[];
  newScheme: Scheme;
  searchQuery: string;
  setSearchQuery: Dispatch<SetStateAction<string>>;
  setNodes: Dispatch<SetStateAction<ExtendedNode[]>>;
  setRelationships: Dispatch<SetStateAction<ExtendedRelationship[]>>;
  focusMode?: boolean;
  setFocusMode?: Dispatch<SetStateAction<boolean>>;
  includeNeighbors?: boolean;
  setIncludeNeighbors?: Dispatch<SetStateAction<boolean>>;
  onClearSelection?: () => void;
  visibleNodeCount?: number;
  documents?: { id: string; name: string; chunks: number }[];
  docFilter?: string[];
  setDocFilter?: Dispatch<SetStateAction<string[]>>;
  scopeIds?: Set<string> | null;
  // 文書間の関係のみの表示で、絞り込みの範囲に含めるリレーションの id。指定した場合はリレーションをこの範囲で集計する
  scopeRelationshipIds?: Set<string> | null;
}
const ResultOverview: React.FunctionComponent<OverViewProps> = ({
  nodes,
  relationships,
  newScheme,
  searchQuery,
  setSearchQuery,
  setNodes,
  setRelationships,
  focusMode,
  setFocusMode,
  includeNeighbors,
  setIncludeNeighbors,
  onClearSelection,
  visibleNodeCount,
  documents,
  docFilter,
  setDocFilter,
  scopeIds,
  scopeRelationshipIds,
}) => {
  // 文書で絞り込んでいる場合は、その範囲のノードとリレーションを集計する
  const scopedNodes = scopeIds ? nodes.filter((n) => scopeIds.has(n.id)) : nodes;
  const scopedRels = scopeRelationshipIds
    ? relationships.filter((r) => scopeRelationshipIds.has(r.id))
    : scopeIds
      ? relationships.filter((r) => scopeIds.has(r.from) && scopeIds.has(r.to))
      : relationships;
  const labelCounts: Record<string, number> = {};
  scopedNodes.forEach((n) => (n.labels ?? []).forEach((l) => (labelCounts[l] = (labelCounts[l] ?? 0) + 1)));
  const nodeCount = (_nodes: ExtendedNode[], label: string): number => labelCounts[label] ?? 0;

  // 凡例の並び: Document と Chunk を先頭、残りは件数の多い順（範囲内に 0 件の種類は出さない）
  const pinned = [graphLabels.document, graphLabels.chunk];
  const nodeCheck = Object.keys(newScheme)
    .filter((l) => (labelCounts[l] ?? 0) > 0)
    .sort((a, b) => {
      const pa = pinned.indexOf(a);
      const pb = pinned.indexOf(b);
      if (pa !== -1 || pb !== -1) {
        return (pa === -1 ? 99 : pa) - (pb === -1 ? 99 : pb);
      }
      return (labelCounts[b] ?? 0) - (labelCounts[a] ?? 0) || a.localeCompare(b);
    });

  // get sorted relationships（state の配列を並べ替えないよう複製して並べ替える）
  const relationshipsSorted = [...scopedRels].sort(sortAlphabetically);

  const relCounts: Record<string, number> = {};
  scopedRels.forEach((r) => {
    const key = r.caption || '';
    relCounts[key] = (relCounts[key] ?? 0) + 1;
  });
  const relationshipCount = (_relationships: ExtendedRelationship[], label: string): number => relCounts[label] ?? 0;

  // リレーションの凡例: 件数の多い順
  const groupedAndSortedRelationships: ExtendedRelationship[] = Object.values(
    relationshipsSorted.reduce((acc: { [key: string]: ExtendedRelationship }, relType: Relationship) => {
      const key = relType.caption || '';
      if (!acc[key]) {
        acc[key] = { ...relType, count: relCounts[key] ?? 0 };
      }
      return acc;
    }, {})
  ).sort((a, b) => (relCounts[b.caption || ''] ?? 0) - (relCounts[a.caption || ''] ?? 0));

  const toggleDoc = (id: string) => {
    if (!setDocFilter) {
      return;
    }
    setDocFilter((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  // On Relationship Legend Click, highlight the relationships and deactivating any active nodes
  const handleRelationshipClick = (nodeLabel: string) => {
    const updatedRelations = relationships.map((rel) => {
      return {
        ...rel,
        selected: rel?.caption?.includes(nodeLabel),
      };
    });

    // // deactivating any active nodes（ノードの大きさは変えない。文書ノードの大きさ 40 を維持する）
    const updatedNodes = nodes.map((node) => {
      return {
        ...node,
        selected: false,
      };
    });
    if (searchQuery !== '') {
      setSearchQuery('');
    }
    setRelationships(updatedRelations);
    setNodes(updatedNodes);
  };

  // On Node Click, highlighting the nodes and deactivating any active relationships
  const handleNodeClick = (nodeLabel: string) => {
    const updatedNodes = nodes.map((node) => {
      const isActive = node.labels.includes(nodeLabel);
      return {
        ...node,
        selected: isActive,
      };
    });
    // deactivating any active relationships
    const updatedRelationships = relationships.map((rel) => {
      return {
        ...rel,
        selected: false,
      };
    });
    if (searchQuery !== '') {
      setSearchQuery('');
    }
    setNodes(updatedNodes);
    setRelationships(updatedRelationships);
  };

  return (
    <>
      {nodeCheck.length > 0 && (
        <>
          <Flex className='py-3 pt-3 ml-2'>
            <Typography variant='h3'>{graphLabels.resultOverview}</Typography>
            <div className={`text-input-container`}>
              <TextInput
                htmlAttributes={{
                  type: 'text',
                  'aria-label': 'ノードを検索',
                  placeholder: 'ノードのプロパティで検索',
                }}
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                }}
                isFluid={true}
                leftElement={
                  <IconButton
                    ariaLabel='検索アイコン'
                    isClean={true}
                    size='small'
                    className='-mt-0.5'
                    htmlAttributes={{ type: 'submit' }}
                  >
                    <MagnifyingGlassIconOutline className='n-size-token-7' />
                  </IconButton>
                }
              />
            </div>
            {documents && setDocFilter && documents.length > 1 && (
              <Flex flexDirection='column' gap='1'>
                <Typography variant='subheading-small'>
                  文書で絞り込み{docFilter && docFilter.length > 0 ? `（${docFilter.length} 件選択中）` : '（全文書）'}
                </Typography>
                <div className='flex flex-col gap-1' style={{ maxHeight: 180, overflowY: 'auto' }}>
                  {documents.map((d) => (
                    <Checkbox
                      key={d.id}
                      isChecked={docFilter?.includes(d.id) ?? false}
                      label={`${d.name}（チャンク ${d.chunks} 件）`}
                      onChange={() => toggleDoc(d.id)}
                    />
                  ))}
                </div>
                <div>
                  <Button
                    size='small'
                    fill='outlined'
                    onClick={() => setDocFilter([])}
                    isDisabled={!docFilter || docFilter.length === 0}
                  >
                    全文書を表示
                  </Button>
                </div>
              </Flex>
            )}
            <Typography variant='subheading-small'>
              {graphLabels.totalNodes} ({nodes.length})
              {visibleNodeCount !== undefined && visibleNodeCount < nodes.length
                ? `、表示中 ${visibleNodeCount} 件`
                : ''}
            </Typography>
            {setFocusMode !== undefined && (
              <Flex flexDirection='column' gap='2'>
                <Checkbox
                  isChecked={focusMode ?? true}
                  label='選択ノードのみ表示'
                  onChange={() => setFocusMode((v) => !v)}
                />
                <Checkbox
                  isChecked={includeNeighbors ?? false}
                  isDisabled={!(focusMode ?? true)}
                  label='隣接ノードを含める'
                  onChange={() => setIncludeNeighbors && setIncludeNeighbors((v) => !v)}
                />
                <div>
                  <Button size='small' fill='outlined' onClick={onClearSelection}>
                    選択を解除
                  </Button>
                </div>
                <Typography variant='body-small' className='n-text-palette-neutral-text-weak'>
                  選択は凡例のラベル・リレーションタイプの押下または検索で行う。グラフ上のクリックは詳細の表示のみである。選択がない場合、「選択ノードのみ表示」は適用されない。
                </Typography>
              </Flex>
            )}
          </Flex>
          <div className='flex gap-2 flex-wrap ml-2'>
            <ShowAll initiallyShown={RESULT_STEP_SIZE}>
              {nodeCheck.map((nodeLabel) => (
                <LegendsChip
                  type='node'
                  key={nodeLabel}
                  label={nodeLabel}
                  scheme={newScheme}
                  count={nodeCount(nodes, nodeLabel)}
                  onClick={() => handleNodeClick(nodeLabel)}
                />
              ))}
            </ShowAll>
          </div>
        </>
      )}
      {relationshipsSorted.length > 0 && (
        <>
          <Flex className='py-3 pt-3 ml-2'>
            <Typography variant='subheading-small'>
              {graphLabels.totalRelationships} ({scopedRels.length})
            </Typography>
          </Flex>
          <div className='flex gap-2 flex-wrap ml-2'>
            <ShowAll initiallyShown={RESULT_STEP_SIZE}>
              {groupedAndSortedRelationships.map((relType, index) => (
                <LegendsChip
                  key={index}
                  label={relType.caption || ''}
                  type='relationship'
                  count={relationshipCount(relationships as ExtendedRelationship[], relType.caption || '')}
                  onClick={() => handleRelationshipClick(relType.caption || '')}
                  scheme={{}}
                />
              ))}
            </ShowAll>
          </div>
        </>
      )}
    </>
  );
};

export default ResultOverview;
