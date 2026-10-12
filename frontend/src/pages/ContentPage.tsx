import { Link, useSearchParams } from "react-router-dom";
import { JobDetails } from "../components/JobDetails";
import { JobTable } from "../components/JobTable";
import { useJobs } from "../hooks/api";

/**
 * The content library.
 *
 * Search and filter live in the URL rather than in component state, so a link to
 * "everything blocked" is a real link an operator can send themselves, and reloading
 * the page does not silently widen the filter back to everything.
 */
export function ContentPage() {
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const state = params.get("state") ?? "";
  const selected = params.get("job");

  const jobs = useJobs(query, state);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const selectedId = selected ? Number(selected) : null;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Content</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {jobs.data?.total ?? 0} item{(jobs.data?.total ?? 0) === 1 ? "" : "s"} ·{" "}
            {jobs.data?.states["JOB-DRAFT"] ?? 0} draft
            {(jobs.data?.states["JOB-DRAFT"] ?? 0) === 1 ? "" : "s"}
          </p>
        </div>
        <Link className="btn btn-primary" to="/content/new">
          New content
        </Link>
      </header>

      <div className={selectedId ? "grid gap-6 xl:grid-cols-[minmax(0,1fr)_520px]" : ""}>
        <JobTable
          data={jobs.data}
          query={query}
          onQuery={(value) => setParam("q", value)}
          state={state}
          onState={(value) => setParam("state", value)}
        />
        {selectedId !== null && (
          <JobDetails jobId={selectedId} onClose={() => setParam("job", "")} />
        )}
      </div>
    </div>
  );
}
