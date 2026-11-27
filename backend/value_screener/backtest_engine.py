"""Deterministic event-driven equity backtest kernel.

This kernel is intentionally strategy-independent: signals are observed at the
close of day T; all fills are attempted at T+1 or later using the next open.
No retrospective use of today's open, no short-selling, T+1 selling, and
conservative limit-up/down and suspension checks.

Do NOT publish engineering runs as validated strategy performance until the
historical universe, point-in-time financials, adjustment factors, fees,
corporate actions and tradeability are verified.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from math import floor, isfinite
from typing import Iterable, Mapping


@dataclass(frozen=True)
class Bar:
    ts_code: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    up_limit: float | None = None
    down_limit: float | None = None
    suspended: bool = False

    def __post_init__(self):
        prices = (self.open, self.high, self.low, self.close)
        if any(not isfinite(x) or x <= 0 for x in prices):
            raise ValueError("Prices must be positive and finite")
        if (self.high < max(self.open,self.close) - 1e-8 or
                self.low > min(self.open,self.close) + 1e-8):
            raise ValueError("OHLC bar is internally inconsistent")
        if not isfinite(self.volume) or self.volume < 0:
            raise ValueError("Volume cannot be negative")


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    holding_sessions: int = 10
    max_positions: int = 5
    lot_size: int = 100
    commission_rate: float = 0.0003  # user-configurable example assumption
    min_commission: float = 5.0
    sell_tax_rate: float = 0.0005  # explicit assumption, not a live tax quote
    slippage_rate: float = 0.0005
    strict_tradeability: bool = True
    stop_loss_rate: float | None = None

    def __post_init__(self):
        if not isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not 1 <= self.holding_sessions <= 260:
            raise ValueError("holding_sessions must be 1..260")
        if not 1 <= self.max_positions <= 100 or self.lot_size < 1:
            raise ValueError("Invalid position or lot limit")
        if self.stop_loss_rate is not None and (not isfinite(self.stop_loss_rate) or not 0 < self.stop_loss_rate < 1):
            raise ValueError("Invalid stop loss rate")
        rates = (self.commission_rate,self.sell_tax_rate,self.slippage_rate)
        if any(not isfinite(x) or x < 0 or x > 0.1 for x in rates):
            raise ValueError("Invalid fee rate assumption")
        if not isfinite(self.min_commission) or not 0 <= self.min_commission <= 1000:
            raise ValueError("Invalid minimum commission")


@dataclass
class Position:
    ts_code: str
    size: int
    entry_day: date
    entry_index: int
    entry_price: float
    buy_commission: float


@dataclass(frozen=True)
class Trade:
    ts_code: str
    entry_day: str
    exit_day: str
    shares: int
    entry_price: float
    exit_price: float
    net_pnl: float
    fees: float
    held_sessions: int


@dataclass(frozen=True)
class EquityPoint:
    day: str
    equity: float
    cash: float
    positions: int


def _commission(notional: float, cfg: BacktestConfig) -> float:
    return max(cfg.min_commission, cfg.commission_rate*notional)


def _tradable(bar: Bar | None, cfg: BacktestConfig, side: str) -> bool:
    if bar is None or bar.suspended or bar.volume <= 0:
        return False
    if cfg.strict_tradeability and (bar.up_limit is None or bar.down_limit is None):
        return False
    if side == "buy" and bar.up_limit is not None and bar.open >= bar.up_limit-1e-7:
        return False
    if side == "sell" and bar.down_limit is not None and bar.open <= bar.down_limit+1e-7:
        return False
    return True


def _performance(curve: list[EquityPoint], trades: list[Trade],
                 cfg: BacktestConfig) -> dict:
    peak=cfg.initial_cash
    worst=0.0
    for point in curve:
        peak=max(peak,point.equity)
        worst=min(worst,point.equity/peak-1)
    wins=sum(t.net_pnl>0 for t in trades)
    gross_profit=sum(max(t.net_pnl,0.0) for t in trades)
    gross_loss=-sum(min(t.net_pnl,0.0) for t in trades)
    return {
        "total_return":curve[-1].equity/cfg.initial_cash-1 if curve else 0.0,
        "max_drawdown":worst,
        "closed_trades":len(trades),
        "win_rate":wins/len(trades) if trades else None,
        "profit_factor":gross_profit/gross_loss if gross_loss else None,
        "completed_sessions":len(curve),
        "fee_assumptions": {
            "commission_rate":cfg.commission_rate,"min_commission":cfg.min_commission,
            "sell_tax_rate":cfg.sell_tax_rate,"slippage_rate":cfg.slippage_rate,
            "stop_loss_rate":cfg.stop_loss_rate,
        },
        "validation":"ENGINEERING_ONLY_NOT_A_VERIFIED_HISTORICAL_RETURN",
    }


def run_backtest(bars: Iterable[Bar], close_signals: Mapping[date, list[str]],
                 cfg: BacktestConfig = BacktestConfig(),
                 *, entry_eligible: Mapping[date, set[str]] | None = None) -> dict:
    """Run a long-only cash portfolio. Signal date is CLOSE; fill at NEXT OPEN.

    A blocked sell is retried at subsequent opens, including after hold expiry.
    On absent daily bars positions are marked at the last known close; the
    result records that stale-close count instead of pretending new prices.
    Open positions at period end are marked to market, not liquidated.
    """
    by_day: dict[date,dict[str,Bar]] = {}
    for bar in bars:
        bucket=by_day.setdefault(bar.day,{})
        if bar.ts_code in bucket:
            raise ValueError(f"Duplicate price bar {bar.ts_code} {bar.day}")
        bucket[bar.ts_code]=bar
    days=sorted(by_day)
    if len(days)<2:
        raise ValueError("At least two market sessions are needed")
    cash=cfg.initial_cash
    holdings:dict[str,Position]={}
    last_closes:dict[str,float]={}
    trades:list[Trade]=[]
    fills:list[dict]=[]
    curve:list[EquityPoint]=[]
    stale_marks=0
    stop_pending:set[str]=set()

    for idx,day in enumerate(days):
        today=by_day[day]
        # Existing positions exit at open after the configured HOLD duration.
        for code,position in list(holdings.items()):
            if idx-position.entry_index < cfg.holding_sessions and code not in stop_pending:
                continue
            bar=today.get(code)
            if not _tradable(bar,cfg,"sell"):
                continue
            assert bar is not None
            sell_price=bar.open*(1-cfg.slippage_rate)
            notional=sell_price*position.size
            sell_fee=_commission(notional,cfg)+notional*cfg.sell_tax_rate
            cash+=notional-sell_fee
            entry_cost=position.entry_price*position.size+position.buy_commission
            net_pnl=notional-sell_fee-entry_cost
            trades.append(Trade(code,position.entry_day.isoformat(),day.isoformat(),
                                position.size,position.entry_price,sell_price,
                                net_pnl,position.buy_commission+sell_fee,
                                idx-position.entry_index))
            fills.append({"day":day.isoformat(),"side":"SELL","ts_code":code,
                          "shares":position.size,"price":sell_price,"fees":sell_fee})
            del holdings[code]
            stop_pending.discard(code)

        # The ONLY allowed buy signals come from the previous day's CLOSE.
        if idx>0:
            prev_day=days[idx-1]
            candidates=list(dict.fromkeys(close_signals.get(prev_day,[])))
            for code in candidates:
                if code in holdings or len(holdings)>=cfg.max_positions:
                    continue
                if entry_eligible is not None and code not in entry_eligible.get(day,set()):
                    continue
                bar=today.get(code)
                if not _tradable(bar,cfg,"buy"):
                    continue
                assert bar is not None
                buy_price=bar.open*(1+cfg.slippage_rate)
                existing_value=sum(
                    pos.size*(today[pos.ts_code].open if pos.ts_code in today
                              else last_closes.get(pos.ts_code,pos.entry_price))
                    for pos in holdings.values()
                )
                equity=cash+existing_value
                slot_budget=min(cash,equity/cfg.max_positions)
                size=int(floor((slot_budget-cfg.min_commission)/buy_price/cfg.lot_size))*cfg.lot_size
                while size>0:
                    notional=size*buy_price
                    fee=_commission(notional,cfg)
                    if notional+fee<=cash+1e-8 and notional+fee<=slot_budget+1e-8:
                        break
                    size-=cfg.lot_size
                if size<=0:
                    continue
                cash-=size*buy_price+fee
                holdings[code]=Position(code,size,day,idx,buy_price,fee)
                fills.append({"day":day.isoformat(),"side":"BUY","ts_code":code,
                              "signal_day":prev_day.isoformat(),
                              "shares":size,"price":buy_price,"fees":fee})
        # Mark values at the CLOSE, without borrowing or hidden liquidation.
        equity=cash
        for code,position in holdings.items():
            if code in today:
                mark=today[code].close
            else:
                stale_marks+=1
                mark=last_closes.get(code,position.entry_price)
            equity+=position.size*mark
        curve.append(EquityPoint(day.isoformat(),round(equity,6),
                                 round(cash,6),len(holdings)))
        for code,bar in today.items():
            last_closes[code]=bar.close
            if (cfg.stop_loss_rate is not None and code in holdings and not bar.suspended
                    and bar.volume>0 and bar.close <= holdings[code].entry_price*(1-cfg.stop_loss_rate)):
                # Close-time risk signal: attempt at next open, retry if blocked.
                stop_pending.add(code)

    return {
        "metrics":_performance(curve,trades,cfg),
        "equity":[asdict(row) for row in curve],
        "trades":[asdict(row) for row in trades],
        "fills":fills,
        "open_positions":len(holdings),
        "stale_mark_count":stale_marks,
        "assumptions":"Unadjusted prices and synthetic commission assumptions unless the source is verified. Not a production investment backtest.",
    }


def generate_ma_trend_signals(bars: Iterable[Bar]) -> dict[date,list[str]]:
    """MA20/50 technical *engineering* strategy, uses close-or-earlier data only."""
    by_code:dict[str,list[Bar]]={}
    for bar in bars:
        by_code.setdefault(bar.ts_code,[]).append(bar)
    result:dict[date,list[str]]={}
    for code,sequence in by_code.items():
        ordered=sorted(sequence,key=lambda b:b.day)
        prices=[]
        previously_true=False
        for bar in ordered:
            prices.append(bar.close)
            if len(prices)<51:
                continue
            ma20=sum(prices[-20:])/20
            previous20=sum(prices[-21:-1])/20
            ma50=sum(prices[-50:])/50
            condition=bar.close>ma20>=ma50 and ma20>=previous20
            if condition and not previously_true:
                result.setdefault(bar.day,[]).append(code)
            previously_true=condition
    return result
