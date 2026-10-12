import { useMemo, useState } from "react";
import type { Job, JobList } from "../api/types";
import { ProgressBar, StatusBadge } from "./StatusBadge";
import {
  STATE_NAMES,
  STATE_ORDER,
  formatBytes,
  formatDuration,
  formatWhen,
  needsAttention,
} from "../lib/state";

type SortKey = "publish" | "updated" | "title";

/**
 * The content table.
 *
 * Every column the MVP is required to show is here and none is hidden behind a menu,
 * because the whole point of the screen is answering "what is happening to my video?"
 * without opening each row. The local state and the observed remote state sit
 * side by side rather than in one merged column: they are separate facts and merging
 * them is how an operator ends up believing a video is public when YouTube still has
 * it private.
 *
 * Opening a row is a callback rather than a navigation, so the table can live inside
 * another section — the calendar — without that section having to know how the table
 * addresses a selected job.
 */
export function JobTable({
  data,
  query,
  onQuery,
  state,
  onState,
  channels,
  channel,
  onChannel,
  onOpen,
}: {
  data: JobList | undefined;
  query: string;
  onQuery: (value: string) => void;
  state: string;
  onState: (value: string) => void;
  channels: { id: number; title: string }[];
  channel: string;
  onChannel: (value: string) => void;
  onOpen: (job: Job) => void;
}) {
  const [sort, setSort] = useState<SortKey>("publish");

  const rows = useMemo(() => {
    const items = [...(data?.items ?? [])];
    items.sort((a, b) => {
      if (sort === "title") return a.title.localeCompare(b.title);
      if (sort === "updated") return b.updated_at.localeCompare(a.updated_at);
      const av = a.publish_at_utc ?? "9999";
      const bv = b.publish_at_utc ?? "9999";
      return av.localeCompare(bv);
    });
    return items;
  }, [data, sort]);

  const counts = data?.states ?? {};
  const filtered = Boolean(query || state || channel);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="sr-only" htmlFor="job-search">
          Search content
        </label>
        <input
          id="job-search"
          className="input max-w-xs"
          placeholder="Search title or description…"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
        />

        <label className="sr-only" htmlFor="job-channel">
          Filter by channel
        </label>
        <select
          id="job-channel"
          className="input w-auto"
          value={channel}
          onChange={(e) => onChannel(e.target.value)}
        >
          <option value="">All channels</option>
          <option value="none">No channel selected</option>
          {channels.map((entry) => (
            <option key={entry.id} value={String(entry.id)}>
              {entry.title}
            </option>
          ))}
        </select>

        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Filter by state">
          <button
            className={`btn px-2.5 py-1 text-xs ${state === "" ? "border-accent text-accent" : ""}`}
            onClick={() => onState("")}
          >
            All ({data?.total ?? 0})
          </button>
          {STATE_ORDER.filter((s) => (counts[s] ?? 0) > 0).map((s) => (
            <button
              key={s}
              className={`btn px-2.5 py-1 text-xs ${state === s ? "border-accent text-accent" : ""}`}
              onClick={() => onState(state === s ? "" : s)}
              aria-pressed={state === s}
            >
              {STATE_NAMES[s]} ({counts[s]})
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-2 text-xs text-ink-muted">
          <label htmlFor="job-sort">Sort</label>
          <select
            id="job-sort"
            className="input w-auto py-1 text-xs"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
          >
            <option value="publish">Publication time</option>
            <option value="updated">Last updated</option>
            <option value="title">Title</option>
          </select>
        </div>
      </div>

      <div className="card overflow-hidden">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">
            Scheduled videos, with their local job state and the most recently observed
            YouTube state.
          </caption>
          <thead>
            <tr className="border-b border-edge text-left text-xs uppercase tracking-wide text-ink-faint">
              <th scope="col" className="px-4 py-3 font-medium">Video</th>
              <th scope="col" className="px-4 py-3 font-medium">Channel</th>
              <th scope="col" className="px-4 py-3 font-medium">Publishes</th>
              <th scope="col" className="px-4 py-3 font-medium">Uploaded</th>
              <th scope="col" className="px-4 py-3 font-medium">State</th>
              <th scope="col" className="px-4 py-3 font-medium">Progress</th>
              <th scope="col" className="px-4 py-3 font-medium">Video</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-ink-muted">
                  {filtered
                    ? "Nothing matches this filter."
                    : "No content yet. Use Create Content to add a clip."}
                </td>
              </tr>
            )}
            {rows.map((job) => (
              <Row key={job.id} job={job} onOpen={() => onOpen(job)} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Row({ job, onOpen }: { job: Job; onOpen: () => void }) {
  const attention = needsAttention(job);
  return (
    <tr
      className={`border-b border-edge/60 align-top transition-colors last:border-0 hover:bg-surface-raised/60 ${
        attention ? "bg-cue-warning/[0.04]" : ""
      }`}
    >
      <td className="px-4 py-3">
        <button
          onClick={onOpen}
          className="text-left font-medium text-ink hover:text-accent"
          aria-label={`Open ${job.title || "(untitled)"}`}
        >
          {job.title || "(untitled)"}
        </button>
        <div className="mt-0.5 text-xs text-ink-faint">
          {job.media_name} · {formatBytes(job.media_size_bytes)}
          {job.media_duration_seconds !== null && ` · ${formatDuration(job.media_duration_seconds)}`}
        </div>
      </td>
      <td className="px-4 py-3 text-ink-muted">
        {job.channel_title ?? <span className="text-cue-attention">Not selected</span>}
      </td>
      <td className="px-4 py-3">
        <div className="text-ink">{formatWhen(job.publish_at_utc)}</div>
        <div className="text-xs text-ink-faint">{job.publish_at_display ?? "No time set"}</div>
      </td>
      <td className="px-4 py-3 text-ink-muted">
        {job.uploaded_at ? (
          <>
            <div>{formatWhen(job.uploaded_at)}</div>
            <div className="text-xs text-ink-faint">{job.uploaded_at_display}</div>
          </>
        ) : (
          <span className="text-ink-faint">—</span>
        )}
      </td>
      <td className="px-4 py-3">
        <StatusBadge job={job} />
        {job.failure_message && (
          <p className="mt-1 max-w-[22rem] text-xs text-cue-warning">{job.failure_message}</p>
        )}
        {job.next_attempt_at && job.state === "JOB-FAILED_RETRY" && (
          <p className="mt-1 text-xs text-ink-muted">
            Retry {job.attempt + 1} of {job.max_attempts} at {formatWhen(job.next_attempt_at)}
          </p>
        )}
        {job.insert_may_have_succeeded && (
          <p className="mt-1 text-xs text-cue-attention">
            Upload outcome uncertain — no automatic retry
          </p>
        )}
      </td>
      <td className="w-40 px-4 py-3">
        <ProgressBar percent={job.progress_percent} active={job.state === "JOB-UPLOADING"} />
      </td>
      <td className="px-4 py-3">
        {job.video_id ? (
          <div className="flex flex-col gap-0.5">
            <code className="font-mono text-xs text-ink-muted">{job.video_id}</code>
            {job.video_url && (
              <a
                className="text-xs text-link hover:underline"
                href={job.video_url}
                target="_blank"
                rel="noreferrer noopener"
              >
                Open on YouTube
              </a>
            )}
          </div>
        ) : (
          <span className="text-xs text-ink-faint">—</span>
        )}
      </td>
    </tr>
  );
}
