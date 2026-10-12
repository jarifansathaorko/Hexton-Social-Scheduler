import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * Phase 4 workflow coverage.
 *
 * Every assertion here pairs something an operator can see with something a row on
 * disk says. Where the two could disagree — an upload that finished versus a video
 * YouTube actually published, a channel label versus a confirmed identity, a report
 * row that was attempted versus one that landed — the test pins the distinction
 * rather than the prettier number, because that distinction is the whole reason this
 * application is worth trusting.
 *
 * The suite runs in declaration order against one seeded database with no worker, so
 * a figure asserted early cannot drift underneath it later.
 */

const NAV_LABELS = [
  "Dashboard",
  "Schedule Calendar",
  "Upload Queue",
  "Media Library",
  "YouTube Channels",
  "Reports & Google Sheets",
  "Settings & Diagnostics",
];

/** The details panel: an `<aside>` with an accessible name, so it is a `complementary`. */
function details(page: Page) {
  return page.getByRole("complementary", { name: "Job details" });
}

/** The value cell of one labelled fact inside a `<dl>` whose rows are wrapped in a `<div>`. */
function fact(scope: Locator, label: string): Locator {
  return scope.locator("dl > div").filter({ hasText: label }).first().locator("dd");
}

/** Pick an option by its text rather than by index, which shifts when fixtures change. */
async function chooseOption(page: Page, select: string, text: string) {
  const value = await page
    .locator(`${select} option`, { hasText: text })
    .first()
    .getAttribute("value");
  expect(value, `no option matching ${text} in ${select}`).not.toBeNull();
  await page.locator(select).selectOption(value!);
}

// ---------------------------------------------------------------------------
// Information architecture
// ---------------------------------------------------------------------------

