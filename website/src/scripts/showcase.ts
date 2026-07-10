const AUTOPLAY_MS = 5000;

export function initShowcase(): void {
  const root = document.getElementById("showcase");
  if (!root) return;

  const panels = Array.from(root.querySelectorAll<HTMLElement>(".showcase-panel"));
  const tabButtons = Array.from(root.querySelectorAll<HTMLButtonElement>(".showcase-tab"));
  const caption = document.getElementById("showcase-caption");
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let activeIndex = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let paused = false;

  function resetFill(button: HTMLButtonElement): void {
    const fill = button.querySelector<HTMLElement>(".showcase-tab__fill");
    if (!fill) return;
    fill.style.transition = "none";
    fill.style.width = "0%";
  }

  function runFill(button: HTMLButtonElement): void {
    const fill = button.querySelector<HTMLElement>(".showcase-tab__fill");
    if (!fill || prefersReducedMotion) return;
    fill.style.transition = "none";
    fill.style.width = "0%";
    // Force a reflow so the next width change actually transitions.
    void fill.offsetWidth;
    fill.style.transition = `width ${AUTOPLAY_MS}ms linear`;
    fill.style.width = "100%";
  }

  function activate(index: number): void {
    activeIndex = (index + tabButtons.length) % tabButtons.length;
    panels.forEach((panel, i) => panel.classList.toggle("is-active", i === activeIndex));
    tabButtons.forEach((btn, i) => {
      btn.classList.toggle("is-active", i === activeIndex);
      btn.setAttribute("aria-pressed", String(i === activeIndex));
      if (i === activeIndex) runFill(btn);
      else resetFill(btn);
    });
    if (caption) caption.textContent = tabButtons[activeIndex]?.dataset.caption ?? "";
    scheduleNext();
  }

  function scheduleNext(): void {
    clearTimeout(timer);
    if (paused || prefersReducedMotion) return;
    timer = setTimeout(() => activate(activeIndex + 1), AUTOPLAY_MS);
  }

  tabButtons.forEach((btn, i) => {
    btn.addEventListener("click", () => activate(i));
  });

  root.addEventListener("mouseenter", () => {
    paused = true;
    clearTimeout(timer);
  });
  root.addEventListener("mouseleave", () => {
    paused = false;
    scheduleNext();
  });

  activate(0);
}
