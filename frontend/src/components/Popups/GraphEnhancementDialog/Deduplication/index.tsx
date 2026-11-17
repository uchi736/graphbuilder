import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { getDuplicateNodes } from '../../../../services/GetDuplicateNodes';
import { useCredentials } from '../../../../context/UserCredentials';
import { dupNodes, selectedDuplicateNodes } from '../../../../types';
import {
  useReactTable,
  getCoreRowModel,
  createColumnHelper,
  getFilteredRowModel,
  getPaginationRowModel,
  Table,
  Row,
  getSortedRowModel,
} from '@tanstack/react-table';
import { Checkbox, DataGrid, DataGridComponents, Flex, Tag, Typography, useMediaQuery, Button } from '@neo4j-ndl/react';
import Legend from '../../../UI/Legend';
import { useDataGridJa } from '../../../UI/DataGridJa';
import { DocumentIconOutline } from '@neo4j-ndl/react/icons';
import { calcWordColor } from '@neo4j-devtools/word-color';
import ButtonWithToolTip from '../../../UI/ButtonWithToolTip';
import mergeDuplicateNodes from '../../../../services/MergeDuplicateEntities';
import { tokens } from '@neo4j-ndl/base';
import GraphViewModal from '../../../Graph/GraphViewModal';
import { handleGraphNodeClick } from '../../../ChatBot/chatInfo';
import { ThemeWrapperContext } from '../../../../context/ThemeWrapper';
import {
  describeCaughtGraphEditFailure,
  describeGraphEditError,
  markDistinct,
} from '../../../../services/GraphEdit';
import { showErrorToast, showSuccessToast } from '../../../../utils/Toasts';
import { graphEditUser, visibleLabels } from '../../../../utils/GraphEditUtils';

const isCancelled = (error: any) => error?.code === 'ERR_CANCELED' || error?.name === 'CanceledError';

// 「非同一として登録」（M10）。処理中は押せないようにする
function DistinctButton({ onMark, isDisabled }: { onMark: () => Promise<void>; isDisabled: boolean }) {
  const [marking, setMarking] = useState<boolean>(false);
  return (
    <Button
      size='small'
      fill='outlined'
      className='whitespace-nowrap'
      isLoading={marking}
      isDisabled={isDisabled}
      htmlAttributes={{ title: 'この行の組を非同一として修正台帳に登録し、以後の候補から除外する' }}
      onClick={async () => {
        setMarking(true);
        try {
          await onMark();
        } finally {
          setMarking(false);
        }
      }}
    >
      非同一として登録
    </Button>
  );
}

