/**
 * Hexton Hybrid Licensing Service Module
 * 
 * Manages both:
 * 1. Online Activated Licenses (Cloudflare Workers + D1 backend, device limits, 7-day offline grace period)
 * 2. Offline Cryptographically Signed Licenses (Ed25519 public key verification)
 */

const fs = require('fs');
const path = require('path');
let app = null;
try {
  ({ app } = require('electron'));
} catch {
  // Pure node runtime fallback
}
const {
  LICENSE_STATUS,
  LICENSE_PLANS,
  DEFAULT_FREE_FEATURES,
  GRACE_PERIOD_MS,
  PERIODIC_VALIDATION_INTERVAL_MS,
  DEFAULT_LICENSING_SERVER_URL
} = require('./licenseConstants');
const { verifyLicense } = require('./licenseVerifier');
const { getDeviceIdentity } = require('./deviceIdentity');
const { onlineLicenseClient } = require('./onlineLicenseClient');

class LicensingService {
  constructor(customStorageDir = null, customServerUrl = null) {
    this.customStorageDir = customStorageDir;
    this.serverUrl = customServerUrl || DEFAULT_LICENSING_SERVER_URL;
    this.storageDir = null;
    this.licenseFilePath = null;        // Offline signed license (hexton.license)
    this.onlineLicenseFilePath = null;  // Online activation data (hexton_online.json)
    this.deviceIdentity = null;
    this.validationTimer = null;
    this.cachedState = this._createDefaultUnlicensedState();
  }

  /**
   * Initializes the service, loads device identity, loads local license records,
   * and starts periodic background validation.
   */
  init(storageDirOverride = null) {
    if (storageDirOverride) {
      this.storageDir = path.resolve(storageDirOverride);
    } else if (this.customStorageDir) {
      this.storageDir = path.resolve(this.customStorageDir);
    } else if (process.env.HEXTON_USER_DATA) {
      this.storageDir = path.resolve(process.env.HEXTON_USER_DATA);
    } else {
      try {
        if (app && typeof app.getPath === 'function') {
          this.storageDir = path.join(app.getPath('userData'), '.license_store');
        }
      } catch {
        this.storageDir = path.resolve(process.cwd(), '.license_store');
      }
    }

    if (!this.storageDir) {
      this.storageDir = path.resolve(process.cwd(), '.license_store');
    }

    if (!fs.existsSync(this.storageDir)) {
      try {
        fs.mkdirSync(this.storageDir, { recursive: true });
      } catch (err) {
        console.error(`[LicensingService] Failed to create storage dir: ${err.message}`);
      }
    }

    this.deviceIdentity = getDeviceIdentity(this.storageDir);
    this.licenseFilePath = path.join(this.storageDir, 'hexton.license');
    this.onlineLicenseFilePath = path.join(this.storageDir, 'hexton_online.json');

    this.reload();
    this.startPeriodicValidation();

    return this.getStatus();
  }

  /**
   * Evaluates local license files (online activation record first, then offline signed file).
   */
  reload() {
    const now = Date.now();

    // 1. Check Online Activation Record
    if (fs.existsSync(this.onlineLicenseFilePath)) {
      try {
        const rawOnline = JSON.parse(fs.readFileSync(this.onlineLicenseFilePath, 'utf8'));
        const evaluated = this._evaluateOnlineLicense(rawOnline, now);
        if (evaluated) {
          this.cachedState = evaluated;
          return evaluated;
        }
      } catch (e) {
        console.warn('[LicensingService] Failed to read online license cache:', e.message);
      }
    }

    // 2. Check Offline Cryptographic File
    if (fs.existsSync(this.licenseFilePath)) {
      try {
        const rawContent = fs.readFileSync(this.licenseFilePath, 'utf8');
        const verification = verifyLicense(rawContent, { now });
        if (verification.valid) {
          this.cachedState = {
            licensed: true,
            status: verification.status,
            type: 'offline',
            plan: verification.payload.plan,
            customerName: verification.payload.customerName || 'Offline Licensee',
            expiresAt: verification.payload.expiresAt || null,
            daysRemaining: verification.daysRemaining,
            isPerpetual: verification.isPerpetual,
            features: verification.payload.features || [],
            licenseId: verification.payload.licenseId,
            licenseKeyMasked: 'OFFLINE-SIGNED-KEY',
            deviceId: this.deviceIdentity ? this.deviceIdentity.deviceId : null,
            serverUrl: this.serverUrl,
            error: null
          };
          return this.cachedState;
        }
      } catch (e) {
        console.warn('[LicensingService] Failed to evaluate offline license:', e.message);
      }
    }

    this.cachedState = this._createDefaultUnlicensedState();
    return this.cachedState;
  }

