import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { usePolicyStatus, useSettings, useWorkerStatus } from "../hooks/api";
import { useLicensing } from "../hooks/useLicensing";
import { useOtaUpdates } from "../hooks/useOtaUpdates";
import { DesktopTitlebar } from "./DesktopTitlebar";
import { LicenseModal } from "./LicenseModal";
import { OtaUpdateBanner } from "./OtaUpdateBanner";
import {
  IconBell,
  IconCalendar,
  IconChannel,
  IconClose,
  IconDashboard,
  IconHexSchedulerLogo,
  IconMedia,
  IconMenu,
  IconMoon,
  IconPlus,
  IconQueue,
  IconReport,
  IconSearch,
  IconSettings,
  IconSun,
  IconTrendingUp,
} from "./Icons";
import { applyTheme, storedTheme } from "../lib/theme";

/**
 * Main navigation destinations according to the design specification:
 * Primary sections in consistent order.
 * "Create Content" is a primary CTA button, intentionally placed outside the nav list.
 */
const NAV = [
  { to: "/", label: "Dashboard", end: true, Icon: IconDashboard },
  { to: "/calendar", label: "Schedule Calendar", end: false, Icon: IconCalendar },
  { to: "/queue", label: "Upload Queue", end: false, Icon: IconQueue },
  { to: "/analytics", label: "1K+ Deliverables", end: false, Icon: IconTrendingUp },
  { to: "/media", label: "Media Library", end: false, Icon: IconMedia },
  { to: "/channels", label: "YouTube Channels", end: false, Icon: IconChannel },
  { to: "/reports", label: "Reports & Google Sheets", end: false, Icon: IconReport },
  { to: "/settings", label: "Settings & Diagnostics", end: false, Icon: IconSettings },
] as const;

/**
 * HexScheduler Application Shell.
 *
 * Implements the dark charcoal & violet foundation, fixed sidebar navigation,
 * unified top search and operator status bar, persistent worker health telemetry,
 * and responsive mobile drawer.
 */
