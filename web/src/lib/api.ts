import type { CandlesResponse, OrderDefaults } from "@/lib/types";

export function getApiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  if (configured !== undefined && configured !== "") return configured.replace(/\/$/, "");
  if (typeof window !== "undefined") return "";
  return "http://127.0.0.1:8000";
}

export function getWsBase(): string {
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  if (configured !== undefined && configured !== "") return configured.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}`;
  }
  return "ws://127.0.0.1:8000";
}

export const API_BASE = getApiBase();
export const WS_BASE = getWsBase();

export const UNAUTHORIZED_EVENT = "terminal:unauthorized";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  const text = await res.text();
  let body: any = {};
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text };
    }
  }
  if (!res.ok) {
    if (res.status === 401 && path !== "/api/v1/auth/login" && typeof window !== "undefined") {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    const detail = body?.detail ?? body?.message;
    let message = typeof detail === "string" ? detail : `Request failed: ${res.status}`;
    const method = (init?.method || "GET").toUpperCase();
    if (path.startsWith("/api/orders/") && method !== "GET" && (res.status === 409 || res.status === 502)) {
      message += " Do not retry automatically. Reconcile positions and the order book before any manual retry; order submissions must reuse the original client_order_id and unchanged request.";
    }
    throw new ApiError(res.status, message);
  }
  return body as T;
}

export const api = {
  authStatus: () => req<{ authenticated: boolean; auth_configured: boolean }>("/api/v1/auth/status"),
  accessLogin: (password: string) => req<{ ok: true }>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  accessLogout: () => req<{ ok: boolean }>("/api/v1/auth/logout", { method: "POST", body: "{}" }),
  session: async () => {
    const res = await req<{
      ok: true;
      data: { mode: string; ucc: string; authenticated: boolean; two_fa_verified: boolean };
    }>("/api/v1/session");
    if (!res.ok || !res.data) throw new Error("Invalid session response");
    return res.data;
  },
  login: (payload: {
    mode: "paper" | "kotak";
    consumer_key?: string;
    mobile_number?: string;
    ucc?: string;
    totp?: string;
    mpin?: string;
  }) => req<{ ok: boolean; data: { mode: string; ucc: string; message: string } }>("/api/v1/login", { method: "POST", body: JSON.stringify(payload) }),
  instruments: (q = "", segment?: string) =>
    req<{ ok: boolean; data: any[] }>(`/api/v1/instruments?q=${encodeURIComponent(q)}${segment ? `&segment=${segment}` : ""}`),
  quotes: (ids: string[]) => req<{ ok: boolean; quotes: any[] }>("/api/v1/quotes", { method: "POST", body: JSON.stringify({ ids }) }),
  candles: (instrument: string, timeframe = "1m", indicators = "ema20,vwap") =>
    req<CandlesResponse>(
      `/api/v1/candles?instrument=${encodeURIComponent(instrument)}&timeframe=${timeframe}&indicators=${encodeURIComponent(indicators)}`
    ),
  optionChain: (underlying: string, expiry?: string) =>
    req<{ ok: boolean; data: any }>(`/api/v1/optchain-data/${underlying}${expiry ? `?expiry=${encodeURIComponent(expiry)}` : ""}`),
  quickOrder: (payload: {
    instrument_token: string;
    exchange_segment: string;
    trading_symbol: string;
    transaction_type: "B" | "S";
    lots?: number;
    product?: string;
    order_type?: string;
    price?: number;
    client_order_id?: string;
  }) => req<{ ok: boolean; data: any }>("/api/orders/v1/quick", { method: "POST", body: JSON.stringify(payload) }),
  placeOrder: (payload: { client_order_id?: string; [key: string]: unknown }) =>
    req<{ ok: boolean; data: any }>("/api/orders/v1/orders", { method: "POST", body: JSON.stringify(payload) }),
  cancelOrder: (orderId: string) => req<{ ok: boolean; data: any }>(`/api/orders/v1/orders/${orderId}`, { method: "DELETE" }),
  bulkCancel: () => req<{ ok: boolean; data: any[] }>("/api/orders/v1/orders/bulk-cancel", { method: "POST", body: "{}" }),
  orderbook: () => req<{ ok: boolean; data: any[] }>("/api/orders/v1/orderbook"),
  trades: () => req<{ ok: boolean; data: any[] }>("/api/orders/v1/trades"),
  positions: () => req<{ ok: boolean; total_pnl: number; positions: any[] }>("/api/orders/v1/positions"),
  exitPosition: (payload: {
    instrument_token?: string;
    trading_symbol: string;
    exchange_segment: string;
    product: OrderDefaults["default_product"];
    quantity: number;
    transaction_type: "B" | "S";
    client_order_id?: string;
  }) => req<{ ok: boolean; order_id: string }>("/api/orders/v1/positions/exit-order", { method: "POST", body: JSON.stringify(payload) }),
  hotExitAll: (clientOrderId: string) =>
    req<{ ok: boolean; order_ids: string[] }>("/api/orders/v1/exit-orders", {
      method: "POST",
      body: JSON.stringify({ client_order_id: clientOrderId }),
    }),
  holdings: () => req<{ ok: boolean; total_value: number; total_pnl: number; data: any[] }>("/api/orders/v1/holdings"),
  limits: () => req<{ ok: boolean; data: any }>("/api/v1/limits"),
  getOrderDefaults: () => req<{ ok: boolean; data: OrderDefaults }>("/api/orders/v1/order-defaults"),
  setOrderDefaults: (payload: OrderDefaults) => req<{ ok: boolean; data: OrderDefaults }>("/api/orders/v1/order-defaults", { method: "PUT", body: JSON.stringify(payload) }),
  getKillSwitch: () => req<{ ok: boolean; data: any }>("/api/orders/v1/kill-switch"),
  setKillSwitch: (payload: any) => req<{ ok: boolean; data: any }>("/api/orders/v1/kill-switch", { method: "POST", body: JSON.stringify(payload) }),
  getPnlProtect: () => req<{ ok: boolean; data: any }>("/api/orders/v1/pnl-protects"),
  setPnlProtect: (payload: any) => req<{ ok: boolean; data: any }>("/api/orders/v1/pnl-protects", { method: "PUT", body: JSON.stringify(payload) }),
  watchlists: () => req<{ ok: boolean; data: any[] }>("/api/v1/watchlists"),
  scanners: () => req<{ ok: boolean; data: any[] }>("/api/v1/scanners"),
  scannerResults: (id: string) => req<{ ok: boolean; instruments: any[] }>(`/api/v1/scanners/${id}/results`),
  alerts: () => req<{ ok: boolean; data: any[] }>("/api/alerts/v1/alerts"),
  createAlert: (payload: any) => req<{ ok: boolean; data: any }>("/api/alerts/v1/alerts", { method: "POST", body: JSON.stringify(payload) }),
  deleteAlert: (id: string) => req<{ ok: boolean }>(`/api/alerts/v1/alerts/${id}`, { method: "DELETE" }),
  egressIp: () => req<{ ok: boolean; ip: string; source?: string }>("/api/v1/egress-ip"),
};
