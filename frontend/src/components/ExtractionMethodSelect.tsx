import { Button, Radio, Typography } from '@neo4j-ndl/react';
import { memo } from 'react';
import { useFileContext } from '../context/UsersFiles';
import { ExtractionMethodOption } from '../services/ExtractionMethods';
import { useExtractionMethodGuard } from '../hooks/useExtractionMethods';
import ButtonWithToolTip from './UI/ButtonWithToolTip';
import EdcSettings from './Popups/GraphEnhancementDialog/EnitityExtraction/EdcSettings';
import {
  EXTRACTION_METHOD_EDC,
  EXTRACTION_METHOD_LABELS,
  EXTRACTION_METHOD_STANDARD,
  saveExtractionMethod,
  withMethodDetail,
} from '../utils/ExtractionMethodUtils';

// 選択肢の表示名。標準は抽出に使用する LLM（メイン画面の LLM の選択）、EDC は EDC 側の LLM を補足として付与する
const optionLabel = (method: ExtractionMethodOption, model: string): string => {
  if (method.id === EXTRACTION_METHOD_STANDARD) {
    return withMethodDetail(method.label, `LLM：${model}`);
  }
  if (method.id === EXTRACTION_METHOD_EDC && method.available) {
    return withMethodDetail(method.label, `LLM：${method.llm ?? '不明'}`);
  }
  return method.label;
};

// 抽出方式の選択（グラフ設定の「取り込み」タブの「抽出の設定」）。標準の LLM 抽出か EDC かを選択する。
// 選択は LLM の選択とは別に保持し、ブラウザに保存する。EDC の LLM は EDC 側の設定で決定されるため、表示のみとする。
// EDC を使用できない間は、選択欄の下に EDC の設定を「EDC の設定（参照）」として折りたたんで表示する（既定は閉じた状態）
const ExtractionMethodSelect: React.FC = () => {
  const { extractionMethod, setExtractionMethod, model } = useFileContext();
  const { methods, failed, refresh } = useExtractionMethodGuard();
  const edc = methods.find((method) => method.id === EXTRACTION_METHOD_EDC);

  const handleChange = (id: string) => {
    setExtractionMethod(id);
    saveExtractionMethod(id);
  };

  return (
    <div className='flex flex-col gap-1'>
      <div className='flex flex-row flex-wrap items-center gap-x-6 gap-y-1' role='radiogroup' aria-label='抽出方式'>
        {methods.map((method) => (
          <div key={method.id} className='flex flex-row flex-wrap items-center gap-2'>
            <Radio
              label={optionLabel(method, model)}
              isChecked={extractionMethod === method.id}
              isDisabled={!method.available}
              onChange={() => handleChange(method.id)}
              htmlAttributes={{
                name: 'extraction-method',
                value: method.id,
                title: method.llm_id ? `EDC 側のモデル名：${method.llm_id}` : undefined,
              }}
            />
            {!method.available && (
              <>
                <Typography
                  variant='body-small'
                  className='n-text-palette-danger-text'
                  htmlAttributes={{ title: method.reason_detail ?? undefined }}
                >
                  {`現在使用不可：${method.reason ?? 'EDC に接続できない'}`}
                </Typography>
                <Button
                  fill='text'
                  size='small'
                  onClick={() => {
                    refresh();
                  }}
                >
                  再確認
                </Button>
              </>
            )}
          </div>
        ))}
      </div>
      {edc !== undefined && !edc.available && (
        <details className='mt-1' data-testid='edc-settings-reference'>
          <summary className='cursor-pointer n-body-medium'>EDC の設定（参照）</summary>
          <div className='mt-2 ml-4'>
            <EdcSettings hasGraphSchema={false} reference={true} />
          </div>
        </details>
      )}
      {failed && (
        <Typography variant='body-small' className='n-text-palette-neutral-text-weak'>
          抽出方式の一覧を取得できないため、標準のみを表示している。
        </Typography>
      )}
    </div>
  );
};
export default memo(ExtractionMethodSelect);

// 表示名の末尾の補足（全角の括弧）を除いた短い名称。例: EDC（スキーマ統制） → EDC
const shortMethodLabel = (label: string): string => label.replace(/（[^（）]*）$/, '');

// EDC の初期スキーマの指定を下部の表示用に短くする。例: 試運転_検査基準・固定、自動判定・固定＋拡張、既定（backend の設定に従う）
export const edcOptionsShortLabel = (options: { doctype: string; schemaMode: string }): string => {
  const doctype =
    options.doctype === '' ? '既定' : options.doctype === 'auto' ? '自動判定' : options.doctype === 'none' ? '種別なし' : options.doctype;
  const mode = options.schemaMode === 'fixed' ? '固定' : options.schemaMode === 'fixed_extended' ? '固定＋拡張' : '';
  return mode ? `${doctype}・${mode}` : doctype;
};

// メイン画面の下部に表示する現在の抽出方式（参照のみ）。変更はグラフ設定の「取り込み」タブで行う。
// 押下するとグラフ設定を開く。disabledReason を渡した場合は押下できず、理由をツールチップに表示する。
// 幅 1280px でも 1 行に収まるよう短い名称を折り返さずに表示し、補足（スキーマ統制）はツールチップに表示する
const ExtractionMethodStatusView: React.FC<{
  onOpenSettings: () => void;
  disabledReason?: string;
  size?: 'small' | 'medium' | 'large';
}> = ({ onOpenSettings, disabledReason, size = 'medium' }) => {
  const { extractionMethod, edcOptions } = useFileContext();
  const { methods } = useExtractionMethodGuard();
  const label =
    methods.find((m) => m.id === extractionMethod)?.label ??
    EXTRACTION_METHOD_LABELS[extractionMethod] ??
    extractionMethod;
  const tooltip =
    disabledReason ??
    (extractionMethod === EXTRACTION_METHOD_EDC
      ? `グラフ設定の「取り込み」タブを開く（初期スキーマの変更もここで行う）。${label}の LLM は EDC 側の設定で決定される。`
      : 'グラフ設定の「取り込み」タブを開く。');
  // EDC の場合は、取り込みで送る初期スキーマ（文書種別・運用モード）を短く併記する。幅を抑えるため「（グラフ設定で変更）」はツールチップへ回す
  const edcDetail = extractionMethod === EXTRACTION_METHOD_EDC ? edcOptionsShortLabel(edcOptions) : '';
  const text =
    extractionMethod === EXTRACTION_METHOD_EDC
      ? `抽出方式：${shortMethodLabel(label)}・${edcDetail}`
      : `抽出方式：${shortMethodLabel(label)}（グラフ設定で変更）`;
  return (
    <ButtonWithToolTip
      text={tooltip}
      label='抽出方式の変更'
      placement='top'
      fill='text'
      size={size}
      onClick={onOpenSettings}
      disabled={disabledReason !== undefined}
      alwaysShowTooltip={true}
      className='whitespace-nowrap'
    >
      {text}
    </ButtonWithToolTip>
  );
};
export const ExtractionMethodStatus = memo(ExtractionMethodStatusView);
