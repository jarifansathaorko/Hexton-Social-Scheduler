import os from "node:os";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration.
 *
 * The suite runs against the **real** application: `webServer` seeds a throwaway
 * database with `e2e/seed.py` and then starts `hexton serve`, which serves the built
 * frontend from the same origin as the API. There is no dev server, no mocked
 * transport and no stubbed state — a green run means the shipped bundle talked to the
 * shipped backend and the persisted rows agreed with the screen.
 *
 * Three projects run in order, and the order is load-bearing:
 *
 * - **setup** accepts the privacy policy through the UI, because every other assertion
 *   about reporting and mentions is meaningless while the gate is closed. Running it
 *   through the form also exercises the gate itself rather than bypassing it.
 * - **core** is the workflow coverage.
 * - **worker** starts and stops a *real* worker process, which moves the seeded jobs
 *   between states. It depends on `core` so that nothing runs afterwards and has to
 *   cope with a queue that has genuinely progressed.
 *
 * The environment below is set here rather than in the launcher so that one value is
 * shared by the fixture script, the API, the test process, and the worker the last
 * project spawns. A worker pointed at a different data directory would heartbeat
 * into a database nobody was reading, which is precisely the failure this suite is
 * meant to catch rather than reproduce.
 */

const PORT = process.env.HEXTON_E2E_PORT || "8797";
const BASE_URL = `http://127.0.0.1:${PORT}`;

// Outside the repository and inside the Windows profile: a fixture that lives in the
// repo is one `git clean` away from being reviewed, and a profile path in the export
// is exactly what the diagnostics redaction test exists to catch.
const DATA_DIR =
  process.env.HEXTON_E2E_DATA_DIR || path.join(os.tmpdir(), "opencode", "hexton-e2e");

process.env.HEXTON_E2E_PORT = PORT;
process.env.HEXTON_E2E_DATA_DIR = DATA_DIR;
process.env.HEXTON_DATA_DIR = DATA_DIR;
process.env.HEXTON_CONNECTOR_KIND ||= "fake";
process.env.HEXTON_CREDENTIAL_STORE_KIND ||= "plaintext";
process.env.HEXTON_ALLOW_PLAINTEXT_CREDENTIALS ||= "true";
process.env.HEXTON_ALLOW_PLAINTEXT ||= "1";
// Shortened so the worker-restart test observes a heartbeat window of ~10s instead
// of ~30s; both the API and the worker read the same value, so the judgement of
// "alive" stays coherent across the two processes.
process.env.HEXTON_HEARTBEAT_SECONDS ||= "5";
process.env.HEXTON_WORKER_POLL_SECONDS ||= "1";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  projects: [
    { name: "setup", testMatch: /setup\.spec\.ts/ },
    { name: "core", testMatch: /core\.spec\.ts/, dependencies: ["setup"] },
    { name: "worker", testMatch: /worker\.spec\.ts/, dependencies: ["core"] },
  ],
  webServer: {
    command: "node e2e/server.mjs",
    cwd: ".",
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
