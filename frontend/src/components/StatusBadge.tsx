import type { Job } from "../api/types";
import { MENTION_NAMES, REMOTE_NAMES, cueClass, cueFromBackend } from "../lib/state";

/**
 * The rendered status for a job.
 *
 * The label is the backend's (`display_label`), which is the single canonical
 * mapping. The chip adds the two things a label cannot carry alone: the colour cue,
 * and the *remote* state — because "Scheduled" locally and "still private" on YouTube
 * are different facts and collapsing them is how an operator ends up believing a
 * video is live when it is not.
 */
export function StatusBadge({ job, compact = false }: { job: Job; compact?: boolean }) {
  const cue = cueClass(cueFromBackend(job.display_cue));
  return (
    <div className="flex flex-col gap-1">
      <span
        className={`inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${cue}`}
      >
        {job.state === "JOB-UPLOADING" && job.progress_percent > 0 && (
          <span aria-hidden className="tabular-nums">
            {job.progress_percent}%
          </span>
        )}
        <span>{job.display_label}</span>
      </span>
      {!compact && (
        <span className="text-[11px] text-ink-faint">
          YouTube: {REMOTE_NAMES[job.remote_state]}
          {job.remote_privacy_status ? ` · ${job.remote_privacy_status}` : ""}
          {job.remote_processing_status ? ` · ${job.remote_processing_status}` : ""}
        </span>
      )}
      {!compact && job.mention_requested && (
        <span
          className={`text-[11px] ${
            job.mention_state === "manual_required" ? "text-cue-attention" : "text-ink-faint"
          }`}
        >
          Mention: {MENTION_NAMES[job.mention_state]}
        </span>
      )}
    </div>
  );
}

/**
 * Upload progress.
 *
 * Shown only while an upload is genuinely in flight. A progress bar that stays at 0%
 * or jumps to 100% is worse than none, so it renders the backend's number and nothing
 * else — it does not interpolate or animate towards a value that has not been
 * observed.
 */
export function ProgressBar({ percent, active }: { percent: number; active: boolean }) {
  if (!active) return null;
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div className="flex items-center gap-2">
      <div
        role="progressbar"
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Upload progress"
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-edge"
      >
        <div
          className="h-full rounded-full bg-cue-progress transition-[width] duration-500"
          style={{ width: `${clamped}%` }}
        />
      </div>
      <span className="w-10 text-right text-xs tabular-nums text-ink-muted">{clamped}%</span>
    </div>
  );
}
