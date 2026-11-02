import { Link } from "react-router-dom";
import type { Job, JobEvent, Observation } from "../api/types";
import { useJob, useJobDetailActions } from "../hooks/jobActions";
import { usePolicyStatus, useSheetsOutbox } from "../hooks/api";
import { MentionPanel } from "./MentionPanel";
import { ProgressBar, StatusBadge } from "./StatusBadge";
import { IconChevronDown, IconExternalLink } from "./Icons";
import { MENTION_NAMES, REMOTE_NAMES, formatBytes, formatDuration, formatWhen } from "../lib/state";

/**
 * States whose *content* an operator may still edit. Mirrors the backend's
 * `EDITABLE_STATES` rather than guessing: once bytes have left for YouTube, the title
 * and description are no longer the whole truth about the remote video, so the panel
 * offers no Edit button that would only be refused with a 409.
 */
const EDITABLE_STATES: Job["state"][] = [
  "JOB-DRAFT",
  "JOB-READY",
  "JOB-QUEUED",
  "JOB-FAILED_RETRY",
  "JOB-BLOCKED",
  "JOB-MISSED_WINDOW",
];

/**
 * The job details panel.
 *
 * Everything here is evidence rather than summary: the event log is the worker's own
 * append-only record of what it decided and why, and the observation log is what
 * YouTube actually said on each poll. Showing them together is what makes an operator
 * able to answer "why is this job stuck?" without reading a log file — and it is why
 * an uncertain upload says *outcome uncertain* in as many places as it can, since
 * retrying one of those can create a duplicate video.
 *
 * The three times are kept apart on purpose: when the bytes finished transferring
 * locally, when the video is *intended* to publish, and when YouTube was actually
 * observed to have made it public. Collapsing any two of them is how an operator ends
 * up believing a video is live.
 */
