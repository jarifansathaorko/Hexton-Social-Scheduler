/**
 * Theme selection for HexScheduler.
 *
 * The visual north star is a dark, minimal black/charcoal foundation with
 * restrained violet/lavender glow. Light mode is available as a supported alternative.
 *
 * The preference lives in `localStorage` under `hexscheduler.theme` (with fallback to
 * `hexton.theme` for continuity) and is applied before React renders to prevent flash.
 */

export type Theme = "light" | "dark";

const PRIMARY_KEY = "hexscheduler.theme";
const LEGACY_KEY = "hexton.theme";

export function storedTheme(): Theme {
  try {
    const saved = window.localStorage.getItem(PRIMARY_KEY) || window.localStorage.getItem(LEGACY_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Storage can be denied in a hardened browser profile; fall through to the default.
  }
  return "light";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.classList.toggle("light", theme === "light");
  root.style.colorScheme = theme;
  try {
    window.localStorage.setItem(PRIMARY_KEY, theme);
    window.localStorage.setItem(LEGACY_KEY, theme);
  } catch {
    // Storage exceptions in restricted environments are ignored.
  }
}

/** Called once from `main.tsx` before the first render. */
export function initTheme(): Theme {
  const theme = storedTheme();
  applyTheme(theme);
  return theme;
}
