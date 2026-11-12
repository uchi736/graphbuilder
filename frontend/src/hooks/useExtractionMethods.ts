import { useEffect, useState } from 'react';
import { useFileContext } from '../context/UsersFiles';
import { ExtractionMethodOption, getExtractionMethods } from '../services/ExtractionMethods';
import { EXTRACTION_METHOD_LABELS, EXTRACTION_METHOD_STANDARD } from '../utils/ExtractionMethodUtils';

// 抽出方式の一覧（GET /extraction_methods）を、メイン画面の表示とグラフ設定の「取り込み」タブで共有する。
// 一覧は部品の表示ごとに再取得し、同時に表示された部品の取得は 1 回の要求に集約する

// backend から取得できない場合（旧版の backend・通信の失敗）は標準のみを表示する
export const FALLBACK_METHODS: ExtractionMethodOption[] = [
  { id: EXTRACTION_METHOD_STANDARD, label: EXTRACTION_METHOD_LABELS[EXTRACTION_METHOD_STANDARD], available: true },
];

export interface ExtractionMethodsState {
  methods: ExtractionMethodOption[];
  // 取得を 1 回以上完了したかどうか（失敗を含む）
  loaded: boolean;
  // 直近の取得に失敗したかどうか
  failed: boolean;
}

let current: ExtractionMethodsState = { methods: FALLBACK_METHODS, loaded: false, failed: false };
let inflight: Promise<void> | null = null;
const listeners = new Set<(state: ExtractionMethodsState) => void>();

const publish = (next: ExtractionMethodsState) => {
  current = next;
  listeners.forEach((listener) => listener(next));
};

export const refreshExtractionMethods = (): Promise<void> => {
  if (inflight) {
    return inflight;
  }
  inflight = getExtractionMethods()
    .then((methods) => publish({ methods, loaded: true, failed: false }))
    .catch(() => publish({ methods: FALLBACK_METHODS, loaded: true, failed: true }))
    .finally(() => {
      inflight = null;
    });
  return inflight;
};

export const useExtractionMethods = () => {
  const [state, setState] = useState<ExtractionMethodsState>(current);
  useEffect(() => {
    listeners.add(setState);
    setState(current);
    refreshExtractionMethods();
    return () => {
      listeners.delete(setState);
    };
  }, []);
  return { ...state, refresh: refreshExtractionMethods };
};

// 選択中の抽出方式を現在使用できない場合は標準に戻す。保存した選択は削除しない（使用できるようになれば、次に画面を開いたときに戻る）
export const useExtractionMethodGuard = () => {
  const { extractionMethod, setExtractionMethod } = useFileContext();
  const methodsState = useExtractionMethods();
  const { loaded, methods } = methodsState;
  useEffect(() => {
    if (!loaded || extractionMethod === EXTRACTION_METHOD_STANDARD) {
      return;
    }
    if (!methods.find((m) => m.id === extractionMethod)?.available) {
      setExtractionMethod(EXTRACTION_METHOD_STANDARD);
    }
  }, [loaded, methods, extractionMethod]);
  return methodsState;
};
