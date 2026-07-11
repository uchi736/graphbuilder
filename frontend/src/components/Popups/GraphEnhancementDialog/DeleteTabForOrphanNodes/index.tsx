import { Checkbox, DataGrid, DataGridComponents, Flex, Typography, useMediaQuery, Button } from '@neo4j-ndl/react';
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { orphanNodeProps } from '../../../../types';
import { getOrphanNodes } from '../../../../services/GetOrphanNodes';
import { useCredentials } from '../../../../context/UserCredentials';
import Legend from '../../../UI/Legend';
import { calcWordColor } from '@neo4j-devtools/word-color';
import { DocumentIconOutline } from '@neo4j-ndl/react/icons';
import ButtonWithToolTip from '../../../UI/ButtonWithToolTip';
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
import DeletePopUp from '../../DeletePopUp/DeletePopUp';
import { tokens } from '@neo4j-ndl/base';
import GraphViewModal from '../../../Graph/GraphViewModal';
import { handleGraphNodeClick } from '../../../ChatBot/chatInfo';
import { ThemeWrapperContext } from '../../../../context/ThemeWrapper';

export default function DeletePopUpForOrphanNodes({
  deleteHandler,
  loading,
}: {
  deleteHandler: (selectedEntities: string[]) => Promise<void>;
  loading: boolean;
}) {
  const { breakpoints } = tokens;
  const isTablet = useMediaQuery(`(min-width:${breakpoints.xs}) and (max-width: ${breakpoints.lg})`);
  const [orphanNodes, setOrphanNodes] = useState<orphanNodeProps[]>([]);
  const [totalOrphanNodes, setTotalOrphanNodes] = useState<number>(0);
  const [isLoading, setLoading] = useState<boolean>(false);
  const { userCredentials } = useCredentials();
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  const tableRef = useRef(null);
  const [showDeletePopUp, setShowDeletePopUp] = useState<boolean>(false);
  const [neoNodes, setNeoNodes] = useState<any[]>([]);
  const [neoRels, setNeoRels] = useState<any[]>([]);
  const [openGraphView, setOpenGraphView] = useState(false);
  const [viewPoint, setViewPoint] = useState('');
  const { colorMode } = useContext(ThemeWrapperContext);
  const ref = useRef<AbortController>();
  const fetchOrphanNodes = useCallback(async () => {
    try {
      setLoading(true);
      const apiresponse = await getOrphanNodes(ref.current?.signal as AbortSignal);
      setLoading(false);
      if (apiresponse.data.data.length) {
        setOrphanNodes(apiresponse.data.data);
        setTotalOrphanNodes(
          apiresponse.data.message != undefined && typeof apiresponse.data.message != 'string'
            ? apiresponse.data.message.total
            : 0
        );
      }
    } catch (error) {
      setLoading(false);
      console.log(error);
    }
  }, []);

  useEffect(() => {
    ref.current = new AbortController();
    if (userCredentials != null) {
      (async () => {
        await fetchOrphanNodes();
      })();
    }
    return () => {
      setOrphanNodes([]);
      setTotalOrphanNodes(0);
      ref?.current?.abort();
    };
  }, [userCredentials]);
  const columnHelper = createColumnHelper<orphanNodeProps>();

  const handleOrphanNodeClick = (elementId: string, viewMode: string) => {
    handleGraphNodeClick(elementId, viewMode, setNeoNodes, setNeoRels, setOpenGraphView, setViewPoint);
  };

  const columns = useMemo(
    () => [
      {
        id: 'Check to Delete All Files',
        header: ({ table }: { table: Table<orphanNodeProps> }) => {
          return (
            <Checkbox
              ariaLabel='header-checkbox'
              isChecked={table.getIsAllRowsSelected()}
              onChange={table.getToggleAllRowsSelectedHandler()}
            />
          );
        },
        cell: ({ row }: { row: Row<orphanNodeProps> }) => {
          return (
            <div className='px-1'>
              <Checkbox
                ariaLabel='row-checkbox'
                onChange={row.getToggleSelectedHandler()}
                htmlAttributes={{ title: '削除する行を選択' }}
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
                className='cursor-pointer! inline!'
                fill='text'
                onClick={() => handleOrphanNodeClick(info.row.id, 'chatInfoView')}
                htmlAttributes={{
                  title: info.getValue() ? info.getValue() : info.row.id,
                }}
              >
                {info.getValue() ? info.getValue() : info.row.id}
              </Button>
            </div>
          );
        },
        header: () => <span>ID</span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.e.labels, {
        id: 'Labels',
        cell: (info) => {
          return (
            <Flex>
              {info.getValue().map((l) => (
                <Legend key={l} title={l} bgColor={calcWordColor(l)} type='node'></Legend>
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
        header: () => <span>関連ドキュメント </span>,
        footer: (info) => info.column.id,
      }),
      columnHelper.accessor((row) => row.chunkConnections, {
        id: 'Connected Chunks',
        cell: (info) => <i>{info?.getValue()}</i>,
        header: () => <span>接続チャンク数</span>,
        footer: (info) => info.column.id,
      }),
    ],
    []
  );
  const table = useReactTable({
    data: orphanNodes,
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

  const selectedFilesCheck = table.getSelectedRowModel().rows.length
    ? `選択したノードを削除 (${table.getSelectedRowModel().rows.length})`
    : '削除するノードを選択';

  const onDeleteHandler = async () => {
    await deleteHandler(table.getSelectedRowModel().rows.map((r) => r.id));
    const selectedRows = table.getSelectedRowModel().rows.map((r) => r.id);
    setTotalOrphanNodes((prev) => prev - selectedRows.length);
    for (let index = 0; index < selectedRows.length; index++) {
      const eid: string = selectedRows[index];
      setOrphanNodes((prev) => prev.filter((node) => node.e.elementId != eid));
    }
    setShowDeletePopUp(false);
    if (totalOrphanNodes) {
      await fetchOrphanNodes();
    }
  };

  return (
    <>
      <div>
        {showDeletePopUp && (
          <DeletePopUp
            open={showDeletePopUp}
            no_of_files={table.getSelectedRowModel().rows.length ?? 0}
            deleteHandler={onDeleteHandler}
            deleteCloseHandler={() => setShowDeletePopUp(false)}
            loading={loading}
            view='settingsView'
          />
        )}
        <div>
          <Flex flexDirection='column'>
            <Flex justifyContent='space-between' flexDirection='row'>
              <Typography variant={isTablet ? 'subheading-medium' : 'subheading-large'}>
                孤立ノードの削除(1バッチあたり100ノード)
              </Typography>
              {totalOrphanNodes > 0 && (
                <Typography variant={isTablet ? 'subheading-medium' : 'subheading-large'}>
                  ノード総数: {totalOrphanNodes}
                </Typography>
              )}
            </Flex>
            <Flex justifyContent='space-between' flexDirection='row'>
              <Typography variant={isTablet ? 'body-small' : 'body-medium'}>
                この機能は、他のどの情報ともつながっていないエンティティを特定して削除することで、ナレッジグラフの精度向上に役立ちます。こうした「孤立した」エンティティは、過去の解析の残骸やデータ処理時のエラーによって生じることがあります。これらを削除することで、よりクリーンで効率的なナレッジグラフを構築でき、より適切で有益な回答につながります。
              </Typography>
            </Flex>
          </Flex>
        </div>
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
            className: 'max-h-[355px] overflow-y-auto!',
          }}
          isLoading={isLoading}
          components={{
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
            onClick={() => setShowDeletePopUp(true)}
            loading={loading}
            text={
              isLoading
                ? '孤立ノードを取得中'
                : !isLoading && !orphanNodes.length
                  ? 'ノードが見つかりません'
                  : !table.getSelectedRowModel().rows.length
                    ? 'ノードが選択されていません'
                    : `選択したノードを削除 (${table.getSelectedRowModel().rows.length})`
            }
            label='孤立ノード削除ボタン'
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
