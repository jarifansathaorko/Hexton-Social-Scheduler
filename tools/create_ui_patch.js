#!/usr/bin/env node
/**
 * create_ui_patch.js
 * Creates a lightweight UI patch zip for Hexton Scheduler OTA updates.
 *
 * Compresses the compiled frontend/dist directory into:
 *   dist/portable/Hexton-Scheduler-<version>-ui-patch.zip
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const rootDir = path.resolve(__dirname, '..');
const distDir = path.join(rootDir, 'dist', 'portable');
const frontendDistDir = path.join(rootDir, 'frontend', 'dist');
const pkgPath = path.join(rootDir, 'package.json');

const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
const version = pkg.version;

if (!fs.existsSync(frontendDistDir)) {
  console.error(`[ERROR] Frontend build not found at ${frontendDistDir}. Run 'npm --prefix frontend run build' first.`);
  process.exit(1);
}

if (!fs.existsSync(distDir)) {
  fs.mkdirSync(distDir, { recursive: true });
}

const patchZipName = `Hexton-Scheduler-${version}-ui-patch.zip`;
const patchZipPath = path.join(distDir, patchZipName);

if (fs.existsSync(patchZipPath)) {
  fs.unlinkSync(patchZipPath);
}

console.log(`[INFO] Compressing ${frontendDistDir} -> ${patchZipPath}...`);

// Use PowerShell Compress-Archive
const psCmd = `Compress-Archive -Path '${frontendDistDir}\\*' -DestinationPath '${patchZipPath}' -Force`;
execSync(`powershell -NoProfile -Command "${psCmd}"`, { stdio: 'inherit' });

if (fs.existsSync(patchZipPath)) {
  const stat = fs.statSync(patchZipPath);
  console.log(`✓ UI Patch Zip created: ${patchZipName} (${(stat.size / 1024).toFixed(1)} KB)`);
} else {
  console.error(`[ERROR] Failed to create ${patchZipPath}`);
  process.exit(1);
}
