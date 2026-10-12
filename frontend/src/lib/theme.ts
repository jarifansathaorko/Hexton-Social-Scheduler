/**
 * Theme selection.
 *
 * Light is the default because the approved desktop references are light first; dark
 * is the optional second theme. The choice lives in `localStorage` under one key and
 * is applied to `<html>` *before* React renders, so the first painted frame is already
 * the right one rather than a flash of the other theme.
 *
 * The theme is presentation only. Nothing about it reaches the backend, and no
 * preference is synced — this application keeps operator preferences on this PC.
 */

export type Theme = "light" | "dark";

const STORAGE_KEY = "hexton.theme";

export function storedTheme(): Theme {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Storage can be denied in a hardened browser profile; fall through to the default.
  }
  return "light";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Same as above: a preference we cannot persist is not an error worth surfacing.
  }
}

/** Called once from `main.tsx` before the first render. */
export function initTheme(): Theme {
  const theme = storedTheme();
  applyTheme(theme);
  return theme;
}
