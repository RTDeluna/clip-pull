(function () {
  var STORAGE_KEY = "clippull-theme";
  var theme = "dark";
  try {
    theme =
      localStorage.getItem(STORAGE_KEY) ||
      (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  } catch (e) {
    // localStorage unavailable — fall back to dark.
  }
  document.documentElement.setAttribute("data-theme", theme);
})();
