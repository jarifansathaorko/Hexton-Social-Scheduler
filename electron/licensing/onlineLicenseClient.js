/**
 * Hexton Online Licensing Client
 * 
 * Communicates with the Cloudflare Worker backend for:
 * - Device Activation
 * - Background License Validation
 * - Device Deactivation
 */

class OnlineLicenseClient {
  constructor(timeoutMs = 10000) {
    this.timeoutMs = timeoutMs;
  }

  async _request(serverUrl, endpoint, method = 'POST', body = null, customTimeout = null) {
    const cleanBase = (serverUrl || '').replace(/\/+$/, '');
    const fullUrl = `${cleanBase}${endpoint}`;

    const timeout = customTimeout || this.timeoutMs;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
      const res = await fetch(fullUrl, {
        method,
        headers: {
          'Content-Type': 'application/json',
          'User-Agent': 'Hexton-Desktop-Client/1.0.0'
        },
        body: body ? JSON.stringify(body) : null,
        signal: controller.signal
      });

      clearTimeout(timeoutId);

      const data = await res.json().catch(() => ({}));

      return {
        ok: res.ok,
        status: res.status,
        data: data,
        isNetworkError: false,
        error: !res.ok ? (data.error || `HTTP ${res.status}`) : null
      };
    } catch (err) {
      clearTimeout(timeoutId);
      const isTimeout = err.name === 'AbortError';
      const msg = isTimeout 
        ? 'Connection timed out while reaching licensing server.'
        : `Network connectivity error: ${err.message}`;

      return {
        ok: false,
        status: 0,
        data: null,
        isNetworkError: true,
        error: msg
      };
    }
  }

  /**
   * Activates a license key on the current device.
   */
  async activate(serverUrl, licenseKey, deviceIdentity, appVersion = '1.0.0') {
    return this._request(serverUrl, '/api/v1/activate', 'POST', {
      license_key: licenseKey,
      device_id: deviceIdentity.deviceId,
      device_name: deviceIdentity.deviceName,
      platform: deviceIdentity.platform,
      app_version: appVersion
    });
  }

  /**
   * Validates an existing device activation against the server.
   */
  async validate(serverUrl, licenseKey, deviceId, appVersion = '1.0.0') {
    return this._request(serverUrl, '/api/v1/validate', 'POST', {
      license_key: licenseKey,
      device_id: deviceId,
      app_version: appVersion
    });
  }

  /**
   * Deactivates the current device on the server.
   */
  async deactivate(serverUrl, licenseKey, deviceId) {
    return this._request(serverUrl, '/api/v1/deactivate', 'POST', {
      license_key: licenseKey,
      device_id: deviceId
    });
  }
}

const onlineLicenseClient = new OnlineLicenseClient();

module.exports = {
  OnlineLicenseClient,
  onlineLicenseClient
};
