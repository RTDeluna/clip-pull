// Purely cosmetic -- no IPC, no backend calls. main.js closes this window
// itself once the main window is ready (or once startup gives up and shows
// its own error dialog), so this script only ever needs to update its own
// text over time, never report anything back.
const MESSAGE_SWAP_DELAY_MS = 7000;

setTimeout(() => {
  const message = document.getElementById("splash-message");
  if (message) {
    message.textContent =
      "First launch can take a little longer while your antivirus checks the app…";
  }
}, MESSAGE_SWAP_DELAY_MS);
