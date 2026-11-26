"use client";

import React from "react";
import { OptionChainResponse, OptionChainStrike } from "@/lib/types";

interface Props {
  chain: OptionChainResponse | null;
  underlying: string;
  onChangeUnderlying: (u: string) => void;
  onSelectExpiry: (exp: string) => void;
  onSelectContract: (token: string, symbol: string) => void;
  onQuickTrade: (side: "B" | "S", token: string, symbol: string, segment: string) => Promise<void> | void;
  oneTapEnabled?: boolean;
  pendingOrderKeys?: Set<string>;
}

function QuickTradeButtons({
  token,
  symbol,
  enabled,
  pending,
  onQuickTrade,
}: {
  token: string;
  symbol: string;
  enabled: boolean;
  pending: boolean;
  onQuickTrade: (side: "B" | "S", token: string, symbol: string, segment: string) => Promise<void> | void;
}) {
  if (!enabled) return <span className="text-[10px] font-semibold text-zinc-500">1-Tap OFF</span>;
  return (
    <div className="inline-flex gap-1">
      {(["B", "S"] as const).map((side) => (
        <button
          key={side}
          disabled={pending}
          onClick={() => onQuickTrade(side, token, symbol, "nse_fo")}
          className={`rounded px-1.5 py-0.5 text-[10px] font-bold disabled:cursor-not-allowed disabled:opacity-50 ${
            side === "B" ? "bg-bull/90 text-black hover:bg-bull" : "bg-bear/90 text-white hover:bg-bear"
          }`}
        >
          {side}
        </button>
      ))}
    </div>
  );
}

