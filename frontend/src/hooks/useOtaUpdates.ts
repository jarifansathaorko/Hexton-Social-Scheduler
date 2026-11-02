import { useEffect, useState, useCallback } from "react";
import type { ElectronUpdateStatus } from "../types/electron";

export function useOtaUpdates() {
  const [updateStatus, setUpdateStatus] = useState<ElectronUpdateStatus>({
    status: "idle",
    currentVersion: "1.0.0",
    availableVersion: null,
    downloadProgress: null,
    error: null,
  });
  const [isChecking, setIsChecking] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);

  const isElectron = Boolean(typeof window !== "undefined" && window.electronAPI?.isElectron);

  useEffect(() => {
    if (!isElectron || !window.electronAPI) return;

    // Fetch initial status
    window.electronAPI.updater.getStatus().then(setUpdateStatus).catch(console.warn);

    // Listen to updater status changes
    const unsubStatus = window.electronAPI.updater.onStatus((status) => {
      setUpdateStatus(status);
      if (status.status !== "downloading") {
        setIsDownloading(false);
      }
    });

    // Listen to progress updates
    const unsubProgress = window.electronAPI.updater.onProgress((progress) => {
      setUpdateStatus((prev) => ({
        ...prev,
        status: "downloading",
        downloadProgress: progress,
      }));
    });

    return () => {
      unsubStatus();
      unsubProgress();
    };
  }, [isElectron]);

  const checkForUpdates = useCallback(async (manual = true) => {
    if (!isElectron || !window.electronAPI) return null;
    setIsChecking(true);
    try {
      const res = await window.electronAPI.updater.checkForUpdates(manual);
      return res;
    } finally {
      setIsChecking(false);
    }
  }, [isElectron]);

  const downloadUpdate = useCallback(async () => {
    if (!isElectron || !window.electronAPI) return false;
    setIsDownloading(true);
    try {
      const res = await window.electronAPI.updater.downloadUpdate();
      return res.success;
    } catch {
      setIsDownloading(false);
      return false;
    }
  }, [isElectron]);

  const quitAndInstall = useCallback(async () => {
    if (!isElectron || !window.electronAPI) return;
    await window.electronAPI.updater.quitAndInstall();
  }, [isElectron]);

  return {
    isElectron,
    updateStatus,
    isChecking,
    isDownloading,
    checkForUpdates,
    downloadUpdate,
    quitAndInstall,
  };
}
