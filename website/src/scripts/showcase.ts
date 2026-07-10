const AUTOPLAY_MS = 6000;

export function initShowcase(): void {
  const root = document.getElementById("showcase");
  if (!root) return;

  const cards = Array.from(root.querySelectorAll<HTMLButtonElement>("[data-showcase-card]"));
  const dots = Array.from(root.querySelectorAll<HTMLButtonElement>("[data-showcase-dot]"));
  const prevBtn = root.querySelector<HTMLButtonElement>("[data-showcase-prev]");
  const nextBtn = root.querySelector<HTMLButtonElement>("[data-showcase-next]");
  const caption = document.getElementById("showcase-caption");
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const count = cards.length;
  if (count === 0) return;

  let activeIndex = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let paused = false;

  function activate(index: number): void {
    activeIndex = ((index % count) + count) % count;
    cards.forEach((card, i) => {
      // Circular distance ahead of the active card — 0 is frontmost, and
      // each step further back peeks out a little less from behind it.
      const offset = (i - activeIndex + count) % count;
      card.style.setProperty("--offset", String(offset));
      card.setAttribute("aria-current", String(i === activeIndex));
    });
    dots.forEach((dot, i) => {
      dot.setAttribute("aria-selected", String(i === activeIndex));
    });
    if (caption) caption.textContent = cards[activeIndex]?.dataset.caption ?? "";
    scheduleNext();
  }

  function scheduleNext(): void {
    clearTimeout(timer);
    if (paused || prefersReducedMotion) return;
    timer = setTimeout(() => activate(activeIndex + 1), AUTOPLAY_MS);
  }

  cards.forEach((card, i) => {
    card.addEventListener("click", () => activate(i));
  });
  dots.forEach((dot, i) => {
    dot.addEventListener("click", () => activate(i));
  });
  prevBtn?.addEventListener("click", () => activate(activeIndex - 1));
  nextBtn?.addEventListener("click", () => activate(activeIndex + 1));

  root.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") activate(activeIndex - 1);
    if (event.key === "ArrowRight") activate(activeIndex + 1);
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
