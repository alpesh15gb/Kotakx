from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.models import Instrument, OptionChainResponse, OptionChainStrike, QuoteData


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def parse_expiry(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.upper(), "%d%b%Y").date()
    except (TypeError, ValueError):
        return None


def time_to_expiry_days(value: str) -> float:
    expiry_date = parse_expiry(value)
    if expiry_date is None:
        return 0.0
    ist = timezone(timedelta(hours=5, minutes=30))
    expiry_at = datetime.combine(expiry_date, datetime.min.time(), tzinfo=ist) + timedelta(hours=15, minutes=30)
    return max(0.0, (expiry_at - datetime.now(ist)).total_seconds() / 86400.0)


def black_scholes_price_and_greeks(
    spot: float,
    strike: float,
    tte_years: float,
    rate: float,
    sigma: float,
    option_type: str,
) -> dict[str, float]:
    """Compute option theoretical price and Greeks (Delta, Gamma, Theta/day, Vega/1% IV)."""
    if spot <= 0 or strike <= 0 or tte_years <= 0 or sigma <= 0:
        intrinsic = max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)
        delta = (1.0 if spot > strike else 0.0) if option_type == "CE" else (-1.0 if spot < strike else 0.0)
        return {"price": intrinsic, "delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    sqrt_t = math.sqrt(tte_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * tte_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf_d1 = _norm_pdf(d1)
    disc = math.exp(-rate * tte_years)

    if option_type == "CE":
        price = spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta = (-(spot * pdf_d1 * sigma) / (2.0 * sqrt_t) - rate * strike * disc * _norm_cdf(d2)) / 365.0
    else:
        price = strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta = (-(spot * pdf_d1 * sigma) / (2.0 * sqrt_t) + rate * strike * disc * _norm_cdf(-d2)) / 365.0

    gamma = pdf_d1 / (spot * sigma * sqrt_t)
    vega = (spot * pdf_d1 * sqrt_t) / 100.0
    return {
        "price": round(max(0.05, price), 2),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 2),
        "vega": round(vega, 2),
    }


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    tte_years: float,
    rate: float,
    option_type: str,
) -> float:
    """Newton-Raphson + bisection IV solver returning annualized sigma (e.g., 0.145 = 14.5%)."""
    if market_price <= 0 or spot <= 0 or strike <= 0 or tte_years <= 0:
        return 0.14
    intrinsic = max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)
    if market_price <= intrinsic + 0.01:
        return 0.12

    low, high = 0.02, 2.50
    sigma = 0.16
    for _ in range(28):
        res = black_scholes_price_and_greeks(spot, strike, tte_years, rate, sigma, option_type)
        diff = res["price"] - market_price
        if abs(diff) < 0.02:
            return round(sigma, 4)
        if diff > 0:
            high = sigma
        else:
            low = sigma
        vega_raw = res["vega"] * 100.0
        if vega_raw > 1e-4:
            cand = sigma - diff / vega_raw
            if low < cand < high:
                sigma = cand
                continue
        sigma = 0.5 * (low + high)
    return round(sigma, 4)


