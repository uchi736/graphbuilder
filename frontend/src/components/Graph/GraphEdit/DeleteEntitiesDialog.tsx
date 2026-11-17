import { useState } from 'react';
import { deleteEntities } from '../../../services/GraphEdit';
import { GraphEditCandidate } from '../../../types';
import { showSuccessToast } from '../../../utils/Toasts';
import { candidateName } from '../../../utils/GraphEditUtils';
import { GraphEditDialog, SuppressCheckbox, useGraphEditSubmit } from './GraphEditDialog';

// M5 エンティティの削除。「再抽出されても作成しない」（削除の記録）を既定で有効にする。
export default function DeleteEntitiesDialog({
  candidates,
  user,
  onClose,
  onDeleted,
}: {
  candidates: GraphEditCandidate[];
  user: string;
  onClose: () => void;
  onDeleted: (elementIds: string[]) => void;
}) {
  const [suppress, setSuppress] = useState<boolean>(true);
  const { submitting, errorText, submit } = useGraphEditSubmit();

  const handleDelete = () =>
    submit(async () => {
      await deleteEntities({
        nodes: candidates.map((candidate) => ({ element_id: candidate.element_id, expected_id: candidate.id })),
        suppress,
        user,
      });
      showSuccessToast(
        `${candidates.length} 件のエンティティを削除しました。${suppress ? '再抽出されても作成しません。' : ''}`
      );
      onDeleted(candidates.map((candidate) => candidate.element_id));
    });

  return (
    <GraphEditDialog
      title='エンティティの削除'
      size='small'
      errorText={errorText}
      submitLabel='削除する'
      submitColor='danger'
      submitting={submitting}
      isSubmitDisabled={candidates.length === 0}
      onSubmit={handleDelete}
      onClose={onClose}
    >
      <div className='n-body-medium'>次の {candidates.length} 件のエンティティを削除します。</div>
      <ul className='list-disc pl-6 max-h-40 overflow-y-auto n-body-medium'>
        {candidates.map((candidate) => (
          <li key={candidate.element_id}>{candidateName(candidate)}</li>
        ))}
      </ul>
      <SuppressCheckbox isChecked={suppress} onChange={setSuppress} />
      <div className='n-body-small n-text-palette-neutral-text-weak'>
        このチェックボックスをオフにした場合、同じ名前のエンティティが次回の取り込みで再作成される。削除は「グラフ設定」の「保守」タブの「修正台帳」から取り消せる。
      </div>
    </GraphEditDialog>
  );
}