export default function DeduplicationTab() {
  const { breakpoints } = tokens;
  const isTablet = useMediaQuery(`(min-width:${breakpoints.xs}) and (max-width: ${breakpoints.lg})`);
  const isSmallDesktop = useMediaQuery(`(min-width: ${breakpoints.lg})`);
  const { userCredentials, isReadOnlyUser } = useCredentials();
  const [duplicateNodes, setDuplicateNodes] = useState<dupNodes[]>([]);
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  const [isLoading, setLoading] = useState<boolean>(false);
  const [mergeAPIloading, setmergeAPIloading] = useState<boolean>(false);
  const tableRef = useRef(null);
  const [neoNodes, setNeoNodes] = useState<any[]>([]);
  const [neoRels, setNeoRels] = useState<any[]>([]);
  const [openGraphView, setOpenGraphView] = useState(false);
  const [viewPoint, setViewPoint] = useState('');
  const [nodesCount, setNodesCount] = useState<number>(0);
  const { colorMode } = useContext(ThemeWrapperContext);
  const abortRef = useRef<AbortController>();

  const fetchDuplicateNodes = useCallback(async () => {
    try {
      setLoading(true);
      const duplicateNodesData = await getDuplicateNodes(abortRef?.current?.signal as AbortSignal);
      setLoading(false);
      if (duplicateNodesData.data.status === 'Failed') {
        throw new Error(duplicateNodesData.data.error);
      }
      if (duplicateNodesData.data.data.length) {
        setDuplicateNodes(duplicateNodesData.data.data);
        // @ts-ignore
        setNodesCount(duplicateNodesData.data.message.total);
      } else {
        setDuplicateNodes([]);
      }
    } catch (error) {
      setLoading(false);
      if (!isCancelled(error)) {
        showErrorToast(`重複の候補を取得できませんでした。${describeCaughtGraphEditFailure(error)}`);
      }
    }
  }, [userCredentials]);

  useEffect(() => {
    abortRef.current = new AbortController();
    if (userCredentials != null) {
      (async () => {
        await fetchDuplicateNodes();
      })();
    }
    return () => {
      abortRef.current?.abort();
    };
  }, [userCredentials]);

  const clickHandler = async () => {
    try {
      const selectedNodeMap = table.getSelectedRowModel().rows.map(
        (r): selectedDuplicateNodes => ({
          firstElementId: r.id,
          similarElementIds: r.original.similar.map((s) => s.elementId),
        })
      );
      setmergeAPIloading(true);
      const response = await mergeDuplicateNodes(selectedNodeMap);
      table.resetRowSelection();
      table.resetPagination();
      setmergeAPIloading(false);
      if (response.data.status === 'Failed') {
        throw new Error(response.data.error);
      }
      // 行が重なる（前の行の統合で統合先か似たエンティティが消えた）行などは、バックエンドが飛ばして skipped で返す
      const skipped = response.data.skipped ?? [];
      if (skipped.length) {
        const details = skipped.map((s) => `${s.row} 行目: ${s.reason}`).join(' ');
        showErrorToast(
          `${selectedNodeMap.length - skipped.length} 行の重複ノードを統合しました。${skipped.length} 行は統合しませんでした（${details}）。候補を再取得してください。`,
          false
        );
      } else {
        showSuccessToast(`${selectedNodeMap.length} 行の重複ノードを統合しました。`);
      }
    } catch (error) {
      setmergeAPIloading(false);
      showErrorToast(`重複ノードを統合できませんでした。${describeCaughtGraphEditFailure(error)}`);
    }
  };

  // その行のエンティティと、残っている似たエンティティの組を、非同一として修正台帳に登録する
  const markDistinctRow = async (row: dupNodes) => {
    const user = graphEditUser(userCredentials);
    let registered = 0;
    try {
      for (const similarNode of row.similar) {
        await markDistinct({ a_element_id: row.e.elementId, b_element_id: similarNode.elementId, user });
        registered += 1;
      }
      showSuccessToast(
        `${row.e.id} と ${row.similar.map((s) => s.id).join('、')} を非同一として登録しました。以後の候補から除外されます。`
      );
      setDuplicateNodes((prev) => prev.filter((d) => d.e.elementId !== row.e.elementId));
      setRowSelection((prev) => Object.fromEntries(Object.entries(prev).filter(([id]) => id !== row.e.elementId)));
    } catch (error) {
      showErrorToast(
        `非同一として登録できませんでした（${registered}/${row.similar.length} 組は登録済み）。${describeGraphEditError(error)}`
      );
    }
  };
  // 表の列の定義は初回に作るので、最新の処理を参照させる
  const markDistinctRef = useRef(markDistinctRow);
  markDistinctRef.current = markDistinctRow;

  const columnHelper = createColumnHelper<dupNodes>();
  const onRemove = (nodeid: string, similarNodeId: string) => {
    setDuplicateNodes((prev) => {
      return prev.map((d) =>
        (d.e.elementId === nodeid
          ? {
              ...d,
              similar: d.similar.filter((n) => n.elementId != similarNodeId),
            }
          : d)
      );
    });
  };

  const handleDuplicateNodeClick = (elementId: string, viewMode: string) => {
    handleGraphNodeClick(elementId, viewMode, setNeoNodes, setNeoRels, setOpenGraphView, setViewPoint);
  };

  const columns = useMemo(
    () => [
      {
        id: 'Check to Delete All Files',
        header: ({ table }: { table: Table<dupNodes> }) => {
          return (
            <Checkbox
              ariaLabel='header-checkbox'
              isChecked={table.getIsAllRowsSelected()}
              onChange={table.getToggleAllRowsSelectedHandler()}
            />
          );
        },
        cell: ({ row }: { row: Row<dupNodes> }) => {
          return (
            <div className='px-1'>
              <Checkbox
                ariaLabel='row-checkbox'
                onChange={row.getToggleSelectedHandler()}
                htmlAttributes={{ title: '統合する行を選択' }}
                isChecked={row.getIsSelected()}
              />
            </div>
          );
        },
        size: 80,
      },
      columnHelper.accessor((row) => row.e.id, {
        id: 'Id',
        cell: (info) => {
          return (
            <div className='textellipsis'>
              <Button
                className='cursor-pointer! max-w-full'
                fill='text'
                onClick={() => handleDuplicateNodeClick(info.row.id, 'chatInfoView')}
                htmlAttributes={{
                  title: info.getValue(),
                }}
              >
                <span className='truncate'>{info.getValue()}</span>
              </Button>
            </div>
          );
        },
        header: () => <span>ID</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.similar, {
        id: 'Similar Nodes',
        cell: (info) => {
          return (
            <Flex>
              {info.getValue().map((s, index) => (
                <Tag
                  style={{
                    backgroundColor: `${calcWordColor(s.id)}`,
                  }}
                  key={`${s.elementId}${index}`}
                  onRemove={() => {
                    onRemove(info.row.original.e.elementId, s.elementId);
                  }}
                  isRemovable={true}
                  type='default'
                  size={isTablet ? 'small' : 'medium'}
                >
                  {s.id}
                </Tag>
              ))}
            </Flex>
          );
        },
        header: () => <span>類似ノード</span>,
        size: isTablet || isSmallDesktop ? 250 : 150,
      }),
      columnHelper.accessor((row) => row.e.labels, {
        id: 'Labels',
        cell: (info) => {
          // 管理用ラベル __Entity__ は表示せず、ラベルを横に並べる
          return (
            <Flex flexDirection='row' flexWrap='wrap' gap='2'>
              {visibleLabels(info.getValue()).map((l, index) => (
                <Legend key={index} title={l} bgColor={calcWordColor(l)} type='node'></Legend>
              ))}
            </Flex>
          );
        },
        header: () => <span>ラベル</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.documents, {
        id: 'Connected Documents',
        cell: (info) => {
          return (
            <Flex className='textellipsis'>
              {Array.from(new Set([...info.getValue()])).map((d, index) => (
                <Flex key={`d${index}`} flexDirection='row'>
                  <span>
                    <DocumentIconOutline className='n-size-token-7' />
                  </span>
                  <span>{d}</span>
                </Flex>
              ))}
            </Flex>
          );
        },
        header: () => <span>関連文書</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.chunkConnections, {
        id: 'Connected Chunks',
        cell: (info) => <i>{info?.getValue()}</i>,
        header: () => <span>接続チャンク数</span>,
        footer: (info) => info.column.id,
      }),
      ...(isReadOnlyUser
        ? []
        : [
            {
              id: 'Mark Distinct',
              header: () => <span>操作</span>,
              cell: ({ row }: { row: Row<dupNodes> }) => (
                <DistinctButton
                  isDisabled={!row.original.similar.length}
                  onMark={() => markDistinctRef.current(row.original)}
                />
              ),
              size: 200,
            },
          ]),
    ],
    [isReadOnlyUser]
  );
  const table = useReactTable({
    data: duplicateNodes,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    state: {
      rowSelection,
    },
    onRowSelectionChange: setRowSelection,
    enableGlobalFilter: false,
    autoResetPageIndex: false,
    enableRowSelection: true,
    enableMultiRowSelection: true,
    getRowId: (row) => row.e.elementId,
    enableSorting: true,
    getSortedRowModel: getSortedRowModel(),
    initialState: {
      pagination: {
        pageSize: 5,
      },
    },
  });
  const dataGridJa = useDataGridJa(table);
  const selectedFilesCheck = mergeAPIloading
    ? '統合中...'
    : table.getSelectedRowModel().rows.length
      ? `重複ノードを統合 (${table.getSelectedRowModel().rows.length})`
      : '統合するノードを選択';
  return (
    <>
      <div>
        <Flex justifyContent='space-between' flexDirection='row'>
          <Flex>
            <Typography variant={'subheading-medium'}>重複エンティティの統合</Typography>
            <Typography variant={'body-small'}>
              同じラベルを持ち、名前または埋め込みが類似するエンティティの組（例：「Apple」と「Apple
              Inc.」）を一覧表示する。選択した行のエンティティを 1
              つに統合する。同一でない組は「非同一として登録」で以後の候補から除外できる。
            </Typography>
          </Flex>
          {nodesCount > 0 && (
            <Typography variant={'subheading-medium'} className='whitespace-nowrap'>
              重複ノード総数：{nodesCount}
            </Typography>
          )}
        </Flex>
        <DataGrid
          ref={tableRef}
          isResizable={true}
          tableInstance={table}
          styling={{
            borderStyle: 'all-sides',
            hasZebraStriping: true,
            headerStyle: 'clean',
          }}
          rootProps={{
            className: 'max-h-[480px] overflow-y-auto!',
          }}
          isLoading={isLoading}
          components={{
            ...dataGridJa,
            Body: () => (
              <DataGridComponents.Body
                innerProps={{
                  className: colorMode == 'dark' ? 'tbody-dark' : 'tbody-light',
                }}
              />
            ),
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
        <Flex className='mt-3' flexDirection='row' justifyContent='flex-end'>
          <ButtonWithToolTip
            onClick={async () => {
              await clickHandler();
              await fetchDuplicateNodes();
            }}
            loading={mergeAPIloading}
            text={
              isLoading
                ? '重複ノードを取得中'
                : !isLoading && !duplicateNodes.length
                  ? 'ノードが見つかりません'
                  : !table.getSelectedRowModel().rows.length
                    ? 'ノードが選択されていません'
                    : mergeAPIloading
                      ? '統合中'
                      : `選択したノードを統合 (${table.getSelectedRowModel().rows.length})`
            }
            label='重複ノードの統合ボタン'
            disabled={!table.getSelectedRowModel().rows.length}
            placement='top'
          >
            {selectedFilesCheck}
          </ButtonWithToolTip>
        </Flex>
      </div>
      {openGraphView && (
        <GraphViewModal
          open={openGraphView}
          setGraphViewOpen={setOpenGraphView}
          viewPoint={viewPoint}
          nodeValues={neoNodes}
          relationshipValues={neoRels}
        />
      )}
    </>
  );
}
