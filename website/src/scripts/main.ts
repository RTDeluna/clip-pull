import { initRipple } from "./ripple";
import { initReveal } from "./reveal";
import { initThemeToggle } from "./theme-toggle";
import { initOsDetect } from "./os-detect";

initRipple();
initReveal();
initThemeToggle();
initOsDetect();

// Mobile nav toggle
const navToggle = document.getElementById("nav-toggle");
const navSheet = document.getElementById("nav-sheet");
navToggle?.addEventListener("click", () => {
  const isOpen = navSheet?.classList.toggle("is-open");
  navToggle.setAttribute("aria-expanded", String(Boolean(isOpen)));
});
