import { ResizePanelDetails } from './ResizePanel';
import { LegendsChip } from './LegendsChip';
import GraphPropertiesTable from './GraphPropertiesTable';
import { DocumentRelationship } from '../../utils/DocumentRelations';

// 文書間の関係のみの表示で選択したリレーションの詳細。
// GraphPropertiesPanel はリレーションのプロパティを表示しないため、同じ構成で GraphPropertiesTable に項目を表示する
const DocumentRelationPanel = ({ relationship }: { relationship: DocumentRelationship }) => {
  const properties = Object.entries(relationship.properties).map(([key, value]) => ({ key, value }));
  return (
    <>
      <ResizePanelDetails.Title>
        <h6 className='mr-auto'>リレーションの詳細</h6>
      </ResizePanelDetails.Title>
      <ResizePanelDetails.Content>
        <div className='mx-4 flex! flex-row flex-wrap gap-2'>
          <LegendsChip type='relationship' label={relationship.caption ?? ''} scheme={{}} />
        </div>
        <div className='mx-4 mt-2 n-body-small n-text-palette-neutral-text-weak'>
          {relationship.collapsed
            ? '文書番号ノード（下表の文書番号）を経由する 2 段のリレーションを、表示用に 1 本に集約したものである。'
            : '未取り込み文書の文書番号ノードへのリレーションである。'}
        </div>
        <div className='bg-palette-neutral-border-weak my-3 h-px w-full' />
        <GraphPropertiesTable propertiesWithTypes={properties} />
      </ResizePanelDetails.Content>
    </>
  );
};

export default DocumentRelationPanel;