def build_option_chain(
    underlying: str,
    spot_price: float,
    instruments: Iterable[Instrument],
    quotes_by_token: dict[str, QuoteData],
    requested_expiry: str | None = None,
    tte_days: float | None = None,
    risk_free_rate: float = 0.065,
) -> OptionChainResponse:
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    options = [
        inst
        for inst in instruments
        if inst.instrument_type in ("CE", "PE")
        and (inst.underlying or "").upper() == underlying.upper()
        and inst.strike_price is not None
        and inst.expiry
        and parse_expiry(inst.expiry) is not None
        and parse_expiry(inst.expiry) >= today
    ]
    expiries = sorted(
        {inst.expiry for inst in options if inst.expiry},
        key=lambda value: parse_expiry(value) or datetime.max.date(),
    )
    selected_expiry = requested_expiry if requested_expiry in expiries else (expiries[0] if expiries else "")
    filtered = [inst for inst in options if inst.expiry == selected_expiry]

    by_strike: dict[float, dict[str, Instrument]] = {}
    for inst in filtered:
        assert inst.strike_price is not None
        slot = by_strike.setdefault(inst.strike_price, {})
        if inst.option_type in ("CE", "PE"):
            slot[inst.option_type] = inst

    sorted_strikes = sorted(by_strike.keys())
    atm_strike = min(sorted_strikes, key=lambda k: abs(k - spot_price)) if sorted_strikes else round(spot_price / 50) * 50

    effective_tte_days = time_to_expiry_days(selected_expiry) if selected_expiry else max(tte_days or 0.0, 0.0)
    tte_years = effective_tte_days / 365.0
    total_call_oi = 0
    total_put_oi = 0
    max_call_oi = -1
    max_put_oi = -1
    max_call_strike = atm_strike
    max_put_strike = atm_strike
    atm_straddle = 0.0

    rows: list[OptionChainStrike] = []
    for strike in sorted_strikes:
        pair = by_strike[strike]
        ce = pair.get("CE")
        pe = pair.get("PE")

        ce_q = quotes_by_token.get(ce.instrument_token) if ce else None
        pe_q = quotes_by_token.get(pe.instrument_token) if pe else None

        ce_ltp = ce_q.ltp if ce_q else 0.0
        pe_ltp = pe_q.ltp if pe_q else 0.0

        ce_iv = implied_volatility(ce_ltp, spot_price, strike, tte_years, risk_free_rate, "CE") if ce_ltp > 0 else 0.0
        pe_iv = implied_volatility(pe_ltp, spot_price, strike, tte_years, risk_free_rate, "PE") if pe_ltp > 0 else 0.0
        ce_g = black_scholes_price_and_greeks(spot_price, strike, tte_years, risk_free_rate, ce_iv, "CE")
        pe_g = black_scholes_price_and_greeks(spot_price, strike, tte_years, risk_free_rate, pe_iv, "PE")

        ce_oi = ce_q.oi if ce_q else 0
        pe_oi = pe_q.oi if pe_q else 0
        total_call_oi += ce_oi
        total_put_oi += pe_oi
        if ce_oi > max_call_oi:
            max_call_oi = ce_oi
            max_call_strike = strike
        if pe_oi > max_put_oi:
            max_put_oi = pe_oi
            max_put_strike = strike
        if strike == atm_strike:
            atm_straddle = ce_ltp + pe_ltp

        rows.append(
            OptionChainStrike(
                strike_price=strike,
                is_atm=(strike == atm_strike),
                call_token=ce.instrument_token if ce else None,
                call_symbol=ce.trading_symbol if ce else None,
                call_ltp=round(ce_ltp, 2),
                call_change_pct=round(ce_q.change_pct, 2) if ce_q else 0.0,
                call_oi=ce_oi,
                call_oi_change=0,
                call_volume=ce_q.volume if ce_q else 0,
                call_iv=round(ce_iv * 100.0, 2),
                call_delta=ce_g["delta"],
                call_gamma=ce_g["gamma"],
                call_theta=ce_g["theta"],
                call_vega=ce_g["vega"],
                put_token=pe.instrument_token if pe else None,
                put_symbol=pe.trading_symbol if pe else None,
                put_ltp=round(pe_ltp, 2),
                put_change_pct=round(pe_q.change_pct, 2) if pe_q else 0.0,
                put_oi=pe_oi,
                put_oi_change=0,
                put_volume=pe_q.volume if pe_q else 0,
                put_iv=round(pe_iv * 100.0, 2),
                put_delta=pe_g["delta"],
                put_gamma=pe_g["gamma"],
                put_theta=pe_g["theta"],
                put_vega=pe_g["vega"],
            )
        )

    pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 1.0
    expected_move_pct = round((atm_straddle / spot_price) * 100.0, 2) if spot_price > 0 and atm_straddle > 0 else 0.0

    return OptionChainResponse(
        underlying=underlying.upper(),
        spot_price=round(spot_price, 2),
        expiry=selected_expiry,
        available_expiries=expiries,
        pcr=pcr,
        max_call_oi_strike=max_call_strike,
        max_put_oi_strike=max_put_strike,
        expected_move_pct=expected_move_pct,
        strikes=rows,
    )
