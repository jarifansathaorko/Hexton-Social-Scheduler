import type { Job, JobEvent, Observation } from "../api/types";
import { useJob, useJobDetailActions } from "../hooks/jobActions";
import { MentionPanel } from "./MentionPanel";
import { ProgressBar, StatusBadge } from "./StatusBadge";
import { MENTION_NAMES, REMOTE_NAMES, formatBytes, formatDuration, formatWhen } from "../lib/state";

/**
 * The job details panel.
 *
 * Everything here is evidence rather than summary: the event log is the worker's own
 * append-only record of what it decided and why, and the observation log is what
 * YouTube actually said on each poll. Showing them together is what makes an operator
 * able to answer "why is this job stuck?" without reading a log file — and it is why
 * an uncertain upload says *outcome uncertain* in as many places as it can, since
 * retrying one of those can create a duplicate video.
 */
export function JobDetails({ jobId, onClose }: { jobId: number; onClose: () => void }) {
  const detail = useJob(jobId);
  const actions = useJobDetailActions(jobId);

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

  const { job, events, observations, upload_sessions, allowed_transitions } = detail.data;
  const active = job.state === "JOB-UPLOADING";

  return (
    <aside className="card flex flex-col overflow-hidden" aria-label="Job details">
      <div className="flex items-start justify-between gap-4 border-b border-edge px-5 py-4">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold">{job.title || "(untitled)"}</h2>
          <p className="mt-1 text-xs text-ink-faint">
            {job.public_id} · {allowed_transitions.length} legal next states
          </p>
        </div>
        <button className="btn" onClick={onClose}>
          Close
        </button>
      </div>

      <div className="flex flex-col gap-5 overflow-y-auto px-5 py-4">
        <section className="flex flex-col gap-3">
          <StatusBadge job={job} />
          {active && <ProgressBar percent={job.progress_percent} active />}
        </section>

        <Facts
          facts={[
            ["Target channel", job.channel_title ?? "Not selected"],
            ["Channel ID", job.channel_id ?? "—"],
            ["Visibility sent", job.privacy],
            ["Intended publication", `${formatWhen(job.publish_at_utc)} (${job.publish_at_display ?? "—"})`],
            ["Actual upload completed", formatWhen(job.uploaded_at)],
            ["Most recent remote state", `${REMOTE_NAMES[job.remote_state]}${job.remote_privacy_status ? ` · ${job.remote_privacy_status}` : ""}`],
            ["Last verified", formatWhen(job.last_polled_at)],
            ["Publication verification", job.poll_result ?? "Never polled"],
            ["Source clip", `${job.media_name} · ${formatBytes(job.media_size_bytes)}${job.media_duration_seconds !== null ? ` · ${formatDuration(job.media_duration_seconds)}` : ""}`],
            ["Container", job.media_container ?? "—"],
            ["Tags", job.tags.length ? job.tags.join(", ") : "—"],
            [
              "Mentions",
              job.mention_requested
                ? `${MENTION_NAMES[job.mention_state]}${job.mention_handles.length ? ` (${job.mention_handles.map((h) => `@${h}`).join(", ")})` : ""}`
                : "None requested",
            ],
            [
              "Manual mention confirmation",
              job.mention_requested && job.mention_state === "manual_required" ? "Required" : "Not required",
            ],
          ]}
        />

        <MentionPanel job={job} actions={actions} />

        {job.video_id && (
          <section className="rounded-md border border-edge bg-canvas p-3">
            <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">Video</h3>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-ink-faint">ID</dt>
              <dd className="font-mono text-xs">{job.video_id}</dd>
              <dt className="text-ink-faint">URL</dt>
              <dd>
                {job.video_url ? (
                  <a className="text-cue-progress hover:underline" href={job.video_url} target="_blank" rel="noreferrer noopener">
                    {job.video_url}
                  </a>
                ) : (
                  "—"
                )}
              </dd>
            </dl>
          </section>
        )}

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

        <Actions job={job} actions={actions} />

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
    </aside>
  );
}

function Actions({ job, actions }: { job: Job; actions: ReturnType<typeof useJobDetailActions> }) {
  const terminal = job.state === "JOB-PUBLISHED" || job.state === "JOB-CANCELLED";
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
            onClick={() =>
              actions.cancel.mutate({
                id: job.id,
                body: { delete_remote: job.video_id !== null, reason: "Cancelled from the details panel." },
              })
            }
          >
            Cancel job
          </button>
        )}
        {(job.state === "JOB-NEEDS_REVIEW" || job.insert_may_have_succeeded) && (
          <>
            {job.video_id && (
              <button
                className="btn"
                disabled={actions.reconcile.isPending}
                onClick={() => actions.reconcile.mutate({ id: job.id, body: { video_id: job.video_id } })}
              >
                This video is mine
              </button>
            )}
            <button
              className="btn"
              disabled={actions.reconcile.isPending}
              onClick={() => actions.reconcile.mutate({ id: job.id, body: { video_id: null } })}
            >
              None of these are mine
            </button>
          </>
        )}
        {(job.state === "JOB-DRAFT" || job.state === "JOB-CANCELLED") && (
          <button
            className="btn"
            disabled={actions.remove.isPending}
            onClick={() => actions.remove.mutate(job.id)}
          >
            Delete
          </button>
        )}
      </div>
      {job.state === "JOB-NEEDS_REVIEW" && (
        <p className="mt-2 text-xs text-cue-attention">
          The outcome of the last upload could not be proven. Hexton will not retry it
          automatically, because a second attempt could create a duplicate video.
        </p>
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
