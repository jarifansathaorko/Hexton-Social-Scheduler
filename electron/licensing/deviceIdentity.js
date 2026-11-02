/**
 * Hexton Persistent Device Identity Module
 * 
 * Generates and stores a persistent, unique machine identifier
 * for device-count licensing on desktop installations.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');

/**
 * Returns or creates a persistent device identity in the specified storage directory.
 * @param {string} storageDir - Target directory (e.g. app.getPath('userData'))
 * @returns {{deviceId: string, deviceName: string, platform: string, arch: string}}
 */
function getDeviceIdentity(storageDir = null) {
  const dir = storageDir || (process.env.HEXTON_USER_DATA ? path.resolve(process.env.HEXTON_USER_DATA) : path.resolve(process.cwd(), '.license_store'));
  const filePath = path.join(dir, 'device_id.json');

  let persistentUuid = null;

  if (fs.existsSync(filePath)) {
    try {
      const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      if (data && data.uuid && typeof data.uuid === 'string') {
        persistentUuid = data.uuid;
      }
    } catch (e) {
      // Corrupted file, will regenerate
    }
  }

  if (!persistentUuid) {
    persistentUuid = crypto.randomUUID();
    try {
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(filePath, JSON.stringify({
        uuid: persistentUuid,
        created_at: new Date().toISOString()
      }, null, 2), { encoding: 'utf8', mode: 0o600 });
    } catch (e) {
      console.warn('[DeviceIdentity] Warning writing device_id.json:', e.message);
    }
  }

  // Combine machine characteristics with persistent UUID into deterministic hash
  const rawFingerprint = `${os.hostname()}|${os.platform()}|${os.arch()}|${persistentUuid}`;
  const deviceIdHash = crypto.createHash('sha256').update(rawFingerprint).digest('hex').slice(0, 32);
  const deviceId = `hex_dev_${deviceIdHash}`;

  const deviceName = os.hostname() || `${os.platform()}-desktop`;
  const platform = process.platform || os.platform();
  const arch = process.arch || os.arch();

  return {
    deviceId,
    deviceName,
    platform,
    arch
  };
}

module.exports = {
  getDeviceIdentity
};
