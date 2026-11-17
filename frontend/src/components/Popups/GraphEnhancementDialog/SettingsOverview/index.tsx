import {
  Banner,
  Button,
  Flex,
  StatusIndicator,
  Typography,
  useMediaQuery,
} from '@neo4j-ndl/react';
import { tokens } from '@neo4j-ndl/base';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createColumnHelper, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { useCredentials } from '../../../../context/UserCredentials';
import {
  OverviewCount,
  OverviewSetting,
  SettingsOverviewData,
  getSettingsOverview,
} from '../../../../services/SettingsOverview';
import { DataGridJaTable } from '../../../UI/DataGridJa';

// 保守: 設定値と検証件数の一覧（参照のみ）。動作を制限する設定の現在値と、グラフの検証項目の件数を表示する。
// グラフ設定の「保守」タブの中の「設定値と検証件数」に配置する
const wrapStyle = { whiteSpace: 'normal', wordBreak: 'break-word' } as const;
const settingColumn = createColumnHelper<OverviewSetting>();
const countColumn = createColumnHelper<OverviewCount>();
const shown = (value: string | null) => (value === null ? '—' : value === '' ? '（空）' : value);
const formatTime = (iso: string | null) => (iso ? new Date(iso).toLocaleString('ja-JP') : '—');
const FETCH_ERROR =
  '設定値と検証件数を取得できませんでした。バックエンドが再起動済みであること（この画面はバックエンドの再起動後に利用できます）と、バックエンドに接続できることを確認してください。';
const SOURCE_NOTE =
  '設定元が「環境変数（backend/.env）」の項目はバックエンドの再起動後に、' +
  '「環境変数（docker-compose.yml・ルートの .env）」の項目はコンテナの再作成後に反映される。' +
  'backend/.env から行を削除した場合は、コンテナを再作成するまで作成時の値が保持される。';

function Verdict({ ok }: { ok: boolean | null }) {
  if (ok === null) {
    return <Typography variant='body-small'>参考</Typography>;
  }
  return (
    <Flex flexDirection='row' alignItems='center' gap='1'>
      <StatusIndicator type={ok ? 'success' : 'danger'} />
      <Typography variant='body-medium'>{ok ? 'OK' : 'NG'}</Typography>
    </Flex>
  );
}

// 「_」の後に改行位置を入れる（ゼロ幅空白はコピー時に混入するため使わない）。環境変数名の途中や閉じ括弧の前で改行しないようにする
function breakAfterUnderscore(text: string) {
  const parts = text.split('_');
  return parts.map((part, index) => (
    <Fragment key={index}>
      {part}
      {index < parts.length - 1 && (
        <>
          _<wbr />
        </>
      )}
    </Fragment>
  ));
}

function Warning({ text }: { text: string }) {
  return (
    <Flex flexDirection='row' alignItems='flex-start' gap='1'>
      <StatusIndicator type='warning' />
      <Typography variant='body-small'>{breakAfterUnderscore(text)}</Typography>
    </Flex>
  );
}

// 設定名
function SettingName({ name }: { name: string }) {
  return (
    <Typography variant='code' style={wrapStyle}>
      {breakAfterUnderscore(name)}
    </Typography>
  );
}

