import { ChangeEvent, useCallback, useRef } from 'react';
import { useFileContext } from '../context/UsersFiles';
import { CustomFile } from '../types';
import { sameFileName, uploadFileInChunks } from '../utils/DocumentUpdate';
import { showErrorToast, showNormalToast } from '../utils/Toasts';

/**
 * 文書の更新（C8）: 一覧の行の「更新」から、同じ名前の新しい版のファイルを選ばせてアップロードし、抽出を始める。
 * 抽出は「グラフを生成」と同じ経路（generate = Content の handleGenerateGraph）。前の版はバックエンドが抽出の始めに消す。
 */
export const useDocumentUpdate = (generate: (files: CustomFile[]) => void) => {
  const { filesData, setFilesData, model } = useFileContext();
  const inputRef = useRef<HTMLInputElement>(null);
  const targetRef = useRef<CustomFile | null>(null);
  // FileTable の列の定義（useMemo）は関数を作り直さないので、最新の値は ref から読む
  const latest = useRef({ filesData, model, generate });
  latest.current = { filesData, model, generate };

  const openUpdateChooser = useCallback((id: string) => {
    const target = latest.current.filesData.find((f) => f.id === id);
    if (!target?.name || !inputRef.current) {
      return;
    }
    targetRef.current = target;
    const dot = target.name.lastIndexOf('.');
    inputRef.current.accept = dot >= 0 ? target.name.substring(dot) : '';
    inputRef.current.click();
  }, []);

  const onChosen = async (e: ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0];
    e.target.value = '';
    const target = targetRef.current;
    targetRef.current = null;
    if (!picked || !target?.name) {
      return;
    }
    const name = target.name;
    if (!sameFileName(picked.name, name)) {
      showErrorToast(
        `ファイル名が一致しません（選択したファイル: ${picked.name}、更新する文書: ${name}）。同じ名前のファイルを選択してください`
      );
      return;
    }
    // 名前は一覧の名前にそろえる（違いが Unicode の正規化だけのとき）。バックエンドは名前で文書を引く
    const file = picked.name === name ? picked : new File([picked], name, { type: picked.type });
    const patch = (p: Partial<CustomFile>) =>
      setFilesData((prev) => prev.map((f) => (f.id === target.id ? { ...f, ...p } : f)));
    // 再処理の指定は外す（残っていると前のチャンクを使う再処理になり、新しい版が読まれない）
    patch({ status: 'Uploading', uploadProgress: 0, retryOption: '', retryOptionStatus: false, errorMessage: '' });
    try {
      await uploadFileInChunks(file, latest.current.model, (percent) => patch({ uploadProgress: percent }));
    } catch (err) {
      patch({ status: 'Upload Failed' });
      showErrorToast(`${name} のアップロードに失敗しました: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    const row: CustomFile = {
      ...target,
      size: file.size,
      status: 'New',
      uploadProgress: 100,
      retryOption: '',
      retryOptionStatus: false,
      errorMessage: '',
      nodesCount: 0,
      relationshipsCount: 0,
      processingProgress: undefined,
      processingTotalTime: 0,
      model: latest.current.model,
      createdAt: new Date(),
    };
    patch(row);
    showNormalToast(`${name} の新しい版をアップロードしました。前の版を削除して再取り込みします`);
    latest.current.generate([row]);
  };

  const updateInput = (
    <input
      ref={inputRef}
      type='file'
      style={{ display: 'none' }}
      onChange={(e) => {
        void onChosen(e);
      }}
    />
  );
  return { openUpdateChooser, updateInput };
};
