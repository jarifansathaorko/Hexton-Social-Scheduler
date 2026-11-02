import type { Job } from "../api/types";
import { MENTION_NAMES, REMOTE_NAMES, cueClass, cueFromBackend } from "../lib/state";

/**
 * The rendered status badge for a job.
 *
 * Uses the backend canonical `display_label` and `display_cue` mapping with
 * refined dark-surface contrast, pill geometry, and clear remote YouTube status.
 */
export function StatusBadge({ job, compact = false }: { job: Job; compact?: boolean }) {
  const cue = cueClass(cueFromBackend(job.display_cue));
  return (
    <div className="flex flex-col gap-1">
      <span
        className={`inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium tracking-tight ${cue}`}
      >
        <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" aria-hidden />
        {job.state === "JOB-UPLOADING" && job.progress_percent > 0 && (
          <span aria-hidden className="tabular-nums font-semibold">
            {job.progress_percent}%
          </span>
        )}
        <span>{job.display_label}</span>
      </span>
      {!compact && (
        <span className="text-[11px] text-[var(--hs-text-muted)] flex items-center gap-1">
          <span>YouTube: {REMOTE_NAMES[job.remote_state]}</span>
          {job.remote_privacy_status ? <span>· {job.remote_privacy_status}</span> : null}
          {job.remote_processing_status ? <span>· {job.remote_processing_status}</span> : null}
        </span>
      )}
      {!compact && job.mention_requested && (
        <span
          className={`text-[11px] ${
            job.mention_state === "manual_required" ? "text-[var(--hs-warning)] font-medium" : "text-[var(--hs-text-muted)]"
          }`}
        >
          Mention: {MENTION_NAMES[job.mention_state]}
        </span>
      )}
    </div>
  );
}

/**
 * Upload progress bar.
 *
 * Renders the observed worker progress with a smooth gradient indicator
 * and readable percentage badge.
 */
export function ProgressBar({ percent, active }: { percent: number; active: boolean }) {
  if (!active) return null;
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div className="flex items-center gap-2.5 w-full">
      <div
        role="progressbar"
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Upload progress"
        className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--hs-surface-soft)] border border-[var(--hs-border)]"
      >
        <div
          className="h-full rounded-full bg-gradient-to-r from-[var(--hs-violet)] to-[var(--hs-info)] shadow-[0_0_8px_rgba(96,165,250,0.4)] transition-[width] duration-300"
          style={{ width: `${clamped}%` }}
        />
      </div>
      <span className="w-10 text-right text-xs font-mono font-medium text-[var(--hs-text-secondary)]">{clamped}%</span>
    </div>
  );
}
