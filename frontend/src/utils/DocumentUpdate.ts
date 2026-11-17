// 文書の更新（C8）: 同じ名前のファイルのアップロードと、取り込み直しで消した前の版の件数の表示
import { chunkSize } from './Constants';
import { uploadAPI } from './FileAPI';
import { showNormalToast } from './Toasts';

// /extract の応答の data.replaced
interface ReplacedCounts {
  chunks: number;
  entities: number;
  sections: number;
  rule_edges: number;
}

// 名前の比較は Unicode の正規化（NFC）をそろえて行う（macOS のファイル名は濁点が分かれていることがある）
export const sameFileName = (a: string, b: string): boolean => a.normalize('NFC') === b.normalize('NFC');

// DropZone と同じ分割アップロード（VITE_CHUNK_SIZE ごとに /upload）。失敗したら例外を投げる
export const uploadFileInChunks = async (file: File, model: string, onProgress?: (percent: number) => void) => {
  const total = Math.max(1, Math.ceil(file.size / chunkSize));
  for (let n = 1; n <= total; n++) {
    const res = await uploadAPI(file.slice((n - 1) * chunkSize, n * chunkSize), model, n, total, file.name);
    if (res?.status === 'Failed') {
      throw new Error(res?.message ?? res?.error ?? 'アップロードに失敗しました');
    }
    onProgress?.(Math.ceil((n / total) * 100));
  }
};

// 取り込み直しで消した前の版の件数をトーストで出す。何も消していなければ出さない
export const showReplacedToast = (replaced: ReplacedCounts | null | undefined, fileName?: string) => {
  if (!replaced || !(replaced.chunks || replaced.entities || replaced.sections || replaced.rule_edges)) {
    return;
  }
  const rebuilt =
    replaced.sections || replaced.rule_edges
      ? `。章・節 ${replaced.sections} 件・文書層のリレーション ${replaced.rule_edges} 件は後処理で再生成されます`
      : '';
  showNormalToast(
    `前の版のチャンク ${replaced.chunks} 件・エンティティ ${replaced.entities} 件を削除して再取り込みしました${
      fileName ? `（${fileName}）` : ''
    }${rebuilt}`
  );
};
