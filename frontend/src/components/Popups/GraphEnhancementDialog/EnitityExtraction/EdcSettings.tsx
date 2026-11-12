import { Radio, Typography } from '@neo4j-ndl/react';
import { Fragment, ReactNode } from 'react';
import { useFileContext } from '../../../../context/UsersFiles';
import { useExtractionMethods } from '../../../../hooks/useExtractionMethods';
import { EdcSettings as EdcSettingsValues, ExtractionDoctype } from '../../../../services/ExtractionMethods';
import {
  EXTRACTION_METHOD_EDC,
  EdcExtractionOptions,
  EdcSchemaMode,
  saveEdcOptions,
} from '../../../../utils/ExtractionMethodUtils';

// EDC の設定（グラフ設定の「取り込み」タブ、抽出方式が EDC の場合に表示する）。backend の現在の設定値を参照のみで表示する。
// EDC を使用できない間は、抽出方式の選択欄の下に「EDC の設定（参照）」として折りたたんで表示する（reference）。
// 「初期スキーマ」の欄で、文書種別と運用モードを取り込みごとに指定できる（指定はブラウザに保存し、/extract に edc_doctype・edc_schema_mode として送る。
// 空は backend の設定に従う）。語彙そのものは EDC の定義ファイルで決まり、この画面では変えない
const NOT_AVAILABLE = '取得できない（バックエンドの再起動後に表示される）';
// 表の罫線は、テーマの色を参照するインラインのスタイルで指定する（border のユーティリティクラスが表のセルに反映されないことを画面で確認したため）
const cellStyle = { border: '1px solid rgb(var(--theme-palette-neutral-border-weak))' };

const doctypeText = (settings: EdcSettingsValues, doctypes: ExtractionDoctype[]): string => {
  switch (settings.doctype_mode) {
    case 'auto':
      return '自動判定（文書ごとに文書種別を判定し、その文書種別の語彙で抽出）';
    case 'none':
      return '判定なし（語彙を限定せずに抽出）';
    case 'fixed':
      return doctypes.length > 0 && !doctypes.some((d) => d.name === settings.doctype)
        ? `${settings.doctype}（固定。未登録の文書種別のため、語彙を限定せずに抽出）`
        : `${settings.doctype}（固定）`;
    default:
      return 'LLM_MODEL_CONFIG_EDC が未設定';
  }
};

const schemaModeText = (settings: EdcSettingsValues): string => {
  if (settings.schema_mode === 'fixed_extended') {
    return '固定＋拡張（語彙にない関係も追加）';
  }
  if (settings.schema_mode === 'fixed') {
    return '固定スキーマ（語彙にない関係は破棄し、提案として記録）';
  }
  return NOT_AVAILABLE;
};

const unknownRelationsText = (settings: EdcSettingsValues): string => {
  if (settings.unknown_relations === 'fold') {
    return 'RELATED_TO に集約（元の名称はプロパティに保持）';
  }
  if (settings.unknown_relations === 'keep') {
    return '語彙にない名称のまま追加';
  }
  return NOT_AVAILABLE;
};

const flagText = (value: boolean | null): string | null => (value === null ? null : String(value));

const dropSentenceText = (value: boolean | null): string => {
  if (value === null) {
    return NOT_AVAILABLE;
  }
  return value ? '有効（主語または目的語が 15 字以上で読点または句点を含むトリプルを除外）' : '無効';
};

// 値と、その値を決定する環境変数（補足）。環境変数の値を判定できない場合は補足を表示しない
const valueWithSource = (text: string, name: string, value: string | null, note = ''): ReactNode => (
  <>
    {text}
    {value !== null && <span className='ml-3 n-text-palette-neutral-text-weak'>{`${name}=${value}${note}`}</span>}
  </>
);

const countText = (value: number | null | undefined): string => (typeof value === 'number' ? String(value) : '—');

