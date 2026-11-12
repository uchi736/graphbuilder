import { Select } from '@neo4j-ndl/react';
import { useEffect, useRef } from 'react';
import { describeGraphEditError, searchEntities } from '../../../services/GraphEdit';
import { GraphEditEntityHit } from '../../../types';
import { showErrorToast } from '../../../utils/Toasts';
import { entityName, visibleLabels } from '../../../utils/GraphEditUtils';

interface EntityOption {
  value: string;
  label: string;
  hit: GraphEditEntityHit;
}

const SEARCH_DELAY_MS = 300;

const toOption = (hit: GraphEditEntityHit): EntityOption => {
  const labels = visibleLabels(hit.node.labels).join('・');
  const documents = hit.documents ?? [];
  const documentText =
    documents.length > 2 ? `${documents.slice(0, 2).join('、')} ほか ${documents.length - 2} 件` : documents.join('、');
  const details = [labels, `チャンク ${hit.chunk_count ?? 0}`, documentText].filter((text) => text !== '').join('、');
  return { value: hit.node.element_id, label: `${entityName(hit.node)}（${details}）`, hit };
};

// 「エンティティの名前検索」入力欄。/graph_edit/search_entities の結果から選ぶ。
export default function EntitySearch({
  onSelect,
  isDisabled = false,
}: {
  onSelect: (hit: GraphEditEntityHit) => void;
  isDisabled?: boolean;
}) {
  const timerRef = useRef<ReturnType<typeof setTimeout>>();
  useEffect(
    () => () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    },
    []
  );

  const loadOptions = (inputValue: string) =>
    new Promise<EntityOption[]>((resolve) => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      const query = inputValue.trim();
      if (query === '') {
        resolve([]);
        return;
      }
      timerRef.current = setTimeout(() => {
        searchEntities(query, 20)
          .then((hits) => resolve((hits ?? []).map(toOption)))
          .catch((error) => {
            showErrorToast(describeGraphEditError(error));
            resolve([]);
          });
      }, SEARCH_DELAY_MS);
    });

  return (
    <div data-testid='graph-edit-entity-search'>
      <Select
        type='async'
        label='エンティティの名前検索'
        size='small'
        isFluid={true}
        isDisabled={isDisabled}
        selectProps={{
          loadOptions,
          cacheOptions: false,
          defaultOptions: false,
          value: null,
          isClearable: false,
          placeholder: '名前の一部を入力',
          menuPosition: 'fixed',
          noOptionsMessage: ({ inputValue }) => {
            return inputValue.trim() === '' ? '名前の一部を入力してください' : '該当するエンティティがありません';
          },
          loadingMessage: () => '検索しています…',
          onChange: (option) => {
            if (option) {
              onSelect((option as EntityOption).hit);
            }
          },
        }}
      />
    </div>
  );
}
