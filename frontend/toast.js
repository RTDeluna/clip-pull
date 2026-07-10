const DURATIONS = { success: 4000, info: 4000, warning: 5000, error: 6000 };
const ICONS = { success: "✓", error: "✕", info: "ℹ", warning: "!" };

let container = null;

function ensureContainer() {
  if (container) return container;
  container = document.createElement("div");
  container.className = "toast-container";
  container.setAttribute("role", "status");
  container.setAttribute("aria-live", "polite");
  document.body.appendChild(container);
  return container;
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
  ensureContainer().appendChild(el);
  setTimeout(dismiss, DURATIONS[type] ?? DURATIONS.info);
  return el;
}
