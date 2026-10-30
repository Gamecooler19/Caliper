import { type Page } from "@playwright/test";
import type { ClassifyResponse } from "../src/lib/types";

/**
 * A minimal valid PNG (1x1, transparent), used as the uploaded image
 * fixture across every test. Its actual pixel content is irrelevant --
 * every test mocks the backend response, including the
 * device_bounding_box's own image_width/image_height, so nothing in
 * the UI ever decodes this file's real dimensions.
 */
export const TINY_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";

export function tinyPngBuffer(): Buffer {
  return Buffer.from(TINY_PNG_BASE64, "base64");
}

export const SAMPLE_DISCLAIMER =
  "This is a pre-assessment estimate, not a regulatory determination. " +
  "Caliper does not replace the Drugs Controller General of India's " +
  "classification authority: under the Medical Devices Rules, 2017, device " +
  "classification is non-appealable once made. This output must not be " +
  "represented to any regulator, customer, or third party as an official " +
  "classification.";

export const SAMPLE_REPORT_PROSE =
  "This device is classified as Risk Class B under rule FS-B-INVASIVE-NONACTIVE, " +
  "licensed by the State authority under Form MD-3. Clinical investigation is not required.";

export function sampleClassifyResponse(
  overrides: Partial<ClassifyResponse> = {},
): ClassifyResponse {
  return {
    classification: {
      risk_class: "B",
      matched_rule_id: "FS-B-INVASIVE-NONACTIVE",
      applicable_licensing_authority: "State",
      applicable_form: "MD-3",
      clinical_investigation_required: false,
      clinical_investigation_basis: "No novel evidence basis triggered.",
      disclaimer: SAMPLE_DISCLAIMER,
    },
    consistency_flags: [],
    // Mirrors backend/app/report/generator.py: "{prose}\n\n{disclaimer}".
    report: `${SAMPLE_REPORT_PROSE}\n\n${SAMPLE_DISCLAIMER}`,
    disclaimer: SAMPLE_DISCLAIMER,
    device_bounding_box: {
      x1: 20,
      y1: 15,
      x2: 180,
      y2: 160,
      image_width: 200,
      image_height: 200,
    },
    ...overrides,
  };
}

/** Mocks POST /api/v1/classify with a 200 JSON success body. */
export async function mockClassifySuccess(
  page: Page,
  body: ClassifyResponse = sampleClassifyResponse(),
  options: { delayMs?: number } = {},
): Promise<void> {
  await page.route("**/api/v1/classify", async (route) => {
    if (options.delayMs) {
      await new Promise((resolve) => setTimeout(resolve, options.delayMs));
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

/** Mocks POST /api/v1/classify with an error status + body. */
export async function mockClassifyError(
  page: Page,
  status: number,
  body: Record<string, unknown>,
): Promise<void> {
  await page.route("**/api/v1/classify", async (route) => {
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  });
}

/** Fills the idle screen (image + description) and submits. */
export async function fillAndSubmit(page: Page, description = "A simple non-invasive bandage."): Promise<void> {
  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles({
    name: "device.png",
    mimeType: "image/png",
    buffer: tinyPngBuffer(),
  });
  await page.getByTestId("description-input").fill(description);
  await page.getByTestId("measure-button").click();
}
