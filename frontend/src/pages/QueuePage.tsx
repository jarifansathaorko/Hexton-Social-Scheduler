import { useSearchParams } from "react-router-dom";
import { JobDetails } from "../components/JobDetails";
import { UploadQueue } from "../components/UploadQueue";
import { useJobs, useWorkerStatus } from "../hooks/api";

/**
 * Upload queue.
 *
 * Live updates come from the TanStack Query refetch interval rather than from the SSE
 * stream, so the queue keeps working with the API's own polling and does not depend on
 * a second live channel being healthy. The SSE endpoint exists for a future denser
 * view; using it here would add a failure mode to a screen whose whole job is to show
 * failure modes.
 */
export function QueuePage() {
  const [params, setParams] = useSearchParams();
  const jobs = useJobs("", "");
  const worker = useWorkerStatus();
  const selected = params.get("job");
  const selectedId = selected ? Number(selected) : null;

  const select = (id: number) => {
    const next = new URLSearchParams(params);
    next.set("job", String(id));
    setParams(next, { replace: true });
  };
  const clear = () => {
    const next = new URLSearchParams(params);
    next.delete("job");
    setParams(next, { replace: true });
  };

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Upload Queue</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Progress is read from the worker's own record, not from this tab. An upload
            is shown as finished when the bytes have landed; it is only ever counted as
            published once YouTube has been observed to say so.
          </p>
        </div>
        {!worker.data?.alive && (
          <div className="rounded-md border border-cue-attention/50 bg-cue-attention/10 px-3 py-2 text-sm text-cue-attention">
            No worker heartbeat. Queued jobs are parked, not progressing. Start it with{" "}
            <code className="font-mono text-xs">hexton serve</code>.
          </div>
        )}
      </header>

      <div className={selectedId ? "grid gap-6 xl:grid-cols-[minmax(0,1fr)_520px]" : ""}>
        <UploadQueue data={jobs.data} onSelect={select} />
        {selectedId !== null && <JobDetails jobId={selectedId} onClose={clear} />}
      </div>
    </div>
  );
}
