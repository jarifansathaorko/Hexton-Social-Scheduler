/**
 * Hexton Offline License Verifier
 * 
 * Verifies Ed25519 digital signatures, schema structure, product identifiers,
 * timestamp validity, and feature sets for offline commercial licenses.
 */

const crypto = require('crypto');
const {
  PRODUCT_ID,
  SCHEMA_VERSION,
  EMBEDDED_PUBLIC_KEY_PEM,
  LICENSE_STATUS,
  CLOCK_SKEW_TOLERANCE_MS
} = require('./licenseConstants');

/**
 * Deterministic JSON canonicalization.
 * Recursively sorts all object keys to ensure identical byte-for-byte serialization
 * during digital signature generation and verification.
 * 
 * @param {any} obj - Object, array, or primitive to canonicalize
 * @returns {string} - Deterministic canonical JSON string
 */
function canonicalizeJson(obj) {
  if (obj === null || typeof obj !== 'object') {
    return JSON.stringify(obj);
  }
  if (Array.isArray(obj)) {
    return '[' + obj.map(item => canonicalizeJson(item)).join(',') + ']';
  }
  const sortedKeys = Object.keys(obj).sort();
  return '{' + sortedKeys.map(key => `${JSON.stringify(key)}:${canonicalizeJson(obj[key])}`).join(',') + '}';
}

/**
 * Parses and verifies an offline signed license file or JSON object.
 * 
 * @param {string|object} rawInput - License content string or parsed object
 * @param {object} [options] - Verification overrides (e.g. custom now or publicKey for tests)
 * @returns {object} Verification outcome with status, payload, and descriptive diagnostics
 */
function verifyLicense(rawInput, options = {}) {
  const now = options.now ? new Date(options.now).getTime() : Date.now();
  const publicKeyPem = options.publicKeyPem || EMBEDDED_PUBLIC_KEY_PEM;

  // 1. Syntax & Input Type Check
  let licenseData;
  if (typeof rawInput === 'string') {
    const trimmed = rawInput.trim();
    if (!trimmed) {
      return {
        valid: false,
        status: LICENSE_STATUS.INVALID,
        error: 'License content is empty.'
      };
    }
    try {
      licenseData = JSON.parse(trimmed);
    } catch (err) {
      return {
        valid: false,
        status: LICENSE_STATUS.INVALID,
        error: `Malformed license file: Invalid JSON syntax (${err.message})`
      };
    }
  } else if (rawInput && typeof rawInput === 'object') {
    licenseData = rawInput;
  } else {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: 'Invalid license input: expected JSON string or object.'
    };
  }

  // 2. Envelope Structure Check
  if (!licenseData.payload || typeof licenseData.payload !== 'object' || Array.isArray(licenseData.payload)) {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: "Malformed license envelope: missing or invalid 'payload' object."
    };
  }
  if (!licenseData.signature || typeof licenseData.signature !== 'string') {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: "Malformed license envelope: missing or invalid 'signature' string."
    };
  }

  // 3. Cryptographic Signature Verification (Ed25519)
  let signatureBuffer;
  try {
    signatureBuffer = Buffer.from(licenseData.signature, 'base64');
    if (signatureBuffer.length !== 64) {
      return {
        valid: false,
        status: LICENSE_STATUS.INVALID,
        error: `Invalid signature format: expected 64-byte Ed25519 signature, got ${signatureBuffer.length} bytes.`
      };
    }
  } catch (err) {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: `Invalid signature encoding: ${err.message}`
    };
  }

  try {
    const canonicalPayload = canonicalizeJson(licenseData.payload);
    const messageBuffer = Buffer.from(canonicalPayload, 'utf8');
    const publicKey = crypto.createPublicKey(publicKeyPem);

    const isSigValid = crypto.verify(null, messageBuffer, publicKey, signatureBuffer);
    if (!isSigValid) {
      return {
        valid: false,
        status: LICENSE_STATUS.INVALID,
        error: 'Cryptographic signature verification failed: Payload has been tampered with or signing key does not match.'
      };
    }
  } catch (cryptoErr) {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: `Cryptographic verification error: ${cryptoErr.message}`
    };
  }

  // 4. Schema & Product ID Validation
  const payload = licenseData.payload;

  if (payload.version !== SCHEMA_VERSION) {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: `Unsupported license schema version: ${payload.version} (supported version: ${SCHEMA_VERSION}).`
    };
  }

  const pid = (payload.productId || '').toLowerCase();
  const validProducts = [PRODUCT_ID.toLowerCase(), 'clipxton', 'hexton', 'hexton-scheduler', 'hextonsystems'];
  if (!validProducts.includes(pid)) {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: `Product ID mismatch: License is for '${payload.productId || 'unknown'}', but this application is '${PRODUCT_ID}'.`
    };
  }

  // 5. Required Field Presence & Types
  if (!payload.licenseId || typeof payload.licenseId !== 'string' || !payload.licenseId.trim()) {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: "License payload missing required 'licenseId' identifier."
    };
  }

  if (!payload.plan || typeof payload.plan !== 'string' || !payload.plan.trim()) {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: "License payload missing required 'plan' tier."
    };
  }

  if (!payload.issuedAt || typeof payload.issuedAt !== 'string') {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: "License payload missing required 'issuedAt' timestamp."
    };
  }

  if (!Array.isArray(payload.features)) {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: "License payload 'features' must be an array of strings."
    };
  }

  // 6. Timestamp & Date Integrity Checks
  const issuedTime = Date.parse(payload.issuedAt);
  if (isNaN(issuedTime)) {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: `Invalid 'issuedAt' date format: '${payload.issuedAt}'. Must be valid ISO-8601.`
    };
  }

  if (issuedTime - now > CLOCK_SKEW_TOLERANCE_MS) {
    return {
      valid: false,
      status: LICENSE_STATUS.INVALID,
      error: 'License issue date is in the future. Check system clock.'
    };
  }

  // Expiration Check
  let isPerpetual = true;
  let daysRemaining = null;

  if (payload.expiresAt !== undefined && payload.expiresAt !== null) {
    isPerpetual = false;
    const expiryTime = Date.parse(payload.expiresAt);
    if (isNaN(expiryTime)) {
      return {
        valid: false,
        status: LICENSE_STATUS.INVALID,
        error: `Invalid 'expiresAt' date format: '${payload.expiresAt}'.`
      };
    }

    if (now >= expiryTime) {
      return {
        valid: false,
        status: LICENSE_STATUS.EXPIRED,
        error: `License expired on ${new Date(expiryTime).toISOString()}.`,
        payload: payload,
        daysRemaining: 0,
        isPerpetual: false
      };
    }

    const msRemaining = expiryTime - now;
    daysRemaining = Math.max(0, Math.ceil(msRemaining / (1000 * 60 * 60 * 24)));
  }

  return {
    valid: true,
    status: LICENSE_STATUS.ACTIVE,
    error: null,
    payload: payload,
    isPerpetual: isPerpetual,
    daysRemaining: daysRemaining
  };
}

module.exports = {
  canonicalizeJson,
  verifyLicense
};
