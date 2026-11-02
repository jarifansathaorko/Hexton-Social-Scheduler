/**
 * Hexton Scheduler — GitHub Release Publishing Utility
 * 
 * Publishes release binaries, latest.yml metadata, and SHA256 checksums
 * to the GitHub repository: jarifansathaorko/Hexton-Social-Scheduler
 */

const fs = require('fs');
const path = require('path');
const https = require('https');
const { execSync } = require('child_process');

const REPO_ROOT = path.resolve(__dirname, '..');
const DIST_DIR = path.join(REPO_ROOT, 'dist', 'portable');
const PACKAGE_JSON = path.join(REPO_ROOT, 'package.json');

const pkg = JSON.parse(fs.readFileSync(PACKAGE_JSON, 'utf8'));
const version = pkg.version;
const tag = `v${version}`;
const releaseName = `Hexton Scheduler v${version} — Production Release`;

function getGitHubToken() {
    if (process.env.GITHUB_TOKEN) {
        return process.env.GITHUB_TOKEN.trim();
    }
    try {
        const stdout = execSync('git credential fill', {
            input: 'protocol=https\nhost=github.com\n\n',
            encoding: 'utf8',
            stdio: ['pipe', 'pipe', 'ignore']
        });
        const match = stdout.match(/^password=(.+)$/m);
        if (match && match[1]) {
            return match[1].trim();
        }
    } catch (e) {}
    throw new Error('Unable to resolve GitHub personal access token from environment or git credential manager.');
}

function request(options, body = null) {
    return new Promise((resolve, reject) => {
        const req = https.request(options, (res) => {
            let data = Buffer.alloc(0);
            res.on('data', (chunk) => {
                data = Buffer.concat([data, chunk]);
            });
            res.on('end', () => {
                const text = data.toString('utf8');
                let parsed = null;
                try {
                    parsed = JSON.parse(text);
                } catch (e) {
                    parsed = text;
                }
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    resolve({ statusCode: res.statusCode, headers: res.headers, data: parsed });
                } else {
                    const err = new Error(`HTTP ${res.statusCode}: ${typeof parsed === 'string' ? parsed : JSON.stringify(parsed)}`);
                    err.statusCode = res.statusCode;
                    err.data = parsed;
                    reject(err);
                }
            });
        });
        req.on('error', reject);
        if (body) {
            req.write(body);
        }
        req.end();
    });
}

async function uploadAsset(uploadUrlTemplate, assetPath, token) {
    const filename = path.basename(assetPath);
    const stat = fs.statSync(assetPath);
    const uploadUrl = uploadUrlTemplate.replace(/\{(\?name,label|name)\}/, `?name=${encodeURIComponent(filename)}`);
    const urlObj = new URL(uploadUrl);

    console.log(`  Uploading ${filename} (${(stat.size / 1024 / 1024).toFixed(2)} MB)...`);

    const contentType = filename.endsWith('.yml') || filename.endsWith('.txt')
        ? 'text/plain; charset=utf-8'
        : 'application/octet-stream';

    return new Promise((resolve, reject) => {
        const fileStream = fs.createReadStream(assetPath);
        const req = https.request({
            hostname: urlObj.hostname,
            path: urlObj.pathname + urlObj.search,
            method: 'POST',
            headers: {
                'Authorization': `token ${token}`,
                'User-Agent': 'Hexton-Release-Publisher',
                'Content-Type': contentType,
                'Content-Length': stat.size,
                'Accept': 'application/vnd.github.v3+json'
            }
        }, (res) => {
            let resData = '';
            res.on('data', chunk => { resData += chunk; });
            res.on('end', () => {
                if (res.statusCode >= 200 && res.statusCode < 300) {
                    console.log(`  ✓ Successfully uploaded: ${filename}`);
                    resolve(JSON.parse(resData));
                } else {
                    reject(new Error(`Failed to upload ${filename}: HTTP ${res.statusCode} ${resData}`));
                }
            });
        });

        req.on('error', reject);
        fileStream.pipe(req);
    });
}

async function uploadAssetWithRetry(uploadUrlTemplate, assetPath, token, maxRetries = 3) {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            return await uploadAsset(uploadUrlTemplate, assetPath, token);
        } catch (err) {
            console.warn(`  Upload attempt ${attempt}/${maxRetries} failed: ${err.message}`);
            if (attempt === maxRetries) {
                throw err;
            }
            console.log(`  Retrying in 5 seconds...`);
            await new Promise(r => setTimeout(r, 5000));
        }
    }
}

