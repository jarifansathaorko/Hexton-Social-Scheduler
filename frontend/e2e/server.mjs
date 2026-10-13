/**
 * Launches the real server for the Playwright suite.
 *
 * Playwright's `webServer` runs exactly one command, and this application needs two
 * with an environment shared between them: seed a throwaway database, then start the
 * API that serves the built frontend from the same origin. A shell one-liner would
 * have to repeat the environment, so it lives here once instead.
 *
 * `--no-start-worker` is deliberate. A running worker would claim the seeded jobs and
 * move them between states while a test is reading them, which turns every queue
 * assertion into a race. The worker is started and stopped explicitly by the
 * worker-restart test so that liveness is something the suite *observes*, not
 * something it assumes.
 */

import { spawn } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const backend = path.join(repo, "backend");

// Deliberately *outside* the repository and inside the Windows profile: a fixture that
// lives in the repo is one `git clean` away from being reviewed, and a profile path in
// the diagnostics export is exactly what the redaction test exists to catch.
const dataDir =
  process.env.HEXTON_E2E_DATA_DIR || path.join(os.tmpdir(), "opencode", "hexton-e2e");
const host = process.env.HEXTON_E2E_HOST || "127.0.0.1";
const port = process.env.HEXTON_E2E_PORT || "8797";

const venvPython =
  process.platform === "win32"
    ? path.join(backend, ".venv", "Scripts", "python.exe")
    : path.join(backend, ".venv", "bin", "python");
const python = process.env.HEXTON_PYTHON || venvPython;

/**
 * The same settings the fixture script sets for itself. The plaintext store matters
 * twice over: it keeps the fake tokens out of the machine's real Windows Credential
 * Manager, and it keeps the run self-contained in the throwaway directory.
 */
const env = {
  ...process.env,
  HEXTON_DATA_DIR: dataDir,
  HEXTON_HOST: host,
  HEXTON_PORT: port,
  HEXTON_CONNECTOR_KIND: "fake",
  HEXTON_TEST_MODE: "1",
  HEXTON_CREDENTIAL_STORE_KIND: "plaintext",
  HEXTON_ALLOW_PLAINTEXT_CREDENTIALS: "true",
  HEXTON_ALLOW_PLAINTEXT: "1",
};

function run(args, label) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, args, { cwd: backend, env, stdio: "inherit" });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${label} exited with code ${code}`));
    });
  });
}

await run([path.join(here, "seed.py"), dataDir], "seed");

const server = spawn(python, ["-m", "hexton", "serve", "--no-start-worker", "--test-mode"], {
  cwd: backend,
  env,
  stdio: "inherit",
});

// Playwright stops the webServer by signalling this process; the child must not be
// left behind holding the port for the next run.
const forward = (signal) => () => server.kill(signal);
process.on("SIGTERM", forward("SIGTERM"));
process.on("SIGINT", forward("SIGINT"));

server.on("error", (error) => {
  console.error(error);
  process.exit(1);
});
server.on("exit", (code) => process.exit(code ?? 0));
