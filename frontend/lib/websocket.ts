/**
 * Minimal reconnecting WebSocket client. Not tied to React — hooks/useTelemetryStream.ts
 * wires this into the zustand store. Reconnects with capped exponential backoff and
 * exposes a connection-status callback the store uses to reflect "connecting" / "live" /
 * "reconnecting" in the UI (see MissionHeader's status pill).
 */

export type ConnectionStatus = "connecting" | "open" | "reconnecting" | "closed";

interface ReconnectingSocketOptions<T> {
  url: string;
  onMessage: (data: T) => void;
  onStatusChange?: (status: ConnectionStatus) => void;
  maxBackoffMs?: number;
}

export class ReconnectingSocket<T = unknown> {
  private ws: WebSocket | null = null;
  private url: string;
  private onMessage: (data: T) => void;
  private onStatusChange?: (status: ConnectionStatus) => void;
  private maxBackoffMs: number;
  private backoffMs = 500;
  private closedByUser = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(opts: ReconnectingSocketOptions<T>) {
    this.url = opts.url;
    this.onMessage = opts.onMessage;
    this.onStatusChange = opts.onStatusChange;
    this.maxBackoffMs = opts.maxBackoffMs ?? 8000;
  }

  connect(): void {
    this.closedByUser = false;
    this.open();
  }

  private open(): void {
    this.onStatusChange?.("connecting");
    const ws = new WebSocket(this.url);
    this.ws = ws;

    ws.onopen = () => {
      this.backoffMs = 500;
      this.onStatusChange?.("open");
    };

    ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as T;
        this.onMessage(parsed);
      } catch {
        // ignore malformed frame
      }
    };

    ws.onclose = () => {
      if (this.closedByUser) {
        this.onStatusChange?.("closed");
        return;
      }
      this.onStatusChange?.("reconnecting");
      this.scheduleReconnect();
    };

    ws.onerror = () => {
      ws.close();
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.closedByUser) this.open();
    }, this.backoffMs);
    this.backoffMs = Math.min(this.backoffMs * 1.7, this.maxBackoffMs);
  }

  close(): void {
    this.closedByUser = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
  }
}
