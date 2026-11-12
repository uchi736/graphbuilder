import { Banner, Button, IconButton, LoadingSpinner, TextArea, TextInput, Typography } from '@neo4j-ndl/react';
import { TrashIconOutline } from '@neo4j-ndl/react/icons';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  describeGraphEditError,
  getNodeDetail,
  isGraphEditError,
  renameEntity,
  setEntityLabels,
  updateEntityProps,
} from '../../../services/GraphEdit';
import {
  GraphEditCandidate,
  GraphEditNodeDetail,
  GraphEditPropertyValue,
  GraphEditVocabulary,
  NeoNode,
  Scheme,
} from '../../../types';
import { showErrorToast, showNormalToast, showSuccessToast } from '../../../utils/Toasts';
import {
  ReservedNames,
  conflictCandidates,
  entityName,
  isSystemPropertyKey,
  validateLabels,
  visibleLabels,
} from '../../../utils/GraphEditUtils';
import LabelsSelect from './LabelsSelect';
import { LegendsChip } from '../LegendsChip';
import GraphPropertiesTable from '../GraphPropertiesTable';

// 名前・説明は専用の欄で扱い、embedding と aliases（別名の写し）はここで変えない
const SPECIAL_KEYS = new Set(['id', 'description', 'embedding', 'aliases']);

const isEditableStringProperty = (key: string, value: unknown) =>
  typeof value === 'string' && !SPECIAL_KEYS.has(key) && !isSystemPropertyKey(key);

const valueText = (value: unknown) => (Array.isArray(value) ? value.map(String).join('、') : String(value));

const sameLabels = (a: string[], b: string[]) => a.length === b.length && a.every((label) => b.includes(label));

type SavingState = '' | 'rename' | 'labels' | 'props';

