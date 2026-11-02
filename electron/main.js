/**
 * Hexton Scheduler Electron Main Process
 * 
 * Manages:
 * - Single application instance lock
 * - Python background server lifecycle (supervisor on 127.0.0.1:8787)
 * - Modern frameless window with custom dark theme shell
 * - Integrated licensing verification and OTA update coordination
 * - System tray and graceful worker termination
 */

const { app, BrowserWindow, ipcMain, shell, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn } = require('child_process');

const { licensingService } = require('./licensing/licensingService');
const { updateManager } = require('./updater/updateManager');

let mainWindow = null;
let backendProcess = null;
let tray = null;
let isQuitting = false;

const BACKEND_PORT = 8787;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

// 1. Single Instance Lock
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      if (!mainWindow.isVisible()) mainWindow.show();
      mainWindow.focus();
    }
  });
}

/**
 * Checks if the backend server is responding on loopback port 8787.
 */
function checkBackendHealth(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_URL}/api/session`, { timeout: timeoutMs }, (res) => {
      resolve(res.statusCode === 200 || res.statusCode === 403 || res.statusCode === 404);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

/**
 * Waits for the backend server to become ready.
 */
async function waitForBackend(maxRetries = 30, intervalMs = 500) {
  for (let i = 0; i < maxRetries; i++) {
    const healthy = await checkBackendHealth();
    if (healthy) return true;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

/**
 * Spawns the local Python backend if it is not already running.
 */
async function ensureBackendRunning() {
  const alreadyRunning = await checkBackendHealth(800);
  if (alreadyRunning) {
    console.log('[Backend] Local backend is already active on 127.0.0.1:8787');
    return;
  }

  console.log('[Backend] Launching local backend server...');

  let serverExe = null;
  let serverArgs = [];
  let serverCwd = path.join(__dirname, '..');

  // Packaged production paths
  const packagedExe = path.join(process.resourcesPath, 'hexton_server.exe');
  const packagedExeAlt = path.join(process.resourcesPath, 'worker', 'hexton_server.exe');
  const localDistExe = path.join(__dirname, '..', 'dist', 'Hexton.exe');

  // Dev python path
  const venvPython = path.join(__dirname, '..', 'backend', '.venv', 'Scripts', 'python.exe');

  if (fs.existsSync(packagedExe)) {
    serverExe = packagedExe;
    serverArgs = [];
  } else if (fs.existsSync(packagedExeAlt)) {
    serverExe = packagedExeAlt;
    serverArgs = [];
  } else if (fs.existsSync(localDistExe)) {
    serverExe = localDistExe;
    serverArgs = [];
  } else if (fs.existsSync(venvPython)) {
    serverExe = venvPython;
    serverArgs = ['-m', 'hexton'];
    serverCwd = path.join(__dirname, '..', 'backend');
  }

  if (!serverExe) {
    console.warn('[Backend] No backend executable or venv found. Assuming external server.');
    return;
  }

  try {
    backendProcess = spawn(serverExe, serverArgs, {
      cwd: serverCwd,
      env: {
        ...process.env,
        HEXTON_HOST: '127.0.0.1',
        HEXTON_PORT: String(BACKEND_PORT),
        PYTHONUNBUFFERED: '1'
      },
      stdio: ['ignore', 'pipe', 'pipe']
    });

    backendProcess.stdout.on('data', (d) => {
      console.log(`[Python stdout] ${d.toString().trim()}`);
    });
    backendProcess.stderr.on('data', (d) => {
      console.log(`[Python stderr] ${d.toString().trim()}`);
    });

    backendProcess.on('exit', (code) => {
      console.log(`[Backend] Process exited with code ${code}`);
      backendProcess = null;
    });

    const isReady = await waitForBackend();
    if (!isReady) {
      console.warn('[Backend] Backend startup timeout. Continuing to open window...');
    } else {
      console.log('[Backend] Backend is ready and responding.');
    }
  } catch (err) {
    console.error('[Backend] Failed to spawn backend process:', err);
  }
}

/**
 * Creates the primary application window.
 */
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    frame: false, // Custom sleek dark titlebar
    backgroundColor: '#0A0A0C',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: true
    }
  });

  updateManager.setMainWindow(mainWindow);

  // Load backend web UI on 127.0.0.1:8787
  mainWindow.loadURL(BACKEND_URL);

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    mainWindow.focus();

    // Check for updates in background (non-blocking)
    setTimeout(() => {
      updateManager.checkForUpdates(false).catch(() => {});
    }, 4000);
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    // Open external web links in system browser
    if (url.startsWith('http:') || url.startsWith('https:')) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      // Optional close-to-tray or graceful quit
      isQuitting = true;
    }
  });
}

function createTray() {
  try {
    const iconPath = path.join(__dirname, '..', 'frontend', 'public', 'favicon.ico');
    let trayIcon = null;
    if (fs.existsSync(iconPath)) {
      trayIcon = nativeImage.createFromPath(iconPath);
    }

    if (!trayIcon) return;

    tray = new Tray(trayIcon);
    tray.setToolTip('Hexton Scheduler — Desktop');

    const contextMenu = Menu.buildFromTemplate([
      {
        label: 'Open Hexton Scheduler',
        click: () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
          }
        }
      },
      {
        label: 'Check for Updates',
        click: () => {
          updateManager.checkForUpdates(true);
        }
      },
      { type: 'separator' },
      {
        label: 'Quit',
        click: () => {
          isQuitting = true;
          app.quit();
        }
      }
    ]);

    tray.setContextMenu(contextMenu);
    tray.on('double-click', () => {
      if (mainWindow) {
        mainWindow.show();
        mainWindow.focus();
      }
    });
  } catch (err) {
    console.warn('[Tray] Could not initialize tray:', err.message);
  }
}

// 2. App Lifecycle
app.whenReady().then(async () => {
  // Initialize Licensing
  try {
    licensingService.init();
    console.log('[Licensing] Subsystem initialized. Plan:', licensingService.getStatus().plan);
  } catch (err) {
    console.error('[Licensing] Initialization error:', err);
  }

  // Register IPC Handlers
  setupIpcHandlers();

  // Ensure Backend Process
  await ensureBackendRunning();

  // Create Window & Tray
  createMainWindow();
  createTray();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on('before-quit', () => {
  isQuitting = true;
  if (backendProcess) {
    console.log('[Backend] Terminating local backend server process...');
    try {
      backendProcess.kill('SIGINT');
      setTimeout(() => {
        if (backendProcess) backendProcess.kill('SIGKILL');
      }, 2000);
    } catch {}
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// 3. IPC Handlers Setup
function setupIpcHandlers() {
  // Window controls
  ipcMain.handle('window:minimize', () => {
    if (mainWindow) mainWindow.minimize();
  });

  ipcMain.handle('window:maximize', () => {
    if (mainWindow) {
      if (mainWindow.isMaximized()) {
        mainWindow.unmaximize();
      } else {
        mainWindow.maximize();
      }
    }
  });

  ipcMain.handle('window:close', () => {
    if (mainWindow) mainWindow.close();
  });

  ipcMain.handle('window:isMaximized', () => {
    return mainWindow ? mainWindow.isMaximized() : false;
  });

  // Licensing
  ipcMain.handle('license:getStatus', () => {
    return licensingService.getStatus();
  });

  ipcMain.handle('license:activateKey', async (_e, { key, serverUrl }) => {
    return licensingService.activateOnlineKey(key, serverUrl);
  });

  ipcMain.handle('license:validate', async (_e, force) => {
    return licensingService.validateOnline(force);
  });

  ipcMain.handle('license:deactivate', async () => {
    return licensingService.deactivateDevice();
  });

  ipcMain.handle('license:checkFeature', (_e, featureName) => {
    return licensingService.checkFeature(featureName);
  });

  // Updater
  ipcMain.handle('updater:check', async (_e, manual) => {
    return updateManager.checkForUpdates(manual);
  });

  ipcMain.handle('updater:download', async () => {
    return updateManager.downloadUpdate();
  });

  ipcMain.handle('updater:quitAndInstall', () => {
    return updateManager.quitAndInstall();
  });

  ipcMain.handle('updater:getStatus', () => {
    return updateManager.getStatus();
  });

  // System
  ipcMain.handle('system:openExternal', (_e, url) => {
    if (url && (url.startsWith('http:') || url.startsWith('https:'))) {
      shell.openExternal(url);
    }
  });

  ipcMain.handle('system:openPath', (_e, dirPath) => {
    if (dirPath && fs.existsSync(dirPath)) {
      shell.openPath(dirPath);
    }
  });

  ipcMain.handle('system:getAppVersion', () => {
    return app.getVersion();
  });
}
