import { useState } from "react";
import type { ElectronUpdateStatus } from "../types/electron";

interface OtaUpdateBannerProps {
  updateStatus: ElectronUpdateStatus;
  isDownloading: boolean;
  onDownload: () => Promise<boolean>;
  onQuitAndInstall: () => Promise<void>;
  isOpen?: boolean;
  onClose?: () => void;
}

export function OtaUpdateBanner({
  updateStatus,
  isDownloading,
  onDownload,
  onQuitAndInstall,
  isOpen = false,
  onClose,
}: OtaUpdateBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  // If there's no available update and modal isn't explicitly open, hide
  const hasUpdate = updateStatus.status === "available" || updateStatus.status === "downloading" || updateStatus.status === "downloaded";
  if ((!hasUpdate && !isOpen) || (dismissed && !isOpen)) {
    return null;
  }

  const percent = updateStatus.downloadProgress?.percent != null
    ? Math.round(updateStatus.downloadProgress.percent)
    : 0;

  return (
    <aside
      aria-label="Software update notification"
      className="fixed bottom-5 right-5 z-40 w-96 max-w-[calc(100vw-2rem)] rounded-2xl border border-violet-500/40 bg-[#12131f]/95 p-4 shadow-2xl backdrop-blur-md animate-slideUp text-xs"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="grid h-8 w-8 place-items-center rounded-xl bg-violet-600/20 text-violet-400 border border-violet-500/30 shrink-0">
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          </div>
          <div>
            <h4 className="font-bold text-white tracking-tight">
              {updateStatus.status === "downloaded"
                ? "Update Ready to Install"
                : updateStatus.status === "downloading"
                ? "Downloading HexScheduler Update"
                : "New Update Available"}
            </h4>
            <p className="text-[11px] text-slate-400">
              {updateStatus.availableVersion ? `Version ${updateStatus.availableVersion}` : "Latest improvements"}
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={() => {
            setDismissed(true);
            onClose?.();
          }}
          className="text-slate-400 hover:text-white p-1 rounded-md transition-colors"
          aria-label="Dismiss notification"
        >
          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Progress or Release Note Summary */}
      {updateStatus.status === "downloading" || isDownloading ? (
        <div className="mt-3 space-y-1.5">
          <div className="flex justify-between text-[11px] text-slate-400">
            <span>Downloading package...</span>
            <span className="font-mono text-violet-300 font-semibold">{percent}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full bg-gradient-to-r from-violet-600 to-fuchsia-500 transition-all duration-300 rounded-full"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      ) : updateStatus.status === "downloaded" ? (
        <div className="mt-2 text-slate-300 text-[11px]">
          The new package is ready. Restart now to run the latest version seamlessly.
        </div>
      ) : (
        <div className="mt-2 text-slate-400 text-[11px] line-clamp-2">
          {updateStatus.releaseNotes || "Performance enhancements, algorithm optimizations, and bug fixes."}
        </div>
      )}

      {/* Action Buttons */}
      <div className="mt-3.5 flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
        {updateStatus.status === "downloaded" ? (
          <button
            type="button"
            onClick={onQuitAndInstall}
            className="btn btn-primary px-3 py-1.5 text-xs rounded-xl font-semibold shadow-md shadow-violet-950/40"
          >
            Restart & Install
          </button>
        ) : updateStatus.status === "available" ? (
          <button
            type="button"
            onClick={onDownload}
            disabled={isDownloading}
            className="btn btn-primary px-3 py-1.5 text-xs rounded-xl font-semibold shadow-md shadow-violet-950/40"
          >
            {isDownloading ? "Downloading..." : "Download Update"}
          </button>
        ) : null}
      </div>
    </aside>
  );
}
