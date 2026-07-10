// Ported 1:1 from the desktop app's frontend/ripple.js — same click-feedback
// behavior, so buttons on the site and in the app feel identical.
const RIPPLE_TARGETS = "[data-ripple]";

function spawnRipple(btn: HTMLElement, clientX?: number, clientY?: number): void {
  const rect = btn.getBoundingClientRect();
  const size = Math.hypot(rect.width, rect.height) * 2;
  const originX = clientX ?? rect.left + rect.width / 2;
  const originY = clientY ?? rect.top + rect.height / 2;

  const span = document.createElement("span");
  span.className = "ripple";
  span.style.width = `${size}px`;
  span.style.height = `${size}px`;
  span.style.left = `${originX - rect.left - size / 2}px`;
  span.style.top = `${originY - rect.top - size / 2}px`;

  btn.appendChild(span);
  span.addEventListener("animationend", () => span.remove(), { once: true });
}

export function initRipple(): void {
  document.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    const target = event.target as HTMLElement | null;
    const btn = target?.closest<HTMLElement>(RIPPLE_TARGETS);
    if (!btn || (btn as HTMLButtonElement).disabled) return;
    spawnRipple(btn, event.clientX, event.clientY);
  });
}
