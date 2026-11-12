import { Banner, Button, Checkbox, Dialog, Select } from '@neo4j-ndl/react';
import { useMemo, useState } from 'react';
import {
  createRelationship,
  deleteRelationship,
  describeGraphEditError,
  updateRelationship,
} from '../../../services/GraphEdit';
import {
  GraphEditCandidate,
  GraphEditRelationshipResult,
  GraphEditRelationshipTarget,
  GraphEditVocabulary,
} from '../../../types';
import { showSuccessToast } from '../../../utils/Toasts';
import {
  NameOption,
  ReservedNames,
  relationshipTypeOptionGroups,
  validateRelationshipType,
} from '../../../utils/GraphEditUtils';

const candidateName = (candidate: GraphEditCandidate) => candidate.id || candidate.element_id;

export const relationshipText = (source: string, type: string, target: string) => `${source} -[${type}]-> ${target}`;

// リレーションの種類を、今のグラフと EDC の語彙から選ぶ（新しい種類は入力して作れる）
function RelationshipTypeSelect({
  value,
  onChange,
  vocabulary,
  reserved,
  errorText,
}: {
  value: string;
  onChange: (value: string) => void;
  vocabulary: GraphEditVocabulary | null;
  reserved: ReservedNames;
  errorText: string;
}) {
  const groups = useMemo(() => relationshipTypeOptionGroups(vocabulary, reserved), [vocabulary, reserved]);
  return (
    <Select
      type='creatable'
      label='リレーションタイプ'
      size='medium'
      isFluid={true}
      errorText={errorText}
      helpText='一覧から選択するか、英大文字・数字・_ で入力する（例: APPLIES_TO）。'
      selectProps={{
        options: groups,
        value: value ? { value, label: value } : null,
        onChange: (option) => onChange((option as NameOption | null)?.value ?? ''),
        onCreateOption: (input) => onChange(input.trim().toUpperCase()),
        formatCreateLabel: (input) => `「${input.trim().toUpperCase()}」を使用`,
        placeholder: 'リレーションタイプを選択または入力',
        menuPosition: 'fixed',
        isClearable: true,
      }}
    />
  );
}

