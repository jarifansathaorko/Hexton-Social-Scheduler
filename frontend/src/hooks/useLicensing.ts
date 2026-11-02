import { useEffect, useState, useCallback } from "react";
import type { ElectronLicensingStatus } from "../types/electron";

const DEFAULT_STATUS: ElectronLicensingStatus = {
  licensed: true,
  status: "active",
  type: "none",
  plan: "pro",
  customerName: "Creator Studio",
  expiresAt: null,
  daysRemaining: null,
  isPerpetual: true,
  features: ["*"],
  licenseKeyMasked: null,
  error: null,
};

export function useLicensing() {
  const [status, setStatus] = useState<ElectronLicensingStatus>(DEFAULT_STATUS);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const isElectron = Boolean(typeof window !== "undefined" && window.electronAPI?.isElectron);

  const refresh = useCallback(async () => {
    if (!isElectron || !window.electronAPI) return;
    try {
      const data = await window.electronAPI.licensing.getStatus();
      setStatus(data);
    } catch (err: unknown) {
      console.warn("Failed to read license status:", err);
    }
  }, [isElectron]);

  useEffect(() => {
    if (isElectron) {
      void refresh();
    }
  }, [isElectron, refresh]);

  const activate = async (key: string) => {
    if (!isElectron || !window.electronAPI) {
      return { success: false, error: "Licensing is only managed in desktop mode." };
    }
    setIsLoading(true);
    setError(null);
    try {
      const res = await window.electronAPI.licensing.activateKey(key);
      if (!res.success) {
        setError(res.error || "Activation failed.");
        return res;
      }
      if (res.state) {
        setStatus(res.state);
      } else {
        await refresh();
      }
      return { success: true };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Activation error.";
      setError(msg);
      return { success: false, error: msg };
    } finally {
      setIsLoading(false);
    }
  };

  const deactivate = async () => {
    if (!isElectron || !window.electronAPI) return { success: false };
    setIsLoading(true);
    try {
      const res = await window.electronAPI.licensing.deactivate();
      if (res.state) {
        setStatus(res.state);
      } else {
        await refresh();
      }
      return res;
    } finally {
      setIsLoading(false);
    }
  };

  return {
    status,
    isElectron,
    isLoading,
    error,
    modalOpen,
    setModalOpen,
    activate,
    deactivate,
    refresh,
  };
}
