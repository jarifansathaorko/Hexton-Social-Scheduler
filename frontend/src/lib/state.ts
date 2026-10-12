/**
 * State presentation.
 *
 * The *label* a job shows comes from the backend (`display_label`), which renders it
 * from the single canonical `DISPLAY_MAP` in `hexton.domain.jobstate`. This module
 * deliberately does **not** decide what a state is called or what it means — it only
 * turns the backend's own `display_cue` string into a colour, so the cue the PRD
 * describes ("grey outline", "blue progress", "amber", "red warning", "green filled")
 * reaches the screen. A second mapping of state to meaning would be exactly the
 * problem the canonical map was introduced to fix.
 */

import type { Job, JobState, MentionState, RemoteState } from "../api/types";

export type Cue = "neutral" | "progress" | "attention" | "warning" | "success";

const CUE_CLASS: Record<Cue, string> = {
  neutral: "border-cue-neutral/50 text-cue-neutral",
  progress: "border-cue-progress/60 text-cue-progress bg-cue-progress/10",
  attention: "border-cue-attention/60 text-cue-attention bg-cue-attention/10",
  warning: "border-cue-warning/60 text-cue-warning bg-cue-warning/10",
  success: "border-cue-success/60 text-cue-success bg-cue-success/10",
};

/** The backend's cue sentence, reduced to one of five colours. */
export function cueFromBackend(cue: string): Cue {
  const text = cue.toLowerCase();
  if (text.includes("red")) return "warning";
  if (text.includes("amber")) return "attention";
  if (text.includes("green")) return "success";
  if (text.includes("blue")) return "progress";
  return "neutral";
}

export function cueClass(cue: Cue): string {
  return CUE_CLASS[cue];
}

/** Compact, human-readable name for a state, for filters and legends. */
export const STATE_NAMES: Record<JobState, string> = {
  "JOB-DRAFT": "Draft",
  "JOB-READY": "Ready",
  "JOB-QUEUED": "Queued",
  "JOB-UPLOADING": "Uploading",
  "JOB-UPLOADED": "Uploaded",
  "JOB-SCHEDULED": "Scheduled",
  "JOB-PUBLISHED": "Published",
  "JOB-FAILED_RETRY": "Retry waiting",
  "JOB-BLOCKED": "Blocked",
  "JOB-MISSED_WINDOW": "Missed window",
  "JOB-NEEDS_REVIEW": "Needs review",
  "JOB-CANCELLED": "Cancelled",
  "JOB-FAILED": "Failed",
};

/** The order the filter chips show, most urgent first. */
export const STATE_ORDER: JobState[] = [
  "JOB-NEEDS_REVIEW",
  "JOB-BLOCKED",
  "JOB-FAILED",
  "JOB-FAILED_RETRY",
  "JOB-MISSED_WINDOW",
  "JOB-UPLOADING",
  "JOB-QUEUED",
  "JOB-READY",
  "JOB-DRAFT",
  "JOB-UPLOADED",
  "JOB-SCHEDULED",
  "JOB-PUBLISHED",
  "JOB-CANCELLED",
];

export const REMOTE_NAMES: Record<RemoteState, string> = {
  "remote.unknown": "Not observed",
  "remote.not_uploaded": "Not uploaded",
  "remote.uploading": "Uploading",
  "remote.uploaded_processing": "Processing",
  "remote.scheduled": "Scheduled",
  "remote.public": "Public",
  "remote.other_privacy": "Other visibility",
  "remote.failed": "Failed",
};

/**
 * Short labels for table chips.
 *
 * The canonical operator-facing text lives in the backend's `MENTION_DISPLAY_MAP`
 * (`hexton/domain/mention.py`) and is what the detail panel renders via
 * `Mention.label`. This map exists only for chips where the full sentence is too long,
 * so it must stay *consistent* with that map — in particular `verified` must never read
 * as though an API check happened. If the two drift, this one is the one to change.
 */
export const MENTION_NAMES: Record<MentionState, string> = {
  not_requested: "No mention",
  text_only: "Not verified — text only",
  manual_required: "Manual confirmation needed",
  pending_manual: "Pending manual action",
  verified: "Confirmed by operator (attestation)",
  failed: "Failed or removed",
};

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  let value = bytes;
  for (const unit of ["KB", "MB", "GB", "TB"]) {
    value /= 1024;
    if (value < 1024) return `${value.toFixed(1)} ${unit}`;
  }
  return `${value.toFixed(1)} PB`;
}

export function formatDuration(seconds: number | null): string | null {
  if (seconds === null) return null;
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Format a UTC instant in the operator's own zone, without a library. */
export function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** A `datetime-local` value for an existing UTC instant. */
export function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** The browser's IANA zone, used as the default when creating a job. */
export function browserZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function isTerminal(state: JobState): boolean {
  return state === "JOB-PUBLISHED" || state === "JOB-CANCELLED" || state === "JOB-FAILED";
}

/** Whether a row needs the operator's attention more than it needs the worker's. */
export function needsAttention(job: Job): boolean {
  return (
    job.state === "JOB-NEEDS_REVIEW" ||
    job.state === "JOB-BLOCKED" ||
    job.state === "JOB-FAILED" ||
    job.state === "JOB-MISSED_WINDOW" ||
    (job.state === "JOB-FAILED_RETRY" && job.attempt >= job.max_attempts)
  );
}
