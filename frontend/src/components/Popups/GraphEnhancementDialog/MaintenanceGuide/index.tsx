import { Typography } from '@neo4j-ndl/react';
import { RETRY_OPIONS, RETRY_OPTION_HELP, RETRY_OPTION_LABELS } from '../../../../utils/Constants';
import { capitalize } from '../../../../utils/Utils';

// 再処理オプションのダイアログ（RetryConfirmation）と抽出方式のエラー文に表示される英語の表示名（例: Start From Beginning）
const englishLabel = (option: string) =>
  option
    .split('_')
    .map((s) => capitalize(s))
    .join(' ');

// 更新・再処理の手順: 文書の更新（同じファイル名での再取り込み）と、再処理オプションの 3 つの選択肢。
// 文面は、更新（document_replace.clear_previous_chunks）・手動で修正したエンティティ（_curated）・続きからの抽出方式の確認の動きに合わせる
export default function MaintenanceGuide() {
  return (
    <div className='flex flex-col gap-6'>
      <section className='flex flex-col gap-2'>
        <Typography variant='subheading-medium'>文書の更新（同じファイル名での再取り込み）</Typography>
        <Typography variant='body-medium'>
          同じファイル名の新しい版で再取り込みするには、ファイル一覧のステータス列の「更新」から同じ名前のファイルを選択する（ステータスが完了済み・失敗・取り消しのローカルファイルの行に表示される）。ファイルを再度追加した場合も、完了済みの文書と同じ名前であれば確認ダイアログが表示される。
        </Typography>
        <Typography variant='body-medium'>
          抽出の開始時に、前の版のチャンク、その文書のチャンクからのみ参照されるエンティティ、規則により生成した章・節と文書層のリレーション（IS_VERSION_OF・REFERS_TO・LISTS・APPLIES_TO・ABOLISHES・REPLACES）を削除した後、分割と抽出を再実行する。他の文書のチャンクからも参照されるエンティティ、規則により生成した文書番号・型式のエンティティ（Standard・Model）、手動で修正したエンティティは保持される。削除した件数は通知に表示される。
        </Typography>
        <Typography variant='body-medium'>
          章・節と文書層は、取り込み後に実行される後処理（文書層と章・節の層）で再生成される。失敗した場合は「グラフ整備」の「後処理ジョブ」で「チェックを入れたジョブを実行」により再実行する。
        </Typography>
      </section>
      <section className='flex flex-col gap-3'>
        <Typography variant='subheading-medium'>再処理オプションの選択（ファイル一覧の「再処理」から）</Typography>
        {RETRY_OPIONS.map((option) => (
          <div key={option} className='flex flex-col gap-1'>
            <Typography variant='body-medium' className='font-semibold!'>
              {`${RETRY_OPTION_LABELS[option] ?? option}（再処理オプションのダイアログでは ${englishLabel(option)}）`}
            </Typography>
            <Typography variant='body-medium'>{RETRY_OPTION_HELP[option]}</Typography>
            <Typography variant='body-small' className='text-palette-neutral-text-weak'>
              {option}
            </Typography>
          </div>
        ))}
        <Typography variant='body-small'>
          再処理オプションを選択するとファイルのステータスは「Ready to
          Reprocess」（再処理可能）になり、「グラフを生成」をクリックすると再処理が開始される。
        </Typography>
      </section>
    </div>
  );
}
