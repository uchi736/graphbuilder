import { Button, Select } from '@neo4j-ndl/react';
import { useMemo, useState } from 'react';
import { createRelationship, deleteRelationship, updateRelationship } from '../../../services/GraphEdit';
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
  candidateName,
  relationshipExpected,
  relationshipTypeOptionGroups,
  validateRelationshipType,
} from '../../../utils/GraphEditUtils';
import { GraphEditDialog, SuppressCheckbox, useGraphEditSubmit } from './GraphEditDialog';

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

// 種類の選択の状態。選択または送信の後に検査の結果を表示する
function useRelationshipTypeInput(validate: (type: string) => string) {
  const [type, setType] = useState<string>('');
  const [touched, setTouched] = useState<boolean>(false);
  const typeError = validate(type);
  return {
    type,
    typeError,
    shownError: touched ? typeError : '',
    touch: () => setTouched(true),
    onChange: (value: string) => {
      setType(value);
      setTouched(true);
    },
  };
}

// M6 選んだ 2 つのエンティティの間にリレーションを追加する
export function CreateRelationshipDialog({
  source,
  target,
  vocabulary,
  reserved,
  user,
  onClose,
  onCreated,
}: {
  source: GraphEditCandidate;
  target: GraphEditCandidate;
  vocabulary: GraphEditVocabulary | null;
  reserved: ReservedNames;
  user: string;
  onClose: () => void;
  onCreated: (result: GraphEditRelationshipResult) => void;
}) {
  const [ends, setEnds] = useState<{ from: GraphEditCandidate; to: GraphEditCandidate }>({ from: source, to: target });
  const input = useRelationshipTypeInput((type) => validateRelationshipType(type, reserved));
  const { submitting, errorText, submit } = useGraphEditSubmit();
  const { type, typeError } = input;

  const handleCreate = async () => {
    input.touch();
    if (typeError !== '') {
      return;
    }
    await submit(async () => {
      const result = await createRelationship({
        source_element_id: ends.from.element_id,
        source_expected_id: ends.from.id,
        target_element_id: ends.to.element_id,
        target_expected_id: ends.to.id,
        type,
        user,
      });
      showSuccessToast(
        `リレーション ${relationshipText(candidateName(ends.from), type, candidateName(ends.to))} を追加しました。`
      );
      onCreated(result);
    });
  };

  return (
    <GraphEditDialog
      title='リレーションの追加'
      size='medium'
      errorText={errorText}
      submitLabel='追加する'
      submitting={submitting}
      isSubmitDisabled={typeError !== ''}
      onSubmit={handleCreate}
      onClose={onClose}
    >
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
        onChange={input.onChange}
        vocabulary={vocabulary}
        reserved={reserved}
        errorText={input.shownError}
      />
      <div className='n-body-small n-text-palette-neutral-text-weak'>
        追加したリレーションは修正台帳に記録され、取り込み後に存在しない場合は再作成される。
      </div>
    </GraphEditDialog>
  );
}

// M8 リレーションの種類を変える（要素 ID が変わる）
export function ChangeRelationshipTypeDialog({
  relationship,
  vocabulary,
  reserved,
  user,
  onClose,
  onUpdated,
}: {
  relationship: GraphEditRelationshipTarget;
  vocabulary: GraphEditVocabulary | null;
  reserved: ReservedNames;
  user: string;
  onClose: () => void;
  onUpdated: (result: GraphEditRelationshipResult) => void;
}) {
  const input = useRelationshipTypeInput((type) => {
    const validation = validateRelationshipType(type, reserved);
    return validation === '' && type === relationship.type ? '現在と同じリレーションタイプです。' : validation;
  });
  const { submitting, errorText, submit } = useGraphEditSubmit();
  const { type, typeError } = input;
  const current = relationshipText(
    candidateName(relationship.source),
    relationship.type,
    candidateName(relationship.target)
  );

  const handleUpdate = async () => {
    input.touch();
    if (typeError !== '') {
      return;
    }
    await submit(async () => {
      const result = await updateRelationship({
        element_id: relationship.element_id,
        expected: relationshipExpected(relationship),
        new_type: type,
        reverse: false,
        user,
      });
      showSuccessToast(`リレーションタイプを ${relationship.type} から ${type} に変更しました。`);
      onUpdated(result);
    });
  };

  return (
    <GraphEditDialog
      title='リレーションタイプの変更'
      size='medium'
      errorText={errorText}
      submitLabel='変更する'
      submitting={submitting}
      isSubmitDisabled={typeError !== ''}
      onSubmit={handleUpdate}
      onClose={onClose}
    >
      <div className='n-body-medium'>
        現在のリレーション: <b>{current}</b>
      </div>
      <RelationshipTypeSelect
        value={type}
        onChange={input.onChange}
        vocabulary={vocabulary}
        reserved={reserved}
        errorText={input.shownError}
      />
      <div className='n-body-small n-text-palette-neutral-text-weak'>
        プロパティを保持したまま付け替える。変更前のリレーションは削除の記録として、変更後のリレーションは追加したリレーションとして修正台帳に記録される。
      </div>
    </GraphEditDialog>
  );
}

// M7 リレーションの削除。「再び抽出されても作らない」を既定で入れる。
export function DeleteRelationshipDialog({
  relationship,
  user,
  onClose,
  onDeleted,
}: {
  relationship: GraphEditRelationshipTarget;
  user: string;
  onClose: () => void;
  onDeleted: (elementId: string) => void;
}) {
  const [suppress, setSuppress] = useState<boolean>(true);
  const { submitting, errorText, submit } = useGraphEditSubmit();
  const current = relationshipText(
    candidateName(relationship.source),
    relationship.type,
    candidateName(relationship.target)
  );

  const handleDelete = () =>
    submit(async () => {
      await deleteRelationship({
        element_id: relationship.element_id,
        expected: relationshipExpected(relationship),
        suppress,
        user,
      });
      showSuccessToast(`リレーション ${current} を削除しました。`);
      onDeleted(relationship.element_id);
    });

  return (
    <GraphEditDialog
      title='リレーションの削除'
      size='small'
      errorText={errorText}
      submitLabel='削除する'
      submitColor='danger'
      submitting={submitting}
      onSubmit={handleDelete}
      onClose={onClose}
    >
      <div className='n-body-medium'>
        <b>{current}</b> を削除します。
      </div>
      <SuppressCheckbox isChecked={suppress} onChange={setSuppress} />
    </GraphEditDialog>
  );
}
