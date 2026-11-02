import { useState } from "react";
import type { ElectronLicensingStatus } from "../types/electron";

interface LicenseModalProps {
  isOpen: boolean;
  onClose: () => void;
  status: ElectronLicensingStatus;
  onActivate: (key: string) => Promise<{ success: boolean; error?: string }>;
  onDeactivate: () => Promise<{ success: boolean; error?: string }>;
}

export function LicenseModal({
  isOpen,
  onClose,
  status,
  onActivate,
  onDeactivate,
}: LicenseModalProps) {
  const [licenseKeyInput, setLicenseKeyInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [copiedDevice, setCopiedDevice] = useState(false);
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);

  if (!isOpen) return null;

  const handleActivate = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = licenseKeyInput.trim();
    if (!trimmed) {
      setErrorMessage("Please enter a valid license key.");
      return;
    }

    setSubmitting(true);
    setErrorMessage(null);
    setSuccessMessage(null);

    const res = await onActivate(trimmed);
    setSubmitting(false);

    if (res.success) {
      setSuccessMessage("License activated successfully! Enjoy full access.");
      setLicenseKeyInput("");
      setTimeout(() => {
        setSuccessMessage(null);
      }, 4000);
    } else {
      setErrorMessage(res.error || "Activation failed. Please check the key and your internet connection.");
    }
  };

  const handleDeactivate = async () => {
    if (!confirmDeactivate) {
      setConfirmDeactivate(true);
      return;
    }

    setSubmitting(true);
    setErrorMessage(null);
    const res = await onDeactivate();
    setSubmitting(false);
    setConfirmDeactivate(false);

    if (res.success) {
      setSuccessMessage("Device seat released successfully.");
    } else {
      setErrorMessage(res.error || "Failed to deactivate license.");
    }
  };

  const copyDeviceId = () => {
    if (status.deviceId) {
      navigator.clipboard.writeText(status.deviceId);
      setCopiedDevice(true);
      setTimeout(() => setCopiedDevice(false), 2000);
    }
  };

  const isProOrStudio = status.licensed && (status.plan === "pro" || status.plan === "studio" || status.plan === "perpetual");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fadeIn">
      {/* Modal Card */}
      <div className="relative w-full max-w-lg rounded-2xl border border-[var(--hs-border)] bg-[#12131c] p-6 shadow-2xl text-[var(--hs-text)]">
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-[var(--hs-border)]">
          <div className="flex items-center gap-2.5">
            <div className="grid h-9 w-9 place-items-center rounded-xl bg-violet-600/20 text-violet-400 border border-violet-500/30">
              <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
            </div>
            <div>
              <h2 className="text-base font-bold text-white tracking-tight">License Management</h2>
              <p className="text-xs text-slate-400">Manage your activation, plan tier & device seat</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white transition-colors"
            aria-label="Close"
          >
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Feedback Alert */}
        {errorMessage && (
          <div className="mt-4 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300 flex items-start gap-2.5">
            <svg className="h-4 w-4 shrink-0 mt-0.5 text-rose-400" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            <span>{errorMessage}</span>
          </div>
        )}

        {successMessage && (
          <div className="mt-4 rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-300 flex items-start gap-2.5">
            <svg className="h-4 w-4 shrink-0 mt-0.5 text-emerald-400" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
            </svg>
            <span>{successMessage}</span>
          </div>
        )}

        {/* Active Status Details */}
        <div className="mt-5 space-y-3">
          <div className="rounded-xl border border-slate-800 bg-[#161824] p-4">
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400 font-medium">Subscription Tier</span>
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-bold uppercase tracking-wider ${
                  isProOrStudio
                    ? "bg-violet-600/20 text-violet-300 border border-violet-500/40"
                    : status.status === "grace_period"
                    ? "bg-amber-600/20 text-amber-300 border border-amber-500/40"
                    : "bg-slate-800 text-slate-400 border border-slate-700"
                }`}
              >
                {status.plan} {status.licensed ? "Tier" : "Inactive"}
              </span>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-slate-500">Status</span>
                <p className="font-semibold text-slate-200 capitalize mt-0.5">
                  {status.status.replace("_", " ")}
                </p>
              </div>
              <div>
                <span className="text-slate-500">Licensed To</span>
                <p className="font-semibold text-slate-200 mt-0.5 truncate">
                  {status.customerName || "Creator Studio"}
                </p>
              </div>
              <div>
                <span className="text-slate-500">Expiration</span>
                <p className="font-semibold text-slate-200 mt-0.5">
                  {status.isPerpetual
                    ? "Perpetual (Never expires)"
                    : status.expiresAt
                    ? new Date(status.expiresAt).toLocaleDateString()
                    : "Trial / Ongoing"}
                </p>
              </div>
              <div>
                <span className="text-slate-500">Active Key</span>
                <p className="font-mono text-slate-300 mt-0.5">
                  {status.licenseKeyMasked || "None"}
                </p>
              </div>
            </div>

            {/* Hardware Fingerprint info */}
            {status.deviceId && (
              <div className="mt-3 pt-3 border-t border-slate-800/80 flex items-center justify-between text-[11px]">
                <div className="text-slate-500 truncate mr-2">
                  <span>Device Fingerprint: </span>
                  <span className="font-mono text-slate-400">{status.deviceId}</span>
                </div>
                <button
                  type="button"
                  onClick={copyDeviceId}
                  className="shrink-0 text-violet-400 hover:text-violet-300 font-medium underline"
                >
                  {copiedDevice ? "Copied" : "Copy"}
                </button>
              </div>
            )}
          </div>

          {/* Activation Form */}
          <form onSubmit={handleActivate} className="pt-2">
            <label className="block text-xs font-semibold text-slate-300 mb-1.5">
              Enter License Key
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={licenseKeyInput}
                onChange={(e) => setLicenseKeyInput(e.target.value)}
                placeholder="HEX-PRO-XXXX-XXXX or CLX-PRO-XXXX-XXXX"
                className="input flex-1 py-2 text-xs font-mono bg-slate-900/90 border-slate-700 focus:border-violet-500 rounded-xl"
                disabled={submitting}
              />
              <button
                type="submit"
                disabled={submitting || !licenseKeyInput.trim()}
                className="btn btn-primary px-4 py-2 text-xs rounded-xl font-semibold shadow-md shrink-0"
              >
                {submitting ? "Verifying..." : "Activate"}
              </button>
            </div>
            <p className="mt-1.5 text-[11px] text-slate-500">
              Keys generated via your Cloudflare D1 licensing dashboard (Clipxton & Hexton) are verified automatically.
            </p>
          </form>
        </div>

        {/* Footer Actions */}
        <div className="mt-6 pt-4 border-t border-slate-800/80 flex items-center justify-between">
          {status.licensed ? (
            <button
              type="button"
              onClick={handleDeactivate}
              disabled={submitting}
              className={`text-xs font-medium transition-colors ${
                confirmDeactivate
                  ? "text-rose-400 font-bold underline"
                  : "text-slate-500 hover:text-rose-400"
              }`}
            >
              {confirmDeactivate ? "Confirm Deactivation?" : "Deactivate this device seat"}
            </button>
          ) : (
            <span className="text-xs text-slate-500">No active seat bound</span>
          )}

          <button
            type="button"
            onClick={onClose}
            className="btn btn-secondary px-4 py-1.5 text-xs rounded-lg"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
