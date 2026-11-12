import { Banner, Button, Typography } from '@neo4j-ndl/react';
import type { Relationship } from '@neo4j-nvl/base';
import { Dispatch, SetStateAction, useEffect, useMemo, useState } from 'react';
import { ResizePanelDetails } from '../ResizePanel';
import { LegendsChip } from '../LegendsChip';
import { useCredentials } from '../../../context/UserCredentials';
import { describeGraphEditError, getVocabulary, updateRelationship } from '../../../services/GraphEdit';
import {
  ExtendedNode,
  GraphEditCandidate,
  GraphEditContextMenuRequest,
  GraphEditEntityHit,
  GraphEditRelationship,
  GraphEditRelationshipTarget,
  GraphEditSelection,
  GraphEditStructureChange,
  GraphEditVocabulary,
  NeoNode,
  Scheme,
} from '../../../types';
import { showErrorToast, showSuccessToast } from '../../../utils/Toasts';
import {
  EMPTY_GRAPH_EDIT_SELECTION,
  addNodeToSelection,
  buildReservedNames,
  entityName,
  graphEditUser,
  isRuleMadeNode,
  nodeEditBlockReason,
  nodeLinkBlockReason,
  relationshipEditBlockReason,
  toCandidate,
  toRelationshipTarget,
} from '../../../utils/GraphEditUtils';
import EntitySearch from './EntitySearch';
import EntityEditor from './EntityEditor';
import GraphEditContextMenu, { GraphEditMenuItem } from './GraphEditContextMenu';
import MergeEntitiesDialog from './MergeEntitiesDialog';
import DeleteEntitiesDialog from './DeleteEntitiesDialog';
import {
  ChangeRelationshipTypeDialog,
  CreateRelationshipDialog,
  DeleteRelationshipDialog,
  relationshipText,
} from './RelationshipDialogs';

type DialogState =
  | { kind: 'merge'; candidates: GraphEditCandidate[]; keepName?: string }
  | { kind: 'deleteNodes'; candidates: GraphEditCandidate[] }
  | { kind: 'createRelationship'; source: GraphEditCandidate; target: GraphEditCandidate }
  | { kind: 'changeRelationshipType'; relationship: GraphEditRelationshipTarget }
  | { kind: 'deleteRelationship'; relationship: GraphEditRelationshipTarget };

export interface GraphEditPanelProps {
  nodes: ExtendedNode[];
  relationships: Relationship[];
  scheme: Scheme;
  selection: GraphEditSelection;
  setSelection: Dispatch<SetStateAction<GraphEditSelection>>;
  contextMenu: GraphEditContextMenuRequest | null;
  setContextMenu: Dispatch<SetStateAction<GraphEditContextMenuRequest | null>>;
  onNodeChanged: (node: NeoNode) => void;
  onRelationshipChanged: (relationship: GraphEditRelationship) => void;
  onStructureChanged: (change: GraphEditStructureChange) => Promise<void>;
  onShowNeighborhood: (elementId: string) => Promise<void>;
}

const HINTS = [
  'エンティティをクリックすると、この欄で名前・ラベル・説明などを編集できる。',
  'Ctrl または Shift を押しながらクリックすると、複数を選択できる（統合・リレーションの追加・削除）。',
  'ノードまたはリレーションを右クリックすると、操作の一覧が表示される。',
  '文書・チャンク・コミュニティのノードと、予約されたリレーションは編集できない。',
  '操作の履歴の確認と取り消しは「グラフ設定」の「保守」タブの「修正台帳」で行う。',
];

