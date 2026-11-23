/** The tab's single WebSocket (FRONTEND_SPEC §10, API_AND_WS_SPEC §12): reconnect with backoff,
 * heartbeat, activity reporting, an outgoing queue for messages typed while offline, and request /
 * response matching by `req_id`. */

export type ServerEvent = { type: string; [key: string]: unknown };
type Listener = (event: ServerEvent) => void;

export interface RequestError {
  code: string;
  message: string;
}

const HEARTBEAT_MS = 25_000;
const ACTIVITY_THROTTLE_MS = 10_000;
const BACKOFF_MIN_MS = 500;
const BACKOFF_MAX_MS = 10_000;
const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart', 'focus'] as const;

export const CLOSE_UNAUTHENTICATED = 4001;
export const CLOSE_ORIGIN = 4003;

function tabId(): string {
  try {
    let id = sessionStorage.getItem('chat.tabId');
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem('chat.tabId', id);
    }
    return id;
  } catch {
    return crypto.randomUUID();
  }
}

export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** attempt);
  return Math.round(base * (0.8 + random() * 0.4)); // ±20 % jitter
}

interface PendingRequest {
  data: string;
  resolve: (event: ServerEvent) => void;
  reject: (error: RequestError) => void;
}

export class ChatSocket {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private requests = new Map<string, PendingRequest>();
  private attempt = 0;
  private everConnected = false;
  private stopped = true;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private lastActivitySent = 0;
  private reqSeq = 0;

  /** Called with `true` after a reconnect (not the first connect): time to catch up via REST. */
  onOpen: (reconnected: boolean) => void = () => {};
  /** The session is gone (close 4001): go to the sign-in page. */
  onUnauthenticated: () => void = () => {};

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    for (const name of ACTIVITY_EVENTS) window.addEventListener(name, this.onActivity, { passive: true });
    document.addEventListener('visibilitychange', this.onVisibility);
    this.open();
  }

  stop(): void {
    this.stopped = true;
    for (const name of ACTIVITY_EVENTS) window.removeEventListener(name, this.onActivity);
    document.removeEventListener('visibilitychange', this.onVisibility);
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.stopHeartbeat();
    this.ws?.close(1000);
    this.ws = null;
    this.failAll({ code: 'disconnected', message: 'Disconnected' });
    this.everConnected = false;
    this.attempt = 0;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Fire-and-forget; dropped while offline (typing, activity). */
  send(event: Record<string, unknown>): void {
    if (this.connected) this.ws?.send(JSON.stringify(event));
  }

  /** Resolves with the `ack`, rejects with the `error` for its req_id. Unanswered requests are (re)sent
   * on every (re)connect, so callers must make them idempotent (send_message: client_msg_id). */
  request(event: Record<string, unknown>): Promise<ServerEvent> {
    const reqId = `r-${++this.reqSeq}`;
    const data = JSON.stringify({ ...event, req_id: reqId });
    return new Promise((resolve, reject) => {
      this.requests.set(reqId, { data, resolve, reject });
      if (this.connected) this.ws?.send(data);
    });
  }

  private open(): void {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws?tab_id=${encodeURIComponent(tabId())}`);
    this.ws = ws;
    ws.onopen = () => {
      const reconnected = this.everConnected;
      this.everConnected = true;
      this.attempt = 0;
      this.startHeartbeat();
      for (const pending of this.requests.values()) ws.send(pending.data);
      this.onOpen(reconnected);
    };
    ws.onmessage = (e) => {
      let event: ServerEvent;
      try {
        event = JSON.parse(String(e.data)) as ServerEvent;
      } catch {
        return;
      }
      const reqId = typeof event.req_id === 'string' ? event.req_id : null;
      if (reqId && (event.type === 'ack' || event.type === 'error')) {
        const pending = this.requests.get(reqId);
        this.requests.delete(reqId);
        if (event.type === 'ack') pending?.resolve(event);
        else pending?.reject({ code: String(event.code), message: String(event.message) });
      }
      for (const listener of this.listeners) listener(event);
    };
    ws.onclose = (e) => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.stopHeartbeat();
      if (this.stopped) return;
      if (e.code === CLOSE_UNAUTHENTICATED) {
        this.stop();
        this.onUnauthenticated();
        return;
      }
      if (e.code === CLOSE_ORIGIN) {
        this.stop();
        return;
      }
      // Unanswered requests stay pending and are resent after the reconnect.
      this.reconnectTimer = setTimeout(() => this.open(), backoffDelay(this.attempt++));
    };
  }

  private failAll(error: RequestError): void {
    const pending = [...this.requests.values()];
    this.requests.clear();
    for (const p of pending) p.reject(error);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => this.send({ type: 'heartbeat' }), HEARTBEAT_MS);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  private onActivity = (): void => {
    const now = Date.now();
    if (now - this.lastActivitySent < ACTIVITY_THROTTLE_MS) return;
    this.lastActivitySent = now; // leading edge: the first action after idling goes out at once
    this.send({ type: 'activity' });
  };

  private onVisibility = (): void => {
    if (document.visibilityState === 'visible') this.onActivity();
  };
}

export const socket = new ChatSocket();