export function OptionChainPanel({
  chain,
  underlying,
  onChangeUnderlying,
  onSelectExpiry,
  onSelectContract,
  onQuickTrade,
  oneTapEnabled = false,
  pendingOrderKeys = new Set(),
}: Props) {
  if (!chain) {
    return (
      <div className="rounded-lg border border-panelBorder bg-panel p-6 text-center text-sm text-zinc-400">
        Loading {underlying} Option Chain...
      </div>
    );
  }

  const maxOi = Math.max(
    1,
    ...chain.strikes.map((s) => Math.max(s.call_oi, s.put_oi))
  );

  return (
    <div className="flex flex-col rounded-lg border border-panelBorder bg-panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-panelBorder px-4 py-2.5 bg-[#0e1118]">
        <div className="flex items-center gap-3">
          <div className="flex rounded-md border border-panelBorder bg-obsidian p-0.5 text-xs">
            {["NIFTY", "BANKNIFTY"].map((u) => (
              <button
                key={u}
                onClick={() => onChangeUnderlying(u)}
                className={`rounded px-2.5 py-1 font-semibold ${
                  underlying === u ? "bg-accent text-white" : "text-zinc-400 hover:text-white"
                }`}
              >
                {u}
              </button>
            ))}
          </div>
          <select
            value={chain.expiry}
            onChange={(e) => onSelectExpiry(e.target.value)}
            className="rounded border border-panelBorder bg-obsidian px-2.5 py-1 text-xs text-zinc-200"
          >
            {chain.available_expiries.map((exp) => (
              <option key={exp} value={exp}>
                Expiry: {exp}
              </option>
            ))}
          </select>
          <span className="text-xs text-zinc-400">
            Spot: <strong className="text-white font-mono">₹{chain.spot_price.toFixed(2)}</strong>
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-4 text-xs text-zinc-400">
          <span>
            PCR: <strong className="text-white font-mono">{chain.pcr.toFixed(2)}</strong>
          </span>
          <span>
            Max Call OI: <strong className="text-bear font-mono">{chain.max_call_oi_strike}</strong>
          </span>
          <span>
            Max Put OI: <strong className="text-bull font-mono">{chain.max_put_oi_strike}</strong>
          </span>
          <span>
            Exp Move: <strong className="text-amber-400 font-mono">±{chain.expected_move_pct}%</strong>
          </span>
        </div>
      </div>

      <div className="overflow-auto max-h-[540px]">
        <table className="w-full text-xs border-collapse">
          <thead className="sticky top-0 z-10 bg-[#121620] text-zinc-400 border-b border-panelBorder">
            <tr>
              <th className="px-2 py-2 text-right">Call OI</th>
              <th className="px-2 py-2 text-right">IV / Δ</th>
              <th className="px-2 py-2 text-right">Call LTP</th>
              <th className="px-2 py-2 text-center">CE Action</th>
              <th className="px-3 py-2 text-center bg-[#181d2a] text-white">Strike</th>
              <th className="px-2 py-2 text-center">PE Action</th>
              <th className="px-2 py-2 text-left">Put LTP</th>
              <th className="px-2 py-2 text-left">IV / Δ</th>
              <th className="px-2 py-2 text-left">Put OI</th>
            </tr>
          </thead>
          <tbody>
            {chain.strikes.map((row: OptionChainStrike) => {
              const ceBarPct = Math.round((row.call_oi / maxOi) * 100);
              const peBarPct = Math.round((row.put_oi / maxOi) * 100);
              return (
                <tr
                  key={row.strike_price}
                  className={`border-b border-panelBorder/60 hover:bg-zinc-800/40 ${
                    row.is_atm ? "bg-amber-500/10" : ""
                  }`}
                >
                  <td className="px-2 py-1.5 text-right font-mono relative">
                    <div
                      className="absolute right-0 top-1 bottom-1 bg-bear/15 pointer-events-none"
                      style={{ width: `${ceBarPct}%` }}
                    />
                    <span className="relative text-zinc-300">{(row.call_oi / 100000).toFixed(2)}L</span>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-zinc-400">
                    {row.call_iv.toFixed(1)}% / {row.call_delta.toFixed(2)}
                  </td>
                  <td
                    onClick={() => row.call_token && row.call_symbol && onSelectContract(row.call_token, row.call_symbol)}
                    className="px-2 py-1.5 text-right font-mono cursor-pointer hover:underline"
                  >
                    <div className="text-white font-semibold">₹{row.call_ltp.toFixed(2)}</div>
                    <div className={row.call_change_pct >= 0 ? "text-bull text-[10px]" : "text-bear text-[10px]"}>
                      {row.call_change_pct >= 0 ? "+" : ""}
                      {row.call_change_pct.toFixed(1)}%
                    </div>
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    {row.call_token && row.call_symbol && (
                      <QuickTradeButtons
                        token={row.call_token}
                        symbol={row.call_symbol}
                        enabled={oneTapEnabled}
                        pending={pendingOrderKeys.has(`B:${row.call_token}`) || pendingOrderKeys.has(`S:${row.call_token}`)}
                        onQuickTrade={onQuickTrade}
                      />
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-center font-mono font-bold bg-[#161b26] text-white">
                    {row.strike_price}
                    {row.is_atm && <span className="ml-1 rounded bg-amber-500/20 px-1 text-[9px] text-amber-300">ATM</span>}
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    {row.put_token && row.put_symbol && (
                      <QuickTradeButtons
                        token={row.put_token}
                        symbol={row.put_symbol}
                        enabled={oneTapEnabled}
                        pending={pendingOrderKeys.has(`B:${row.put_token}`) || pendingOrderKeys.has(`S:${row.put_token}`)}
                        onQuickTrade={onQuickTrade}
                      />
                    )}
                  </td>
                  <td
                    onClick={() => row.put_token && row.put_symbol && onSelectContract(row.put_token, row.put_symbol)}
                    className="px-2 py-1.5 text-left font-mono cursor-pointer hover:underline"
                  >
                    <div className="text-white font-semibold">₹{row.put_ltp.toFixed(2)}</div>
                    <div className={row.put_change_pct >= 0 ? "text-bull text-[10px]" : "text-bear text-[10px]"}>
                      {row.put_change_pct >= 0 ? "+" : ""}
                      {row.put_change_pct.toFixed(1)}%
                    </div>
                  </td>
                  <td className="px-2 py-1.5 text-left font-mono text-zinc-400">
                    {row.put_iv.toFixed(1)}% / {row.put_delta.toFixed(2)}
                  </td>
                  <td className="px-2 py-1.5 text-left font-mono relative">
                    <div
                      className="absolute left-0 top-1 bottom-1 bg-bull/15 pointer-events-none"
                      style={{ width: `${peBarPct}%` }}
                    />
                    <span className="relative text-zinc-300">{(row.put_oi / 100000).toFixed(2)}L</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
