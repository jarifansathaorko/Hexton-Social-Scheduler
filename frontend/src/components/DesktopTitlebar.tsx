import React, { useState, useEffect } from "react";
import type { ElectronLicensingStatus, ElectronUpdateStatus } from "../types/electron";

interface DesktopTitlebarProps {
  licenseStatus: ElectronLicensingStatus;
  updateStatus: ElectronUpdateStatus;
  onOpenLicense: () => void;
  onOpenUpdateModal?: () => void;
  workerAlive?: boolean;
}

const dragStyle = { WebkitAppRegion: "drag" } as React.CSSProperties;
const noDragStyle = { WebkitAppRegion: "no-drag" } as React.CSSProperties;

export function DesktopTitlebar({
  licenseStatus,
  updateStatus,
  onOpenLicense,
  onOpenUpdateModal,
  workerAlive = false,
}: DesktopTitlebarProps) {
  const isElectron = typeof window !== "undefined" && Boolean(window.electronAPI?.isElectron);
  const [isMaximized, setIsMaximized] = useState(false);

  useEffect(() => {
    if (!isElectron || !window.electronAPI) return;
    window.electronAPI.windowControls.isMaximized().then(setIsMaximized).catch(console.warn);
  }, [isElectron]);

  if (!isElectron) {
    return null;
  }

  const handleMinimize = () => {
    window.electronAPI?.windowControls.minimize();
  };

  const handleMaximize = async () => {
    await window.electronAPI?.windowControls.maximize();
    const max = await window.electronAPI?.windowControls.isMaximized();
    setIsMaximized(Boolean(max));
  };

  const handleClose = () => {
    window.electronAPI?.windowControls.close();
  };

  const isPro = licenseStatus.licensed && (licenseStatus.plan === "pro" || licenseStatus.plan === "studio" || licenseStatus.plan === "creator" || licenseStatus.plan === "perpetual");
  const isGrace = licenseStatus.status === "grace_period";
  const isUpdateAvailable = updateStatus.status === "available" || updateStatus.status === "downloaded";

  return (
    <div
      style={dragStyle}
      className="fixed top-0 left-0 right-0 z-50 flex h-9 select-none items-center justify-between border-b border-[#262836] bg-[#0c0d14]/95 px-3 text-xs backdrop-blur-md"
    >
      {/* Left: App Identity */}
      <div className="flex items-center gap-2.5">
        <svg
          className="h-4 w-4 text-[var(--hs-violet)]"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 2l8 4.5v9L12 20l-8-4.5v-9L12 2z" />
          <path d="M12 12l8-4.5" />
          <path d="M12 12v8" />
          <path d="M12 12L4 7.5" />
        </svg>
        <div className="flex items-center gap-1.5 font-semibold text-slate-200 tracking-tight text-[11px]">
          <span>HexScheduler</span>
          <span className="rounded bg-violet-950/60 px-1 py-0.2 text-[9px] font-mono text-violet-400 border border-violet-800/40">
            v{updateStatus.currentVersion || "1.0.0"}
          </span>
        </div>

        {/* Worker Pulse */}
        <div
          style={noDragStyle}
          className="hidden sm:flex items-center gap-1.5 ml-2 px-2 py-0.5 rounded-full bg-slate-900/80 border border-slate-800 text-[10px]"
          title={workerAlive ? "Scheduler background worker is active" : "Scheduler background worker is idle"}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              workerAlive ? "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)] animate-pulse" : "bg-amber-400"
            }`}
          />
          <span className="text-slate-400">{workerAlive ? "Worker Active" : "Worker Idle"}</span>
        </div>
      </div>

      {/* Center / Right-Center: License Badge & Update Pill */}
      <div className="flex items-center gap-2">
        {/* License Pill */}
        <button
          type="button"
          style={noDragStyle}
          onClick={onOpenLicense}
          className={`flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-medium transition-all ${
            isPro
              ? "bg-violet-950/50 hover:bg-violet-900/60 text-violet-300 border border-violet-700/50 shadow-[0_0_10px_rgba(139,92,246,0.2)]"
              : isGrace
              ? "bg-amber-950/50 hover:bg-amber-900/60 text-amber-300 border border-amber-600/50"
              : "bg-red-950/40 hover:bg-red-900/50 text-red-300 border border-red-700/50 animate-pulse"
          }`}
          title="Click to manage license & activation"
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              isPro ? "bg-violet-400" : isGrace ? "bg-amber-400" : "bg-red-400"
            }`}
          />
          <span className="uppercase tracking-wider">
            {licenseStatus.plan} {licenseStatus.licensed ? "Active" : "Activate"}
          </span>
          {licenseStatus.daysRemaining != null && licenseStatus.daysRemaining <= 14 && (
            <span className="text-[9px] opacity-75 font-mono">({licenseStatus.daysRemaining}d)</span>
          )}
        </button>

        {/* Update Notification Pill */}
        {isUpdateAvailable && (
          <button
            type="button"
            style={noDragStyle}
            onClick={onOpenUpdateModal}
            className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-950/60 hover:bg-emerald-900/70 text-emerald-300 border border-emerald-600/50 text-[10px] font-medium transition-all shadow-[0_0_8px_rgba(16,185,129,0.2)]"
            title="A newer version is ready"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-ping" />
            <span>v{updateStatus.availableVersion} Ready</span>
          </button>
        )}
      </div>

      {/* Right: Window Controls */}
      <div style={noDragStyle} className="flex items-center">
        {/* Minimize */}
        <button
          type="button"
          onClick={handleMinimize}
          aria-label="Minimize"
          className="flex h-9 w-10 items-center justify-center text-slate-400 hover:bg-[#1e202f] hover:text-white transition-colors"
        >
          <svg className="h-3 w-3" viewBox="0 0 10 1" fill="currentColor">
            <rect width="10" height="1" rx="0.5" />
          </svg>
        </button>

        {/* Maximize / Restore */}
        <button
          type="button"
          onClick={handleMaximize}
          aria-label={isMaximized ? "Restore" : "Maximize"}
          className="flex h-9 w-10 items-center justify-center text-slate-400 hover:bg-[#1e202f] hover:text-white transition-colors"
        >
          {isMaximized ? (
            <svg className="h-3 w-3" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1">
              <path d="M2.5 1.5h6v6h-6z" />
              <path d="M1.5 3.5v5h5" />
            </svg>
          ) : (
            <svg className="h-3 w-3" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1">
              <rect x="1" y="1" width="8" height="8" rx="0.5" />
            </svg>
          )}
        </button>

        {/* Close */}
        <button
          type="button"
          onClick={handleClose}
          aria-label="Close"
          className="flex h-9 w-10 items-center justify-center text-slate-400 hover:bg-rose-600 hover:text-white transition-colors"
        >
          <svg className="h-3 w-3" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.2">
            <path d="M1 1l8 8M9 1l-8 8" />
          </svg>
        </button>
      </div>
    </div>
  );
}
