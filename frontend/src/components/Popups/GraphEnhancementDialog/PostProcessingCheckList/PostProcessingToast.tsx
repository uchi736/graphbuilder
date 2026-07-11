import { Flex, Typography } from '@neo4j-ndl/react';
import SelectedJobList from './SelectedJobList';

export default function PostProcessingToast({
  postProcessingTasks,
  isGdsActive,
  isSchema,
}: {
  postProcessingTasks: string[];
  isGdsActive: boolean;
  isSchema: boolean;
}) {
  return (
    <Flex flexDirection='column'>
      <Typography variant='subheading-medium'>一部のQ&A機能は処理完了後に利用可能になります</Typography>
      <Typography variant='subheading-small'>実行中の後処理ジョブ</Typography>
      <SelectedJobList postProcessingTasks={postProcessingTasks} isGdsActive={isGdsActive} isSchema={isSchema} />
    </Flex>
  );
}
