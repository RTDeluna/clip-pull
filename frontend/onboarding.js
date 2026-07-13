// First-run onboarding overlay. A large centered card walking a new user
// through what CLIP.PULL does, the Chrome extension, and the optional (and
// separately-billed) AI features. Gated by a localStorage flag — pure
// client-side UI state with nothing to sync — matching the app's other
// localStorage-backed flags (clippull-theme, clippull-sidebar).
//
// Deliberately a single static multi-step card (one step visible at a time,
// with Back / Next / Skip / Get started controls) rather than an animated
// carousel — simpler to get right, and easier to keep accessible.

const STORAGE_KEY = "clippull-onboarded";

function hasOnboarded() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    // localStorage unavailable — treat as not-yet-onboarded (worst case the
    // tour shows again next launch, which is harmless).
    return false;
  }
}

function markOnboarded() {
  try {
    localStorage.setItem(STORAGE_KEY, "true");
  } catch {
    // localStorage unavailable — the flag just won't persist across launches.
  }
}

function clearOnboarded() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // localStorage unavailable — nothing to clear.
  }
}

const ICON_DOWNLOAD = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>`;
const ICON_PUZZLE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h3a1 1 0 0 0 1-1 2 2 0 1 1 4 0 1 1 0 0 0 1 1h3v3a1 1 0 0 0 1 1 2 2 0 1 1 0 4 1 1 0 0 0-1 1v3h-3a1 1 0 0 1-1-1 2 2 0 1 0-4 0 1 1 0 0 1-1 1H4v-3a1 1 0 0 1 1-1 2 2 0 1 0 0-4 1 1 0 0 1-1-1z"></path></svg>`;
const ICON_SPARKLES = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2c.3 3.6 1.1 6 2.5 7.5S18.4 11.7 22 12c-3.6.3-6 1.1-7.5 2.5S12.3 18.4 12 22c-.3-3.6-1.1-6-2.5-7.5S6.6 12.3 2 12c3.6-.3 6-1.1 7.5-2.5S11.7 6.6 12 2z"></path></svg>`;
const ICON_CHECK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;

const STEPS = [
  {
    icon: ICON_DOWNLOAD,
    title: "Welcome to CLIP.PULL",
    body:
      "Paste your video links, choose a folder, and download a whole batch at once. " +
      "No accounts to set up — start the batch and walk away while it runs.",
  },
  {
    icon: ICON_PUZZLE,
    title: "Grab videos in one click",
    body:
      "Our free Chrome extension spots the video on a course lesson page and sends it " +
      "straight to CLIP.PULL's Queue — no hunting for links or copy-pasting. You'll find " +
      "it behind the download button at the bottom of the sidebar.",
  },
  {
    icon: ICON_SPARKLES,
    title: "Optional AI extras",
    body:
      "Turn any download into a transcript, Lesson Notes, or a short summary. These use " +
      "your own API key from a provider like Gemini or Anthropic, and any usage is billed " +
      "to your account by that provider — never by CLIP.PULL. It's completely optional; " +
      "everything else works without it.",
  },
  {
    icon: ICON_CHECK,
    title: "You're all set",
    body:
      "That's the whole tour. Paste a few links, pick a folder, and pull your first clip. " +
      "You can reopen this tour anytime from Settings.",
  },
];

let overlay = null;
let previouslyFocused = null;
let currentStep = 0;

// Element refs, populated by buildOverlay.
let iconEl;
let titleEl;
let bodyEl;
let dotsEl;
let stepCountEl;
let backBtn;
let nextBtn;

