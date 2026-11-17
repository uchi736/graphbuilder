import api from '../API/Index';

// 保守: 今の設定と数（読むだけ）。POST /settings_overview。資格情報は API/Index のインターセプタが足す
export interface OverviewSetting {
  name: string;
  group: string;
  value: string | null;
  default: string | null;
  source: string;
  meaning: string;
  file_value: string | null;
  pending: boolean;
  note: string | null;
}

export interface OverviewCount {
  key: string;
  group: string;
  label: string;
  value: number | null;
  pass_line: string;
  ok: boolean | null;
  detail: string | null;
  error: string | null;
}

export interface SettingsOverviewData {
  settings: OverviewSetting[];
  counts: OverviewCount[];
  process: { started_at: string | null; env_file_mtime: string | null; env_file_changed: boolean };
  generated_at: string;
  elapsed: number;
}

interface SettingsOverviewResponse {
  status: string;
  data?: SettingsOverviewData;
  message?: string;
  error?: string;
}

// includeCounts が false のときは検証件数を照会せず、設定値だけを返す（counts は空）
export const getSettingsOverview = async (signal?: AbortSignal, includeCounts = true) => {
  const formData = new FormData();
  formData.append('include_counts', includeCounts ? 'true' : 'false');
  const response = await api.post<SettingsOverviewResponse>('/settings_overview', formData, { signal });
  return response.data;
};
