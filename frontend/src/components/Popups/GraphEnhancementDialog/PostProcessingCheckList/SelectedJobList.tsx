import { Checkbox, Flex, Typography } from '@neo4j-ndl/react';
import { capitalizeEachWord } from '../../../../utils/Utils';
import { useMemo } from 'react';
import { POST_PROCESSING_JOBS } from '../../../../utils/Constants';
import { postProcessingPayload } from '../../../../utils/PostProcessingUtils';

// 表示名（一覧に無いジョブは API の名前を読みやすくしたもの）と流れる順番
const jobOf = (task: string) => POST_PROCESSING_JOBS.find((job) => job.title === task);
const jobLabel = (task: string) => jobOf(task)?.label ?? capitalizeEachWord(task);

export default function SelectedJobList({
  postProcessingTasks,
  isGdsActive,
  isSchema,
}: {
  postProcessingTasks: string[];
  isGdsActive: boolean;
  isSchema: boolean;
}) {
  // 実際に送るジョブ（スキーマ設定中の統合・GDS の無いコミュニティを除く）を、流れる順番に並べる
  const ongoingPostProcessingTasks = useMemo(
    () =>
      postProcessingPayload(postProcessingTasks, isGdsActive, isSchema).sort(
        (a, b) => (jobOf(a)?.order ?? 99) - (jobOf(b)?.order ?? 99)
      ),
    [isGdsActive, isSchema, postProcessingTasks]
  );
  return (
    <Flex justifyContent='space-between' flexDirection='column' gap='4'>
      {ongoingPostProcessingTasks.map((task, idx) => {
        return (
          <Checkbox
            key={idx}
            label={<Typography variant='label'>{jobLabel(task)}</Typography>}
            isChecked={true}
            isDisabled={true}
            ariaLabel='Selected-postprocessing-jobs'
          />
        );
      })}
    </Flex>
  );
}
