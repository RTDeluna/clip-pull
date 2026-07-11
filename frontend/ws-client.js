const BACKEND_PORT = window.api?.backendPort ?? 8934;
const WS_URL = `ws://127.0.0.1:${BACKEND_PORT}/ws`;

// onStatusChange (optional) is called with "connected" or "disconnected" as
// the connection opens/drops, so callers can show the user something during
// a long download instead of the Queue/History views silently freezing with
// no indication anything's wrong. The backend already sends a fresh "sync"
// event on every new connection (see main.py's /ws handler), so each
// reconnect attempt below already re-syncs state on its own -- no separate
// resync call is needed here.
export function connectQueueSocket(onEvent, onStatusChange) {
  const socket = new WebSocket(WS_URL);

  socket.addEventListener("open", () => {
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

  socket.addEventListener("close", () => {
    onStatusChange?.("disconnected");
    setTimeout(() => connectQueueSocket(onEvent, onStatusChange), 1000);
  });

  return socket;
}
