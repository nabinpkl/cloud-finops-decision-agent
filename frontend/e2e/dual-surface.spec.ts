import { expect, test, type APIRequestContext } from "@playwright/test";

// E2E for the dual-surface workspace: a manual deterministic dashboard plus a
// separate, on-demand agent panel. FE-only cases run anywhere; @backend cases
// self-skip when /compare is unreachable (no populated store / backend down).
// The full "a real agent turn never changes the dashboard rows" assertion needs
// a live model; the runnable decoupling smoke below proves the weaker, always-
// checkable half (opening the panel does not mutate the table).

async function backendReachable(request: APIRequestContext): Promise<boolean> {
  try {
    const res = await request.post("http://localhost:3000/compare", {
      data: { vcpu: 4, ram_gb: 8, region: "eu-central", family: "general-purpose" },
      timeout: 5000,
    });
    return res.ok();
  } catch {
    return false;
  }
}

async function runManualCompare(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(page.locator("table.aui-compare-table")).toBeVisible({
    timeout: 15_000,
  });
}

test.describe("workspace shell (FE only)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("renders the navbar, page header, and empty dashboard", async ({
    page,
  }) => {
    await expect(page.getByRole("button", { name: /ask ai/i })).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /compare cloud instances/i }),
    ).toBeVisible();
    await expect(
      page.getByText(/pick a spec and press compare/i),
    ).toBeVisible();
  });

  test("Ask AI toggles the agent panel open and closed", async ({ page }) => {
    const askAi = page.getByRole("button", { name: /ask ai/i });
    await expect(askAi).toHaveAttribute("aria-pressed", "false");

    await askAi.click();
    await expect(askAi).toHaveAttribute("aria-pressed", "true");
    await expect(
      page.getByRole("heading", { name: "Pricing assistant" }).or(
        page.getByText("Pricing assistant"),
      ),
    ).toBeVisible();

    // close via the panel ✕
    await page.getByRole("button", { name: /close panel/i }).click();
    await expect(askAi).toHaveAttribute("aria-pressed", "false");
  });

  test("context strip shows the grounding hint before any comparison", async ({
    page,
  }) => {
    await page.getByRole("button", { name: /ask ai/i }).click();
    await expect(
      page.getByText(/the assistant will ground answers in it/i),
    ).toBeVisible();
  });
});

test.describe("manual dashboard (@backend)", () => {
  test.beforeEach(async ({ page, request }) => {
    test.skip(
      !(await backendReachable(request)),
      "backend /compare not reachable (needs FastAPI + populated store)",
    );
    await page.goto("/");
  });

  test("Compare renders a ranked cited table", async ({ page }) => {
    await runManualCompare(page);
    await expect(page.getByText(/Cheapest 4 vCPU/)).toBeVisible();
    const rows = page.locator("table.aui-compare-table tbody tr");
    expect(await rows.count()).toBeGreaterThan(0);
    // committed spec mirrored in the page header + context strip
    await expect(
      page.getByText(/Looking at\s*4 vCPU · 8 GB · general-purpose/),
    ).toBeVisible();
  });

  test("a derived row expands its composite breakdown on click", async ({
    page,
  }) => {
    await runManualCompare(page);
    const expandable = page.locator("tr[aria-expanded]");
    test.skip(
      (await expandable.count()) === 0,
      "no synthesized/derived row in this result set",
    );
    const first = expandable.first();
    await expect(first).toHaveAttribute("aria-expanded", "false");
    await first.click();
    await expect(first).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText("↳", { exact: false }).first()).toBeVisible();
  });

  test("opening the panel does not change the dashboard rows (decoupling)", async ({
    page,
  }) => {
    await runManualCompare(page);
    const rows = page.locator("table.aui-compare-table tbody tr");
    const before = await rows.count();
    const heading = await page.getByText(/Cheapest 4 vCPU/).textContent();

    await page.getByRole("button", { name: /ask ai/i }).click();
    await expect(page.getByRole("button", { name: /ask ai/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    // the manual table is driven solely by /compare; the panel cannot mutate it
    expect(await rows.count()).toBe(before);
    expect(await page.getByText(/Cheapest 4 vCPU/).textContent()).toBe(heading);
  });
});
