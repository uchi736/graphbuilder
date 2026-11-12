import { Button, Checkbox, Dialog, TextInput, Typography } from '@neo4j-ndl/react';
import { useEffect, useMemo, useState } from 'react';
import api from '../../API/Index';
import {
  RetrievalSettings,
  clearRetrievalSettings,
  getRetrievalSettings,
  setRetrievalSettings,
} from '../../utils/RetrievalSettingsUtils';

interface SettingChoice {
  value: string;
  label: string;
}

interface SettingSpec {
  key: string;
  type: 'int' | 'float' | 'bool' | 'choice';
  group: string;
  label: string;
  help: string;
  default: number | boolean | string;
  min: number | null;
  max: number | null;
  choices?: SettingChoice[];
}

const isNumeric = (s: SettingSpec) => s.type === 'int' || s.type === 'float';

const isInvalid = (s: SettingSpec, v: RetrievalSettings[string] | undefined) => {
  if (!isNumeric(s)) {
    return false;
  }
  if (typeof v !== 'number' || Number.isNaN(v)) {
    return true;
  }
  if (s.type === 'int' && !Number.isInteger(v)) {
    return true;
  }
  return (s.min !== null && v < s.min) || (s.max !== null && v > s.max);
};

// 既定値の表示。選択式は選択肢の表示名、オン・オフの項目は「オン」「オフ」とする
const defaultText = (s: SettingSpec) => {
  if (s.type === 'bool') {
    return s.default ? 'オン' : 'オフ';
  }
  if (s.type === 'choice') {
    return s.choices?.find((c) => c.value === String(s.default))?.label ?? String(s.default);
  }
  return String(s.default);
};

