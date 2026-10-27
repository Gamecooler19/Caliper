import { expect, test } from "@playwright/test";
import { fillAndSubmit, mockClassifyError } from "./fixtures";

/**
 * One test per documented error-mapping case (per the Module 5 brief).
 * Each asserts the honest, specific message for that code, and that
 * the raw backend error code/body text never appears in the rendered
 * page.
 */

test("intake_parsing_failed: tells the user to add detail, offers to go back with input preserved", async ({
  page,
}) => {
  await mockClassifyError(page, 422, {
    error: "intake_parsing_failed",
    message: "Could not confidently resolve required field 'invasiveness_tier'.",
  });

  await page.goto("/");
  await fillAndSubmit(page, "A thing.");

  await expect(page.getByTestId("error-screen")).toBeVisible();
  await expect(page.getByTestId("error-message")).toContainText("description");
  await expect(page.getByTestId("error-primary-action")).toHaveText("Edit description");

  const bodyText = await page.textContent("body");
  expect(bodyText).not.toContain("intake_parsing_failed");
  expect(bodyText).not.toContain("invasiveness_tier");

  // Going back preserves the user's input.
  await page.getByTestId("error-primary-action").click();
  await expect(page.getByTestId("description-input")).toHaveValue("A thing.");
});

test("upstream_model_api_error: service temporarily unavailable, offers retry", async ({ page }) => {
  await mockClassifyError(page, 502, { error: "upstream_model_api_error", message: "Internal server error." });

  await page.goto("/");
  await fillAndSubmit(page);

  await expect(page.getByTestId("error-screen")).toBeVisible();
  await expect(page.getByTestId("error-message")).toContainText("temporarily unavailable");
  await expect(page.getByTestId("error-primary-action")).toHaveText("Retry");

  const bodyText = await page.textContent("body");
  expect(bodyText).not.toContain("upstream_model_api_error");
  expect(bodyText).not.toContain("Internal server error.");
});

test("upstream_model_api_timeout: service temporarily unavailable, offers retry", async ({ page }) => {
  await mockClassifyError(page, 504, { error: "upstream_model_api_timeout" });

  await page.goto("/");
  await fillAndSubmit(page);

  await expect(page.getByTestId("error-screen")).toBeVisible();
  await expect(page.getByTestId("error-message")).toContainText("temporarily unavailable");
  await expect(page.getByTestId("error-primary-action")).toHaveText("Retry");
});

test("rate_limit_exceeded: tells the user to wait and try again", async ({ page }) => {
  await mockClassifyError(page, 429, { error: "rate_limit_exceeded" });

  await page.goto("/");
  await fillAndSubmit(page);

  await expect(page.getByTestId("error-screen")).toBeVisible();
  await expect(page.getByTestId("error-message")).toContainText("wait");

  const bodyText = await page.textContent("body");
  expect(bodyText).not.toContain("rate_limit_exceeded");
});

test("stray unauthorized response: generic try-again-shortly message, nothing specific leaked", async ({
  page,
}) => {
  await mockClassifyError(page, 401, { error: "unauthorized" });

  await page.goto("/");
  await fillAndSubmit(page);

  await expect(page.getByTestId("error-screen")).toBeVisible();
  await expect(page.getByTestId("error-message")).toContainText("try again shortly");

  const bodyText = await page.textContent("body");
  expect(bodyText).not.toContain("unauthorized");
});

test("anything else (e.g. internal_error): generic try-again-shortly message", async ({ page }) => {
  await mockClassifyError(page, 500, { error: "internal_error" });

  await page.goto("/");
  await fillAndSubmit(page);

  await expect(page.getByTestId("error-screen")).toBeVisible();
  await expect(page.getByTestId("error-message")).toContainText("try again shortly");

  const bodyText = await page.textContent("body");
  expect(bodyText).not.toContain("internal_error");
});
