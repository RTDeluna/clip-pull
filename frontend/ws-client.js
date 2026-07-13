const BACKEND_PORT = window.api?.backendPort ?? 8934;
// The WebSocket constructor can't set custom headers, unlike fetch() (which
// gets the token injected transparently by main.js) -- so it rides as a
// query param instead. The backend validates it in the /ws handshake itself
// (see main.py's websocket_endpoint), closing the connection immediately if
// it's missing/wrong.
const WS_URL = `ws://127.0.0.1:${BACKEND_PORT}/ws?token=${encodeURIComponent(window.api?.apiToken ?? "")}`;

// Capped exponential backoff: start at 1s and double each failed attempt up to
// 10s, so a backend that's slow to come up (or briefly down) isn't hammered
// with a fixed 1/sec retry. A successful connection resets it back to 1s.
const RECONNECT_MIN_DELAY = 1000;
const RECONNECT_MAX_DELAY = 10000;

// onStatusChange (optional) is called with "connected" or "disconnected" as
// the connection opens/drops, so callers can show the user something during
// a long download instead of the Queue/History views silently freezing with
// no indication anything's wrong. The backend already sends a fresh "sync"
// event on every new connection (see main.py's /ws handler), so each
// reconnect attempt below already re-syncs state on its own -- no separate
// resync call is needed here.
export function connectQueueSocket(onEvent, onStatusChange, reconnectDelay = RECONNECT_MIN_DELAY) {
  const socket = new WebSocket(WS_URL);
  let delayForNextAttempt = reconnectDelay;

  socket.addEventListener("open", () => {
    // A healthy connection resets the backoff so a later drop retries quickly.
    delayForNextAttempt = RECONNECT_MIN_DELAY;
    onStatusChange?.("connected");
  });

  socket.addEventListener("message", (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (error) {
      console.error("Received a malformed WebSocket message:", event.data, error);
      return;
    }
    onEvent(data);
  });

  // The browser always fires "close" after "error" (standard WebSocket
  // semantics), and that close handler is what drives reconnection — so this
  // just surfaces the error in the console rather than duplicating retry logic.
  socket.addEventListener("error", (event) => {
    console.error("WebSocket error:", event);
  });

  socket.addEventListener("close", () => {
    onStatusChange?.("disconnected");
    const nextDelay = Math.min(delayForNextAttempt * 2, RECONNECT_MAX_DELAY);
    setTimeout(() => connectQueueSocket(onEvent, onStatusChange, nextDelay), delayForNextAttempt);
  });

  return socket;
}
