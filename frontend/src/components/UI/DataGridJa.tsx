import { useMemo } from 'react';
import { DataGridComponents, LoadingSpinner, Select, Typography } from '@neo4j-ndl/react';
import { Table } from '@tanstack/react-table';

// NDL の DataGrid の既定の英語（件数の表示・表示件数の選択・空表示）を置き換える部品

// 1 ページの表示件数の選択肢（NDL の既定と同じ値。現在の表示件数も加える）
const PAGE_SIZE_VALUES = [10, 25, 50, 100];

// 件数の表示。数え方は NDL の既定と同じ（読み込み済みの行数。一覧に上限がある表や追加読み込みの表では総数と異なるため「全」は付けない）
export function DataGridJaResults<T>({ table }: { table: Table<T> }) {
  const { pageIndex, pageSize } = table.getState().pagination;
  const total = table.getCoreRowModel().rows.length;
  const from = pageIndex * pageSize + 1;
  const to = pageIndex * pageSize + table.getRowModel().rows.length;
  let text = `${total} 件`;
  if (total > 0 && to > from) {
    text = `${total} 件中 ${from}〜${to} 件を表示`;
  } else if (total > 0 && to === from) {
    text = `${total} 件中 ${from} 件目を表示`;
  }
  return <DataGridComponents.TableResults>{text}</DataGridComponents.TableResults>;
}

// 1 ページの表示件数の選択。children を渡すと既定の「Show」と選択欄が置き換わる
export function DataGridJaRowsPerPage<T>({ table }: { table: Table<T> }) {
  const { pageSize } = table.getState().pagination;
  const options = Array.from(new Set([...PAGE_SIZE_VALUES, pageSize]))
    .sort((a, b) => a - b)
    .map((value) => ({ value, label: `${value}` }));
  return (
    <DataGridComponents.RowsPerPage>
      <span className='whitespace-nowrap'>表示件数</span>
      <Select
        type='select'
        size='medium'
        selectProps={{
          'aria-label': '1 ページの表示件数',
          options,
          value: options.find((option) => option.value === pageSize),
          isSearchable: false,
          menuPosition: 'fixed',
          onChange: (option) => {
            if (option) {
              table.setPageSize(option.value);
            }
          },
          styles: { container: () => ({ width: '100%' }) },
        }}
      />
    </DataGridComponents.RowsPerPage>
  );
}

// 空表示
export function DataGridJaNoData() {
  return (
    <DataGridComponents.NoDataPlaceholder>
      <DataGridComponents.NoDataPlaceholderContentWrapper>
        <DataGridComponents.NoDataIcon />
        <Typography variant='subheading-small'>表示するデータがない</Typography>
      </DataGridComponents.NoDataPlaceholderContentWrapper>
    </DataGridComponents.NoDataPlaceholder>
  );
}

// 読み込み中の表示（NDL の既定と同じ構成で文言だけを置き換える）
export function DataGridJaLoading() {
  return (
    <DataGridComponents.LoadingPlaceholder>
      <div role='cell' className='ndl-data-grid-placeholder'>
        <div className='ndl-data-grid-loading-placeholder'>
          <LoadingSpinner />
          <h6>データを読み込み中</h6>
        </div>
      </div>
    </DataGridComponents.LoadingPlaceholder>
  );
}

// DataGrid の components に展開する部品。table ごとに同じ参照を返し、親が再描画されても選択欄の状態を保つ
export function useDataGridJa<T>(table: Table<T>) {
  return useMemo(
    () => ({
      TableResults: () => <DataGridJaResults table={table} />,
      RowsPerPage: () => <DataGridJaRowsPerPage table={table} />,
      NoDataPlaceholder: DataGridJaNoData,
      LoadingPlaceholder: DataGridJaLoading,
    }),
    [table]
  );
}