  /**
   * Returns the current cached licensing state.
   */
  getStatus() {
    return {
      ...this.cachedState,
      deviceId: this.deviceIdentity ? this.deviceIdentity.deviceId : null,
      deviceName: this.deviceIdentity ? this.deviceIdentity.deviceName : null
    };
  }

  /**
   * Checks whether a specific feature flag is granted under current license.
   */
  checkFeature(featureName) {
    if (!featureName) return false;
    const features = this.cachedState.features || [];
    return features.includes(featureName) || features.includes('*');
  }

  /**
   * Activates an online license key against the Cloudflare Workers backend.
   */
  async activateOnlineKey(licenseKey, serverUrl = null) {
    const targetServer = serverUrl || this.serverUrl;
    if (!licenseKey || typeof licenseKey !== 'string') {
      return { success: false, error: 'License key must be a non-empty string.' };
    }

    const appVer = (app && typeof app.getVersion === 'function') ? app.getVersion() : '1.0.0';
    const result = await onlineLicenseClient.activate(targetServer, licenseKey.trim(), this.deviceIdentity, appVer);

    if (!result.ok) {
      return { success: false, error: result.error || 'Activation failed.' };
    }

    const data = result.data || {};
    const record = {
      licenseKey: licenseKey.trim(),
      serverUrl: targetServer,
      plan: data.license?.plan || LICENSE_PLANS.PRO,
      customerName: data.license?.customer_name || 'Valued Creator',
      features: data.license?.features || [
        'multi_channel',
        'smart_scheduling',
        'custom_excel_exports',
        'automatic_sheets_sync',
        'advanced_analytics',
        'unlimited_channels'
      ],
      expiresAt: data.license?.expires_at || null,
      lastValidatedAt: new Date().toISOString(),
      activatedAt: new Date().toISOString(),
      status: 'active'
    };

    try {
      fs.writeFileSync(this.onlineLicenseFilePath, JSON.stringify(record, null, 2), { encoding: 'utf8', mode: 0o600 });
    } catch (err) {
      return { success: false, error: `Failed to persist license cache: ${err.message}` };
    }

    this.reload();
    return { success: true, state: this.getStatus() };
  }

  /**
   * Performs an online check-in to confirm license status.
   */
  async validateOnline(force = false) {
    if (!fs.existsSync(this.onlineLicenseFilePath)) {
      return { success: false, error: 'No active online license record.' };
    }

    let record;
    try {
      record = JSON.parse(fs.readFileSync(this.onlineLicenseFilePath, 'utf8'));
    } catch {
      return { success: false, error: 'Failed to read local license record.' };
    }

    const now = Date.now();
    const lastValidated = record.lastValidatedAt ? new Date(record.lastValidatedAt).getTime() : 0;
    if (!force && (now - lastValidated < PERIODIC_VALIDATION_INTERVAL_MS)) {
      return { success: true, cached: true, state: this.getStatus() };
    }

    const appVer = (app && typeof app.getVersion === 'function') ? app.getVersion() : '1.0.0';
    const result = await onlineLicenseClient.validate(record.serverUrl || this.serverUrl, record.licenseKey, this.deviceIdentity.deviceId, appVer);

    if (result.isNetworkError) {
      return { success: true, graceActive: true, state: this.getStatus() };
    }

    if (!result.ok) {
      record.status = 'revoked';
      try {
        fs.writeFileSync(this.onlineLicenseFilePath, JSON.stringify(record, null, 2), { encoding: 'utf8', mode: 0o600 });
      } catch {}
      this.reload();
      return { success: false, error: result.error || 'Validation rejected by server.' };
    }

    record.lastValidatedAt = new Date().toISOString();
    if (result.data?.license?.expires_at !== undefined) {
      record.expiresAt = result.data.license.expires_at;
    }
    if (result.data?.license?.plan) {
      record.plan = result.data.license.plan;
    }

    try {
      fs.writeFileSync(this.onlineLicenseFilePath, JSON.stringify(record, null, 2), { encoding: 'utf8', mode: 0o600 });
    } catch {}

    this.reload();
    return { success: true, state: this.getStatus() };
  }

