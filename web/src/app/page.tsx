"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Bell,
  Briefcase,
  CandlestickChart,
  Layers,
  List,
  Lock,
  Power,
  Radar,
  RefreshCw,
  ShieldAlert,
  Sliders,
  Wifi,
  WifiOff,
  Zap,
} from "lucide-react";
import { CandleChart } from "@/components/CandleChart";
import { LoginModal } from "@/components/LoginModal";
import { OptionChainPanel } from "@/components/OptionChainPanel";
import { api, ApiError, getWsBase, UNAUTHORIZED_EVENT } from "@/lib/api";
import { createReconnectingSocket } from "@/lib/reconnectingSocket";
import {
  AlertRecord,
  HoldingRecord,
  KillSwitchState,
  OptionChainResponse,
  OptionChainStrike,
  OrderDefaults,
  OrderRecord,
  PnlProtectConfig,
  PositionRecord,
  QuoteData,
} from "@/lib/types";

type WorkspaceTab = "scalper" | "options" | "trade" | "portfolio";
type PortfolioSubTab = "positions" | "orders" | "holdings" | "risk";
type RightDrawerTab = "watchlist" | "scanners" | "depth" | "alerts";
type OptionSelection = { token: string; symbol: string } | null;
type AccessState = "checking" | "required" | "ready" | "error";

function atmContracts(chain: OptionChainResponse): { primary: OptionSelection; secondary: OptionSelection } {
  const valid = chain.strikes.filter((strike) => strike.call_token && strike.call_symbol && strike.put_token && strike.put_symbol);
  const atm = valid.find((strike) => strike.is_atm)
    || valid.reduce<OptionChainStrike | undefined>((nearest, strike) => {
      if (!nearest) return strike;
      return Math.abs(strike.strike_price - chain.spot_price) < Math.abs(nearest.strike_price - chain.spot_price) ? strike : nearest;
    }, undefined);
  if (!atm) return { primary: null, secondary: null };
  return {
    primary: { token: atm.call_token!, symbol: atm.call_symbol! },
    secondary: { token: atm.put_token!, symbol: atm.put_symbol! },
  };
}

function reconcileContract(selection: OptionSelection, chain: OptionChainResponse, fallback: OptionSelection): OptionSelection {
  if (selection && chain.strikes.some((strike) =>
    (strike.call_token === selection.token && strike.call_symbol === selection.symbol)
    || (strike.put_token === selection.token && strike.put_symbol === selection.symbol),
  )) return selection;
  return fallback;
}

function clientOrderId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function positionExitKey(position: PositionRecord): string {
  const side = position.net_qty > 0 ? "S" : "B";
  return `exit:${position.instrument_token}:${position.exchange_segment}:${position.product}:${side}:${Math.abs(position.net_qty)}`;
}

function mergeChainTicks(chain: OptionChainResponse | null, quotes: Record<string, any>): OptionChainResponse | null {
  if (!chain) return null;
  return {
    ...chain,
    strikes: chain.strikes.map((strike) => {
      const call = strike.call_token ? quotes[strike.call_token] : undefined;
      const put = strike.put_token ? quotes[strike.put_token] : undefined;
      return {
        ...strike,
        call_ltp: call?.ltp ?? strike.call_ltp,
        call_change_pct: call?.change_pct ?? strike.call_change_pct,
        call_oi: call?.oi ?? strike.call_oi,
        call_volume: call?.volume ?? strike.call_volume,
        put_ltp: put?.ltp ?? strike.put_ltp,
        put_change_pct: put?.change_pct ?? strike.put_change_pct,
        put_oi: put?.oi ?? strike.put_oi,
        put_volume: put?.volume ?? strike.put_volume,
      };
    }),
  };
}

export default function TradingTerminalPage() {
  const [access, setAccess] = useState<AccessState>("checking");
  const [password, setPassword] = useState("");
  const [accessError, setAccessError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const accessGeneration = useRef(0);

  const checkAccess = useCallback(async () => {
    const generation = ++accessGeneration.current;
    setAccess("checking");
    setAccessError("");
    try {
      const status = await api.authStatus();
      if (generation !== accessGeneration.current) return;
      setPassword("");
      setAccess(status.authenticated || !status.auth_configured ? "ready" : "required");
    } catch (err) {
      if (generation !== accessGeneration.current) return;
      setAccess("error");
      setAccessError(err instanceof Error ? err.message : "Unable to check terminal access");
    }
  }, []);

  const requireAccess = useCallback(() => {
    accessGeneration.current += 1;
    setSubmitting(false);
    setPassword("");
    setAccessError("Your session expired. Enter the access password to continue.");
    setAccess("required");
  }, []);

  useEffect(() => {
    void checkAccess();
    window.addEventListener(UNAUTHORIZED_EVENT, requireAccess);
    return () => {
      accessGeneration.current += 1;
      window.removeEventListener(UNAUTHORIZED_EVENT, requireAccess);
    };
  }, [checkAccess, requireAccess]);

  const unlock = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submitting || !password) return;
    const generation = ++accessGeneration.current;
    setSubmitting(true);
    setAccessError("");
    try {
      await api.accessLogin(password);
      if (generation !== accessGeneration.current) return;
      setPassword("");
      setAccess("ready");
    } catch (err) {
      if (generation === accessGeneration.current) setAccessError(err instanceof Error ? err.message : "Access denied");
    } finally {
      if (generation === accessGeneration.current) setSubmitting(false);
    }
  };

  const lock = async () => {
    accessGeneration.current += 1;
    setPassword("");
    setAccess("required");
    setAccessError("");
    try {
      await api.accessLogout();
    } catch (err) {
      setAccessError(err instanceof Error ? err.message : "Logout failed; reload before leaving this device unattended.");
    }
  };

  if (access === "checking") {
    return (
      <div className="flex h-screen items-center justify-center bg-obsidian text-sm text-zinc-400">
        Checking terminal access...
      </div>
    );
  }

  if (access !== "ready") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-obsidian p-4 text-zinc-100">
        <form onSubmit={unlock} className="w-full max-w-sm space-y-4 rounded-xl border border-panelBorder bg-panel p-6 shadow-2xl">
          <div className="flex items-center gap-2 text-base font-bold text-white">
            <Lock className="h-5 w-5 text-accent" /> Terminal access
          </div>
          <p className="text-xs leading-relaxed text-zinc-400">
            Enter the server access password. Terminal data and trading connections stay disconnected until access is verified.
          </p>
          <input
            type="password"
            autoComplete="current-password"
            autoFocus
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Access password"
            className="w-full rounded-lg border border-panelBorder bg-obsidian px-3 py-2.5 text-sm text-white outline-none focus:border-accent"
          />
          {accessError && <div className="rounded bg-bear/20 px-3 py-2 text-xs text-red-200">{accessError}</div>}
          <button
            type="submit"
            disabled={submitting || !password}
            className="w-full rounded-lg bg-accent py-2.5 text-sm font-bold text-white hover:brightness-110 disabled:opacity-50"
          >
            {submitting ? "Unlocking..." : "Unlock terminal"}
          </button>
          {access === "error" && (
            <button type="button" onClick={() => void checkAccess()} className="flex w-full items-center justify-center gap-1.5 text-xs text-zinc-400 hover:text-white">
              <RefreshCw className="h-3.5 w-3.5" /> Retry access check
            </button>
          )}
        </form>
      </div>
    );
  }

  return <TradingTerminal onRequireAccess={() => void lock()} />;
}

