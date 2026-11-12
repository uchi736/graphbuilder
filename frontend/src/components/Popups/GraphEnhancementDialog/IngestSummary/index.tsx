import { Typography } from '@neo4j-ndl/react';
import { Fragment, ReactNode, useEffect, useState } from 'react';
import { useCredentials } from '../../../../context/UserCredentials';
import { getSettingsOverview } from '../../../../services/SettingsOverview';
import ExtractionMethodSelect from '../../../ExtractionMethodSelect';

// 抽出の設定（グラフ設定の「取り込み」タブの先頭）。抽出方式はこの欄で選択する。
// PDF の分割と参照リンクの生成方式は backend の設定で決定されるため、参照のみとする
const CHUNK_STRATEGY_LABELS: Record<string, string> = {
  markdown: 'Markdown に変換し、見出しと段落で分割',
  token: '固定トークン数で分割（上流版の既定）',
};
const REFERENCE_MODE_LABELS: Record<string, string> = {
  llm: 'LLM のみ（既定）',
  hybrid: '規則で解決できる参照は規則、残りは LLM',
  rule: '規則のみ（LLM を使用しない）',
};

export default function IngestSummary() {
  const { userCredentials } = useCredentials();
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  // 設定が反映されない組み合わせの注記（例: MARKDOWN_CHUNKING が false の場合、PDF_CHUNK_STRATEGY=markdown は反映されない）
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState<boolean>(false);

  useEffect(() => {
    // POST /settings_overview は資格情報を送るため、接続するまでは呼び出さない
    if (userCredentials == null) {
      return;
    }
    let alive = true;
    getSettingsOverview()
      .then((res) => {
        if (res.status !== 'Success' || !res.data) {
          throw new Error(res.error ?? res.message ?? '');
        }
        // 設定は名前（name）と現在値（value）の一覧で返る。未設定の項目は既定値が value に入る
        const values: Record<string, unknown> = Object.fromEntries(res.data.settings.map((s) => [s.name, s.value]));
        const noteOf: Record<string, string> = {};
        res.data.settings.forEach((s) => {
          if (s.note) {
            noteOf[s.name] = s.note;
          }
        });
        if (alive) {
          setSettings(values);
          setNotes(noteOf);
          setFailed(false);
        }
      })
      .catch(() => {
        if (alive) {
          setFailed(true);
        }
      });
    return () => {
      alive = false;
    };
  }, [userCredentials]);

  // backend の設定の表示。値が空の場合は backend の既定（PDF_CHUNK_STRATEGY は token、CHUNK_REFERENCE_MODE は llm）とする。
  // 説明の後に、環境変数の名前と値を補足として表示する
  const fromBackend = (name: string, labels: Record<string, string>, fallback: string): ReactNode => {
    if (failed) {
      return '取得できない（バックエンドの再起動と接続の後に表示される）';
    }
    if (userCredentials == null) {
      return 'データベースに接続すると表示される';
    }
    if (settings === null) {
      return '取得中…';
    }
    const value = String(settings[name] || fallback)
      .trim()
      .toLowerCase();
    return (
      <>
        {labels[value] ?? value}
        <span className='ml-3 n-text-palette-neutral-text-weak'>{`${name}=${value}`}</span>
        {notes[name] ? `。注記：${notes[name]}` : ''}
      </>
    );
  };
  const rows: [string, ReactNode][] = [
    ['抽出方式', <ExtractionMethodSelect key='method' />],
    ['PDF の分割', fromBackend('PDF_CHUNK_STRATEGY', CHUNK_STRATEGY_LABELS, 'token')],
    ['参照リンクの生成方式', fromBackend('CHUNK_REFERENCE_MODE', REFERENCE_MODE_LABELS, 'llm')],
  ];
  return (
    <section className='flex flex-col gap-2'>
      <Typography variant='subheading-medium'>抽出の設定</Typography>
      <div className='grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 items-baseline'>
        {rows.map(([name, value]) => (
          <Fragment key={name}>
            <Typography variant='body-medium' className='font-semibold!'>
              {name}
            </Typography>
            <Typography variant='body-medium' as='div'>
              {value}
            </Typography>
          </Fragment>
        ))}
      </div>
      <Typography variant='body-small'>
        {`標準は、メイン画面下部で選択した LLM がチャンクから直接抽出する方式である。EDC（Extract, Define, Canonicalize）は、文書種別ごとに定義した語彙に沿って抽出する方式であり、グラフスキーマは使用しない。EDC の LLM は EDC 側の設定で決定される。`}
        抽出方式の選択はブラウザに保存され、以後の取り込みに適用される。「続きから」の再処理には、各文書の前回の抽出方式が適用される。
      </Typography>
      <Typography variant='body-small'>
        PDF の分割と参照リンクの生成方式はバックエンドの設定で決定され、この画面では変更できない。
      </Typography>
    </section>
  );
}