  /**
   * Deactivates the current machine.
   */
  async deactivateDevice() {
    if (fs.existsSync(this.onlineLicenseFilePath)) {
      try {
        const record = JSON.parse(fs.readFileSync(this.onlineLicenseFilePath, 'utf8'));
        if (record && record.licenseKey) {
          await onlineLicenseClient.deactivate(record.serverUrl || this.serverUrl, record.licenseKey, this.deviceIdentity.deviceId);
        }
      } catch {}

      try {
        fs.unlinkSync(this.onlineLicenseFilePath);
      } catch {}
    }

    if (fs.existsSync(this.licenseFilePath)) {
      try {
        fs.unlinkSync(this.licenseFilePath);
      } catch {}
    }

    this.reload();
    return { success: true, state: this.getStatus() };
  }

  startPeriodicValidation() {
    if (this.validationTimer) clearInterval(this.validationTimer);
    this.validationTimer = setInterval(() => {
      this.validateOnline(false).catch(() => {});
    }, PERIODIC_VALIDATION_INTERVAL_MS);
    if (this.validationTimer && typeof this.validationTimer.unref === 'function') {
      this.validationTimer.unref();
    }
  }

  _evaluateOnlineLicense(raw, now) {
    if (!raw || !raw.licenseKey) return null;
    const lastValidated = raw.lastValidatedAt ? new Date(raw.lastValidatedAt).getTime() : 0;
    const timeSinceValidation = now - lastValidated;

    let isPerpetual = true;
    let daysRemaining = null;

    if (raw.expiresAt) {
      isPerpetual = false;
      const expiry = new Date(raw.expiresAt).getTime();
      if (now >= expiry) {
        return {
          licensed: false,
          status: LICENSE_STATUS.EXPIRED,
          type: 'online',
          plan: raw.plan || LICENSE_PLANS.FREE,
          customerName: raw.customerName,
          expiresAt: raw.expiresAt,
          daysRemaining: 0,
          isPerpetual: false,
          features: DEFAULT_FREE_FEATURES,
          licenseKeyMasked: this._maskKey(raw.licenseKey),
          error: 'License subscription has expired.'
        };
      }
      daysRemaining = Math.max(0, Math.ceil((expiry - now) / (1000 * 60 * 60 * 24)));
    }

    if (raw.status === 'revoked') {
      return {
        licensed: false,
        status: LICENSE_STATUS.REVOKED,
        type: 'online',
        plan: LICENSE_PLANS.FREE,
        customerName: raw.customerName,
        expiresAt: raw.expiresAt,
        daysRemaining: 0,
        isPerpetual,
        features: DEFAULT_FREE_FEATURES,
        licenseKeyMasked: this._maskKey(raw.licenseKey),
        error: 'License was deactivated or revoked.'
      };
    }

    let status = LICENSE_STATUS.ACTIVE;
    if (timeSinceValidation > GRACE_PERIOD_MS) {
      status = LICENSE_STATUS.GRACE_EXPIRED;
    } else if (timeSinceValidation > PERIODIC_VALIDATION_INTERVAL_MS * 2) {
      status = LICENSE_STATUS.GRACE_PERIOD;
    }

    return {
      licensed: status !== LICENSE_STATUS.GRACE_EXPIRED,
      status,
      type: 'online',
      plan: raw.plan || LICENSE_PLANS.PRO,
      customerName: raw.customerName || 'Valued Creator',
      expiresAt: raw.expiresAt || null,
      daysRemaining,
      isPerpetual,
      features: raw.features || [
        'multi_channel',
        'smart_scheduling',
        'custom_excel_exports',
        'automatic_sheets_sync',
        'advanced_analytics',
        'unlimited_channels'
      ],
      licenseKeyMasked: this._maskKey(raw.licenseKey),
      lastValidatedAt: raw.lastValidatedAt,
      error: status === LICENSE_STATUS.GRACE_EXPIRED ? 'Offline grace period exceeded. Please connect to internet to verify.' : null
    };
  }

  _maskKey(key) {
    if (!key) return '';
    const clean = key.trim();
    if (clean.length < 8) return '****';
    return `${clean.slice(0, 4)}-****-****-${clean.slice(-4)}`;
  }

  _createDefaultUnlicensedState() {
    return {
      licensed: false,
      status: LICENSE_STATUS.UNLICENSED,
      type: 'none',
      plan: LICENSE_PLANS.FREE,
      customerName: 'Trial User',
      expiresAt: null,
      daysRemaining: null,
      isPerpetual: false,
      features: DEFAULT_FREE_FEATURES,
      licenseKeyMasked: null,
      error: null
    };
  }
}

const licensingService = new LicensingService();

module.exports = {
  LicensingService,
  licensingService
};