// グラフ表示の編集の状態で、右の詳細欄に出す編集欄
export default function GraphEditPanel({
  nodes,
  relationships,
  scheme,
  selection,
  setSelection,
  contextMenu,
  setContextMenu,
  onNodeChanged,
  onRelationshipChanged,
  onStructureChanged,
  onShowNeighborhood,
}: GraphEditPanelProps) {
  const { userCredentials } = useCredentials();
  const editUser = graphEditUser(userCredentials);
  const [vocabulary, setVocabulary] = useState<GraphEditVocabulary | null>(null);
  const [vocabularyError, setVocabularyError] = useState<string>('');
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [working, setWorking] = useState<boolean>(false);

  useEffect(() => {
    let cancelled = false;
    getVocabulary()
      .then((loaded) => {
        if (!cancelled) {
          setVocabulary(loaded);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setVocabularyError(describeGraphEditError(error));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const reserved = useMemo(() => buildReservedNames(vocabulary), [vocabulary]);
  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const relationshipById = useMemo(
    () => new Map(relationships.map((relationship) => [relationship.id, relationship])),
    [relationships]
  );

  const selectedNodes = selection.nodeIds
    .map((id) => nodeById.get(id))
    .filter((node): node is ExtendedNode => node !== undefined);
  const selectedRelationship =
    selection.relationshipId === undefined ? undefined : relationshipById.get(selection.relationshipId);

  const relationshipTargetOf = (relationship: Relationship | undefined): GraphEditRelationshipTarget | undefined => {
    if (relationship === undefined) {
      return undefined;
    }
    const source = nodeById.get(relationship.from);
    const target = nodeById.get(relationship.to);
    if (source === undefined || target === undefined) {
      return undefined;
    }
    return toRelationshipTarget(relationship, source, target);
  };

  const closeDialog = () => setDialog(null);

  const handleSearchSelect = async (hit: GraphEditEntityHit) => {
    try {
      await onShowNeighborhood(hit.node.element_id);
    } catch (error) {
      showErrorToast(describeGraphEditError(error));
    }
    setSelection({ nodeIds: [hit.node.element_id] });
  };

  const handleReverse = async (relationship: GraphEditRelationshipTarget) => {
    setWorking(true);
    try {
      const result = await updateRelationship({
        element_id: relationship.element_id,
        expected: { src: relationship.source.id, type: relationship.type, tgt: relationship.target.id },
        reverse: true,
        user: editUser,
      });
      showSuccessToast(
        `向きを反転しました：${relationshipText(relationship.target.id, relationship.type, relationship.source.id)}`
      );
      setSelection({ nodeIds: [], relationshipId: result.relationship.element_id });
      await onStructureChanged({
        removedRelationshipIds: [relationship.element_id],
        addedRelationships: [result.relationship],
      });
    } catch (error) {
      showErrorToast(describeGraphEditError(error));
    } finally {
      setWorking(false);
    }
  };

  // 結果を表示に反映する。要素 ID が変わる操作（統合・削除・付け替え）は表示を取り直す。
  const afterStructureChange = async (change: GraphEditStructureChange) => {
    try {
      await onStructureChanged(change);
    } catch (error) {
      showErrorToast(describeGraphEditError(error));
    }
  };

  // 右クリックの操作の一覧
  const menu = (() => {
    if (contextMenu === null) {
      return null;
    }
    if (contextMenu.type === 'node') {
      const node = nodeById.get(contextMenu.id);
      const blockReason = nodeEditBlockReason(node, reserved);
      const editable = blockReason === '' && node !== undefined;
      const linkable = node !== undefined && nodeLinkBlockReason(node, reserved) === '';
      const pair = [...new Set([...selection.nodeIds, contextMenu.id])]
        .map((id) => nodeById.get(id))
        .filter((item): item is ExtendedNode => item !== undefined);
      const pairEditable = pair.length === 2 && pair.every((item) => nodeLinkBlockReason(item, reserved) === '');
      const alreadySelected = selection.nodeIds.includes(contextMenu.id);
      const items: GraphEditMenuItem[] = [
        {
          id: 'edit',
          title: '編集',
          isDisabled: !editable,
          onClick: () => setSelection({ nodeIds: [contextMenu.id] }),
        },
        {
          id: 'add-to-merge',
          title: '統合対象に追加',
          description: alreadySelected ? '選択済みです' : `選択中の ${selection.nodeIds.length} 件に追加します`,
          isDisabled: !linkable || alreadySelected,
          onClick: () => setSelection((previous) => addNodeToSelection(previous, contextMenu.id)),
        },
        {
          id: 'create-relationship',
          title: '選択した 2 つの間にリレーションを追加',
          description:
            pair.length === 2
              ? `${entityName(pair[0])} → ${entityName(pair[1])}`
              : 'エンティティを 2 つ選択すると使用できます',
          isDisabled: !pairEditable,
          onClick: () =>
            setDialog({ kind: 'createRelationship', source: toCandidate(pair[0]), target: toCandidate(pair[1]) }),
        },
        {
          id: 'delete',
          title: '削除',
          isDanger: true,
          isDisabled: !editable,
          onClick: () => {
            if (node !== undefined) {
              setDialog({ kind: 'deleteNodes', candidates: [toCandidate(node)] });
            }
          },
        },
      ];
      return { title: `エンティティ：${entityName(node) || contextMenu.id}`, note: blockReason, items };
    }
    const relationship = relationshipById.get(contextMenu.id);
    const target = relationshipTargetOf(relationship);
    const blockReason = relationshipEditBlockReason(
      relationship,
      relationship ? nodeById.get(relationship.from) : undefined,
      relationship ? nodeById.get(relationship.to) : undefined,
      reserved
    );
    const editable = blockReason === '' && target !== undefined;
    const items: GraphEditMenuItem[] = [
      {
        id: 'change-type',
        title: 'リレーションタイプを変更',
        isDisabled: !editable,
        onClick: () => {
          if (target !== undefined) {
            setDialog({ kind: 'changeRelationshipType', relationship: target });
          }
        },
      },
      {
        id: 'reverse',
        title: '向きを反転',
        isDisabled: !editable || working,
        onClick: () => {
          if (target !== undefined) {
            handleReverse(target);
          }
        },
      },
      {
        id: 'delete-relationship',
        title: '削除',
        isDanger: true,
        isDisabled: !editable,
        onClick: () => {
          if (target !== undefined) {
            setDialog({ kind: 'deleteRelationship', relationship: target });
          }
        },
      },
    ];
    const title = target ? relationshipText(target.source.id, target.type, target.target.id) : contextMenu.id;
    return { title: `リレーション：${title}`, note: blockReason, items };
  })();

  const renderRelationship = (relationship: Relationship) => {
    const target = relationshipTargetOf(relationship);
    const blockReason = relationshipEditBlockReason(
      relationship,
      nodeById.get(relationship.from),
      nodeById.get(relationship.to),
      reserved
    );
    return (
      <div className='flex flex-col gap-2' data-testid='graph-edit-relationship'>
        <Typography variant='subheading-medium'>リレーション</Typography>
        <div>
          <LegendsChip type='relationship' label={relationship.caption ?? ''} scheme={{}} />
        </div>
        <div className='n-body-medium'>
          始点：{target ? target.source.id : relationship.from}
          <br />
          終点：{target ? target.target.id : relationship.to}
        </div>
        {blockReason !== '' && <Banner type='warning' usage='inline' description={blockReason} />}
        {blockReason === '' && target !== undefined && (
          <div className='flex flex-row flex-wrap gap-2'>
            <Button size='small' onClick={() => setDialog({ kind: 'changeRelationshipType', relationship: target })}>
              リレーションタイプを変更…
            </Button>
            <Button size='small' fill='outlined' isLoading={working} onClick={() => handleReverse(target)}>
              向きを反転
            </Button>
            <Button
              size='small'
              fill='outlined'
              color='danger'
              onClick={() => setDialog({ kind: 'deleteRelationship', relationship: target })}
            >
              削除…
            </Button>
          </div>
        )}
      </div>
    );
  };

  const renderMultiSelection = () => {
    const blocked = selectedNodes.filter((node) => nodeLinkBlockReason(node, reserved) !== '');
    const ruleMade = selectedNodes.filter((node) => isRuleMadeNode(node));
    const candidates = selectedNodes.map(toCandidate);
    return (
      <div className='flex flex-col gap-2' data-testid='graph-edit-multi-selection'>
        <Typography variant='subheading-medium'>選択中のエンティティ（{selectedNodes.length} 件）</Typography>
        <ul className='pl-4 n-body-medium'>
          {selectedNodes.map((node) => (
            <li key={node.id} className='flex flex-row items-center gap-2'>
              <span className='flex-1 min-w-0 textellipsis'>{entityName(node) || node.id}</span>
              <Button
                size='small'
                fill='text'
                onClick={() =>
                  setSelection((previous) => ({ nodeIds: previous.nodeIds.filter((id) => id !== node.id) }))
                }
              >
                除外
              </Button>
            </li>
          ))}
        </ul>
        {blocked.length > 0 && (
          <Banner
            type='warning'
            usage='inline'
            description={`編集できないノードが含まれています：${blocked.map((node) => entityName(node) || node.id).join('、')}`}
          />
        )}
        {ruleMade.length > 0 && (
          <Banner
            type='warning'
            usage='inline'
            description={`規則により生成したノードを含みます（${ruleMade.map((node) => entityName(node) || node.id).join('、')}）。統合ではこのノードが統合先となり、名前とラベルは変更されません。削除はできません。`}
          />
        )}
        <div className='flex flex-row flex-wrap gap-2'>
          <Button
            size='small'
            isDisabled={blocked.length > 0 || ruleMade.length > 1}
            onClick={() => setDialog({ kind: 'merge', candidates })}
          >
            統合…
          </Button>
          <Button
            size='small'
            fill='outlined'
            isDisabled={blocked.length > 0 || selectedNodes.length !== 2}
            onClick={() => setDialog({ kind: 'createRelationship', source: candidates[0], target: candidates[1] })}
          >
            リレーションを追加…
          </Button>
          <Button
            size='small'
            fill='outlined'
            color='danger'
            isDisabled={blocked.length > 0 || ruleMade.length > 0}
            onClick={() => setDialog({ kind: 'deleteNodes', candidates })}
          >
            削除…
          </Button>
          <Button size='small' fill='text' onClick={() => setSelection(EMPTY_GRAPH_EDIT_SELECTION)}>
            選択を解除
          </Button>
        </div>
      </div>
    );
  };

  const renderBody = () => {
    if (selectedRelationship !== undefined) {
      return renderRelationship(selectedRelationship);
    }
    if (selection.nodeIds.length === 1) {
      const [elementId] = selection.nodeIds;
      const node = nodeById.get(elementId);
      const blockReason = node === undefined ? '' : nodeEditBlockReason(node, reserved);
      if (blockReason !== '') {
        return <Banner type='warning' usage='inline' description={blockReason} />;
      }
      return (
        <EntityEditor
          key={elementId}
          elementId={elementId}
          vocabulary={vocabulary}
          reserved={reserved}
          scheme={scheme}
          user={editUser}
          onNodeChanged={onNodeChanged}
          onProposeMerge={(candidates, keepName) => setDialog({ kind: 'merge', candidates, keepName })}
          onRequestDelete={(candidate) => setDialog({ kind: 'deleteNodes', candidates: [candidate] })}
        />
      );
    }
    if (selectedNodes.length >= 2) {
      return renderMultiSelection();
    }
    return (
      <div className='flex flex-col gap-1 n-body-small' data-testid='graph-edit-hints'>
        <div className='n-body-medium font-bold!'>編集モード</div>
        <ul className='list-disc pl-5'>
          {HINTS.map((hint) => (
            <li key={hint}>{hint}</li>
          ))}
        </ul>
      </div>
    );
  };

  return (
    <>
      <ResizePanelDetails.Title>
        <h6 className='mr-auto'>グラフの編集</h6>
      </ResizePanelDetails.Title>
      <ResizePanelDetails.Content>
        {/* 入力欄や選択の一覧で Escape を押しても、グラフ表示のダイアログ（document で Escape を受ける）を閉じない */}
        <div
          className='mx-4 flex flex-col gap-4'
          data-testid='graph-edit-panel'
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation();
            }
          }}
        >
          <EntitySearch onSelect={handleSearchSelect} />
          {vocabularyError && (
            <Banner
              type='warning'
              usage='inline'
              description={`ラベルとリレーションタイプの語彙を読み込めませんでした。入力した名前は形式のみを検査します。${vocabularyError}`}
            />
          )}
          {renderBody()}
        </div>
      </ResizePanelDetails.Content>

      {contextMenu !== null && menu !== null && (
        <GraphEditContextMenu
          key={`${contextMenu.type}-${contextMenu.id}-${contextMenu.x}-${contextMenu.y}`}
          request={contextMenu}
          title={menu.title}
          note={menu.note || undefined}
          items={menu.items}
          onClose={() => setContextMenu(null)}
        />
      )}

      {dialog?.kind === 'merge' && (
        <MergeEntitiesDialog
          open={true}
          initialCandidates={dialog.candidates}
          initialKeepName={dialog.keepName}
          vocabulary={vocabulary}
          reserved={reserved}
          scheme={scheme}
          user={editUser}
          onClose={closeDialog}
          onMerged={async (result, sourceElementIds) => {
            closeDialog();
            setSelection({ nodeIds: [result.node.element_id] });
            await afterStructureChange({
              removedNodeIds: sourceElementIds,
              changedNodes: [result.node],
              survivorElementId: result.node.element_id,
            });
          }}
        />
      )}
      {dialog?.kind === 'deleteNodes' && (
        <DeleteEntitiesDialog
          open={true}
          candidates={dialog.candidates}
          user={editUser}
          onClose={closeDialog}
          onDeleted={async (_result, elementIds) => {
            closeDialog();
            setSelection(EMPTY_GRAPH_EDIT_SELECTION);
            await afterStructureChange({ removedNodeIds: elementIds });
          }}
        />
      )}
      {dialog?.kind === 'createRelationship' && (
        <CreateRelationshipDialog
          open={true}
          source={dialog.source}
          target={dialog.target}
          vocabulary={vocabulary}
          reserved={reserved}
          user={editUser}
          onClose={closeDialog}
          onCreated={(result) => {
            closeDialog();
            onRelationshipChanged(result.relationship);
            setSelection({ nodeIds: [], relationshipId: result.relationship.element_id });
          }}
        />
      )}
      {dialog?.kind === 'changeRelationshipType' && (
        <ChangeRelationshipTypeDialog
          open={true}
          relationship={dialog.relationship}
          vocabulary={vocabulary}
          reserved={reserved}
          user={editUser}
          onClose={closeDialog}
          onUpdated={async (result) => {
            const oldId = dialog.relationship.element_id;
            closeDialog();
            setSelection({ nodeIds: [], relationshipId: result.relationship.element_id });
            await afterStructureChange({
              removedRelationshipIds: [oldId],
              addedRelationships: [result.relationship],
            });
          }}
        />
      )}
      {dialog?.kind === 'deleteRelationship' && (
        <DeleteRelationshipDialog
          open={true}
          relationship={dialog.relationship}
          user={editUser}
          onClose={closeDialog}
          onDeleted={async (elementId) => {
            closeDialog();
            setSelection(EMPTY_GRAPH_EDIT_SELECTION);
            await afterStructureChange({ removedRelationshipIds: [elementId] });
          }}
        />
      )}
    </>
  );
}
