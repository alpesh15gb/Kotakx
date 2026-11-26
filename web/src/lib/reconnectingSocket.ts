export interface ReconnectingSocketOptions {
  onMessage: (text: string) => void;
  onOpen?: (socket: WebSocket) => void;
  onStateChange?: (connected: boolean) => void;
}

export interface ReconnectingSocket {
  updateSubscription: (payload: unknown) => void;
  close: () => void;
}

/** A browser WebSocket with bounded exponential backoff and durable subscriptions. */
export function createReconnectingSocket(
  url: string,
  { onMessage, onOpen, onStateChange }: ReconnectingSocketOptions,
): ReconnectingSocket {
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let subscription: string | null = null;
  let attempt = 0;
  let closed = false;

  const send = (text: string) => {
    if (socket?.readyState === WebSocket.OPEN) {
      try {
        socket.send(text);
      } catch {
        socket?.close();
      }
    }
  };

  const scheduleReconnect = (expected: WebSocket | null) => {
    if (closed || (expected !== null && socket !== expected) || reconnectTimer) return;
    socket = null;
    onStateChange?.(false);
    const base = Math.min(30_000, 1_000 * 2 ** attempt);
    attempt += 1;
    const delay = base + Math.floor(Math.random() * Math.min(1_000, base / 2));
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delay);
  };

  const connect = () => {
    if (closed || reconnectTimer) return;
    onStateChange?.(false);
    try {
      const ws = new WebSocket(url);
      socket = ws;
      ws.onopen = () => {
        if (closed || socket !== ws) return;
        attempt = 0;
        onStateChange?.(true);
        if (subscription) send(subscription);
        onOpen?.(ws);
      };
      ws.onmessage = (event) => {
        if (!closed && socket === ws && typeof event.data === "string") onMessage(event.data);
      };
      ws.onerror = () => ws.close();
      ws.onclose = () => scheduleReconnect(ws);
    } catch {
      scheduleReconnect(null);
    }
  };

  connect();

  return {
    updateSubscription: (payload) => {
      subscription = JSON.stringify(payload);
      send(subscription);
    },
    close: () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      const ws = socket;
      socket = null;
      ws?.close(1000, "terminal closed");
      onStateChange?.(false);
    },
  };
}
