import { Select } from '@neo4j-ndl/react';
import { useMemo } from 'react';
import { GraphEditVocabulary } from '../../../types';
import { NameOption, NameOptionGroup, ReservedNames, labelOptionGroups } from '../../../utils/GraphEditUtils';

// ラベルを現在のグラフと EDC の語彙から選択する。新しいラベルは入力して追加できる（形式は保存の前に検査する）。
export default function LabelsSelect({
  value,
  onChange,
  vocabulary,
  reserved,
  currentLabels,
  extraLabels = [],
  errorText,
  isDisabled = false,
}: {
  value: string[];
  onChange: (labels: string[]) => void;
  vocabulary: GraphEditVocabulary | null;
  reserved: ReservedNames;
  currentLabels: string[];
  extraLabels?: string[];
  errorText?: string;
  isDisabled?: boolean;
}) {
  const groups = useMemo(
    () => labelOptionGroups(vocabulary, reserved, currentLabels, extraLabels),
    [vocabulary, reserved, currentLabels, extraLabels]
  );
  return (
    <Select<NameOption, true, NameOptionGroup>
      type='creatable'
      label='ラベル'
      size='medium'
      isFluid={true}
      isDisabled={isDisabled}
      errorText={errorText}
      selectProps={{
        isMulti: true,
        options: groups,
        value: value.map((name) => ({ value: name, label: name })),
        onChange: (options) => onChange(options.map((option) => option.value)),
        onCreateOption: (input) => {
          const name = input.trim();
          if (name !== '' && !value.includes(name)) {
            onChange([...value, name]);
          }
        },
        formatCreateLabel: (input) => `「${input.trim()}」を新しいラベルとして追加`,
        placeholder: 'ラベルを選択または入力',
        menuPosition: 'fixed',
        isDisabled,
      }}
    />
  );
}
