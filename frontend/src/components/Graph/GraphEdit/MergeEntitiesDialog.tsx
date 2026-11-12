import { Banner, Button, Dialog, LoadingSpinner, Radio, Select, TextArea } from '@neo4j-ndl/react';
import { useEffect, useMemo, useState } from 'react';
import { describeGraphEditError, getNodeDetail, isGraphEditError, mergeEntities } from '../../../services/GraphEdit';
import {
  GraphEditCandidate,
  GraphEditDescriptionMode,
  GraphEditMergeResult,
  GraphEditNodeDetail,
  GraphEditVocabulary,
  Scheme,
} from '../../../types';
import { showSuccessToast } from '../../../utils/Toasts';
import {
  NameOption,
  ReservedNames,
  conflictCandidates,
  entityName,
  validateLabels,
  visibleLabels,
} from '../../../utils/GraphEditUtils';
import LabelsSelect from './LabelsSelect';
import { LegendsChip } from '../LegendsChip';

const DESCRIPTION_PREVIEW_LENGTH = 160;

const descriptionOf = (detail?: GraphEditNodeDetail) => {
  const value = detail?.node.properties.description;
  return typeof value === 'string' ? value : '';
};

const shorten = (text: string, length: number) => (text.length > length ? `${text.slice(0, length)}…` : text);

const DESCRIPTION_MODES: { value: GraphEditDescriptionMode; label: string }[] = [
  { value: 'target', label: '統合先の説明を使用' },
  { value: 'concat', label: 'すべての説明を連結' },
  { value: 'custom', label: '説明を再作成' },
];

