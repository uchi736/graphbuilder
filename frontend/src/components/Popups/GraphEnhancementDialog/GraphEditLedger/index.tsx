import { Banner, Button, Flex, Select, Typography } from '@neo4j-ndl/react';
import { ColumnDef, RowData, getCoreRowModel, getPaginationRowModel, useReactTable } from '@tanstack/react-table';
import { ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { useCredentials } from '../../../../context/UserCredentials';
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
import { DataGridJaTable } from '../../../UI/DataGridJa';
import { GraphEditDialog } from '../../../Graph/GraphEdit/GraphEditDialog';

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
  return <DataGridJaTable table={table} isLoading={isLoading} hasNavigation={true} />;
}

// 1 行を選んで確認してから実行する操作（取り消し・行の削除）
interface RowAction<T> {
  title: string;
  label: string;
  color: 'primary' | 'danger';
  body: (row: T) => ReactNode;
  // 実行して、成功の通知の文を返す
  run: (row: T) => Promise<string>;
}

// 履歴と修正台帳の欄の共通の枠。PAGE_SIZE 件ずつ読み足し、操作の後は先頭から読み直す。
// fetchPage は読み込み済みの行を受け取り、その続きの 1 ページを返す
function PagedLedgerSection<T extends RowData>({
  title,
  description,
  filter,
  fetchPage,
  reloadToken,
  columns,
  rowId,
  moreLabel,
  action,
  onChanged,
}: {
  title: string;
  description: string;
  filter?: ReactNode;
  fetchPage: (current: T[]) => Promise<T[] | null | undefined>;
  reloadToken: number;
  columns: (requestAction: (row: T) => void) => ColumnDef<T, any>[];
  rowId: (row: T) => string;
  moreLabel: string;
  action: RowAction<T>;
  onChanged: () => void;
}) {
  const [rows, setRows] = useState<T[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [hasMore, setHasMore] = useState<boolean>(false);
  const [errorText, setErrorText] = useState<string>('');
  const [confirmRow, setConfirmRow] = useState<T | null>(null);
  const [running, setRunning] = useState<boolean>(false);

  const load = useCallback(
    async (current: T[]) => {
      setLoading(true);
      setErrorText('');
      try {
        const page = (await fetchPage(current)) ?? [];
        setRows([...current, ...page]);
        setHasMore(page.length === PAGE_SIZE);
      } catch (error) {
        setErrorText(describeGraphEditError(error));
      } finally {
        setLoading(false);
      }
    },
    [fetchPage]
  );

  useEffect(() => {
    load([]);
  }, [reloadToken, load]);

  const handleConfirm = async () => {
    if (confirmRow === null) {
      return;
    }
    setRunning(true);
    try {
      showSuccessToast(await action.run(confirmRow));
      onChanged();
    } catch (error) {
      showErrorToast(describeGraphEditError(error));
    } finally {
      setRunning(false);
      setConfirmRow(null);
      await load([]);
    }
  };

  const gridColumns = useMemo(() => columns(setConfirmRow), [columns]);

  return (
    <section className='flex flex-col gap-2'>
      <Flex flexDirection='row' justifyContent='space-between' alignItems='center'>
        <Typography variant='subheading-large'>{title}</Typography>
        <Button size='small' fill='outlined' onClick={() => load([])} isDisabled={loading}>
          再読込
        </Button>
      </Flex>
      <Typography variant='body-medium'>{description}</Typography>
      {filter}
      {errorText && <Banner type='danger' usage='inline' description={errorText} />}
      <LedgerGrid data={rows} columns={gridColumns} isLoading={loading} rowId={rowId} />
      {hasMore && (
        <Flex flexDirection='row' justifyContent='flex-end'>
          <Button size='small' fill='text' onClick={() => load(rows)} isDisabled={loading}>
            {moreLabel}
          </Button>
        </Flex>
      )}
      {confirmRow !== null && (
        <GraphEditDialog
          title={action.title}
          size='small'
          contentClassName='flex flex-col gap-2 n-body-medium'
          submitLabel={action.label}
          submitColor={action.color}
          submitting={running}
          onSubmit={handleConfirm}
          onClose={() => setConfirmRow(null)}
        >
          {action.body(confirmRow)}
        </GraphEditDialog>
      )}
    </section>
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
  const fetchPage = useCallback((current: GraphEditHistoryRow[]) => {
    const beforeSeq = current.length > 0 ? Math.min(...current.map((row) => row.seq)) : undefined;
    return getEditHistory({ limit: PAGE_SIZE, before_seq: beforeSeq });
  }, []);

  const columns = useCallback(
    (requestAction: (row: GraphEditHistoryRow) => void): ColumnDef<GraphEditHistoryRow, any>[] => [
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
              onClick={() => requestAction(row.original)}
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
    <PagedLedgerSection
      title='操作の履歴'
      description='画面で行った修正の記録（新しい順）。取り消すと、操作前の複製からエンティティを復元し、修正台帳も元に戻す。後続の操作で同じエンティティが変更された操作は取り消せない。'
      fetchPage={fetchPage}
      reloadToken={reloadToken}
      columns={columns}
      rowId={(row) => row.op_id}
      moreLabel='さらに古い履歴を読み込む'
      onChanged={onChanged}
      action={{
        title: '操作の取り消し',
        label: '取り消す',
        color: 'primary',
        body: (row) => (
          <>
            <div>次の操作を取り消します。</div>
            <div className='font-bold'>
              {opLabel(row.op)}: {row.summary}
            </div>
            <div className='n-body-small n-text-palette-neutral-text-weak'>取り消しも操作の履歴に記録されます。</div>
          </>
        ),
        run: async (row) => {
          const result = await undoEditOperation({ op_id: row.op_id, user });
          return `取り消しました（復元したエンティティ ${(result.restored_ids ?? []).length} 件）。`;
        },
      }}
    />
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

  const fetchPage = useCallback(
    (current: GraphEditLedgerRow[]) =>
      getLedgerRows({ kind: kind === '' ? undefined : kind, limit: PAGE_SIZE, offset: current.length }),
    [kind]
  );

  const columns = useCallback(
    (requestAction: (row: GraphEditLedgerRow) => void): ColumnDef<GraphEditLedgerRow, any>[] => [
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
              onClick={() => requestAction(row.original)}
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
    <PagedLedgerSection
      title='修正台帳の行'
      description='取り込みのたびに再適用される記録（別名・削除の記録・ラベルの固定・修正したプロパティ・追加したリレーション・非同一指定）。行を削除すると、次回の取り込みから再適用されない。行を削除しても、統合・削除・ラベルの変更などの修正結果は元に戻らない。修正前の状態に戻す場合は、上の「操作の履歴」で取り消す。'
      filter={
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
      }
      fetchPage={fetchPage}
      reloadToken={reloadToken}
      columns={columns}
      rowId={(row) => row.uid}
      moreLabel='さらに読み込む'
      onChanged={onChanged}
      action={{
        title: '修正台帳の行の削除',
        label: '削除する',
        color: 'danger',
        body: (row) => (
          <>
            <div>次の行を削除します。</div>
            <div className='font-bold'>
              {kindLabel(row.kind)}：{describeLedgerRow(row)}
            </div>
            <div className='n-body-small n-text-palette-neutral-text-weak'>
              削除の記録を削除すると、次回の取り込みで同じエンティティまたはリレーションが再作成される場合がある。別名を削除すると、旧名称は読み替えられなくなる。
            </div>
          </>
        ),
        run: async (row) => {
          await deleteLedgerRow({ uid: row.uid, user });
          return `修正台帳の行（${kindLabel(row.kind)}）を削除しました。`;
        },
      }}
    />
  );
}

// グラフの設定の新しいタブ「修正台帳」
export default function GraphEditLedgerTab() {
  const { userCredentials, isReadOnlyUser } = useCredentials();
  const user = graphEditUser(userCredentials);
  const [historyToken, setHistoryToken] = useState<number>(0);
  const [ledgerToken, setLedgerToken] = useState<number>(0);
  return (
    <div className='flex flex-col gap-8'>
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