function TradingTerminal({ onRequireAccess }: { onRequireAccess: () => void }) {
  const [workspace, setWorkspace] = useState<WorkspaceTab>("scalper");
  const [portfolioTab, setPortfolioTab] = useState<PortfolioSubTab>("positions");
  const [rightTab, setRightTab] = useState<RightDrawerTab>("watchlist");

  const [mode, setMode] = useState("paper");
  const [ucc, setUcc] = useState("DEMO01");
  const [loginOpen, setLoginOpen] = useState(false);

  const [quotesMap, setQuotesMap] = useState<Record<string, QuoteData>>({});
  const [scalperUnderlying, setScalperUnderlying] = useState("NIFTY");
  const [scalperLayout, setScalperLayout] = useState<"spot_opt" | "spot_ce_pe" | "ce_pe">("spot_opt");
  const [primaryOption, setPrimaryOption] = useState<OptionSelection>(null);
  const [secondaryOption, setSecondaryOption] = useState<OptionSelection>(null);
  const [stockSelection, setStockSelection] = useState({ token: "2885", symbol: "RELIANCE-EQ", segment: "nse_cm" });

  const [optionChain, setOptionChain] = useState<OptionChainResponse | null>(null);
  const [selectedExpiry, setSelectedExpiry] = useState<string | undefined>(undefined);

  const [positions, setPositions] = useState<PositionRecord[]>([]);
  const [totalPnl, setTotalPnl] = useState(0);
  const [orders, setOrders] = useState<OrderRecord[]>([]);
  const [holdings, setHoldings] = useState<HoldingRecord[]>([]);
  const [limits, setLimits] = useState<any>(null);

  const [defaults, setDefaults] = useState<OrderDefaults>({
    default_lots: 1,
    default_product: "MIS",
    default_order_type: "MKT",
    one_tap_enabled: false,
  });
  const [killSwitch, setKillSwitch] = useState<KillSwitchState>({ active: false });
  const [pnlProtect, setPnlProtect] = useState<PnlProtectConfig>({
    enabled: false,
    max_loss: -5000,
    max_profit: 10000,
    trailing_sl_enabled: false,
    trailing_sl_step: 1000,
    lock_profit_at: 2000,
    auto_activate_kill_switch: false,
    peak_pnl: 0,
    triggered: false,
    triggered_at: null,
  });

  const [watchlistQuotes, setWatchlistQuotes] = useState<QuoteData[]>([]);
  const [activeScanner, setActiveScanner] = useState("top_gainers");
  const [scannerRows, setScannerRows] = useState<any[]>([]);
  const [alerts, setAlerts] = useState<AlertRecord[]>([]);
  const [alertPrice, setAlertPrice] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [pendingOrderKeys, setPendingOrderKeys] = useState<Set<string>>(new Set());
  const [hotExitPending, setHotExitPending] = useState(false);
  const [socketState, setSocketState] = useState({ market: false, orders: false });

  const pendingOrderKeysRef = useRef<Set<string>>(new Set());
  const quickOrderIdsRef = useRef<Map<string, string>>(new Map());
  const positionExitIdsRef = useRef<Map<string, string>>(new Map());
  const hotExitPendingRef = useRef(false);
  const hotExitOrderIdRef = useRef<string | null>(null);
  const portfolioGeneration = useRef(0);
  const controlsGeneration = useRef(0);
  const scannerGeneration = useRef(0);
  const chainGeneration = useRef(0);
  const selectionRef = useRef<{ underlying: string; expiry?: string }>({ underlying: "NIFTY" });

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    setTimeout(() => setToast((cur) => (cur === msg ? null : cur)), 3500);
  }, []);

  const refreshPortfolio = useCallback(async () => {
    const generation = ++portfolioGeneration.current;
    try {
      const [posRes, ordRes, holdRes, limRes] = await Promise.all([
        api.positions(),
        api.orderbook(),
        api.holdings(),
        api.limits(),
      ]);
      if (generation !== portfolioGeneration.current) return;
      setPositions(posRes.positions || []);
      setTotalPnl(posRes.total_pnl || 0);
      setOrders(ordRes.data || []);
      setHoldings(holdRes.data || []);
      setLimits(limRes.data || null);
    } catch {
      // ignore transient errors while backend starts
    }
  }, []);

  const refreshControls = useCallback(async () => {
    const generation = ++controlsGeneration.current;
    try {
      const [session, d, k, p, w, a, sc] = await Promise.all([
        api.session(),
        api.getOrderDefaults(),
        api.getKillSwitch(),
        api.getPnlProtect(),
        api.watchlists(),
        api.alerts(),
        api.scannerResults(activeScanner),
      ]);
      if (generation !== controlsGeneration.current) return;
      setMode(session.mode);
      setUcc(session.ucc || "DEMO01");
      setDefaults(d.data);
      setKillSwitch(k.data);
      setPnlProtect(p.data);
      if (w.data?.[0]?.quotes) setWatchlistQuotes(w.data[0].quotes);
      setAlerts(a.data || []);
      setScannerRows(sc.instruments || []);
    } catch {
      // ignore
    }
  }, [activeScanner]);

  const loadOptionChain = useCallback(async (u: string, exp?: string) => {
    const generation = ++chainGeneration.current;
    try {
      const res = await api.optionChain(u, exp);
      const selection = selectionRef.current;
      const chain = res.data;
      if (generation !== chainGeneration.current || selection.underlying !== u || selection.expiry !== exp) return;
      if (chain.underlying?.toUpperCase() !== u.toUpperCase() || (exp && chain.expiry !== exp)) return;
      const atm = atmContracts(chain);
      setOptionChain(chain);
      setPrimaryOption((current) => reconcileContract(current, chain, atm.primary));
      setSecondaryOption((current) => reconcileContract(current, chain, atm.secondary));
    } catch {
      // Keep the last valid chain; selection is already cleared on underlying/expiry changes.
    }
  }, []);

  const changeUnderlying = useCallback((underlying: string) => {
    selectionRef.current = { underlying };
    chainGeneration.current += 1;
    setScalperUnderlying(underlying);
    setSelectedExpiry(undefined);
    setOptionChain(null);
    setPrimaryOption(null);
    setSecondaryOption(null);
  }, []);

  const changeExpiry = useCallback((expiry: string) => {
    selectionRef.current = { underlying: scalperUnderlying, expiry };
    chainGeneration.current += 1;
    setSelectedExpiry(expiry);
    setOptionChain(null);
    setPrimaryOption(null);
    setSecondaryOption(null);
  }, [scalperUnderlying]);

  useEffect(() => {
    refreshPortfolio();
    refreshControls();
    const id = setInterval(() => void refreshPortfolio(), 2500);
    return () => clearInterval(id);
  }, [refreshPortfolio, refreshControls]);

  useEffect(() => {
    void loadOptionChain(scalperUnderlying, selectedExpiry);
    const id = setInterval(() => void loadOptionChain(scalperUnderlying, selectedExpiry), 15_000);
    return () => clearInterval(id);
  }, [loadOptionChain, scalperUnderlying, selectedExpiry]);

  useEffect(() => {
    const generation = ++scannerGeneration.current;
    api.scannerResults(activeScanner)
      .then((res) => {
        if (generation === scannerGeneration.current) setScannerRows(res.instruments || []);
      })
      .catch(() => {});
  }, [activeScanner]);

  const spotToken = scalperUnderlying === "NIFTY" ? "NIFTY 50" : "NIFTY BANK";
  const marketTokens = useMemo(() => {
    const tokens = [
      "NIFTY 50",
      "NIFTY BANK",
      stockSelection.token,
      ...watchlistQuotes.map((quote) => quote.instrument_token),
      ...(optionChain?.strikes.flatMap((strike) => [strike.call_token, strike.put_token]) || []),
    ];
    return [...new Set(tokens.filter((token): token is string => Boolean(token)))];
  }, [stockSelection.token, watchlistQuotes, optionChain]);
  const marketTokensRef = useRef(marketTokens);
  marketTokensRef.current = marketTokens;
  const marketSocketRef = useRef<ReturnType<typeof createReconnectingSocket> | null>(null);
  const orderSocketRef = useRef<ReturnType<typeof createReconnectingSocket> | null>(null);

  // Both feeds reconnect with backoff; the latest market subscription is replayed after every reconnect.
  useEffect(() => {
    const wsBase = getWsBase();
    const market = createReconnectingSocket(`${wsBase}/ws/marketfeed`, {
      onStateChange: (connected) => setSocketState((current) => ({ ...current, market: connected })),
      onMessage: (text) => {
        try {
          const msg = JSON.parse(text);
          if (msg.type !== "snapshot" && msg.type !== "ticks") return;
          const list: QuoteData[] = Array.isArray(msg.data) ? msg.data : [];
          setQuotesMap((previous) => {
            const next = { ...previous };
            for (const quote of list) {
              if (!quote?.instrument_token) continue;
              const prior = next[quote.instrument_token];
              if (!prior || quote.updated_at >= prior.updated_at) next[quote.instrument_token] = quote;
            }
            return next;
          });
        } catch {
          // Ignore malformed feed frames without terminating the stream.
        }
      },
    });
    marketSocketRef.current = market;
    market.updateSubscription({ action: "replace", tokens: marketTokensRef.current });

    const orders = createReconnectingSocket(`${wsBase}/ws/orderfeed`, {
      onStateChange: (connected) => setSocketState((current) => ({ ...current, orders: connected })),
      onMessage: (text) => {
        try {
          const msg = JSON.parse(text);
          const data = msg.data || {};
          if (msg.type === "kill_switch") {
            setKillSwitch((current) => ({ ...current, ...data }));
            showToast(data.active ? "Kill Switch ACTIVATED — New entries blocked" : "Kill Switch deactivated");
            void refreshControls();
          } else if (msg.type === "pnl_protect_triggered" || msg.type === "risk_event") {
            showToast(`Risk event: ${data.reason || data.message || "Portfolio protection triggered"}`);
            void refreshControls();
          } else if (msg.type === "alert_triggered") {
            showToast(`Alert Triggered: ${data.trading_symbol} @ ₹${data.target_price}`);
            void refreshControls();
          } else if (msg.type === "order_update" || msg.type === "order_cancelled" || msg.type === "broker_order_update") {
            void refreshPortfolio();
          } else if (msg.type === "connected") {
            void refreshControls();
            void refreshPortfolio();
          }
        } catch {
          // Ignore malformed feed frames without terminating the stream.
        }
      },
    });
    orderSocketRef.current = orders;

    return () => {
      market.close();
      orders.close();
      if (marketSocketRef.current === market) marketSocketRef.current = null;
      if (orderSocketRef.current === orders) orderSocketRef.current = null;
    };
  }, [refreshPortfolio, refreshControls, showToast]);

  useEffect(() => {
    marketSocketRef.current?.updateSubscription({ action: "replace", tokens: marketTokens });
  }, [marketTokens]);

  const handleQuickTrade = async (side: "B" | "S", token: string, symbol: string, segment: string) => {
    if (!defaults.one_tap_enabled) {
      showToast("Order blocked: 1-Tap is OFF");
      return;
    }
    if (!token || !symbol) {
      showToast("Order blocked: no valid option-chain contract selected");
      return;
    }
    const key = `${side}:${token}`;
    if (pendingOrderKeysRef.current.has(key)) return;
    pendingOrderKeysRef.current.add(key);
    setPendingOrderKeys(new Set(pendingOrderKeysRef.current));
    const orderId = quickOrderIdsRef.current.get(key) ?? clientOrderId();
    quickOrderIdsRef.current.set(key, orderId);
    try {
      const res = await api.quickOrder({
        instrument_token: token,
        exchange_segment: segment,
        trading_symbol: symbol,
        transaction_type: side,
        client_order_id: orderId,
      });
      if (!res.ok || !res.data?.order_id) throw new Error("Order submission was not confirmed");
      const status = String(res.data.status || "unknown").toUpperCase();
      quickOrderIdsRef.current.delete(key);
      showToast(`${side === "B" ? "BUY" : "SELL"} ${symbol} (${res.data.quantity ?? 0} qty) -> ${status}`);
      void refreshPortfolio();
    } catch (err) {
      showToast(`Order not confirmed: ${err instanceof Error ? err.message : "Unknown error"}`);
      if (err instanceof ApiError && (err.status === 409 || err.status === 502)) void refreshPortfolio();
    } finally {
      pendingOrderKeysRef.current.delete(key);
      setPendingOrderKeys(new Set(pendingOrderKeysRef.current));
    }
  };

  const toggleOneTap = async () => {
    try {
      const next = { ...defaults, one_tap_enabled: !defaults.one_tap_enabled };
      const saved = await api.setOrderDefaults(next);
      setDefaults(saved.data);
      showToast(`1-Tap Execution ${saved.data.one_tap_enabled ? "ENABLED" : "DISABLED"}`);
    } catch (err) {
      showToast(`Could not update 1-Tap: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  };

  const toggleKillSwitch = async () => {
    try {
      const nextActive = !killSwitch.active;
      const saved = await api.setKillSwitch({ active: nextActive, reason: nextActive ? "User toggled in header" : "" });
      setKillSwitch(saved.data);
      showToast(nextActive ? "Kill Switch ACTIVATED — New entries blocked" : "Kill Switch deactivated");
    } catch (err) {
      showToast(`Could not update Kill Switch: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  };

  const handleExitPosition = async (position: PositionRecord) => {
    const key = positionExitKey(position);
    if (pendingOrderKeysRef.current.has(key)) return;
    pendingOrderKeysRef.current.add(key);
    setPendingOrderKeys(new Set(pendingOrderKeysRef.current));
    const orderId = positionExitIdsRef.current.get(key) ?? clientOrderId();
    positionExitIdsRef.current.set(key, orderId);
    try {
      const res = await api.exitPosition({
        instrument_token: position.instrument_token,
        trading_symbol: position.trading_symbol,
        exchange_segment: position.exchange_segment,
        product: position.product,
        quantity: Math.abs(position.net_qty),
        transaction_type: position.net_qty > 0 ? "S" : "B",
        client_order_id: orderId,
      });
      if (!res.ok || !res.order_id) throw new Error("Position exit was not confirmed");
      positionExitIdsRef.current.delete(key);
      showToast(`Exited ${position.trading_symbol}`);
      void refreshPortfolio();
    } catch (err) {
      showToast(`Exit not confirmed: ${err instanceof Error ? err.message : "Unknown error"}`);
      if (err instanceof ApiError && (err.status === 409 || err.status === 502)) void refreshPortfolio();
    } finally {
      pendingOrderKeysRef.current.delete(key);
      setPendingOrderKeys(new Set(pendingOrderKeysRef.current));
    }
  };

  const handleHotExitAll = async () => {
    if (hotExitPendingRef.current) return;
    hotExitPendingRef.current = true;
    setHotExitPending(true);
    const orderId = hotExitOrderIdRef.current ?? clientOrderId();
    hotExitOrderIdRef.current = orderId;
    try {
      const res = await api.hotExitAll(orderId);
      if (!res.ok || !Array.isArray(res.order_ids)) throw new Error("Hot Exit was not confirmed");
      hotExitOrderIdRef.current = null;
      showToast(`Hot Exit accepted (${res.order_ids.length} exit orders submitted)`);
      void refreshPortfolio();
    } catch (err) {
      showToast(`Hot Exit not confirmed: ${err instanceof Error ? err.message : "Unknown error"}`);
      if (err instanceof ApiError && (err.status === 409 || err.status === 502)) void refreshPortfolio();
    } finally {
      hotExitPendingRef.current = false;
      setHotExitPending(false);
    }
  };

  const niftyQ = quotesMap["NIFTY 50"];
  const bnfQ = quotesMap["NIFTY BANK"];
  const depthTargetQuote = (primaryOption && quotesMap[primaryOption.token]) || quotesMap[stockSelection.token] || niftyQ;
  const displayOptionChain = useMemo(() => mergeChainTicks(optionChain, quotesMap), [optionChain, quotesMap]);
  const primaryPending = Boolean(primaryOption && (pendingOrderKeys.has(`B:${primaryOption.token}`) || pendingOrderKeys.has(`S:${primaryOption.token}`)));
  const secondaryPending = Boolean(secondaryOption && (pendingOrderKeys.has(`B:${secondaryOption.token}`) || pendingOrderKeys.has(`S:${secondaryOption.token}`)));
  const stockPending = pendingOrderKeys.has(`B:${stockSelection.token}`) || pendingOrderKeys.has(`S:${stockSelection.token}`);

  const quickStrikes = useMemo(() => {
    if (!displayOptionChain?.strikes.length) return [];
    const markedAtm = displayOptionChain.strikes.findIndex((strike) => strike.is_atm);
    const atmIdx = markedAtm >= 0 ? markedAtm : displayOptionChain.strikes.reduce((nearest, strike, index) =>
      Math.abs(strike.strike_price - displayOptionChain.spot_price) < Math.abs(displayOptionChain.strikes[nearest].strike_price - displayOptionChain.spot_price) ? index : nearest, 0);
    const start = Math.max(0, atmIdx - 3);
    return displayOptionChain.strikes.slice(start, start + 7);
  }, [displayOptionChain]);

  return (
    <div className="flex flex-col h-screen bg-obsidian text-zinc-100 select-none">
      {/* Top Sahi-style Header */}
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-panelBorder bg-panel px-4 py-2">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-emerald-400 to-blue-500 font-black text-black text-sm">
              K
            </div>
            <span className="font-bold tracking-tight text-white text-sm">KotakNeo Pro</span>
            <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase text-zinc-300">{mode}</span>
          </div>

          <div className="hidden md:flex items-center gap-4 border-l border-panelBorder pl-4 text-xs">
            {niftyQ && (
              <button
                onClick={() => changeUnderlying("NIFTY")}
                className="flex items-center gap-1.5 hover:opacity-80"
              >
                <span className="font-semibold text-zinc-300">NIFTY50</span>
                <span className={`font-mono font-semibold ${niftyQ.change_pct >= 0 ? "text-bull" : "text-bear"}`}>
                  {niftyQ.ltp.toFixed(2)} ({niftyQ.change_pct >= 0 ? "+" : ""}
                  {niftyQ.change_pct.toFixed(2)}%)
                </span>
              </button>
            )}
            {bnfQ && (
              <button
                onClick={() => changeUnderlying("BANKNIFTY")}
                className="flex items-center gap-1.5 hover:opacity-80"
              >
                <span className="font-semibold text-zinc-300">BANKNIFTY</span>
                <span className={`font-mono font-semibold ${bnfQ.change_pct >= 0 ? "text-bull" : "text-bear"}`}>
                  {bnfQ.ltp.toFixed(2)} ({bnfQ.change_pct >= 0 ? "+" : ""}
                  {bnfQ.change_pct.toFixed(2)}%)
                </span>
              </button>
            )}
          </div>
        </div>

        {/* Center 1-Tap & Kill Switch */}
        <div className="flex items-center gap-2.5">
          <button
            onClick={toggleOneTap}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition ${
              defaults.one_tap_enabled
                ? "border-amber-400/50 bg-amber-400/15 text-amber-300"
                : "border-zinc-700 bg-obsidian text-zinc-400"
            }`}
          >
            <Zap className="h-3.5 w-3.5" />
            1-Tap: {defaults.one_tap_enabled ? `ON (${defaults.default_lots}L ${defaults.default_product})` : "OFF"}
          </button>

          <button
            onClick={toggleKillSwitch}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition ${
              killSwitch.active
                ? "border-bear bg-bear/20 text-red-300 animate-pulse"
                : "border-zinc-700 bg-obsidian text-zinc-400 hover:text-white"
            }`}
          >
            <Power className="h-3.5 w-3.5" />
            Kill Switch {killSwitch.active ? "ACTIVE" : "OFF"}
          </button>
          <span
            title={`Market feed: ${socketState.market ? "connected" : "reconnecting"}; order feed: ${socketState.orders ? "connected" : "reconnecting"}`}
            className={`flex items-center gap-1 text-[10px] font-semibold ${socketState.market && socketState.orders ? "text-bull" : "text-amber-300"}`}
          >
            {socketState.market && socketState.orders ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
            {socketState.market && socketState.orders ? "Feeds live" : "Reconnecting"}
          </span>
        </div>

        {/* Right Portfolio Quick Links + P&L */}
        <div className="flex items-center gap-4 text-xs">
          <button
            onClick={() => {
              setWorkspace("portfolio");
              setPortfolioTab("holdings");
            }}
            className="text-zinc-300 hover:text-white"
          >
            Holdings ({holdings.length})
          </button>
          <button
            onClick={() => {
              setWorkspace("portfolio");
              setPortfolioTab("orders");
            }}
            className="text-zinc-300 hover:text-white"
          >
            Orders ({orders.length})
          </button>
          <button
            onClick={() => {
              setWorkspace("portfolio");
              setPortfolioTab("positions");
            }}
            className="text-zinc-300 hover:text-white"
          >
            Positions ({positions.filter((p) => p.net_qty !== 0).length})
          </button>
          <div className="font-mono font-bold">
            P&amp;L:{" "}
            <span className={totalPnl >= 0 ? "text-bull" : "text-bear"}>
              {totalPnl >= 0 ? "+" : ""}₹{totalPnl.toFixed(2)}
            </span>
          </div>
          <button
            onClick={() => setLoginOpen(true)}
            className="rounded-md bg-zinc-800 px-2.5 py-1 font-semibold text-zinc-200 hover:bg-zinc-700"
          >
            {ucc}
          </button>
          <button onClick={onRequireAccess} title="Clear the access cookie and lock the terminal" className="rounded-md border border-panelBorder px-2.5 py-1 font-semibold text-zinc-300 hover:text-white">
            <Lock className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      {/* Main 3-Column Terminal Body */}
      <div className="flex flex-1 min-h-0">
        {/* Left Nav Rail (Sahi Layout) */}
        <aside className="flex w-16 flex-col items-center gap-2 border-r border-panelBorder bg-panel py-3">
          {[
            { id: "scalper", label: "Scalper", icon: Zap },
            { id: "options", label: "Options", icon: Layers },
            { id: "trade", label: "Stocks", icon: CandlestickChart },
            { id: "portfolio", label: "Portfolio", icon: Briefcase },
          ].map((item) => {
            const Icon = item.icon;
            const active = workspace === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setWorkspace(item.id as WorkspaceTab)}
                className={`flex w-14 flex-col items-center gap-1 rounded-lg py-2 text-[10px] font-medium transition ${
                  active ? "bg-accent/20 text-blue-400" : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
                }`}
              >
                <Icon className="h-4 w-4" />
                <span>{item.label}</span>
              </button>
            );
          })}

          <div className="mt-auto flex flex-col items-center gap-2">
            <button
              type="button"
              onClick={handleHotExitAll}
              disabled={hotExitPending}
              title={hotExitPending ? "Hot Exit is pending; do not submit again" : "Hot Exit All Open Positions & Cancel Pending Orders"}
              className="flex w-14 flex-col items-center gap-1 rounded-lg bg-bear/15 py-2 text-[10px] font-bold text-bear hover:bg-bear/25 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ShieldAlert className="h-4 w-4" />
              <span>{hotExitPending ? "Exiting..." : "Hot Exit"}</span>
            </button>
          </div>
        </aside>

        {/* Center Workspace */}
        <main className="flex-1 min-w-0 overflow-auto p-3 space-y-3">
          {workspace === "scalper" && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-panelBorder bg-panel px-3 py-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-zinc-400">Layout:</span>
                  {[
                    { id: "spot_opt", label: "Spot + Option" },
                    { id: "spot_ce_pe", label: "Spot + CE + PE" },
                    { id: "ce_pe", label: "Call + Put" },
                  ].map((l) => (
                    <button
                      key={l.id}
                      onClick={() => setScalperLayout(l.id as any)}
                      className={`rounded px-2 py-1 font-medium ${
                        scalperLayout === l.id ? "bg-accent text-white" : "bg-obsidian text-zinc-400 hover:text-white"
                      }`}
                    >
                      {l.label}
                    </button>
                  ))}
                </div>

                {/* Quick Strike Bar */}
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-zinc-400 mr-1">Quick Strikes ({scalperUnderlying}):</span>
                  {quickStrikes.map((s) => (
                    <div key={s.strike_price} className="inline-flex rounded border border-panelBorder bg-obsidian overflow-hidden">
                      <button
                        onClick={() =>
                          s.call_token &&
                          s.call_symbol &&
                          setPrimaryOption({ token: s.call_token, symbol: s.call_symbol })
                        }
                        className={`px-2 py-0.5 text-[11px] hover:bg-bull/20 ${
                          primaryOption?.token === s.call_token ? "bg-bull/25 text-bull font-bold" : "text-zinc-300"
                        }`}
                      >
                        {s.strike_price} CE
                      </button>
                      <button
                        onClick={() => {
                          if (s.put_token && s.put_symbol) {
                            if (scalperLayout === "spot_opt") {
                              setPrimaryOption({ token: s.put_token, symbol: s.put_symbol });
                            } else {
                              setSecondaryOption({ token: s.put_token, symbol: s.put_symbol });
                            }
                          }
                        }}
                        className={`border-l border-panelBorder px-2 py-0.5 text-[11px] hover:bg-bear/20 ${
                          primaryOption?.token === s.put_token || secondaryOption?.token === s.put_token
                            ? "bg-bear/25 text-bear font-bold"
                            : "text-zinc-300"
                        }`}
                      >
                        PE
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div
                className={`grid gap-3 ${
                  scalperLayout === "spot_ce_pe" ? "grid-cols-1 xl:grid-cols-3" : "grid-cols-1 lg:grid-cols-2"
                }`}
              >
                {scalperLayout !== "ce_pe" && (
                  <CandleChart
                    instrumentToken={spotToken}
                    symbolLabel={`${scalperUnderlying} Spot`}
                    exchangeSegment="nse_cm"
                    liveQuote={quotesMap[spotToken]}
                    height={460}
                  />
                )}
                <CandleChart
                  instrumentToken={primaryOption?.token || ""}
                  symbolLabel={primaryOption?.symbol || ""}
                  exchangeSegment="nse_fo"
                  liveQuote={primaryOption ? quotesMap[primaryOption.token] : undefined}
                  showTradeButtons
                  oneTapEnabled={defaults.one_tap_enabled}
                  orderPending={primaryPending}
                  onQuickTrade={handleQuickTrade}
                  height={460}
                />
                {scalperLayout !== "spot_opt" && (
                  <CandleChart
                    instrumentToken={secondaryOption?.token || ""}
                    symbolLabel={secondaryOption?.symbol || ""}
                    exchangeSegment="nse_fo"
                    liveQuote={secondaryOption ? quotesMap[secondaryOption.token] : undefined}
                    showTradeButtons
                    oneTapEnabled={defaults.one_tap_enabled}
                    orderPending={secondaryPending}
                    onQuickTrade={handleQuickTrade}
                    height={460}
                  />
                )}
              </div>

              {/* Compact Live Positions Strip below Scalper */}
              <div className="rounded-lg border border-panelBorder bg-panel p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-zinc-300">Open Scalper Positions</span>
                  <span className={`font-mono text-xs font-bold ${totalPnl >= 0 ? "text-bull" : "text-bear"}`}>
                    Total MTM: {totalPnl >= 0 ? "+" : ""}₹{totalPnl.toFixed(2)}
                  </span>
                </div>
                {positions.filter((p) => p.net_qty !== 0).length === 0 ? (
                  <div className="text-xs text-zinc-500">No open positions. Use Buy / Sell on the option chart to place a 1-Tap trade.</div>
                ) : (
                  <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                    {positions
                      .filter((p) => p.net_qty !== 0)
                      .map((p) => (
                        <div
                          key={`${p.instrument_token}_${p.product}`}
                          className="flex items-center justify-between rounded border border-panelBorder bg-obsidian px-3 py-2 text-xs"
                        >
                          <div>
                            <div className="font-semibold text-white">{p.trading_symbol}</div>
                            <div className="text-zinc-400">
                              Qty: {p.net_qty} ({p.product}) | Avg ₹{p.avg_price.toFixed(2)} | LTP ₹{p.ltp.toFixed(2)}
                            </div>
                          </div>
                          <div className="text-right">
                            <div className={`font-mono font-bold ${p.pnl >= 0 ? "text-bull" : "text-bear"}`}>
                              {p.pnl >= 0 ? "+" : ""}₹{p.pnl.toFixed(2)}
                            </div>
                            <button
                              type="button"
                              onClick={() => void handleExitPosition(p)}
                              disabled={pendingOrderKeys.has(positionExitKey(p))}
                              className="mt-1 rounded bg-zinc-800 px-2 py-0.5 text-[10px] text-zinc-200 hover:bg-bear hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {pendingOrderKeys.has(positionExitKey(p)) ? "Exiting..." : "Exit"}
                            </button>
                          </div>
                        </div>
                      ))}
                  </div>
                )}
              </div>
            </>
          )}

          {workspace === "options" && (
            <div className="grid grid-cols-1 xl:grid-cols-12 gap-3">
              <div className="xl:col-span-8">
                <OptionChainPanel
                  chain={displayOptionChain}
                  underlying={scalperUnderlying}
                  onChangeUnderlying={changeUnderlying}
                  onSelectExpiry={changeExpiry}
                  onSelectContract={(token, symbol) => setPrimaryOption({ token, symbol })}
                  onQuickTrade={handleQuickTrade}
                  oneTapEnabled={defaults.one_tap_enabled}
                  pendingOrderKeys={pendingOrderKeys}
                />
              </div>
              <div className="xl:col-span-4">
                <CandleChart
                  instrumentToken={primaryOption?.token || ""}
                  symbolLabel={primaryOption?.symbol || ""}
                  exchangeSegment="nse_fo"
                  liveQuote={primaryOption ? quotesMap[primaryOption.token] : undefined}
                  showTradeButtons
                  oneTapEnabled={defaults.one_tap_enabled}
                  orderPending={primaryPending}
                  onQuickTrade={handleQuickTrade}
                  height={480}
                />
              </div>
            </div>
          )}

          {workspace === "trade" && (
            <div className="space-y-3">
              <CandleChart
                instrumentToken={stockSelection.token}
                symbolLabel={stockSelection.symbol}
                exchangeSegment={stockSelection.segment}
                liveQuote={quotesMap[stockSelection.token]}
                showTradeButtons
                oneTapEnabled={defaults.one_tap_enabled}
                orderPending={stockPending}
                onQuickTrade={handleQuickTrade}
                height={460}
              />
            </div>
          )}

          {workspace === "portfolio" && (
            <div className="rounded-lg border border-panelBorder bg-panel overflow-hidden">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-panelBorder px-4 py-2.5 bg-[#0e1118]">
                <div className="flex items-center gap-2 text-xs">
                  {(["positions", "orders", "holdings", "risk"] as PortfolioSubTab[]).map((t) => (
                    <button
                      key={t}
                      onClick={() => setPortfolioTab(t)}
                      className={`rounded-md px-3 py-1.5 font-semibold capitalize ${
                        portfolioTab === t ? "bg-accent text-white" : "text-zinc-400 hover:text-white"
                      }`}
                    >
                      {t === "risk" ? "Risk & 1-Tap Defaults" : t}
                    </button>
                  ))}
                </div>
                {limits && (
                  <div className="flex items-center gap-4 text-xs text-zinc-400">
                    <span>
                      Available Cash: <strong className="text-white font-mono">₹{limits.availableCash ?? limits.Net}</strong>
                    </span>
                    <span>
                      Margin Used: <strong className="text-amber-300 font-mono">₹{limits.marginUsed ?? limits.MarginUsed}</strong>
                    </span>
                  </div>
                )}
              </div>

              <div className="p-4">
                {portfolioTab === "positions" && (
                  <div>
                    <div className="flex justify-between items-center mb-3">
                      <span className="text-sm font-semibold">Positions</span>
                      <button
                        type="button"
                        onClick={handleHotExitAll}
                        disabled={hotExitPending}
                        className="rounded bg-bear px-3 py-1 text-xs font-bold text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {hotExitPending ? "Hot Exit Pending..." : "Hot Exit All"}
                      </button>
                    </div>
                    <table className="w-full text-xs">
                      <thead className="text-zinc-400 border-b border-panelBorder">
                        <tr>
                          <th className="py-2 text-left">Symbol</th>
                          <th className="py-2 text-left">Product</th>
                          <th className="py-2 text-right">Net Qty</th>
                          <th className="py-2 text-right">Avg Price</th>
                          <th className="py-2 text-right">LTP</th>
                          <th className="py-2 text-right">P&amp;L</th>
                          <th className="py-2 text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {positions.map((p) => (
                          <tr key={`${p.instrument_token}_${p.product}`} className="border-b border-panelBorder/50">
                            <td className="py-2 font-semibold text-white">{p.trading_symbol}</td>
                            <td className="py-2 text-zinc-300">{p.product}</td>
                            <td className="py-2 text-right font-mono">{p.net_qty}</td>
                            <td className="py-2 text-right font-mono">₹{p.avg_price.toFixed(2)}</td>
                            <td className="py-2 text-right font-mono">₹{p.ltp.toFixed(2)}</td>
                            <td className={`py-2 text-right font-mono font-bold ${p.pnl >= 0 ? "text-bull" : "text-bear"}`}>
                              {p.pnl >= 0 ? "+" : ""}₹{p.pnl.toFixed(2)}
                            </td>
                            <td className="py-2 text-right">
                              {p.net_qty !== 0 && (
                                <button
                                  type="button"
                                  onClick={() => void handleExitPosition(p)}
                                  disabled={pendingOrderKeys.has(positionExitKey(p))}
                                  className="rounded bg-zinc-800 px-2 py-0.5 hover:bg-bear text-white disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  {pendingOrderKeys.has(positionExitKey(p)) ? "Exiting..." : "Exit"}
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {portfolioTab === "orders" && (
                  <div>
                    <div className="flex justify-between items-center mb-3">
                      <span className="text-sm font-semibold">Order Book</span>
                      <button
                        onClick={() => api.bulkCancel().then(refreshPortfolio)}
                        className="rounded border border-panelBorder bg-obsidian px-3 py-1 text-xs text-zinc-300 hover:text-white"
                      >
                        Bulk Cancel Open
                      </button>
                    </div>
                    <table className="w-full text-xs">
                      <thead className="text-zinc-400 border-b border-panelBorder">
                        <tr>
                          <th className="py-2 text-left">Time</th>
                          <th className="py-2 text-left">Symbol</th>
                          <th className="py-2 text-left">Side</th>
                          <th className="py-2 text-left">Type</th>
                          <th className="py-2 text-right">Qty</th>
                          <th className="py-2 text-right">Price</th>
                          <th className="py-2 text-left">Status</th>
                          <th className="py-2 text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {orders.map((o) => (
                          <tr key={o.order_id} className="border-b border-panelBorder/50">
                            <td className="py-2 text-zinc-400">{o.order_time}</td>
                            <td className="py-2 font-semibold text-white">{o.trading_symbol}</td>
                            <td className={`py-2 font-bold ${o.transaction_type === "B" ? "text-bull" : "text-bear"}`}>
                              {o.transaction_type === "B" ? "BUY" : "SELL"}
                            </td>
                            <td className="py-2 text-zinc-300">
                              {o.product} / {o.order_type}
                            </td>
                            <td className="py-2 text-right font-mono">
                              {o.filled_quantity}/{o.quantity}
                            </td>
                            <td className="py-2 text-right font-mono">₹{(o.avg_price || o.price).toFixed(2)}</td>
                            <td className="py-2 uppercase text-zinc-300">{o.status}</td>
                            <td className="py-2 text-right">
                              {o.status === "open" && (
                                <button
                                  onClick={() => api.cancelOrder(o.order_id).then(refreshPortfolio)}
                                  className="rounded bg-zinc-800 px-2 py-0.5 text-zinc-200 hover:bg-bear"
                                >
                                  Cancel
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {portfolioTab === "holdings" && (
                  <table className="w-full text-xs">
                    <thead className="text-zinc-400 border-b border-panelBorder">
                      <tr>
                        <th className="py-2 text-left">Symbol</th>
                        <th className="py-2 text-right">Qty</th>
                        <th className="py-2 text-right">Avg Cost</th>
                        <th className="py-2 text-right">LTP</th>
                        <th className="py-2 text-right">Mkt Value</th>
                        <th className="py-2 text-right">P&amp;L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {holdings.map((h) => (
                        <tr key={h.instrument_token} className="border-b border-panelBorder/50">
                          <td className="py-2 font-semibold text-white">{h.trading_symbol}</td>
                          <td className="py-2 text-right font-mono">{h.quantity}</td>
                          <td className="py-2 text-right font-mono">₹{h.average_price.toFixed(2)}</td>
                          <td className="py-2 text-right font-mono">₹{h.ltp.toFixed(2)}</td>
                          <td className="py-2 text-right font-mono">₹{h.mkt_value.toFixed(2)}</td>
                          <td className={`py-2 text-right font-mono font-bold ${h.pnl >= 0 ? "text-bull" : "text-bear"}`}>
                            {h.pnl >= 0 ? "+" : ""}₹{h.pnl.toFixed(2)} ({h.pnl_pct.toFixed(2)}%)
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                {portfolioTab === "risk" && (
                  <div className="grid gap-4 md:grid-cols-2 text-xs">
                    <div className="rounded-lg border border-panelBorder bg-obsidian p-4 space-y-3">
                      <h3 className="text-sm font-bold text-white flex items-center gap-2">
                        <Sliders className="h-4 w-4 text-accent" /> 1-Tap Order Defaults
                      </h3>
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-zinc-400 mb-1">Default Lots</label>
                          <input
                            type="number"
                            min={1}
                            value={defaults.default_lots}
                            onChange={(e) => setDefaults({ ...defaults, default_lots: Number(e.target.value) || 1 })}
                            className="w-full rounded border border-panelBorder bg-panel px-2.5 py-1.5 text-white"
                          />
                        </div>
                        <div>
                          <label className="block text-zinc-400 mb-1">Product</label>
                          <select
                            value={defaults.default_product}
                            onChange={(e) => setDefaults({ ...defaults, default_product: e.target.value as OrderDefaults["default_product"] })}
                            className="w-full rounded border border-panelBorder bg-panel px-2.5 py-1.5 text-white"
                          >
                            <option value="MIS">MIS (Intraday)</option>
                            <option value="NRML">NRML (Overnight)</option>
                            <option value="CNC">CNC (Delivery)</option>
                            <option value="MTF">MTF (Margin Trading Facility)</option>
                          </select>
                        </div>
                      </div>
                      <button
                        onClick={() =>
                          api.setOrderDefaults(defaults).then((res) => {
                            setDefaults(res.data);
                            showToast("Order defaults saved");
                          })
                        }
                        className="rounded bg-accent px-4 py-1.5 font-bold text-white"
                      >
                        Save Defaults
                      </button>
                    </div>

                    <div className="rounded-lg border border-panelBorder bg-obsidian p-4 space-y-3">
                      <h3 className="text-sm font-bold text-white flex items-center gap-2">
                        <ShieldAlert className="h-4 w-4 text-amber-400" /> P&amp;L Protect (Auto Square-Off)
                      </h3>
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-zinc-400 mb-1">Max Loss (₹)</label>
                          <input
                            type="number"
                            value={pnlProtect.max_loss}
                            onChange={(e) => setPnlProtect({ ...pnlProtect, max_loss: Number(e.target.value) })}
                            className="w-full rounded border border-panelBorder bg-panel px-2.5 py-1.5 text-white"
                          />
                        </div>
                        <div>
                          <label className="block text-zinc-400 mb-1">Max Profit Target (₹)</label>
                          <input
                            type="number"
                            value={pnlProtect.max_profit}
                            onChange={(e) => setPnlProtect({ ...pnlProtect, max_profit: Number(e.target.value) })}
                            className="w-full rounded border border-panelBorder bg-panel px-2.5 py-1.5 text-white"
                          />
                        </div>
                      </div>
                      <label className="flex items-center gap-2 text-zinc-300">
                        <input
                          type="checkbox"
                          checked={pnlProtect.enabled}
                          onChange={(e) => setPnlProtect({ ...pnlProtect, enabled: e.target.checked })}
                        />
                        Enable P&amp;L Protect Auto-Exit
                      </label>
                      <label className="flex items-center gap-2 text-zinc-300">
                        <input
                          type="checkbox"
                          checked={pnlProtect.auto_activate_kill_switch}
                          onChange={(e) => setPnlProtect({ ...pnlProtect, auto_activate_kill_switch: e.target.checked })}
                        />
                        Auto-Activate Kill Switch on Trigger
                      </label>
                      <button
                        onClick={() =>
                          api.setPnlProtect(pnlProtect).then((res) => {
                            setPnlProtect(res.data);
                            showToast("P&L Protect settings updated");
                          })
                        }
                        className="rounded bg-amber-500 px-4 py-1.5 font-bold text-black"
                      >
                        Save P&amp;L Protect
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </main>

        {/* Right Docked Sahi Tool Drawer */}
        <aside className="hidden lg:flex w-80 flex-col border-l border-panelBorder bg-panel">
          <div className="grid grid-cols-4 border-b border-panelBorder text-[11px] font-medium">
            {[
              { id: "watchlist", label: "Watchlist", icon: List },
              { id: "scanners", label: "Scanners", icon: Radar },
              { id: "depth", label: "Depth", icon: Activity },
              { id: "alerts", label: "Alerts", icon: Bell },
            ].map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  onClick={() => setRightTab(tab.id as RightDrawerTab)}
                  className={`flex flex-col items-center gap-1 py-2 ${
                    rightTab === tab.id ? "border-b-2 border-accent text-white bg-obsidian/50" : "text-zinc-400 hover:text-white"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </div>

          <div className="flex-1 overflow-auto p-3 text-xs space-y-2">
            {rightTab === "watchlist" &&
              watchlistQuotes.map((q) => {
                const live = quotesMap[q.instrument_token] || q;
                const up = live.change_pct >= 0;
                return (
                  <div
                    key={live.instrument_token}
                    onClick={() => {
                      if (live.exchange_segment === "nse_fo") {
                        setWorkspace("options");
                        showToast("Select the option from the loaded chain to avoid trading a stale contract.");
                      } else {
                        setStockSelection({
                          token: live.instrument_token,
                          symbol: live.trading_symbol,
                          segment: live.exchange_segment,
                        });
                        setWorkspace("trade");
                      }
                    }}
                    className="flex items-center justify-between rounded border border-panelBorder bg-obsidian px-2.5 py-2 cursor-pointer hover:border-zinc-600"
                  >
                    <div>
                      <div className="font-semibold text-white">{live.trading_symbol}</div>
                      <div className="text-[10px] text-zinc-500">{live.exchange_segment}</div>
                    </div>
                    <div className="text-right font-mono">
                      <div className="text-white font-semibold">₹{live.ltp.toFixed(2)}</div>
                      <div className={up ? "text-bull text-[10px]" : "text-bear text-[10px]"}>
                        {up ? "+" : ""}
                        {live.change_pct.toFixed(2)}%
                      </div>
                    </div>
                  </div>
                );
              })}

            {rightTab === "scanners" && (
              <div className="space-y-2">
                <select
                  value={activeScanner}
                  onChange={(e) => setActiveScanner(e.target.value)}
                  className="w-full rounded border border-panelBorder bg-obsidian px-2.5 py-1.5 text-white"
                >
                  <option value="top_gainers">Top Gainers</option>
                  <option value="top_losers">Top Losers</option>
                  <option value="high_oi_options">High OI Active Options</option>
                  <option value="high_volume">Volume Shockers</option>
                </select>
                {scannerRows.slice(0, 15).map((row) => (
                  <div
                    key={row.instrument_token}
                    onClick={() => {
                      if (row.exchange_segment === "nse_fo") {
                        setWorkspace("options");
                        showToast("Select the option from the loaded chain to avoid trading a stale contract.");
                      } else {
                        setStockSelection({
                          token: row.instrument_token,
                          symbol: row.trading_symbol,
                          segment: row.exchange_segment,
                        });
                        setWorkspace("trade");
                      }
                    }}
                    className="flex items-center justify-between rounded border border-panelBorder bg-obsidian px-2.5 py-1.5 cursor-pointer hover:border-zinc-600"
                  >
                    <span className="font-medium text-white truncate max-w-[150px]">{row.trading_symbol}</span>
                    <span className={`font-mono ${row.change_pct >= 0 ? "text-bull" : "text-bear"}`}>
                      ₹{row.ltp.toFixed(2)} ({row.change_pct >= 0 ? "+" : ""}
                      {row.change_pct.toFixed(1)}%)
                    </span>
                  </div>
                ))}
              </div>
            )}

            {rightTab === "depth" && depthTargetQuote && (
              <div className="space-y-2">
                <div className="font-semibold text-white">{depthTargetQuote.trading_symbol} — 5-Level Depth</div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <div className="text-[10px] text-bull mb-1 font-semibold">BID (QTY)</div>
                    {depthTargetQuote.depth_bids?.map((b, idx) => (
                      <div key={idx} className="flex justify-between font-mono py-0.5 border-b border-panelBorder/40">
                        <span className="text-bull">{b.price.toFixed(2)}</span>
                        <span className="text-zinc-300">{b.qty}</span>
                      </div>
                    ))}
                  </div>
                  <div>
                    <div className="text-[10px] text-bear mb-1 font-semibold">ASK (QTY)</div>
                    {depthTargetQuote.depth_asks?.map((a, idx) => (
                      <div key={idx} className="flex justify-between font-mono py-0.5 border-b border-panelBorder/40">
                        <span className="text-bear">{a.price.toFixed(2)}</span>
                        <span className="text-zinc-300">{a.qty}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {rightTab === "alerts" && (
              <div className="space-y-3">
                {primaryOption ? (
                  <div className="rounded border border-panelBorder bg-obsidian p-2.5 space-y-2">
                    <div className="font-semibold text-white">Alert on {primaryOption.symbol}</div>
                    <div className="flex gap-2">
                      <input
                        type="number"
                        placeholder="Target ₹"
                        value={alertPrice}
                        onChange={(e) => setAlertPrice(e.target.value)}
                        className="flex-1 rounded border border-panelBorder bg-panel px-2 py-1 text-white"
                      />
                      <button
                        onClick={() => {
                          if (!alertPrice) return;
                          api
                            .createAlert({
                              instrument_token: primaryOption.token,
                              trading_symbol: primaryOption.symbol,
                              condition: "ABOVE",
                              target_price: Number(alertPrice),
                              note: "Web terminal alert",
                            })
                            .then(() => {
                              setAlertPrice("");
                              refreshControls();
                              showToast("Super Alert created");
                            })
                            .catch((err) => showToast(`Alert failed: ${err instanceof Error ? err.message : "Unknown error"}`));
                        }}
                        className="rounded bg-accent px-2.5 py-1 font-semibold text-white"
                      >
                        Add
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="rounded border border-panelBorder bg-obsidian p-2.5 text-zinc-400">
                    Select a contract from the loaded option chain to create an alert.
                  </div>
                )}
                {alerts.map((al) => (
                  <div key={al.id} className="flex items-center justify-between rounded border border-panelBorder bg-obsidian px-2.5 py-2">
                    <div>
                      <div className="font-semibold text-white">{al.trading_symbol}</div>
                      <div className="text-[10px] text-zinc-400">
                        {al.condition} ₹{al.target_price} • {al.status}
                      </div>
                    </div>
                    <button
                      onClick={() => api.deleteAlert(al.id).then(refreshControls)}
                      className="text-zinc-400 hover:text-bear"
                    >
                      Delete
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>
      </div>

      {toast && (
        <div className="fixed bottom-4 right-4 z-50 rounded-lg border border-panelBorder bg-[#171c28] px-4 py-2.5 text-xs font-semibold text-white shadow-xl">
          {toast}
        </div>
      )}

      <LoginModal
        open={loginOpen}
        currentMode={mode}
        onClose={() => setLoginOpen(false)}
        onSuccess={(m, u, msg) => {
          setMode(m);
          setUcc(u);
          showToast(msg);
          void refreshPortfolio();
          void refreshControls();
        }}
      />
    </div>
  );
}
