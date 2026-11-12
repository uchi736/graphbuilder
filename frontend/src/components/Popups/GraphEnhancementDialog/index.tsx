import { Dialog, Tabs, Typography, Flex, useMediaQuery } from '@neo4j-ndl/react';
import graphenhancement from '../../../assets/images/graph-enhancements.svg';
import { Dispatch, SetStateAction, useState } from 'react';
import DeletePopUpForOrphanNodes from './DeleteTabForOrphanNodes';
import deleteOrphanAPI from '../../../services/DeleteOrphanNodes';
import NewEntityExtractionSetting from './EnitityExtraction/NewEntityExtractionSetting';
import { useFileContext } from '../../../context/UsersFiles';
import DeduplicationTab from './Deduplication';
import { tokens } from '@neo4j-ndl/base';
import PostProcessingCheckList from './PostProcessingCheckList';
import AdditionalInstructionsText from './AdditionalInstructions';
import IngestSummary from './IngestSummary';
import MaintenanceGuide from './MaintenanceGuide';
import SettingsOverview from './SettingsOverview';
import GraphEditLedgerTab from './GraphEditLedger';
import { RetrievalSettingsPanel } from '../../ChatBot/RetrievalSettingsDialog';
import { OptionType } from '../../../types';
import { describeGraphEditFailure } from '../../../services/GraphEdit';
import { showErrorToast, showSuccessToast } from '../../../utils/Toasts';

// タブの番号。TabPanel の id（tabpanel-<番号>）が重ならないよう、中のタブは 10 番台・30 番台にする
const TAB = { ingest: 0, refine: 1, retrieval: 2, maintenance: 3 };
const REFINE_TAB = { jobs: 10, orphans: 11, duplicates: 12 };
// 保守の中のタブ: 設定値と検証件数・更新・再処理の手順・修正台帳（グラフの手動修正の操作の履歴と台帳の行。C6）
const MAINTENANCE_TAB = { settings: 30, guide: 31, ledger: 32 };

