import type { Job, JobList } from "../api/types";
import { ProgressBar, StatusBadge } from "./StatusBadge";
import { MENTION_NAMES, REMOTE_NAMES, formatBytes, formatWhen } from "../lib/state";

/**
 * The upload queue.
 *
 * Deliberately separates three things that are easy to conflate and expensive to
 * confuse: the transfer is in progress, the transfer finished, and YouTube published
 * the video. Only the first two are shown as "done" here, and a finished transfer
 * whose publication is still being verified says so, because a green tick on an
 * upload that YouTube has not yet made public is the single most misleading thing
 * this application could display.
 *
 * Each row carries the identifiers an operator needs to act without opening anything:
 * the Schedule ID (which is also the report's key), the next retry time, the error
 * classification, and the video ID and URL once one exists.
 */
export function UploadQueue({ data, onSelect }: { data: JobList | undefined; onSelect: (id: number) => void }) {
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
      <Section
        title="In progress"
        hint="These are being transferred now, or are waiting to be claimed by the worker."
        jobs={active}
        onSelect={onSelect}
        empty="Nothing is uploading."
        showProgress
      />

      <section aria-labelledby="uncertain-heading">
        <h2 id="uncertain-heading" className="text-base font-semibold text-cue-attention">
          Needs your decision
        </h2>
        <p className="mt-1 text-sm text-ink-muted">
          The outcome of these uploads could not be proven, so Hexton will not retry
          them automatically. Retrying one could create a duplicate video.
        </p>
        {uncertain.length === 0 ? (
          <p className="mt-3 text-sm text-ink-faint">Nothing is waiting on you.</p>
        ) : (
          <ul className="mt-3 flex flex-col gap-2">
            {uncertain.map((job) => (
              <UncertainRow key={job.id} job={job} onSelect={onSelect} />
            ))}
          </ul>
        )}
      </section>

      <Section
        title="Recently finished"
        hint="A finished transfer is not the same as a published video; the remote state is shown alongside."
        jobs={recentlyFinished}
        onSelect={onSelect}
        empty="Nothing has finished yet."
      />
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
    <section>
      <h2 className="text-base font-semibold">{title}</h2>
      <p className="mt-1 text-sm text-ink-muted">{hint}</p>
      {jobs.length === 0 ? (
        <p className="mt-3 text-sm text-ink-faint">{empty}</p>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {jobs.map((job) => (
            <li key={job.id} className="card p-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <button
                    className="truncate text-left font-medium hover:text-accent"
                    onClick={() => onSelect(job.id)}
                    aria-label={`Open details for ${job.title || "(untitled)"}`}
                  >
                    {job.title || "(untitled)"}
                  </button>
                  <div className="mt-1 text-xs text-ink-faint">
                    {job.channel_title ?? "No channel selected"} ·{" "}
                    {formatBytes(job.media_size_bytes)}
                    {job.media_container ? ` · ${job.media_container}` : ""}
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-ink-faint">
                    Schedule {job.public_id}
                    {job.video_id && ` · video ${job.video_id}`}
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-right text-xs text-ink-muted">
                    <div>Publishes {formatWhen(job.publish_at_utc)}</div>
                    <div>YouTube: {REMOTE_NAMES[job.remote_state]}</div>
                    {job.mention_requested && <div>Mention: {MENTION_NAMES[job.mention_state]}</div>}
                  </div>
                  <StatusBadge job={job} compact />
                </div>
              </div>

              <dl className="mt-3 grid gap-x-6 gap-y-1 border-t border-edge/60 pt-3 text-xs sm:grid-cols-3">
                <div className="flex gap-2">
                  <dt className="text-ink-faint">Next retry</dt>
                  <dd className="text-ink-muted">
                    {job.next_attempt_at
                      ? `${formatWhen(job.next_attempt_at)} (${job.attempt + 1}/${job.max_attempts})`
                      : "—"}
                  </dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-ink-faint">Error class</dt>
                  <dd className="text-ink-muted">{job.failure_kind ?? "—"}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-ink-faint">Video</dt>
                  <dd className="text-ink-muted">
                    {job.video_url ? (
                      <a className="text-link hover:underline" href={job.video_url} target="_blank" rel="noreferrer noopener">
                        {job.video_id}
                      </a>
                    ) : (
                      "—"
                    )}
                  </dd>
                </div>
              </dl>

              {showProgress && (
                <div className="mt-3">
                  <ProgressBar percent={job.progress_percent} active={job.state === "JOB-UPLOADING"} />
                  {job.state === "JOB-QUEUED" && (
                    <p className="mt-1 text-xs text-ink-faint">
                      Waiting to be claimed by the worker.
                      {job.next_attempt_at ? ` Next attempt ${formatWhen(job.next_attempt_at)}.` : ""}
                    </p>
                  )}
                </div>
              )}
              {job.failure_message && (
                <p className="mt-2 text-xs text-cue-warning">{job.failure_message}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The uncertain-outcome row.
 *
 * It carries the recovery procedure rather than a Retry button. There is no blind
 * retry here on purpose: a second `videos.insert` after an ambiguous timeout is how a
 * duplicate Short gets published, and the only safe next step is a human comparing
 * YouTube Studio against the candidate ID.
 */
function UncertainRow({ job, onSelect }: { job: Job; onSelect: (id: number) => void }) {
  return (
    <li className="card border-cue-warning/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <button
            className="truncate text-left font-medium text-cue-warning hover:underline"
            onClick={() => onSelect(job.id)}
          >
            {job.title || "(untitled)"}
          </button>
          <p className="mt-1 font-mono text-[11px] text-ink-faint">
            Schedule {job.public_id}
            {job.video_id && ` · candidate video ${job.video_id}`}
          </p>
          <ol className="mt-2 max-w-2xl list-decimal space-y-1 pl-4 text-xs text-ink-muted">
            <li>
              Open YouTube Studio and look for a video with this title uploaded in the
              last hour.
            </li>
            <li>
              Found it? Open the job and press <span className="text-ink">This video is
              mine</span>; paste or confirm the ID so Hexton adopts it.
            </li>
            <li>
              Not there? Press <span className="text-ink">None of these are mine</span>.
              Only that answer allows a fresh upload — nothing restarts on its own.
            </li>
          </ol>
        </div>
        <StatusBadge job={job} compact />
      </div>
    </li>
  );
}
