import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

/**
 * Worker liveness, observed rather than assumed.
 *
 * The whole application is built on one claim: a separate, durable worker process is
 * what moves a schedule from queued to uploaded, and the browser never executes a
 * schedule. The only honest way to test that claim is to start a *real* worker,
 * watch the UI agree with its heartbeat, then kill it and watch the UI admit it.
 *
 * Liveness is read from the worker's own heartbeat row by the API — the frontend does
 * not infer it from how fresh the data looks — so this test is checking a durable
 * record, not a guess.
 *
 * This runs last and depends on `core`: starting a worker claims the seeded jobs and
 * moves them between states, which would quietly invalidate every queue assertion
 * that ran afterwards.
 */

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const backend = path.join(repo, "backend");

const venvPython =
  process.platform === "win32"
    ? path.join(backend, ".venv", "Scripts", "python.exe")
    : path.join(backend, ".venv", "bin", "python");
const python = process.env.HEXTON_PYTHON || venvPython;

test("the queue indicator follows the worker's own heartbeat", async ({ page }) => {
  test.setTimeout(180_000);

  await page.goto("/");
  await expect(page.getByText("Worker not running", { exact: true })).toBeVisible();

  // stdio is ignored on purpose: a pipe nobody drains fills up, and a worker blocked
  // on a full stdout buffer is a different bug than the one being tested here.
  const worker = spawn(python, ["-m", "hexton", "worker", "--test-mode"], {
    cwd: backend,
    env: process.env,
    stdio: "ignore",
  });
  const exited = new Promise<void>((resolve) => worker.once("exit", () => resolve()));

  try {
    await expect(page.getByText("Worker running", { exact: true })).toBeVisible({
      timeout: 60_000,
    });

    // The dashboard agrees, and quotes the heartbeat the worker wrote — not a value
    // the browser made up.
    await page.reload();
    await expect(page.getByText(/^Running — heartbeat from /)).toBeVisible();

    const status = await page.request.get("/api/worker/status");
    expect(status.ok()).toBe(true);
    expect(await status.json()).toMatchObject({ alive: true });
  } finally {
    worker.kill();
    await exited;
  }

  // The heartbeat goes stale rather than disappearing, so the UI must stop claiming a
  // worker is running once the tolerated window has passed.
  await expect(page.getByText("Worker not running", { exact: true })).toBeVisible({
    timeout: 60_000,
  });

  await page.goto("/");
  await expect(
    page.getByText("Not running — queued jobs are parked, not progressing"),
  ).toBeVisible();

  const status = await page.request.get("/api/worker/status");
  expect(await status.json()).toMatchObject({ alive: false });
});
