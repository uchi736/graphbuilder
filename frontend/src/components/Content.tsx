import { useEffect, useState, useMemo, useRef, Suspense, useReducer, useCallback, useContext } from 'react';
import FileTable from './FileTable';
import {
  Button,
  Typography,
  Flex,
  StatusIndicator,
  useMediaQuery,
  Menu,
  SpotlightTarget,
  useSpotlightContext,
} from '@neo4j-ndl/react';
import { useCredentials } from '../context/UserCredentials';
import { useFileContext } from '../context/UsersFiles';
import { extractAPI } from '../utils/FileAPI';
import { BannerAlertProps, ContentProps, CustomFile, OptionType, chunkdata, FileTableHandle } from '../types';
import deleteAPI from '../services/DeleteFiles';
import { postProcessing } from '../services/PostProcessing';
import { triggerStatusUpdateAPI } from '../services/ServerSideStatusUpdateAPI';
import useServerSideEvent from '../hooks/useSse';
import {
  batchSize,
  buttonCaptions,
  chatModeLables,
  largeFileSize,
  llms,
  RETRY_OPIONS,
  tooltips,
  tokenchunkSize,
  chunkOverlap,
  chunksToCombine,
  SKIP_AUTH,
} from '../utils/Constants';
import ButtonWithToolTip from './UI/ButtonWithToolTip';
import DropdownComponent from './Dropdown';
import { ExtractionMethodStatus, edcOptionsShortLabel } from './ExtractionMethodSelect';
import GraphViewModal from './Graph/GraphViewModal';
import { lazy } from 'react';
import FallBackDialog from './UI/FallBackDialog';
import DeletePopUp from './Popups/DeletePopUp/DeletePopUp';
import GraphEnhancementDialog from './Popups/GraphEnhancementDialog';
import { tokens } from '@neo4j-ndl/base';
import axios from 'axios';
import DatabaseStatusIcon from './UI/DatabaseStatusIcon';
import RetryConfirmationDialog from './Popups/RetryConfirmation/Index';
import retry from '../services/Retry';
import { showErrorToast, showNormalToast, showSuccessToast } from '../utils/Toasts';
import { useMessageContext } from '../context/UserMessages';
import PostProcessingToast from './Popups/GraphEnhancementDialog/PostProcessingCheckList/PostProcessingToast';
import { getChunkText } from '../services/getChunkText';
import ChunkPopUp from './Popups/ChunkPopUp';
import { isExpired, isFileReadyToProcess, shouldShowTokenTracking } from '../utils/Utils';
import { checkTokenLimits } from '../utils/TokenWarning';
import { useHasSelections } from '../hooks/useHasSelections';
import { ChevronUpIconOutline, ChevronDownIconOutline } from '@neo4j-ndl/react/icons';
import { ThemeWrapperContext } from '../context/ThemeWrapper';
import { useAuth0 } from '@auth0/auth0-react';
import React from 'react';
import { clearEmbeddingConfig, clearChunkConfig, getEmbeddingModel } from '../utils/EmbeddingConfigUtils';
import {
  EXTRACTION_METHOD_EDC,
  EXTRACTION_METHOD_STANDARD,
  EXTRACTION_METHOD_STORAGE_KEY,
} from '../utils/ExtractionMethodUtils';
import { useDocumentUpdate } from '../hooks/useDocumentUpdate';
import { showReplacedToast } from '../utils/DocumentUpdate';

const ConfirmationDialog = lazy(() => import('./Popups/LargeFilePopUp/ConfirmationDialog'));

