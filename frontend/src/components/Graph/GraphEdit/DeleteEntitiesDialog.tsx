import { Banner, Button, Checkbox, Dialog } from '@neo4j-ndl/react';
import { useState } from 'react';
import { deleteEntities, describeGraphEditError } from '../../../services/GraphEdit';
import { GraphEditCandidate, GraphEditDeleteResult } from '../../../types';
import { showSuccessToast } from '../../../utils/Toasts';

// M5 エンティティの削除。「再抽出されても作成しない」（削除の記録）を既定で有効にする。
export default function DeleteEntitiesDialog({
  open,
  candidates,
  user,
  onClose,
  onDeleted,
}: {
  open: boolean;
  candidates: GraphEditCandidate[];
  user: string;
  onClose: () => void;
  onDeleted: (result: GraphEditDeleteResult, elementIds: string[]) => void;
}) {
  const [suppress, setSuppress] = useState<boolean>(true);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [errorText, setErrorText] = useState<string>('');

  const handleDelete = async () => {
    setSubmitting(true);
    setErrorText('');
    try {
      const result = await deleteEntities({
        nodes: candidates.map((candidate) => ({ element_id: candidate.element_id, expected_id: candidate.id })),
        suppress,
        user,
      });
      showSuccessToast(
        `${candidates.length} 件のエンティティを削除しました。${suppress ? '再抽出されても作成しません。' : ''}`
      );
      onDeleted(
        result,
        candidates.map((candidate) => candidate.element_id)
      );
    } catch (error) {
      setErrorText(describeGraphEditError(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog isOpen={open} onClose={onClose} size='small' htmlAttributes={{ 'aria-label': 'エンティティの削除' }}>
      <Dialog.Header>エンティティの削除</Dialog.Header>
      <Dialog.Content className='flex flex-col gap-3'>
        <div className='n-body-medium'>次の {candidates.length} 件のエンティティを削除します。</div>
        <ul className='list-disc pl-6 max-h-40 overflow-y-auto n-body-medium'>
          {candidates.map((candidate) => (
            <li key={candidate.element_id}>{candidate.id || candidate.element_id}</li>
          ))}
        </ul>
        <Checkbox
          label='再抽出されても作成しない（削除の記録を修正台帳に登録する）'
          isChecked={suppress}
          onChange={(event) => setSuppress(event.target.checked)}
        />
        <div className='n-body-small n-text-palette-neutral-text-weak'>
          このチェックボックスをオフにした場合、同じ名前のエンティティが次回の取り込みで再作成される。削除は「グラフ設定」の「保守」タブの「修正台帳」から取り消せる。
        </div>
        {errorText && <Banner type='danger' usage='inline' description={errorText} />}
      </Dialog.Content>
      <Dialog.Actions>
        <Button fill='outlined' onClick={onClose} isDisabled={submitting}>
          キャンセル
        </Button>
        <Button color='danger' onClick={handleDelete} isLoading={submitting} isDisabled={candidates.length === 0}>
          削除する
        </Button>
      </Dialog.Actions>
    </Dialog>
  );
}
