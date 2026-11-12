import { MouseEventHandler, useCallback, useEffect, useState, useRef, Dispatch, SetStateAction } from 'react';
import ButtonWithToolTip from '../../../UI/ButtonWithToolTip';
import { buttonCaptions, tooltips } from '../../../../utils/Constants';
import { Flex, Typography, DropdownButton, Menu } from '@neo4j-ndl/react';
import { useCredentials } from '../../../../context/UserCredentials';
import { useFileContext } from '../../../../context/UsersFiles';
import { OptionType, TupleType } from '../../../../types';
import { showNormalToast } from '../../../../utils/Toasts';
import PatternContainer from './PatternContainer';
import SchemaViz from '../../../Graph/SchemaViz';
import GraphPattern from './GraphPattern';
import {
  updateLocalStorage,
  extractOptions,
  parseRelationshipString,
  deduplicateByFullPattern,
  deduplicateNodeByValue,
} from '../../../../utils/Utils';
import TooltipWrapper from '../../../UI/TipWrapper';
import EdcSettings from './EdcSettings';
import { GraphSchemaOptions, loadGraphSchemaOptions } from '../../../../services/SchemaOptions';
import { EXTRACTION_METHOD_EDC } from '../../../../utils/ExtractionMethodUtils';
import { useHasSelections } from '../../../../hooks/useHasSelections';

