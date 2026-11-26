"use client";

import React, { useEffect, useRef, useState } from "react";
import { createChart, ColorType, IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { api } from "@/lib/api";
import { Candle, QuoteData } from "@/lib/types";

interface Props {
  instrumentToken: string;
  symbolLabel: string;
  exchangeSegment: string;
  liveQuote?: QuoteData;
  showTradeButtons?: boolean;
  oneTapEnabled?: boolean;
  orderPending?: boolean;
  onQuickTrade?: (side: "B" | "S", token: string, symbol: string, segment: string) => Promise<void> | void;
  height?: number;
}

const TIMEFRAMES = ["1m", "2m", "3m", "5m", "15m", "1D"];

function timeframeSeconds(timeframe: string): number {
  return timeframe === "1D" ? 86_400 : Math.max(60, parseInt(timeframe, 10) * 60 || 60);
}

function mergeTick(previous: Candle, quote: QuoteData, timeframe: string): Candle | null {
  if (!Number.isFinite(quote.ltp) || !Number.isFinite(quote.updated_at)) return null;
  const rawTime = quote.updated_at > 1_000_000_000_000 ? Math.floor(quote.updated_at / 1_000) : quote.updated_at;
  const step = timeframeSeconds(timeframe);
  const bucket = Math.floor(rawTime / step) * step;
  if (bucket < previous.time) return null;
  if (bucket === previous.time) {
    return {
      ...previous,
      high: Math.max(previous.high, quote.ltp),
      low: Math.min(previous.low, quote.ltp),
      close: quote.ltp,
    };
  }
  return {
    time: bucket,
    open: previous.close,
    high: Math.max(previous.close, quote.ltp),
    low: Math.min(previous.close, quote.ltp),
    close: quote.ltp,
    volume: 0,
  };
}

export function CandleChart({
  instrumentToken,
  symbolLabel,
  exchangeSegment,
  liveQuote,
  showTradeButtons = false,
  oneTapEnabled = false,
  orderPending = false,
  onQuickTrade,
  height = 420,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const emaSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const lastCandleRef = useRef<Candle | null>(null);
  const latestQuoteRef = useRef<QuoteData | undefined>(liveQuote);
  const lastTickRef = useRef<{ token: string; updatedAt: number }>({ token: "", updatedAt: -1 });
  const [timeframe, setTimeframe] = useState("1m");
  const [showEma, setShowEma] = useState(true);
  const [showVwap, setShowVwap] = useState(true);
  const [loading, setLoading] = useState(true);
  const [synthetic, setSynthetic] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  latestQuoteRef.current = liveQuote;

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0b0e14" },
        textColor: "#8b93a7",
      },
      grid: {
        vertLines: { color: "#151a24" },
        horzLines: { color: "#151a24" },
      },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: "#1e232e" },
      timeScale: {
        borderColor: "#1e232e",
        timeVisible: true,
        secondsVisible: false,
      },
      height,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#16c784",
      downColor: "#ea3943",
      borderVisible: false,
      wickUpColor: "#16c784",
      wickDownColor: "#ea3943",
    });
    const emaSeries = chart.addLineSeries({ color: "#3b82f6", lineWidth: 1, title: "EMA20" });
    const vwapSeries = chart.addLineSeries({ color: "#f59e0b", lineWidth: 1, title: "VWAP" });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    emaSeriesRef.current = emaSeries;
    vwapSeriesRef.current = vwapSeries;

    const handleResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener("resize", handleResize);
    handleResize();

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      emaSeriesRef.current = null;
      vwapSeriesRef.current = null;
      lastCandleRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    const series = candleSeriesRef.current;
    const emaSeries = emaSeriesRef.current;
    const vwapSeries = vwapSeriesRef.current;
    const chart = chartRef.current;
    if (!series || !emaSeries || !vwapSeries || !chart) return;

    series.setData([]);
    lastCandleRef.current = null;
    lastTickRef.current = { token: instrumentToken, updatedAt: -1 };
    setSynthetic(null);
    setError(null);

    if (!instrumentToken) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    const indicators = [showEma ? "ema20" : "", showVwap ? "vwap" : ""].filter(Boolean).join(",");

    api
      .candles(instrumentToken, timeframe, indicators)
      .then((res) => {
        if (cancelled) return;
        const byTime = new Map<number, Candle>();
        for (const candle of res.candles || []) {
          if (Number.isFinite(candle.time)) byTime.set(candle.time, candle);
        }
        const candles = [...byTime.values()].sort((a, b) => a.time - b.time);
        const bars = candles.map((candle) => ({
          time: candle.time as UTCTimestamp,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        }));
        series.setData(bars);
        if (candles.length) lastCandleRef.current = candles[candles.length - 1];
        setSynthetic(Boolean(res.synthetic));

        if (showEma) {
          emaSeries.setData(
            (res.indicators?.ema20 || [])
              .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value))
              .sort((a, b) => a.time - b.time)
              .map((point) => ({ time: point.time as UTCTimestamp, value: point.value })),
          );
        } else {
          emaSeries.setData([]);
        }
        if (showVwap) {
          vwapSeries.setData(
            (res.indicators?.vwap || [])
              .filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value))
              .sort((a, b) => a.time - b.time)
              .map((point) => ({ time: point.time as UTCTimestamp, value: point.value })),
          );
        } else {
          vwapSeries.setData([]);
        }

        const quote = latestQuoteRef.current;
        const previous = lastCandleRef.current;
        if (quote?.instrument_token === instrumentToken && previous) {
          const merged = mergeTick(previous, quote, timeframe);
          if (merged) {
            lastCandleRef.current = merged;
            lastTickRef.current = { token: instrumentToken, updatedAt: quote.updated_at };
            series.update({ time: merged.time as UTCTimestamp, open: merged.open, high: merged.high, low: merged.low, close: merged.close });
          }
        }
        chart.timeScale().fitContent();
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load candles");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [instrumentToken, timeframe, showEma, showVwap, height]);

  // Ignore delayed ticks and merge only the newest tick into the active candle.
  useEffect(() => {
    const quote = liveQuote;
    const series = candleSeriesRef.current;
    const previous = lastCandleRef.current;
    if (!quote || !series || !previous || quote.instrument_token !== instrumentToken) return;
    const lastTick = lastTickRef.current;
    if (lastTick.token === instrumentToken && quote.updated_at < lastTick.updatedAt) return;
    const merged = mergeTick(previous, quote, timeframe);
    if (!merged) return;
    lastTickRef.current = { token: instrumentToken, updatedAt: quote.updated_at };
    lastCandleRef.current = merged;
    series.update({ time: merged.time as UTCTimestamp, open: merged.open, high: merged.high, low: merged.low, close: merged.close });
  }, [liveQuote, instrumentToken, timeframe]);

  const ltp = liveQuote?.ltp ?? lastCandleRef.current?.close ?? 0;
  const chgPct = liveQuote?.change_pct ?? 0;
  const isUp = chgPct >= 0;
  const canQuickTrade = Boolean(showTradeButtons && onQuickTrade && oneTapEnabled && instrumentToken);

  return (
    <div className="flex flex-col rounded-lg border border-panelBorder bg-[#0b0e14] overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-panelBorder bg-panel px-3 py-2">
        <div className="flex items-center gap-3">
          <span className="font-semibold text-sm text-white">{symbolLabel || "No contract selected"}</span>
          <span className={`font-mono text-sm font-semibold ${isUp ? "text-bull" : "text-bear"}`}>
            ₹{ltp.toFixed(2)} ({isUp ? "+" : ""}{chgPct.toFixed(2)}%)
          </span>
          {instrumentToken && synthetic !== null && (
            <span
              title={synthetic ? "Simulated candles — not live exchange data" : "Live broker candles"}
              className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${synthetic ? "bg-amber-500/20 text-amber-300" : "bg-bull/15 text-bull"}`}
            >
              {synthetic ? "SYNTHETIC" : "LIVE"}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`rounded px-2 py-0.5 text-xs font-medium transition ${
                timeframe === tf ? "bg-zinc-700 text-white" : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
              }`}
            >
              {tf}
            </button>
          ))}
          <button
            onClick={() => setShowEma((value) => !value)}
            className={`ml-2 rounded border px-2 py-0.5 text-xs ${
              showEma ? "border-blue-500/60 bg-blue-500/15 text-blue-300" : "border-zinc-700 text-zinc-400"
            }`}
          >
            EMA20
          </button>
          <button
            onClick={() => setShowVwap((value) => !value)}
            className={`rounded border px-2 py-0.5 text-xs ${
              showVwap ? "border-amber-500/60 bg-amber-500/15 text-amber-300" : "border-zinc-700 text-zinc-400"
            }`}
          >
            VWAP
          </button>

          {canQuickTrade && (
            <div className="ml-2 flex items-center gap-1.5">
              <button
                onClick={() => onQuickTrade?.("B", instrumentToken, symbolLabel, exchangeSegment)}
                disabled={orderPending}
                className="rounded-md bg-bull px-3 py-1 text-xs font-bold text-black hover:brightness-110 active:scale-95 transition disabled:cursor-not-allowed disabled:opacity-50"
                title={orderPending ? "Order already pending" : "1-Tap Instant Buy"}
              >
                {orderPending ? "Pending..." : "Buy"}
              </button>
              <button
                onClick={() => onQuickTrade?.("S", instrumentToken, symbolLabel, exchangeSegment)}
                disabled={orderPending}
                className="rounded-md bg-bear px-3 py-1 text-xs font-bold text-white hover:brightness-110 active:scale-95 transition disabled:cursor-not-allowed disabled:opacity-50"
                title={orderPending ? "Order already pending" : "1-Tap Instant Sell"}
              >
                {orderPending ? "Pending..." : "Sell"}
              </button>
            </div>
          )}
          {showTradeButtons && onQuickTrade && !oneTapEnabled && (
            <span className="ml-2 text-[10px] font-semibold text-zinc-500">1-Tap OFF</span>
          )}
        </div>
      </div>

      <div className="relative w-full" style={{ height }}>
        {loading && instrumentToken && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#0b0e14]/70 text-xs text-zinc-400">
            Loading {symbolLabel} candles...
          </div>
        )}
        {!instrumentToken && (
          <div className="absolute inset-0 z-10 flex items-center justify-center px-4 text-center text-xs text-zinc-400">
            Select an ATM or option-chain contract to load candles. No stale contract can be traded.
          </div>
        )}
        {error && (
          <div className="absolute inset-x-0 bottom-0 z-10 bg-bear/20 px-3 py-2 text-xs text-red-200">{error}</div>
        )}
        <div ref={containerRef} className="w-full h-full" />
      </div>
    </div>
  );
}
