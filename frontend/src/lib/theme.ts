export type Theme = "light" | "dark";

const STORAGE_KEY = "caliper-theme";

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Read the persisted theme choice, falling back to the OS preference. */
export function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return systemPrefersDark() ? "dark" : "light";
}

/**
 * Apply a theme by toggling the `.dark` class on <html> -- the single
 * source of truth tokens.css's selectors key off (see that file's
 * `.dark { ... }` block). Also persists the explicit choice so a
 * reload doesn't fall back to the OS preference once the user has
 * picked one.
 */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.classList.toggle("light", theme === "light");
  window.localStorage.setItem(STORAGE_KEY, theme);
}