test.describe("information architecture", () => {
  test("the navigation is exactly the seven specified sections", async ({ page }) => {
    await page.goto("/");

    const nav = page.getByRole("navigation", { name: "Main" });
    const labels = (await nav.getByRole("link").allInnerTexts())
      .map((text) => text.trim())
      .filter(Boolean);
    expect(labels).toEqual(NAV_LABELS);

    // No tabs for networks this build does not drive — not even disabled ones, which
    // would advertise a capability that does not exist.
    await expect(
      page.getByRole("link", { name: /Instagram|TikTok|Facebook|Threads|Snapchat/ }),
    ).toHaveCount(0);

    // Create Content is a primary action, not an eighth place to live: it sits
    // outside the list entirely.
    await expect(nav.getByRole("link", { name: "Create Content" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Create Content" }).first()).toBeVisible();

    // Each destination resolves to a real page with its own heading — a nav item that
    // renders nothing is a promise the app does not keep.
    for (const label of NAV_LABELS) {
      await nav.getByRole("link", { name: label }).click();
      await expect(page.getByRole("heading", { level: 1, name: label })).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    }
  });
});

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

test.describe("dashboard", () => {
  test("every figure comes from a persisted record", async ({ page }) => {
    await page.goto("/");

    // The seed holds seven jobs: one draft, one ready, one scheduled with a video,
    // one published with evidence, one uncertain, one waiting on a retry, one
    // transferring.
    await expect(page.getByRole("group", { name: "Pending scheduled: 2" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Active uploads: 1" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Uploads completed: 2" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Publication verified: 1" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Failed jobs: 1" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Manual review: 1" })).toBeVisible();

    await expect(page.getByText("7 jobs on this PC")).toBeVisible();
    await expect(page.getByText("1 draft")).toBeVisible();
  });

  test("an upload that finished is not shown as a video that is published", async ({ page }) => {
    await page.goto("/");

    // Two transfers finished; only one of them is public. The two cards differ, and
    // the sentence beneath them says why.
    await expect(page.getByRole("group", { name: "Uploads completed: 2" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Publication verified: 1" })).toBeVisible();
    await expect(
      page.getByText(
        "A video is never counted as published on the strength of an upload response alone",
      ),
    ).toBeVisible();

    // The unverified transfer is listed with the *observed* remote state, not with a
    // tick that implies YouTube confirmed anything.
    const upcoming = page.getByRole("heading", { name: "Upcoming publication times" }).locator("..");
    await expect(upcoming.getByText("Bravo uploaded not verified")).toBeVisible();
    await expect(upcoming.getByText(/YouTube: Scheduled/)).toBeVisible();
  });

  test("worker and reporting health are stated from the backend's own records", async ({
    page,
  }) => {
    await page.goto("/");

    // The suite starts no worker on purpose, and the dashboard must say so rather
    // than leaving an operator to wonder why nothing is progressing.
    await expect(
      page.getByText("Not running — queued jobs are parked, not progressing"),
    ).toBeVisible();
    await expect(page.getByText("Worker running")).toHaveCount(0);

    const sheets = page.getByRole("heading", { name: "Google Sheets sync" }).locator("..");
    await expect(sheets.getByText("Last successful sync")).toBeVisible();
    await expect(fact(sheets, "Rows delivered")).toHaveText("1");
    await expect(fact(sheets, "Did not go through")).toHaveText("1");
    await expect(fact(sheets, "Unconfirmed outcome")).toHaveText("1");
  });
});

// ---------------------------------------------------------------------------
// Upload queue
// ---------------------------------------------------------------------------

test.describe("upload queue", () => {
  test("rows carry the identifiers needed to act without opening anything", async ({ page }) => {
    await page.goto("/queue");

    const retryRow = page.locator("li.card", { hasText: "Echo retry is scheduled" });
    await expect(retryRow).toBeVisible();
    await expect(retryRow).toContainText(/Schedule [A-Za-z0-9_-]+/);
    await expect(retryRow.getByText("Next retry")).toBeVisible();
    await expect(retryRow.getByText("quota_exhausted")).toBeVisible();
    // The sentence a reschedule that YouTube refused now lands in (F-05) travels the
    // same surface this row already renders, so it is pinned here.
    await expect(retryRow.getByText("Daily quota exhausted; next attempt scheduled.")).toBeVisible();
    await expect(retryRow.getByText(/attempt 2\/3|\(2\/3\)/)).toBeVisible();

    const finished = page.locator("li.card", { hasText: "Bravo uploaded not verified" });
    await expect(finished).toBeVisible();
    await expect(finished.getByRole("link", { name: "vid-bravo-sched" })).toHaveAttribute(
      "href",
      /youtube\.com\/watch\?v=vid-bravo-sched/,
    );
    await expect(finished).toContainText(/YouTube: Scheduled/);
  });

  test("an uncertain upload offers recovery, never a blind retry", async ({ page }) => {
    await page.goto("/queue");

    const uncertain = page.locator("li.card", { hasText: "Delta upload outcome uncertain" });
    await expect(uncertain).toBeVisible();
    await expect(uncertain).toContainText("Open YouTube Studio and look for a video with this title");
    await expect(uncertain).toContainText("This video is mine");
    await expect(uncertain).toContainText("None of these are mine");
    await expect(uncertain).toContainText("nothing restarts on its own");

    // The three-step recovery is the only path offered. A second `videos.insert`
    // after an ambiguous timeout is how a duplicate Short gets published.
    await expect(uncertain.getByRole("button", { name: /retry/i })).toHaveCount(0);

    await expect(page.getByRole("heading", { name: "Needs your decision" })).toBeVisible();
    await expect(page.getByText("Hexton will not retry them automatically")).toBeVisible();
  });

  test("the uncertain job's details offer every recovery the backend permits, and nothing else", async ({
    page,
  }) => {
    // F-04: "This video is mine" was gated on a video ID already being stored — absent
    // in exactly the ambiguous case that needs it — and rejecting cancelled the job
    // outright, so a Short hit by one timeout had no exit at all.
    await page.goto("/queue");
    await page
      .getByRole("button", { name: "Delta upload outcome uncertain" })
      .click();

    const panel = details(page);
    await expect(panel).toContainText("Delta upload outcome uncertain");

    // Both honest answers are offered: adopt something the ladder found, or say that
    // nothing was uploaded and let the job run again.
    await expect(
      panel.getByRole("button", { name: "None of these are mine — run it again" }),
    ).toBeVisible();
    await expect(
      panel.getByRole("button", { name: /None of these are mine — cancel/ }),
    ).toBeVisible();

    // The panel's own offer must never contradict the state machine: a blind retry of
    // an ambiguous upload is how a duplicate Short gets published.
    await expect(panel.getByRole("button", { name: "Retry" })).toHaveCount(0);
    await expect(panel).toContainText("Hexton will not retry it");

    // Re-queuing is a real state change the backend recorded, not a label the client
    // painted over: the recovery offer disappears with the uncertainty itself, because
    // there is no longer an outcome to reconcile.
    await panel.getByRole("button", { name: "None of these are mine — run it again" }).click();
    await expect(
      panel.getByRole("button", { name: "None of these are mine — run it again" }),
    ).toHaveCount(0);
    await expect(panel.getByText("The outcome of the last upload could not be proven")).toHaveCount(
      0,
    );
    await expect(panel).toContainText("Queued");
  });
});

// ---------------------------------------------------------------------------
// Schedule calendar
// ---------------------------------------------------------------------------

test.describe("schedule calendar", () => {
  test("selection lives in the URL and survives a reload", async ({ page }) => {
    await page.goto("/calendar");

    await page.getByRole("button", { name: "Open Charlie published and verified" }).click();
    await expect(page).toHaveURL(/\/calendar\?job=\d+/);

    const panel = details(page);
    await expect(panel.getByRole("heading", { name: "Charlie published and verified" })).toBeVisible();

    // Three different times, named distinctly, and never conflated.
    await expect(fact(panel, "Local upload completed")).not.toHaveText("Not uploaded");
    await expect(fact(panel, "Intended YouTube publication")).not.toHaveText("");
    await expect(fact(panel, "Verified public publication")).not.toHaveText("Not yet verified");
    await expect(fact(panel, "Schedule ID")).toHaveText(/.+/);

    // The report row was delivered, and the panel says which row it is talking about.
    await expect(fact(panel, "Google Sheets reporting")).toContainText("synced");

    // Published is terminal: the editor must not offer an Edit that would 409.
    await expect(panel.getByRole("link", { name: "Edit" })).toHaveCount(0);

    // A hard reload restores the same selection from the same URL.
    await page.reload();
    await expect(details(page)).toContainText("Charlie published and verified");
  });

  test("an eligible schedule can be edited, and the filter narrows honestly", async ({ page }) => {
    await page.goto("/calendar");

    await page.getByRole("button", { name: "Open Zeta draft waiting for a time" }).click();
    const panel = details(page);
    await expect(panel.getByRole("link", { name: "Edit" })).toBeVisible();
    await expect(fact(panel, "Verified public publication")).toHaveText("Not yet verified");
    await expect(fact(panel, "Local upload completed")).toHaveText("Not uploaded");

    await panel.getByRole("button", { name: "Close" }).click();
    await expect(details(page)).toHaveCount(0);
    await expect(page).not.toHaveURL(/job=/);

    // Search narrows the table, and the count says so.
    await page.locator("#job-search").fill("Charlie");
    const allContent = page.getByRole("heading", { name: "All content" }).locator("..");
    await expect(allContent.getByText(/1 item/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Open Bravo uploaded not verified" })).toHaveCount(0);

    // A channel the jobs were never addressed to matches nothing, and says so rather
    // than showing an unexplained empty table.
    await page.locator("#job-search").fill("");
    await page.locator("#job-channel").selectOption({ label: "Brand account channel" });
    await expect(page.getByText("Nothing matches this filter.")).toBeVisible();
    await expect(page.getByText("channel filter applied here, not by the server")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Create content
// ---------------------------------------------------------------------------

test.describe("create content", () => {
  test("a saved draft appears in the calendar and survives a browser refresh", async ({ page }) => {
    await page.goto("/new");
    await expect(page.getByRole("heading", { level: 1, name: "New content" })).toBeVisible();

    // Privacy defaults to Private on every new job — the brief's safety default.
    await expect(page.locator("#privacy")).toHaveValue("private");

    await page.locator("#title").fill("E2E draft created through the editor");
    await chooseOption(page, "#connection", "Hexton test channel");
    await chooseOption(page, "#channel", "Hexton Test Channel");

    // The unconfirmed brand-account identity is present in the select but *not*
    // selectable: choosing a label must not imply the connection can publish to it.
    // `<option>` and `<optgroup>` have no layout box and no text that Playwright's
    // text engine will match against, so the structure is read straight out of the
    // DOM rather than asserted through visibility.
    const options = await page.locator("#channel").evaluate((select) => {
      const element = select as HTMLSelectElement;
      return Array.from(element.options).map((option) => ({
        text: option.textContent?.trim() ?? "",
        disabled: option.disabled,
        group: option.parentElement?.getAttribute("label") ?? null,
      }));
    });

    const brand = options.find((option) => option.text.includes("Brand account channel"));
    expect(brand, "the unconfirmed identity must still be offered, not hidden").toBeDefined();
    expect(brand!.disabled, "an unconfirmed identity must not be selectable").toBe(true);
    expect(brand!.group).toMatch(/^Not confirmed/);
    expect(
      options.filter((option) => !option.disabled && option.text.includes("Brand account channel")),
    ).toHaveLength(0);
    expect(
      options.filter((option) => !option.disabled).map((option) => option.text),
    ).toEqual(["None selected", "Hexton Test Channel (@hextontest)"]);

    // From the library rather than a path typed by hand: the file has to exist and
    // parse before it can be scheduled.
    await page.getByRole("button", { name: /e2e-clip\.mp4/ }).click();

    const publishAt = new Date(Date.now() + 26 * 3600_000);
    const local = new Date(publishAt.getTime() - publishAt.getTimezoneOffset() * 60_000)
      .toISOString()
      .slice(0, 16);
    await page.locator("#publish-at").fill(local);

    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page).toHaveURL(/\/calendar\?job=\d+$/);

    const panel = details(page);
    await expect(panel.getByRole("heading", { name: "E2E draft created through the editor" })).toBeVisible();
    await expect(fact(panel, "Local upload completed")).toHaveText("Not uploaded");
    await expect(fact(panel, "Verified public publication")).toHaveText("Not yet verified");
    await expect(panel).toContainText("Hexton Test Channel");

    // Navigation is one reload away: the row must already be persisted, not held in
    // the browser's memory.
    await page.reload();
    await expect(details(page)).toContainText("E2E draft created through the editor");

    // …and it is findable in the list the operator actually browses.
    await page.goto("/calendar");
    await page.locator("#job-search").fill("E2E draft created through the editor");
    await expect(
      page.getByRole("button", { name: "Open E2E draft created through the editor" }),
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// YouTube channels
// ---------------------------------------------------------------------------

test.describe("youtube channels", () => {
  test("authorization state, identity confirmation and brand-account limits are stated", async ({
    page,
  }) => {
    await page.goto("/channels");

    const connection = page.getByRole("region", { name: "Connection Hexton test channel" });
    await expect(connection).toBeVisible();

    // The seeded credential expired three days ago, and the page says so plainly
    // rather than showing a green light.
    await expect(connection.getByText(/Credential expires .* — reauthorization required/)).toBeVisible();
    await expect(connection.getByRole("button", { name: "Reauthorize" })).toBeVisible();

    const confirmed = connection.locator("li", { hasText: "Hexton Test Channel" });
    await expect(confirmed.getByText(/Confirmed by you/)).toBeVisible();

    const brand = connection.locator("li", { hasText: "Brand account channel" });
    await expect(brand.getByText("Not confirmed — never used for publishing")).toBeVisible();
    await expect(brand.getByText(/brand-account publishing path as unverified \(DF-18\)/)).toBeVisible();

    // Disable and revoke are separate buttons, and disabling states what it did *not*
    // do rather than silently deleting the authorization.
    await expect(connection.getByRole("button", { name: "Disable connection" })).toBeVisible();
    await expect(connection.getByRole("button", { name: "Revoke authorization" })).toBeVisible();

    await connection.getByRole("button", { name: "Disable connection" }).click();
    await expect(connection.getByText("Status now: disabled")).toBeVisible();
    await expect(connection.getByText(/^Did: /)).toBeVisible();
    await expect(connection.getByText(/^Did not: /)).not.toHaveText(/Did not: nothing further/);
    await expect(connection.getByRole("button", { name: "Enable connection" })).toBeVisible();

    // Restored, so later tests read the same record they would have without this one.
    await connection.getByRole("button", { name: "Enable connection" }).click();
    await expect(connection.getByRole("button", { name: "Disable connection" })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Reports & Google Sheets
// ---------------------------------------------------------------------------

test.describe("reports and google sheets", () => {
  test("Sync Key and Schedule ID are visible for every row owed", async ({ page }) => {
    await page.goto("/reports");

    const outbox = page.getByRole("heading", { name: "Outbox" }).locator("..");
    await expect(outbox.getByRole("columnheader", { name: "Sync Key" })).toBeVisible();
    await expect(outbox.getByRole("columnheader", { name: "Schedule ID" })).toBeVisible();

    const rows = outbox.locator("tbody tr");
    await expect(rows).toHaveCount(4);

    // Every row carries a real Sync Key in the documented shape, and a Schedule ID
    // that matches the schedule it was built from.
    for (const row of await rows.all()) {
      await expect(row.locator("td").first()).toHaveText(/^SCH_\d{4}-\d{2}-\d{2}_.+_v1$/);
      await expect(row.locator("td").nth(1)).not.toHaveText("—");
    }

    // Retry exists only where the outcome is known to be wrong or is unconfirmed. A
    // delivered row and a waiting row have nothing to retry.
    await expect(outbox.getByRole("button", { name: "Retry this row" })).toHaveCount(2);

    // The destination is visible and the required columns are stated as unswitchable.
    await expect(page.getByText(/Always written, and not switchable off: .*Sync Key/)).toBeVisible();
  });

  test("a failed row can be retried, and the retry reaches the server", async ({ page }) => {
    await page.goto("/reports");
    const outbox = page.getByRole("heading", { name: "Outbox" }).locator("..");

    const [response] = await Promise.all([
      page.waitForResponse(
        (r) => /\/api\/reporting\/outbox\/\d+\/retry/.test(r.url()) && r.request().method() === "POST",
      ),
      outbox.getByRole("button", { name: "Retry this row" }).first().click(),
    ]);
    expect(response.ok()).toBe(true);

    // A report operation. Nothing it returns may carry a credential.
    expect(await response.text()).not.toMatch(/access-token|refresh-token/);
  });

  test("a preview states what the row would claim before anything is written", async ({ page }) => {
    await page.goto("/reports");

    const preview = page.getByRole("region", { name: "Per-upload report row" });
    await expect(preview.getByText("Pick a schedule to see the exact row")).toBeVisible();

    await chooseOption(page, "#preview-job", "Charlie published and verified");
    await expect(fact(preview, "Sync Key")).toHaveText(/^SCH_/);
    await expect(fact(preview, "Schedule ID")).toHaveText(/.+/);
    await expect(preview.getByText("Would claim Published")).toBeVisible();
    await expect(preview.getByText("Video ID present")).toBeVisible();
    await expect(preview.getByText("Verified publication time present")).toBeVisible();

    // A job with no publication time gets no invented report date: the endpoint
    // refuses, and the refusal is shown rather than swallowed.
    await chooseOption(page, "#preview-job", "Zeta draft waiting for a time");
    await expect(preview.getByRole("alert")).toContainText("no report date");
  });

  test("the daily completion report reads one report date at a time", async ({ page }) => {
    await page.goto("/reports");

    const daily = page.getByRole("region", { name: "Daily completion summary" });
    await expect(daily.getByRole("heading", { name: "Daily completion report" })).toBeVisible();
    await expect(daily.getByLabel("Report date")).toHaveValue(/\d{4}-\d{2}-\d{2}/);
    await expect(
      daily.getByRole("table").or(daily.getByText(/No summary has been produced for/)),
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Settings & diagnostics
// ---------------------------------------------------------------------------

test.describe("settings and diagnostics", () => {
  test("scheduling values save and survive a reload", async ({ page }) => {
    await page.goto("/settings");

    await page.locator("#missed_grace_minutes").fill("9");
    await page.getByRole("button", { name: "Save scheduling settings" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "worker reads these at startup" }),
    ).toBeVisible();

    await page.reload();
    await expect(page.locator("#missed_grace_minutes")).toHaveValue("9");

    // Put it back, so the rest of the suite reads the same record it did before.
    await page.locator("#missed_grace_minutes").fill("5");
    await page.getByRole("button", { name: "Save scheduling settings" }).click();
    await page.reload();
    await expect(page.locator("#missed_grace_minutes")).toHaveValue("5");
  });

  test("the settings page states which controls it deliberately does not offer", async ({
    page,
  }) => {
    await page.goto("/settings");

    const notConfigurable = page.getByRole("region", { name: "Not configurable" });
    await expect(notConfigurable.getByText("Default visibility")).toBeVisible();
    await expect(notConfigurable.getByText("Automatic thumbnails")).toBeVisible();
    await expect(notConfigurable.getByText("Drag-to-reschedule")).toBeVisible();

    // Nothing inert: the things this build does not implement are named, not offered
    // as controls that would do nothing when pressed.
    await expect(notConfigurable.getByRole("button")).toHaveCount(0);
    await expect(notConfigurable.getByRole("checkbox")).toHaveCount(0);
  });

  test("the diagnostics export carries no credential and no Windows account name", async ({
    page,
  }) => {
    await page.goto("/settings");

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Download diagnostics" }).click(),
    ]);
    const file = await download.path();
    expect(file).not.toBeNull();
    const raw = readFileSync(file!, "utf8");

    // The fake tokens exist on disk in the plaintext store. They must not leave.
    expect(raw).not.toContain("access-token");
    expect(raw).not.toContain("refresh-token");
    expect(raw).not.toMatch(
      /"(refresh_token|access_token|password|client_secret|cookie|csrf_token)"\s*:\s*"(?!\[redacted\])/,
    );

    // The data directory lives inside this Windows profile precisely so that the
    // account name has something to strip.
    const profile = process.env.USERPROFILE?.split(/[\\/]/).pop();
    if (profile) expect(raw).not.toContain(profile);

    const parsed = JSON.parse(raw) as { settings?: Record<string, unknown>; note?: string };
    expect(String(parsed.settings?.["data_dir"])).toMatch(/\\Users\\<redacted>/);
    // …while the store's *name* survives, because it is the field most useful in a
    // bug report and is explicitly allow-listed.
    expect(parsed.settings?.["credential_store_kind"]).toBe("plaintext");
    expect(parsed.settings?.["connector_kind"]).toBe("fake");
    expect(parsed.note).toContain("never sends them to the browser");
  });
});

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------

test.describe("theme", () => {
  test("the light theme is the default and the dark theme is remembered", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("html")).not.toHaveClass(/dark/);

    await page.getByRole("button", { name: "Dark theme" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(page.getByRole("button", { name: "Light theme" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    // Persisted, not per-render: the theme is a preference, not a page state.
    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);

    await page.getByRole("button", { name: "Light theme" }).click();
    await expect(page.locator("html")).not.toHaveClass(/dark/);
  });
});
