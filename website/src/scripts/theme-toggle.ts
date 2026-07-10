// Ported from the desktop app's frontend/theme.js — same localStorage key
// and same OS-preference fallback, so a visitor's choice feels consistent
// if they later use the app too.
const STORAGE_KEY = "clippull-theme";

export function initThemeToggle(): void {
  const root = document.documentElement;
  const toggleBtn = document.getElementById("theme-toggle");

  function setTheme(theme: "dark" | "light"): void {
    root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // localStorage unavailable — theme just won't persist across visits.
    }
    toggleBtn?.setAttribute("aria-pressed", String(theme === "light"));
  }

  if (toggleBtn) {
    toggleBtn.setAttribute(
      "aria-pressed",
      String(root.getAttribute("data-theme") === "light")
    );
    toggleBtn.addEventListener("click", () => {
      const next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      setTheme(next);
    });
  }
}