// M6 選んだ 2 つのエンティティの間にリレーションを追加する
export function CreateRelationshipDialog({
  open,
  source,
  target,
  vocabulary,
  reserved,
  user,
  onClose,
  onCreated,
}: {
  open: boolean;
  source: GraphEditCandidate;
  target: GraphEditCandidate;
  vocabulary: GraphEditVocabulary | null;
  reserved: ReservedNames;
  user: string;
  onClose: () => void;
  onCreated: (result: GraphEditRelationshipResult) => void;
}) {
  const [ends, setEnds] = useState<{ from: GraphEditCandidate; to: GraphEditCandidate }>({ from: source, to: target });
  const [type, setType] = useState<string>('');
  const [touched, setTouched] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [errorText, setErrorText] = useState<string>('');
  const typeError = validateRelationshipType(type, reserved);

  const handleCreate = async () => {
    setTouched(true);
    if (typeError !== '') {
      return;
    }
    setSubmitting(true);
    setErrorText('');
    try {
      const result = await createRelationship({
        source_element_id: ends.from.element_id,
        source_expected_id: ends.from.id,
        target_element_id: ends.to.element_id,
        target_expected_id: ends.to.id,
        type,
        props: {},
        user,
      });
      showSuccessToast(
        `リレーション ${relationshipText(candidateName(ends.from), type, candidateName(ends.to))} を追加しました。`
      );
      onCreated(result);
    } catch (error) {
      setErrorText(describeGraphEditError(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog isOpen={open} onClose={onClose} size='medium' htmlAttributes={{ 'aria-label': 'リレーションの追加' }}>
      <Dialog.Header>リレーションの追加</Dialog.Header>
      <Dialog.Content className='flex flex-col gap-3'>
        <div className='n-body-medium'>
          始点: <b>{candidateName(ends.from)}</b>
          <br />
          終点: <b>{candidateName(ends.to)}</b>
        </div>
        <div>
          <Button size='small' fill='outlined' onClick={() => setEnds({ from: ends.to, to: ends.from })}>
            向きを反転
          </Button>
        </div>
        <RelationshipTypeSelect
          value={type}
          onChange={(value) => {
            setType(value);
            setTouched(true);
          }}
          vocabulary={vocabulary}
          reserved={reserved}
          errorText={touched ? typeError : ''}
        />
        <div className='n-body-small n-text-palette-neutral-text-weak'>
          追加したリレーションは修正台帳に記録され、取り込み後に存在しない場合は再作成される。
        </div>
        {errorText && <Banner type='danger' usage='inline' description={errorText} />}
      </Dialog.Content>
      <Dialog.Actions>
        <Button fill='outlined' onClick={onClose} isDisabled={submitting}>
          キャンセル
        </Button>
        <Button onClick={handleCreate} isLoading={submitting} isDisabled={typeError !== ''}>
          追加する
        </Button>
      </Dialog.Actions>
    </Dialog>
  );
}

const expectedOf = (relationship: GraphEditRelationshipTarget) => ({
  src: relationship.source.id,
  type: relationship.type,
  tgt: relationship.target.id,
});

// M8 リレーションの種類を変える（要素 ID が変わる）
export function ChangeRelationshipTypeDialog({
  open,
  relationship,
  vocabulary,
  reserved,
  user,
  onClose,
  onUpdated,
}: {
  open: boolean;
  relationship: GraphEditRelationshipTarget;
  vocabulary: GraphEditVocabulary | null;
  reserved: ReservedNames;
  user: string;
  onClose: () => void;
  onUpdated: (result: GraphEditRelationshipResult) => void;
}) {
  const [type, setType] = useState<string>('');
  const [touched, setTouched] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [errorText, setErrorText] = useState<string>('');
  const validation = validateRelationshipType(type, reserved);
  const typeError = validation === '' && type === relationship.type ? '現在と同じリレーションタイプです。' : validation;
  const current = relationshipText(
    candidateName(relationship.source),
    relationship.type,
    candidateName(relationship.target)
  );

  const handleUpdate = async () => {
    setTouched(true);
    if (typeError !== '') {
      return;
    }
    setSubmitting(true);
    setErrorText('');
    try {
      const result = await updateRelationship({
        element_id: relationship.element_id,
        expected: expectedOf(relationship),
        new_type: type,
        reverse: false,
        user,
      });
      showSuccessToast(`リレーションタイプを ${relationship.type} から ${type} に変更しました。`);
      onUpdated(result);
    } catch (error) {
      setErrorText(describeGraphEditError(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog isOpen={open} onClose={onClose} size='medium' htmlAttributes={{ 'aria-label': 'リレーションタイプの変更' }}>
      <Dialog.Header>リレーションタイプの変更</Dialog.Header>
      <Dialog.Content className='flex flex-col gap-3'>
        <div className='n-body-medium'>
          現在のリレーション: <b>{current}</b>
        </div>
        <RelationshipTypeSelect
          value={type}
          onChange={(value) => {
            setType(value);
            setTouched(true);
          }}
          vocabulary={vocabulary}
          reserved={reserved}
          errorText={touched ? typeError : ''}
        />
        <div className='n-body-small n-text-palette-neutral-text-weak'>
          プロパティを保持したまま付け替える。変更前のリレーションは削除の記録として、変更後のリレーションは追加したリレーションとして修正台帳に記録される。
        </div>
        {errorText && <Banner type='danger' usage='inline' description={errorText} />}
      </Dialog.Content>
      <Dialog.Actions>
        <Button fill='outlined' onClick={onClose} isDisabled={submitting}>
          キャンセル
        </Button>
        <Button onClick={handleUpdate} isLoading={submitting} isDisabled={typeError !== ''}>
          変更する
        </Button>
      </Dialog.Actions>
    </Dialog>
  );
}

// M7 リレーションの削除。「再び抽出されても作らない」を既定で入れる。
export function DeleteRelationshipDialog({
  open,
  relationship,
  user,
  onClose,
  onDeleted,
}: {
  open: boolean;
  relationship: GraphEditRelationshipTarget;
  user: string;
  onClose: () => void;
  onDeleted: (elementId: string) => void;
}) {
  const [suppress, setSuppress] = useState<boolean>(true);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [errorText, setErrorText] = useState<string>('');
  const current = relationshipText(
    candidateName(relationship.source),
    relationship.type,
    candidateName(relationship.target)
  );

  const handleDelete = async () => {
    setSubmitting(true);
    setErrorText('');
    try {
      await deleteRelationship({
        element_id: relationship.element_id,
        expected: expectedOf(relationship),
        suppress,
        user,
      });
      showSuccessToast(`リレーション ${current} を削除しました。`);
      onDeleted(relationship.element_id);
    } catch (error) {
      setErrorText(describeGraphEditError(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog isOpen={open} onClose={onClose} size='small' htmlAttributes={{ 'aria-label': 'リレーションの削除' }}>
      <Dialog.Header>リレーションの削除</Dialog.Header>
      <Dialog.Content className='flex flex-col gap-3'>
        <div className='n-body-medium'>
          <b>{current}</b> を削除します。
        </div>
        <Checkbox
          label='再抽出されても作成しない（削除の記録を修正台帳に登録する）'
          isChecked={suppress}
          onChange={(event) => setSuppress(event.target.checked)}
        />
        {errorText && <Banner type='danger' usage='inline' description={errorText} />}
      </Dialog.Content>
      <Dialog.Actions>
        <Button fill='outlined' onClick={onClose} isDisabled={submitting}>
          キャンセル
        </Button>
        <Button color='danger' onClick={handleDelete} isLoading={submitting}>
          削除する
        </Button>
      </Dialog.Actions>
    </Dialog>
  );
}
