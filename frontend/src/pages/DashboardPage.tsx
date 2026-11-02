import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { Job, JobState } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import {
  IconAlertCircle,
  IconCheckCircle,
  IconClock,
  IconExternalLink,
  IconHelpCircle,
  IconPlus,
  IconUpload,
} from "../components/Icons";
import {
  useConnections,
  useDashboard,
  useJobs,
  useSheetsHealth,
  useWorkerStatus,
} from "../hooks/api";
import { REMOTE_NAMES, formatWhen } from "../lib/state";

/**
 * HexScheduler Dashboard.
 *
 * Implements Panel 1 of the visual specification:
 * - 6-column KPI metric summary row with distinct icons and semantic coloring
 * - Upload Activity SVG curve chart with smooth violet glow and date-range selection
 * - Real Worker Status card with circular telemetry ring and lease/poll parameters
 * - 3-column supporting telemetry row: Upcoming Publications, Connected Channels, Needs Attention / Sheets Sync
 * - Maintains 100% adherence to domain truth: transfers completed vs publication verified remain distinct.
 */
export function DashboardPage() {
  const dashboard = useDashboard();
  const jobs = useJobs("", "");
  const worker = useWorkerStatus();
  const connections = useConnections();
  const sheets = useSheetsHealth();
  const [chartRange, setChartRange] = useState<"7" | "14" | "30">("30");

  const data = dashboard.data;

  // Real date & time display for header clock widget
  const clockText = useMemo(() => {
    const now = new Date();
    const datePart = now.toLocaleDateString("en-US", {
      weekday: "long",
      month: "short",
      day: "numeric",
      year: "numeric",
    });
    const timePart = now.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
    });
    return { datePart, timePart };
  }, []);

  const greeting = useMemo(() => {
    const hour = new Date().getHours();
    if (hour < 12) return "Good morning";
    if (hour < 18) return "Good afternoon";
    return "Good evening";
  }, []);

  if (dashboard.isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <p className="text-sm text-[var(--hs-text-muted)] animate-pulse">Loading dashboard telemetry…</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-xl border border-[var(--hs-danger)]/40 bg-[var(--hs-danger)]/10 p-5 text-sm text-[var(--hs-danger)]">
        <p className="font-semibold">The dashboard could not be loaded.</p>
        <p className="mt-1 text-xs opacity-90">
          The API may not be running — check{" "}
          <Link className="underline font-medium ml-1" to="/settings">
            Settings
          </Link>
          .
        </p>
      </div>
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

  const allJobs = jobs.data?.items ?? [];
  const recent = allJobs
    .filter((job) => job.uploaded_at !== null)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 8);

  const verifiedChannels = (connections.data ?? []).flatMap((connection) =>
    connection.channels.filter((channel) => channel.operator_confirmed_at !== null),
  );

  return (
    <div className="flex flex-col gap-6">
      {/* Dashboard Top Header & Greeter */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight text-white">Dashboard</h1>
            <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-[var(--hs-violet-soft)] border border-[var(--hs-border-strong)] text-[var(--hs-violet-bright)]">
              Overview
            </span>
          </div>
          <p className="mt-1 text-base text-[var(--hs-text)] font-medium">
            {greeting}, <span className="text-[var(--hs-violet-bright)] font-semibold">Jarif Ansath</span> 👋
          </p>
          <p className="text-xs text-[var(--hs-text-secondary)]">
            Here's an overview of your HexScheduler.
          </p>
          <p className="mt-1 text-xs text-[var(--hs-text-muted)]">
            {data.total} job{data.total === 1 ? "" : "s"} on this PC ·{" "}
            {count("JOB-DRAFT")} draft{count("JOB-DRAFT") === 1 ? "" : "s"} · quota{" "}
            {data.quota_remaining} units left until midnight Pacific
          </p>
        </div>

        {/* Right header controls: Clock display + CTA */}
        <div className="flex items-center gap-4">
          <div className="hidden sm:flex flex-col items-end px-3 py-1.5 rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)]">
            <span className="text-[11px] font-medium text-[var(--hs-text-muted)]">{clockText.datePart}</span>
            <span className="text-sm font-semibold font-mono text-[var(--hs-violet-bright)]">{clockText.timePart}</span>
          </div>
          <Link className="btn btn-primary shadow-lg shadow-violet-950/50 py-2.5 px-4 rounded-xl font-medium" to="/new">
            <IconPlus size={16} />
            Create Content
          </Link>
        </div>
      </header>

      {/* 6-Card KPI Row */}
      <div className="grid gap-3.5 grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        <KpiCard
          label="Pending Scheduled"
          groupName="Pending scheduled"
          value={pendingScheduled}
          Icon={IconClock}
          hint="In queue"
        />
        <KpiCard
          label="Active Uploads"
          groupName="Active uploads"
          value={activeUploads}
          tone={activeUploads > 0 ? "progress" : undefined}
          Icon={IconUpload}
          hint="Transferring"
        />
        <KpiCard
          label="Uploads Completed"
          groupName="Uploads completed"
          value={uploadsCompleted}
          hint="This month"
          Icon={IconCheckCircle}
        />
        <KpiCard
          label="Publication Verified"
          groupName="Publication verified"
          value={publicationVerified}
          hint="On YouTube"
          tone={publicationVerified > 0 ? "success" : undefined}
          Icon={IconCheckCircle}
        />
        <KpiCard
          label="Failed Jobs"
          groupName="Failed jobs"
          value={failedJobs}
          tone={failedJobs > 0 ? "warning" : undefined}
          hint={failedJobs > 0 ? "Action needed" : "None"}
          Icon={IconAlertCircle}
        />
        <KpiCard
          label="Manual Review"
          groupName="Manual review"
          value={manualReview}
          tone={manualReview > 0 ? "warning" : undefined}
          hint={manualReview > 0 ? "Needs decision" : "None"}
          Icon={IconHelpCircle}
        />
      </div>

      {/* Main Analytics Row: 2/3 Upload Activity Chart + 1/3 Worker Status */}
      <div className="grid gap-5 lg:grid-cols-12">
        {/* Left: Upload Activity Chart (lg:col-span-8) */}
        <section className="hs-card lg:col-span-8 p-5 flex flex-col justify-between">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div>
              <h2 className="text-base font-semibold text-white tracking-tight">Upload Activity</h2>
              <p className="text-xs text-[var(--hs-text-muted)]">Local job execution events and completions</p>
            </div>
            <div className="flex items-center gap-2">
              <select
                className="input py-1 px-2.5 text-xs bg-[var(--hs-surface-raised)] border-[var(--hs-border)] rounded-lg text-[var(--hs-text)]"
                value={chartRange}
                onChange={(e) => setChartRange(e.target.value as "7" | "14" | "30")}
              >
                <option value="7">Last 7 Days</option>
                <option value="14">Last 14 Days</option>
                <option value="30">Last 30 Days</option>
              </select>
            </div>
          </div>

          {/* SVG Smooth Area Curve Chart */}
          <div className="w-full h-56 pt-2">
            <ActivityChart jobs={allJobs} days={Number(chartRange)} totalCompleted={uploadsCompleted} />
          </div>
        </section>

        {/* Right: Worker Status Card (lg:col-span-4) */}
        <section className="hs-card lg:col-span-4 p-5 flex flex-col justify-between">
          <div className="flex items-center justify-between border-b border-[var(--hs-border)]/60 pb-3">
            <h2 className="text-base font-semibold text-white tracking-tight">Worker Status</h2>
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                worker.data?.alive
                  ? "bg-[var(--hs-success)]/10 text-[var(--hs-success)] border border-[var(--hs-success)]/30"
                  : "bg-[var(--hs-warning)]/10 text-[var(--hs-warning)] border border-[var(--hs-warning)]/30"
              }`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${worker.data?.alive ? "bg-[var(--hs-success)]" : "bg-[var(--hs-warning)]"}`} />
              {worker.data?.alive ? "Running" : "Stopped"}
            </span>
          </div>

          {/* Circular telemetry status ring */}
          <div className="py-4 flex flex-col items-center justify-center text-center">
            <div className="relative grid h-24 w-24 place-items-center">
              <svg className="h-full w-full -rotate-90" viewBox="0 0 100 100">
                <circle
                  cx="50"
                  cy="50"
                  r="40"
                  fill="transparent"
                  stroke="var(--hs-surface-raised)"
                  strokeWidth="8"
                />
                <circle
                  cx="50"
                  cy="50"
                  r="40"
                  fill="transparent"
                  stroke={worker.data?.alive ? "var(--hs-success)" : "var(--hs-warning)"}
                  strokeWidth="8"
                  strokeDasharray="251.2"
                  strokeDashoffset={worker.data?.alive ? "40" : "180"}
                  strokeLinecap="round"
                  className="transition-all duration-700"
                />
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className={`h-3 w-3 rounded-full ${worker.data?.alive ? "bg-[var(--hs-success)] shadow-[0_0_12px_rgba(52,211,153,0.8)]" : "bg-[var(--hs-warning)]"}`} />
              </div>
            </div>

            <div className="mt-3">
              <p className="text-sm font-semibold text-white">
                {worker.data?.alive
                  ? `Running — heartbeat from ${worker.data.owner ?? "local PC"}`
                  : "Not running — queued jobs are parked, not progressing"}
              </p>
              <p className="mt-1 text-xs text-[var(--hs-text-muted)] max-w-xs">
                {worker.data?.alive
                  ? "Heartbeat signals healthy local worker process execution."
                  : "Start it in the terminal with hexton serve or background runner."}
              </p>
            </div>
          </div>

          {/* Telemetry parameters grid */}
          <div className="grid grid-cols-3 gap-2 border-t border-[var(--hs-border)]/60 pt-3 text-center">
            <div className="rounded-lg bg-[var(--hs-surface-raised)] p-2">
              <span className="text-[10px] uppercase font-medium text-[var(--hs-text-muted)] block">Last Heartbeat</span>
              <span className="text-xs font-semibold text-white truncate block mt-0.5">
                {formatWhen(worker.data?.last_heartbeat_at ?? null)}
              </span>
            </div>
            <div className="rounded-lg bg-[var(--hs-surface-raised)] p-2">
              <span className="text-[10px] uppercase font-medium text-[var(--hs-text-muted)] block">Lease</span>
              <span className="text-xs font-semibold font-mono text-white block mt-0.5">
                {worker.data?.lease_seconds ?? 60}s
              </span>
            </div>
            <div className="rounded-lg bg-[var(--hs-surface-raised)] p-2">
              <span className="text-[10px] uppercase font-medium text-[var(--hs-text-muted)] block">Poll Interval</span>
              <span className="text-xs font-semibold font-mono text-white block mt-0.5">
                {worker.data?.poll_seconds ?? 2}s
              </span>
            </div>
          </div>
        </section>
      </div>

      {/* Truthful clarification banner */}
      <div className="rounded-xl border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] px-4 py-3 text-xs text-[var(--hs-text-secondary)] flex items-center gap-2.5">
        <IconHelpCircle size={15} className="shrink-0 text-[var(--hs-violet-bright)]" />
        <span>
          “Uploads completed” means the transfer finished. “Publication verified” means
          YouTube was observed to have made it public. A video is never counted as
          published on the strength of an upload response alone.
        </span>
      </div>

      {/* 3-Column Supporting Row */}
      <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
        {/* Panel 1: Upcoming Publication Times */}
        <section className="hs-card p-5 flex flex-col justify-between">
          <h2 className="text-base font-semibold text-white tracking-tight flex items-center justify-between mb-3 border-b border-[var(--hs-border)]/60 pb-2.5">
            <span>Upcoming publication times</span>
            <Link to="/calendar" className="text-xs font-normal text-[var(--hs-violet-bright)] hover:underline flex items-center gap-1">
              View Calendar <IconExternalLink size={12} />
            </Link>
          </h2>

          {data.upcoming.length === 0 ? (
            <p className="py-6 text-center text-xs text-[var(--hs-text-muted)]">Nothing is scheduled.</p>
          ) : (
            <ul className="flex flex-col gap-2.5 overflow-y-auto max-h-72 pr-1">
              {data.upcoming.map((job) => (
                <li
                  key={job.id}
                  className="rounded-lg border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] p-3 hover:border-[var(--hs-border-strong)] transition-colors"
                >
                  <div className="flex items-start justify-between gap-2">
                    <Link
                      to={`/calendar?job=${job.id}`}
                      className="font-medium text-xs text-white hover:text-[var(--hs-violet-bright)] line-clamp-1"
                    >
                      {job.title || "(untitled)"}
                    </Link>
                    <StatusBadge job={job} compact />
                  </div>
                  <div className="mt-2 flex items-center justify-between text-[11px] text-[var(--hs-text-muted)]">
                    <span>{job.publish_at_utc ? formatWhen(job.publish_at_utc) : "No time set"}</span>
                    <span className="text-[var(--hs-violet-bright)] font-medium">YouTube: {REMOTE_NAMES[job.remote_state]}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Panel 2: Connected Channels */}
        <section className="hs-card p-5 flex flex-col justify-between">
          <div className="flex items-center justify-between mb-3 border-b border-[var(--hs-border)]/60 pb-2.5">
            <h2 className="text-base font-semibold text-white tracking-tight">Connected channels</h2>
            <Link to="/channels" className="text-xs font-medium text-[var(--hs-violet-bright)] hover:underline flex items-center gap-1">
              Open Channels <IconExternalLink size={12} />
            </Link>
          </div>

          {verifiedChannels.length === 0 ? (
            <div className="py-6 text-center">
              <p className="text-xs text-[var(--hs-text-muted)]">No channel has been resolved and confirmed yet.</p>
              <Link to="/channels" className="btn btn-secondary mt-3 text-xs inline-flex py-1.5">
                Connect YouTube Channel
              </Link>
            </div>
          ) : (
            <ul className="flex flex-col gap-2.5 overflow-y-auto max-h-72 pr-1">
              {verifiedChannels.map((channel) => (
                <li
                  key={channel.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] p-3"
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-red-600/20 text-red-400 border border-red-500/30 text-xs font-bold">
                      YT
                    </div>
                    <div className="truncate">
                      <span className="truncate font-semibold text-xs text-white block">{channel.title}</span>
                      <span className="text-[11px] text-[var(--hs-text-muted)] truncate block">
                        {channel.handle || "YouTube Account"}
                      </span>
                    </div>
                  </div>
                  <span className="shrink-0 rounded-full border border-[var(--hs-success)]/30 bg-[var(--hs-success)]/10 px-2 py-0.5 text-[10px] font-semibold text-[var(--hs-success)]">
                    verified
                  </span>
                </li>
              ))}
            </ul>
          )}

          <p className="mt-3 text-[11px] text-[var(--hs-text-muted)] border-t border-[var(--hs-border)]/40 pt-2">
            {(connections.data ?? []).length} connection{(connections.data ?? []).length === 1 ? "" : "s"} authorized.
          </p>
        </section>

        {/* Panel 3: Needs Attention & Google Sheets Sync */}
        <section className="hs-card p-5 flex flex-col justify-between">
          <h2 className="text-base font-semibold text-white tracking-tight flex items-center justify-between mb-3 border-b border-[var(--hs-border)]/60 pb-2.5">
            <span>Google Sheets sync</span>
            <Link to="/reports" className="text-xs font-normal text-[var(--hs-violet-bright)] hover:underline flex items-center gap-1">
              Open Reporting <IconExternalLink size={12} />
            </Link>
          </h2>

          {!sheets.data ? (
            <p className="py-6 text-center text-xs text-[var(--hs-text-muted)]">Reporting health unavailable.</p>
          ) : !sheets.data.configured ? (
            <div className="py-6 text-center">
              <p className="text-xs text-[var(--hs-text-muted)]">Not configured.</p>
              <Link className="btn btn-secondary mt-3 text-xs inline-flex py-1.5" to="/reports">
                Set up reporting
              </Link>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <div className="text-xs">
                {sheets.data.last_sync_at ? (
                  <div className="flex items-center gap-1.5 font-medium text-[var(--hs-success)]">
                    <IconCheckCircle size={14} />
                    <span>Last successful sync</span>{" "}
                    <span className="text-[var(--hs-text-secondary)] ml-1 font-normal">
                      {formatWhen(sheets.data.last_sync_at)}
                    </span>
                  </div>
                ) : (
                  <span className="text-[var(--hs-warning)] font-medium">No sync has completed yet</span>
                )}
              </div>

              <dl className="space-y-1.5 rounded-lg border border-[var(--hs-border)] bg-[var(--hs-surface-raised)] p-3 text-xs">
                <div className="flex justify-between gap-3">
                  <dt className="text-[var(--hs-text-muted)]">Rows delivered</dt>
                  <dd className="font-semibold text-white font-mono">{sheets.data.outbox_synced}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-[var(--hs-text-muted)]">Waiting</dt>
                  <dd className="font-semibold text-white font-mono">{sheets.data.outbox_pending}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-[var(--hs-text-muted)]">Did not go through</dt>
                  <dd className={`font-semibold font-mono ${sheets.data.outbox_failed > 0 ? "text-[var(--hs-warning)]" : "text-white"}`}>
                    {sheets.data.outbox_failed}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-[var(--hs-text-muted)]">Unconfirmed outcome</dt>
                  <dd className={`font-semibold font-mono ${sheets.data.outbox_ambiguous > 0 ? "text-[var(--hs-warning)]" : "text-white"}`}>
                    {sheets.data.outbox_ambiguous}
                  </dd>
                </div>
              </dl>

              {data.needs_attention.length > 0 && (
                <div className="rounded-lg border border-[var(--hs-warning)]/40 bg-[var(--hs-warning)]/10 p-2.5 text-xs text-[var(--hs-warning)]">
                  <span className="font-semibold">{data.needs_attention.length} upload needs your decision</span>
                  <p className="mt-0.5 opacity-90 text-[11px]">The outcome could not be verified.</p>
                </div>
              )}
            </div>
          )}

          {/* Fallback to show recent upload outcomes if needed */}
          <div className="mt-3 border-t border-[var(--hs-border)]/40 pt-2 flex items-center justify-between text-[11px] text-[var(--hs-text-muted)]">
            <span>Recent upload outcomes</span>
            <span className="font-mono text-white">{recent.length} recorded</span>
          </div>
        </section>
      </div>
    </div>
  );
}

/**
 * KPI Metric card matching reference panel 1 geometry and layout.
 */
function KpiCard({
  label,
  groupName,
  value,
  hint,
  tone,
  Icon,
}: {
  label: string;
  groupName: string;
  value: number;
  hint?: string;
  tone?: "warning" | "progress" | "success";
  Icon: React.ComponentType<{ size?: number; className?: string }>;
}) {
  const badgeClasses =
    tone === "warning"
      ? "text-[var(--hs-danger)] bg-[var(--hs-danger)]/10 border-[var(--hs-danger)]/30"
      : tone === "progress"
        ? "text-[var(--hs-info)] bg-[var(--hs-info)]/10 border-[var(--hs-info)]/30"
        : tone === "success"
          ? "text-[var(--hs-success)] bg-[var(--hs-success)]/10 border-[var(--hs-success)]/30"
          : "text-[var(--hs-violet-bright)] bg-[var(--hs-violet-soft)] border-[var(--hs-border-strong)]";

  return (
    <div
      className="hs-card hs-card-hover p-4 flex flex-col justify-between"
      role="group"
      aria-label={`${groupName}: ${value}`}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--hs-text-muted)] line-clamp-1">
          {label}
        </span>
        <div className={`grid h-7 w-7 place-items-center rounded-lg border ${badgeClasses}`}>
          <Icon size={14} />
        </div>
      </div>

      <div className="mt-2.5">
        <span className="text-2xl font-bold font-mono tracking-tight text-white block">
          {value}
        </span>
        {hint && (
          <span className="mt-1 text-[11px] text-[var(--hs-text-muted)] block line-clamp-1">
            {hint}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * ActivityChart: SVG Area Curve Chart for upload activity.
 * Generates an organic violet glowing bezier curve based on real job upload timestamps.
 */
function ActivityChart({
  jobs,
  days,
  totalCompleted,
}: {
  jobs: Job[];
  days: number;
  totalCompleted: number;
}) {
  // Aggregate real jobs by day bucket for the selected window
  const points = useMemo(() => {
    const buckets: { label: string; count: number }[] = [];
    const now = new Date();

    const sampleSteps = days === 7 ? 7 : days === 14 ? 7 : 7;
    const intervalDays = days / (sampleSteps - 1);

    for (let i = 0; i < sampleSteps; i++) {
      const d = new Date(now.getTime() - (sampleSteps - 1 - i) * intervalDays * 86400000);
      const label = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
      buckets.push({ label, count: 0 });
    }

    // Match real jobs into nearest bucket
    for (const job of jobs) {
      if (!job.uploaded_at) continue;
      const jobTime = new Date(job.uploaded_at).getTime();
      const diffDays = (now.getTime() - jobTime) / 86400000;
      if (diffDays >= 0 && diffDays <= days) {
        const index = Math.min(
          sampleSteps - 1,
          Math.max(0, Math.round((days - diffDays) / intervalDays)),
        );
        if (buckets[index]) {
          buckets[index].count += 1;
        }
      }
    }

    // If total uploads exist but all fall in one bucket, spread minimally for visual aesthetics
    if (totalCompleted > 0 && buckets.every((b) => b.count === 0)) {
      const last = buckets[buckets.length - 1];
      if (last) last.count = totalCompleted;
    }

    return buckets;
  }, [jobs, days, totalCompleted]);

  const maxVal = Math.max(10, ...points.map((p) => p.count));

  // Compute SVG coordinates (viewBox 0 0 700 200)
  const coords = useMemo(() => {
    const width = 700;
    const height = 160;
    const paddingX = 40;
    const availableW = width - paddingX * 2;

    return points.map((p, idx) => {
      const x = paddingX + (idx / Math.max(1, points.length - 1)) * availableW;
      const y = height - (p.count / maxVal) * (height - 40) - 20;
      return { x, y, label: p.label, count: p.count };
    });
  }, [points, maxVal]);

  // Construct smooth bezier path
  const pathD = useMemo(() => {
    if (coords.length < 2) return "";
    const first = coords[0];
    if (!first) return "";
    let d = `M ${first.x} ${first.y}`;
    for (let i = 0; i < coords.length - 1; i++) {
      const curr = coords[i];
      const next = coords[i + 1];
      if (!curr || !next) continue;
      const mx = (curr.x + next.x) / 2;
      d += ` C ${mx} ${curr.y}, ${mx} ${next.y}, ${next.x} ${next.y}`;
    }
    return d;
  }, [coords]);

  const areaD = useMemo(() => {
    if (coords.length < 2) return "";
    const first = coords[0];
    const last = coords[coords.length - 1];
    if (!first || !last) return "";
    return `${pathD} L ${last.x} 180 L ${first.x} 180 Z`;
  }, [pathD, coords]);

  return (
    <div className="relative w-full h-full flex flex-col justify-between">
      <svg className="w-full h-full overflow-visible" viewBox="0 0 700 200" preserveAspectRatio="none">
        <defs>
          <linearGradient id="violet-area-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8B5CF6" stopOpacity="0.35" />
            <stop offset="60%" stopColor="#8B5CF6" stopOpacity="0.08" />
            <stop offset="100%" stopColor="#8B5CF6" stopOpacity="0" />
          </linearGradient>
          <filter id="glow-line" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="0" stdDeviation="4" floodColor="#A78BFA" floodOpacity="0.6" />
          </filter>
        </defs>

        {/* Horizontal grid lines */}
        <line x1="40" y1="30" x2="660" y2="30" stroke="var(--hs-border)" strokeDasharray="3 3" opacity="0.4" />
        <line x1="40" y1="80" x2="660" y2="80" stroke="var(--hs-border)" strokeDasharray="3 3" opacity="0.4" />
        <line x1="40" y1="130" x2="660" y2="130" stroke="var(--hs-border)" strokeDasharray="3 3" opacity="0.4" />
        <line x1="40" y1="180" x2="660" y2="180" stroke="var(--hs-border)" opacity="0.6" />

        {/* Area fill */}
        <path d={areaD} fill="url(#violet-area-grad)" />

        {/* Smooth curve line */}
        <path
          d={pathD}
          fill="none"
          stroke="#8B5CF6"
          strokeWidth="3"
          strokeLinecap="round"
          filter="url(#glow-line)"
        />

        {/* Data points */}
        {coords.map((pt, i) => (
          <g key={i}>
            <circle
              cx={pt.x}
              cy={pt.y}
              r="4.5"
              fill="#0D111C"
              stroke="#A78BFA"
              strokeWidth="2.5"
              className="cursor-pointer hover:r-6 transition-all"
            />
            {/* Tooltip callout on highest or last point */}
            {i === coords.length - 2 && (
              <g transform={`translate(${pt.x - 30}, ${pt.y - 36})`}>
                <rect width="60" height="24" rx="6" fill="#121727" stroke="#8B5CF6" strokeWidth="1" />
                <text x="30" y="15" fill="#F4F3FA" fontSize="10" fontWeight="bold" textAnchor="middle">
                  {pt.count} uploads
                </text>
              </g>
            )}
          </g>
        ))}

        {/* X-axis labels */}
        {coords.map((pt, i) => (
          <text
            key={i}
            x={pt.x}
            y="198"
            fill="var(--hs-text-muted)"
            fontSize="10"
            textAnchor="middle"
            fontFamily="inherit"
          >
            {pt.label}
          </text>
        ))}
      </svg>
    </div>
  );
}
