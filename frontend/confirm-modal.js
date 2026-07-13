// A reusable accessible confirmation dialog, generalized from the
// duplicate-download modal in index.html / renderer.js. That modal's markup
// and JS are tightly wired to the skip/queue-all duplicate flow (its own
// element ids, a URL list, and "skip_duplicates"/"queue_all" decision
// strings), so rather than risk breaking it this builds a separate generic
// modal element per call and mirrors the same accessibility behavior:
// role="alertdialog", aria-labelledby/aria-describedby wired to the
// title/message, a Tab focus trap, Esc-to-cancel, and backdrop-click-to-cancel.
// Reuses the existing .modal-overlay / .modal / .modal__* styling so it
// matches the duplicate modal's glass panel + blur backdrop.

// Unique ids per invocation so aria-labelledby/aria-describedby never collide
// if two modals are ever momentarily in the DOM at once.
let idCounter = 0;

export function showConfirmModal({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "default",
} = {}) {
  return new Promise((resolve) => {
    const uid = ++idCounter;
    const titleId = `confirm-modal-title-${uid}`;
    const messageId = `confirm-modal-message-${uid}`;

    // Restored on close so keyboard focus returns to whatever triggered the
    // modal (e.g. the Transcribe button) instead of jumping to the top.
    const previouslyFocused = document.activeElement;

    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.dataset.tone = tone;

    const modal = document.createElement("div");
    modal.className = "modal panel confirm-modal";
    modal.setAttribute("role", "alertdialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", titleId);
    modal.setAttribute("aria-describedby", messageId);

    const titleEl = document.createElement("h3");
    titleEl.className = "queue-title";
    titleEl.id = titleId;
    titleEl.textContent = title || "";

    const messageEl = document.createElement("p");
    messageEl.className = "modal__message";
    messageEl.id = messageId;
    messageEl.textContent = message || "";

    const actions = document.createElement("div");
    actions.className = "modal__actions";

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn btn--ghost";
    cancelBtn.textContent = cancelLabel;

    const confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = "btn btn--primary";
    confirmBtn.textContent = confirmLabel;

    actions.append(cancelBtn, confirmBtn);
    modal.append(titleEl, messageEl, actions);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Focus starts on the confirm button (mirrors the duplicate modal, which
    // focuses its primary "Queue anyway" action).
    confirmBtn.focus();

    function close(result) {
      document.removeEventListener("keydown", onKeydown);
      overlay.remove();
      if (previouslyFocused && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
      resolve(result);
    }

    function onKeydown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        close(false);
        return;
      }
      // Trap Tab focus between the two buttons — the only focusable elements
      // in the modal — so it never leaks to the page underneath.
      if (event.key === "Tab") {
        if (event.shiftKey && document.activeElement === cancelBtn) {
          event.preventDefault();
          confirmBtn.focus();
        } else if (!event.shiftKey && document.activeElement === confirmBtn) {
          event.preventDefault();
          cancelBtn.focus();
        }
      }
    }

    cancelBtn.addEventListener("click", () => close(false));
    confirmBtn.addEventListener("click", () => close(true));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close(false);
    });
    document.addEventListener("keydown", onKeydown);
  });
}
