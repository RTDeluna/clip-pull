const RIPPLE_TARGETS = ".btn, .nav-btn, .retry-btn, .theme-toggle, .sidebar__collapse-btn";

function spawnRipple(btn, clientX, clientY) {
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

document.addEventListener("pointerdown", (event) => {
  if (event.button !== undefined && event.button !== 0) return;
  const btn = event.target.closest(RIPPLE_TARGETS);
  if (!btn || btn.disabled) return;
  spawnRipple(btn, event.clientX, event.clientY);
});
