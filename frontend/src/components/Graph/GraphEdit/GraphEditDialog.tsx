import { Banner, Button, Checkbox, Dialog } from '@neo4j-ndl/react';
import { ReactNode, useState } from 'react';
import { describeGraphEditError } from '../../../services/GraphEdit';

// グラフの手動修正の確認・入力のダイアログの枠。見出し・本文・失敗の文・キャンセルと実行のボタンを並べる
export function GraphEditDialog({
  title,
  size,
  contentClassName = 'flex flex-col gap-3',
  errorText = '',
  submitLabel,
  submitColor,
  submitting,
  isSubmitDisabled,
  onSubmit,
  onClose,
  children,
}: {
  title: string;
  size: 'small' | 'medium' | 'large';
  contentClassName?: string;
  errorText?: string;
  submitLabel: string;
  submitColor?: 'primary' | 'danger';
  submitting: boolean;
  isSubmitDisabled?: boolean;
  onSubmit: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Dialog isOpen={true} onClose={onClose} size={size} htmlAttributes={{ 'aria-label': title }}>
      <Dialog.Header>{title}</Dialog.Header>
      <Dialog.Content className={contentClassName}>
        {children}
        {errorText && <Banner type='danger' usage='inline' description={errorText} />}
      </Dialog.Content>
      <Dialog.Actions>
        <Button fill='outlined' onClick={onClose} isDisabled={submitting}>
          キャンセル
        </Button>
        <Button color={submitColor} onClick={onSubmit} isLoading={submitting} isDisabled={isSubmitDisabled}>
          {submitLabel}
        </Button>
      </Dialog.Actions>
    </Dialog>
  );
}

// 送信中の印と失敗の文。submit は失敗を日本語の文にしてダイアログの中に表示する
export function useGraphEditSubmit() {
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [errorText, setErrorText] = useState<string>('');
  const submit = async (action: () => Promise<void>) => {
    setSubmitting(true);
    setErrorText('');
    try {
      await action();
    } catch (error) {
      setErrorText(describeGraphEditError(error));
    } finally {
      setSubmitting(false);
    }
  };
  return { submitting, errorText, submit };
}

// 削除のダイアログの「再抽出されても作成しない」（既定で有効）
export function SuppressCheckbox({
  isChecked,
  onChange,
}: {
  isChecked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <Checkbox
      label='再抽出されても作成しない（削除の記録を修正台帳に登録する）'
      isChecked={isChecked}
      onChange={(event) => onChange(event.target.checked)}
    />
  );
}
