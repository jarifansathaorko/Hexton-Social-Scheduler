import { Link } from "react-router-dom";
import type { Job, JobState } from "../api/types";
import { useDashboard } from "../hooks/api";
import { StatusBadge } from "../components/StatusBadge";
import { STATE_NAMES, formatWhen } from "../lib/state";

/**
 * Dashboard.
 *
 * Answers the three questions an operator opens the app with: what needs me, what is
 * moving, and what is coming. The quota figure and the worker indicator are here
 * because both change how the rest of the screen should be read — a backlog with no
 * worker is parked, and a backlog with no quota is parked until midnight Pacific.
 */
export function DashboardPage() {
  const dashboard = useDashboard();
  const data = dashboard.data;

  if (dashboard.isLoading) return <p className="text-ink-muted">Loading…</p>;
  if (!data) return <p className="text-ink-muted">The dashboard could not be loaded.</p>;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {data.total} job{data.total === 1 ? "" : "s"} on this PC.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div
            className={`rounded-md border px-3 py-2 text-sm ${
              data.worker_alive
                ? "border-cue-success/40 text-cue-success"
                : "border-cue-attention/40 text-cue-attention"
            }`}
          >
            {data.worker_alive
              ? "Worker is running"
              : "Worker is not running — queued jobs will not progress"}
          </div>
          <Link className="btn btn-primary" to="/content/new">
            New content
          </Link>
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Quota remaining today" value={String(data.quota_remaining)} />
        <Stat
          label="Uploading"
          value={String(data.by_state["JOB-UPLOADING"] ?? 0)}
        />
        <Stat
          label="Scheduled on YouTube"
          value={String(data.by_state["JOB-SCHEDULED"] ?? 0)}
        />
        <Stat
          label="Needs attention"
          value={String(
            (data.by_state["JOB-NEEDS_REVIEW"] ?? 0) +
              (data.by_state["JOB-BLOCKED"] ?? 0) +
              (data.by_state["JOB-FAILED"] ?? 0),
          )}
          tone={(data.by_state["JOB-NEEDS_REVIEW"] ?? 0) > 0 ? "warning" : undefined}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Panel
          title="Needs attention"
          empty="Nothing is blocked or waiting on you."
          jobs={data.needs_attention}
        />
        <Panel title="In progress" empty="Nothing is uploading." jobs={data.uploading} />
        <Panel title="Coming up" empty="Nothing is scheduled." jobs={data.upcoming} />
      </div>

      <section>
        <h2 className="text-base font-semibold">All states</h2>
        <ul className="mt-3 grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {(Object.entries(data.by_state) as [JobState, number][])
            .filter(([, count]) => count > 0)
            .map(([state, count]) => (
              <li key={state} className="card flex items-center justify-between px-3 py-2 text-sm">
                <span className="text-ink-muted">{STATE_NAMES[state] ?? state}</span>
                <span className="tabular-nums">{count}</span>
              </li>
            ))}
          {Object.values(data.by_state).every((c) => c === 0) && (
            <li className="text-sm text-ink-faint">No jobs yet.</li>
          )}
        </ul>
      </section>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "warning" }) {
  return (
    <div className="card px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-ink-faint">{label}</div>
      <div
        className={`mt-1 text-2xl font-semibold tabular-nums ${
          tone === "warning" ? "text-cue-warning" : "text-ink"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function Panel({ title, empty, jobs }: { title: string; empty: string; jobs: Job[] }) {
  return (
    <section className="card p-4">
      <h2 className="text-base font-semibold">{title}</h2>
      {jobs.length === 0 ? (
        <p className="mt-3 text-sm text-ink-faint">{empty}</p>
      ) : (
        <ul className="mt-3 flex flex-col gap-3">
          {jobs.map((job) => (
            <li key={job.id} className="border-b border-edge/50 pb-3 last:border-0 last:pb-0">
              <Link to={`/content/${job.id}`} className="font-medium hover:text-cue-progress">
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
  );
}
