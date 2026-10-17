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

export default function RetrievalSettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [specs, setSpecs] = useState<SettingSpec[]>([]);
  const [values, setValues] = useState<RetrievalSettings>({});
  const [message, setMessage] = useState<string>('');
  const [error, setError] = useState<string>('');

  useEffect(() => {
    if (!open) {
      return;
    }
    setMessage('');
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
  }, [open]);

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

  const invalidKeys = useMemo(() => specs.filter((s) => isInvalid(s, values[s.key])).map((s) => s.key), [specs, values]);

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

  const rangeText = (s: SettingSpec) =>
    isNumeric(s) && s.min !== null && s.max !== null ? `範囲 ${s.min}〜${s.max}。` : '';

  return (
    <Dialog isOpen={open} onClose={onClose} size='large'>
      <Dialog.Header>検索の詳細設定</Dialog.Header>
      <Dialog.Description>
        チャットの検索で使う制限値を変更します。設定はこのブラウザに保存され、次の質問から適用されます。
      </Dialog.Description>
      <Dialog.Content>
        {error && <Typography variant='body-medium'>{error}</Typography>}
        <div className='flex flex-col gap-6'>
          {groups.map((g) => (
            <section key={g.name} className='flex flex-col gap-3'>
              <Typography variant='subheading-medium'>{g.name}</Typography>
              {g.items.map((s) => {
                const help = `${s.help} ${rangeText(s)}既定値 ${String(s.default)}。`;
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
                      <Typography variant='body-small'>{help}</Typography>
                    </div>
                  );
                }
                if (s.type === 'choice') {
                  return (
                    <div key={s.key} className='flex flex-col gap-1'>
                      <label htmlFor={`retrieval-${s.key}`}>
                        <Typography variant='body-medium'>{s.label}</Typography>
                      </label>
                      <select
                        id={`retrieval-${s.key}`}
                        className='n-border n-border-palette-neutral-border-strong n-rounded-md px-2 py-1'
                        value={String(values[s.key] ?? s.default)}
                        onChange={(e) => update(s.key, e.target.value)}
                      >
                        {(s.choices ?? []).map((c) => (
                          <option key={c.value} value={c.value}>
                            {c.label}
                          </option>
                        ))}
                      </select>
                      <Typography variant='body-small'>{help}</Typography>
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
        {message && (
          <div className='mt-4'>
            <Typography variant='body-medium'>{message}</Typography>
          </div>
        )}
      </Dialog.Content>
      <Dialog.Actions>
        <Button fill='outlined' color='neutral' onClick={handleReset} isDisabled={specs.length === 0}>
          既定値に戻す
        </Button>
        <Button fill='outlined' color='neutral' onClick={onClose}>
          閉じる
        </Button>
        <Button onClick={handleSave} isDisabled={specs.length === 0 || invalidKeys.length > 0}>
          保存
        </Button>
      </Dialog.Actions>
    </Dialog>
  );
}
