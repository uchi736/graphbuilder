import { Button, Checkbox, Flex, Tag, Typography, useMediaQuery } from '@neo4j-ndl/react';
import { useMemo } from 'react';
import { tokens } from '@neo4j-ndl/base';
import { POST_PROCESSING_GROUPS, POST_PROCESSING_JOBS, PostProcessingJob } from '../../../../utils/Constants';
import { useFileContext } from '../../../../context/UsersFiles';
import { useCredentials } from '../../../../context/UserCredentials';
import { useHasSelections } from '../../../../hooks/useHasSelections';
import { postProcessing } from '../../../../services/PostProcessing';
import { mergePostProcessingCounts, postProcessingPayload } from '../../../../utils/PostProcessingUtils';
import { showErrorToast, showNormalToast, showSuccessToast } from '../../../../utils/Toasts';

const byOrder = (a: PostProcessingJob, b: PostProcessingJob) => a.order - b.order;

export default function PostProcessingCheckList() {
  const { breakpoints } = tokens;
  const tablet = useMediaQuery(`(min-width:${breakpoints.xs}) and (max-width: ${breakpoints.lg})`);
  const {
    postProcessingTasks,
    setPostProcessingTasks,
    selectedNodes,
    selectedRels,
    filesData,
    setFilesData,
    queue,
    postProcessingVal,
    setPostProcessingVal,
  } = useFileContext();
  const { isGdsActive } = useCredentials();
  const hasSelections = useHasSelections(selectedNodes, selectedRels);
  // postProcessingVal（上流版では未使用）を「この画面から実行した後処理が実行中」の印に使用する。タブを移っても二重に実行しない
  const running = postProcessingVal;
  const busy = running || !queue.isEmpty() || filesData.some((f) => f.status === 'Processing');

  // 選べないジョブ: GDS が無いときのコミュニティ、スキーマを設定しているときのスキーマ統合
  const isDisabled = (job: PostProcessingJob) =>
    (job.title === 'enable_communities' && !isGdsActive) ||
    (job.title === 'graph_schema_consolidation' && hasSelections);

  const toggle = (title: string, checked: boolean) => {
    if (checked) {
      setPostProcessingTasks((prev) => (prev.includes(title) ? prev : [...prev, title]));
    } else {
      setPostProcessingTasks((prev) => prev.filter((s) => s !== title));
    }
  };

  // 今回送るジョブ（取り込みの後の自動の後処理と同じ絞り込み）を、バックエンドが実行する順序に並べる
  const runList = useMemo(() => {
    const payload = postProcessingPayload(postProcessingTasks, isGdsActive, hasSelections);
    return POST_PROCESSING_JOBS.filter((job) => payload.includes(job.title)).sort(byOrder);
  }, [postProcessingTasks, isGdsActive, hasSelections]);

  const runAll = async () => {
    const payload = runList.map((job) => job.title);
    if (!payload.length || busy) {
      return;
    }
    setPostProcessingVal(true);
    const slow = runList.some((job) => job.group === 'llm') ? '。LLM を使用するジョブを含むため、時間がかかります' : '';
    showNormalToast(`後処理を開始しました（${payload.length} 件）${slow}`);
    try {
      const response = await postProcessing(payload);
      const rows = response.data?.data;
      if (Array.isArray(rows) && rows.length) {
        setFilesData((prev) => mergePostProcessingCounts(prev, rows));
      }
      if (response.data?.status === 'Success') {
        showSuccessToast('後処理がすべて完了しました');
      } else {
        const detail = String(response.data?.error ?? response.data?.message ?? '');
        showErrorToast(`後処理の一部が完了しませんでした: ${detail.slice(0, 300)}`);
      }
    } catch (error) {
      showErrorToast(`後処理を実行できませんでした: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setPostProcessingVal(false);
    }
  };

  return (
    <Flex flexDirection='column' gap={tablet ? '6' : '8'}>
      <Typography variant='body-medium'>
        取り込みの完了後に、チェックを入れたジョブを自動で実行する。この画面から一括で再実行することもできる。
      </Typography>
      {POST_PROCESSING_GROUPS.map((group) => (
        <section key={group.id} className='flex flex-col gap-4'>
          <div className='flex flex-col gap-1'>
            <Typography variant='subheading-medium'>{group.label}</Typography>
            <Typography variant='body-small'>{group.description}</Typography>
          </div>
          {POST_PROCESSING_JOBS.filter((job) => job.group === group.id)
            .sort(byOrder)
            .map((job) => (
              <Flex key={job.title} gap='2'>
                <Checkbox
                  label={<Typography variant='label'>{job.label}</Typography>}
                  isChecked={!isDisabled(job) && postProcessingTasks.includes(job.title)}
                  onChange={(e) => toggle(job.title, e.target.checked)}
                  isDisabled={isDisabled(job)}
                  ariaLabel={`checkbox-${job.title}`}
                />
                <Typography variant={tablet ? 'body-small' : 'body-medium'}>{job.description}</Typography>
                <Flex flexDirection='row' gap='2' alignItems='center'>
                  {job.group === 'llm' && (
                    <Tag type='default' size='small'>
                      LLM を使用
                    </Tag>
                  )}
                  <Typography variant='body-small' className='text-palette-neutral-text-weak'>
                    {job.title}
                  </Typography>
                </Flex>
              </Flex>
            ))}
        </section>
      ))}
      <section className='flex flex-col gap-3'>
        <Typography variant='subheading-medium'>実行順序</Typography>
        <Typography variant='body-medium'>
          {runList.length
            ? runList.map((job, i) => `${i + 1}. ${job.label}`).join(' → ')
            : 'チェックを入れたジョブはない'}
        </Typography>
        <Flex flexDirection='row' gap='4' alignItems='center'>
          <Button
            onClick={runAll}
            isDisabled={busy || !runList.length}
            isLoading={running}
            size={tablet ? 'small' : 'medium'}
          >
            チェックを入れたジョブを実行
          </Button>
          <Typography variant='body-small'>
            {busy && !running ? '取り込み中は実行できない。' : '上記の順序で一括実行する。'}
          </Typography>
        </Flex>
      </section>
    </Flex>
  );
}
