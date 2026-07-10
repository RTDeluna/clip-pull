const AUTOPLAY_MS = 6000;

// Astro's <Image> emits a srcset with several widths sized for the small
// card frame (~768px slot) — good enough for the deck, but softer than
// ideal blown up in a near-full-viewport lightbox. Picking the widest
// candidate directly gets the sharpest version actually available.
function largestSrcFromSrcset(img: HTMLImageElement): string {
  const srcset = img.srcset;
  if (!srcset) return img.currentSrc || img.src;
  const candidates = srcset
    .split(",")
    .map((entry) => {
      const [url, widthToken] = entry.trim().split(/\s+/);
      return { url, width: widthToken ? parseInt(widthToken, 10) : 0 };
    })
    .filter((candidate) => candidate.url);
  candidates.sort((a, b) => b.width - a.width);
  return candidates[0]?.url || img.currentSrc || img.src;
}

export function initShowcase(): void {
  const root = document.getElementById("showcase");
  if (!root) return;

  const cards = Array.from(root.querySelectorAll<HTMLButtonElement>("[data-showcase-card]"));
  const dots = Array.from(root.querySelectorAll<HTMLButtonElement>("[data-showcase-dot]"));
  const prevBtn = root.querySelector<HTMLButtonElement>("[data-showcase-prev]");
  const nextBtn = root.querySelector<HTMLButtonElement>("[data-showcase-next]");
  const caption = document.getElementById("showcase-caption");
  const lightbox = document.getElementById("showcase-lightbox");
  const lightboxImg = document.getElementById("showcase-lightbox-img") as HTMLImageElement | null;
  const lightboxClose = document.querySelector<HTMLButtonElement>("[data-showcase-lightbox-close]");
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const count = cards.length;
  if (count === 0) return;

  let activeIndex = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let paused = false;
  let lastFocusedBeforeLightbox: HTMLElement | null = null;

  function isLightboxOpen(): boolean {
    return !!lightbox && !lightbox.hidden;
  }

  function openLightbox(card: HTMLButtonElement): void {
    const img = card.querySelector("img");
    if (!lightbox || !lightboxImg || !img) return;
    lightboxImg.src = largestSrcFromSrcset(img);
    lightboxImg.alt = img.alt;
    lastFocusedBeforeLightbox = card;
    lightbox.hidden = false;
    document.body.style.overflow = "hidden";
    lightboxClose?.focus();
    paused = true;
    clearTimeout(timer);
  }

  function closeLightbox(): void {
    if (!lightbox || lightbox.hidden) return;
    lightbox.hidden = true;
    document.body.style.overflow = "";
    lastFocusedBeforeLightbox?.focus();
    paused = false;
    scheduleNext();
  }

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
    // The frontmost card is already fully visible — clicking it opens a
    // full-size preview instead of re-activating a no-op. Any other card
    // is a peeking background card, so clicking it just brings it forward.
    card.addEventListener("click", () => {
      if (i === activeIndex) {
        openLightbox(card);
      } else {
        activate(i);
      }
    });
  });
  dots.forEach((dot, i) => {
    dot.addEventListener("click", () => activate(i));
  });
  prevBtn?.addEventListener("click", () => activate(activeIndex - 1));
  nextBtn?.addEventListener("click", () => activate(activeIndex + 1));

  lightboxClose?.addEventListener("click", closeLightbox);
  lightbox?.addEventListener("click", (event) => {
    if (event.target === lightbox) closeLightbox();
  });

  root.addEventListener("keydown", (event) => {
    if (isLightboxOpen()) return;
    if (event.key === "ArrowLeft") activate(activeIndex - 1);
    if (event.key === "ArrowRight") activate(activeIndex + 1);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isLightboxOpen()) closeLightbox();
  });

  root.addEventListener("mouseenter", () => {
    paused = true;
    clearTimeout(timer);
  });
  root.addEventListener("mouseleave", () => {
    if (!isLightboxOpen()) {
      paused = false;
      scheduleNext();
    }
  });

  activate(0);
}
