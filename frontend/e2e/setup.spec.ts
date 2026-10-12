import { expect, test } from "@playwright/test";

/**
 * The privacy-policy gate, exercised through the form rather than around it.
 *
 * Everything else in the suite that touches reporting or mentions assumes an accepted
 * policy, so this project runs first and depends on nothing. Accepting through the UI
 * (rather than writing the row directly) is the point: a gate that is bypassed by the
 * test is a gate whose *behaviour* is never verified.
 */
test.describe("privacy-policy gate", () => {
  test("the gate refuses before it is accepted and lifts after it", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("status")).toContainText("privacy policy has not been accepted");

    // Reporting refuses politely, and says where to go — it does not render an empty
    // table that would read as "nothing has gone wrong yet".
    await page.getByRole("link", { name: "Reports & Google Sheets" }).click();
    await expect(
      page.getByRole("region", { name: "Daily completion summary" }),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Daily completion summary" }).getByText("Accept the privacy policy in"),
    ).toBeVisible();

    // Accept, naming the operator who accepted — the record is who, not just that.
    await page.getByRole("link", { name: "Settings & Diagnostics" }).click();
    await page.locator("#policy-acceptor").fill("E2E Operator");
    await page.getByRole("button", { name: /Accept policy/ }).click();
    await expect(page.getByText("Accepted. API-connected features are available.")).toBeVisible();

    // A reload must not resurrect the banner: acceptance is persisted server-side.
    await page.reload();
    await expect(page.getByRole("status")).toHaveCount(0);

    await page.goto("/reports");
    await expect(page.getByText("Accept the privacy policy in")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Daily completion report" })).toBeVisible();
  });
});
