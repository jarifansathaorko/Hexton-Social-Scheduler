import { NavLink, Outlet } from "react-router-dom";
import { useWorkerStatus } from "../hooks/api";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/content", label: "Content" },
  { to: "/calendar", label: "Calendar" },
  { to: "/queue", label: "Upload queue" },
  { to: "/settings", label: "Settings" },
];

/**
 * Application shell.
 *
 * The worker indicator lives here and never in a page, because "is the queue actually
 * being serviced?" is the one fact that changes how every other number on screen
 * should be read. A queued job with no worker is not progressing; it is parked. That
 * distinction comes from the worker's own durable heartbeat, not from the frontend
 * guessing on the basis of how fresh the data looks.
 */
export function Layout() {
  const worker = useWorkerStatus();

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-20 border-b border-edge bg-surface/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-6 py-3">
          <div className="flex items-center gap-2">
            <span className="text-lg font-semibold tracking-tight">Hexton</span>
            <span className="rounded border border-edge px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-ink-faint">
              local
            </span>
          </div>
          <nav className="flex flex-1 items-center gap-1" aria-label="Main">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm transition-colors ${
                    isActive
                      ? "bg-surface-raised text-ink"
                      : "text-ink-muted hover:bg-surface-raised hover:text-ink"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div
            className={`flex items-center gap-2 text-xs ${
              worker.data?.alive ? "text-cue-success" : "text-cue-attention"
            }`}
            title={
              worker.data?.alive
                ? `Worker ${worker.data.owner} is running.`
                : "No worker heartbeat has been seen recently; queued jobs are parked, not progressing."
            }
          >
            <span
              aria-hidden
              className={`h-2 w-2 rounded-full ${
                worker.data?.alive ? "bg-cue-success" : "bg-cue-attention"
              }`}
            />
            <span>{worker.data?.alive ? "Worker running" : "Worker not detected"}</span>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1400px] flex-1 px-6 py-6">
        <Outlet />
      </main>

      <footer className="border-t border-edge px-6 py-4 text-center text-[11px] text-ink-faint">
        Runs entirely on this PC. Schedules execute in the local worker, not in this
        tab — closing the browser does not stop them.
      </footer>
    </div>
  );
}
