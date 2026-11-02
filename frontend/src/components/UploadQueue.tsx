import { useState } from "react";
import type { Job, JobList } from "../api/types";
import { ProgressBar, StatusBadge } from "./StatusBadge";
import { MENTION_NAMES, REMOTE_NAMES, formatBytes, formatWhen } from "../lib/state";
import { IconExternalLink, IconPlay } from "./Icons";

/**
 * UploadQueue Component matching Panel 3 of the HexScheduler visual specification:
 * - Segmented tabs with genuine live counts: In Progress, Needs Decision, Recently Finished
 * - Rich job cards with 16:9 thumbnail preview tiles, progress bars, schedule IDs, and retry telemetry
 * - Unmistakable safety protocol for uncertain upload outcomes
 */
export function UploadQueue({
  data,
  onSelect,
}: {
  data: JobList | undefined;
  onSelect: (id: number) => void;
}) {
  const [activeTab, setActiveTab] = useState<"all" | "in_progress" | "uncertain" | "finished">("all");

  const all = data?.items ?? [];
  const active = all.filter((j) => j.state === "JOB-UPLOADING" || j.state === "JOB-QUEUED");
  const uncertain = all.filter((j) => j.state === "JOB-NEEDS_REVIEW" || j.insert_may_have_succeeded);
  const recentlyFinished = all
    .filter(
      (j) =>
        j.state === "JOB-UPLOADED" ||
        j.state === "JOB-SCHEDULED" ||
        j.state === "JOB-PUBLISHED" ||
        j.state === "JOB-FAILED_RETRY" ||
        j.state === "JOB-BLOCKED" ||
        j.state === "JOB-FAILED" ||
        j.state === "JOB-MISSED_WINDOW",
    )
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 25);

  return (
    <div className="flex flex-col gap-6">
      {/* Segmented Filter Tabs matching Panel 3 */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          className={`flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-semibold transition-all ${
            activeTab === "all"
              ? "bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] border border-[var(--hs-border-strong)] shadow-sm"
              : "bg-[var(--hs-surface-raised)] text-[var(--hs-text-secondary)] border border-[var(--hs-border)] hover:text-white"
          }`}
          onClick={() => setActiveTab("all")}
        >
          <span>All Items</span>
          <span className="rounded-full bg-[var(--hs-surface-soft)] px-2 py-0.5 text-[10px] font-mono">
            {all.length}
          </span>
        </button>

        <button
          className={`flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-semibold transition-all ${
            activeTab === "in_progress"
              ? "bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] border border-[var(--hs-border-strong)] shadow-sm"
              : "bg-[var(--hs-surface-raised)] text-[var(--hs-text-secondary)] border border-[var(--hs-border)] hover:text-white"
          }`}
          onClick={() => setActiveTab("in_progress")}
        >
          <span>In Progress</span>
          <span className="rounded-full bg-[var(--hs-surface-soft)] px-2 py-0.5 text-[10px] font-mono">
            {active.length}
          </span>
        </button>

        <button
          className={`flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-semibold transition-all ${
            activeTab === "uncertain"
              ? "bg-[var(--hs-warning)]/20 text-[var(--hs-warning)] border border-[var(--hs-warning)]/40 shadow-sm"
              : "bg-[var(--hs-surface-raised)] text-[var(--hs-text-secondary)] border border-[var(--hs-border)] hover:text-white"
          }`}
          onClick={() => setActiveTab("uncertain")}
        >
          <span>Needs Decision</span>
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-mono ${uncertain.length > 0 ? "bg-[var(--hs-warning)]/30 text-[var(--hs-warning)]" : "bg-[var(--hs-surface-soft)]"}`}>
            {uncertain.length}
          </span>
        </button>

        <button
          className={`flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-semibold transition-all ${
            activeTab === "finished"
              ? "bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] border border-[var(--hs-border-strong)] shadow-sm"
              : "bg-[var(--hs-surface-raised)] text-[var(--hs-text-secondary)] border border-[var(--hs-border)] hover:text-white"
          }`}
          onClick={() => setActiveTab("finished")}
        >
          <span>Recently Finished</span>
          <span className="rounded-full bg-[var(--hs-surface-soft)] px-2 py-0.5 text-[10px] font-mono">
            {recentlyFinished.length}
          </span>
        </button>
      </div>

      {/* Sections rendering based on tab selection */}
      {(activeTab === "all" || activeTab === "in_progress") && (
        <Section
          title="In progress"
          hint="These are being transferred now, or are waiting to be claimed by the worker."
          jobs={active}
          onSelect={onSelect}
          empty="Nothing is uploading."
          showProgress
        />
      )}

      {(activeTab === "all" || activeTab === "uncertain") && (
        <section aria-labelledby="uncertain-heading" className="hs-card p-5 border-[var(--hs-warning)]/30 bg-[var(--hs-surface)]">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-[var(--hs-warning)]" />
            <h2 id="uncertain-heading" className="text-base font-semibold text-[var(--hs-warning)] tracking-tight">
              Needs your decision
            </h2>
          </div>
          <p className="mt-1 text-xs text-[var(--hs-text-secondary)]">
            The outcome of these uploads could not be proven, so Hexton will not retry
            them automatically. Retrying one could create a duplicate video.
          </p>
          {uncertain.length === 0 ? (
            <p className="mt-4 text-xs text-[var(--hs-text-muted)]">Nothing is waiting on you.</p>
          ) : (
            <ul className="mt-4 flex flex-col gap-3">
              {uncertain.map((job) => (
                <UncertainRow key={job.id} job={job} onSelect={onSelect} />
              ))}
            </ul>
          )}
        </section>
      )}

      {(activeTab === "all" || activeTab === "finished") && (
        <Section
          title="Recently finished"
          hint="A finished transfer is not the same as a published video; the remote state is shown alongside."
          jobs={recentlyFinished}
          onSelect={onSelect}
          empty="Nothing has finished yet."
        />
      )}
    </div>
  );
}

function Section({
  title,
  hint,
  jobs,
  onSelect,
  empty,
  showProgress,
}: {
  title: string;
  hint: string;
  jobs: Job[];
  onSelect: (id: number) => void;
  empty: string;
  showProgress?: boolean;
}) {
  return (
    <section className="hs-card p-5">
      <div className="border-b border-[var(--hs-border)]/60 pb-3 mb-4">
        <h2 className="text-base font-semibold text-white tracking-tight">{title}</h2>
        <p className="mt-0.5 text-xs text-[var(--hs-text-muted)]">{hint}</p>
      </div>

      {jobs.length === 0 ? (
        <p className="py-6 text-center text-xs text-[var(--hs-text-muted)]">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {jobs.map((job) => (
            <li key={job.id} className="card hs-card-hover p-4 flex flex-col gap-3">
              <div className="flex flex-wrap items-start justify-between gap-4">
                {/* Left: Thumbnail & Title block */}
                <div className="flex items-start gap-3.5 min-w-0 flex-1">
                  {/* Thumbnail / Video Tile */}
                  <div className="relative h-16 w-28 shrink-0 rounded-lg overflow-hidden border border-[var(--hs-border)] bg-[var(--hs-surface-soft)] flex items-center justify-center group cursor-pointer"
                       onClick={() => onSelect(job.id)}>
                    <div className="absolute inset-0 bg-gradient-to-tr from-[var(--hs-violet-soft)] to-transparent opacity-60" />
                    <IconPlay size={20} className="text-[var(--hs-violet-bright)] opacity-80 group-hover:scale-110 transition-transform" />
                    {job.media_size_bytes > 0 && (
                      <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1 py-0.5 text-[9px] font-mono text-white">
                        {formatBytes(job.media_size_bytes)}
                      </span>
                    )}
                  </div>

                  {/* Title & Metadata */}
                  <div className="min-w-0 flex-1">
                    <button
                      className="truncate text-left text-sm font-semibold text-white hover:text-[var(--hs-violet-bright)] transition-colors block w-full"
                      onClick={() => onSelect(job.id)}
                      aria-label={`Open details for ${job.title || "(untitled)"}`}
                    >
                      {job.title || "(untitled)"}
                    </button>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--hs-text-muted)]">
                      <span className="text-[var(--hs-text-secondary)] font-medium">
                        {job.channel_title ?? "No channel selected"}
                      </span>
                      <span>·</span>
                      <span>{job.media_container ? `${job.media_container}` : "Video file"}</span>
                      <span>·</span>
                      <span className="font-mono text-[11px]">Schedule {job.public_id}</span>
                      {job.video_id && (
                        <>
                          <span>·</span>
                          <span className="font-mono text-[11px] text-[var(--hs-violet-bright)]">video {job.video_id}</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>

                {/* Right: Remote status & State chip */}
                <div className="flex items-center gap-4 shrink-0">
                  <div className="text-right text-xs text-[var(--hs-text-muted)] hidden sm:block">
                    <div>Publishes {formatWhen(job.publish_at_utc)}</div>
                    <div className="font-medium text-[var(--hs-text-secondary)]">YouTube: {REMOTE_NAMES[job.remote_state]}</div>
                    {job.mention_requested && <div>Mention: {MENTION_NAMES[job.mention_state]}</div>}
                  </div>
                  <StatusBadge job={job} compact />
                </div>
              </div>

              {/* Retry & Telemetry Rows */}
              <dl className="grid gap-x-6 gap-y-1.5 border-t border-[var(--hs-border)]/60 pt-3 text-xs sm:grid-cols-3">
                <div className="flex items-center gap-2">
                  <dt className="text-[var(--hs-text-muted)]">Next retry</dt>
                  <dd className="text-white font-mono">
                    {job.next_attempt_at
                      ? `${formatWhen(job.next_attempt_at)} (${job.attempt + 1}/${job.max_attempts})`
                      : "—"}
                  </dd>
                </div>
                <div className="flex items-center gap-2">
                  <dt className="text-[var(--hs-text-muted)]">Error class</dt>
                  <dd className="text-[var(--hs-text-secondary)] font-mono">{job.failure_kind ?? "—"}</dd>
                </div>
                <div className="flex items-center gap-2">
                  <dt className="text-[var(--hs-text-muted)]">Video</dt>
                  <dd className="text-[var(--hs-text-secondary)]">
                    {job.video_url ? (
                      <a
                        className="text-[var(--hs-violet-bright)] hover:underline flex items-center gap-1 font-mono"
                        href={job.video_url}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {job.video_id} <IconExternalLink size={12} />
                      </a>
                    ) : (
                      "—"
                    )}
                  </dd>
                </div>
              </dl>

              {/* Transfer Progress */}
              {showProgress && (
                <div className="mt-1">
                  <ProgressBar percent={job.progress_percent} active={job.state === "JOB-UPLOADING"} />
                  {job.state === "JOB-QUEUED" && (
                    <p className="mt-1 text-xs text-[var(--hs-text-muted)]">
                      Waiting to be claimed by the worker.
                      {job.next_attempt_at ? ` Next attempt ${formatWhen(job.next_attempt_at)}.` : ""}
                    </p>
                  )}
                </div>
              )}

              {/* Reschedule refusal failure message (F-05) */}
              {job.failure_message && (
                <p className="rounded-lg border border-[var(--hs-warning)]/30 bg-[var(--hs-warning)]/10 px-3 py-1.5 text-xs text-[var(--hs-warning)]">
                  {job.failure_message}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * UncertainRow Component:
 * Strict protocol for recovery without blind retries.
 */
function UncertainRow({ job, onSelect }: { job: Job; onSelect: (id: number) => void }) {
  return (
    <li className="card hs-card-hover border-[var(--hs-warning)]/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <button
            className="truncate text-left text-sm font-semibold text-[var(--hs-warning)] hover:underline block"
            onClick={() => onSelect(job.id)}
          >
            {job.title || "(untitled)"}
          </button>
          <p className="mt-1 font-mono text-[11px] text-[var(--hs-text-muted)]">
            Schedule {job.public_id}
            {job.video_id && ` · candidate video ${job.video_id}`}
          </p>
          <ol className="mt-2.5 max-w-2xl list-decimal space-y-1.5 pl-4 text-xs text-[var(--hs-text-secondary)]">
            <li>
              Open YouTube Studio and look for a video with this title uploaded in the last hour.
            </li>
            <li>
              Found it? Open the job and press <span className="text-white font-semibold">This video is mine</span>; paste or confirm the ID so Hexton adopts it.
            </li>
            <li>
              Not there? Press <span className="text-white font-semibold">None of these are mine</span>.
              Only that answer allows a fresh upload — nothing restarts on its own.
            </li>
          </ol>
        </div>
        <StatusBadge job={job} compact />
      </div>
    </li>
  );
}
