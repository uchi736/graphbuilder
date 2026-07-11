import { Checkbox } from '@neo4j-ndl/react';

function MetricsCheckbox({
  enableReference,
  toggleReferenceVisibility,
  isDisabled = false,
}: {
  enableReference: boolean;
  toggleReferenceVisibility: React.DispatchWithoutAction;
  isDisabled?: boolean;
}) {
  return (
    <Checkbox
      isDisabled={isDisabled}
      label={'参照回答を入力して追加のメトリクスを取得'}
      isChecked={enableReference}
      onChange={toggleReferenceVisibility}
    />
  );
}
export default MetricsCheckbox;
