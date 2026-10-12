import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { CalendarMonth } from "../components/CalendarMonth";
import { JobDetails } from "../components/JobDetails";
import { JobTable } from "../components/JobTable";
import { useConnections, useJobs } from "../hooks/api";

/**
 * Schedule Calendar.
 *
 * The month view answers the shape question ("is anything going out on the 14th?")
 * and the table underneath answers the list question ("show me everything blocked").
 * They share one selection, which lives in the URL — so a link to "the 14th, filtered
 * to this channel" is a real link an operator can send themselves, and reloading does
 * not silently widen the filter back to everything.
 *
 * Channel filtering is applied here rather than by the API because `GET /api/jobs`
 * takes `q` and `state` only. It is a deliberate narrowing of an already-fetched page
 * rather than a claim that the server filtered anything, which is why the calendar and
 * the table are both fed the narrowed list instead of just the table.
 */
export function CalendarPage() {
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const state = params.get("state") ?? "";
  const channel = params.get("channel") ?? "";
  const selected = params.get("job");
  const selectedId = selected ? Number(selected) : null;

  const jobs = useJobs(query, state);
  const connections = useConnections();

  const channels = useMemo(
    () =>
      (connections.data ?? []).flatMap((connection) =>
        connection.channels.map((entry) => ({ id: entry.id, title: entry.title })),
      ),
    [connections.data],
  );

  const visible = useMemo(() => {
    const all = jobs.data?.items ?? [];
    if (!channel) return all;
    if (channel === "none") return all.filter((job) => job.channel_identity_id === null);
    const wanted = Number(channel);
    return all.filter((job) => job.channel_identity_id === wanted);
  }, [jobs.data, channel]);

  const filteredData = useMemo(() => {
    if (!jobs.data) return undefined;
    if (!channel) return jobs.data;
    return { ...jobs.data, items: visible, total: visible.length };
  }, [jobs.data, visible, channel]);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const select = (id: number) => setParam("job", String(id));
  const clear = () => setParam("job", "");

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Schedule Calendar</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Placed by intended publication time in your own time zone. A job with no time
          is listed rather than given a date it never had, and nothing here can be
          dragged to a new date — rescheduling goes through the editor so the change is
          validated before it is accepted.
        </p>
      </header>

      <div className={selectedId ? "grid gap-6 xl:grid-cols-[minmax(0,1fr)_520px]" : ""}>
        <div className="flex min-w-0 flex-col gap-8">
          <CalendarMonth jobs={visible} onSelect={select} selectedId={selectedId} />
          <section>
            <h2 className="text-base font-semibold">All content</h2>
            <p className="mt-1 mb-4 text-sm text-ink-muted">
              {filteredData?.total ?? 0} item{(filteredData?.total ?? 0) === 1 ? "" : "s"}
              {channel && " · channel filter applied here, not by the server"}
            </p>
            <JobTable
              data={filteredData}
              query={query}
              onQuery={(value) => setParam("q", value)}
              state={state}
              onState={(value) => setParam("state", value)}
              channels={channels}
              channel={channel}
              onChannel={(value) => setParam("channel", value)}
              onOpen={(job) => select(job.id)}
            />
          </section>
        </div>
        {selectedId !== null && <JobDetails jobId={selectedId} onClose={clear} />}
      </div>
    </div>
  );
}
