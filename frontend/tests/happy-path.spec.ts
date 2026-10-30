import { expect, test } from "@playwright/test";
import {
  SAMPLE_DISCLAIMER,
  SAMPLE_REPORT_PROSE,
  fillAndSubmit,
  mockClassifySuccess,
  sampleClassifyResponse,
} from "./fixtures";

test.describe("idle -> result happy path", () => {
  test("renders segmentation overlay and populated consistency-flags callout", async ({ page }) => {
    const response = sampleClassifyResponse({
      consistency_flags: [
        {
          field_name: "invasiveness_tier",
          stated_value: "non_invasive",
          vision_signal: true,
          message:
            "Vision analysis found visual evidence consistent with an implantable device, but the " +
            "stated profile's invasiveness_tier is 'non_invasive', not implantable. This discrepancy " +
            "needs human review before either input is relied on.",
        },
      ],
    });
    await mockClassifySuccess(page, response, { delayMs: 400 });

    await page.goto("/");

    // Idle state: Measure is disabled until both fields are filled.
    await expect(page.getByTestId("measure-button")).toBeDisabled();

    await fillAndSubmit(page);

    // Submitting state: one calm status line, no fabricated step list.
    await expect(page.getByTestId("submitting-screen")).toBeVisible();

    // Result state.
    await expect(page.getByTestId("result-screen")).toBeVisible();
    await expect(page.getByTestId("risk-class-readout")).toHaveText("B");
    await expect(page.getByTestId("licensing-authority")).toHaveText("State");
    await expect(page.getByTestId("applicable-form")).toHaveText("MD-3");

    // Segmentation overlay is present and proportionally positioned
    // (viewBox carries the backend's own reported source dimensions).
    const overlay = page.getByTestId("segmentation-overlay");
    await expect(overlay).toBeVisible();
    await expect(overlay).toHaveAttribute("viewBox", "0 0 200 200");

    // Consistency flags: non-empty array renders the amber callout, not
    // the success treatment.
    await expect(page.getByTestId("consistency-flags-callout")).toBeVisible();
    await expect(page.getByTestId("consistency-flag-item")).toHaveCount(1);
    await expect(page.getByTestId("no-discrepancy-confirmation")).toHaveCount(0);

    // Report + disclaimer in its own bordered block.
    await expect(page.getByTestId("report-card")).toBeVisible();
    await expect(page.getByTestId("report-disclaimer")).toContainText("pre-assessment estimate");

    // New Measurement returns to idle.
    await page.getByTestId("new-measurement-button").click();
    await expect(page.getByTestId("measure-button")).toBeVisible();
    await expect(page.getByTestId("measure-button")).toBeDisabled();
  });

  test("disclaimer block renders from the disclaimer field, not from the report text", async ({ page }) => {
    // The report here contains no disclaimer text and no "\n\n" separator,
    // so the only possible source for the block's text is the new field.
    const fieldDisclaimer = "FIELD-SOURCED disclaimer text, distinct from the report.";
    await mockClassifySuccess(
      page,
      sampleClassifyResponse({
        report: "Single-paragraph report with no disclaimer in it.",
        disclaimer: fieldDisclaimer,
      }),
    );

    await page.goto("/");
    await fillAndSubmit(page);

    await expect(page.getByTestId("result-screen")).toBeVisible();
    await expect(page.getByTestId("report-disclaimer")).toHaveText(fieldDisclaimer);
    await expect(page.getByTestId("report-card")).toContainText(
      "Single-paragraph report with no disclaimer in it.",
    );
  });

  test("full report with appended disclaimer shows the disclaimer once, in its own block", async ({
    page,
  }) => {
    await mockClassifySuccess(page, sampleClassifyResponse());

    await page.goto("/");
    await fillAndSubmit(page);

    await expect(page.getByTestId("report-disclaimer")).toHaveText(SAMPLE_DISCLAIMER);
    await expect(page.getByTestId("report-card")).toContainText(SAMPLE_REPORT_PROSE);
    const cardText = (await page.getByTestId("report-card").innerText()).replace(/\s+/g, " ");
    expect(cardText.split("pre-assessment estimate").length - 1).toBe(1);
  });

  test("when the box fetch failed (null box), the full classification still renders with no overlay and no error", async ({
    page,
  }) => {
    // Backend resolves a failed display-only box lookup to
    // device_bounding_box: null on an otherwise normal 200 response.
    await mockClassifySuccess(page, sampleClassifyResponse({ device_bounding_box: null }));

    await page.goto("/");
    await fillAndSubmit(page);

    await expect(page.getByTestId("result-screen")).toBeVisible();
    await expect(page.getByTestId("risk-class-readout")).toHaveText("B");
    await expect(page.getByTestId("licensing-authority")).toHaveText("State");
    await expect(page.getByTestId("applicable-form")).toHaveText("MD-3");
    await expect(page.getByTestId("clinical-investigation-flag")).toBeVisible();
    await expect(page.getByTestId("no-discrepancy-confirmation")).toBeVisible();
    await expect(page.getByTestId("report-card")).toContainText(SAMPLE_REPORT_PROSE);
    await expect(page.getByTestId("report-disclaimer")).toHaveText(SAMPLE_DISCLAIMER);
    await expect(page.getByTestId("new-measurement-button")).toBeVisible();

    // No overlay, and no error state anywhere.
    await expect(page.getByTestId("segmentation-overlay")).toHaveCount(0);
    await expect(page.getByTestId("error-screen")).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("empty consistency_flags renders the success treatment, not the callout", async ({ page }) => {
    await mockClassifySuccess(page, sampleClassifyResponse({ consistency_flags: [] }));

    await page.goto("/");
    await fillAndSubmit(page);

    await expect(page.getByTestId("result-screen")).toBeVisible();
    await expect(page.getByTestId("no-discrepancy-confirmation")).toBeVisible();
    await expect(page.getByTestId("consistency-flags-callout")).toHaveCount(0);
  });
});
