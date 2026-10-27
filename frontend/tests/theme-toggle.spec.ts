import { expect, test } from "@playwright/test";

test("toggling light/dark mode changes the rendered theme tokens", async ({ page }) => {
  await page.goto("/");

  const readBgToken = () =>
    page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--color-bg").trim(),
    );

  const initialBg = await readBgToken();
  const initialHasDarkClass = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  );

  await page.getByTestId("theme-toggle").click();

  const toggledBg = await readBgToken();
  const toggledHasDarkClass = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  );

  // The `.dark` class toggled, and the actual resolved value of the
  // --color-bg custom property changed as a result -- not just a class
  // name, but the real token value the rest of the UI reads.
  expect(toggledHasDarkClass).not.toBe(initialHasDarkClass);
  expect(toggledBg).not.toBe(initialBg);

  // Toggling back restores the original token value.
  await page.getByTestId("theme-toggle").click();
  const restoredBg = await readBgToken();
  expect(restoredBg).toBe(initialBg);
});
