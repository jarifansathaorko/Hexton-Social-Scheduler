export interface ElectronLicensingStatus {
  licensed: boolean;
  status: 'active' | 'expired' | 'invalid' | 'unlicensed' | 'grace_period' | 'grace_expired' | 'revoked' | 'deactivated';
  type: 'online' | 'offline' | 'none';
  plan: 'free' | 'trial' | 'creator' | 'pro' | 'studio' | 'perpetual';
  customerName?: string;
  expiresAt?: string | null;
  daysRemaining?: number | null;
  isPerpetual?: boolean;
  features?: string[];
  licenseKeyMasked?: string | null;
  deviceId?: string | null;
  deviceName?: string | null;
  error?: string | null;
}

export interface ElectronUpdateStatus {
  status: 'idle' | 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error';
  currentVersion: string;
  availableVersion?: string | null;
  releaseNotes?: string | null;
  downloadProgress?: { percent: number; transferred: number; total: number } | null;
  error?: string | null;
}

export interface ElectronAPI {
  isElectron: boolean;
  windowControls: {
    minimize: () => Promise<void>;
    maximize: () => Promise<void>;
    close: () => Promise<void>;
    isMaximized: () => Promise<boolean>;
  };
  licensing: {
    getStatus: () => Promise<ElectronLicensingStatus>;
    activateKey: (key: string, serverUrl?: string) => Promise<{ success: boolean; error?: string; state?: ElectronLicensingStatus }>;
    validate: (force?: boolean) => Promise<{ success: boolean; error?: string; state?: ElectronLicensingStatus }>;
    deactivate: () => Promise<{ success: boolean; state?: ElectronLicensingStatus }>;
    checkFeature: (featureName: string) => Promise<boolean>;
  };
  updater: {
    checkForUpdates: (manual?: boolean) => Promise<{ available: boolean; version?: string; notes?: string; error?: string }>;
    downloadUpdate: () => Promise<{ success: boolean; filePath?: string; error?: string }>;
    quitAndInstall: () => Promise<boolean>;
    getStatus: () => Promise<ElectronUpdateStatus>;
    onStatus: (callback: (status: ElectronUpdateStatus) => void) => () => void;
    onProgress: (callback: (progress: { percent: number; transferred: number; total: number }) => void) => () => void;
  };
  system: {
    openExternal: (url: string) => Promise<void>;
    openPath: (pathStr: string) => Promise<void>;
    getAppVersion: () => Promise<string>;
  };
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}
