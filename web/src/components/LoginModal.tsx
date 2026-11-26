"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

interface Props {
  open: boolean;
  currentMode: string;
  onClose: () => void;
  onSuccess: (mode: string, ucc: string, msg: string) => void;
}

export function LoginModal({ open, currentMode, onClose, onSuccess }: Props) {
  const [mode, setMode] = useState<"paper" | "kotak">(currentMode === "kotak" ? "kotak" : "paper");
  const [consumerKey, setConsumerKey] = useState("");
  const [mobileNumber, setMobileNumber] = useState("");
  const [ucc, setUcc] = useState("DEMO01");
  const [totp, setTotp] = useState("");
  const [mpin, setMpin] = useState("");
  const [staticIp, setStaticIp] = useState<string>("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const requestGeneration = useRef(0);

  const clearSecrets = useCallback(() => {
    setConsumerKey("");
    setTotp("");
    setMpin("");
  }, []);

  const handleClose = useCallback(() => {
    requestGeneration.current += 1;
    clearSecrets();
    setError("");
    setLoading(false);
    onClose();
  }, [clearSecrets, onClose]);

  useEffect(() => {
    if (!open) {
      requestGeneration.current += 1;
      clearSecrets();
      setError("");
      setLoading(false);
      return;
    }
    let cancelled = false;
    api
      .egressIp()
      .then((res) => {
        if (!cancelled && res.ip) setStaticIp(res.ip);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [open, clearSecrets]);

  useEffect(() => () => {
    requestGeneration.current += 1;
    clearSecrets();
  }, [clearSecrets]);

  if (!open) return null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const generation = ++requestGeneration.current;
    setError("");
    setLoading(true);
    try {
      const res = await api.login({
        mode,
        consumer_key: consumerKey,
        mobile_number: mobileNumber,
        ucc,
        totp,
        mpin,
      });
      if (generation !== requestGeneration.current) return;
      clearSecrets();
      onSuccess(res.data.mode, res.data.ucc, res.data.message);
      onClose();
    } catch (err: any) {
      if (generation === requestGeneration.current) setError(err.message || "Authentication failed");
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4">
      <div className="w-full max-w-md rounded-xl border border-panelBorder bg-panel p-5 shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-bold text-white">Broker Connection (Kotak Neo API v3)</h2>
          <button onClick={handleClose} className="text-xs text-zinc-400 hover:text-white">
            Close
          </button>
        </div>

        <div className="mb-4 grid grid-cols-2 gap-2 rounded-lg bg-obsidian p-1 text-xs">
          <button
            type="button"
            onClick={() => setMode("paper")}
            className={`rounded-md py-1.5 font-semibold ${
              mode === "paper" ? "bg-accent text-white" : "text-zinc-400 hover:text-white"
            }`}
          >
            Paper Simulator
          </button>
          <button
            type="button"
            onClick={() => setMode("kotak")}
            className={`rounded-md py-1.5 font-semibold ${
              mode === "kotak" ? "bg-bull text-black" : "text-zinc-400 hover:text-white"
            }`}
          >
            Live Kotak Neo (TOTP)
          </button>
        </div>

        <form onSubmit={submit} className="space-y-3 text-xs">
          {mode === "kotak" ? (
            <>
              <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 py-2 text-[11px] text-blue-200">
                Whitelist this VPS Static IP in Kotak Neo Trade API:{" "}
                <strong className="font-mono text-white">{staticIp || "checking..."}</strong>
              </div>
              <div>
                <label className="block text-zinc-400 mb-1">Kotak Neo Consumer Key (Trade API Token)</label>
                <input
                  type="password"
                  autoComplete="off"
                  value={consumerKey}
                  onChange={(e) => setConsumerKey(e.target.value)}
                  required
                  placeholder="Paste Neo Trade API consumer key"
                  className="w-full rounded border border-panelBorder bg-obsidian px-3 py-2 text-white"
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-zinc-400 mb-1">Mobile Number (+91...)</label>
                  <input
                    value={mobileNumber}
                    onChange={(e) => setMobileNumber(e.target.value)}
                    required
                    placeholder="+9198XXXXXXXX"
                    className="w-full rounded border border-panelBorder bg-obsidian px-3 py-2 text-white"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1">UCC (Client Code)</label>
                  <input
                    value={ucc}
                    onChange={(e) => setUcc(e.target.value)}
                    required
                    placeholder="e.g. XY123"
                    className="w-full rounded border border-panelBorder bg-obsidian px-3 py-2 text-white"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-zinc-400 mb-1">TOTP (6 digits)</label>
                  <input
                    type="password"
                    inputMode="numeric"
                    pattern="[0-9]{6}"
                    minLength={6}
                    maxLength={6}
                    autoComplete="one-time-code"
                    value={totp}
                    onChange={(e) => setTotp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                    required
                    placeholder="123456"
                    className="w-full rounded border border-panelBorder bg-obsidian px-3 py-2 text-white"
                  />
                </div>
                <div>
                  <label className="block text-zinc-400 mb-1">MPIN (6 digits)</label>
                  <input
                    type="password"
                    inputMode="numeric"
                    pattern="[0-9]{6}"
                    minLength={6}
                    maxLength={6}
                    autoComplete="off"
                    value={mpin}
                    onChange={(e) => setMpin(e.target.value.replace(/\D/g, "").slice(0, 6))}
                    required
                    placeholder="••••••"
                    className="w-full rounded border border-panelBorder bg-obsidian px-3 py-2 text-white"
                  />
                </div>
              </div>
            </>
          ) : (
            <div className="rounded-lg border border-panelBorder bg-obsidian p-3 text-zinc-300 leading-relaxed">
              Runs full NIFTY / BANKNIFTY / Equity live tick simulation, Option Chain with Black-Scholes Greeks,
              1-Tap Scalper orders, P&L Protect, and Kill Switch without risking real capital.
            </div>
          )}

          {error && <div className="rounded bg-bear/20 px-3 py-2 text-bear">{error}</div>}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-accent py-2.5 font-bold text-white hover:brightness-110 disabled:opacity-50"
          >
            {loading ? "Connecting..." : mode === "kotak" ? "Authenticate via TOTP + MPIN" : "Connect Paper Terminal"}
          </button>
        </form>
      </div>
    </div>
  );
}
