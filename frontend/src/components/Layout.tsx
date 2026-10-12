import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { usePolicyStatus, useWorkerStatus } from "../hooks/api";
import {
  IconCalendar,
  IconChannel,
  IconClose,
  IconDashboard,
  IconMedia,
  IconMenu,
  IconMoon,
  IconPlus,
  IconQueue,
  IconReport,
  IconSettings,
  IconSun,
} from "./Icons";
import { applyTheme, storedTheme } from "../lib/theme";

/**
 * The navigation, exactly as `01-visual-uiux-prd.md` §1 specifies it: one list, seven
 * items, in the order an operator works through them. "Create Content" is a *primary
 * action* rather than an eighth nav entry, because it creates a thing rather than
 * taking you to a place — putting it in the list would imply the editor is somewhere
 * you live instead of somewhere you visit.
 *
 * There are deliberately no tabs for other platforms. The YouTube-only MVP has exactly
 * one connector, and a disabled "Instagram" tab would advertise a capability this
 * build does not have.
 */
const NAV = [
  { to: "/", label: "Dashboard", end: true, Icon: IconDashboard },
  { to: "/calendar", label: "Schedule Calendar", end: false, Icon: IconCalendar },
  { to: "/queue", label: "Upload Queue", end: false, Icon: IconQueue },
  { to: "/media", label: "Media Library", end: false, Icon: IconMedia },
  { to: "/channels", label: "YouTube Channels", end: false, Icon: IconChannel },
  { to: "/reports", label: "Reports & Google Sheets", end: false, Icon: IconReport },
  { to: "/settings", label: "Settings & Diagnostics", end: false, Icon: IconSettings },
] as const;

/**
 * Application shell.
 *
 * Three facts live here and never in a page, because they change how every other
 * number on screen should be read: whether the worker is alive (a queued job with no
 * worker is parked, not progressing), whether the privacy policy has been accepted
 * (reporting and the mention workflow refuse to run until it is), and which theme is
 * showing. Each is read from the backend's own durable record — the frontend never
 * infers liveness from how fresh the data looks.
 */
export function Layout() {
  const worker = useWorkerStatus();
  const policy = usePolicyStatus();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useState(() => storedTheme());

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
  };

  const accepted = policy.data?.accepted ?? true;
  const workerAlive = worker.data?.alive ?? false;

  return (
    <div className="flex min-h-full">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-edge bg-surface lg:flex">
        <div className="flex items-center gap-2 px-5 py-5">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-accent text-on-accent text-sm font-bold">
            H
          </span>
          <span className="text-base font-semibold tracking-tight">Hexton</span>
          <span className="rounded border border-edge px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-ink-faint">
            local
          </span>
        </div>

        <div className="px-4 pb-3">
          <Link className="btn btn-primary w-full justify-center" to="/new">
            <IconPlus size={16} />
            Create Content
          </Link>
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-3 py-2" aria-label="Main">
          {NAV.map(({ to, label, end, Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors ${
                  isActive
                    ? "bg-accent/10 font-medium text-accent"
                    : "text-ink-muted hover:bg-surface-raised hover:text-ink"
                }`
              }
            >
              <Icon size={17} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="flex flex-col gap-2 border-t border-edge px-4 py-3">
          <WorkerPill alive={workerAlive} owner={worker.data?.owner ?? null} />
          <button
            className="btn w-full justify-center"
            onClick={toggleTheme}
            aria-pressed={theme === "dark"}
          >
            {theme === "dark" ? <IconSun size={15} /> : <IconMoon size={15} />}
            {theme === "dark" ? "Light theme" : "Dark theme"}
          </button>
        </div>
      </aside>

      {/* Mobile: the same list, opened from the top bar, never a second structure. */}
      {menuOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            className="absolute inset-0 bg-black/40"
            aria-label="Close navigation"
            onClick={() => setMenuOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 flex w-64 flex-col border-r border-edge bg-surface">
            <div className="flex items-center justify-between px-4 py-4">
              <span className="font-semibold">Hexton</span>
              <button className="btn px-2 py-1" onClick={() => setMenuOpen(false)} aria-label="Close navigation">
                <IconClose size={16} />
              </button>
            </div>
            <div className="px-3 pb-3">
              <Link className="btn btn-primary w-full justify-center" to="/new" onClick={() => setMenuOpen(false)}>
                <IconPlus size={16} />
                Create Content
              </Link>
            </div>
            <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-3" aria-label="Main">
              {NAV.map(({ to, label, end, Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  onClick={() => setMenuOpen(false)}
                  className={({ isActive }) =>
                    `flex items-center gap-2.5 rounded-md px-3 py-2 text-sm ${
                      isActive ? "bg-accent/10 font-medium text-accent" : "text-ink-muted"
                    }`
                  }
                >
                  <Icon size={17} />
                  <span>{label}</span>
                </NavLink>
              ))}
            </nav>
            <div className="border-t border-edge px-3 py-3">
              <WorkerPill alive={workerAlive} owner={worker.data?.owner ?? null} />
            </div>
          </div>
        </div>
      )}

      <div className="flex min-h-full flex-1 flex-col lg:pl-60">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-edge bg-surface/95 px-4 py-2.5 backdrop-blur lg:hidden">
          <button
            className="btn px-2 py-1"
            aria-label="Open navigation"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
          >
            <IconMenu size={16} />
          </button>
          <span className="font-semibold">Hexton</span>
          <Link className="btn btn-primary ml-auto px-2.5 py-1" to="/new">
            <IconPlus size={15} />
            Create Content
          </Link>
        </header>

        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8" key={location.pathname}>
          {!accepted && (
            <div
              role="status"
              className="mb-6 rounded-md border border-cue-attention/40 bg-cue-attention/10 px-4 py-3 text-sm text-cue-attention"
            >
              The privacy policy has not been accepted on this PC. Google Sheets
              reporting and the creator-mention workflow stay locked until it is —{" "}
              <Link className="underline" to="/settings">
                read and accept it in Settings
              </Link>
              .
            </div>
          )}
          <div className="mx-auto max-w-[1500px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

/**
 * Worker health, shown in the shell rather than only on the dashboard because it is
 * the one signal that changes the meaning of every screen: while it is dark, nothing
 * in the queue is progressing and no amount of refreshing will change that.
 */
function WorkerPill({ alive, owner }: { alive: boolean; owner: string | null }) {
  return (
    <div
      className={`flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs ${
        alive
          ? "border-cue-success/40 text-cue-success"
          : "border-cue-attention/50 bg-cue-attention/10 text-cue-attention"
      }`}
      title={owner ? `Worker heartbeat from ${owner}` : "No worker heartbeat recorded"}
    >
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" aria-hidden />
      <span className="truncate">
        {alive ? "Worker running" : "Worker not running"}
      </span>
    </div>
  );
}
