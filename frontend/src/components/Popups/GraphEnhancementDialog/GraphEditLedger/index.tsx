import { Banner, Button, DataGrid, DataGridComponents, Dialog, Flex, Select, Typography } from '@neo4j-ndl/react';
import { ColumnDef, RowData, getCoreRowModel, getPaginationRowModel, useReactTable } from '@tanstack/react-table';
import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useCredentials } from '../../../../context/UserCredentials';
import { ThemeWrapperContext } from '../../../../context/ThemeWrapper';
import {
  deleteLedgerRow,
  describeGraphEditError,
  getEditHistory,
  getLedgerRows,
  undoEditOperation,
} from '../../../../services/GraphEdit';
import { GraphEditHistoryRow, GraphEditLedgerRow } from '../../../../types';
import { showErrorToast, showSuccessToast } from '../../../../utils/Toasts';
import {
  GRAPH_EDIT_LEDGER_KIND_LABELS,
  GRAPH_EDIT_OP_LABELS,
  describeLedgerRow,
  formatGraphEditTime,
  graphEditUser,
} from '../../../../utils/GraphEditUtils';
import { useDataGridJa } from '../../../UI/DataGridJa';

const PAGE_SIZE = 50;

const KIND_OPTIONS = [
  { value: '', label: 'すべて' },
  ...Object.entries(GRAPH_EDIT_LEDGER_KIND_LABELS).map(([value, label]) => ({ value, label })),
];

const opLabel = (op: string) => GRAPH_EDIT_OP_LABELS[op] ?? op;
const kindLabel = (kind: string) => GRAPH_EDIT_LEDGER_KIND_LABELS[kind] ?? kind;

const historyState = (row: GraphEditHistoryRow) => {
  if (row.undone) {
    return '取り消し済み';
  }
  if (row.undoable) {
    return '取り消し可';
  }
  return '取り消し不可';
};

// 状態のセル。取り消し不可の理由は括弧で包まず、2 行目に表示する
function HistoryState({ row }: { row: GraphEditHistoryRow }) {
  const isBlocked = !row.undone && !row.undoable;
  return (
    <div className='flex flex-col whitespace-normal'>
      <span>{historyState(row)}</span>
      {isBlocked && row.reason ? <Typography variant='body-small'>{row.reason}</Typography> : null}
    </div>
  );
}

// 既存の表（孤立ノード・重複統合のタブ）と同じ見た目の表
function LedgerGrid<T extends RowData>({
  data,
  columns,
  isLoading,
  rowId,
}: {
  data: T[];
  columns: ColumnDef<T, any>[];
  isLoading: boolean;
  rowId: (row: T) => string;
}) {
  const { colorMode } = useContext(ThemeWrapperContext);
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getRowId: rowId,
    autoResetPageIndex: false,
    enableSorting: false,
    initialState: {
      pagination: {
        pageSize: 10,
      },
    },
  });
  const dataGridJa = useDataGridJa(table);
  return (
    <DataGrid
      isResizable={true}
      tableInstance={table}
      styling={{
        borderStyle: 'all-sides',
        hasZebraStriping: true,
        headerStyle: 'clean',
      }}
      rootProps={{
        className: 'max-h-[420px] overflow-y-auto!',
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
      }}
      isKeyboardNavigable={false}
    />
  );
}

function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  isDanger = false,
  loading,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children: React.ReactNode;
  confirmLabel: string;
  isDanger?: boolean;
  loading: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog isOpen={open} onClose={onCancel} size='small' htmlAttributes={{ 'aria-label': title }}>
      <Dialog.Header>{title}</Dialog.Header>
      <Dialog.Content className='flex flex-col gap-2 n-body-medium'>{children}</Dialog.Content>
      <Dialog.Actions>
        <Button fill='outlined' onClick={onCancel} isDisabled={loading}>
          キャンセル
        </Button>
        <Button color={isDanger ? 'danger' : 'primary'} onClick={onConfirm} isLoading={loading}>
          {confirmLabel}
        </Button>
      </Dialog.Actions>
    </Dialog>
  );
}

