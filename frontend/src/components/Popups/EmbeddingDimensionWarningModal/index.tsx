import { Dialog, Typography, Banner } from '@neo4j-ndl/react';
import { memo, useState } from 'react';
import { EmbeddingModelOption } from '../../../utils/Constants';
import { showErrorToast } from '../../../utils/Toasts';
import ButtonWithToolTip from '../../UI/ButtonWithToolTip';

interface EmbeddingDimensionWarningModalProps {
  open: boolean;
  onClose: () => void;
  onProceed: (provider: string, model: string) => Promise<void>;
  dbDimension: number;
  selectedDimension: number;
  pendingEmbeddingModel: EmbeddingModelOption | null;
  lastEmbeddingModel: EmbeddingModelOption | null;
  onCancel: (provider: string, model: string) => Promise<void>;
}

function EmbeddingDimensionWarningModal({
  open,
  onClose,
  onProceed,
  dbDimension,
  selectedDimension,
  pendingEmbeddingModel,
  lastEmbeddingModel,
  onCancel,
}: EmbeddingDimensionWarningModalProps) {
  const [isProcessing, setIsProcessing] = useState(false);
  const [isSuccess, setIsSuccess] = useState(false);

  const handleProceed = async () => {
    if (!pendingEmbeddingModel) {
      showErrorToast('埋め込みモデルが選択されていません');
      return;
    }

    console.log('User acknowledged dimension mismatch warning');
    console.log('Details:', {
      previousDimension: dbDimension,
      newDimension: selectedDimension,
      provider: pendingEmbeddingModel.provider,
      model: pendingEmbeddingModel.model,
      timestamp: new Date().toISOString(),
    });

    setIsProcessing(true);
    try {
      await onProceed(pendingEmbeddingModel.provider, pendingEmbeddingModel.model);
      setIsSuccess(true);
    } catch (error) {
      console.error('Error changing embedding model:', error);
      showErrorToast('埋め込みモデルの変更に失敗しました。もう一度お試しください。');
    } finally {
      setIsProcessing(false);
    }
  };

  const handleClose = async () => {
    if (!lastEmbeddingModel) {
      setIsSuccess(false);
      onClose();
      return;
    }

    try {
      await onCancel(lastEmbeddingModel.provider, lastEmbeddingModel.model);
      setIsSuccess(false);
      onClose();
    } catch (error) {
      console.error('Error reverting embedding model:', error);
      showErrorToast('埋め込みモデルの復元に失敗しました。もう一度お試しください。');
    }
  };

  const handleContactTeam = () => {
    window.open('https://github.com/neo4j-labs/llm-graph-builder/issues/new', '_blank');
  };

  return (
    <Dialog
      size='medium'
      isOpen={open}
      onClose={handleClose}
      htmlAttributes={{
        'aria-labelledby': 'embedding-dimension-warning-dialog',
      }}
    >
      <Dialog.Header htmlAttributes={{ id: 'embedding-dimension-warning-dialog' }}>
        {isSuccess ? '埋め込みモデルを変更しました' : '重要：埋め込みモデルの更新'}
      </Dialog.Header>
      <Dialog.Content className='n-flex n-flex-col n-gap-token-4'>
        <div className='n-flex n-flex-col n-gap-token-3'>
          <Typography variant='body-medium'>
            埋め込みモデルを新しいモデルに切り替える操作である。新しいモデルは文書を異なる方法で解釈する。
          </Typography>
          <div className='n-flex n-flex-col n-gap-token-2'>
            <Typography variant='body-medium'>
              変更前モデルの次元数：<strong>{dbDimension}</strong>
            </Typography>
            <Typography variant='body-medium'>
              新モデルの次元数：<strong>{selectedDimension}</strong>
            </Typography>
          </div>
          <Typography variant='body-medium'>
            新しいモデルで正しく動作させるには、次の 2 つの手順が必要である。
          </Typography>
          <div className='n-bg-palette-neutral-bg-weak n-p-token-4 n-rounded'>
            <ol className='n-list-decimal n-ml-token-6 n-flex n-flex-col n-gap-token-3'>
              <li>
                <Typography variant='body-medium'>
                  <strong>データベースインデックスの更新：</strong>
                  <strong>「インデックスを更新」</strong>
                  をクリックすると、データベースのベクトルインデックスが新しいモデルに合わせて自動的に更新される。
                </Typography>
              </li>
              <li>
                <Typography variant='body-medium'>
                  <strong>ファイルの再処理：</strong>インデックスの更新後、これまでに処理したすべてのファイルを
                  <strong>手動で</strong>再処理する必要がある。この手順により、文書が新しいモデルに対応する。
                </Typography>
              </li>
            </ol>
          </div>
          <Banner
            type='warning'
            name='Warning Banner'
            description='ファイルの再処理を省略すると、エラーや不正確な結果の原因となる。'
            usage='inline'
          />
        </div>
      </Dialog.Content>
      <Dialog.Actions>
        <ButtonWithToolTip
          text='GitHubを開いてサポート用のIssueを作成'
          label='チームに問い合わせ'
          onClick={handleContactTeam}
          size='medium'
          fill='outlined'
          disabled={isProcessing}
        >
          チームに問い合わせ
        </ButtonWithToolTip>
        <ButtonWithToolTip
          text='埋め込みモデルの変更をキャンセル'
          label='キャンセル'
          onClick={onClose}
          size='medium'
          fill='outlined'
          disabled={isProcessing}
        >
          キャンセル
        </ButtonWithToolTip>
        <ButtonWithToolTip
          text='新しい埋め込み次元数に合わせてベクトルインデックスを更新'
          label='インデックスを更新'
          onClick={handleProceed}
          size='medium'
          loading={isProcessing}
          disabled={isProcessing}
        >
          <strong>インデックスを更新</strong>
        </ButtonWithToolTip>
      </Dialog.Actions>
    </Dialog>
  );
}

export default memo(EmbeddingDimensionWarningModal);
