import { useJob as useJobQuery } from "./api";
import {
  useBeginMention,
  useCancelJob,
  useClearMention,
  useConfirmMention,
  useDeleteJob,
  useDuplicateJob,
  useMention,
  useQueueJob,
  useReconcileJob,
  useReportMentionRemoved,
  useRescheduleJob,
  useRetryJob,
} from "./api";

export const useJob = useJobQuery;

/**
 * Every action available from the details panel, grouped.
 *
 * Grouped rather than constructed per button so the panel stays readable and so each
 * mutation's invalidation behaviour is defined once. Each one maps to an endpoint
 * that records an event, which is why the panel never has to optimistic-update: the
 * truth arrives from the worker's own record on the next poll.
 *
 * The mention actions are keyed by job because they now carry their own response —
 * the workflow view — rather than a job, so the panel can show the new state,
 * the new steps and the attestation-only qualifier immediately.
 */
export function useJobDetailActions(jobId: number) {
  const queue = useQueueJob();
  const retry = useRetryJob();
  const duplicate = useDuplicateJob();
  const cancel = useCancelJob();
  const reschedule = useRescheduleJob();
  const mention = useMention(jobId);
  const confirmMention = useConfirmMention(jobId);
  const clearMention = useClearMention(jobId);
  const beginMention = useBeginMention(jobId);
  const reportMentionRemoved = useReportMentionRemoved(jobId);
  const reconcile = useReconcileJob();
  const remove = useDeleteJob();

  void jobId;
  void reschedule;

  return {
    queue,
    retry,
    duplicate,
    cancel,
    reschedule,
    mention,
    confirmMention,
    clearMention,
    beginMention,
    reportMentionRemoved,
    reconcile,
    remove,
  };
}