export default function GraphEnhancementDialog({
  open,
  onClose,
  combinedPatterns,
  setCombinedPatterns,
  combinedNodes,
  setCombinedNodes,
  combinedRels,
  setCombinedRels,
}: {
  open: boolean;
  onClose: () => void;
  combinedPatterns: string[];
  setCombinedPatterns: Dispatch<SetStateAction<string[]>>;
  combinedNodes: OptionType[];
  setCombinedNodes: Dispatch<SetStateAction<OptionType[]>>;
  combinedRels: OptionType[];
  setCombinedRels: Dispatch<SetStateAction<OptionType[]>>;
}) {
  const { breakpoints } = tokens;
  const [orphanDeleteAPIloading, setorphanDeleteAPIloading] = useState<boolean>(false);
  const {
    setShowTextFromSchemaDialog,
    setSchemaLoadDialog,
    setPredefinedSchemaDialog,
    setUserDefinedPattern,
    setUserDefinedNodes,
    setUserDefinedRels,
    setDbPattern,
    setDbNodes,
    setDbRels,
    setSchemaValNodes,
    setSchemaValRels,
    setSchemaTextPattern,
    setPreDefinedNodes,
    setPreDefinedRels,
    setPreDefinedPattern,
    setSelectedPreDefOption,
    allPatterns,
    setDataImporterSchemaDialog,
    setImporterNodes,
    setImporterPattern,
    setImporterRels,
  } = useFileContext();
  const isTablet = useMediaQuery(`(min-width:${breakpoints.xs}) and (max-width: ${breakpoints.lg})`);

  // 削除できたら true（表から消す）。失敗（status が Failed、通信の失敗）はトーストで出し、false を返して表に残す
  const orphanNodesDeleteHandler = async (selectedEntities: string[]): Promise<boolean> => {
    try {
      setorphanDeleteAPIloading(true);
      const response = await deleteOrphanAPI(selectedEntities);
      setorphanDeleteAPIloading(false);
      if (response.data?.status === 'Failed') {
        showErrorToast(
          `孤立ノードを削除できませんでした。${describeGraphEditFailure(response.data.error ?? response.data.message, response.data.data)}`
        );
        return false;
      }
      const skipped: unknown[] = response.data?.data?.skipped ?? [];
      const skippedText = skipped.length ? `${skipped.length} 件は編集の対象でないため削除しませんでした。` : '';
      showSuccessToast(`孤立ノード ${selectedEntities.length - skipped.length} 件を削除しました。${skippedText}`);
      return true;
    } catch (error) {
      setorphanDeleteAPIloading(false);
      showErrorToast(
        `孤立ノードを削除できませんでした。${describeGraphEditFailure(error instanceof Error ? error.message : String(error))}`
      );
      return false;
    }
  };

  const handleOnclose = () => {
    if (allPatterns.length > 0) {
      onClose();
      return;
    }
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
    // combined Nodes and rels
    setCombinedNodes([]);
    setCombinedRels([]);
    setCombinedPatterns([]);
    // Data Importer
    setImporterNodes([]);
    setImporterPattern([]);
    setImporterRels([]);
    setSelectedPreDefOption(null);
    onClose();
  };

  const [activeTab, setactiveTab] = useState<number>(TAB.ingest);
  const [refineTab, setRefineTab] = useState<number>(REFINE_TAB.jobs);
  const [maintenanceTab, setMaintenanceTab] = useState<number>(MAINTENANCE_TAB.settings);
  return (
    <Dialog
      modalProps={{
        id: 'graph-enhancement-popup',
        // 高さの下限を画面の高さから上下の余白（各 32px）を除いた値にし、タブを切り替えても上端の位置を保つ
        className: 'n-p-token-4 n-rounded-lg min-h-[calc(100%-64px)]',
      }}
      isOpen={open}
      size='unset'
      hasDisabledCloseButton={false}
      onClose={handleOnclose}
    >
      <Dialog.Header className='flex justify-between self-stretch mb-0! '>
        <div className='n-bg-palette-neutral-bg-weak px-4 w-full'>
          <div className='flex! flex-row items-center mb-2'>
            <img
              src={graphenhancement}
              style={{
                width: isTablet ? 170 : 220,
                height: isTablet ? 170 : 220,
                marginRight: 10,
                objectFit: 'contain',
              }}
              loading='lazy'
              alt='graph-enhancement-options-logo'
            />
            <div className='flex flex-col flex-1'>
              <Typography variant={isTablet ? 'h5' : 'h2'}>グラフ設定</Typography>
              <Typography variant={isTablet ? 'subheading-small' : 'subheading-medium'} className='mb-2'>
                {isTablet
                  ? `取り込み、グラフ整備、検索設定、保守の各機能を集約する`
                  : `取り込みの抽出方式とグラフスキーマ、後処理によるグラフ整備、チャットの検索設定、保守の機能を集約する`}
              </Typography>
              <Flex className='pt-2'>
                <Tabs fill='underline' onChange={setactiveTab} size={isTablet ? 'small' : 'large'} value={activeTab}>
                  <Tabs.Tab tabId={TAB.ingest} htmlAttributes={{ 'aria-label': '取り込み' }}>
                    取り込み
                  </Tabs.Tab>
                  <Tabs.Tab tabId={TAB.refine} htmlAttributes={{ 'aria-label': 'グラフ整備' }}>
                    グラフ整備
                  </Tabs.Tab>
                  <Tabs.Tab tabId={TAB.retrieval} htmlAttributes={{ 'aria-label': '検索設定' }}>
                    検索設定
                  </Tabs.Tab>
                  <Tabs.Tab tabId={TAB.maintenance} htmlAttributes={{ 'aria-label': '保守' }}>
                    保守
                  </Tabs.Tab>
                </Tabs>
              </Flex>
            </div>
          </div>
        </div>
      </Dialog.Header>
      <Dialog.Content className='flex flex-col n-gap-token- grow w-[90%] mx-auto mt-0!'>
        <Tabs.TabPanel
          className='n-flex n-flex-col n-gap-token-6 n-px-token-6 n-pb-token-6'
          value={activeTab}
          tabId={TAB.ingest}
        >
          <IngestSummary />
          <NewEntityExtractionSetting
            view='Tabs'
            openTextSchema={() => {
              setShowTextFromSchemaDialog({ triggeredFrom: 'enhancementtab', show: true });
            }}
            openLoadSchema={() => setSchemaLoadDialog({ triggeredFrom: 'enhancementtab', show: true })}
            openPredefinedSchema={() => {
              setPredefinedSchemaDialog({ triggeredFrom: 'enhancementtab', show: true });
            }}
            closeEnhanceGraphSchemaDialog={onClose}
            settingView='headerView'
            combinedPatterns={combinedPatterns}
            setCombinedPatterns={setCombinedPatterns}
            combinedNodes={combinedNodes}
            setCombinedNodes={setCombinedNodes}
            combinedRels={combinedRels}
            setCombinedRels={setCombinedRels}
            openDataImporterSchema={() => {
              setDataImporterSchemaDialog({ triggeredFrom: 'enhancementtab', show: true });
            }}
          />
          <AdditionalInstructionsText closeEnhanceGraphSchemaDialog={onClose} />
        </Tabs.TabPanel>
        <Tabs.TabPanel
          className='n-flex n-flex-col n-gap-token-4 n-px-token-6 n-pb-token-6'
          value={activeTab}
          tabId={TAB.refine}
        >
          <Tabs
            fill='filled'
            size='small'
            onChange={setRefineTab}
            value={refineTab}
            htmlAttributes={{ id: 'gs-subtabs-refine' }}
          >
            <Tabs.Tab tabId={REFINE_TAB.jobs}>後処理ジョブ</Tabs.Tab>
            <Tabs.Tab tabId={REFINE_TAB.orphans}>孤立ノード</Tabs.Tab>
            <Tabs.Tab tabId={REFINE_TAB.duplicates}>重複ノード</Tabs.Tab>
          </Tabs>
          <Tabs.TabPanel value={refineTab} tabId={REFINE_TAB.jobs}>
            <PostProcessingCheckList />
          </Tabs.TabPanel>
          <Tabs.TabPanel value={refineTab} tabId={REFINE_TAB.orphans}>
            <DeletePopUpForOrphanNodes deleteHandler={orphanNodesDeleteHandler} loading={orphanDeleteAPIloading} />
          </Tabs.TabPanel>
          <Tabs.TabPanel value={refineTab} tabId={REFINE_TAB.duplicates}>
            <DeduplicationTab />
          </Tabs.TabPanel>
        </Tabs.TabPanel>
        <Tabs.TabPanel
          className='n-flex n-flex-col n-gap-token-4 n-px-token-6 n-pb-token-6'
          value={activeTab}
          tabId={TAB.retrieval}
        >
          <RetrievalSettingsPanel />
        </Tabs.TabPanel>
        <Tabs.TabPanel
          className='n-flex n-flex-col n-gap-token-4 n-px-token-6 n-pb-token-6'
          value={activeTab}
          tabId={TAB.maintenance}
        >
          <Tabs
            fill='filled'
            size='small'
            onChange={setMaintenanceTab}
            value={maintenanceTab}
            htmlAttributes={{ id: 'gs-subtabs-maintenance' }}
          >
            <Tabs.Tab tabId={MAINTENANCE_TAB.settings}>設定値と検証件数</Tabs.Tab>
            <Tabs.Tab tabId={MAINTENANCE_TAB.guide}>更新・再処理の手順</Tabs.Tab>
            <Tabs.Tab tabId={MAINTENANCE_TAB.ledger}>修正台帳</Tabs.Tab>
          </Tabs>
          <Tabs.TabPanel value={maintenanceTab} tabId={MAINTENANCE_TAB.settings}>
            <SettingsOverview />
          </Tabs.TabPanel>
          <Tabs.TabPanel value={maintenanceTab} tabId={MAINTENANCE_TAB.guide}>
            <MaintenanceGuide />
          </Tabs.TabPanel>
          <Tabs.TabPanel value={maintenanceTab} tabId={MAINTENANCE_TAB.ledger}>
            <GraphEditLedgerTab />
          </Tabs.TabPanel>
        </Tabs.TabPanel>
      </Dialog.Content>
    </Dialog>
  );
}
