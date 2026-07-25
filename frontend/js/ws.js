const MAX_BACKOFF_MS = 10_000;

export class WSClient {
  constructor({ token, onMessage, onReconnect, onStatusChange }) {
    this.token = token;
    this.onMessage = onMessage;
    this.onReconnect = onReconnect;
    this.onStatusChange = onStatusChange;
    this.socket = null;
    this.attempt = 0;
    this.manuallyClosed = false;
    this._connect(false);
  }

  _connect(isReconnect) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws?token=${encodeURIComponent(this.token)}`;
    const socket = new WebSocket(url);
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.onStatusChange?.(true);
      // Spec: WS drop -> auto-reconnect -> full REST refetch, never stale
      // data. The server also sends a fresh snapshot on connect, but the
      // explicit refetch is the documented contract for reconnect catch-up.
      if (isReconnect) this.onReconnect?.();
    };

    socket.onmessage = (evt) => {
      try {
        this.onMessage(JSON.parse(evt.data));
      } catch (_) {
        /* ignore malformed frame */
      }
    };

    socket.onclose = () => {
      this.onStatusChange?.(false);
      if (this.manuallyClosed) return;
      this.attempt += 1;
      const delay = Math.min(1000 * 2 ** this.attempt, MAX_BACKOFF_MS);
      setTimeout(() => this._connect(true), delay);
    };

    socket.onerror = () => socket.close();
  }

  close() {
    this.manuallyClosed = true;
    this.socket?.close();
  }
}
