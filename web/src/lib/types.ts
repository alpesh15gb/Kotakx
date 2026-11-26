export interface Instrument {
  instrument_token: string;
  exchange_segment: string;
  trading_symbol: string;
  symbol: string;
  name: string;
  instrument_type: "INDEX" | "EQ" | "FUT" | "CE" | "PE";
  expiry?: string;
  strike_price?: number;
  option_type?: "CE" | "PE";
  lot_size: number;
  tick_size: number;
  underlying?: string;
}

export interface DepthLevel {
  price: number;
  qty: number;
  orders: number;
}

export interface QuoteData {
  instrument_token: string;
  exchange_segment: string;
  trading_symbol: string;
  ltp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  change: number;
  change_pct: number;
  volume: number;
  oi: number;
  bid: number;
  ask: number;
  bid_qty: number;
  ask_qty: number;
  depth_bids: DepthLevel[];
  depth_asks: DepthLevel[];
  updated_at: number;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface CandlesResponse {
  ok: boolean;
  candles: Candle[];
  indicators: Record<string, any[]>;
  synthetic: boolean;
}

export interface IndicatorPoint {
  time: number;
  value: number;
}

export interface OptionChainStrike {
  strike_price: number;
  is_atm: boolean;
  call_token?: string;
  call_symbol?: string;
  call_ltp: number;
  call_change_pct: number;
  call_oi: number;
  call_oi_change: number;
  call_volume: number;
  call_iv: number;
  call_delta: number;
  call_gamma: number;
  call_theta: number;
  call_vega: number;
  put_token?: string;
  put_symbol?: string;
  put_ltp: number;
  put_change_pct: number;
  put_oi: number;
  put_oi_change: number;
  put_volume: number;
  put_iv: number;
  put_delta: number;
  put_gamma: number;
  put_theta: number;
  put_vega: number;
}

export interface OptionChainResponse {
  underlying: string;
  spot_price: number;
  expiry: string;
  available_expiries: string[];
  pcr: number;
  max_call_oi_strike: number;
  max_put_oi_strike: number;
  expected_move_pct: number;
  strikes: OptionChainStrike[];
}

export interface OrderRecord {
  order_id: string;
  trading_symbol: string;
  exchange_segment: string;
  transaction_type: "B" | "S";
  product: string;
  order_type: string;
  quantity: number;
  filled_quantity: number;
  pending_quantity: number;
  price: number;
  trigger_price: number;
  avg_price: number;
  status: string;
  rejection_reason?: string;
  tag?: string;
  order_time: string;
}

export interface PositionRecord {
  instrument_token: string;
  trading_symbol: string;
  exchange_segment: string;
  product: OrderDefaults["default_product"];
  net_qty: number;
  buy_qty: number;
  sell_qty: number;
  buy_avg_price: number;
  sell_avg_price: number;
  avg_price: number;
  ltp: number;
  realized_pnl: number;
  unrealized_pnl: number;
  pnl: number;
  lot_size: number;
}

export interface HoldingRecord {
  instrument_token: string;
  exchange_segment: string;
  trading_symbol: string;
  symbol: string;
  quantity: number;
  sellable_quantity: number;
  average_price: number;
  closing_price: number;
  ltp: number;
  mkt_value: number;
  holding_cost: number;
  pnl: number;
  pnl_pct: number;
}

export interface OrderDefaults {
  default_lots: number;
  default_product: "MIS" | "NRML" | "CNC" | "MTF";
  default_order_type: "MKT" | "L" | "SL" | "SL-M";
  one_tap_enabled: boolean;
}

export interface PnlProtectConfig {
  enabled: boolean;
  max_loss: number;
  max_profit: number;
  trailing_sl_enabled: boolean;
  trailing_sl_step: number;
  lock_profit_at: number;
  auto_activate_kill_switch: boolean;
  peak_pnl: number;
  triggered: boolean;
  triggered_at: string | null;
}

export interface KillSwitchState {
  active: boolean;
  activated_at?: string;
  reason?: string;
}

export interface AlertRecord {
  id: string;
  instrument_token: string;
  trading_symbol: string;
  condition: string;
  target_price: number;
  note: string;
  status: string;
  created_at: string;
}