// 検索設定の中身。チャットの「検索設定」（下のダイアログ）と、グラフ設定の「検索設定」タブの両方で使う。
// 表示されるたびに設定項目を取り直す（ダイアログを閉じる・タブを移ると unmount される）。onClose を渡すと「閉じる」を出す
export function RetrievalSettingsPanel({ onClose }: { onClose?: () => void }) {
  const [specs, setSpecs] = useState<SettingSpec[]>([]);
  const [values, setValues] = useState<RetrievalSettings>({});
  const [message, setMessage] = useState<string>('');
  const [error, setError] = useState<string>('');

  useEffect(() => {
    api
      .get('/retrieval_settings')
      .then((res) => {
        const list: SettingSpec[] = res.data?.data?.settings ?? [];
        const stored = getRetrievalSettings() ?? {};
        const initial: RetrievalSettings = {};
        list.forEach((s) => {
          initial[s.key] = s.key in stored ? stored[s.key] : s.default;
        });
        setSpecs(list);
        setValues(initial);
        setError('');
      })
      .catch(() => {
        setError('設定項目を取得できませんでした。バックエンドに接続できるか確認してください。');
      });
  }, []);

  const groups = useMemo(() => {
    const grouped: { name: string; items: SettingSpec[] }[] = [];
    specs.forEach((s) => {
      const found = grouped.find((g) => g.name === s.group);
      if (found) {
        found.items.push(s);
      } else {
        grouped.push({ name: s.group, items: [s] });
      }
    });
    return grouped;
  }, [specs]);

  const invalidKeys = useMemo(
    () => specs.filter((s) => isInvalid(s, values[s.key])).map((s) => s.key),
    [specs, values]
  );

  const update = (key: string, value: RetrievalSettings[string]) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    setMessage('');
  };

  const handleSave = () => {
    const changed: RetrievalSettings = {};
    specs.forEach((s) => {
      if (values[s.key] !== s.default) {
        changed[s.key] = values[s.key];
      }
    });
    if (Object.keys(changed).length > 0) {
      setRetrievalSettings(changed);
      setMessage(`${Object.keys(changed).length} 項目を保存しました。次の質問から適用されます。`);
    } else {
      clearRetrievalSettings();
      setMessage('すべて既定値です。次の質問から既定値で検索します。');
    }
  };

  const handleReset = () => {
    const initial: RetrievalSettings = {};
    specs.forEach((s) => {
      initial[s.key] = s.default;
    });
    clearRetrievalSettings();
    setValues(initial);
    setMessage('既定値に戻しました。');
  };

  const rangeText = (s: SettingSpec) => {
    return isNumeric(s) && s.min !== null && s.max !== null ? `範囲：${s.min}〜${s.max}。` : '';
  };

  return (
    <div className='flex flex-col gap-4'>
      <Typography variant='body-medium'>
        チャットの検索で使用する制限値を変更する。設定はこのブラウザに保存され、次の質問から適用される。
      </Typography>
      {error && <Typography variant='body-medium'>{error}</Typography>}
      <div className='flex flex-col gap-6'>
        {groups.map((g) => (
          <section key={g.name} className='flex flex-col gap-3'>
            <Typography variant='subheading-medium'>{g.name}</Typography>
            {g.items.map((s) => {
              const help = `${s.help}${rangeText(s)}既定値：${defaultText(s)}。`;
              if (s.type === 'bool') {
                return (
                  <div key={s.key} className='flex flex-col gap-1'>
                    <Checkbox
                      label={s.label}
                      ariaLabel={s.label}
                      isChecked={Boolean(values[s.key])}
                      onChange={(e) => update(s.key, e.target.checked)}
                      htmlAttributes={{ id: `retrieval-${s.key}` }}
                    />
                    <Typography variant='body-small' className='n-text-palette-neutral-text-weak'>
                      {help}
                    </Typography>
                  </div>
                );
              }
              // 選択式の項目名・補足の色と欄の高さは、数値の入力欄（TextInput）に合わせる
              if (s.type === 'choice') {
                return (
                  <div key={s.key} className='flex flex-col gap-1'>
                    <label htmlFor={`retrieval-${s.key}`}>
                      <Typography variant='body-medium' className='n-text-palette-neutral-text-weak'>
                        {s.label}
                      </Typography>
                    </label>
                    <select
                      id={`retrieval-${s.key}`}
                      className='n-border n-border-palette-neutral-border-strong n-rounded-md px-2 py-1 h-9 n-body-medium'
                      value={String(values[s.key] ?? s.default)}
                      onChange={(e) => update(s.key, e.target.value)}
                    >
                      {(s.choices ?? []).map((c) => (
                        <option key={c.value} value={c.value}>
                          {c.label}
                        </option>
                      ))}
                    </select>
                    <Typography variant='body-small' className='n-text-palette-neutral-text-weak'>
                      {help}
                    </Typography>
                  </div>
                );
              }
              const invalid = invalidKeys.includes(s.key);
              return (
                <TextInput
                  key={s.key}
                  label={s.label}
                  isFluid={true}
                  value={values[s.key] === undefined || Number.isNaN(values[s.key]) ? '' : String(values[s.key])}
                  helpText={help}
                  errorText={invalid ? `値が不正です。${rangeText(s)}` : undefined}
                  htmlAttributes={{
                    id: `retrieval-${s.key}`,
                    type: 'number',
                    step: s.type === 'int' ? 1 : 0.01,
                    min: s.min ?? undefined,
                    max: s.max ?? undefined,
                    'aria-label': s.label,
                  }}
                  onChange={(e) => update(s.key, e.target.value === '' ? Number.NaN : Number(e.target.value))}
                />
              );
            })}
          </section>
        ))}
      </div>
      {message && <Typography variant='body-medium'>{message}</Typography>}
      <div className='flex flex-row justify-end gap-4'>
        <Button fill='outlined' color='neutral' onClick={handleReset} isDisabled={specs.length === 0}>
          既定値に戻す
        </Button>
        {onClose && (
          <Button fill='outlined' color='neutral' onClick={onClose}>
            閉じる
          </Button>
        )}
        <Button onClick={handleSave} isDisabled={specs.length === 0 || invalidKeys.length > 0}>
          保存
        </Button>
      </div>
    </div>
  );
}

export default function RetrievalSettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog isOpen={open} onClose={onClose} size='large'>
      <Dialog.Header>検索設定</Dialog.Header>
      <Dialog.Content>{open && <RetrievalSettingsPanel onClose={onClose} />}</Dialog.Content>
    </Dialog>
  );
}