// M4 統合。選んだ 2 つ以上のエンティティを 1 つにまとめる。残す名前・種類・説明のまとめ方を選ぶ。
export default function MergeEntitiesDialog({
  open,
  initialCandidates,
  initialKeepName,
  vocabulary,
  reserved,
  scheme,
  user,
  onClose,
  onMerged,
}: {
  open: boolean;
  initialCandidates: GraphEditCandidate[];
  initialKeepName?: string;
  vocabulary: GraphEditVocabulary | null;
  reserved: ReservedNames;
  scheme: Scheme;
  user: string;
  onClose: () => void;
  onMerged: (result: GraphEditMergeResult, sourceElementIds: string[]) => void;
}) {
  const [candidates, setCandidates] = useState<GraphEditCandidate[]>(initialCandidates);
  const [details, setDetails] = useState<Record<string, GraphEditNodeDetail>>({});
  const [loadError, setLoadError] = useState<string>('');
  const [targetId, setTargetId] = useState<string>('');
  const [keepName, setKeepName] = useState<string>(initialKeepName ?? '');
  const [nameTouched, setNameTouched] = useState<boolean>(Boolean(initialKeepName));
  const [labels, setLabels] = useState<string[]>([]);
  const [labelsTouched, setLabelsTouched] = useState<boolean>(false);
  const [descriptionMode, setDescriptionMode] = useState<GraphEditDescriptionMode>('target');
  const [customDescription, setCustomDescription] = useState<string>('');
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [errorText, setErrorText] = useState<string>('');
  const [conflicts, setConflicts] = useState<GraphEditCandidate[]>([]);
  const [reloadToken, setReloadToken] = useState<number>(0);

  const missing = candidates.filter((candidate) => details[candidate.element_id] === undefined);
  const allLoaded = missing.length === 0;

  // 開くときに各エンティティの詳細を取り直す（表示の近傍はプロパティが id と title だけのため）
  useEffect(() => {
    if (missing.length === 0) {
      return;
    }
    let cancelled = false;
    setLoadError('');
    Promise.all(missing.map((candidate) => getNodeDetail(candidate.element_id)))
      .then((loaded) => {
        if (cancelled) {
          return;
        }
        setDetails((previous) => {
          const next = { ...previous };
          loaded.forEach((detail, index) => {
            next[missing[index].element_id] = detail;
          });
          return next;
        });
      })
      .catch((error) => {
        if (!cancelled) {
          setLoadError(describeGraphEditError(error));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [candidates, reloadToken]);

  const nameOf = (elementId: string) => {
    const candidate = candidates.find((item) => item.element_id === elementId);
    return entityName(details[elementId]?.node) || candidate?.id || elementId;
  };
  const labelsOf = (elementId: string) => visibleLabels(details[elementId]?.node.labels ?? []);
  const chunkCountOf = (elementId: string) => details[elementId]?.chunk_count ?? 0;
  // 規則で作ったノード（文書番号・型式）は統合先にだけでき、名前と種類はそのまま残す（後処理が同じ名前で作り直すため）
  const ruleTargets = candidates.filter((candidate) => details[candidate.element_id]?.merge_target_only);
  const ruleTargetId = ruleTargets.length === 1 ? ruleTargets[0].element_id : '';

  const selectTarget = (elementId: string) => {
    setTargetId(elementId);
    if (!nameTouched) {
      setKeepName(nameOf(elementId));
    }
    if (!labelsTouched) {
      setLabels(labelsOf(elementId));
    }
  };

  // 統合先の既定: 残す名前と同じ名前のもの、無ければ参照するチャンクが最も多いもの
  useEffect(() => {
    if (!allLoaded || candidates.length === 0) {
      return;
    }
    if (targetId !== '' && candidates.some((candidate) => candidate.element_id === targetId)) {
      return;
    }
    const byName = initialKeepName
      ? candidates.find((candidate) => nameOf(candidate.element_id) === initialKeepName)
      : undefined;
    const [best] = [...candidates].sort((a, b) => chunkCountOf(b.element_id) - chunkCountOf(a.element_id));
    selectTarget((byName ?? best).element_id);
  }, [allLoaded, candidates]);

  // 規則で作ったノードが 1 つだけなら、それを統合先に固定し、名前と種類をそのノードのものにする
  useEffect(() => {
    if (ruleTargetId === '') {
      return;
    }
    setTargetId(ruleTargetId);
    setKeepName(nameOf(ruleTargetId));
    setNameTouched(true);
    setLabels(labelsOf(ruleTargetId));
    setLabelsTouched(true);
  }, [ruleTargetId, details]);

  const concatenatedDescription = useMemo(
    () =>
      [
        ...new Set(candidates.map((candidate) => descriptionOf(details[candidate.element_id])).filter((text) => text)),
      ].join('\n'),
    [candidates, details]
  );

  const targetLabels = labelsOf(targetId);
  const otherLabels = useMemo(
    () => [...new Set(candidates.flatMap((candidate) => labelsOf(candidate.element_id)))],
    [candidates, details]
  );
  const nameOptions: NameOption[] = [...new Set(candidates.map((candidate) => nameOf(candidate.element_id)))].map(
    (name) => ({ value: name, label: name })
  );
  const trimmedName = keepName.trim();
  const labelsError = targetId === '' ? '' : validateLabels(labels, reserved, targetLabels);
  const blocked = candidates.filter(
    (candidate) =>
      details[candidate.element_id]?.editable === false && !details[candidate.element_id]?.merge_target_only
  );
  const aliasNames = nameOptions.map((option) => option.value).filter((name) => name !== trimmedName);
  const canSubmit =
    allLoaded &&
    candidates.length >= 2 &&
    targetId !== '' &&
    trimmedName !== '' &&
    labelsError === '' &&
    blocked.length === 0 &&
    ruleTargets.length <= 1 &&
    !(descriptionMode === 'custom' && customDescription.trim() === '');

  const removeCandidate = (elementId: string) => {
    setCandidates((previous) => previous.filter((candidate) => candidate.element_id !== elementId));
  };

  const handleMerge = async () => {
    if (!canSubmit) {
      return;
    }
    setSubmitting(true);
    setErrorText('');
    setConflicts([]);
    const sources = candidates
      .filter((candidate) => candidate.element_id !== targetId)
      .map((candidate) => ({ element_id: candidate.element_id, expected_id: nameOf(candidate.element_id) }));
    try {
      const result = await mergeEntities({
        target_element_id: targetId,
        target_expected_id: nameOf(targetId),
        sources,
        keep_id: trimmedName,
        labels,
        description_mode: descriptionMode,
        description: descriptionMode === 'custom' ? customDescription : '',
        user,
      });
      showSuccessToast(`${candidates.length} 件のエンティティを「${trimmedName}」に統合しました。`);
      onMerged(
        result,
        sources.map((source) => source.element_id)
      );
    } catch (error) {
      setErrorText(describeGraphEditError(error));
      if (isGraphEditError(error, 'CONFLICT')) {
        const known = new Set(candidates.map((candidate) => candidate.element_id));
        setConflicts(conflictCandidates(error.data).filter((candidate) => !known.has(candidate.element_id)));
      }
      if (isGraphEditError(error, 'STALE') || isGraphEditError(error, 'NOT_FOUND')) {
        setDetails({});
        setReloadToken((token) => token + 1);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog isOpen={open} onClose={onClose} size='large' htmlAttributes={{ 'aria-label': 'エンティティの統合' }}>
      <Dialog.Header>エンティティの統合</Dialog.Header>
      <Dialog.Content className='flex flex-col gap-4'>
        <div className='n-body-medium'>
          {candidates.length} 件のエンティティを 1
          つに統合する。リレーションは統合先に付け替え、使用されなくなる名前は別名として修正台帳に記録する。
        </div>
        {loadError && (
          <Banner
            type='danger'
            usage='inline'
            description={loadError}
            actions={[{ label: '再読込', as: 'button', onClick: () => setReloadToken((token) => token + 1) }]}
          />
        )}
        {!allLoaded && !loadError && (
          <div className='flex flex-row items-center gap-2'>
            <LoadingSpinner size='small' />
            <span className='n-body-small'>各エンティティの詳細を読み込んでいます…</span>
          </div>
        )}
        <section className='flex flex-col gap-2'>
          <div className='n-body-medium font-bold!'>統合先（保持するエンティティ）</div>
          {candidates.map((candidate) => {
            const detail = details[candidate.element_id];
            const description = descriptionOf(detail);
            return (
              <div
                key={candidate.element_id}
                className='flex flex-row gap-2 items-start border border-palette-neutral-border-weak rounded p-2'
              >
                <Radio
                  ariaLabel={`${nameOf(candidate.element_id)} を統合先にする`}
                  isChecked={targetId === candidate.element_id}
                  isDisabled={detail === undefined || (ruleTargetId !== '' && candidate.element_id !== ruleTargetId)}
                  onChange={() => selectTarget(candidate.element_id)}
                />
                <div className='flex flex-col flex-1 gap-1 min-w-0'>
                  <div className='flex flex-row flex-wrap items-center gap-2'>
                    <span className='n-body-medium font-bold!'>{nameOf(candidate.element_id)}</span>
                    {labelsOf(candidate.element_id).map((label) => (
                      <LegendsChip key={label} type='node' label={label} scheme={scheme} />
                    ))}
                  </div>
                  {detail && (
                    <div className='n-body-small n-text-palette-neutral-text-weak'>
                      チャンク {detail.chunk_count} 件・文書：{(detail.documents ?? []).join('、') || 'なし'}
                    </div>
                  )}
                  {description && (
                    <div className='n-body-small whitespace-pre-wrap' title={description}>
                      {shorten(description, DESCRIPTION_PREVIEW_LENGTH)}
                    </div>
                  )}
                  {detail?.editable === false && !detail.merge_target_only && (
                    <div className='n-body-small n-text-palette-danger-text'>
                      このノードは統合できません。{detail.reason ?? ''}
                    </div>
                  )}
                  {detail?.merge_target_only && (
                    <div className='n-body-small n-text-palette-neutral-text-weak'>
                      規則により生成したノードである。統合先となり、名前とラベルは変更されない。
                    </div>
                  )}
                </div>
                {candidates.length > 2 && candidate.element_id !== targetId && (
                  <Button size='small' fill='text' onClick={() => removeCandidate(candidate.element_id)}>
                    除外
                  </Button>
                )}
              </div>
            );
          })}
        </section>
        <Select<NameOption, false>
          type='creatable'
          label='統合後の名称'
          size='medium'
          isFluid={true}
          isDisabled={ruleTargetId !== ''}
          errorText={allLoaded && trimmedName === '' ? '統合後の名称を選択または入力してください。' : ''}
          helpText={aliasNames.length > 0 ? `別名として保持する名前：${aliasNames.join('、')}` : undefined}
          selectProps={{
            isDisabled: ruleTargetId !== '',
            options: nameOptions,
            value: trimmedName ? { value: trimmedName, label: trimmedName } : null,
            onChange: (option) => {
              setKeepName(option?.value ?? '');
              setNameTouched(true);
            },
            onCreateOption: (input) => {
              setKeepName(input.trim());
              setNameTouched(true);
            },
            formatCreateLabel: (input) => `「${input.trim()}」を名称として使用`,
            menuPosition: 'fixed',
            placeholder: '名称を選択または入力',
          }}
        />
        <LabelsSelect
          value={labels}
          onChange={(next) => {
            setLabels(next);
            setLabelsTouched(true);
          }}
          vocabulary={vocabulary}
          reserved={reserved}
          currentLabels={targetLabels}
          extraLabels={otherLabels}
          errorText={labelsError}
          isDisabled={!allLoaded || ruleTargetId !== ''}
        />
        <section className='flex flex-col gap-2'>
          <div className='n-body-medium font-bold!'>説明の統合方法</div>
          {DESCRIPTION_MODES.map((mode) => (
            <Radio
              key={mode.value}
              label={mode.label}
              isChecked={descriptionMode === mode.value}
              onChange={() => {
                setDescriptionMode(mode.value);
                if (mode.value === 'custom' && customDescription === '') {
                  setCustomDescription(concatenatedDescription);
                }
              }}
            />
          ))}
          {descriptionMode === 'custom' && (
            <TextArea
              label='統合後の説明'
              isFluid={true}
              value={customDescription}
              style={{ resize: 'vertical' }}
              htmlAttributes={{
                rows: 5,
                onChange: (event) => setCustomDescription(event.target.value),
              }}
            />
          )}
        </section>
        {ruleTargets.length > 1 && (
          <Banner
            type='danger'
            usage='inline'
            description='規則により生成したノード同士は統合できません。いずれかを除外してください。'
          />
        )}
        {errorText && <Banner type='danger' usage='inline' description={errorText} />}
        {conflicts.length > 0 && (
          <div className='flex flex-row flex-wrap items-center gap-2'>
            <span className='n-body-small'>
              同じ名前の候補：{conflicts.map((candidate) => candidate.id).join('、')}
            </span>
            <Button
              size='small'
              fill='outlined'
              onClick={() => {
                setCandidates((previous) => [...previous, ...conflicts]);
                setConflicts([]);
                setErrorText('');
              }}
            >
              これらも統合対象に追加
            </Button>
          </div>
        )}
      </Dialog.Content>
      <Dialog.Actions>
        <Button fill='outlined' onClick={onClose} isDisabled={submitting}>
          キャンセル
        </Button>
        <Button onClick={handleMerge} isLoading={submitting} isDisabled={!canSubmit}>
          統合する
        </Button>
      </Dialog.Actions>
    </Dialog>
  );
}