// M1〜M3 1 つのエンティティの名前・種類・説明・文字列のプロパティを編集する。
// 開くときに /graph_edit/node_detail で全プロパティを取り直し、取り直すまで編集させない。
export default function EntityEditor({
  elementId,
  vocabulary,
  reserved,
  scheme,
  user,
  onNodeChanged,
  onProposeMerge,
  onRequestDelete,
}: {
  elementId: string;
  vocabulary: GraphEditVocabulary | null;
  reserved: ReservedNames;
  scheme: Scheme;
  user: string;
  onNodeChanged: (node: NeoNode) => void;
  onProposeMerge: (candidates: GraphEditCandidate[], keepName: string) => void;
  onRequestDelete: (candidate: GraphEditCandidate) => void;
}) {
  const [detail, setDetail] = useState<GraphEditNodeDetail | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string>('');
  const [name, setName] = useState<string>('');
  const [labels, setLabels] = useState<string[]>([]);
  const [description, setDescription] = useState<string>('');
  const [stringProps, setStringProps] = useState<Record<string, string>>({});
  const [removedKeys, setRemovedKeys] = useState<string[]>([]);
  const [newKey, setNewKey] = useState<string>('');
  const [newValue, setNewValue] = useState<string>('');
  const [newKeyError, setNewKeyError] = useState<string>('');
  const [saving, setSaving] = useState<SavingState>('');
  const requestRef = useRef<number>(0);

  const applyDetail = (loaded: GraphEditNodeDetail) => {
    const properties = loaded.node.properties ?? {};
    setDetail(loaded);
    setName(entityName(loaded.node));
    setLabels(visibleLabels(loaded.node.labels));
    setDescription(typeof properties.description === 'string' ? properties.description : '');
    setStringProps(
      Object.fromEntries(Object.entries(properties).filter(([key, value]) => isEditableStringProperty(key, value)))
    );
    setRemovedKeys([]);
    setNewKey('');
    setNewValue('');
    setNewKeyError('');
  };

  const loadDetail = useCallback(async () => {
    requestRef.current += 1;
    const requestId = requestRef.current;
    setLoading(true);
    setLoadError('');
    try {
      const loaded = await getNodeDetail(elementId);
      if (requestId === requestRef.current) {
        applyDetail(loaded);
      }
    } catch (error) {
      if (requestId === requestRef.current) {
        setDetail(null);
        setLoadError(describeGraphEditError(error));
      }
    } finally {
      if (requestId === requestRef.current) {
        setLoading(false);
      }
    }
  }, [elementId]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  if (loading && detail === null) {
    return (
      <div className='flex flex-row items-center gap-2 py-4'>
        <LoadingSpinner size='small' />
        <span className='n-body-small'>
          エンティティの詳細を読み込んでいます（読み込みが完了するまで編集できません）…
        </span>
      </div>
    );
  }
  if (detail === null) {
    return (
      <Banner
        type='danger'
        usage='inline'
        description={loadError || 'エンティティの詳細を読み込めませんでした。'}
        actions={[{ label: '再読込', as: 'button', onClick: () => loadDetail() }]}
      />
    );
  }

  const properties = detail.node.properties ?? {};
  const currentId = entityName(detail.node);
  const currentLabels = visibleLabels(detail.node.labels);
  const candidate: GraphEditCandidate = { element_id: detail.node.element_id, id: currentId, labels: currentLabels };
  const busy = saving !== '' || loading;
  const trimmedName = name.trim();
  const labelsError = validateLabels(labels, reserved, currentLabels);
  const labelsChanged = !sameLabels(labels, currentLabels);
  const originalDescription = typeof properties.description === 'string' ? properties.description : '';

  const buildPropsChange = () => {
    const change: Record<string, GraphEditPropertyValue> = {};
    if (description !== originalDescription) {
      change.description = description.trim() === '' ? null : description;
    }
    Object.entries(stringProps).forEach(([key, value]) => {
      if (properties[key] !== value) {
        change[key] = value;
      }
    });
    removedKeys.forEach((key) => {
      change[key] = null;
    });
    return change;
  };
  const propsChange = buildPropsChange();
  const propsChanged = Object.keys(propsChange).length > 0;

  const handleFailure = async (error: unknown) => {
    showErrorToast(describeGraphEditError(error));
    if (isGraphEditError(error, 'STALE') || isGraphEditError(error, 'NOT_FOUND')) {
      await loadDetail();
    }
  };

  const handleRename = async () => {
    setSaving('rename');
    try {
      const result = await renameEntity({
        element_id: detail.node.element_id,
        expected_id: currentId,
        new_id: trimmedName,
        user,
      });
      showSuccessToast(`名前を「${currentId}」から「${trimmedName}」に変更しました。旧名称は別名として保持されます。`);
      onNodeChanged(result.node);
      await loadDetail();
    } catch (error) {
      if (isGraphEditError(error, 'CONFLICT')) {
        // 理由が付くのは、旧い名前と正規化キーが同じ別のエンティティがある・新しい名前が他のエンティティの別名のとき
        const reason = error.data?.reason ? String(error.data.reason) : '';
        showNormalToast(
          reason
            ? `${reason}統合を提案します。`
            : `「${trimmedName}」と同じ名前（または正規化すると同じ名前）のエンティティがあります。統合を提案します。`
        );
        onProposeMerge([candidate, ...conflictCandidates(error.data)], trimmedName);
      } else {
        await handleFailure(error);
      }
    } finally {
      setSaving('');
    }
  };

  const handleSetLabels = async () => {
    setSaving('labels');
    try {
      const result = await setEntityLabels({
        element_id: detail.node.element_id,
        expected_id: currentId,
        labels,
        user,
      });
      showSuccessToast(
        `ラベルを ${labels.join('・')} に変更しました。次回以降の取り込みでもこのラベルに固定されます。`
      );
      onNodeChanged(result.node);
      await loadDetail();
    } catch (error) {
      await handleFailure(error);
    } finally {
      setSaving('');
    }
  };

  const handleSaveProps = async () => {
    setSaving('props');
    try {
      const result = await updateEntityProps({
        element_id: detail.node.element_id,
        expected_id: currentId,
        props: propsChange,
        user,
      });
      showSuccessToast('プロパティを保存しました。修正した項目は次の取り込みで上書きされません。');
      onNodeChanged(result.node);
      await loadDetail();
    } catch (error) {
      await handleFailure(error);
    } finally {
      setSaving('');
    }
  };

  const handleAddProperty = () => {
    const key = newKey.trim();
    if (key === '') {
      setNewKeyError('プロパティ名を入力してください。');
      return;
    }
    if (SPECIAL_KEYS.has(key) || isSystemPropertyKey(key)) {
      setNewKeyError('id・description・embedding・aliases と、先頭が _ の名前は使用できません。');
      return;
    }
    if (properties[key] !== undefined || stringProps[key] !== undefined) {
      setNewKeyError('同じ名前のプロパティが既にあります。');
      return;
    }
    setStringProps((previous) => ({ ...previous, [key]: newValue }));
    setRemovedKeys((previous) => previous.filter((removed) => removed !== key));
    setNewKey('');
    setNewValue('');
    setNewKeyError('');
  };

  const handleRemoveProperty = (key: string) => {
    setStringProps((previous) => Object.fromEntries(Object.entries(previous).filter(([name]) => name !== key)));
    if (properties[key] !== undefined) {
      setRemovedKeys((previous) => (previous.includes(key) ? previous : [...previous, key]));
    }
  };

  const readOnlyProps = Object.entries(properties).filter(
    ([key, value]) => !SPECIAL_KEYS.has(key) && !isSystemPropertyKey(key) && typeof value !== 'string' && value !== null
  );
  const systemProps = Object.entries(properties)
    .filter(([key]) => isSystemPropertyKey(key))
    .map(([key, value]) => ({ key, value: valueText(value) }));
  const aliases =
    detail.ledger?.aliases && detail.ledger.aliases.length > 0
      ? detail.ledger.aliases.map(String)
      : Array.isArray(properties.aliases)
        ? properties.aliases.map(String)
        : [];
  const neighborNames = new Map(
    (detail.neighbors ?? []).map((neighbor) => [neighbor.element_id, entityName(neighbor)])
  );

  return (
    <div className='flex flex-col gap-3' data-testid='graph-edit-entity-editor'>
      <div className='flex flex-row flex-wrap items-center gap-2'>
        <Typography variant='subheading-medium'>{currentId}</Typography>
        {currentLabels.map((label) => (
          <LegendsChip key={label} type='node' label={label} scheme={scheme} />
        ))}
        {loading && <LoadingSpinner size='small' />}
      </div>
      <div className='n-body-small n-text-palette-neutral-text-weak'>
        チャンク {detail.chunk_count} 件から参照・文書：{(detail.documents ?? []).join('、') || 'なし'}
      </div>

      {!detail.editable ? (
        <>
          <Banner type='warning' usage='inline' description={`このノードは編集できない。${detail.reason ?? ''}`} />
          <GraphPropertiesTable
            propertiesWithTypes={Object.entries(properties).map(([key, value]) => ({ key, value: valueText(value) }))}
          />
        </>
      ) : (
        <>
          <section className='flex flex-col gap-2'>
            <TextInput
              label='名前'
              value={name}
              isFluid={true}
              isDisabled={busy}
              errorText={trimmedName === '' ? '名前を入力してください。' : undefined}
              onChange={(event) => setName(event.target.value)}
              htmlAttributes={{ 'aria-label': 'エンティティの名前' }}
            />
            <div className='flex flex-row justify-end'>
              <Button
                size='small'
                onClick={handleRename}
                isLoading={saving === 'rename'}
                isDisabled={busy || trimmedName === '' || trimmedName === currentId}
              >
                名前を変更
              </Button>
            </div>
          </section>

          <section className='flex flex-col gap-2'>
            <LabelsSelect
              value={labels}
              onChange={setLabels}
              vocabulary={vocabulary}
              reserved={reserved}
              currentLabels={currentLabels}
              errorText={labelsChanged ? labelsError : ''}
              isDisabled={busy}
            />
            <div className='flex flex-row justify-end'>
              <Button
                size='small'
                onClick={handleSetLabels}
                isLoading={saving === 'labels'}
                isDisabled={busy || !labelsChanged || labelsError !== ''}
              >
                ラベルを変更
              </Button>
            </div>
          </section>

          <section className='flex flex-col gap-2'>
            <TextArea
              label='説明（description）'
              isFluid={true}
              value={description}
              isDisabled={busy}
              style={{ resize: 'vertical' }}
              htmlAttributes={{
                rows: 4,
                onChange: (event) => setDescription(event.target.value),
              }}
            />
            {Object.entries(stringProps).map(([key, value]) => (
              <div key={key} className='flex flex-row items-end gap-1'>
                <div className='flex-1 min-w-0'>
                  <TextInput
                    label={key}
                    value={value}
                    isFluid={true}
                    isDisabled={busy}
                    onChange={(event) => setStringProps((previous) => ({ ...previous, [key]: event.target.value }))}
                  />
                </div>
                <IconButton
                  ariaLabel={`プロパティ ${key} を削除`}
                  size='small'
                  isClean={true}
                  isDisabled={busy}
                  onClick={() => handleRemoveProperty(key)}
                >
                  <TrashIconOutline className='n-size-token-6' />
                </IconButton>
              </div>
            ))}
            {removedKeys.length > 0 && (
              <div className='n-body-small n-text-palette-warning-text'>
                保存時に削除するプロパティ：{removedKeys.join('、')}
              </div>
            )}
            <div className='flex flex-row items-end gap-1'>
              <div className='flex-1 min-w-0'>
                <TextInput
                  label='プロパティ名'
                  value={newKey}
                  isFluid={true}
                  size='small'
                  isDisabled={busy}
                  errorText={newKeyError || undefined}
                  onChange={(event) => {
                    setNewKey(event.target.value);
                    setNewKeyError('');
                  }}
                />
              </div>
              <div className='flex-1 min-w-0'>
                <TextInput
                  label='値'
                  value={newValue}
                  isFluid={true}
                  size='small'
                  isDisabled={busy}
                  onChange={(event) => setNewValue(event.target.value)}
                />
              </div>
              <Button size='small' fill='outlined' isDisabled={busy} onClick={handleAddProperty}>
                追加
              </Button>
            </div>
            <div className='flex flex-row justify-end'>
              <Button
                size='small'
                onClick={handleSaveProps}
                isLoading={saving === 'props'}
                isDisabled={busy || !propsChanged}
              >
                プロパティを保存
              </Button>
            </div>
          </section>

          {readOnlyProps.length > 0 && (
            <section className='flex flex-col gap-1'>
              <div className='n-body-small font-bold!'>文字列以外のプロパティ（この画面では変更不可）</div>
              {readOnlyProps.map(([key, value]) => (
                <div key={key} className='n-body-small'>
                  {key}：{valueText(value)}
                </div>
              ))}
            </section>
          )}
        </>
      )}

      <section className='flex flex-col gap-1 n-body-small'>
        <div className='font-bold'>修正台帳</div>
        <div>別名：{aliases.length > 0 ? aliases.join('、') : 'なし'}</div>
        <div>
          ラベルの固定：
          {detail.ledger?.type_override && detail.ledger.type_override.length > 0
            ? detail.ledger.type_override.join('・')
            : 'なし'}
        </div>
        <div>
          手動で修正した項目：
          {detail.ledger?.curated_keys && detail.ledger.curated_keys.length > 0
            ? detail.ledger.curated_keys.join('、')
            : 'なし'}
        </div>
      </section>

      {(detail.relationships ?? []).length > 0 && (
        <details>
          <summary className='cursor-pointer n-body-small'>リレーション（{detail.relationships.length} 本）</summary>
          <ul className='pl-4 n-body-small'>
            {detail.relationships.map((relationship) => {
              const outgoing = relationship.start_node_element_id === detail.node.element_id;
              const otherId = outgoing ? relationship.end_node_element_id : relationship.start_node_element_id;
              const otherName = neighborNames.get(otherId) || otherId;
              return (
                <li key={relationship.element_id}>
                  {outgoing ? `→ ${relationship.type} → ${otherName}` : `← ${relationship.type} ← ${otherName}`}
                </li>
              );
            })}
          </ul>
        </details>
      )}

      {systemProps.length > 0 && (
        <details>
          <summary className='cursor-pointer n-body-small'>システムプロパティ（{systemProps.length}）</summary>
          <ul className='pl-4 n-body-small'>
            {systemProps.map(({ key, value }) => (
              <li key={key}>
                {key}：{value}
              </li>
            ))}
          </ul>
        </details>
      )}

      {detail.editable && (
        <div className='flex flex-row justify-between items-center gap-2 pt-2'>
          <span className='n-body-small n-text-palette-neutral-text-weak'>
            統合する場合は、Ctrl または Shift を押しながら統合対象のエンティティをクリックする。
          </span>
          <Button
            size='small'
            fill='outlined'
            color='danger'
            isDisabled={busy}
            onClick={() => onRequestDelete(candidate)}
          >
            削除…
          </Button>
        </div>
      )}
    </div>
  );
}
