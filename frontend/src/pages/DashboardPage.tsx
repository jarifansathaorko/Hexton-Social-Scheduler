import { Link } from "react-router-dom";
import type { Job, JobState } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import {
  useConnections,
  useDashboard,
  useJobs,
  useSheetsHealth,
  useWorkerStatus,
} from "../hooks/api";
import { REMOTE_NAMES, STATE_NAMES, formatWhen } from "../lib/state";

/**
 * Dashboard.
 *
 * Answers the questions an operator opens the app with: what needs me, what is moving,
 * what is coming, and whether the two things that make it all real — the worker and
 * the last report sync — are healthy.
 *
 * The two figures that are easiest to conflate are deliberately kept apart:
 * **uploads completed** is a local fact about a finished transfer, and **publication
 * verified** is a remote fact about what YouTube actually did, reached only through a
 * persisted video ID plus observed evidence. A transfer that finished is not a video
 * anyone can watch, and a dashboard that merges them is how an operator ends up
 * believing a Short is live while YouTube still has it private.
 *
 * Every number here comes from the backend's own durable records. Nothing is derived
 * in the browser from how fresh the data looks.
 */
export function DashboardPage() {
  const dashboard = useDashboard();
  const jobs = useJobs("", "");
  const worker = useWorkerStatus();
  const connections = useConnections();
  const sheets = useSheetsHealth();

  const data = dashboard.data;

  if (dashboard.isLoading) return <p className="text-ink-muted">Loading…</p>;
  if (!data) {
    return (
      <p role="alert" className="text-cue-warning">
        The dashboard could not be loaded. The API may not be running — check{" "}
        <Link className="underline" to="/settings">
          Settings &amp; Diagnostics
        </Link>
        .
      </p>
    );
  }

  const count = (state: JobState) => data.by_state[state] ?? 0;
  const sum = (states: JobState[]) => states.reduce((total, state) => total + count(state), 0);

  const pendingScheduled = sum(["JOB-READY", "JOB-QUEUED", "JOB-SCHEDULED"]);
  const activeUploads = count("JOB-UPLOADING");
  // A finished transfer, regardless of what YouTube has done with it yet.
  const uploadsCompleted = sum(["JOB-UPLOADED", "JOB-SCHEDULED", "JOB-PUBLISHED"]);
  // Only reachable with a persisted video ID plus verified remote evidence.
  const publicationVerified = count("JOB-PUBLISHED");
  const failedJobs = sum(["JOB-FAILED", "JOB-FAILED_RETRY", "JOB-BLOCKED", "JOB-MISSED_WINDOW"]);
  const manualReview = count("JOB-NEEDS_REVIEW");

  const recent = (jobs.data?.items ?? [])
    .filter((job) => job.uploaded_at !== null)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 8);

  const verifiedChannels = (connections.data ?? []).flatMap((connection) =>
    connection.channels.filter((channel) => channel.operator_confirmed_at !== null),
  );

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {data.total} job{data.total === 1 ? "" : "s"} on this PC ·{" "}
            {count("JOB-DRAFT")} draft{count("JOB-DRAFT") === 1 ? "" : "s"} · quota{" "}
            {data.quota_remaining} units left until midnight Pacific
          </p>
        </div>
        <Link className="btn btn-primary" to="/new">
          Create Content
        </Link>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        <Stat label="Pending scheduled" value={pendingScheduled} />
        <Stat label="Active uploads" value={activeUploads} tone={activeUploads > 0 ? "progress" : undefined} />
        <Stat label="Uploads completed" value={uploadsCompleted} hint="Transfers finished" />
        <Stat
          label="Publication verified"
          value={publicationVerified}
          hint="Confirmed public on YouTube"
        />
        <Stat label="Failed jobs" value={failedJobs} tone={failedJobs > 0 ? "warning" : undefined} />
        <Stat
          label="Manual review"
          value={manualReview}
          tone={manualReview > 0 ? "warning" : undefined}
        />
      </div>

      <p className="rounded-md border border-edge bg-surface px-4 py-3 text-xs text-ink-muted">
        “Uploads completed” means the transfer finished. “Publication verified” means
        YouTube was observed to have made it public. A video is never counted as
        published on the strength of an upload response alone.
      </p>

      <div className="grid gap-6 xl:grid-cols-3">
        <Panel
          title="Upcoming publication times"
          empty="Nothing is scheduled."
          jobs={data.upcoming}
          showTime
        />
        <Panel
          title="Recent upload outcomes"
          empty="Nothing has been uploaded yet."
          jobs={recent}
          showUploaded
        />
        <section className="card p-4">
          <h2 className="text-base font-semibold">Needs attention</h2>
          {data.needs_attention.length === 0 ? (
            <p className="mt-3 text-sm text-ink-faint">
              Nothing is blocked or waiting on you.
            </p>
          ) : (
            <ul className="mt-3 flex flex-col gap-3">
              {data.needs_attention.map((job) => (
                <li key={job.id} className="border-b border-edge/50 pb-3 last:border-0 last:pb-0">
                  <Link
                    to={`/calendar?job=${job.id}`}
                    className="font-medium text-link hover:underline"
                  >
                    {job.title || "(untitled)"}
                  </Link>
                  <div className="mt-1 flex items-center justify-between gap-3">
                    <span className="text-xs text-ink-faint">
                      {job.publish_at_utc ? formatWhen(job.publish_at_utc) : "No time set"}
                    </span>
                    <StatusBadge job={job} compact />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="card p-4">
          <h2 className="text-base font-semibold">Connected channels</h2>
          {verifiedChannels.length === 0 ? (
            <p className="mt-3 text-sm text-ink-faint">
              No channel has been resolved and confirmed yet.{" "}
              <Link className="text-link underline" to="/channels">
                Open YouTube Channels
              </Link>
              .
            </p>
          ) : (
            <ul className="mt-3 flex flex-col gap-2">
              {verifiedChannels.map((channel) => (
                <li key={channel.id} className="flex items-center justify-between gap-2 text-sm">
                  <span className="truncate font-medium">{channel.title}</span>
                  <span className="shrink-0 text-xs text-cue-success">verified</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-ink-faint">
            {(connections.data ?? []).length} connection
            {(connections.data ?? []).length === 1 ? "" : "s"} authorized.
          </p>
        </section>

        <section className="card p-4">
          <h2 className="text-base font-semibold">Worker health</h2>
          <div
            className={`mt-3 rounded-md border px-3 py-2 text-sm ${
              worker.data?.alive
                ? "border-cue-success/40 bg-cue-success/10 text-cue-success"
                : "border-cue-attention/50 bg-cue-attention/10 text-cue-attention"
            }`}
          >
            {worker.data?.alive
              ? `Running — heartbeat from ${worker.data.owner ?? "an unknown process"}`
              : "Not running — queued jobs are parked, not progressing"}
          </div>
          <dl className="mt-3 space-y-1 text-xs text-ink-muted">
            <div className="flex justify-between gap-3">
              <dt>Last heartbeat</dt>
              <dd>{formatWhen(worker.data?.last_heartbeat_at ?? null)}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt>Lease</dt>
              <dd>{worker.data?.lease_seconds ?? "—"}s</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt>Poll interval</dt>
              <dd>{worker.data?.poll_seconds ?? "—"}s</dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-ink-faint">
            Start it with <code className="font-mono">hexton serve</code>. It is a
            separate process on purpose: closing this tab never stops a schedule.
          </p>
        </section>

        <section className="card p-4">
          <h2 className="text-base font-semibold">Google Sheets sync</h2>
          {!sheets.data ? (
            <p className="mt-3 text-sm text-ink-faint">Reporting health unavailable.</p>
          ) : !sheets.data.configured ? (
            <p className="mt-3 text-sm text-ink-faint">
              Not configured.{" "}
              <Link className="text-link underline" to="/reports">
                Set up reporting
              </Link>
              .
            </p>
          ) : (
            <>
              <div className="mt-3 text-sm">
                {sheets.data.last_sync_at ? (
                  <>
                    <span className="text-cue-success">Last successful sync</span>{" "}
                    {formatWhen(sheets.data.last_sync_at)}
                  </>
                ) : (
                  <span className="text-cue-attention">No sync has completed yet</span>
                )}
              </div>
              <dl className="mt-3 space-y-1 text-xs text-ink-muted">
                <div className="flex justify-between gap-3">
                  <dt>Rows delivered</dt>
                  <dd>{sheets.data.outbox_synced}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt>Waiting</dt>
                  <dd>{sheets.data.outbox_pending}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt>Did not go through</dt>
                  <dd className={sheets.data.outbox_failed > 0 ? "text-cue-attention" : undefined}>
                    {sheets.data.outbox_failed}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt>Unconfirmed outcome</dt>
                  <dd className={sheets.data.outbox_ambiguous > 0 ? "text-cue-attention" : undefined}>
                    {sheets.data.outbox_ambiguous}
                  </dd>
                </div>
              </dl>
              {sheets.data.last_sync_error && (
                <p className="mt-2 text-xs text-cue-attention">{sheets.data.last_sync_error}</p>
              )}
            </>
          )}
        </section>
      </div>

      <section>
        <h2 className="text-base font-semibold">All states</h2>
        <ul className="mt-3 grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {(Object.entries(data.by_state) as [JobState, number][])
            .filter(([, total]) => total > 0)
            .map(([state, total]) => (
              <li
                key={state}
                className="card flex items-center justify-between px-3 py-2 text-sm"
              >
                <span className="text-ink-muted">{STATE_NAMES[state] ?? state}</span>
                <span className="tabular-nums">{total}</span>
              </li>
            ))}
          {Object.values(data.by_state).every((total) => total === 0) && (
            <li className="text-sm text-ink-faint">No jobs yet.</li>
          )}
        </ul>
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: number;
  hint?: string;
  tone?: "warning" | "progress";
}) {
  const toneClass =
    tone === "warning"
      ? "text-cue-warning"
      : tone === "progress"
        ? "text-cue-progress"
        : "text-ink";
  // The card is named by its own label and value so the figure is readable as one
  // phrase by assistive technology — and so a test can assert the *number* rather
  // than counting pixels next to some text.
  return (
    <div className="card px-4 py-3" role="group" aria-label={`${label}: ${value}`}>
      <div className="text-xs uppercase tracking-wide text-ink-faint">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`} aria-hidden="true">
        {value}
      </div>
      {hint && <div className="mt-0.5 text-[11px] text-ink-faint">{hint}</div>}
    </div>
  );
}

function Panel({
  title,
  empty,
  jobs,
  showTime,
  showUploaded,
}: {
  title: string;
  empty: string;
  jobs: Job[];
  showTime?: boolean;
  showUploaded?: boolean;
}) {
  return (
    <section className="card p-4">
      <h2 className="text-base font-semibold">{title}</h2>
      {jobs.length === 0 ? (
        <p className="mt-3 text-sm text-ink-faint">{empty}</p>
      ) : (
        <ul className="mt-3 flex flex-col gap-3">
          {jobs.map((job) => (
            <li key={job.id} className="border-b border-edge/50 pb-3 last:border-0 last:pb-0">
              <Link
                to={`/calendar?job=${job.id}`}
                className="font-medium text-link hover:underline"
              >
                {job.title || "(untitled)"}
              </Link>
              <div className="mt-1 flex items-center justify-between gap-3">
                <span className="text-xs text-ink-faint">
                  {showTime &&
                    (job.publish_at_utc ? formatWhen(job.publish_at_utc) : "No time set")}
                  {showUploaded &&
                    (job.uploaded_at
                      ? `Uploaded ${formatWhen(job.uploaded_at)}`
                      : "Not uploaded")}
                </span>
                <StatusBadge job={job} compact />
              </div>
              <div className="mt-0.5 text-[11px] text-ink-faint">
                YouTube: {REMOTE_NAMES[job.remote_state]}
                {job.remote_privacy_status ? ` · ${job.remote_privacy_status}` : ""}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