async function publishToRepo(owner, repo, tag, releaseName, releaseBody, assets, token) {
    console.log(`\n==========================================================`);
    console.log(`Publishing release ${tag} to ${owner}/${repo}`);
    console.log(`==========================================================`);

    // 1. Get or create release
    let release = null;
    try {
        const res = await request({
            hostname: 'api.github.com',
            path: `/repos/${owner}/${repo}/releases/tags/${tag}`,
            method: 'GET',
            headers: {
                'Authorization': `token ${token}`,
                'User-Agent': 'Hexton-Release-Publisher',
                'Accept': 'application/vnd.github.v3+json'
            }
        });
        release = res.data;
        console.log(`Found existing release: ${release.name} (id: ${release.id})`);
    } catch (e) {
        if (e.statusCode === 404) {
            console.log(`Release for tag ${tag} not found. Creating release...`);
            const createRes = await request({
                hostname: 'api.github.com',
                path: `/repos/${owner}/${repo}/releases`,
                method: 'POST',
                headers: {
                    'Authorization': `token ${token}`,
                    'User-Agent': 'Hexton-Release-Publisher',
                    'Content-Type': 'application/json',
                    'Accept': 'application/vnd.github.v3+json'
                }
            }, JSON.stringify({
                tag_name: tag,
                name: releaseName,
                body: releaseBody,
                draft: false,
                prerelease: false
            }));
            release = createRes.data;
            console.log(`Created release id: ${release.id}`);
        } else {
            throw e;
        }
    }

    // 2. Fetch existing assets
    const existingAssetsRes = await request({
        hostname: 'api.github.com',
        path: `/repos/${owner}/${repo}/releases/${release.id}/assets`,
        method: 'GET',
        headers: {
            'Authorization': `token ${token}`,
            'User-Agent': 'Hexton-Release-Publisher',
            'Accept': 'application/vnd.github.v3+json'
        }
    });

    const existingAssets = existingAssetsRes.data;

    // 3. Upload assets
    for (const assetPath of assets) {
        const filename = path.basename(assetPath);
        const stat = fs.statSync(assetPath);
        const match = existingAssets.find(a => a.name === filename);

        if (match) {
            if (match.size === stat.size && match.state === 'uploaded' && !filename.endsWith('.yml') && !filename.endsWith('.txt')) {
                console.log(`  ✓ Existing asset ${filename} is up-to-date (${(stat.size / 1024 / 1024).toFixed(2)} MB). Skipping.`);
                continue;
            } else {
                console.log(`  Replacing outdated asset: ${filename} (id: ${match.id})...`);
                await request({
                    hostname: 'api.github.com',
                    path: `/repos/${owner}/${repo}/releases/assets/${match.id}`,
                    method: 'DELETE',
                    headers: {
                        'Authorization': `token ${token}`,
                        'User-Agent': 'Hexton-Release-Publisher',
                        'Accept': 'application/vnd.github.v3+json'
                    }
                });
            }
        }

        await uploadAssetWithRetry(release.upload_url, assetPath, token);
    }

    console.log(`✓ Release ${tag} successfully published to https://github.com/${owner}/${repo}/releases/tag/${tag}`);
    return release;
}

async function main() {
    console.log(`\n==========================================================`);
    console.log(`   HEXTON SCHEDULER — GITHUB RELEASE PUBLISHER            `);
    console.log(`==========================================================`);

    const token = getGitHubToken();
    console.log(`✓ GitHub token resolved from Windows Credential Manager.`);

    // Check available assets
    const candidateFiles = [
        path.join(DIST_DIR, `Hexton Scheduler-${version}-Portable.exe`),
        path.join(DIST_DIR, `Hexton-Scheduler-${version}-Portable.exe`),
        path.join(DIST_DIR, 'latest.yml'),
        path.join(DIST_DIR, 'SHA256SUMS.txt'),
        path.join(DIST_DIR, `Hexton-Scheduler-${version}-ui-patch.zip`)
    ];

    const assets = candidateFiles.filter(f => fs.existsSync(f));
    if (assets.length === 0) {
        console.error(`[ERROR] No release artifacts found in ${DIST_DIR}. Please run build-portable.ps1 first.`);
        process.exit(1);
    }

    console.log(`Found ${assets.length} artifacts to publish:`);
    assets.forEach(a => console.log(`  - ${path.basename(a)} (${(fs.statSync(a).size / 1024 / 1024).toFixed(2)} MB)`));

    const releaseBody = `## 🚀 Hexton Scheduler v${version} — Standalone Portable Release

### ✨ Key Features & Optimizations:
- **Standalone Portable Architecture:** Zero installation required; single standalone executable with zero background dependencies.
- **Frameless Dark UI & Desktop Controls:** Modern desktop titlebar, native minimize/maximize/close, and worker health pulse.
- **Unified Cloudflare Licensing:** Hardware device seat binding connected directly to your Cloudflare Workers & D1 licensing panel.
- **Dual-Mode OTA Updater:** Automatic background checks with in-app live progress bar and one-click restart.
- **Algorithmic Cadence & Peak Hour Optimizer:** Recommends optimal schedule slots, audience peak hour scoring, and multi-channel collision clearance.
- **Simplified Reporting & Excel Export:** Custom column selection, date range filtering, live preview, CSV, and Excel (.xls) exports.

### 📦 Artifacts:
\`\`\`
${fs.existsSync(path.join(DIST_DIR, 'SHA256SUMS.txt')) ? fs.readFileSync(path.join(DIST_DIR, 'SHA256SUMS.txt'), 'utf8').trim() : ''}
\`\`\`
`;

    // Publish to main repository
    await publishToRepo('jarifansathaorko', 'Hexton-Social-Scheduler', tag, releaseName, releaseBody, assets, token);

    console.log(`\n🎉 Release ${tag} complete!`);
}

main().catch(err => {
    console.error(`\n[FATAL] Release failed: ${err.message}`);
    process.exit(1);
});
