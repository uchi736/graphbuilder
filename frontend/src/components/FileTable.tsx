import {
  DataGrid,
  DataGridComponents,
  Flex,
  IconButton,
  ProgressBar,
  StatusIndicator,
  TextLink,
  Typography,
  useCopyToClipboard,
  Checkbox,
  useMediaQuery,
} from '@neo4j-ndl/react';
import {
  forwardRef,
  useContext,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  ForwardRefRenderFunction,
} from 'react';
import {
  useReactTable,
  getCoreRowModel,
  createColumnHelper,
  ColumnFiltersState,
  getFilteredRowModel,
  getPaginationRowModel,
  CellContext,
  Table,
  Row,
  getSortedRowModel,
} from '@tanstack/react-table';
import { useFileContext } from '../context/UsersFiles';
import { getSourceNodes } from '../services/GetFiles';
import { getTokenLimits } from '../services/TokenLimits';
import { v4 as uuidv4 } from 'uuid';
import {
  statusCheck,
  isFileCompleted,
  calculateProcessedCount,
  getFileSourceStatus,
  isProcessingFileValid,
  getParsedDate,
} from '../utils/Utils';
import { SourceNode, CustomFile, FileTableProps, UserCredentials, statusupdate, ChildRef } from '../types';
import { useCredentials } from '../context/UserCredentials';
import {
  ArrowPathIconSolid,
  ClipboardDocumentIconSolid,
  CloudArrowUpIconSolid,
  DocumentTextIconSolid,
  ExploreIcon,
} from '@neo4j-ndl/react/icons';
import CustomProgressBar from './UI/CustomProgressBar';
import { useDataGridJa } from './UI/DataGridJa';
import subscribe from '../services/PollingAPI';
import { triggerStatusUpdateAPI } from '../services/ServerSideStatusUpdateAPI';
import useServerSideEvent from '../hooks/useSse';
import { AxiosError } from 'axios';
import { XMarkIconOutline } from '@neo4j-ndl/react/icons';
import cancelAPI from '../services/CancelAPI';
import { IconButtonWithToolTip } from './UI/IconButtonToolTip';
import { batchSize, largeFileSize, llms } from '../utils/Constants';
import {
  EDC_MODEL_FILTER,
  EXTRACTION_METHOD_EDC,
  effectiveExtractionMethod,
  modelColumnLabel,
} from '../utils/ExtractionMethodUtils';
import { showErrorToast, showNormalToast } from '../utils/Toasts';
import { ThemeWrapperContext } from '../context/ThemeWrapper';
import BreakDownPopOver from './BreakDownPopOver';
import { InformationCircleIconOutline } from '@neo4j-ndl/react/icons';
import { useAuth0 } from '@auth0/auth0-react';
import React from 'react';

let onlyfortheFirstRender = true;