// reference: 参照用の表示（EDC を使用できない間、折りたたみの中に表示する）。見出しと、グラフスキーマを表示しない旨の説明を省く
export default function EdcSettings({
  hasGraphSchema,
  reference = false,
}: {
  hasGraphSchema: boolean;
  reference?: boolean;
}) {
  const { methods, loaded } = useExtractionMethods();
  const { edcOptions, setEdcOptions } = useFileContext();
  const edc = methods.find((m) => m.id === EXTRACTION_METHOD_EDC);
  const settings = edc?.settings ?? null;
  const updateOptions = (patch: Partial<EdcExtractionOptions>) => {
    const next = { ...edcOptions, ...patch };
    setEdcOptions(next);
    saveEdcOptions(next);
  };
  // 取り込みで実際に使う文書種別の見出し（表の行の強調に使う）
  const effectiveFixedDoctype =
    edcOptions.doctype !== '' && edcOptions.doctype !== 'auto' && edcOptions.doctype !== 'none'
      ? edcOptions.doctype
      : edcOptions.doctype === '' && settings?.doctype_mode === 'fixed'
        ? settings.doctype
        : null;
  const doctypes = edc?.doctypes ?? [];
  const vocabulary = edc?.vocabulary ?? null;
  const hasCounts = doctypes.some((d) => typeof d.node_label_count === 'number');

  const rows: [string, ReactNode][] = settings
    ? [
        [
          '文書種別',
          valueWithSource(doctypeText(settings, doctypes), 'LLM_MODEL_CONFIG_EDC の 2 番目の値', settings.doctype),
        ],
        [
          'スキーマ運用モード',
          valueWithSource(schemaModeText(settings), 'EDC_ENRICH_SCHEMA', flagText(settings.enrich_schema)),
        ],
        [
          '語彙にない関係の扱い',
          valueWithSource(
            unknownRelationsText(settings),
            'EDC_FOLD_UNKNOWN_RELATIONS',
            flagText(settings.fold_unknown_relations),
            settings.schema_mode === 'fixed' ? '。固定スキーマでは語彙にない関係を破棄するため適用されない' : ''
          ),
        ],
        [
          '文の形の名称の除外',
          valueWithSource(
            dropSentenceText(settings.drop_sentence_names),
            'EDC_DROP_SENTENCE_NAMES',
            flagText(settings.drop_sentence_names)
          ),
        ],
      ]
    : [];

  return (
    <section className='flex flex-col gap-3'>
      {!reference && <Typography variant='subheading-medium'>EDC の設定</Typography>}
      {!reference && (
        <Typography variant='body-small'>
          以下はバックエンドの設定値（既定）である。取り込みごとに変える場合は「初期スキーマ」で指定する。
        </Typography>
      )}
      {!loaded ? (
        <Typography variant='body-medium'>取得中…</Typography>
      ) : settings === null ? (
        <Typography variant='body-medium'>{`設定値：${NOT_AVAILABLE}`}</Typography>
      ) : (
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
      )}
      {!reference && (
        <div className='flex flex-col gap-2' data-testid='edc-initial-schema'>
          <Typography variant='body-medium' className='font-semibold!'>
            初期スキーマ（この取り込みでの指定）
          </Typography>
          <div className='grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 items-baseline'>
            <Typography variant='body-medium'>文書種別</Typography>
            <div
              className='flex flex-row flex-wrap items-center gap-x-5 gap-y-1'
              role='radiogroup'
              aria-label='初期スキーマの文書種別'
            >
              <Radio
                label={`バックエンドの設定に従う（${doctypeText(settings ?? ({} as EdcSettingsValues), doctypes)}）`}
                isChecked={edcOptions.doctype === ''}
                onChange={() => updateOptions({ doctype: '' })}
                htmlAttributes={{ name: 'edc-doctype', value: '' }}
              />
              <Radio
                label='自動判定（文書ごとに判定）'
                isChecked={edcOptions.doctype === 'auto'}
                onChange={() => updateOptions({ doctype: 'auto' })}
                htmlAttributes={{ name: 'edc-doctype', value: 'auto' }}
              />
              <Radio
                label='文書種別なし（語彙を限定せずに発見）'
                isChecked={edcOptions.doctype === 'none'}
                onChange={() => updateOptions({ doctype: 'none' })}
                htmlAttributes={{ name: 'edc-doctype', value: 'none' }}
              />
              {doctypes.map((d) => (
                <Radio
                  key={d.name}
                  label={d.name}
                  isChecked={edcOptions.doctype === d.name}
                  onChange={() => updateOptions({ doctype: d.name })}
                  htmlAttributes={{ name: 'edc-doctype', value: d.name, title: d.description || undefined }}
                />
              ))}
            </div>
            <Typography variant='body-medium'>運用モード</Typography>
            <div
              className='flex flex-row flex-wrap items-center gap-x-5 gap-y-1'
              role='radiogroup'
              aria-label='初期スキーマの運用モード'
            >
              <Radio
                label={`バックエンドの設定に従う（${settings ? schemaModeText(settings) : NOT_AVAILABLE}）`}
                isChecked={edcOptions.schemaMode === ''}
                onChange={() => updateOptions({ schemaMode: '' })}
                htmlAttributes={{ name: 'edc-schema-mode', value: '' }}
              />
              {(
                [
                  ['fixed', '固定（語彙にない関係は破棄）'],
                  ['fixed_extended', '固定＋拡張（語彙にない関係も追加）'],
                ] as [EdcSchemaMode, string][]
              ).map(([value, label]) => (
                <Radio
                  key={value}
                  label={label}
                  isChecked={edcOptions.schemaMode === value}
                  onChange={() => updateOptions({ schemaMode: value })}
                  htmlAttributes={{ name: 'edc-schema-mode', value }}
                />
              ))}
            </div>
          </div>
          <Typography variant='body-small'>
            指定はこのブラウザに保存され、EDC
            での以後の取り込みに適用される。文書種別の語彙（ノードラベルとリレーションタイプの定義）は EDC
            の定義ファイルで決まり、この画面では変更できない。「続きから」の再処理では、前回と同じ指定を用いること。
          </Typography>
        </div>
      )}
      <div className='flex flex-col gap-1'>
        <Typography variant='body-medium' className='font-semibold!'>
          文書種別ごとの語彙
        </Typography>
        {doctypes.length === 0 ? (
          <Typography variant='body-medium'>
            {!loaded
              ? '取得中…'
              : `文書種別の一覧：${settings ? '取得できない（EDC と定義ファイルのいずれからも取得できない）' : NOT_AVAILABLE}`}
          </Typography>
        ) : (
          <table className='n-body-medium border-collapse w-fit'>
            <thead>
              <tr>
                <th className='px-3 py-1 text-left' style={cellStyle}>
                  文書種別
                </th>
                <th className='px-3 py-1 text-right' style={cellStyle}>
                  ノードラベル数
                </th>
                <th className='px-3 py-1 text-right' style={cellStyle}>
                  リレーションタイプ数
                </th>
              </tr>
            </thead>
            <tbody>
              {doctypes.map((d) => (
                <tr
                  key={d.name}
                  title={d.description || undefined}
                  className={effectiveFixedDoctype === d.name ? 'n-bg-palette-primary-bg-weak' : undefined}
                >
                  <td className='px-3 py-1' style={cellStyle}>
                    {d.name}
                  </td>
                  <td className='px-3 py-1 text-right' style={cellStyle}>
                    {countText(d.node_label_count)}
                  </td>
                  <td className='px-3 py-1 text-right' style={cellStyle}>
                    {countText(d.relationship_type_count)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className='flex flex-col gap-1'>
        {doctypes.length > 0 &&
          (hasCounts && vocabulary?.available ? (
            <>
              <Typography variant='body-small'>
                {`件数は、共通の語彙（ノードラベル ${vocabulary.backbone_node_label_count} 種類、リレーションタイプ ${vocabulary.backbone_relationship_type_count} 種類）と文書種別ごとの語彙の和集合を EDC の定義ファイルから集計した値である。`}
              </Typography>
              <Typography variant='body-small'>文書種別の説明は行にポインタを合わせると表示される。</Typography>
            </>
          ) : (
            <Typography variant='body-small'>{`件数：${vocabulary?.error ?? NOT_AVAILABLE}`}</Typography>
          ))}
        {edc?.doctypes_source === 'registry' && (
          <Typography variant='body-small'>
            EDC から文書種別の一覧を取得できないため、定義ファイルから表示している。
          </Typography>
        )}
        <Typography variant='body-small'>
          設定値はバックエンドの環境変数（ルートの
          .env）で決定され、この画面では変更できない。変更はバックエンドのコンテナの再作成後に反映される。
        </Typography>
        {!reference && (
          <Typography variant='body-small'>
            グラフスキーマ（標準抽出で使用）とエンティティ抽出の追加指示は EDC
            では使用しないため、グラフスキーマはこの状態では表示しない。
            {hasGraphSchema ? '設定済みのグラフスキーマは保持され、抽出方式を標準に変更すると再び適用される。' : ''}
          </Typography>
        )}
      </div>
    </section>
  );
}
