import { Banner, Dialog, Flex, Radio } from '@neo4j-ndl/react';
import { RETRY_OPIONS } from '../../../utils/Constants';
import { useFileContext } from '../../../context/UsersFiles';
import { capitalize } from '../../../utils/Utils';
import { BannerAlertProps } from '../../../types';
import ButtonWithToolTip from '../../UI/ButtonWithToolTip';
import { memo } from 'react';

function RetryConfirmationDialog({
  open,
  onClose,
  fileId,
  retryLoading,
  retryHandler,
  alertStatus,
  onBannerClose,
}: {
  open: boolean;
  onClose: () => void;
  fileId: string;
  retryLoading: boolean;
  alertStatus: BannerAlertProps;
  retryHandler: (filename: string, retryoption: string) => void;
  onBannerClose: () => void;
}) {
  const { filesData, setFilesData } = useFileContext();
  const file = filesData.find((c) => c.id === fileId);
  // 抽出に失敗したチャンクのある完了済みのファイルでは「続きから」も選べる（そのチャンクだけ抽出し直す。T16）
  const hasSkippedChunks = file?.status === 'Completed' && (file?.skipped_chunks ?? 0) > 0;
  const RetryOptionsForFile =
    file?.status === 'Failed' || file?.status === 'Cancelled'
      ? RETRY_OPIONS
      : RETRY_OPIONS.filter(
          (option) =>
            option !== 'start_from_beginning' && (option !== 'start_from_last_processed_position' || hasSkippedChunks)
        );
  // 続行で送るのは、この窓で選んだ表示中の選択肢だけ（前回の retryOption が残っていても、表示していなければ送らない）
  const selectedRetryOption =
    file?.retryOptionStatus && RetryOptionsForFile.includes(file.retryOption) ? file.retryOption : '';
  return (
    <Dialog isOpen={open} onClose={onClose}>
      <Dialog.Header>再処理オプション</Dialog.Header>
      <Dialog.Description>
        「続行」をクリックすると、これらのファイルは「再処理可能」としてマークされます。その後、「グラフを生成」をクリックすると再処理が開始されます。
      </Dialog.Description>
      <Dialog.Content>
        {alertStatus.showAlert && (
          <Banner isCloseable onClose={onBannerClose} className='my-4' type={alertStatus.alertType} usage='inline'>
            {alertStatus.alertMessage}
          </Banner>
        )}
        <Flex>
          {RetryOptionsForFile.map((o, i) => {
            return (
              <Radio
                key={`${o}${i}`}
                onChange={(e) => {
                  setFilesData((prev) => {
                    return prev.map((f) => {
                      return f.id === fileId ? { ...f, retryOptionStatus: e.target.checked, retryOption: o } : f;
                    });
                  });
                }}
                htmlAttributes={{
                  name: 'retryoptions',
                  onKeyDown: (e) => {
                    if (e.code === 'Enter' && selectedRetryOption.length) {
                      retryHandler(file?.name as string, selectedRetryOption);
                    }
                  },
                }}
                isChecked={o === file?.retryOption && file?.retryOptionStatus}
                label={o
                  .split('_')
                  .map((s) => capitalize(s))
                  .join(' ')}
              />
            );
          })}
        </Flex>
        <Dialog.Actions>
          <Dialog.Actions className='mt-3!'>
            <ButtonWithToolTip
              placement='left'
              label='再処理実行ボタン'
              text={
                !selectedRetryOption.length ? `いずれかのオプションを選択してください` : 'ステータスを「再処理可能」にリセット'
              }
              loading={retryLoading}
              disabled={!selectedRetryOption.length}
              onClick={() => {
                retryHandler(file?.name as string, selectedRetryOption);
              }}
            >
              続行
            </ButtonWithToolTip>
          </Dialog.Actions>
        </Dialog.Actions>
      </Dialog.Content>
    </Dialog>
  );
}
export default memo(RetryConfirmationDialog);
