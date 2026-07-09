const BACKEND_PORT = window.api?.backendPort ?? 8934;
const WS_URL = `ws://127.0.0.1:${BACKEND_PORT}/ws`;

export function connectQueueSocket(onEvent) {
  const socket = new WebSocket(WS_URL);

  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    onEvent(data);
  });

  socket.addEventListener("close", () => {
    setTimeout(() => connectQueueSocket(onEvent), 1000);
  });

  return socket;
}
