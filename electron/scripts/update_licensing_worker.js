const fs = require('fs');
const path = require('path');

const workerDir = 'E:/project/Clipxton/services/licensing-worker';
const indexFile = path.join(workerDir, 'src/index.js');
const adminHtmlFile = path.join(workerDir, 'src/admin.html');
const adminJsFile = path.join(workerDir, 'src/adminHtml.js');

// 1. Sync admin.html to adminHtml.js
const htmlContent = fs.readFileSync(adminHtmlFile, 'utf8');
const jsOutput = 'export default ' + JSON.stringify(htmlContent) + ';\n';
fs.writeFileSync(adminJsFile, jsOutput, 'utf8');
console.log('✓ Successfully synchronized admin.html -> adminHtml.js');

// 2. Update index.js with SCHEDULER_FEATURES and dynamic allocation
let indexContent = fs.readFileSync(indexFile, 'utf8');

const target1 = `const DEFAULT_FEATURES = [
  'cloud_ai',
  'batch_export',
  'export_4k',
  'all_subtitles',
  'pro_audio',
  'unlimited_projects'
];`;

const extraFeatures = `

const SCHEDULER_FEATURES = [
  'multi_channel',
  'smart_scheduling',
  'custom_excel_exports',
  'automatic_sheets_sync',
  'advanced_analytics',
  'unlimited_channels',
  'creator_approval_flow'
];

const SUITE_FEATURES = [
  ...DEFAULT_FEATURES,
  ...SCHEDULER_FEATURES
];`;

if (!indexContent.includes('SCHEDULER_FEATURES')) {
  if (indexContent.includes(target1)) {
    indexContent = indexContent.replace(target1, target1 + extraFeatures);
  } else if (indexContent.includes(target1.replace(/\n/g, '\r\n'))) {
    indexContent = indexContent.replace(target1.replace(/\n/g, '\r\n'), target1.replace(/\n/g, '\r\n') + extraFeatures.replace(/\n/g, '\r\n'));
  }
  console.log('✓ Added SCHEDULER_FEATURES and SUITE_FEATURES to index.js');
}

const keyGenTarget = 'const rawKey = generateLicenseKey(plan);';
const allocBlock = `const pLower = (plan || 'pro').toLowerCase();
          let allocatedFeatures = DEFAULT_FEATURES;
          if (pLower === 'scheduler' || pLower === 'hexton' || pLower.includes('sched')) {
            allocatedFeatures = SCHEDULER_FEATURES;
          } else if (pLower === 'suite' || pLower === 'bundle' || pLower === 'all') {
            allocatedFeatures = SUITE_FEATURES;
          }
          const rawKey = generateLicenseKey(plan);`;

if (!indexContent.includes('allocatedFeatures')) {
  if (indexContent.includes(keyGenTarget)) {
    indexContent = indexContent.replace(keyGenTarget, allocBlock);
  } else if (indexContent.includes(keyGenTarget.replace(/\n/g, '\r\n'))) {
    indexContent = indexContent.replace(keyGenTarget.replace(/\n/g, '\r\n'), allocBlock.replace(/\n/g, '\r\n'));
  }
  indexContent = indexContent.replace('JSON.stringify(DEFAULT_FEATURES)', 'JSON.stringify(allocatedFeatures)');
  console.log('✓ Updated license issuance to bind specific plan features in D1');
}

fs.writeFileSync(indexFile, indexContent, 'utf8');
console.log('✓ Successfully wrote updated index.js');
