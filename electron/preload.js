/**
 * Hexton Scheduler Electron Preload Bridge
 * 
 * Exposes secure IPC APIs to the React renderer application via contextBridge.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isElectron: true,

  // Window Controls
  windowControls: {
    minimize: () => ipcRenderer.invoke('window:minimize'),
    maximize: () => ipcRenderer.invoke('window:maximize'),
    close: () => ipcRenderer.invoke('window:close'),
    isMaximized: () => ipcRenderer.invoke('window:isMaximized')
  },

  // Licensing Subsystem
  licensing: {
    getStatus: () => ipcRenderer.invoke('license:getStatus'),
    activateKey: (key, serverUrl) => ipcRenderer.invoke('license:activateKey', { key, serverUrl }),
    validate: (force) => ipcRenderer.invoke('license:validate', force),
    deactivate: () => ipcRenderer.invoke('license:deactivate'),
    checkFeature: (featureName) => ipcRenderer.invoke('license:checkFeature', featureName)
  },

  // Over-The-Air (OTA) Updater Subsystem
  updater: {
    checkForUpdates: (manual = false) => ipcRenderer.invoke('updater:check', manual),
    downloadUpdate: () => ipcRenderer.invoke('updater:download'),
    quitAndInstall: () => ipcRenderer.invoke('updater:quitAndInstall'),
    getStatus: () => ipcRenderer.invoke('updater:getStatus'),
    onStatus: (callback) => {
      const handler = (_event, data) => callback(data);
      ipcRenderer.on('updater:status', handler);
      return () => ipcRenderer.removeListener('updater:status', handler);
    },
    onProgress: (callback) => {
      const handler = (_event, data) => callback(data);
      ipcRenderer.on('updater:progress', handler);
      return () => ipcRenderer.removeListener('updater:progress', handler);
    }
  },

  // System & Shell Helpers
  system: {
    openExternal: (url) => ipcRenderer.invoke('system:openExternal', url),
    openPath: (pathStr) => ipcRenderer.invoke('system:openPath', pathStr),
    getAppVersion: () => ipcRenderer.invoke('system:getAppVersion')
  }
});