export default function NewEntityExtractionSetting({
  view,
  onClose,
  openTextSchema,
  openLoadSchema,
  openPredefinedSchema,
  settingView,
  onContinue,
  closeEnhanceGraphSchemaDialog,
  combinedPatterns,
  setCombinedPatterns,
  combinedNodes,
  setCombinedNodes,
  combinedRels,
  setCombinedRels,
  openDataImporterSchema,
}: {
  view: 'Dialog' | 'Tabs';
  open?: boolean;
  onClose?: () => void;
  openTextSchema: () => void;
  openLoadSchema: () => void;
  openPredefinedSchema: () => void;
  settingView: 'contentView' | 'headerView';
  onContinue?: () => void;
  closeEnhanceGraphSchemaDialog?: () => void;
  combinedPatterns: string[];
  setCombinedPatterns: Dispatch<SetStateAction<string[]>>;
  combinedNodes: OptionType[];
  setCombinedNodes: Dispatch<SetStateAction<OptionType[]>>;
  combinedRels: OptionType[];
  setCombinedRels: Dispatch<SetStateAction<OptionType[]>>;
  openDataImporterSchema: () => void;
}) {
  const {
    setSelectedRels,
    setSelectedNodes,
    userDefinedPattern,
    setUserDefinedPattern,
    setUserDefinedNodes,
    setUserDefinedRels,
    setAllPatterns,
    dbPattern,
    setDbPattern,
    setDbNodes,
    setDbRels,
    setSchemaValNodes,
    setSchemaValRels,
    schemaTextPattern,
    setSchemaTextPattern,
    setPreDefinedNodes,
    setPreDefinedRels,
    preDefinedPattern,
    setPreDefinedPattern,
    setImporterNodes,
    setImporterRels,
    setImporterPattern,
    importerPattern,
    extractionMethod,
    selectedNodes,
    selectedRels,
  } = useFileContext();
  const { userCredentials } = useCredentials();
  const [openGraphView, setOpenGraphView] = useState<boolean>(false);
  const [viewPoint, setViewPoint] = useState<string>('tableView');
  const [tupleOptions, setTupleOptions] = useState<TupleType[]>([]);
  const [selectedSource, setSource] = useState<OptionType | null>(null);
  const [selectedType, setType] = useState<OptionType | null>(null);
  const [selectedTarget, setTarget] = useState<OptionType | null>(null);
  const [highlightPattern, setHighlightedPattern] = useState<string | null>(null);

  const [isSchemaMenuOpen, setIsSchemaMenuOpen] = useState<boolean>(false);
  const schemaBtnRef = useRef<HTMLButtonElement>(null);
  // 抽出方式が EDC の場合、グラフスキーマは使用しないため表示せず、EDC の設定を表示する
  const isEdc = extractionMethod === EXTRACTION_METHOD_EDC;
  const hasGraphSchema = useHasSelections(selectedNodes, selectedRels);
  // グラフスキーマの選択肢として、現在のグラフに存在するラベルとリレーションタイプを取得する（グラフスキーマを表示した時点で 1 回）
  const [graphOptions, setGraphOptions] = useState<GraphSchemaOptions | null>(null);
  const [graphOptionsStatus, setGraphOptionsStatus] = useState<'idle' | 'loading' | 'loaded' | 'failed'>('idle');
  const aliveRef = useRef<boolean>(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (isEdc || graphOptionsStatus !== 'idle') {
      return;
    }
    setGraphOptionsStatus('loading');
    loadGraphSchemaOptions()
      .then((options) => {
        if (aliveRef.current) {
          setGraphOptions(options);
          setGraphOptionsStatus('loaded');
        }
      })
      .catch(() => {
        if (aliveRef.current) {
          setGraphOptionsStatus('failed');
        }
      });
  }, [isEdc, graphOptionsStatus]);

  useEffect(() => {
    if (userDefinedPattern.length > 0) {
      const lastPattern = userDefinedPattern[0];
      setHighlightedPattern(null);
      setTimeout(() => {
        setHighlightedPattern(lastPattern);
      }, 100);
    }
  }, [userDefinedPattern]);

  const handleFinalClear = () => {
    // overall
    setSelectedNodes([]);
    setSelectedRels([]);
    setAllPatterns([]);
    // User
    setUserDefinedPattern([]);
    setUserDefinedNodes([]);
    setUserDefinedRels([]);
    // DB
    setDbPattern([]);
    setDbNodes([]);
    setDbRels([]);
    // Text
    setSchemaTextPattern([]);
    setSchemaValNodes([]);
    setSchemaValRels([]);
    // Predefined
    setPreDefinedNodes([]);
    setPreDefinedRels([]);
    setPreDefinedPattern([]);
    setCombinedPatterns([]);
    setCombinedNodes([]);
    setCombinedRels([]);
    setTupleOptions([]);
    updateLocalStorage(userCredentials!, 'selectedNodeLabels', []);
    updateLocalStorage(userCredentials!, 'selectedRelationshipLabels', []);
    updateLocalStorage(userCredentials!, 'selectedPattern', []);
    showNormalToast(`グラフスキーマをクリアしました`);
    // Importer clear
    setImporterNodes([]);
    setImporterRels([]);
    setImporterPattern([]);
  };

  const handleFinalApply = (pattern: string[], nodeLables: OptionType[], relationshipLabels: OptionType[]) => {
    showNormalToast(`グラフスキーマを適用しました`);
    if (view === 'Tabs' && closeEnhanceGraphSchemaDialog != undefined) {
      closeEnhanceGraphSchemaDialog();
    }
    setAllPatterns(pattern);
    setSelectedNodes(nodeLables);
    setSelectedRels(relationshipLabels);
    updateLocalStorage(userCredentials!, 'selectedNodeLabels', nodeLables);
    updateLocalStorage(userCredentials!, 'selectedRelationshipLabels', relationshipLabels);
    updateLocalStorage(userCredentials!, 'selectedPattern', pattern);
  };

  const handleSchemaView = () => {
    setOpenGraphView(true);
    setViewPoint('showSchemaView');
  };

  const handlePatternChange = (
    source: OptionType[] | OptionType | null,
    type: OptionType[] | OptionType | null,
    target: OptionType[] | OptionType | null
  ) => {
    setSource(source as OptionType);
    setType(type as OptionType);
    setTarget(target as OptionType);
  };

  const handleAddPattern = (tupleOptionsValue: TupleType[]) => {
    if (selectedSource && selectedType && selectedTarget) {
      const patternValue = `${selectedSource.value} -[:${selectedType.value}]-> ${selectedTarget.value}`;
      const relValue = `${selectedSource.value},${selectedType.value},${selectedTarget.value}`;
      const relationshipOption: TupleType = {
        value: relValue,
        label: patternValue,
        source: selectedSource.value,
        target: selectedTarget.value,
        type: selectedType.value,
      };
      setUserDefinedPattern((prev) => (prev.includes(patternValue) ? prev : [patternValue, ...prev]));
      setCombinedPatterns((prev) => (prev.includes(patternValue) ? prev : [patternValue, ...prev]));
      const alreadyExists = tupleOptionsValue.some((tuple) => tuple.value === relValue);
      if (!alreadyExists) {
        const updatedTuples = [relationshipOption];
        const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(updatedTuples);
        setUserDefinedNodes((prev: OptionType[]) => {
          const combined = [...prev, ...nodeLabelOptions];
          return deduplicateNodeByValue(combined);
        });
        setUserDefinedRels((prev: OptionType[]) => {
          const combined = [...prev, ...relationshipTypeOptions];
          return deduplicateByFullPattern(combined);
        });
        setCombinedNodes((prev: OptionType[]) => {
          const combined = [...prev, ...nodeLabelOptions];
          return deduplicateNodeByValue(combined);
        });
        setCombinedRels((prev: OptionType[]) => {
          const combined = [...prev, ...relationshipTypeOptions];
          return deduplicateByFullPattern(combined);
        });
        setTupleOptions((prev) => [...updatedTuples, ...prev]);
      } else {
        if (tupleOptions.length === 0 && tupleOptionsValue.length > 0) {
          setTupleOptions(tupleOptionsValue);
        }
        showNormalToast('このパターンは既に存在します');
      }
      setSource(null);
      setType(null);
      setTarget(null);
    }
  };
  const updateStore = (
    patterns: string[],
    patternToRemove: string,
    setPatterns: React.Dispatch<React.SetStateAction<string[]>>,
    setNodes: React.Dispatch<React.SetStateAction<OptionType[]>>,
    setRels: React.Dispatch<React.SetStateAction<OptionType[]>>
  ) => {
    const updatedPatterns = patterns.filter((p) => p !== patternToRemove);
    if (updatedPatterns.length === 0) {
      setPatterns([]);
      setNodes([]);
      setRels([]);
      return;
    }
    const updatedTuples: TupleType[] = updatedPatterns
      .map((item) => {
        const parts = item.match(/(.*?) -\[:(.*?)\]-> (.*)/);
        if (!parts) {
          return null;
        }
        const [src, rel, tgt] = parts.slice(1).map((s) => s.trim());
        return {
          value: `${src},${rel},${tgt}`,
          label: `${src} -[:${rel}]-> ${tgt}`,
          source: src,
          target: tgt,
          type: rel,
        };
      })
      .filter(Boolean) as TupleType[];
    const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(updatedTuples);
    setPatterns(updatedPatterns);
    setNodes(nodeLabelOptions);
    setRels(relationshipTypeOptions);
  };

  const handleRemovePattern = (patternToRemove: string) => {
    if (userDefinedPattern.includes(patternToRemove)) {
      updateStore(userDefinedPattern, patternToRemove, setUserDefinedPattern, setUserDefinedNodes, setUserDefinedRels);
    }
    if (preDefinedPattern.includes(patternToRemove)) {
      updateStore(preDefinedPattern, patternToRemove, setPreDefinedPattern, setPreDefinedNodes, setPreDefinedRels);
    }
    if (dbPattern.includes(patternToRemove)) {
      updateStore(dbPattern, patternToRemove, setDbPattern, setDbNodes, setDbRels);
    }
    if (schemaTextPattern.includes(patternToRemove)) {
      updateStore(schemaTextPattern, patternToRemove, setSchemaTextPattern, setSchemaValNodes, setSchemaValRels);
    }
    if (importerPattern.includes(patternToRemove)) {
      updateStore(importerPattern, patternToRemove, setImporterPattern, setImporterNodes, setImporterRels);
    }
    const updatedCombinedPatterns = combinedPatterns.filter((p) => p !== patternToRemove);
    setCombinedPatterns(updatedCombinedPatterns);
    const updatedTuples: TupleType[] = updatedCombinedPatterns
      .map((item) => {
        const parts = item.match(/(.*?) -\[:(.*?)\]-> (.*)/);
        if (!parts) {
          return null;
        }
        const [src, rel, tgt] = parts.slice(1).map((s) => s.trim());
        return {
          value: `${src},${rel},${tgt}`,
          label: `${src} -[:${rel}]-> ${tgt}`,
          source: src,
          target: tgt,
          type: rel,
        };
      })
      .filter(Boolean) as TupleType[];
    const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(updatedTuples);
    setCombinedNodes(nodeLabelOptions);
    setCombinedRels(relationshipTypeOptions);
    setTupleOptions((prev) => prev.filter((t) => t.label !== patternToRemove));
  };

  const onSchemaFromTextCLick = () => {
    if (view === 'Dialog' && onClose != undefined) {
      onClose();
    }
    if (view === 'Tabs' && closeEnhanceGraphSchemaDialog != undefined) {
      closeEnhanceGraphSchemaDialog();
    }
    openTextSchema();
  };

  const onPredefinedSchemaCLick = () => {
    if (view === 'Dialog' && onClose != undefined) {
      onClose();
    }
    if (view === 'Tabs' && closeEnhanceGraphSchemaDialog != undefined) {
      closeEnhanceGraphSchemaDialog();
    }
    openPredefinedSchema();
  };

  const onLoadExistingSchemaCLick: MouseEventHandler<HTMLButtonElement> = useCallback(async () => {
    if (view === 'Dialog' && onClose != undefined) {
      onClose();
    }
    if (view === 'Tabs' && closeEnhanceGraphSchemaDialog != undefined) {
      closeEnhanceGraphSchemaDialog();
    }
    openLoadSchema();
  }, []);

  const onDataImporterSchemaCLick: MouseEventHandler<HTMLButtonElement> = useCallback(async () => {
    if (view === 'Dialog' && onClose != undefined) {
      onClose();
    }
    if (view === 'Tabs' && closeEnhanceGraphSchemaDialog != undefined) {
      closeEnhanceGraphSchemaDialog();
    }
    openDataImporterSchema();
  }, []);

  if (isEdc) {
    return <EdcSettings hasGraphSchema={hasGraphSchema} />;
  }

  // 適用済みのグラフスキーマの状態（selectedRels はパターン「始点,リレーションタイプ,終点」の一覧）
  const currentSchemaText =
    selectedRels.length > 0
      ? `パターン ${selectedRels.length} 件を設定済み`
      : hasGraphSchema
        ? `ラベル ${selectedNodes.length} 件を設定済み`
        : '未設定';

  return (
    <div>
      <section className='flex flex-col gap-2'>
        <Typography variant='subheading-medium'>グラフスキーマ（標準抽出で使用）</Typography>
        <div className='flex flex-col gap-1 n-bg-palette-primary-bg-weak rounded-lg p-3'>
          <Typography variant='body-medium'>
            LLM が生成するノードラベルとリレーションタイプを制限する。
            <strong>未設定の場合は制限しない</strong>
            {`（現在：${currentSchemaText}）。`}
          </Typography>
          <Typography variant='body-medium'>
            {`選択肢には現在のグラフに存在するラベルとリレーションタイプを${graphOptions?.hasCounts ? '件数付きで' : ''}表示する。`}
            管理用の名称と、日本語・空白などを含む名称（手動修正と同じ規則）は表示しない。一覧にない名称は入力により追加できる。別のスキーマは「スキーマの追加元」から読み込む。
          </Typography>
          <Typography variant='body-medium'>
            {`パターン（始点ノードのラベル・リレーションタイプ・終点ノードのラベルの組）は、3 つを選択して「+ 追加」で選択中のパターンに加え、「適用」で確定する。設定はこのブラウザに保存され、以後の取り込みに適用される。取り込み済みのグラフは変更しない。`}
          </Typography>
          {graphOptionsStatus === 'loading' && (
            <Typography variant='body-small'>現在のグラフのラベルとリレーションタイプを取得中…</Typography>
          )}
          {graphOptionsStatus === 'failed' && (
            <Typography variant='body-small' className='n-text-palette-danger-text'>
              現在のグラフのラベルとリレーションタイプを取得できない。名称は入力により追加できる。
            </Typography>
          )}
        </div>
      </section>
      <div className='mt-4'>
        <GraphPattern
          selectedSource={selectedSource}
          selectedType={selectedType}
          selectedTarget={selectedTarget}
          onPatternChange={handlePatternChange}
          onAddPattern={() =>
            handleAddPattern(
              tupleOptions.length > 0
                ? tupleOptions
                : combinedPatterns.map((pattern) => parseRelationshipString(pattern))
            )
          }
          selectedTupleOptions={tupleOptions}
          graphLabels={graphOptions?.labels}
          graphRelationshipTypes={graphOptions?.relationshipTypes}
        ></GraphPattern>
        <PatternContainer
          pattern={combinedPatterns}
          handleRemove={handleRemovePattern}
          handleSchemaView={handleSchemaView}
          highlightPattern={highlightPattern ?? ''}
          nodes={combinedNodes}
          rels={combinedRels}
          headingVariant='subheading-small'
        ></PatternContainer>
        <Flex className='my-8! mb-2 flex! items-center' flexDirection='row' justifyContent='flex-end'>
          <DropdownButton
            isOpen={isSchemaMenuOpen}
            htmlAttributes={{
              onClick: () => setIsSchemaMenuOpen((old) => !old),
            }}
            ref={schemaBtnRef}
          >
            スキーマの追加元 ...
          </DropdownButton>
          <Menu isOpen={isSchemaMenuOpen} anchorRef={schemaBtnRef} onClose={() => setIsSchemaMenuOpen(false)}>
            <Menu.Items
              htmlAttributes={{
                id: 'default-menu',
              }}
            >
              <Menu.Item
                title={
                  <TooltipWrapper hasButtonWrapper={true} placement='right' tooltip={tooltips.predinedSchema}>
                    定義済みスキーマ
                  </TooltipWrapper>
                }
                onClick={onPredefinedSchemaCLick}
              />
              <Menu.Item
                title={
                  <TooltipWrapper hasButtonWrapper={true} placement='right' tooltip={tooltips.useExistingSchema}>
                    既存スキーマを読み込み
                  </TooltipWrapper>
                }
                onClick={onLoadExistingSchemaCLick}
              />
              <Menu.Item
                title={
                  <TooltipWrapper hasButtonWrapper={true} placement='right' tooltip={tooltips.createSchema}>
                    テキストからスキーマを取得
                  </TooltipWrapper>
                }
                onClick={onSchemaFromTextCLick}
              />
              <Menu.Item
                title={
                  <TooltipWrapper hasButtonWrapper={true} placement='right' tooltip={tooltips.createSchema}>
                    Data ImporterのJSON
                  </TooltipWrapper>
                }
                onClick={onDataImporterSchemaCLick}
              />
            </Menu.Items>
          </Menu>
          <Flex flexDirection='row' gap='4'>
            {settingView === 'contentView' ? (
              <ButtonWithToolTip
                text={tooltips.continue}
                placement='top'
                onClick={onContinue}
                label='抽出に進む'
              >
                {buttonCaptions.continueSettings}
              </ButtonWithToolTip>
            ) : (
              <ButtonWithToolTip
                text={tooltips.clearGraphSettings}
                placement='top'
                onClick={handleFinalClear}
                label='グラフスキーマをクリア'
                disabled={!combinedPatterns.length}
              >
                {buttonCaptions.clearSettings}
              </ButtonWithToolTip>
            )}
            <ButtonWithToolTip
              text={tooltips.applySettings}
              placement='top'
              onClick={() => handleFinalApply(combinedPatterns, combinedNodes, combinedRels)}
              label='グラフスキーマを適用'
              disabled={!combinedPatterns.length}
            >
              {buttonCaptions.applyGraphSchema}
            </ButtonWithToolTip>
          </Flex>
        </Flex>
      </div>
      {openGraphView && (
        <SchemaViz
          open={openGraphView}
          setGraphViewOpen={setOpenGraphView}
          viewPoint={viewPoint}
          nodeValues={combinedNodes}
          relationshipValues={combinedRels}
        />
      )}
    </div>
  );
}
