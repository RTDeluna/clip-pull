const COLLAPSE_KEY = "clippull-sidebar";

const navButtons = document.querySelectorAll(".nav-btn");
const views = document.querySelectorAll(".view");
const navIndicator = document.querySelector(".nav-indicator");
const sidebar = document.querySelector(".sidebar");
const collapseBtn = document.getElementById("sidebar-collapse-btn");

function moveIndicatorTo(btn) {
  if (!navIndicator) return;
  navIndicator.style.transform = `translateY(${btn.offsetTop}px)`;
}

function showView(view) {
  view.hidden = false;
  view.classList.remove("view--enter");
  // Force a reflow so the animation restarts every time this view is shown.
  void view.offsetWidth;
  view.classList.add("view--enter");
}

navButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    navButtons.forEach((b) => b.classList.remove("active"));
    views.forEach((v) => (v.hidden = true));
    btn.classList.add("active");
    moveIndicatorTo(btn);
    showView(document.getElementById(btn.dataset.view));
  });
});

const initialActive = document.querySelector(".nav-btn.active");
if (initialActive) moveIndicatorTo(initialActive);
window.addEventListener("resize", () => {
  const active = document.querySelector(".nav-btn.active");
  if (active) moveIndicatorTo(active);
});

// Collapse / expand ---------------------------------------------------

function setCollapsed(collapsed) {
  if (!sidebar) return;
  sidebar.classList.toggle("sidebar--collapsed", collapsed);
  collapseBtn?.setAttribute("aria-pressed", String(collapsed));
  collapseBtn?.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
  collapseBtn?.setAttribute("title", collapsed ? "Expand sidebar" : "Collapse sidebar");
  try {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? "collapsed" : "expanded");
  } catch {
    // localStorage unavailable — collapse state just won't persist.
  }
}

let storedCollapsed = "expanded";
try {
  storedCollapsed = localStorage.getItem(COLLAPSE_KEY) || "expanded";
} catch {
  // localStorage unavailable — default to expanded.
}
setCollapsed(storedCollapsed === "collapsed");

collapseBtn?.addEventListener("click", () => {
  setCollapsed(!sidebar.classList.contains("sidebar--collapsed"));
});

// Extension download badge -----------------------------------------------
// A quiet dot on the sidebar's Extension tab so users always know a
// downloadable build exists, even while parked on Queue/History/Settings.

const extensionBadge = document.getElementById("nav-extension-badge");

async function refreshExtensionBadge() {
  if (!extensionBadge) return;
  try {
    const info = await window.api?.getExtensionPackageInfo?.();
    extensionBadge.hidden = !info?.filename;
  } catch {
    extensionBadge.hidden = true;
  }
}

refreshExtensionBadge();

// Extension download popup -------------------------------------------
// A flyout anchored to the sidebar's CTA button rather than a nav tab --
// it's excluded from the navButtons loop above simply by not being a
// .nav-btn, so tab-switching never touches it.

const extensionTrigger = document.getElementById("extension-popup-trigger");
const extensionPopup = document.getElementById("extension-popup");
const extensionPopupClose = extensionPopup?.querySelector(".extension-popup__close");

function onExtensionPopupOutsideClick(event) {
  if (extensionPopup.contains(event.target) || extensionTrigger.contains(event.target)) return;
  closeExtensionPopup();
}

function onExtensionPopupKeydown(event) {
  if (event.key === "Escape") closeExtensionPopup();
}

function openExtensionPopup() {
  if (!extensionTrigger || !extensionPopup) return;
  extensionPopup.hidden = false;
  extensionTrigger.setAttribute("aria-expanded", "true");
  // Lets extension-view.js re-check download status each time the popup
  // opens instead of only once at page load, in case the extension zip
  // gets built while the app is already running.
  document.dispatchEvent(new CustomEvent("clippull:extension-popup-opened"));
  (extensionPopupClose || extensionPopup).focus();
  document.addEventListener("mousedown", onExtensionPopupOutsideClick);
  document.addEventListener("keydown", onExtensionPopupKeydown);
}

function closeExtensionPopup() {
  if (!extensionPopup || extensionPopup.hidden) return;
  extensionPopup.hidden = true;
  extensionTrigger?.setAttribute("aria-expanded", "false");
  document.removeEventListener("mousedown", onExtensionPopupOutsideClick);
  document.removeEventListener("keydown", onExtensionPopupKeydown);
  extensionTrigger?.focus();
}

extensionTrigger?.addEventListener("click", () => {
  if (extensionPopup.hidden) openExtensionPopup();
  else closeExtensionPopup();
});
extensionPopupClose?.addEventListener("click", closeExtensionPopup);
