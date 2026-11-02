import { useSearchParams } from "react-router-dom";
import { JobDetails } from "../components/JobDetails";
import { UploadQueue } from "../components/UploadQueue";
import { useJobs, useWorkerStatus } from "../hooks/api";

/**
 * Upload Queue Page.
 *
 * Implements Panel 3 of the visual reference:
 * Progress is read from the worker's own record. Transfers completed vs publication verified
 * remain strictly distinguished.
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
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Upload Queue</h1>
          <p className="mt-1 text-sm text-[var(--hs-text-secondary)]">
            Monitor active video uploads and publishing progress in real time.
          </p>
        </div>
        {!worker.data?.alive && (
          <div className="rounded-xl border border-[var(--hs-warning)]/40 bg-[var(--hs-warning)]/10 px-4 py-2.5 text-xs text-[var(--hs-warning)] flex items-center gap-2 shadow-sm">
            <span className="h-2 w-2 rounded-full bg-[var(--hs-warning)] shrink-0" />
            <span>
              No worker heartbeat. Queued jobs are parked, not progressing. Start it with{" "}
              <code className="font-mono text-white bg-black/40 px-1 py-0.5 rounded">hexton serve</code>.
            </span>
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