export function JobDetails({ jobId, onClose }: { jobId: number; onClose: () => void }) {
  const detail = useJob(jobId);
  const actions = useJobDetailActions(jobId);
  const reporting = useReportingState();

  if (detail.isLoading) {
    return (
      <aside className="card p-6 text-ink-muted" aria-busy="true">
        Loading…
      </aside>
    );
  }
  if (!detail.data) {
    return (
      <aside className="card p-6 text-ink-muted">
        That job could not be loaded.
        <button className="btn ml-3" onClick={onClose}>
          Close
        </button>
      </aside>
    );
  }

  const {
    job,
    events,
    observations,
    upload_sessions,
    candidates,
    can_requeue,
  } = detail.data;
  const active = job.state === "JOB-UPLOADING";

  /**
   * Every video ID this job may legitimately claim, most-likely-first: the ID the job
   * itself holds (when it holds one), then whatever the ladder found. Deduplicated so
   * the same ID never appears twice as two separate "mine" decisions.
   */
  const adoptable = Array.from(
    new Set([job.video_id, ...candidates].filter((v): v is string => Boolean(v))),
  );

  // The moment YouTube was first observed to have the video public. Derived from the
  // observation log — the backend's record of what YouTube said — never from an
  // upload response.
  const publicObservation = observations.find((o) => o.remote_state === "remote.public");
  const verifiedPublicAt = publicObservation
    ? (publicObservation.publish_at ?? publicObservation.observed_at)
    : null;

  return (
    <aside className="card flex flex-col overflow-hidden" aria-label="Job details">
      <div className="flex items-start justify-between gap-4 border-b border-edge px-5 py-4">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold">{job.title || "(untitled)"}</h2>
          <p className="mt-1 text-xs text-ink-faint">
            {job.channel_title ? job.channel_title : "No channel selected"}
            {job.publish_at_utc ? ` · Scheduled for ${formatWhen(job.publish_at_utc)}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          {EDITABLE_STATES.includes(job.state) && (
            <Link className="btn" to={`/edit/${job.id}`}>
              Edit
            </Link>
          )}
          <button className="btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      <div className="flex flex-col gap-5 overflow-y-auto px-5 py-4">
        <section className="flex flex-col gap-3">
          <StatusBadge job={job} />
          {active && <ProgressBar percent={job.progress_percent} active />}
        </section>

        {job.video_id && (
          <section className="rounded-xl border border-edge bg-[var(--hs-surface-raised)]/60 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <span className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">YouTube Video</span>
                <p className="font-mono text-xs text-ink mt-0.5">{job.video_id}</p>
              </div>
              {job.video_url && (
                <a
                  className="btn btn-primary py-1.5 px-3 text-xs flex items-center gap-1.5 shadow-sm"
                  href={job.video_url}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  Watch on YouTube <IconExternalLink size={12} />
                </a>
              )}
            </div>
          </section>
        )}

        <Facts
          facts={[
            ["Schedule ID", job.public_id],
            ["Target channel", job.channel_title ?? "Not selected"],
            ["Channel ID", job.channel_id ?? "—"],
            ["Visibility sent", job.privacy],
            [
              "Local upload completed",
              job.uploaded_at
                ? `${formatWhen(job.uploaded_at)} (${job.uploaded_at_display ?? "—"})`
                : "Not uploaded",
            ],
            [
              "Intended YouTube publication",
              `${formatWhen(job.publish_at_utc)} (${job.publish_at_display ?? "—"})`,
            ],
            [
              "Verified public publication",
              verifiedPublicAt ? formatWhen(verifiedPublicAt) : "Not yet verified",
            ],
            [
              "Most recent remote state",
              `${REMOTE_NAMES[job.remote_state]}${job.remote_privacy_status ? ` · ${job.remote_privacy_status}` : ""}`,
            ],
            ["Last verified", formatWhen(job.last_polled_at)],
            ["Publication verification", job.poll_result ?? "Never polled"],
            [
              "Google Sheets reporting",
              reporting.forSchedule(job.public_id),
            ],
            ["Error classification", job.failure_kind ?? "—"],
            [
              "Next retry",
              job.next_attempt_at
                ? `${formatWhen(job.next_attempt_at)} (attempt ${job.attempt + 1} of ${job.max_attempts})`
                : "—",
            ],
            ["Source clip", `${job.media_name} · ${formatBytes(job.media_size_bytes)}${job.media_duration_seconds !== null ? ` · ${formatDuration(job.media_duration_seconds)}` : ""}`],
            ["Container", job.media_container ?? "—"],
            ["Tags", job.tags.length ? job.tags.join(", ") : "—"],
            ...(job.mention_requested
              ? [
                  [
                    "Mentions",
                    `${MENTION_NAMES[job.mention_state]}${job.mention_handles.length ? ` (${job.mention_handles.map((h) => `@${h}`).join(", ")})` : ""}`,
                  ] as [string, string],
                  [
                    "Manual mention confirmation",
                    job.mention_state === "manual_required" ? "Required" : "Not required",
                  ] as [string, string],
                ]
              : []),
          ]}
        />

        <MentionPanel job={job} actions={actions} />

        {job.failure_message && (
          <section className="rounded-md border border-cue-warning/40 bg-cue-warning/5 p-3">
            <h3 className="text-xs font-medium uppercase tracking-wide text-cue-warning">Problem</h3>
            <p className="mt-1 text-sm text-ink">{job.failure_message}</p>
            {job.next_attempt_at && (
              <p className="mt-1 text-xs text-ink-muted">
                Next attempt {formatWhen(job.next_attempt_at)}
              </p>
            )}
          </section>
        )}

        <Actions
          job={job}
          actions={actions}
          adoptable={adoptable}
          can_requeue={can_requeue}
          onClose={onClose}
        />

        {/* Technical logs collapsed by default so UI stays simplistic and clean */}
        <details className="group rounded-xl border border-edge bg-[var(--hs-surface-soft)]/40 overflow-hidden">
          <summary className="cursor-pointer select-none px-4 py-3 text-xs font-semibold uppercase tracking-wider text-ink-muted hover:text-white flex items-center justify-between transition-colors">
            <span>Technical Logs & Audit Details ({events.length} events)</span>
            <IconChevronDown size={14} className="transition-transform group-open:rotate-180" />
          </summary>
          <div className="flex flex-col gap-4 border-t border-edge p-4">
            {upload_sessions.length > 0 && (
              <section>
                <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">
                  Resumable sessions
                </h3>
                <ul className="mt-2 flex flex-col gap-1 text-xs text-ink-muted">
                  {upload_sessions.map((s) => (
                    <li key={s.id} className="rounded border border-edge px-2 py-1.5">
                      <span className="text-ink">{s.phase}</span> · {formatBytes(s.bytes_uploaded)} of{" "}
                      {formatBytes(s.total_bytes)} · attempt {s.attempt}
                      {s.insert_may_have_succeeded && (
                        <span className="text-cue-attention"> · outcome uncertain</span>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <Observations observations={observations} />
            <EventLog events={events} />
          </div>
        </details>
      </div>
    </aside>
  );
}

function Actions({
  job,
  actions,
  adoptable,
  can_requeue,
  onClose,
}: {
  job: Job;
  actions: ReturnType<typeof useJobDetailActions>;
  /** Video IDs this job may legitimately claim, most-likely-first (F-04). */
  adoptable: string[];
  /** Whether re-running the job is a meaningful remaining action (F-04). */
  can_requeue: boolean;
  onClose: () => void;
}) {
  const isUploading = job.state === "JOB-UPLOADING";
  const terminal = job.state === "JOB-PUBLISHED" || job.state === "JOB-CANCELLED";

  const handleDelete = () => {
    const confirmMsg = job.video_id
      ? `Are you sure you want to delete this job? This will delete video ${job.video_id} from YouTube and remove it from Hexton.`
      : "Are you sure you want to delete this job from Hexton?";
    if (window.confirm(confirmMsg)) {
      actions.remove.mutate(
        { id: job.id, deleteRemote: true },
        {
          onSuccess: () => {
            onClose();
          },
        }
      );
    }
  };

  const handleCancel = () => {
    actions.cancel.mutate({
      id: job.id,
      body: {
        delete_remote: Boolean(job.video_id),
        reason: "Cancelled from the details panel.",
      },
    });
  };

  return (
    <section>
      <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">Actions</h3>
      <div className="mt-2 flex flex-wrap gap-2">
        {["JOB-DRAFT", "JOB-READY", "JOB-FAILED_RETRY", "JOB-BLOCKED", "JOB-MISSED_WINDOW"].includes(
          job.state,
        ) && (
          <button
            className="btn btn-primary"
            disabled={actions.queue.isPending}
            onClick={() => actions.queue.mutate({ id: job.id, body: {} })}
          >
            Queue now
          </button>
        )}
        {job.can_retry && job.state !== "JOB-DRAFT" && (
          <button
            className="btn"
            disabled={actions.retry.isPending}
            onClick={() => actions.retry.mutate({ id: job.id, body: {} })}
          >
            Retry
          </button>
        )}
        <button
          className="btn"
          disabled={actions.duplicate.isPending}
          onClick={() => actions.duplicate.mutate({ id: job.id, body: {} })}
        >
          Duplicate
        </button>
        {job.can_cancel && (
          <button
            className="btn btn-danger"
            disabled={actions.cancel.isPending}
            onClick={handleCancel}
          >
            {actions.cancel.isPending ? "Cancelling..." : "Cancel job"}
          </button>
        )}
        {(job.state === "JOB-NEEDS_REVIEW" || job.insert_may_have_succeeded) && (
          <>
            {adoptable.map((candidate) => (
              <button
                key={candidate}
                className="btn"
                disabled={actions.reconcile.isPending}
                onClick={() =>
                  actions.reconcile.mutate({
                    id: job.id,
                    body: { video_id: candidate, resolution: "adopt" },
                  })
                }
              >
                This video is mine <span className="font-mono text-[11px]">{candidate}</span>
              </button>
            ))}
            {adoptable.length === 0 && job.video_id && (
              <button
                className="btn"
                disabled={actions.reconcile.isPending}
                onClick={() =>
                  actions.reconcile.mutate({
                    id: job.id,
                    body: { video_id: job.video_id, resolution: "adopt" },
                  })
                }
              >
                This video is mine
              </button>
            )}
            <button
              className="btn"
              disabled={actions.reconcile.isPending}
              onClick={() =>
                actions.reconcile.mutate({ id: job.id, body: { resolution: "cancel" } })
              }
            >
              None of these are mine — cancel
            </button>
            {can_requeue && (
              <button
                className="btn"
                disabled={actions.reconcile.isPending}
                onClick={() =>
                  actions.reconcile.mutate({
                    id: job.id,
                    body: { resolution: "not_uploaded" },
                  })
                }
              >
                None of these are mine — run it again
              </button>
            )}
          </>
        )}
        {!isUploading && (
          <button
            className="btn btn-danger"
            disabled={actions.remove.isPending}
            onClick={handleDelete}
            title={job.video_id ? "Delete job and delete video from YouTube" : "Delete job from Hexton"}
          >
            {actions.remove.isPending ? "Deleting..." : "Delete"}
          </button>
        )}
      </div>

      {(actions.cancel.error || actions.remove.error) && (
        <div className="mt-3 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3.5 text-xs text-rose-300">
          <div className="font-semibold text-rose-200">
            {actions.remove.error ? "Delete Notice" : "Cancellation Notice"}
          </div>
          <div className="mt-1">
            {actions.remove.error?.message || actions.cancel.error?.message}
          </div>
          {((actions.remove.error?.message || actions.cancel.error?.message || "").includes("403") ||
            (actions.remove.error?.message || actions.cancel.error?.message || "").includes("permission")) && (
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <a
                href="#/channels"
                className="inline-flex items-center rounded-lg bg-rose-500/25 px-2.5 py-1 text-xs font-semibold text-rose-200 border border-rose-500/40 hover:bg-rose-500/40 transition-colors"
              >
                Reconnect Channel in YouTube Channels
              </a>
              {actions.remove.error && (
                <button
                  type="button"
                  onClick={() => {
                    actions.remove.mutate(
                      { id: job.id, deleteRemote: false, force: true },
                      { onSuccess: () => onClose() }
                    );
                  }}
                  className="text-xs text-[var(--hs-text-muted)] hover:text-white underline cursor-pointer"
                >
                  Force delete from Hexton only
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {job.state === "JOB-NEEDS_REVIEW" && (
        <div className="mt-2 rounded-md border border-cue-attention/50 bg-cue-attention/10 px-3 py-2 text-xs text-cue-attention">
          <p className="font-medium">
            The outcome of the last upload could not be proven. Hexton will not retry it
            automatically, because a second attempt could create a duplicate video.
          </p>
          <ol className="mt-2 list-decimal space-y-1 pl-4">
            <li>
              Open YouTube Studio and search for a video with this title uploaded in the
              last hour.
            </li>
            <li>
              Found it? Press{" "}
              <span className="font-medium">This video is mine</span> next to the matching
              ID
              {adoptable.length
                ? " — Hexton already listed every candidate video it found for this job."
                : " — Hexton records the ID you confirm."}{" "}
              This is a decision a person made, not a check Hexton can make for you (DF-08).
            </li>
            <li>
              Not there? Press{" "}
              <span className="font-medium">None of these are mine</span> — either to
              cancel the job, or{" "}
              {can_requeue ? (
                <>
                  to <span className="font-medium">run it again</span> once you have
                  confirmed no upload exists
                </>
              ) : (
                "to run it again"
              )}
              . Nothing restarts on its own either way.
            </li>
          </ol>
        </div>
      )}
      {terminal && <p className="mt-2 text-xs text-ink-faint">This job is finished.</p>}
    </section>
  );
}

function Facts({ facts }: { facts: [string, string][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      {facts.map(([label, value]) => (
        <div key={label} className="flex flex-col">
          <dt className="text-xs uppercase tracking-wide text-ink-faint">{label}</dt>
          <dd className="text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Observations({ observations }: { observations: Observation[] }) {
  if (!observations.length) return null;
  return (
    <section>
      <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">
        Observed on YouTube
      </h3>
      <ul className="mt-2 flex flex-col gap-1 text-xs text-ink-muted">
        {observations.slice(0, 12).map((o) => (
          <li key={o.id} className="rounded border border-edge px-2 py-1.5">
            <span className="text-ink">{formatWhen(o.observed_at)}</span> ·{" "}
            {REMOTE_NAMES[o.remote_state]}
            {o.privacy_status ? ` · ${o.privacy_status}` : ""}
            {o.processing_status ? ` · ${o.processing_status}` : ""}
          </li>
        ))}
      </ul>
    </section>
  );
}

function EventLog({ events }: { events: JobEvent[] }) {
  if (!events.length) return null;
  return (
    <section>
      <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">
        What Hexton did
      </h3>
      <ol className="mt-2 flex flex-col gap-2 text-xs text-ink-muted">
        {events.slice(0, 40).map((e) => (
          <li key={e.id} className="border-l-2 border-edge pl-3">
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-[10px] uppercase text-ink-faint">{e.kind}</span>
              <span className="text-ink-faint">{formatWhen(e.occurred_at)}</span>
            </div>
            <p className="mt-0.5 text-ink">{e.message}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

/**
 * What Google Sheets reporting currently says about one schedule.
 *
 * Three distinct answers, deliberately not collapsed: reporting is switched off (the
 * policy has not been accepted, so the outbox is not even readable), no row is owed
 * yet (the upload has not reached the point where a report record exists), or here is
 * the row and its delivery status. Showing "not reported" for the first case would
 * tell an operator their report went missing when nothing was ever attempted.
 */
function useReportingState() {
  const policy = usePolicyStatus();
  const accepted = policy.data?.accepted ?? false;
  const outbox = useSheetsOutbox(accepted);

  return {
    forSchedule(scheduleId: string): string {
      if (!accepted) return "Locked — privacy policy not accepted";
      if (outbox.isLoading) return "Reading…";
      if (outbox.error) return "Unavailable";
      const row = outbox.data?.rows.find((candidate) => candidate.schedule_id === scheduleId);
      if (!row) return "No report row owed";
      const attempts = row.attempts > 0 ? ` · ${row.attempts} attempt${row.attempts === 1 ? "" : "s"}` : "";
      return `${row.status}${attempts}${row.last_error ? ` — ${row.last_error}` : ""}`;
    },
  };
}
