import { useSearchParams } from "react-router-dom";
import { CalendarMonth } from "../components/CalendarMonth";
import { JobDetails } from "../components/JobDetails";
import { useJobs } from "../hooks/api";

/**
 * Calendar.
 *
 * The selected job lives in the URL so a month view can be linked to, and so opening
 * a job does not lose the month being looked at.
 */
export function CalendarPage() {
  const [params, setParams] = useSearchParams();
  const jobs = useJobs("", "");
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
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Calendar</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Placed by intended publication time in your own time zone. A job with no time
          is listed below rather than given a date it never had.
        </p>
      </header>

      <div className={selectedId ? "grid gap-6 xl:grid-cols-[minmax(0,1fr)_520px]" : ""}>
        <CalendarMonth jobs={jobs.data?.items ?? []} onSelect={select} selectedId={selectedId} />
        {selectedId !== null && <JobDetails jobId={selectedId} onClose={clear} />}
      </div>
    </div>
  );
}
