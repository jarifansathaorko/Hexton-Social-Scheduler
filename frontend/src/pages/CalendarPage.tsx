import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CalendarMonth } from "../components/CalendarMonth";
import { JobDetails } from "../components/JobDetails";
import { JobTable } from "../components/JobTable";
import { IconPlus } from "../components/Icons";
import { useConnections, useJobs, useSettings } from "../hooks/api";

/**
 * Schedule Calendar Page.
 *
 * Implements Panel 2 of the visual reference:
 * - Direct calendar month navigation and view toggling
 * - Side agenda / JobDetails complementary panel
 * - Filterable "All content" list with search and channel filter
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
  const settings = useSettings();

  const timeZone = settings.data?.display_timezone || "Asia/Dhaka";

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
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Schedule Calendar</h1>
          <p className="mt-1 text-sm text-[var(--hs-text-secondary)]">
            Schedule, organize, and view your upcoming YouTube clip releases ({timeZone}).
          </p>
        </div>
        <Link className="btn btn-primary shadow-lg shadow-violet-950/50 py-2.5 px-4 rounded-xl font-medium" to="/new">
          <IconPlus size={16} />
          Schedule Upload
        </Link>
      </header>

      <div className={selectedId ? "grid gap-6 xl:grid-cols-[minmax(0,1fr)_520px]" : ""}>
        <div className="flex min-w-0 flex-col gap-8">
          <CalendarMonth jobs={visible} onSelect={select} selectedId={selectedId} />

          <section className="hs-card p-5">
            <h2 className="text-base font-semibold text-white tracking-tight">All content</h2>
            <p className="mt-1 mb-4 text-xs text-[var(--hs-text-muted)]">
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