let afterFirstRender = false;
const Content: React.FC<ContentProps> = ({
  showEnhancementDialog,
  toggleEnhancementDialog,
  setOpenConnection,
  showDisconnectButton,
  connectionStatus,
  combinedPatterns,
  setCombinedPatterns,
  combinedNodes,
  setCombinedNodes,
  combinedRels,
  setCombinedRels,
}) => {
  const { breakpoints } = tokens;
  const isTablet = useMediaQuery(`(min-width:${breakpoints.xs}) and (max-width: ${breakpoints.lg})`);
  const [openGraphView, setOpenGraphView] = useState<boolean>(false);
  const [inspectedName, setInspectedName] = useState<string>('');
  const [documentName, setDocumentName] = useState<string>('');
  const [showConfirmationModal, setShowConfirmationModal] = useState<boolean>(false);
  const [showExpirationModal, setShowExpirationModal] = useState<boolean>(false);
  const [extractLoading, setIsExtractLoading] = useState<boolean>(false);
  const { setUserCredentials, userCredentials, setConnectionStatus, isGdsActive, isReadOnlyUser, isGCSActive } =
    useCredentials();
  const [retryFile, setRetryFile] = useState<string>('');
  const [retryLoading, setRetryLoading] = useState<boolean>(false);
  const [showRetryPopup, toggleRetryPopup] = useReducer((state) => !state, false);
  const [showChunkPopup, toggleChunkPopup] = useReducer((state) => !state, false);
  const [chunksLoading, toggleChunksLoading] = useReducer((state) => !state, false);
  const [currentPage, setCurrentPage] = useState<number>(0);
  const [totalPageCount, setTotalPageCount] = useState<number | null>(null);
  const [textChunks, setTextChunks] = useState<chunkdata[]>([]);
  const [isGraphBtnMenuOpen, setIsGraphBtnMenuOpen] = useState<boolean>(false);
  const graphbtnRef = useRef<HTMLDivElement>(null);
  const chunksTextAbortController = useRef<AbortController>();
  const { colorMode } = useContext(ThemeWrapperContext);
  const { isAuthenticated } = useAuth0();
  const { setIsOpen } = useSpotlightContext();
  const [alertStateForRetry, setAlertStateForRetry] = useState<BannerAlertProps>({
    showAlert: false,
    alertType: 'neutral',
    alertMessage: '',
  });
  const { setMessages } = useMessageContext();
  const {
    filesData,
    setFilesData,
    setModel,
    selectedNodes,
    selectedRels,
    selectedTokenChunkSize,
    selectedChunk_overlap,
    selectedChunks_to_combine,
    setSelectedNodes,
    setAllPatterns,
    setRowSelection,
    setSelectedRels,
    setSelectedTokenChunkSize,
    setSelectedChunk_overlap,
    setSelectedChunks_to_combine,
    postProcessingTasks,
    queue,
    processedCount,
    setProcessedCount,
    setchatModes,
    model,
    extractionMethod,
    edcOptions,
    setExtractionMethod,
    additionalInstructions,
    setAdditionalInstructions,
  } = useFileContext();
  const [viewPoint, setViewPoint] = useState<
    'tableView' | 'showGraphView' | 'chatInfoView' | 'neighborView' | 'showSchemaView'
  >('tableView');
  const [showDeletePopUp, setShowDeletePopUp] = useState<boolean>(false);
  const [deleteLoading, setIsDeleteLoading] = useState<boolean>(false);

  const hasSelections = useHasSelections(selectedNodes, selectedRels);
  const connectDisabled = !isAuthenticated && !SKIP_AUTH;

  const { updateStatusForLargeFiles } = useServerSideEvent(
    (inMinutes, time, fileName) => {
      showNormalToast(`${fileName} の処理には約 ${time} ${inMinutes ? '分' : '秒'} かかります`);
      localStorage.setItem('alertShown', JSON.stringify(true));
    },
    (fileName) => {
      showErrorToast(`${fileName} の処理に失敗しました`);
    }
  );
  const childRef = useRef<FileTableHandle>(null);

  const incrementPage = async () => {
    setCurrentPage((prev) => prev + 1);
    await getChunks(documentName, currentPage + 1);
  };
  const decrementPage = async () => {
    setCurrentPage((prev) => prev - 1);
    await getChunks(documentName, currentPage - 1);
  };

  useEffect(() => {
    if (afterFirstRender) {
      localStorage.setItem('processedCount', JSON.stringify({ db: userCredentials?.uri, count: processedCount }));
    }
    if (processedCount == batchSize && !isReadOnlyUser) {
      handleGenerateGraph([], true);
    }
    if (processedCount === 1 && queue.isEmpty()) {
      (async () => {
        showNormalToast(
          <PostProcessingToast
            isGdsActive={isGdsActive}
            postProcessingTasks={postProcessingTasks}
            isSchema={hasSelections}
          />
        );
        try {
          if (userCredentials && connectionStatus && shouldShowTokenTracking(userCredentials.email)) {
            const tokenCheck = await checkTokenLimits(userCredentials);
            if (tokenCheck.shouldWarn) {
              showNormalToast(tokenCheck.message);
            }
          }

          const payload = isGdsActive
            ? hasSelections
              ? postProcessingTasks.filter((task) => task !== 'graph_schema_consolidation')
              : postProcessingTasks
            : hasSelections
              ? postProcessingTasks.filter(
                  (task) => task !== 'graph_schema_consolidation' && task !== 'enable_communities'
                )
              : postProcessingTasks.filter((task) => task !== 'enable_communities');
          if (payload.length) {
            const response = await postProcessing(payload);
            if (response.data.status === 'Success') {
              const communityfiles = response.data?.data;
              if (Array.isArray(communityfiles) && communityfiles.length) {
                communityfiles?.forEach((c: any) => {
                  setFilesData((prev) => {
                    return prev.map((f) => {
                      if (f.name === c.filename) {
                        return {
                          ...f,
                          chunkNodeCount: c.chunkNodeCount ?? 0,
                          entityNodeCount: c.entityNodeCount ?? 0,
                          communityNodeCount: c.communityNodeCount ?? 0,
                          chunkRelCount: c.chunkRelCount ?? 0,
                          entityEntityRelCount: c.entityEntityRelCount ?? 0,
                          communityRelCount: c.communityRelCount ?? 0,
                          nodesCount: c.nodeCount,
                          relationshipsCount: c.relationshipCount,
                        };
                      }
                      return f;
                    });
                  });
                });
              }
              showSuccessToast('すべてのQ&A機能が利用可能になりました。');
            } else {
              throw new Error(JSON.stringify(response.data));
            }
          }
        } catch (error) {
          if (error instanceof Error) {
            try {
              const errorData = JSON.parse(error.message);
              if (errorData.error && errorData.message) {
                console.error('Error:', errorData.error);
                showErrorToast(errorData.message);
              } else {
                showErrorToast(error.message);
              }
            } catch {
              showErrorToast(error.message);
            }
          }
        }
      })();
    }
  }, [processedCount, userCredentials, queue, isReadOnlyUser, isGdsActive]);

  useEffect(() => {
    if (afterFirstRender) {
      localStorage.setItem('waitingQueue', JSON.stringify({ db: userCredentials?.uri, queue: queue.items }));
    }
    afterFirstRender = true;
  }, [queue.items.length, userCredentials]);
  const isFirstTimeUser = useMemo(() => {
    return localStorage.getItem('neo4j.connection') === null && localStorage.getItem('gb.tourSeen') === null;
  }, []);
  useEffect(() => {
    if (!isAuthenticated && !connectionStatus && isFirstTimeUser) {
      setIsOpen(true);
    }
  }, [connectionStatus, isAuthenticated, isFirstTimeUser]);
  const handleDropdownChange = (selectedOption: OptionType | null | void) => {
    if (selectedOption?.value) {
      setModel(selectedOption?.value);
    }
    setFilesData((prevfiles) => {
      return prevfiles.map((curfile) => {
        return {
          ...curfile,
          model:
            curfile.status === 'New' || curfile.status === 'Ready to Reprocess' || curfile.status === 'Failed'
              ? (selectedOption?.value ?? '')
              : curfile.model,
        };
      });
    });
  };
  const getChunks = async (name: string, pageNo: number) => {
    chunksTextAbortController.current = new AbortController();
    toggleChunksLoading();
    const response = await getChunkText(name, pageNo, chunksTextAbortController.current.signal);
    setTextChunks(response.data.data.pageitems);
    if (!totalPageCount) {
      setTotalPageCount(response.data.data.total_pages);
    }
    toggleChunksLoading();
  };

  const extractData = async (uid: string, isselectedRows = false, filesTobeProcess: CustomFile[]) => {
    if (!isselectedRows) {
      const fileItem = filesData.find((f) => f.id == uid);
      if (fileItem) {
        setIsExtractLoading(true);
        await extractHandler(fileItem, uid);
      }
    } else {
      const fileItem = filesTobeProcess.find((f) => f.id == uid);
      if (fileItem) {
        setIsExtractLoading(true);
        await extractHandler(fileItem, uid);
      }
    }
  };

  const extractHandler = async (fileItem: CustomFile, uid: string) => {
    queue.remove((item) => item.name === fileItem.name);
    // 続きからは前回と同じ抽出方式でしか動かない（backend の check_resume_method）。画面の選択ではなく、その文書の方式を送る（T16）
    const methodForFile =
      fileItem.retryOption === 'start_from_last_processed_position'
        ? fileItem.extraction_method ||
          (fileItem.model?.toLowerCase() === EXTRACTION_METHOD_EDC ? EXTRACTION_METHOD_EDC : EXTRACTION_METHOD_STANDARD)
        : extractionMethod;
    try {
      if (userCredentials && connectionStatus && shouldShowTokenTracking(userCredentials.email)) {
        const tokenCheck = await checkTokenLimits(userCredentials);
        if (tokenCheck.shouldWarn) {
          showNormalToast(tokenCheck.message);
        }
      }

      setFilesData((prevfiles) =>
        prevfiles.map((curfile) => {
          if (curfile.id === uid) {
            return {
              ...curfile,
              status: 'Processing',
              extraction_method: methodForFile,
            };
          }
          return curfile;
        })
      );
      setRowSelection((prev) => {
        const copiedobj = { ...prev };
        for (const key in copiedobj) {
          if (key == uid) {
            copiedobj[key] = false;
          }
        }
        return copiedobj;
      });
      if (fileItem.name != undefined && userCredentials != null) {
        const { name } = fileItem;
        triggerStatusUpdateAPI(name as string, userCredentials, updateStatusForLargeFiles);
      }

      const apiResponse = await extractAPI(
        model,
        fileItem.fileSource,
        fileItem.retryOption ?? '',
        fileItem.sourceUrl,
        localStorage.getItem('accesskey'),
        atob(localStorage.getItem('secretkey') ?? ''),
        fileItem.name ?? '',
        fileItem.gcsBucket ?? '',
        fileItem.gcsBucketFolder ?? '',
        selectedNodes.map((l) => l.value),
        selectedRels.map((t) => t.value),
        selectedTokenChunkSize,
        selectedChunk_overlap,
        selectedChunks_to_combine,
        fileItem.googleProjectId,
        fileItem.language,
        fileItem.accessToken,
        additionalInstructions,
        methodForFile,
        edcOptions.doctype,
        edcOptions.schemaMode
      );
      showReplacedToast(apiResponse?.data?.replaced, fileItem.name); // 取り込み直しで消した前の版の件数（C8）
      if (apiResponse?.status === 'Failed') {
        let errorobj = { error: apiResponse.error, message: apiResponse.message, fileName: apiResponse.file_name };
        throw new Error(JSON.stringify(errorobj));
      } else if (fileItem.size != undefined && fileItem.size < largeFileSize) {
        if (apiResponse.data.message) {
          const apiRes = apiResponse.data.message;
          showSuccessToast(apiRes);
        }
        setFilesData((prevfiles) => {
          return prevfiles.map((curfile) => {
            if (curfile.name == apiResponse?.data?.fileName) {
              const apiRes = apiResponse?.data;
              return {
                ...curfile,
                processingProgress: apiRes?.processingTime?.toFixed(2),
                processingTotalTime: apiRes?.processingTime?.toFixed(2),
                status: apiRes?.status,
                nodesCount: apiRes?.nodeCount,
                relationshipsCount: apiRes?.relationshipCount,
                model: apiRes?.model,
                extraction_method: apiRes?.extraction_method ?? curfile.extraction_method,
                token_usage: apiRes?.token_usage,
                skipped_chunks: apiRes?.skipped_chunks ?? curfile.skipped_chunks,
              };
            }
            return curfile;
          });
        });
      }
    } catch (err: any) {
      if (err instanceof Error) {
        try {
          const error = JSON.parse(err.message);
          if (Object.keys(error).includes('fileName')) {
            setProcessedCount((prev) => {
              if (prev == batchSize) {
                return batchSize - 1;
              }
              return prev + 1;
            });
            const { message, fileName } = error;
            queue.remove((item) => item.name === fileName);
            const errorMessage = error.message;
            showErrorToast(message);
            setFilesData((prevfiles) =>
              prevfiles.map((curfile) => {
                if (curfile.name == fileName) {
                  return { ...curfile, status: 'Failed', errorMessage };
                }
                return curfile;
              })
            );
          } else {
            console.error('Unexpected error format:', error);
          }
        } catch (parseError) {
          if (axios.isAxiosError(err)) {
            const axiosErrorMessage = err.response?.data?.message || err.message;
            console.error('Axios error occurred:', axiosErrorMessage);
          } else {
            console.error('An unexpected error occurred:', err.message);
          }
        }
      } else {
        console.error('An unknown error occurred:', err);
      }
    }
  };

  const triggerBatchProcessing = (
    batch: CustomFile[],
    selectedFiles: CustomFile[],
    isSelectedFiles: boolean,
    newCheck: boolean
  ) => {
    const data = [];
    showNormalToast(`${batch.length} 件のファイルを同時に処理しています。`);
    for (let i = 0; i < batch.length; i++) {
      if (newCheck) {
        if (batch[i]?.status === 'New' || batch[i].status === 'Ready to Reprocess') {
          data.push(extractData(batch[i].id, isSelectedFiles, selectedFiles as CustomFile[]));
        }
      } else {
        data.push(extractData(batch[i].id, isSelectedFiles, selectedFiles as CustomFile[]));
      }
    }
    return data;
  };

  const addFilesToQueue = async (remainingFiles: CustomFile[]) => {
    if (!remainingFiles.length && postProcessingTasks.length) {
      showNormalToast(
        <PostProcessingToast
          isGdsActive={isGdsActive}
          postProcessingTasks={postProcessingTasks}
          isSchema={hasSelections}
        />
      );
      try {
        if (userCredentials && connectionStatus && shouldShowTokenTracking(userCredentials.email)) {
          const tokenCheck = await checkTokenLimits(userCredentials);
          if (tokenCheck.shouldWarn) {
            showNormalToast(tokenCheck.message);
          }
        }

        const response = await postProcessing(postProcessingTasks);
        if (response.data.status !== 'Success') {
          throw new Error('後処理に失敗しました');
        }

        const communityfiles = response.data?.data;
        if (Array.isArray(communityfiles) && communityfiles.length) {
          const updateFileData = (f: any, c: any) => {
            if (f.name !== c.filename) {
              return f;
            }
            return {
              ...f,
              chunkNodeCount: c.chunkNodeCount ?? 0,
              entityNodeCount: c.entityNodeCount ?? 0,
              communityNodeCount: c.communityNodeCount ?? 0,
              chunkRelCount: c.chunkRelCount ?? 0,
              entityEntityRelCount: c.entityEntityRelCount ?? 0,
              communityRelCount: c.communityRelCount ?? 0,
              nodesCount: c.nodeCount,
              relationshipsCount: c.relationshipCount,
            };
          };

          communityfiles?.forEach((c: any) => {
            setFilesData((prev) => prev.map((f) => updateFileData(f, c)));
          });
        }
        showSuccessToast('すべてのQ&A機能が利用可能になりました。');
      } catch (error) {
        if (error instanceof Error) {
          showSuccessToast(error.message);
        }
      }
    }
    for (let index = 0; index < remainingFiles.length; index++) {
      const f = remainingFiles[index];
      setFilesData((prev) =>
        prev.map((pf) => {
          if (pf.id === f.id) {
            return {
              ...pf,
              status: 'Waiting',
            };
          }
          return pf;
        })
      );
      queue.enqueue(f);
    }
  };

  const scheduleBatchWiseProcess = (selectedRows: CustomFile[], isSelectedFiles: boolean) => {
    let data = [];
    if (queue.size() > batchSize) {
      const batch = queue.items.slice(0, batchSize);
      data = triggerBatchProcessing(batch, selectedRows as CustomFile[], isSelectedFiles, false);
    } else {
      let mergedfiles = [...selectedRows];
      let filesToProcess: CustomFile[] = [];
      if (mergedfiles.length > batchSize) {
        filesToProcess = mergedfiles.slice(0, batchSize);
        const remainingFiles = [...(mergedfiles as CustomFile[])].splice(batchSize);
        addFilesToQueue(remainingFiles);
      } else {
        filesToProcess = mergedfiles;
      }
      data = triggerBatchProcessing(filesToProcess, selectedRows as CustomFile[], isSelectedFiles, false);
    }
    return data;
  };

  /**
   * Processes files in batches, respecting a maximum batch size.
   *
   * This function prioritizes processing files from the queue if it's not empty.
   * If the queue is empty, it processes the provided `filesTobeProcessed`:
   *   - If the number of files exceeds the batch size, it processes a batch and queues the rest.
   *   - If the number of files is within the batch size, it processes them all.
   *   - If there are already files being processed, it adjusts the batch size to avoid exceeding the limit.
   *
   * @param filesTobeProcessed - The files to be processed.
   * @param queueFiles - Whether to prioritize processing files from the queue. Defaults to false.
   */
  const handleGenerateGraph = (filesTobeProcessed: CustomFile[], queueFiles: boolean = false) => {
    let data = [];
    const processingFilesCount = filesData.filter((f) => f.status === 'Processing').length;
    if (filesTobeProcessed.length && !queueFiles && processingFilesCount < batchSize) {
      if (!queue.isEmpty()) {
        data = scheduleBatchWiseProcess(filesTobeProcessed as CustomFile[], true);
      } else if (filesTobeProcessed.length > batchSize) {
        const filesToProcess = filesTobeProcessed?.slice(0, batchSize) as CustomFile[];
        data = triggerBatchProcessing(filesToProcess, filesTobeProcessed as CustomFile[], true, false);
        const remainingFiles = [...(filesTobeProcessed as CustomFile[])].splice(batchSize);
        addFilesToQueue(remainingFiles);
      } else {
        let filesTobeSchedule: CustomFile[] = filesTobeProcessed;
        if (filesTobeProcessed.length + processingFilesCount > batchSize) {
          filesTobeSchedule = filesTobeProcessed.slice(
            0,
            filesTobeProcessed.length + processingFilesCount - batchSize
          ) as CustomFile[];
          const idstoexclude = new Set(filesTobeSchedule.map((f) => f.id));
          const remainingFiles = [...(childRef.current?.getSelectedRows() as CustomFile[])].filter(
            (f) => !idstoexclude.has(f.id)
          );
          addFilesToQueue(remainingFiles);
        }
        data = triggerBatchProcessing(filesTobeSchedule, filesTobeProcessed, true, true);
      }
      Promise.allSettled(data).then((_) => {
        setIsExtractLoading(false);
      });
    } else if (queueFiles && !queue.isEmpty() && processingFilesCount < batchSize) {
      data = scheduleBatchWiseProcess(queue.items, true);
      Promise.allSettled(data).then((_) => {
        setIsExtractLoading(false);
      });
    } else {
      addFilesToQueue(filesTobeProcessed as CustomFile[]);
    }
  };

  // 文書の更新（C8）: 行の「更新」→ 同じ名前のファイルを選ぶ → アップロード → 抽出（handleGenerateGraph と同じ経路）
  const { openUpdateChooser, updateInput } = useDocumentUpdate(handleGenerateGraph);

  const processWaitingFilesOnRefresh = useCallback(() => {
    let data = [];
    const processingFilesCount = filesData.filter((f) => f.status === 'Processing').length;

    if (!queue.isEmpty() && processingFilesCount < batchSize) {
      if (queue.size() > batchSize) {
        const batch = queue.items.slice(0, batchSize);
        data = triggerBatchProcessing(batch, queue.items as CustomFile[], true, false);
      } else {
        data = triggerBatchProcessing(queue.items, queue.items as CustomFile[], true, false);
      }
      Promise.allSettled(data).then((_) => {
        setIsExtractLoading(false);
      });
    } else {
      const selectedNewFiles = childRef.current
        ?.getSelectedRows()
        .filter((f) => f.status === 'New' || f.status == 'Ready to Reprocess');
      addFilesToQueue(selectedNewFiles as CustomFile[]);
    }
  }, [filesData, queue]);

  const handleOpenGraphClick = () => {
    const bloomUrl = import.meta.env.VITE_BLOOM_URL;
    let finalUrl = bloomUrl;
    if (userCredentials?.database && userCredentials.uri && userCredentials.userName) {
      const uriCoded = userCredentials.uri.replace(/:\d+$/, '');
      const connectURL = `${uriCoded.split('//')[0]}//${userCredentials.userName}@${uriCoded.split('//')[1]}:${
        userCredentials.port ?? '7687'
      }`;
      const encodedURL = encodeURIComponent(connectURL);
      finalUrl = bloomUrl?.replace('{CONNECT_URL}', encodedURL);
    }
    window.open(finalUrl, '_blank');
  };

  const [previewAllDocuments, setPreviewAllDocuments] = useState<boolean>(false);
  // 全文書表示の対象: 処理が完了した文書すべて（一覧の選択に関係なく）
  const completedFiles = useMemo(() => filesData.filter((f) => f.status === 'Completed'), [filesData]);
  const handleGraphView = () => {
    setPreviewAllDocuments(false);
    setOpenGraphView(true);
    setViewPoint('showGraphView');
  };
  const handleGraphViewAll = () => {
    setPreviewAllDocuments(true);
    setOpenGraphView(true);
    setViewPoint('showGraphView');
  };

  const disconnect = () => {
    queue.clear();
    const date = new Date();
    setProcessedCount(0);
    setConnectionStatus(false);
    localStorage.removeItem('password');
    localStorage.removeItem('selectedModel');
    localStorage.removeItem(EXTRACTION_METHOD_STORAGE_KEY);
    setExtractionMethod(EXTRACTION_METHOD_STANDARD);
    setUserCredentials({ uri: '', password: '', userName: '', database: '', email: '' });
    setSelectedNodes([]);
    setSelectedRels([]);
    setAllPatterns([]);
    localStorage.removeItem('selectedTokenChunkSize');
    setSelectedTokenChunkSize(tokenchunkSize);
    clearChunkConfig();
    setSelectedChunk_overlap(chunkOverlap);
    setSelectedChunks_to_combine(chunksToCombine);
    localStorage.removeItem('selectedNodeLabels');
    localStorage.removeItem('selectedRelationshipLabels');
    localStorage.removeItem('selectedPattern');
    clearEmbeddingConfig();
    localStorage.removeItem('allowEmbeddingChange');
    setAdditionalInstructions('');
    setMessages([
      {
        datetime: `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`,
        id: 2,
        modes: {
          'graph+vector+fulltext': {
            message:
              ' Neo4jナレッジグラフチャットへようこそ。処理が完了したドキュメントに関する質問ができます。',
          },
        },
        user: 'chatbot',
        currentMode: 'graph+vector+fulltext',
      },
    ]);
    setchatModes([chatModeLables['graph+vector+fulltext']]);
  };

  const retryHandler = async (filename: string, retryoption: string) => {
    try {
      setRetryLoading(true);
      const response = await retry(filename, retryoption);
      setRetryLoading(false);
      if (response.data.status === 'Failed') {
        throw new Error(response.data.error);
      } else if (
        response.data.status === 'Success' &&
        response.data?.message != undefined &&
        (response.data?.message as string).includes('Chunks are not created')
      ) {
        showNormalToast(response.data.message as string);
        retryOnclose();
      } else {
        const isStartFromBeginning = retryoption === RETRY_OPIONS[0] || retryoption === RETRY_OPIONS[1];
        setFilesData((prev) => {
          return prev.map((f) => {
            return f.name === filename
              ? {
                  ...f,
                  status: 'Ready to Reprocess',
                  processingProgress: isStartFromBeginning ? 0 : f.processingProgress,
                  nodesCount: isStartFromBeginning ? 0 : f.nodesCount,
                  relationshipsCount: isStartFromBeginning ? 0 : f.relationshipsCount,
                  token_usage: isStartFromBeginning ? 0 : f.token_usage,
                  embedding_model: getEmbeddingModel(),
                }
              : f;
          });
        });
        showSuccessToast(response.data.message as string);
        retryOnclose();
      }
    } catch (error) {
      setRetryLoading(false);
      if (error instanceof Error) {
        setAlertStateForRetry({
          showAlert: true,
          alertMessage: error.message,
          alertType: 'danger',
        });
      }
    }
  };

  const selectedfileslength = useMemo(
    () => childRef.current?.getSelectedRows().length,
    [childRef.current?.getSelectedRows()]
  );

  const newFilecheck = useMemo(
    () =>
      childRef.current?.getSelectedRows().filter((f) => f.status === 'New' || f.status == 'Ready to Reprocess').length,
    [childRef.current?.getSelectedRows()]
  );

  const completedfileNo = useMemo(
    () => childRef.current?.getSelectedRows().filter((f) => f.status === 'Completed').length,
    [childRef.current?.getSelectedRows()]
  );

  const dropdowncheck = useMemo(
    () => !filesData.some((f) => f.status === 'New' || f.status === 'Waiting' || f.status === 'Ready to Reprocess'),
    [filesData]
  );

  const disableCheck = useMemo(
    () => (!selectedfileslength ? dropdowncheck : !newFilecheck),
    [selectedfileslength, filesData, newFilecheck]
  );

  const showGraphCheck = useMemo(
    () => (selectedfileslength ? completedfileNo === 0 : true),
    [selectedfileslength, completedfileNo]
  );

  const filesForProcessing = useMemo(() => {
    let newstatusfiles: CustomFile[] = [];
    const selectedRows = childRef.current?.getSelectedRows();
    if (selectedRows?.length) {
      for (let index = 0; index < selectedRows.length; index++) {
        const parsedFile: CustomFile = selectedRows[index];
        if (parsedFile.status === 'New' || parsedFile.status == 'Ready to Reprocess') {
          newstatusfiles.push(parsedFile);
        }
      }
    } else if (filesData.length) {
      newstatusfiles = filesData.filter((f) => f.status === 'New' || f.status === 'Ready to Reprocess');
    }
    return newstatusfiles;
  }, [filesData, childRef.current?.getSelectedRows()]);

  const handleDeleteFiles = async (deleteEntities: boolean) => {
    try {
      setIsDeleteLoading(true);
      const response = await deleteAPI(childRef.current?.getSelectedRows() as CustomFile[], deleteEntities);
      queue.clear();
      setProcessedCount(0);
      setRowSelection({});
      setIsDeleteLoading(false);
      if (response.data.status == 'Success') {
        showSuccessToast(response.data.message);
        const filenames = childRef.current?.getSelectedRows().map((str) => str.name);
        if (filenames?.length) {
          for (let index = 0; index < filenames.length; index++) {
            const name = filenames[index];
            setFilesData((prev) => prev.filter((f) => f.name != name));
          }
        }
      } else {
        let errorobj = { error: response.data.error, message: response.data.message };
        throw new Error(JSON.stringify(errorobj));
      }
      setShowDeletePopUp(false);
    } catch (err) {
      setIsDeleteLoading(false);
      if (err instanceof Error) {
        const error = JSON.parse(err.message);
        const { message } = error;
        showErrorToast(message);
        console.log(err);
      }
    }
    setShowDeletePopUp(false);
  };

  const onClickHandler = () => {
    const selectedRows = childRef.current?.getSelectedRows();
    if (selectedRows?.length) {
      const expiredFilesExists = selectedRows.some(
        (c) => isFileReadyToProcess(c, true) && isExpired((c?.createdAt as Date) ?? new Date())
      );
      const largeFileExists = selectedRows.some(
        (c) => isFileReadyToProcess(c, true) && typeof c.size === 'number' && c.size > largeFileSize
      );
      if (expiredFilesExists) {
        setShowExpirationModal(true);
      } else if (largeFileExists && isGCSActive) {
        setShowConfirmationModal(true);
      } else {
        handleGenerateGraph(selectedRows.filter((f) => isFileReadyToProcess(f, false)));
      }
    } else if (filesData.length) {
      const expiredFileExists = filesData.some((c) => isFileReadyToProcess(c, true) && isExpired(c?.createdAt as Date));
      const largeFileExists = filesData.some(
        (c) => isFileReadyToProcess(c, true) && typeof c.size === 'number' && c.size > largeFileSize
      );
      const selectAllNewFiles = filesData.filter((f) => isFileReadyToProcess(f, false));
      const stringified = selectAllNewFiles.reduce((accu, f) => {
        const key = f.id;
        // @ts-ignore
        accu[key] = true;
        return accu;
      }, {});
      setRowSelection(stringified);
      if (largeFileExists) {
        setShowConfirmationModal(true);
      } else if (expiredFileExists && isGCSActive) {
        setShowExpirationModal(true);
      } else {
        handleGenerateGraph(filesData.filter((f) => isFileReadyToProcess(f, false)));
      }
    }
  };

  const retryOnclose = useCallback(() => {
    setRetryFile('');
    setAlertStateForRetry({
      showAlert: false,
      alertMessage: '',
      alertType: 'neutral',
    });
    setRetryLoading(false);
    toggleRetryPopup();
  }, []);

  const onBannerClose = useCallback(() => {
    setAlertStateForRetry({
      showAlert: false,
      alertMessage: '',
      alertType: 'neutral',
    });
  }, []);

  const handleSchemaView = () => {
    setOpenGraphView(true);
    setViewPoint('showSchemaView');
  };

  return (
    <>
      <RetryConfirmationDialog
        retryLoading={retryLoading}
        retryHandler={retryHandler}
        fileId={retryFile}
        onClose={retryOnclose}
        open={showRetryPopup}
        onBannerClose={onBannerClose}
        alertStatus={alertStateForRetry}
      />
      {showConfirmationModal && filesForProcessing.length && (
        <Suspense fallback={<FallBackDialog />}>
          <ConfirmationDialog
            open={showConfirmationModal}
            largeFiles={filesForProcessing}
            extractHandler={handleGenerateGraph}
            onClose={() => setShowConfirmationModal(false)}
            loading={extractLoading}
            selectedRows={childRef.current?.getSelectedRows() as CustomFile[]}
            isLargeDocumentAlert={true}
          ></ConfirmationDialog>
        </Suspense>
      )}
      {showExpirationModal && filesForProcessing.length && (
        <Suspense fallback={<FallBackDialog />}>
          <ConfirmationDialog
            open={showExpirationModal}
            largeFiles={filesForProcessing}
            extractHandler={handleGenerateGraph}
            onClose={() => setShowExpirationModal(false)}
            loading={extractLoading}
            selectedRows={childRef.current?.getSelectedRows() as CustomFile[]}
            isLargeDocumentAlert={false}
          ></ConfirmationDialog>
        </Suspense>
      )}
      {showDeletePopUp && (
        <DeletePopUp
          open={showDeletePopUp}
          no_of_files={selectedfileslength ?? 0}
          deleteHandler={(delentities: boolean) => handleDeleteFiles(delentities)}
          deleteCloseHandler={() => setShowDeletePopUp(false)}
          loading={deleteLoading}
          view='contentView'
        ></DeletePopUp>
      )}
      {showChunkPopup && (
        <ChunkPopUp
          chunksLoading={chunksLoading}
          onClose={() => {
            chunksTextAbortController.current?.abort();
            toggleChunkPopup();
          }}
          showChunkPopup={showChunkPopup}
          chunks={textChunks}
          incrementPage={incrementPage}
          decrementPage={decrementPage}
          currentPage={currentPage}
          totalPageCount={totalPageCount}
        ></ChunkPopUp>
      )}
      {showEnhancementDialog && (
        <GraphEnhancementDialog
          open={showEnhancementDialog}
          onClose={toggleEnhancementDialog}
          combinedPatterns={combinedPatterns}
          setCombinedPatterns={setCombinedPatterns}
          combinedNodes={combinedNodes}
          setCombinedNodes={setCombinedNodes}
          combinedRels={combinedRels}
          setCombinedRels={setCombinedRels}
        ></GraphEnhancementDialog>
      )}
      <GraphViewModal
        inspectedName={inspectedName}
        open={openGraphView}
        setGraphViewOpen={setOpenGraphView}
        viewPoint={viewPoint}
        selectedRows={previewAllDocuments ? completedFiles : childRef.current?.getSelectedRows()}
        allDocuments={previewAllDocuments}
      />
      <div className={`n-bg-palette-neutral-bg-default main-content-wrapper`}>
        <Flex
          className='w-full absolute top-0'
          alignItems='center'
          justifyContent='space-between'
          flexDirection='row'
          flexWrap='wrap'
        >
          <div className='connectionstatus__container'>
            <span className='h6 px-1'>Neo4j接続 {isReadOnlyUser ? '（読み取り専用モード）' : ''}</span>
            <Typography variant='body-medium'>
              <DatabaseStatusIcon
                isConnected={connectionStatus}
                isGdsActive={isGdsActive}
                uri={userCredentials?.uri}
                database={userCredentials?.database}
              />
              <div className='pt-1 flex! gap-1 items-center'>
                <div>
                  {extractionMethod === EXTRACTION_METHOD_EDC ? (
                    <StatusIndicator type='info' />
                  ) : !hasSelections ? (
                    <StatusIndicator type='warning' />
                  ) : (
                    <StatusIndicator type='success' />
                  )}
                </div>
                <div>
                  {extractionMethod === EXTRACTION_METHOD_EDC ? (
                    // EDC はグラフスキーマを使わず、EDC の語彙（文書種別ごとの初期スキーマ）で抽出する。下部の抽出方式の表示と同じ短い表記を使う
                    <span className='n-body-small'>
                      EDC の語彙で抽出する（文書種別：{edcOptionsShortLabel(edcOptions)}）
                    </span>
                  ) : hasSelections ? (
                    <span className='n-body-small'>
                      {hasSelections} グラフスキーマ設定済み
                      {hasSelections
                        ? `（ラベル ${selectedNodes.length} 種類・パターン ${selectedRels.length} 件）`
                        : ''}
                    </span>
                  ) : (
                    <span className='n-body-small'>グラフスキーマ未設定（抽出を制限しない）</span>
                  )}
                </div>
              </div>
            </Typography>
          </div>
          <div className='enhancement-btn__wrapper'>
            <ButtonWithToolTip
              placement='top'
              text={
                !isAuthenticated && !SKIP_AUTH
                  ? '先にログインしてください'
                  : !connectionStatus
                    ? 'Neo4jに接続してください'
                    : '取り込み・グラフ整備・検索設定・保守の設定を開く'
              }
              label='グラフ設定'
              className='mr-2!'
              onClick={toggleEnhancementDialog}
              disabled={!connectionStatus || isReadOnlyUser}
              size={isTablet ? 'small' : 'medium'}
              alwaysShowTooltip={true}
            >
              グラフ設定
            </ButtonWithToolTip>
            {!connectionStatus ? (
              <SpotlightTarget id='connectbutton' hasPulse={!connectDisabled} indicatorVariant='border'>
                <ButtonWithToolTip
                  text={connectDisabled ? '接続するには先にログインしてください' : buttonCaptions.connectToNeo4j}
                  label={buttonCaptions.connectToNeo4j}
                  disabled={connectDisabled}
                  size={isTablet ? 'small' : 'medium'}
                  className='mr-2!'
                  onClick={() => setOpenConnection((prev) => ({ ...prev, openPopUp: true }))}
                  alwaysShowTooltip={true}
                >
                  {buttonCaptions.connectToNeo4j}
                </ButtonWithToolTip>
              </SpotlightTarget>
            ) : (
              showDisconnectButton && (
                <Button size={isTablet ? 'small' : 'medium'} className='mr-2.5' onClick={disconnect}>
                  {buttonCaptions.disconnect}
                </Button>
              )
            )}
          </div>
        </Flex>

        {updateInput}
        <FileTable
          connectionStatus={connectionStatus}
          setConnectionStatus={setConnectionStatus}
          onInspect={useCallback((name) => {
            setInspectedName(name);
            setOpenGraphView(true);
            setViewPoint('tableView');
          }, [])}
          onRetry={useCallback((id) => {
            setRetryFile(id);
            toggleRetryPopup();
          }, [])}
          onUpdate={openUpdateChooser}
          onChunkView={useCallback(
            async (name) => {
              setDocumentName(name);
              if (name != documentName) {
                toggleChunkPopup();
                if (totalPageCount) {
                  setTotalPageCount(null);
                }
                setCurrentPage(1);
                await getChunks(name, 1);
              }
            },
            [documentName, totalPageCount]
          )}
          ref={childRef}
          handleGenerateGraph={processWaitingFilesOnRefresh}
        ></FileTable>

        <Flex className={`p-2.5  mt-1.5 absolute bottom-0 w-full`} justifyContent='space-between' flexDirection={'row'}>
          <Flex flexDirection='row' gap='4' alignItems='flex-start'>
            {/* LLM の選択の見出しは部品の幅（150px）より長いため、隣の抽出方式の表示と重ならない幅を確保する */}
            <div className='min-w-[200px]'>
              <DropdownComponent
                onSelect={handleDropdownChange}
                options={llms ?? ['']}
                placeholder='LLMモデルを選択'
                defaultValue={model}
                view='ContentView'
                isDisabled={false}
              />
            </div>
            {/* 抽出方式は表示のみとし、押下でグラフ設定（「取り込み」タブ）を開く。変更はグラフ設定で行う */}
            <div className='self-end'>
              <ExtractionMethodStatus
                onOpenSettings={toggleEnhancementDialog}
                disabledReason={
                  !connectionStatus
                    ? 'Neo4jに接続してください'
                    : isReadOnlyUser
                      ? '読み取り専用モードでは変更できない'
                      : undefined
                }
                size={isTablet ? 'small' : 'medium'}
              />
            </div>
          </Flex>
          <Flex flexDirection='row' gap='4' className='self-end mb-2.5' flexWrap='wrap'>
            <SpotlightTarget id='generategraphbtn'>
              <ButtonWithToolTip
                text={!isAuthenticated && !SKIP_AUTH ? '先にログインしてください' : tooltips.generateGraph}
                placement='top'
                label='グラフを生成'
                onClick={onClickHandler}
                disabled={disableCheck || isReadOnlyUser}
                className='mr-0.5'
                size={isTablet ? 'small' : 'medium'}
                alwaysShowTooltip={true}
              >
                {buttonCaptions.generateGraph}{' '}
                {selectedfileslength && !disableCheck && newFilecheck ? `(${newFilecheck})` : ''}
              </ButtonWithToolTip>
            </SpotlightTarget>
            <ButtonWithToolTip
              text={
                !isAuthenticated && !SKIP_AUTH
                  ? '先にログインしてください'
                  : !selectedfileslength
                    ? '削除するファイルを選択してください'
                    : `${selectedfileslength} ${tooltips.deleteSelectedFiles}`
              }
              placement='top'
              onClick={() => setShowDeletePopUp(true)}
              disabled={!selectedfileslength || isReadOnlyUser}
              className='ml-0.5'
              label='ファイルを削除'
              size={isTablet ? 'small' : 'medium'}
              alwaysShowTooltip={true}
            >
              {buttonCaptions.deleteFiles}
              {selectedfileslength != undefined && selectedfileslength > 0 && `(${selectedfileslength})`}
            </ButtonWithToolTip>
            <ButtonWithToolTip
              text={
                completedFiles.length === 0
                  ? '処理が完了した文書がない'
                  : `処理が完了した ${completedFiles.length} 件の文書を一括で表示する（ファイル一覧での選択は不要）`
              }
              label='全文書をプレビュー'
              onClick={handleGraphViewAll}
              alwaysShowTooltip={true}
              disabled={!connectionStatus || completedFiles.length === 0}
              className='ml-0.5'
              size={isTablet ? 'small' : 'medium'}
              fill='outlined'
            >
              全文書をプレビュー ({completedFiles.length})
            </ButtonWithToolTip>
            <SpotlightTarget id='visualizegraphbtn'>
              <Flex flexDirection='row' gap='0'>
                <ButtonWithToolTip
                  text={
                    !selectedfileslength || selectedfileslength === 0
                      ? 'グラフをプレビューするファイルを選択してください'
                      : completedfileNo === 0
                        ? 'プレビューするには処理完了済みのファイルを選択してください'
                        : `${selectedfileslength} 件選択中、${completedfileNo} 件のファイルをプレビューできます`
                  }
                  label='グラフをプレビュー'
                  onClick={handleGraphView}
                  alwaysShowTooltip={true}
                  disabled={showGraphCheck}
                  className='px-0! flex! items-center justify-between gap-4 graphbtn'
                  size={isTablet ? 'small' : 'medium'}
                >
                  <span className='mx-2'>
                    {buttonCaptions.showPreviewGraph}{' '}
                    {selectedfileslength && completedfileNo ? `(${completedfileNo})` : ''}
                  </span>
                </ButtonWithToolTip>
                <div
                  className={`ndl-icon-btn ndl-clean dropdownbtn ${colorMode === 'dark' ? 'darktheme' : ''} ${
                    isTablet ? 'small' : 'medium'
                  }`}
                  onClick={(e) => {
                    setIsGraphBtnMenuOpen((old) => !old);
                    e.stopPropagation();
                  }}
                  ref={graphbtnRef}
                >
                  {!isGraphBtnMenuOpen ? (
                    <ChevronUpIconOutline className='n-size-token-5' />
                  ) : (
                    <ChevronDownIconOutline className='n-size-token-' />
                  )}
                </div>
              </Flex>
            </SpotlightTarget>
            <Menu
              placement='top-end-bottom-end'
              isOpen={isGraphBtnMenuOpen}
              anchorRef={graphbtnRef}
              onClose={() => setIsGraphBtnMenuOpen(false)}
            >
              <Menu.Items
                htmlAttributes={{
                  id: 'default-menu',
                }}
              >
                <Menu.Item title='グラフスキーマ' onClick={handleSchemaView} isDisabled={!connectionStatus} />
                <Menu.Item
                  title='Neo4jでグラフを探索'
                  onClick={handleOpenGraphClick}
                  isDisabled={!filesData.some((f) => f?.status === 'Completed')}
                />
              </Menu.Items>
            </Menu>
          </Flex>
        </Flex>
      </div>
    </>
  );
};

export default React.memo(Content);