export default function SettingsOverview() {
  const { breakpoints } = tokens;
  const isTablet = useMediaQuery(`(min-width:${breakpoints.xs}) and (max-width: ${breakpoints.lg})`);
  const { userCredentials } = useCredentials();
  const [data, setData] = useState<SettingsOverviewData | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const ref = useRef<AbortController>();

  const fetchOverview = useCallback(async () => {
    ref.current?.abort();
    const controller = new AbortController();
    ref.current = controller;
    setIsLoading(true);
    setError('');
    try {
      const response = await getSettingsOverview(controller.signal);
      if (response.status === 'Success' && response.data) {
        setData(response.data);
      } else {
        setError(`設定値と検証件数を取得できませんでした。${response.error ?? response.message ?? ''}`);
      }
    } catch {
      if (!controller.signal.aborted) {
        setError(FETCH_ERROR);
      }
    } finally {
      if (!controller.signal.aborted) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (userCredentials != null) {
      void fetchOverview();
    }
    return () => {
      ref.current?.abort();
    };
  }, [userCredentials, fetchOverview]);

  const settingColumns = useMemo(
    () => [
      settingColumn.accessor('group', { header: () => <span>区分</span>, cell: (info) => info.getValue(), size: 120 }),
      settingColumn.accessor('name', {
        header: () => <span>設定名</span>,
        cell: (info) => <SettingName name={info.getValue()} />,
        size: 200,
      }),
      settingColumn.accessor('value', {
        header: () => <span>現在値</span>,
        cell: (info) => {
          const row = info.row.original;
          return (
            <div style={wrapStyle}>
              <div>{shown(row.value)}</div>
              {row.pending && <Warning text={`backend/.env の値：${shown(row.file_value)}（再起動後に反映される）`} />}
              {row.note && <Warning text={row.note} />}
            </div>
          );
        },
        size: 180,
      }),
      settingColumn.accessor('default', {
        header: () => <span>既定値</span>,
        cell: (info) => <div style={wrapStyle}>{shown(info.getValue())}</div>,
        size: 130,
      }),
      settingColumn.accessor('source', {
        header: () => <span>設定元</span>,
        cell: (info) => <div style={wrapStyle}>{info.getValue()}</div>,
        size: 160,
      }),
      // 列幅の合計は 1,130px（幅 1,366px の画面でも横にあふれない値）。NDL は幅を指定しない列を初回の描画の実測値で固定し、
      // 罫線の分だけあふれるため、すべての列に幅を指定する
      settingColumn.accessor('meaning', {
        header: () => <span>説明</span>,
        cell: (info) => <div style={wrapStyle}>{info.getValue()}</div>,
        size: 340,
      }),
    ],
    []
  );

  const countColumns = useMemo(
    () => [
      countColumn.accessor('group', { header: () => <span>区分</span>, cell: (info) => info.getValue(), size: 120 }),
      countColumn.accessor('label', {
        header: () => <span>項目</span>,
        cell: (info) => {
          const row = info.row.original;
          return (
            <div style={wrapStyle}>
              <div>{row.label}</div>
              {row.detail && (
                <div title={row.detail}>
                  <Typography variant='body-small'>
                    {row.detail.length > 120 ? `${row.detail.slice(0, 120)}…` : row.detail}
                  </Typography>
                </div>
              )}
            </div>
          );
        },
        size: 380,
      }),
      countColumn.accessor('value', {
        header: () => <span>件数</span>,
        cell: (info) => {
          const row = info.row.original;
          if (row.error) {
            return (
              <div title={row.error}>
                <Typography variant='body-small'>取得失敗</Typography>
              </div>
            );
          }
          return <Typography variant='body-medium'>{row.value === null ? '—' : row.value.toLocaleString()}</Typography>;
        },
        size: 110,
      }),
      countColumn.accessor('pass_line', {
        header: () => <span>基準値</span>,
        cell: (info) => <div style={wrapStyle}>{info.getValue()}</div>,
        size: 220,
      }),
      countColumn.accessor('ok', {
        header: () => <span>判定</span>,
        cell: (info) => <Verdict ok={info.getValue()} />,
        size: 90,
      }),
    ],
    []
  );

  const settingsTable = useReactTable({
    data: data?.settings ?? [],
    columns: settingColumns,
    getCoreRowModel: getCoreRowModel(),
    enableSorting: false,
  });
  const countsTable = useReactTable({
    data: data?.counts ?? [],
    columns: countColumns,
    getCoreRowModel: getCoreRowModel(),
    enableSorting: false,
  });

  const ngCount = data ? data.counts.filter((c) => c.ok === false).length : 0;
  const pendingCount = data ? data.settings.filter((s) => s.pending).length : 0;

  return (
    <Flex flexDirection='column' gap='4'>
      <Flex flexDirection='row' justifyContent='space-between' alignItems='center'>
        <Typography variant={isTablet ? 'subheading-medium' : 'subheading-large'}>
          設定値と検証件数の一覧（参照のみ）
        </Typography>
        <Button size='small' fill='outlined' onClick={fetchOverview} isLoading={isLoading}>
          再読込
        </Button>
      </Flex>
      <Typography variant={isTablet ? 'body-small' : 'body-medium'}>
        動作を制限する設定の現在値と、グラフの検証項目の件数を表示する。この画面から設定は変更できない。
      </Typography>
      {error && (
        <Banner type='danger' usage='inline'>
          {error}
        </Banner>
      )}
      {data && (
        <Typography variant='body-small'>
          {`取得日時：${formatTime(data.generated_at)}（所要 ${data.elapsed} 秒）・判定 NG ${ngCount} 件・再起動待ちの設定 ${pendingCount} 件`}
        </Typography>
      )}
      {data?.process.env_file_changed && (
        <Warning
          text={`backend/.env は作業プロセスの起動（${formatTime(data.process.started_at)}）後に変更されている（${formatTime(
            data.process.env_file_mtime
          )}）。変更はバックエンドの再起動後に反映される。`}
        />
      )}
      <Typography variant='subheading-medium'>設定値</Typography>
      <DataGridJaTable table={settingsTable} isLoading={isLoading} heightClass='max-h-[60vh]' hasNavigation={false} />
      <Typography variant='body-small'>{SOURCE_NOTE}</Typography>
      <Typography variant='subheading-medium'>検証件数</Typography>
      <DataGridJaTable table={countsTable} isLoading={isLoading} hasNavigation={false} />
    </Flex>
  );
}
