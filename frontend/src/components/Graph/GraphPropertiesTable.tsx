import { GraphLabel, Typography } from '@neo4j-ndl/react';
import { GraphPropertiesTableProps, PropertyValue } from '../../types';
import { isSystemPropertyKey } from '../../utils/GraphEditUtils';

const PropertyRow = ({ propertyKey, value }: { propertyKey: string; value: PropertyValue }) => (
  <div className='border-palette-neutral-border-weak flex! border-t py-1 pl-2 first:border-none'>
    <div className='shrink basis-1/3 overflow-hidden whitespace-nowrap'>
      <GraphLabel
        type='propertyKey'
        className='pointer-events-none max-w-full! text-ellipsis'
        htmlAttributes={{
          tabIndex: -1,
        }}
      >
        {propertyKey}
      </GraphLabel>
    </div>
    <div className={`ml-2 flex-1 whitespace-pre-wrap`}>{value}</div>
  </div>
);

const GraphPropertiesTable = ({ propertiesWithTypes }: GraphPropertiesTableProps): JSX.Element => {
  const displayable = propertiesWithTypes.filter(
    ({ value }) => value !== undefined && value !== null && value !== '' && !Array.isArray(value)
  );
  // 先頭が _ のプロパティ（版の番号 _v、手動の印 _curated など）は通常の表示から外し、「システム」に畳む
  const regular = displayable.filter(({ key }) => !isSystemPropertyKey(key));
  const system = displayable.filter(({ key }) => isSystemPropertyKey(key));
  return (
    <div className='flex w-full flex-col break-all px-4 text-sm' data-testid='viz-details-pane-properties-table'>
      <div className='mb-1 flex! flex-row pl-2'>
        <Typography variant='body-medium' className='basis-1/3'>
          キー
        </Typography>
        <Typography variant='body-medium'>値</Typography>
      </div>
      {regular.map(({ key, value }) => (
        <PropertyRow key={key} propertyKey={key} value={value} />
      ))}
      {system.length > 0 && (
        <details className='mt-2' data-testid='viz-details-pane-system-properties'>
          <summary className='cursor-pointer pl-2 n-body-small'>システムプロパティ（{system.length}）</summary>
          {system.map(({ key, value }) => (
            <PropertyRow key={key} propertyKey={key} value={String(value)} />
          ))}
        </details>
      )}
    </div>
  );
};

export default GraphPropertiesTable;