function buildOverlay() {
  overlay = document.createElement("div");
  overlay.className = "onboarding-overlay";

  const card = document.createElement("div");
  card.className = "onboarding-card panel";
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-labelledby", "onboarding-title");

  const skipBtn = document.createElement("button");
  skipBtn.type = "button";
  skipBtn.className = "onboarding-skip";
  skipBtn.textContent = "Skip";
  skipBtn.setAttribute("aria-label", "Skip the welcome tour");
  skipBtn.addEventListener("click", finishOnboarding);

  const step = document.createElement("div");
  step.className = "onboarding-step";

  iconEl = document.createElement("div");
  iconEl.className = "onboarding-icon";

  titleEl = document.createElement("h2");
  titleEl.className = "onboarding-title";
  titleEl.id = "onboarding-title";

  bodyEl = document.createElement("p");
  bodyEl.className = "onboarding-body";

  step.append(iconEl, titleEl, bodyEl);

  const footer = document.createElement("div");
  footer.className = "onboarding-footer";

  const progress = document.createElement("div");
  progress.className = "onboarding-progress";

  dotsEl = document.createElement("div");
  dotsEl.className = "onboarding-dots";
  dotsEl.setAttribute("aria-hidden", "true");
  STEPS.forEach(() => {
    const dot = document.createElement("span");
    dot.className = "onboarding-dot";
    dotsEl.appendChild(dot);
  });

  stepCountEl = document.createElement("span");
  stepCountEl.className = "onboarding-step-count";

  progress.append(dotsEl, stepCountEl);

  const nav = document.createElement("div");
  nav.className = "onboarding-nav";

  backBtn = document.createElement("button");
  backBtn.type = "button";
  backBtn.className = "btn btn--ghost onboarding-back";
  backBtn.textContent = "Back";
  backBtn.addEventListener("click", () => goToStep(currentStep - 1));

  nextBtn = document.createElement("button");
  nextBtn.type = "button";
  nextBtn.className = "btn btn--primary onboarding-next";
  nextBtn.addEventListener("click", () => {
    if (currentStep >= STEPS.length - 1) {
      finishOnboarding();
    } else {
      goToStep(currentStep + 1);
    }
  });

  nav.append(backBtn, nextBtn);
  footer.append(progress, nav);
  card.append(skipBtn, step, footer);
  overlay.appendChild(card);
  document.body.appendChild(overlay);
}

function renderStep() {
  const step = STEPS[currentStep];
  iconEl.innerHTML = step.icon;
  titleEl.textContent = step.title;
  bodyEl.textContent = step.body;

  dotsEl.querySelectorAll(".onboarding-dot").forEach((dot, i) => {
    dot.classList.toggle("onboarding-dot--active", i === currentStep);
  });
  stepCountEl.textContent = `Step ${currentStep + 1} of ${STEPS.length}`;

  const isLast = currentStep === STEPS.length - 1;
  // Back is meaningless on the first step — hide it so the nav stays clean.
  backBtn.hidden = currentStep === 0;
  nextBtn.textContent = isLast ? "Get started" : "Next";

  // Keep focus on the primary action after every navigation so keyboard flow
  // is predictable and focus never lands on a button that was just hidden.
  nextBtn.focus();
}

function goToStep(index) {
  currentStep = Math.max(0, Math.min(STEPS.length - 1, index));
  renderStep();
}

function getFocusable() {
  return Array.from(overlay.querySelectorAll("button")).filter(
    (btn) => !btn.hidden && !btn.disabled && btn.offsetParent !== null
  );
}

function onKeydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    finishOnboarding();
    return;
  }
  // Trap Tab focus within the card so it never reaches the page underneath.
  if (event.key === "Tab") {
    const focusables = getFocusable();
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
}

// Opens the tour on demand (also used by the "Show welcome tour" control in
// Settings). Does not itself gate on the flag — callers decide when to show.
export function showOnboarding() {
  if (overlay) return; // already open
  currentStep = 0;
  previouslyFocused = document.activeElement;
  buildOverlay();
  renderStep();
  document.addEventListener("keydown", onKeydown);
}

// Clears the onboarded flag and re-shows the tour — the Settings "Show
// welcome tour" button's action.
export function replayOnboarding() {
  clearOnboarded();
  showOnboarding();
}

function closeOnboarding() {
  if (!overlay) return;
  document.removeEventListener("keydown", onKeydown);
  overlay.remove();
  overlay = null;
  if (previouslyFocused && document.contains(previouslyFocused)) {
    previouslyFocused.focus();
  }
}

// Both "Skip" and "Get started" (and Esc) land here: record that the tour has
// been seen, then close.
function finishOnboarding() {
  markOnboarded();
  closeOnboarding();
}

// Self-init: show the tour once, on the very first launch.
if (!hasOnboarded()) {
  showOnboarding();
}
