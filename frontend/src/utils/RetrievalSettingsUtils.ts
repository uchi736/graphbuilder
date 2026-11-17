// チャット検索の詳細設定（画面に出ていなかった制限値）をブラウザに保存する。
// 保存するのは既定値から変更した項目だけで、チャット送信時にバックエンドへ渡す。
type RetrievalSettingValue = number | boolean | string;
export type RetrievalSettings = Record<string, RetrievalSettingValue>;

const STORAGE_KEY = 'retrievalSettings';

export const getRetrievalSettings = (): RetrievalSettings | null => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as RetrievalSettings) : null;
  } catch (e) {
    console.error('Error reading retrieval settings:', e);
    return null;
  }
};

export const setRetrievalSettings = (settings: RetrievalSettings) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch (e) {
    console.error('Error saving retrieval settings:', e);
  }
};

export const clearRetrievalSettings = () => {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (e) {
    console.error('Error clearing retrieval settings:', e);
  }
};
