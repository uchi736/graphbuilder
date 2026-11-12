import { Button, Dialog, Typography } from '@neo4j-ndl/react';
import { memo } from 'react';

// 取り込み済みの文書と同じ名前のファイルを追加する前の確認（C8）。window.confirm は使わない
function ReplaceConfirmDialog({
  open,
  names,
  hasNewFiles,
  onReplace,
  onSkip,
  onCancel,
}: {
  open: boolean;
  names: string[];
  hasNewFiles: boolean;
  onReplace: () => void;
  onSkip: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog isOpen={open} onClose={onCancel}>
      <Dialog.Header>前の版の置き換え</Dialog.Header>
      <Dialog.Content>
        <Typography variant='body-medium'>
          {`次の ${names.length} 件は、完了済みの文書と同じ名前である。アップロードするとファイル一覧のステータスは「New」に戻り、「グラフを生成」で前の版のチャンク・その文書のみのエンティティ・章・節を削除した後に再取り込みする。他の文書と共有するエンティティと、規則により生成した文書番号・型式のエンティティは保持される。`}
        </Typography>
        <ul className='mt-3 max-h-48 overflow-auto list-disc pl-5'>
          {names.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </Dialog.Content>
      <Dialog.Actions className='mt-3'>
        <Button fill='outlined' onClick={onCancel}>
          キャンセル
        </Button>
        {hasNewFiles && (
          <Button fill='outlined' onClick={onSkip}>
            新しいファイルのみ追加
          </Button>
        )}
        <Button onClick={onReplace}>置き換えて追加</Button>
      </Dialog.Actions>
    </Dialog>
  );
}
export default memo(ReplaceConfirmDialog);
