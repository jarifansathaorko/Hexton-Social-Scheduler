const fs = require('fs');
const crypto = require('crypto');
const path = require('path');

const rootDir = path.resolve(__dirname, '..');
const distDir = path.join(rootDir, 'dist', 'portable');
const pkg = JSON.parse(fs.readFileSync(path.join(rootDir, 'package.json'), 'utf8'));
const version = pkg.version;
const exeFilename = `Hexton Scheduler-${version}-Portable.exe`;
const exePath = path.join(distDir, exeFilename);

if (!fs.existsSync(exePath)) {
  // Check without space if needed
  const altExe = path.join(distDir, `Hexton-Scheduler-${version}-Portable.exe`);
  if (fs.existsSync(altExe)) {
    buildYml(altExe, path.basename(altExe));
  } else {
    console.error(`Cannot generate latest.yml: ${exePath} does not exist.`);
    process.exit(1);
  }
} else {
  buildYml(exePath, exeFilename);
}

function buildYml(targetPath, filename) {
  const buffer = fs.readFileSync(targetPath);
  const sha512 = crypto.createHash('sha512').update(buffer).digest('base64');
  const size = buffer.length;
  const isoDate = new Date().toISOString();

  const yamlContent = `version: ${version}
files:
  - url: ${filename}
    sha512: ${sha512}
    size: ${size}
path: ${filename}
sha512: ${sha512}
releaseDate: '${isoDate}'
`;

  const outputPath = path.join(distDir, 'latest.yml');
  fs.writeFileSync(outputPath, yamlContent, 'utf8');
  console.log(`✓ Generated ${outputPath}:\n${yamlContent}`);
}
