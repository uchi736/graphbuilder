import { Checkbox, Radio } from '@neo4j-ndl/react';
import { DocumentRelationKind, DocumentViewScope, RELATION_STYLES } from '../../utils/DocumentRelations';

interface DocumentScopeControlProps {
  scope: DocumentViewScope;
  onScopeChange: (scope: DocumentViewScope) => void;
  includeExternal: boolean;
  onIncludeExternalChange: (includeExternal: boolean) => void;
  isDisabled?: boolean;
  // 文書間の関係のみの表示で描画中のリレーションの種類ごとの件数。null の場合は表示しない
  kindCounts?: Record<DocumentRelationKind, number> | null;
}

interface KindCountsProps {
  counts: Record<DocumentRelationKind, number>;
  includeExternal: boolean;
}

// 種類ごとの件数の項目名と表示の順序
const KIND_NAMES: Record<DocumentRelationKind, string> = {
  reference: '文書間の参照',
  listing: '発行目録への掲載',
  external: '未取り込み文書への参照',
};
const KIND_ORDER: DocumentRelationKind[] = ['reference', 'listing', 'external'];
// 凡例の見本の線の長さ（px）と、描画の線幅に対する太さの倍率
const SWATCH_LENGTH = 16;
const SWATCH_WIDTH_SCALE = 2;

// 種類ごとの件数。未取り込み文書への参照は、表示する場合のみ件数を示す。
// 各項目の前に描画と同じ色・線幅に比例した太さの短い線を置き、色の凡例を兼ねる。
// 見本の線は文字を含まない行内要素とし、行の文字列は「文書間の参照 15 件／発行目録への掲載 5 件」の形を保つ
const KindCounts = ({ counts, includeExternal }: KindCountsProps) => (
  <div className='w-full n-body-small' data-testid='graph-view-scope-counts'>
    {KIND_ORDER.filter((kind) => includeExternal || kind !== 'external').map((kind, index) => (
      <span key={kind}>
        {index > 0 && <span className='mx-1'>／</span>}
        <span
          aria-hidden='true'
          className='inline-block mr-1'
          style={{
            width: SWATCH_LENGTH,
            height: RELATION_STYLES[kind].width * SWATCH_WIDTH_SCALE,
            backgroundColor: RELATION_STYLES[kind].color,
            verticalAlign: 'middle',
          }}
        />
        {`${KIND_NAMES[kind]} ${counts[kind]} 件`}
      </span>
    ))}
  </div>
);

// 全文書のグラフの表示範囲（全ノード／文書間の関係のみ）と、未取り込み文書への参照の表示の切り替え。
// 種類ごとの件数は、親の Flex（折り返しあり）の中で操作の行の下に幅いっぱいの行として表示する。
// 全ノードの表示では表示しない
const DocumentScopeControl = ({
  scope,
  onScopeChange,
  includeExternal,
  onIncludeExternalChange,
  isDisabled = false,
  kindCounts = null,
}: DocumentScopeControlProps) => (
  <>
    <div className='flex flex-wrap items-center gap-x-5 gap-y-1 mt-2' data-testid='graph-view-scope'>
      <div className='flex flex-wrap items-center gap-x-4' role='radiogroup' aria-label='表示範囲'>
        <span className='n-body-medium'>表示範囲：</span>
        <Radio
          label='全ノード'
          isChecked={scope === 'all'}
          isDisabled={isDisabled}
          onChange={() => onScopeChange('all')}
          htmlAttributes={{ name: 'graph-view-scope' }}
        />
        <Radio
          label='文書間の関係のみ'
          isChecked={scope === 'documents'}
          isDisabled={isDisabled}
          onChange={() => onScopeChange('documents')}
          htmlAttributes={{ name: 'graph-view-scope' }}
        />
      </div>
      <Checkbox
        label='未取り込み文書への参照を表示'
        isChecked={includeExternal}
        isDisabled={isDisabled || scope !== 'documents'}
        onChange={() => onIncludeExternalChange(!includeExternal)}
      />
    </div>
    {scope === 'documents' && kindCounts && <KindCounts counts={kindCounts} includeExternal={includeExternal} />}
  </>
);

export default DocumentScopeControl;
