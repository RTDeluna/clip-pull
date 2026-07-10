const STORAGE_KEY = "clippull-theme";
const root = document.documentElement;
const toggleBtn = document.getElementById("theme-toggle");

function setTheme(theme) {
  root.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // localStorage unavailable — theme just won't persist across launches.
  }
  if (toggleBtn) {
    toggleBtn.setAttribute("aria-pressed", String(theme === "light"));
  }
}

if (toggleBtn) {
  toggleBtn.setAttribute("aria-pressed", String(root.getAttribute("data-theme") === "light"));
  toggleBtn.addEventListener("click", () => {
    const next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
    setTheme(next);
  });
}
