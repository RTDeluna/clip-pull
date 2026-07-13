const DURATIONS = { success: 4000, info: 4000, warning: 5000, error: 6000 };
const ICONS = { success: "✓", error: "✕", info: "ℹ", warning: "!" };

// Two side-by-side live regions sharing one visual stack: errors go to an
// assertive region so screen readers interrupt and announce them right away,
// while success/info/warning stay polite so they don't talk over the user.
let stack = null;
const containers = {};

function ensureContainers() {
  if (stack) return containers;
  stack = document.createElement("div");
  stack.className = "toast-stack";

  const polite = document.createElement("div");
  polite.className = "toast-container";
  polite.setAttribute("role", "status");
  polite.setAttribute("aria-live", "polite");

  const assertive = document.createElement("div");
  assertive.className = "toast-container";
  assertive.setAttribute("role", "alert");
  assertive.setAttribute("aria-live", "assertive");

  stack.append(polite, assertive);
  document.body.appendChild(stack);
  containers.polite = polite;
  containers.assertive = assertive;
  return containers;
}

export function showToast(message, type = "info") {
  const el = document.createElement("div");
  el.className = `toast toast--${type}`;
  el.innerHTML = `
    <span class="toast__icon" aria-hidden="true">${ICONS[type] || ICONS.info}</span>
    <span class="toast__message"></span>
    <button class="toast__close" type="button" aria-label="Dismiss notification">×</button>
  `;
  el.querySelector(".toast__message").textContent = message;

  function dismiss() {
    if (el.dataset.dismissing) return;
    el.dataset.dismissing = "true";
    el.classList.add("toast--leaving");
    el.addEventListener("animationend", () => el.remove(), { once: true });
  }

  el.querySelector(".toast__close").addEventListener("click", dismiss);
  const { polite, assertive } = ensureContainers();
  (type === "error" ? assertive : polite).appendChild(el);
  setTimeout(dismiss, DURATIONS[type] ?? DURATIONS.info);
  return el;
}
