import { expect, test } from "@playwright/test";
import { fillAndSubmit, mockClassifySuccess } from "./fixtures";

test.use({ viewport: { width: 390, height: 844 } }); // iPhone 12-class mobile viewport

test("idle screen renders mobile-first on a narrow viewport", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByTestId("image-dropzone")).toBeVisible();
  await expect(page.getByTestId("description-input")).toBeVisible();
  const measureButton = page.getByTestId("measure-button");
  await expect(measureButton).toBeVisible();

  // The primary action spans the available width on mobile -- the
  // dominant, full-width control the brief calls for.
  const box = await measureButton.boundingBox();
  const viewportSize = page.viewportSize();
  expect(box).not.toBeNull();
  expect(viewportSize).not.toBeNull();
  if (box && viewportSize) {
    expect(box.width).toBeGreaterThan(viewportSize.width * 0.7);
  }
});

test("result screen renders mobile-first on a narrow viewport", async ({ page }) => {
  await mockClassifySuccess(page);

  await page.goto("/");
  await fillAndSubmit(page);

  await expect(page.getByTestId("result-screen")).toBeVisible();
  await expect(page.getByTestId("risk-class-readout")).toBeVisible();
  await expect(page.getByTestId("result-photo")).toBeVisible();
  await expect(page.getByTestId("new-measurement-button")).toBeVisible();

  // No horizontal overflow/scroll on a narrow viewport.
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
  expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);
});