// M9 操作の履歴（新しい順）と取り消し
function OperationHistory({
  user,
  readOnly,
  reloadToken,
  onChanged,
}: {
  user: string;
  readOnly: boolean;
  reloadToken: number;
  onChanged: () => void;
}) {
  const [rows, setRows] = useState<GraphEditHistoryRow[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [hasMore, setHasMore] = useState<boolean>(false);
  const [errorText, setErrorText] = useState<string>('');
  const [confirmRow, setConfirmRow] = useState<GraphEditHistoryRow | null>(null);
  const [undoing, setUndoing] = useState<boolean>(false);

  const load = useCallback(async (current: GraphEditHistoryRow[]) => {
    setLoading(true);
    setErrorText('');
    try {
      const beforeSeq = current.length > 0 ? Math.min(...current.map((row) => row.seq)) : undefined;
      const page = (await getEditHistory({ limit: PAGE_SIZE, before_seq: beforeSeq })) ?? [];
      setRows([...current, ...page]);
      setHasMore(page.length === PAGE_SIZE);
    } catch (error) {
      setErrorText(describeGraphEditError(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load([]);
  }, [reloadToken, load]);

  const handleUndo = async () => {
    if (confirmRow === null) {
      return;
    }
    setUndoing(true);
    try {
      const result = await undoEditOperation({ op_id: confirmRow.op_id, user });
      showSuccessToast(`取り消しました（復元したエンティティ ${(result.restored_ids ?? []).length} 件）。`);
      onChanged();
    } catch (error) {
      showErrorToast(describeGraphEditError(error));
    } finally {
      setUndoing(false);
      setConfirmRow(null);
      await load([]);
    }
  };

  const columns = useMemo<ColumnDef<GraphEditHistoryRow, any>[]>(
    () => [
      { id: 'seq', header: () => <span>番号</span>, cell: ({ row }) => row.original.seq, size: 70 },
      {
        id: 'at',
        header: () => <span>日時</span>,
        cell: ({ row }) => <span className='textellipsis'>{formatGraphEditTime(row.original.at)}</span>,
        size: 190,
      },
      { id: 'user', header: () => <span>操作者</span>, cell: ({ row }) => row.original.user, size: 110 },
      { id: 'op', header: () => <span>操作</span>, cell: ({ row }) => opLabel(row.original.op), size: 130 },
      {
        id: 'summary',
        header: () => <span>内容</span>,
        cell: ({ row }) => (
          <span className='whitespace-normal' title={row.original.summary}>
            {row.original.summary}
          </span>
        ),
        size: 320,
      },
      {
        id: 'state',
        header: () => <span>状態</span>,
        cell: ({ row }) => <HistoryState row={row.original} />,
        size: 180,
      },
      {
        id: 'action',
        header: () => <span>取り消し</span>,
        cell: ({ row }) => {
          return readOnly ? null : (
            <Button
              size='small'
              fill='outlined'
              className='whitespace-nowrap'
              isDisabled={row.original.undone || !row.original.undoable}
              onClick={() => setConfirmRow(row.original)}
            >
              取り消す
            </Button>
          );
        },
        size: 130,
      },
    ],
    [readOnly]
  );

  return (
    <section className='flex flex-col gap-2' data-testid='graph-edit-history'>
      <Flex flexDirection='row' justifyContent='space-between' alignItems='center'>
        <Typography variant='subheading-large'>操作の履歴</Typography>
        <Button size='small' fill='outlined' onClick={() => load([])} isDisabled={loading}>
          再読込
        </Button>
      </Flex>
      <Typography variant='body-medium'>
        画面で行った修正の記録（新しい順）。取り消すと、操作前の複製からエンティティを復元し、修正台帳も元に戻す。後続の操作で同じエンティティが変更された操作は取り消せない。
      </Typography>
      {errorText && <Banner type='danger' usage='inline' description={errorText} />}
      <LedgerGrid data={rows} columns={columns} isLoading={loading} rowId={(row) => row.op_id} />
      {hasMore && (
        <Flex flexDirection='row' justifyContent='flex-end'>
          <Button size='small' fill='text' onClick={() => load(rows)} isDisabled={loading}>
            さらに古い履歴を読み込む
          </Button>
        </Flex>
      )}
      {confirmRow !== null && (
        <ConfirmDialog
          open={true}
          title='操作の取り消し'
          confirmLabel='取り消す'
          loading={undoing}
          onConfirm={handleUndo}
          onCancel={() => setConfirmRow(null)}
        >
          <div>次の操作を取り消します。</div>
          <div className='font-bold'>
            {opLabel(confirmRow.op)}: {confirmRow.summary}
          </div>
          <div className='n-body-small n-text-palette-neutral-text-weak'>取り消しも操作の履歴に記録されます。</div>
        </ConfirmDialog>
      )}
    </section>
  );
}

// M10 修正台帳の行の一覧（種類で絞り込み、1 行ずつ削除）
function LedgerRows({
  user,
  readOnly,
  reloadToken,
  onChanged,
}: {
  user: string;
  readOnly: boolean;
  reloadToken: number;
  onChanged: () => void;
}) {
  const [kind, setKind] = useState<string>('');
  const [rows, setRows] = useState<GraphEditLedgerRow[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [hasMore, setHasMore] = useState<boolean>(false);
  const [errorText, setErrorText] = useState<string>('');
  const [confirmRow, setConfirmRow] = useState<GraphEditLedgerRow | null>(null);
  const [deleting, setDeleting] = useState<boolean>(false);

  const load = useCallback(
    async (current: GraphEditLedgerRow[]) => {
      setLoading(true);
      setErrorText('');
      try {
        const page =
          (await getLedgerRows({ kind: kind === '' ? undefined : kind, limit: PAGE_SIZE, offset: current.length })) ??
          [];
        setRows([...current, ...page]);
        setHasMore(page.length === PAGE_SIZE);
      } catch (error) {
        setErrorText(describeGraphEditError(error));
      } finally {
        setLoading(false);
      }
    },
    [kind]
  );

  useEffect(() => {
    load([]);
  }, [reloadToken, load]);

  const handleDelete = async () => {
    if (confirmRow === null) {
      return;
    }
    setDeleting(true);
    try {
      await deleteLedgerRow({ uid: confirmRow.uid, user });
      showSuccessToast(`修正台帳の行（${kindLabel(confirmRow.kind)}）を削除しました。`);
      onChanged();
    } catch (error) {
      showErrorToast(describeGraphEditError(error));
    } finally {
      setDeleting(false);
      setConfirmRow(null);
      await load([]);
    }
  };

  const columns = useMemo<ColumnDef<GraphEditLedgerRow, any>[]>(
    () => [
      { id: 'kind', header: () => <span>種類</span>, cell: ({ row }) => kindLabel(row.original.kind), size: 190 },
      {
        id: 'content',
        header: () => <span>内容</span>,
        cell: ({ row }) => {
          const text = describeLedgerRow(row.original);
          return (
            <span className='whitespace-normal' title={text}>
              {text}
            </span>
          );
        },
        size: 400,
      },
      {
        id: 'created_at',
        header: () => <span>作成日時</span>,
        cell: ({ row }) => <span className='textellipsis'>{formatGraphEditTime(row.original.created_at)}</span>,
        size: 190,
      },
      {
        id: 'action',
        header: () => <span>操作</span>,
        cell: ({ row }) => {
          return readOnly ? null : (
            <Button
              size='small'
              fill='outlined'
              color='danger'
              className='whitespace-nowrap'
              onClick={() => setConfirmRow(row.original)}
            >
              削除
            </Button>
          );
        },
        size: 110,
      },
    ],
    [readOnly]
  );

  const selectedKind = KIND_OPTIONS.find((option) => option.value === kind) ?? KIND_OPTIONS[0];

  return (
    <section className='flex flex-col gap-2' data-testid='graph-edit-ledger'>
      <Flex flexDirection='row' justifyContent='space-between' alignItems='center'>
        <Typography variant='subheading-large'>修正台帳の行</Typography>
        <Button size='small' fill='outlined' onClick={() => load([])} isDisabled={loading}>
          再読込
        </Button>
      </Flex>
      <Typography variant='body-medium'>
        取り込みのたびに再適用される記録（別名・削除の記録・ラベルの固定・修正したプロパティ・追加したリレーション・非同一指定）。行を削除すると、次回の取り込みから再適用されない。行を削除しても、統合・削除・ラベルの変更などの修正結果は元に戻らない。修正前の状態に戻す場合は、上の「操作の履歴」で取り消す。
      </Typography>
      <div className='w-[320px]'>
        <Select
          type='select'
          label='種類で絞り込み'
          size='small'
          isFluid={true}
          selectProps={{
            options: KIND_OPTIONS,
            value: selectedKind,
            onChange: (option) => setKind(option?.value ?? ''),
            menuPosition: 'fixed',
            isClearable: false,
          }}
        />
      </div>
      {errorText && <Banner type='danger' usage='inline' description={errorText} />}
      <LedgerGrid data={rows} columns={columns} isLoading={loading} rowId={(row) => row.uid} />
      {hasMore && (
        <Flex flexDirection='row' justifyContent='flex-end'>
          <Button size='small' fill='text' onClick={() => load(rows)} isDisabled={loading}>
            さらに読み込む
          </Button>
        </Flex>
      )}
      {confirmRow !== null && (
        <ConfirmDialog
          open={true}
          title='修正台帳の行の削除'
          confirmLabel='削除する'
          isDanger={true}
          loading={deleting}
          onConfirm={handleDelete}
          onCancel={() => setConfirmRow(null)}
        >
          <div>次の行を削除します。</div>
          <div className='font-bold'>
            {kindLabel(confirmRow.kind)}：{describeLedgerRow(confirmRow)}
          </div>
          <div className='n-body-small n-text-palette-neutral-text-weak'>
            削除の記録を削除すると、次回の取り込みで同じエンティティまたはリレーションが再作成される場合がある。別名を削除すると、旧名称は読み替えられなくなる。
          </div>
        </ConfirmDialog>
      )}
    </section>
  );
}

// グラフの設定の新しいタブ「修正台帳」
export default function GraphEditLedgerTab() {
  const { userCredentials, isReadOnlyUser } = useCredentials();
  const user = graphEditUser(userCredentials);
  const [historyToken, setHistoryToken] = useState<number>(0);
  const [ledgerToken, setLedgerToken] = useState<number>(0);
  return (
    <div className='flex flex-col gap-8' data-testid='graph-edit-ledger-tab'>
      <OperationHistory
        user={user}
        readOnly={isReadOnlyUser}
        reloadToken={historyToken}
        onChanged={() => setLedgerToken((token) => token + 1)}
      />
      <LedgerRows
        user={user}
        readOnly={isReadOnlyUser}
        reloadToken={ledgerToken}
        onChanged={() => setHistoryToken((token) => token + 1)}
      />
    </div>
  );
}
