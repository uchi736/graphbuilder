import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Select, Typography } from '@neo4j-ndl/react';
import ButtonWithToolTip from '../../../UI/ButtonWithToolTip';
import { OptionType, TupleCreationProps } from '../../../../types';
import { appLabels } from '../../../../utils/Constants';
import { useFileContext } from '../../../../context/UsersFiles';
import { useCredentials } from '../../../../context/UserCredentials';
import { SchemaOption } from '../../../../services/SchemaOptions';
interface IErrorState {
  showError: boolean;
  errorMessage: string;
}
// 現在のグラフに存在するラベルとリレーションタイプ（グラフ設定を開いた時点で取得する）。選択肢に追加して表示する
type GraphPatternProps = TupleCreationProps & {
  graphLabels?: SchemaOption[];
  graphRelationshipTypes?: SchemaOption[];
};
const NO_GRAPH_OPTIONS: SchemaOption[] = [];
const COMMA_ERROR = 'カンマを含まない名称を入力してください。この欄ではカンマを使用できません';
// 3 つの選択欄に共通の一覧の指定。一覧が画面の下端で切れないよう、下の空きが足りない場合は上向きに開く。
// maxMenuHeight は開く向きの判定に使われる。一覧の高さは NDL の CSS（size='medium' は 280px）で決まるため、同じ値にする。
// NDL の CSS（.ndl-select-menu-list.ndl-medium の max-height）が react-select の縮めた値より優先されるため、!important で縮めた値を使う
const MENU_PROPS = {
  menuPlacement: 'auto' as const,
  maxMenuHeight: 280,
  menuPosition: 'fixed' as const,
  styles: { menuList: (base: { maxHeight?: unknown }) => ({ maxHeight: `${base.maxHeight}px !important` }) },
};
// 選択肢に現在のグラフの名称を追加する。同じ名称は 1 件にまとめ、件数を取得できた名称は表示名に件数を付与する。
// 選択後の値は名称のみとする（handleNewValue が名称から選択肢を作成する）
const withGraphOptions = (options: OptionType[], graphOptions: SchemaOption[]): OptionType[] => {
  const countOf = new Map(graphOptions.map((option) => [option.name, option.count]));
  const shown = (label: string, count: number | null | undefined) => {
    if (typeof count === 'number') {
      return `${label}（${count} 件）`;
    }
    return label;
  };
  const seen = new Set<string>();
  const merged: OptionType[] = [];
  options.forEach((option) => {
    if (!seen.has(option.value)) {
      seen.add(option.value);
      merged.push({ value: option.value, label: shown(option.label, countOf.get(option.value)) });
    }
  });
  graphOptions.forEach((option) => {
    if (!seen.has(option.name)) {
      seen.add(option.name);
      merged.push({ value: option.name, label: shown(option.name, option.count) });
    }
  });
  return merged;
};
const GraphPattern: React.FC<GraphPatternProps> = ({
  selectedSource,
  selectedType,
  selectedTarget,
  onPatternChange,
  onAddPattern,
  graphLabels = NO_GRAPH_OPTIONS,
  graphRelationshipTypes = NO_GRAPH_OPTIONS,
}) => {
  const {
    sourceOptions,
    setSourceOptions,
    typeOptions,
    setTypeOptions,
    targetOptions,
    setTargetOptions,
    setSelectedRels,
    selectedRels,
  } = useFileContext();
  const [inputValues, setInputValues] = useState<{ source: string; type: string; target: string }>({
    source: '',
    type: '',
    target: '',
  });
  const [showWarning, setShowWarning] = useState<Record<'source' | 'type' | 'target', IErrorState>>({
    source: { showError: false, errorMessage: '' },
    type: { showError: false, errorMessage: '' },
    target: { showError: false, errorMessage: '' },
  });
  const sourceRef = useRef<HTMLDivElement | null>(null);
  const { userCredentials } = useCredentials();
  const sourceSelectOptions = useMemo(() => withGraphOptions(sourceOptions, graphLabels), [sourceOptions, graphLabels]);
  const targetSelectOptions = useMemo(() => withGraphOptions(targetOptions, graphLabels), [targetOptions, graphLabels]);
  const typeSelectOptions = useMemo(
    () => withGraphOptions(typeOptions, graphRelationshipTypes),
    [typeOptions, graphRelationshipTypes]
  );
  const deduplicateOptions = (options: OptionType[]): OptionType[] => {
    const seen = new Set<string>();
    return options.filter((option) => {
      if (seen.has(option.value)) {
        return false;
      }
      seen.add(option.value);
      return true;
    });
  };

  useEffect(() => {
    const isGlobalStateSet =
      selectedRels.length > 0 || sourceOptions.length > 0 || typeOptions.length > 0 || targetOptions.length > 0;
    if (isGlobalStateSet) {
      return;
    }
    const selectedNodeRelsStr = localStorage.getItem('selectedRelationshipLabels');
    if (selectedNodeRelsStr != null) {
      const selectedGraphOptions = JSON.parse(selectedNodeRelsStr);
      if (userCredentials?.uri === selectedGraphOptions.db) {
        const rels = selectedGraphOptions.selectedOptions;
        const sourceSet = new Set<string>();
        const typeSet = new Set<string>();
        const targetSet = new Set<string>();
        const mappedRels = rels.map((rel: { value: string }) => {
          const [sourceVal, typeVal, targetVal] = rel.value.split(',');
          sourceSet.add(sourceVal);
          typeSet.add(typeVal);
          targetSet.add(targetVal);
          return {
            source: { value: sourceVal, label: sourceVal },
            type: { value: typeVal, label: typeVal },
            target: { value: targetVal, label: targetVal },
          };
        });
        const savedTypes: OptionType[] = Array.from(typeSet).map((val) => ({ value: val, label: val }));
        const combinedSourceTarget = new Set([...sourceSet, ...targetSet]);
        const combinedSourceTargetOptions: OptionType[] = Array.from(combinedSourceTarget).map((val) => ({
          value: val,
          label: val,
        }));

        setSelectedRels(mappedRels);
        setSourceOptions(combinedSourceTargetOptions);
        setTypeOptions(savedTypes);
        setTargetOptions(combinedSourceTargetOptions);
      }
    }
  }, []);

  useEffect(() => {
    let timeoutId: NodeJS.Timeout;
    timeoutId = setTimeout(() => {
      if (sourceOptions.length > 0) {
        const deduped = deduplicateOptions(sourceOptions);
        if (deduped.length !== sourceOptions.length) {
          setSourceOptions(deduped);
        }
      }

      if (targetOptions.length > 0) {
        const deduped = deduplicateOptions(targetOptions);
        if (deduped.length !== targetOptions.length) {
          setTargetOptions(deduped);
        }
      }

      if (typeOptions.length > 0) {
        const deduped = deduplicateOptions(typeOptions);
        if (deduped.length !== typeOptions.length) {
          setTypeOptions(deduped);
        }
      }
    }, 1000);

    return () => {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
  }, []);

  const handleNewValue = (newValue: string, type: 'source' | 'type' | 'target') => {
    const regex = /^[^,]*$/;
    if (!newValue.trim()) {
      setShowWarning((old) => ({ ...old, [type]: { showError: true, errorMessage: '値を入力してください' } }));
      return;
    }
    if (!regex.test(newValue)) {
      setShowWarning((old) => ({
        ...old,
        [type]: {
          showError: true,
          errorMessage: COMMA_ERROR,
        },
      }));
    } else {
      setShowWarning((old) => ({ ...old, [type]: { showError: false, errorMessage: '' } }));
      const newOption: OptionType = { value: newValue.trim(), label: newValue.trim() };
      const checkUniqueValue = (list: OptionType[], value: OptionType) => {
        const exists = list.some((opt) => opt.value === value.value);
        const updatedList = exists ? list : [...list, value];
        return deduplicateOptions(updatedList);
      };

      switch (type) {
        case 'source':
          setSourceOptions((prev) => checkUniqueValue(prev, newOption));
          setTargetOptions((prev) => checkUniqueValue(prev, newOption));
          onPatternChange(newOption, selectedType as OptionType, selectedTarget as OptionType);
          break;
        case 'type':
          setTypeOptions((prev) => checkUniqueValue(prev, newOption));
          onPatternChange(selectedSource as OptionType, newOption, selectedTarget as OptionType);
          break;
        case 'target':
          setTargetOptions((prev) => checkUniqueValue(prev, newOption));
          setSourceOptions((prev) => checkUniqueValue(prev, newOption));
          onPatternChange(selectedSource as OptionType, selectedType as OptionType, newOption);
          break;
        default:
          // Invalid type provided
          break;
      }
      setInputValues((prev) => ({ ...prev, [type]: '' }));
    }
  };

  const handleInputChange = (newValue: string, type: 'source' | 'type' | 'target') => {
    setInputValues((prev) => ({ ...prev, [type]: newValue }));
  };

  const handleAddPattern = () => {
    onAddPattern();
    setTimeout(() => {
      const selectInput = sourceRef.current?.querySelector('input');
      selectInput?.focus();
    }, 100);
  };

  const handleBlurPattern = (value: string, type: 'source' | 'type' | 'target') => {
    const regex = /^[^,]*$/;
    if (!regex.test(value)) {
      setShowWarning((old) => ({
        ...old,
        [type]: {
          showError: true,
          errorMessage: COMMA_ERROR,
        },
      }));
    } else {
      setShowWarning((old) => ({ ...old, [type]: { showError: false, errorMessage: '' } }));
    }
  };
  const isDisabled = !selectedSource?.value.length || !selectedTarget?.value.length || !selectedType?.value.length;
  return (
    <div className='bg-white rounded-lg shadow-md'>
      <div className='flex'>
        <Typography variant='subheading-small'>{appLabels.graphPatternTuple}</Typography>
      </div>
      <div className='flex gap-4 items-end mb-6 mt-3 justify-between'>
        <div className='w-1/4' ref={sourceRef}>
          <Select
            label='始点ノードのラベル'
            size='medium'
            errorText={showWarning.source.showError ? showWarning.source.errorMessage : ''}
            selectProps={{
              isClearable: true,
              placeholder: '選択または入力',
              options: sourceSelectOptions,
              ...MENU_PROPS,
              onChange: (selected) => {
                if (!selected) {
                  onPatternChange(null, selectedType, selectedTarget);
                } else {
                  handleNewValue(selected.value, 'source');
                }
              },
              value: selectedSource,
              inputValue: inputValues.source,
              onInputChange: (newValue) => handleInputChange(newValue, 'source'),
              onCreateOption: (newOption) => handleNewValue(newOption, 'source'),
              onBlur: (e) => {
                handleBlurPattern(e.target.value, 'source');
              },
            }}
            type='creatable'
          />
        </div>
        <Select
          label='リレーションタイプ'
          size='medium'
          errorText={showWarning.type.showError ? showWarning.type.errorMessage : ''}
          selectProps={{
            isClearable: true,
            placeholder: '選択または入力',
            options: typeSelectOptions,
            ...MENU_PROPS,
            onChange: (selected) => {
              if (!selected) {
                onPatternChange(selectedSource, null, selectedTarget);
              } else {
                handleNewValue(selected.value, 'type');
              }
            },
            value: selectedType,
            inputValue: inputValues.type,
            onInputChange: (newValue) => handleInputChange(newValue, 'type'),
            onCreateOption: (newOption) => handleNewValue(newOption, 'type'),
            onBlur: (e) => {
              handleBlurPattern(e.target.value, 'type');
            },
          }}
          type='creatable'
          className='w-1/4'
        />
        <Select
          label='終点ノードのラベル'
          errorText={showWarning.target.showError ? showWarning.target.errorMessage : ''}
          size='medium'
          selectProps={{
            isClearable: true,
            placeholder: '選択または入力',
            options: targetSelectOptions,
            ...MENU_PROPS,
            onChange: (selected) => {
              if (!selected) {
                onPatternChange(selectedSource, selectedType, null);
              } else {
                handleNewValue(selected.value, 'target');
              }
            },
            value: selectedTarget,
            inputValue: inputValues.target,
            onInputChange: (newValue) => handleInputChange(newValue, 'target'),
            onCreateOption: (newOption) => handleNewValue(newOption, 'target'),
            onBlur: (e) => {
              handleBlurPattern(e.target.value, 'target');
            },
          }}
          type='creatable'
          className='w-1/4'
        />
        <div className='flex items-center' tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && handleAddPattern()}>
          <ButtonWithToolTip
            text='パターンを追加'
            placement='top'
            onClick={handleAddPattern}
            label='パターンを追加'
            size='medium'
            disabled={isDisabled}
          >
            + 追加
          </ButtonWithToolTip>
        </div>
      </div>
    </div>
  );
};
export default GraphPattern;
