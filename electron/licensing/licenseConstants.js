/**
 * Hexton Scheduler Licensing Constants and Configuration
 * 
 * Integrated with the unified Cloudflare Workers & D1 Licensing Panel.
 */

const PRODUCT_ID = 'hexton';
const SCHEMA_VERSION = 1;

// Embedded Ed25519 Public Verification Key (SPKI PEM)
const EMBEDDED_PUBLIC_KEY_PEM = `-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAzDuIqj08Y52RrtSMcGntOY41EV1cj4v0jwsvxmi225g=
-----END PUBLIC KEY-----`;

const LICENSE_STATUS = Object.freeze({
  ACTIVE: 'active',
  EXPIRED: 'expired',
  INVALID: 'invalid',
  UNLICENSED: 'unlicensed',
  GRACE_PERIOD: 'grace_period',
  GRACE_EXPIRED: 'grace_expired',
  REVOKED: 'revoked',
  DEACTIVATED: 'deactivated'
});

const LICENSE_PLANS = Object.freeze({
  FREE: 'free',
  TRIAL: 'trial',
  CREATOR: 'creator',
  PRO: 'pro',
  STUDIO: 'studio',
  PERPETUAL: 'perpetual'
});

const FEATURES = Object.freeze({
  MULTI_CHANNEL: 'multi_channel',
  SMART_SCHEDULING: 'smart_scheduling',
  CUSTOM_EXCEL_EXPORTS: 'custom_excel_exports',
  AUTOMATIC_SHEETS_SYNC: 'automatic_sheets_sync',
  ADVANCED_ANALYTICS: 'advanced_analytics',
  UNLIMITED_CHANNELS: 'unlimited_channels',
  CREATOR_APPROVAL_FLOW: 'creator_approval_flow'
});

const DEFAULT_FREE_FEATURES = Object.freeze([
  'single_channel',
  'basic_scheduling',
  'standard_reports'
]);

// 7 days offline grace period tolerance for continuous operability
const GRACE_PERIOD_MS = 7 * 24 * 60 * 60 * 1000;
const CLOCK_SKEW_TOLERANCE_MS = 24 * 60 * 60 * 1000;
const PERIODIC_VALIDATION_INTERVAL_MS = 6 * 60 * 60 * 1000;

// Shared Cloudflare Workers endpoint with Clipxton
const DEFAULT_LICENSING_SERVER_URL = process.env.HEXTON_LICENSING_SERVER || 
  process.env.CLIPXTON_LICENSING_SERVER || 
  'https://clipxton-licensing.jarif-aorko.workers.dev';

module.exports = {
  PRODUCT_ID,
  SCHEMA_VERSION,
  EMBEDDED_PUBLIC_KEY_PEM,
  LICENSE_STATUS,
  LICENSE_PLANS,
  FEATURES,
  DEFAULT_FREE_FEATURES,
  GRACE_PERIOD_MS,
  CLOCK_SKEW_TOLERANCE_MS,
  PERIODIC_VALIDATION_INTERVAL_MS,
  DEFAULT_LICENSING_SERVER_URL
};