export function Layout() {
  const worker = useWorkerStatus();
  const policy = usePolicyStatus();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useState(() => storedTheme());
  const [searchQuery, setSearchQuery] = useState("");
  const [updateModalOpen, setUpdateModalOpen] = useState(false);

  const licensing = useLicensing();
  const ota = useOtaUpdates();

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
  };

  const accepted = policy.data?.accepted ?? true;
  const workerAlive = worker.data?.alive ?? false;
  const isElectron = licensing.isElectron;

  return (
    <div className={`flex min-h-screen bg-[var(--hs-canvas)] text-[var(--hs-text)] ${isElectron ? "pt-9" : ""}`}>
      {/* Desktop Frameless Window Titlebar (Electron Only) */}
      <DesktopTitlebar
        licenseStatus={licensing.status}
        updateStatus={ota.updateStatus}
        onOpenLicense={() => licensing.setModalOpen(true)}
        onOpenUpdateModal={() => setUpdateModalOpen(true)}
        workerAlive={workerAlive}
      />

      {/* Desktop Fixed Left Sidebar */}
      <aside className={`fixed ${isElectron ? "top-9 bottom-0" : "inset-y-0"} left-0 z-30 hidden w-64 flex-col border-r border-[var(--hs-border)] bg-[var(--hs-shell)] lg:flex`}>
        {/* Brand Header */}
        <div className="flex items-center gap-3 px-5 py-5 border-b border-[var(--hs-border)]/60">
          <IconHexSchedulerLogo size={28} />
          <div className="flex items-baseline gap-1">
            <span className="text-base font-bold tracking-tight text-white">Hex</span>
            <span className="text-base font-bold tracking-tight text-[var(--hs-violet-bright)]">Scheduler</span>
          </div>
          <span className="ml-auto rounded border border-[var(--hs-violet)]/30 bg-[var(--hs-violet-soft)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--hs-violet-bright)]">
            {isElectron ? "desktop" : "local"}
          </span>
        </div>

        {/* Primary CTA: Create Content */}
        <div className="px-4 pt-4 pb-2">
          <Link
            className="btn btn-primary w-full justify-center py-2.5 rounded-xl font-medium shadow-md shadow-violet-950/40 text-sm"
            to="/new"
          >
            <IconPlus size={16} />
            Create Content
          </Link>
        </div>

        {/* Navigation list */}
        <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-3 py-3" aria-label="Main">
          {NAV.map(({ to, label, end, Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all ${
                  isActive
                    ? "bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] border border-[var(--hs-border-strong)] shadow-[0_0_12px_rgba(139,92,246,0.15)]"
                    : "text-[var(--hs-text-secondary)] hover:bg-[var(--hs-surface-soft)] hover:text-white"
                }`
              }
            >
              <Icon size={18} className="shrink-0" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        {/* Sidebar Footer: Worker Heartbeat & Theme Switch */}
        <div className="flex flex-col gap-2.5 border-t border-[var(--hs-border)] px-4 py-3.5 bg-[var(--hs-shell)]">
          <WorkerPill alive={workerAlive} owner={worker.data?.owner ?? null} />
          <button
            className="btn btn-secondary w-full justify-center text-xs py-2"
            onClick={toggleTheme}
            aria-pressed={theme === "dark"}
          >
            {theme === "dark" ? <IconSun size={14} /> : <IconMoon size={14} />}
            {theme === "dark" ? "Light theme" : "Dark theme"}
          </button>
        </div>
      </aside>

      {/* Mobile Drawer Navigation */}
      {menuOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            aria-label="Close navigation"
            onClick={() => setMenuOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 flex w-72 flex-col border-r border-[var(--hs-border)] bg-[var(--hs-shell)] shadow-2xl">
            <div className="flex items-center justify-between border-b border-[var(--hs-border)] px-5 py-4">
              <div className="flex items-center gap-2.5">
                <IconHexSchedulerLogo size={24} />
                <span className="font-bold text-white">Hex<span className="text-[var(--hs-violet-bright)]">Scheduler</span></span>
              </div>
              <button
                className="btn px-2 py-1.5 text-[var(--hs-text-muted)] hover:text-white"
                onClick={() => setMenuOpen(false)}
                aria-label="Close navigation"
              >
                <IconClose size={16} />
              </button>
            </div>
            <div className="px-4 pt-4 pb-2">
              <Link
                className="btn btn-primary w-full justify-center py-2.5 rounded-xl font-medium"
                to="/new"
                onClick={() => setMenuOpen(false)}
              >
                <IconPlus size={16} />
                Create Content
              </Link>
            </div>
            <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-3 py-2" aria-label="Main">
              {NAV.map(({ to, label, end, Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  onClick={() => setMenuOpen(false)}
                  className={({ isActive }) =>
                    `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium ${
                      isActive
                        ? "bg-[var(--hs-violet-soft)] text-[var(--hs-violet-bright)] border border-[var(--hs-border-strong)]"
                        : "text-[var(--hs-text-secondary)]"
                    }`
                  }
                >
                  <Icon size={18} />
                  <span>{label}</span>
                </NavLink>
              ))}
            </nav>
            <div className="border-t border-[var(--hs-border)] px-4 py-3 flex flex-col gap-2">
              <WorkerPill alive={workerAlive} owner={worker.data?.owner ?? null} />
            </div>
          </div>
        </div>
      )}

      {/* Main App Container */}
      <div className="flex min-h-screen flex-1 flex-col lg:pl-64">
        {/* Top Header Bar across desktop and mobile */}
        <header className={`sticky ${isElectron ? "top-9" : "top-0"} z-20 flex h-16 items-center justify-between border-b border-[var(--hs-border)] bg-[var(--hs-shell)]/90 px-4 sm:px-6 backdrop-blur-md`}>
          {/* Mobile menu toggle & brand */}
          <div className="flex items-center gap-3 lg:hidden">
            <button
              className="btn px-2.5 py-1.5"
              aria-label="Open navigation"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <IconMenu size={18} />
            </button>
            <div className="flex items-center gap-2">
              <IconHexSchedulerLogo size={22} />
              <span className="font-bold text-white text-sm">Hex<span className="text-[var(--hs-violet-bright)]">Scheduler</span></span>
            </div>
          </div>

          {/* Global Search Bar */}
          <div className="hidden sm:flex items-center flex-1 max-w-md">
            <div className="relative w-full">
              <IconSearch size={16} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-[var(--hs-text-muted)]" />
              <input
                type="search"
                className="input pl-9 pr-12 py-1.5 text-xs bg-[var(--hs-surface-raised)] border-[var(--hs-border)] focus:border-[var(--hs-violet)] rounded-lg w-full"
                placeholder="Search anything..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] uppercase font-mono px-1.5 py-0.5 rounded border border-[var(--hs-border)] text-[var(--hs-text-muted)] bg-[var(--hs-surface)]">
                Ctrl+K
              </span>
            </div>
          </div>

          {/* Right Action Utilities */}
          <div className="flex items-center gap-3 ml-auto">
            {/* License Status Badge */}
            <button
              type="button"
              onClick={() => licensing.setModalOpen(true)}
              className={`hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border transition-all ${
                licensing.status.licensed
                  ? "border-violet-500/30 bg-violet-950/40 text-violet-300 hover:bg-violet-900/50 shadow-sm"
                  : "border-amber-500/40 bg-amber-950/40 text-amber-300 hover:bg-amber-900/50"
              }`}
              title="Manage License & Activation"
            >
              <span className={`h-1.5 w-1.5 rounded-full ${licensing.status.licensed ? "bg-violet-400" : "bg-amber-400"}`} />
              <span className="uppercase text-[10px] tracking-wider">
                {licensing.status.plan} {licensing.status.licensed ? "Active" : "Activate"}
              </span>
            </button>

            {/* Notification Icon */}
            <button
              type="button"
              className="relative p-2 rounded-lg text-[var(--hs-text-secondary)] hover:text-white hover:bg-[var(--hs-surface-soft)] transition-colors"
              title="Notifications"
              aria-label="Notifications"
            >
              <IconBell size={18} />
              <span className="absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-[var(--hs-violet)]" />
            </button>

            {/* Operator Identity Badge (Local Workspace) */}
            <div className="flex items-center gap-2.5 pl-2 border-l border-[var(--hs-border)]">
              <div className="grid h-8 w-8 place-items-center rounded-full bg-[var(--hs-violet)] text-white text-xs font-semibold shadow-sm">
                J
              </div>
              <div className="hidden md:flex flex-col">
                <span className="text-xs font-semibold text-white leading-tight">Jarif Ansath</span>
                <span className="text-[10px] text-[var(--hs-text-muted)] leading-tight">Local Operator</span>
              </div>
            </div>

            {/* Mobile Create Content shortcut */}
            <Link className="btn btn-primary sm:hidden px-2.5 py-1 text-xs" to="/new">
              <IconPlus size={14} />
              Create
            </Link>
          </div>
        </header>

        {/* Page Content Landmark */}
        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8 hs-glow-radial" key={location.pathname}>
          <ConnectorBanner />

          {/* Privacy Policy Global Alert */}
          {!accepted && (
            <div
              role="status"
              className="mb-6 rounded-xl border border-[var(--hs-warning)]/40 bg-[var(--hs-warning)]/10 px-4 py-3.5 text-sm text-[var(--hs-warning)] flex items-start gap-3 shadow-lg"
            >
              <span className="h-2 w-2 mt-1.5 shrink-0 rounded-full bg-[var(--hs-warning)]" />
              <div>
                The privacy policy has not been accepted on this PC. Google Sheets
                reporting and the creator-mention workflow stay locked until it is —{" "}
                <Link className="underline font-semibold text-white hover:text-[var(--hs-warning)] ml-1" to="/settings">
                  read and accept it in Settings
                </Link>
                .
              </div>
            </div>
          )}

          <div className="mx-auto max-w-[1550px]">
            <Outlet />
          </div>
        </main>
      </div>

      {/* License Management Modal */}
      <LicenseModal
        isOpen={licensing.modalOpen}
        onClose={() => licensing.setModalOpen(false)}
        status={licensing.status}
        onActivate={licensing.activate}
        onDeactivate={licensing.deactivate}
      />

      {/* OTA Update Banner */}
      <OtaUpdateBanner
        updateStatus={ota.updateStatus}
        isDownloading={ota.isDownloading}
        onDownload={ota.downloadUpdate}
        onQuitAndInstall={ota.quitAndInstall}
        isOpen={updateModalOpen}
        onClose={() => setUpdateModalOpen(false)}
      />
    </div>
  );
}

/**
 * Worker telemetry indicator.
 * Reflects genuine worker heartbeat from the backend.
 */
function WorkerPill({ alive, owner }: { alive: boolean; owner: string | null }) {
  return (
    <div
      className={`flex items-center gap-2.5 rounded-lg border px-3 py-2 text-xs transition-colors ${
        alive
          ? "border-[var(--hs-success)]/30 bg-[var(--hs-success)]/10 text-[var(--hs-success)]"
          : "border-[var(--hs-warning)]/40 bg-[var(--hs-warning)]/10 text-[var(--hs-warning)]"
      }`}
      title={owner ? `Worker heartbeat from ${owner}` : "No worker heartbeat recorded"}
    >
      <span
        className={`h-2 w-2 shrink-0 rounded-full ${
          alive ? "bg-[var(--hs-success)] shadow-[0_0_8px_rgba(52,211,153,0.6)]" : "bg-[var(--hs-warning)]"
        }`}
        aria-hidden
      />
      <span className="font-medium truncate">
        {alive ? "Worker running" : "Worker not running"}
      </span>
    </div>
  );
}

/**
 * Connector status banner.
 */
function ConnectorBanner() {
  const settings = useSettings();
  if (!settings.data || settings.data.connector_ready) return null;

  const simulated = settings.data.connector_kind === "fake";
  const heading = simulated ? "Simulated uploads" : "This build cannot publish to YouTube";

  return (
    <div
      role="alert"
      data-testid="connector-banner"
      className="mb-6 rounded-xl border border-[var(--hs-warning)]/40 bg-[var(--hs-surface-raised)] p-4 text-sm text-[var(--hs-text)] shadow-lg"
    >
      <div className="flex items-center gap-2 text-[var(--hs-warning)] font-semibold">
        <span className="h-2 w-2 rounded-full bg-[var(--hs-warning)]" />
        <p>{heading}</p>
      </div>
      <p className="mt-1 text-xs text-[var(--hs-text-secondary)]">
        {simulated
          ? "The fake connector is active, so nothing leaves this computer. Every state, count and timestamp below is produced locally — no video is uploaded to YouTube and no report is written to Google Sheets."
          : "The YouTube connector is not wired in this build, so an upload attempt fails loudly rather than pretending. No video is uploaded to YouTube and no report is written to Google Sheets."}{" "}
        <Link className="underline text-[var(--hs-violet-bright)] font-medium" to="/settings">
          Connector details in Settings
        </Link>
        .
      </p>
    </div>
  );
}