const FileTable: ForwardRefRenderFunction<ChildRef, FileTableProps> = (props, ref) => {
  const { connectionStatus, setConnectionStatus, onInspect, onRetry, onChunkView, onUpdate } = props;
  const {
    filesData,
    setFilesData,
    model,
    extractionMethod,
    rowSelection,
    setRowSelection,
    setSelectedRows,
    setProcessedCount,
    queue,
  } = useFileContext();
  const { userCredentials, isReadOnlyUser } = useCredentials();
  const columnHelper = createColumnHelper<CustomFile>();
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isCancellingQueue, setIsCancellingQueue] = useState<boolean>(false);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [filetypeFilter, setFiletypeFilter] = useState<string>('');
  const [fileSourceFilter, setFileSourceFilter] = useState<string>('');
  const [llmtypeFilter, setLLmtypeFilter] = useState<string>('');
  const skipPageResetRef = useRef<boolean>(false);
  const [_, copy] = useCopyToClipboard();
  const { colorMode } = useContext(ThemeWrapperContext);
  const [copyRow, setCopyRow] = useState<boolean>(false);
  const islargeDesktop = useMediaQuery(`(min-width:1440px )`);
  const tableRef = useRef(null);
  const { isAuthenticated } = useAuth0();
  const { updateStatusForLargeFiles } = useServerSideEvent(
    (inMinutes, time, fileName) => {
      showNormalToast(`${fileName} の処理には約 ${time} ${inMinutes ? '分' : '秒'} かかります`);
    },
    (fileName) => {
      showErrorToast(`${fileName} の処理に失敗しました`);
    }
  );

  const handleCopy = (rowData: any) => {
    const rowString = JSON.stringify(rowData, null, 2);
    copy(rowString);
    setCopyRow(true);
    setTimeout(() => {
      setCopyRow(false);
    }, 3000);
  };
  const columns = useMemo(
    () => [
      {
        id: 'select',
        header: ({ table }: { table: Table<CustomFile> }) => {
          const processingcheck = table
            .getPaginationRowModel()
            .rows.map((i) => i.original.status)
            .includes('Processing');
          return (
            <Checkbox
              ariaLabel='header-checkbox'
              isChecked={table.getIsAllPageRowsSelected()}
              onChange={table.getToggleAllPageRowsSelectedHandler()}
              isDisabled={processingcheck}
              htmlAttributes={{
                title: processingcheck
                  ? `処理中のファイルがあるため、削除する場合は個別のチェックボックスを選択してください`
                  : '現在のページのすべての行を削除対象として選択',
              }}
            />
          );
        },
        cell: ({ row }: { row: Row<CustomFile> }) => {
          return (
            <div className='px-1'>
              <Checkbox
                ariaLabel='row-selection'
                isChecked={row.getIsSelected()}
                isDisabled={
                  !row.getCanSelect() ||
                  row.original.status == 'Uploading' ||
                  row.original.status === 'Processing' ||
                  row.original.status === 'Waiting'
                }
                onChange={row.getToggleSelectedHandler()}
              />
            </div>
          );
        },
        size: 80,
      },
      columnHelper.accessor((row) => row.name, {
        id: 'name',
        cell: (info) => {
          return (
            <div className='textellipsis'>
              <span
                title={
                  (info.row.original?.fileSource === 's3 bucket' && info.row.original?.sourceUrl) ||
                  (info.row.original?.fileSource === 'youtube' && info.row.original?.sourceUrl) ||
                  info.getValue()
                }
              >
                {info.getValue()}
              </span>
            </div>
          );
        },
        header: () => <span>名前</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.status, {
        id: 'status',
        cell: (info) => {
          if (info.getValue() != 'Processing') {
            return (
              <div
                className='cellClass flex! gap-1 items-center'
                title={info.row.original?.status === 'Failed' ? info.row.original?.errorMessage : ''}
              >
                <div>
                  <StatusIndicator type={statusCheck(info.getValue())} />
                </div>
                <div className='flex flex-col'>
                  <div>{info.getValue()}</div>
                  {info.getValue() === 'Completed' && (info.row.original?.skipped_chunks ?? 0) > 0 && (
                    <span
                      className='n-body-small n-text-palette-warning-text whitespace-nowrap'
                      title='抽出処理が例外で中断したチャンク（出力上限到達など）。再処理オプションで「Start From Last Processed Position」（続きから）を選択すると、該当チャンクのみ再抽出する'
                    >
                      {`抽出失敗チャンク ${info.row.original?.skipped_chunks} 件`}
                    </span>
                  )}
                </div>
                {(info.getValue() === 'Completed' || info.getValue() === 'Failed' || info.getValue() === 'Cancelled') &&
                  !isReadOnlyUser && (
                    <span className='mx-1'>
                      <IconButtonWithToolTip
                        placement='right'
                        text='再処理'
                        size='small'
                        label='再処理'
                        clean
                        onClick={() => onRetry(info?.row?.id as string)}
                      >
                        <ArrowPathIconSolid className='n-size-token-4' />
                      </IconButtonWithToolTip>
                      {onUpdate && info.row.original?.fileSource === 'local file' && (
                        <IconButtonWithToolTip
                          placement='right'
                          text='更新（同じファイル名の新しい版で再取り込み）'
                          size='small'
                          label='更新'
                          clean
                          onClick={() => onUpdate(info?.row?.id as string)}
                        >
                          <CloudArrowUpIconSolid className='n-size-token-4' />
                        </IconButtonWithToolTip>
                      )}
                    </span>
                  )}
              </div>
            );
          } else if (info.getValue() === 'Processing' && info.row.original.processingProgress === undefined) {
            return (
              <div className='cellClass flex! gap-1 items-center'>
                <div>
                  <StatusIndicator type={statusCheck(info.getValue())} />
                </div>
                <div>
                  <i>Processing</i>
                </div>
                <div className='mx-1'>
                  <IconButton
                    size='medium'
                    htmlAttributes={{
                      title: '処理ジョブをキャンセル',
                    }}
                    ariaLabel='ジョブをキャンセル'
                    isClean
                    isDisabled={info.row.original.processingStatus}
                    onClick={() => {
                      cancelHandler(
                        info.row.original.name as string,
                        info.row.original.id as string,
                        info.row.original.fileSource as string
                      );
                    }}
                  >
                    <XMarkIconOutline />
                  </IconButton>
                </div>
              </div>
            );
          } else if (
            info.getValue() === 'Processing' &&
            info.row.original.processingProgress != undefined &&
            info.row.original.processingProgress < 100
          ) {
            return (
              <div className='cellClass'>
                <ProgressBar
                  heading='Processing '
                  size='small'
                  value={info.row.original.processingProgress}
                ></ProgressBar>
                <div className='mx-1'>
                  <IconButton
                    size='medium'
                    htmlAttributes={{
                      title: '処理ジョブをキャンセル',
                    }}
                    ariaLabel='ジョブをキャンセル'
                    isClean={true}
                    isDisabled={info.row.original.processingStatus}
                    onClick={() => {
                      cancelHandler(
                        info.row.original.name as string,
                        info.row.original.id as string,
                        info.row.original.fileSource as string
                      );
                    }}
                  >
                    <XMarkIconOutline />
                  </IconButton>
                </div>
              </div>
            );
          }
          return (
            <div className='cellClass flex! gap-1'>
              <div>
                <StatusIndicator type={statusCheck(info.getValue())} />
              </div>
              <div>{info.getValue()}</div>
            </div>
          );
        },
        header: () => <span>ステータス</span>,
        footer: (info) => info.column.id,
        filterFn: 'statusFilter' as any,
        size: 250,
        meta: {
          columnActions: {
            actions: [
              {
                title: (
                  <span className={`${statusFilter === 'All' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    すべてのファイル
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('All');
                  table.getColumn('status')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'Completed' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='success'></StatusIndicator> 完了したファイル
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('Completed');
                  table.getColumn('status')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'New' ? 'n-bg-palette-primary-bg-selected' : 'p-2'} p-2`}>
                    <StatusIndicator type='info'></StatusIndicator> 新規ファイル
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('New');
                  table.getColumn('status')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
              {
                title: (
                  <span className={`${statusFilter === 'Failed' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    <StatusIndicator type='danger'></StatusIndicator> 失敗したファイル
                  </span>
                ),
                onClick: () => {
                  setStatusFilter('Failed');
                  table.getColumn('status')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
            ],
            hasDefaultSortingActions: false,
          },
        },
      }),
      columnHelper.accessor((row) => row.uploadProgress, {
        id: 'uploadprogess',
        cell: (info: CellContext<CustomFile, string>) => {
          if (Number(info.getValue()) === 100 || info.row.original?.status === 'New') {
            return (
              <div className='flex! gap-1 items-center'>
                <Typography variant='body-medium'>
                  <StatusIndicator type='success' />
                </Typography>
                <Typography variant='body-medium'>アップロード済み</Typography>
              </div>
            );
          } else if (info.row.original?.status === 'Uploading') {
            return <CustomProgressBar value={Number(info?.getValue())}></CustomProgressBar>;
          } else if (info.row.original?.status === 'Failed') {
            return (
              <div className='flex! gap-1 items-center'>
                <Typography variant='body-medium'>
                  <StatusIndicator type='danger' />
                </Typography>
                <Typography variant='body-medium'> NA</Typography>
              </div>
            );
          }
          return (
            <div className='flex! items-center gap-1'>
              <Typography variant='body-medium'>
                <StatusIndicator type='success' />
              </Typography>
              <Typography variant='body-medium'>Uploaded</Typography>
            </div>
          );
        },
        header: () => <span>アップロード状況</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.size, {
        id: 'fileSize',
        cell: (info: CellContext<CustomFile, string>) => <i>{(Number(info?.getValue()) / 1000)?.toFixed(2)}</i>,
        header: () => <span>サイズ (KB)</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row, {
        id: 'source',
        cell: (info) => {
          if (
            info.row.original.fileSource === 'youtube' ||
            info.row.original.fileSource === 'Wikipedia' ||
            info.row.original.fileSource === 'web-url'
          ) {
            return (
              <Flex>
                <span>
                  <TextLink
                    type='external'
                    href={info.row.original.sourceUrl}
                    htmlAttributes={{
                      target: '_blank',
                    }}
                  >
                    {info.row.original.fileSource}
                  </TextLink>
                </span>
              </Flex>
            );
          }
          return (
            <div>
              <span>{info.row.original.fileSource}</span>
            </div>
          );
        },
        header: () => <span>ソース</span>,
        footer: (info) => info.column.id,
        filterFn: 'fileSourceFilter' as any,
        meta: {
          columnActions: {
            actions: [
              {
                title: (
                  <span className={`${fileSourceFilter === 'All' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    すべてのソース
                  </span>
                ),
                onClick: () => {
                  setFileSourceFilter('All');
                  table.getColumn('source')?.setFilterValue(true);
                },
              },
              ...Array.from(new Set(filesData.map((f) => f.fileSource))).map((t) => {
                return {
                  title: (
                    <span className={`${t === fileSourceFilter ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                      {t}
                    </span>
                  ),
                  onClick: () => {
                    setFileSourceFilter(t as string);
                    table.getColumn('source')?.setFilterValue(true);
                    skipPageResetRef.current = true;
                  },
                };
              }),
            ],
            hasDefaultSortingActions: false,
          },
        },
      }),
      columnHelper.accessor((row) => row, {
        id: 'type',
        cell: (info) => {
          return (
            <div>
              <span>{info.row.original.type}</span>
            </div>
          );
        },
        header: () => <span>種類</span>,
        footer: (info) => info.column.id,
        filterFn: 'fileTypeFilter' as any,
        meta: {
          columnActions: {
            actions: [
              {
                title: (
                  <span className={`${filetypeFilter === 'All' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    すべての種類
                  </span>
                ),
                onClick: () => {
                  setFiletypeFilter('All');
                  table.getColumn('type')?.setFilterValue(true);
                },
              },
              ...Array.from(new Set(filesData.map((f) => f.type))).map((t) => {
                return {
                  title: (
                    <span className={`${t === filetypeFilter ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>{t}</span>
                  ),
                  onClick: () => {
                    setFiletypeFilter(t as string);
                    table.getColumn('type')?.setFilterValue(true);
                    skipPageResetRef.current = true;
                  },
                };
              }),
            ],
            hasDefaultSortingActions: false,
          },
        },
      }),
      columnHelper.accessor((row) => row.model, {
        id: 'model',
        cell: (info) => {
          const model = info.getValue();
          // EDC で抽出した行は「LLM / EDC」。まだ抽出していない行は画面で選んでいる抽出方式で出す
          return <i>{modelColumnLabel(model, effectiveExtractionMethod(info.row.original, extractionMethod))}</i>;
        },
        header: () => <span>モデル</span>,
        footer: (info) => info.column.id,
        filterFn: 'llmTypeFilter' as any,
        meta: {
          columnActions: {
            actions: [
              {
                title: (
                  <span className={`${llmtypeFilter === 'All' ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>
                    すべて
                  </span>
                ),
                onClick: () => {
                  setLLmtypeFilter('All');
                  table.getColumn('model')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
              ...llms.map((m) => {
                return {
                  title: (
                    <span className={`${m === llmtypeFilter ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}>{m}</span>
                  ),
                  onClick: () => {
                    setLLmtypeFilter(m);
                    table.getColumn('model')?.setFilterValue(true);
                    skipPageResetRef.current = true;
                  },
                };
              }),
              {
                title: (
                  <span
                    className={`${llmtypeFilter === EDC_MODEL_FILTER ? 'n-bg-palette-primary-bg-selected' : ''} p-2`}
                  >
                    EDC（抽出方式）
                  </span>
                ),
                onClick: () => {
                  setLLmtypeFilter(EDC_MODEL_FILTER);
                  table.getColumn('model')?.setFilterValue(true);
                  skipPageResetRef.current = true;
                },
              },
            ],
            hasDefaultSortingActions: false,
          },
        },
      }),
      // columnHelper.accessor((row) => row.embedding_model, {
      //   id: 'embedding_model',
      //   cell: (info) => {
      //     const embedding_model = info.getValue();
      //     return <i>{embedding_model ?? ''}</i>;
      //   },
      //   header: () => <span>Embedding Model</span>,
      //   footer: (info) => info.column.id,
      // }),
      columnHelper.accessor((row) => row.nodesCount, {
        id: 'NodesCount',
        cell: (info) => {
          const hasNodeBreakDownValues =
            info.row.original.chunkNodeCount > 0 ||
            info.row.original.communityNodeCount > 0 ||
            info.row.original.entityNodeCount > 0;

          return (
            <Flex alignItems='center' flexDirection='row'>
              <i>{info.getValue()}</i>
              {hasNodeBreakDownValues &&
                (info.row.original.status === 'Completed' ||
                  info.row.original.status === 'Failed' ||
                  info.row.original.status === 'Cancelled') && (
                  <BreakDownPopOver file={info.row.original} isNodeCount={true} />
                )}
            </Flex>
          );
        },
        header: () => <span>ノード</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.relationshipsCount, {
        id: 'relationshipCount',
        cell: (info) => {
          const hasRelationsBreakDownValues =
            info.row.original.chunkRelCount > 0 ||
            info.row.original.communityRelCount > 0 ||
            info.row.original.entityEntityRelCount > 0;
          return (
            <Flex alignItems='center' flexDirection='row'>
              <i>{info.getValue()}</i>
              {hasRelationsBreakDownValues &&
                (info.row.original.status === 'Completed' ||
                  info.row.original.status === 'Failed' ||
                  info.row.original.status === 'Cancelled') && (
                  <BreakDownPopOver file={info.row.original} isNodeCount={false} />
                )}
            </Flex>
          );
        },
        header: () => <span>リレーション</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.token_usage, {
        id: 'tokenUsage',
        cell: (info) => {
          const tokenUsage = info.getValue();
          return <i>{tokenUsage ?? 0}</i>;
        },
        header: () => <span>トークン使用量</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.status, {
        id: 'inspect',
        cell: (info) => (
          <>
            <IconButtonWithToolTip
              placement='right'
              text='グラフ'
              size='large'
              label='グラフ表示'
              disabled={info.getValue() === 'New' || info.getValue() === 'Uploading'}
              clean
              onClick={() => onInspect(info?.row?.original?.name as string)}
            >
              <ExploreIcon className='n-size-token-7' />
            </IconButtonWithToolTip>
            <IconButtonWithToolTip
              placement='left'
              text={copyRow ? 'コピーしました' : 'コピー'}
              size='large'
              label='行をコピー'
              disabled={info.getValue() === 'Uploading'}
              clean
              onClick={() => {
                const copied = { ...info.row.original };
                delete copied.accessToken;
                handleCopy(copied);
              }}
            >
              <ClipboardDocumentIconSolid className={`${copyRow ? 'cursor-progress!' : 'cursor'} `} />
            </IconButtonWithToolTip>
            <IconButtonWithToolTip
              onClick={() => {
                onChunkView(info?.row?.original?.name as string);
              }}
              clean
              placement='left'
              label='chunktextaction'
              text='チャンクを表示'
              size='large'
              disabled={info.getValue() === 'Uploading' || info.getValue() === 'New'}
            >
              <DocumentTextIconSolid className='n-size-token-7' />
            </IconButtonWithToolTip>
          </>
        ),
        maxSize: 300,
        minSize: 180,
        header: () => <span>操作</span>,
        footer: (info) => info.column.id,
      }),
    ],
    [
      filesData.length,
      statusFilter,
      filetypeFilter,
      llmtypeFilter,
      extractionMethod,
      fileSourceFilter,
      isReadOnlyUser,
      colorMode,
      copyRow,
    ]
  );

  const table = useReactTable({
    data: filesData,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    onColumnFiltersChange: setColumnFilters,
    state: {
      columnFilters,
      rowSelection,
    },
    onRowSelectionChange: setRowSelection,
    filterFns: {
      statusFilter: (row, columnId, filterValue) => {
        if (statusFilter === 'All') {
          return row;
        }
        const value = filterValue ? row.original[columnId] === statusFilter : row.original[columnId];
        return value;
      },
      fileTypeFilter: (row) => {
        if (filetypeFilter === 'All') {
          return true;
        }
        return row.original.type === filetypeFilter;
      },
      fileSourceFilter: (row) => {
        if (fileSourceFilter === 'All') {
          return true;
        }
        return row.original.fileSource === fileSourceFilter;
      },
      llmTypeFilter: (row) => {
        if (llmtypeFilter === 'All') {
          return true;
        }
        if (llmtypeFilter === EDC_MODEL_FILTER) {
          return effectiveExtractionMethod(row.original, extractionMethod) === EXTRACTION_METHOD_EDC;
        }
        return row.original.model === llmtypeFilter;
      },
    },
    enableGlobalFilter: false,
    autoResetPageIndex: skipPageResetRef.current,
    enableRowSelection: true,
    enableMultiRowSelection: true,
    getRowId: (row) => row.id,
    enableSorting: true,
    getSortedRowModel: getSortedRowModel(),
  });
  // 件数の表示は下の TableResults で独自に表示するため、表示件数の選択だけを日本語の部品に置き換える
  const dataGridJa = useDataGridJa(table);

  useEffect(() => {
    skipPageResetRef.current = false;
  }, [filesData.length]);

  // 「EDC（抽出方式）」で絞り込んでいる間に抽出方式を替えたら、絞り込みをやり直す。
  // 未抽出の行の方式は画面の選択で決まるが、表の絞り込みは行のデータか絞り込みの値が変わったときにしかやり直されないためである
  useEffect(() => {
    if (llmtypeFilter === EDC_MODEL_FILTER) {
      table.getColumn('model')?.setFilterValue(true);
      skipPageResetRef.current = true;
    }
  }, [extractionMethod]);

  const handleFileUploadError = (error: AxiosError) => {
    // @ts-ignore
    const errorfile = decodeURI(error?.config?.url?.split('?')[0].split('/').at(-1));
    setProcessedCount((prev) => Math.max(prev - 1, 0));
    setFilesData((prevfiles) =>
      prevfiles.map((curfile) => (curfile.name === errorfile ? { ...curfile, status: 'Failed' } : curfile))
    );
  };

  const handleSmallFile = (item: SourceNode, userCredentials: UserCredentials) => {
    subscribe(item.fileName, userCredentials, updatestatus, updateProgress).catch(handleFileUploadError);
  };

  const handleLargeFile = (item: SourceNode, userCredentials: UserCredentials) => {
    triggerStatusUpdateAPI(item.fileName, userCredentials, updateStatusForLargeFiles);
  };
  useEffect(() => {
    const waitingQueue: CustomFile[] = JSON.parse(
      localStorage.getItem('waitingQueue') ?? JSON.stringify({ queue: [] })
    ).queue;
    const fetchFiles = async () => {
      try {
        setIsLoading(true);
        if (!userCredentials) {
          setIsLoading(false);
          return;
        }
        const res = await getSourceNodes(userCredentials);
        getTokenLimits(userCredentials).catch((error) => {
          console.error('Failed to refresh token limits:', error);
        });
        if (!res.data) {
          throw new Error('バックエンドの接続を確認してください');
        }
        const stringified = waitingQueue.reduce((accu, f) => {
          const key = f.id;
          // @ts-ignore
          accu[key] = true;
          return accu;
        }, {});
        setRowSelection(stringified);
        if (res.data.status !== 'Failed') {
          const prefiles: CustomFile[] = [];
          if (res.data.data.length) {
            res.data.data.forEach((item) => {
              if (item.fileName != undefined && item.fileName.length) {
                const waitingFile =
                  waitingQueue.length && waitingQueue.find((f: CustomFile) => f.name === item.fileName);
                if (isFileCompleted(waitingFile as CustomFile, item)) {
                  setProcessedCount((prev) => calculateProcessedCount(prev, batchSize));
                  queue.remove((i) => i.name === item.fileName);
                }
                if (waitingFile && item.status === 'Completed') {
                  setProcessedCount((prev) => {
                    if (prev === batchSize) {
                      return batchSize - 1;
                    }
                    return prev + 1;
                  });
                  queue.remove((i) => i.name === item.fileName);
                }
                prefiles.push({
                  name: item?.fileName,
                  size: item?.fileSize ?? 0,
                  type: item?.fileType?.includes('.')
                    ? (item?.fileType?.substring(1)?.toUpperCase() ?? 'None')
                    : (item?.fileType?.toUpperCase() ?? 'None'),
                  nodesCount: item?.nodeCount ?? 0,
                  processingTotalTime: item?.processingTime ?? 'None',
                  relationshipsCount: item?.relationshipCount ?? 0,
                  status: waitingFile ? 'Waiting' : getFileSourceStatus(item),
                  model: item?.model ?? model,
                  extraction_method: item?.extraction_method,
                  id: !waitingFile ? uuidv4() : waitingFile.id,
                  sourceUrl: item?.url != 'None' && item?.url != '' ? item.url : '',
                  fileSource: item?.fileSource ?? 'None',
                  gcsBucket: item?.gcsBucket,
                  gcsBucketFolder: item?.gcsBucketFolder,
                  errorMessage: item?.errorMessage,
                  uploadProgress: item?.uploadprogress ?? 0,
                  googleProjectId: item?.gcsProjectId,
                  language: item?.language ?? '',
                  processingProgress:
                    item?.processed_chunk != undefined &&
                    item?.total_chunks != undefined &&
                    !isNaN(Math.floor((item?.processed_chunk / item?.total_chunks) * 100))
                      ? Math.floor((item?.processed_chunk / item?.total_chunks) * 100)
                      : undefined,
                  accessToken: item?.accessToken ?? '',
                  // 再処理の指定は「再処理可能」の文書だけ写す。完了した後も DB に残る retry_condition を、次の生成で送らないため（T16）
                  retryOption: item.status === 'Ready to Reprocess' ? (item.retry_condition ?? '') : '',
                  retryOptionStatus: false,
                  chunkNodeCount: item.chunkNodeCount ?? 0,
                  chunkRelCount: item.chunkRelCount ?? 0,
                  entityNodeCount: item.entityNodeCount ?? 0,
                  entityEntityRelCount: item.entityEntityRelCount ?? 0,
                  communityNodeCount: item.communityNodeCount ?? 0,
                  communityRelCount: item.communityRelCount ?? 0,
                  createdAt: item.createdAt != undefined ? getParsedDate(item?.createdAt) : undefined,
                  token_usage: item.token_usage ?? 0,
                  embedding_model: item.embedding_model ?? '',
                  skipped_chunks: item.skipped_chunks ?? 0,
                });
              }
            });
            res.data.data.forEach((item) => {
              if (isProcessingFileValid(item, userCredentials as UserCredentials)) {
                item.fileSize < largeFileSize
                  ? handleSmallFile(item, userCredentials as UserCredentials)
                  : handleLargeFile(item, userCredentials as UserCredentials);
              }
            });
          } else {
            queue.clear();
          }
          setIsLoading(false);
          setFilesData(prefiles);
        } else {
          throw new Error(res?.data?.error);
        }
        setIsLoading(false);
      } catch (error: any) {
        console.log(error);
        if (error instanceof Error) {
          showErrorToast(error.message);
        }

        setIsLoading(false);
        setConnectionStatus(false);
        setFilesData([]);
      }
    };
    if (connectionStatus) {
      fetchFiles();
    } else {
      setFilesData([]);
    }
  }, [connectionStatus, userCredentials]);

  useEffect(() => {
    if (connectionStatus && filesData.length && onlyfortheFirstRender && !isReadOnlyUser) {
      const processingFilesCount = filesData.filter((f) => f.status === 'Processing').length;
      if (processingFilesCount) {
        if (processingFilesCount === 1) {
          setProcessedCount(1);
        }
        showNormalToast(`ファイルを処理中です。前のバッチが完了するまでお待ちください`);
      } else {
        const waitingQueue: CustomFile[] = JSON.parse(
          localStorage.getItem('waitingQueue') ?? JSON.stringify({ queue: [] })
        ).queue;
        if (waitingQueue.length) {
          props.handleGenerateGraph();
        }
      }
      onlyfortheFirstRender = false;
    }
  }, [connectionStatus, filesData.length, isReadOnlyUser]);

  const refreshFileData = async () => {
    if (!userCredentials) {
      return;
    }
    try {
      const res = await getSourceNodes(userCredentials);
      getTokenLimits(userCredentials).catch((error) => {
        console.error('Failed to refresh token limits:', error);
      });
      if (res.data && res.data.status !== 'Failed' && res.data.data.length) {
        const updatedFiles = res.data.data
          .map((item: SourceNode) => {
            const existingFile = filesData.find((f) => f.name === item.fileName);
            if (existingFile) {
              // Check if file is in queue
              const isInQueue = queue.items.some((f) => f.name === item.fileName);
              return {
                ...existingFile,
                status: isInQueue ? 'Waiting' : getFileSourceStatus(item),
                nodesCount: item?.nodeCount ?? existingFile.nodesCount,
                relationshipsCount: item?.relationshipCount ?? existingFile.relationshipsCount,
                processingTotalTime: item?.processingTime ?? existingFile.processingTotalTime,
                token_usage: item.token_usage ?? existingFile.token_usage ?? 0,
                skipped_chunks: item.skipped_chunks ?? existingFile.skipped_chunks,
              };
            }
            return existingFile;
          })
          .filter(Boolean);

        setFilesData(updatedFiles as CustomFile[]);
        setRowSelection((prev) => {
          const updated = { ...prev };
          updatedFiles.forEach((file) => {
            if (file?.status === 'Cancelled' && updated[file.id]) {
              delete updated[file.id];
            }
          });
          return updated;
        });
      }
    } catch (error) {
      console.error('Refresh failed:', error);
    }
  };

  const cancelQueue = async () => {
    if (queue.isEmpty()) {
      showNormalToast('キャンセルする待機中のファイルはありません');
      return;
    }

    setIsCancellingQueue(true);
    try {
      const queuedFileNames = queue.items.map((f) => f.name as string).filter(Boolean);
      const queuedFileSources = queue.items.map((f) => f.fileSource as string).filter(Boolean);
      const res = await cancelAPI(queuedFileNames, queuedFileSources);

      if (res.data.status === 'Success') {
        queue.clear();
        await refreshFileData();

        showNormalToast(`待機中のファイル ${queuedFileNames.length} 件をキャンセルしました`);
      } else {
        throw new Error(res.data.error || 'キューのキャンセルに失敗しました');
      }
    } catch (err) {
      if (err instanceof Error) {
        showErrorToast(`キューのキャンセルに失敗しました: ${err.message}`);
      }
    } finally {
      setIsCancellingQueue(false);
    }
  };

  const cancelHandler = async (fileName: string, id: string, fileSource: string) => {
    setFilesData((prevfiles) =>
      prevfiles.map((curfile) => {
        if (curfile.id === id) {
          return {
            ...curfile,
            processingStatus: true,
          };
        }
        return curfile;
      })
    );
    try {
      const res = await cancelAPI([fileName], [fileSource]);
      if (res.data.status === 'Success') {
        setFilesData((prevfiles) =>
          prevfiles.map((curfile) => {
            if (curfile.id === id) {
              return {
                ...curfile,
                status: 'Cancelled',
                processingStatus: false,
              };
            }
            return curfile;
          })
        );
        setRowSelection((prev) => {
          const updated = { ...prev };
          delete updated[id];
          return updated;
        });
        setProcessedCount((prev) => {
          if (prev == batchSize) {
            return batchSize - 1;
          }
          return prev + 1;
        });
        queue.remove((i) => i.name === fileName);
      } else {
        let errorobj = { error: res.data.error, message: res.data.message, fileName };
        throw new Error(JSON.stringify(errorobj));
      }
    } catch (err) {
      setFilesData((prevfiles) =>
        prevfiles.map((curfile) => {
          if (curfile.id === id) {
            return {
              ...curfile,
              processingStatus: false,
            };
          }
          return curfile;
        })
      );
      if (err instanceof Error) {
        const error = JSON.parse(err.message);
        if (Object.keys(error).includes('fileName')) {
          const { message } = error;
          showErrorToast(message);
        }
      }
    }
  };

  const updatestatus = (i: statusupdate) => {
    const { file_name } = i;
    const {
      fileName,
      nodeCount = 0,
      relationshipCount = 0,
      processingTime = 0,
      model,
      extraction_method,
      status,
      processed_chunk = 0,
      total_chunks,
      chunkNodeCount,
      entityNodeCount,
      communityNodeCount,
      chunkRelCount,
      entityEntityRelCount,
      communityRelCount,
      token_usage,
      embedding_model,
      skipped_chunks,
    } = file_name;
    if (fileName && total_chunks) {
      setFilesData((prevfiles) =>
        prevfiles.map((curfile) => {
          if (curfile.name == fileName) {
            return {
              ...curfile,
              status: status,
              nodesCount: nodeCount,
              relationshipsCount: relationshipCount,
              model: model,
              skipped_chunks: skipped_chunks ?? curfile.skipped_chunks,
              extraction_method: extraction_method ?? curfile.extraction_method,
              processingTotalTime: processingTime?.toFixed(2),
              processingProgress: Math.floor((processed_chunk / total_chunks) * 100),
              chunkNodeCount: chunkNodeCount ?? 0,
              entityNodeCount: entityNodeCount ?? 0,
              communityNodeCount: communityNodeCount ?? 0,
              chunkRelCount: chunkRelCount ?? 0,
              entityEntityRelCount: entityEntityRelCount ?? 0,
              communityRelCount: communityRelCount ?? 0,
              token_usage: token_usage ?? 0,
              embedding_model: embedding_model ?? '',
            };
          }
          return curfile;
        })
      );
      setProcessedCount((prev) => {
        if (prev == batchSize) {
          return batchSize - 1;
        }
        return prev + 1;
      });
      queue.remove((i) => i.name === fileName);
    }
  };

  const updateProgress = (i: statusupdate) => {
    const { file_name } = i;
    const {
      fileName,
      nodeCount = 0,
      relationshipCount = 0,
      status,
      processed_chunk = 0,
      total_chunks,
      chunkNodeCount,
      entityNodeCount,
      communityNodeCount,
      chunkRelCount,
      entityEntityRelCount,
      communityRelCount,
      token_usage,
      embedding_model,
    } = file_name;
    if (fileName && total_chunks) {
      setFilesData((prevfiles) =>
        prevfiles.map((curfile) => {
          if (curfile.name == fileName) {
            return {
              ...curfile,
              status: status,
              nodesCount: nodeCount,
              relationshipsCount: relationshipCount,
              processingProgress: Math.floor((processed_chunk / total_chunks) * 100),
              chunkNodeCount: chunkNodeCount ?? 0,
              entityNodeCount: entityNodeCount ?? 0,
              communityNodeCount: communityNodeCount ?? 0,
              chunkRelCount: chunkRelCount ?? 0,
              entityEntityRelCount: entityEntityRelCount ?? 0,
              communityRelCount: communityRelCount ?? 0,
              token_usage: token_usage ?? 0,
              embedding_model: embedding_model ?? '',
            };
          }
          return curfile;
        })
      );
    }
  };

  useImperativeHandle(
    ref,
    () => ({
      getSelectedRows: () => table.getSelectedRowModel().rows.map((r) => r.original),
    }),
    [table]
  );

  useEffect(() => {
    setSelectedRows(table.getSelectedRowModel().rows.map((i) => i.id));
  }, [table.getSelectedRowModel()]);

  return (
    <>
      {filesData ? (
        <>
          <DataGrid
            ref={tableRef}
            isResizable={true}
            tableInstance={table}
            styling={{
              borderStyle: 'all-sides',
              hasZebraStriping: true,
              headerStyle: 'clean',
            }}
            isLoading={isLoading}
            rootProps={{
              className: `absolute h-[67%] left-10 filetable ${!islargeDesktop ? 'top-[17%]' : 'top-[14%]'}`,
            }}
            components={{
              Body: () => (
                <DataGridComponents.Body
                  innerProps={{
                    className: colorMode == 'dark' ? 'tbody-dark' : 'tbody-light',
                  }}
                />
              ),
              TableResults: () => {
                if (connectionStatus && !isAuthenticated && !isLoading && filesData.length === 0) {
                  return (
                    <DataGridComponents.TableResults>
                      <Flex flexDirection='row' gap='0' alignItems='center'>
                        <span>
                          <InformationCircleIconOutline className='n-size-token-6' />
                        </span>
                        {` データが取り込まれていません。ナレッジグラフの構築を開始するには、メインアプリケーションにログインしてください。`}
                        <span></span>
                      </Flex>
                    </DataGridComponents.TableResults>
                  );
                } else if (connectionStatus) {
                  const queueSize = queue.size();
                  return (
                    <DataGridComponents.TableResults>
                      <Flex flexDirection='row' gap='4' alignItems='center'>
                        <Flex flexDirection='row' gap='0' alignItems='center'>
                          <span>
                            <InformationCircleIconOutline className='n-size-token-6' />
                          </span>
                          {`1 文書あたりの取り込み上限は ${import.meta.env.VITE_MAX_TOKEN_CHUNK_SIZE ?? '200,000'} トークンである。超過分は取り込まれない。`}
                        </Flex>
                        {queueSize > 0 && (
                          <Flex
                            flexDirection='row'
                            gap='2'
                            alignItems='center'
                            className={`${isCancellingQueue ? 'opacity-50' : 'animate-pulse'} bg-palette-warning-bg-weak rounded-md px-3 py-2 border border-palette-warning-border`}
                          >
                            <InformationCircleIconOutline className='n-size-token-5 text-palette-warning-text' />
                            <Typography variant='body-medium' className='font-semibold! text-palette-warning-text'>
                              {isCancellingQueue
                                ? '待機キューのファイルをキャンセルしています...'
                                : `${queueSize} 件のファイルがキューで待機中です`}
                            </Typography>
                            {!isReadOnlyUser && (
                              <IconButtonWithToolTip
                                placement='right'
                                text={isCancellingQueue ? 'キャンセル中...' : 'すべての待機中ファイルをキャンセル'}
                                size='small'
                                label='キューをキャンセル'
                                clean
                                disabled={isCancellingQueue}
                                onClick={cancelQueue}
                              >
                                <XMarkIconOutline className='n-size-token-4' />
                              </IconButtonWithToolTip>
                            )}
                          </Flex>
                        )}
                      </Flex>
                    </DataGridComponents.TableResults>
                  );
                }
                return <dataGridJa.TableResults />;
              },
              RowsPerPage: dataGridJa.RowsPerPage,
              NoDataPlaceholder: dataGridJa.NoDataPlaceholder,
              LoadingPlaceholder: dataGridJa.LoadingPlaceholder,
              PaginationNumericButton: ({ isSelected, innerProps, ...restProps }) => {
                return (
                  <DataGridComponents.PaginationNumericButton
                    {...restProps}
                    isSelected={isSelected}
                    innerProps={{
                      ...innerProps,
                      style: {
                        ...(isSelected && {
                          backgroundSize: '200% auto',
                          borderRadius: '10px',
                        }),
                      },
                    }}
                  />
                );
              },
            }}
            isKeyboardNavigable={false}
          />
        </>
      ) : null}
    </>
  );
};

export default React.memo(forwardRef(FileTable));
