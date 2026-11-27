"""Research-only technical factors and close-time signals, independent of Net-Net.

Input contract: raw execution Bar, historical adjustment factor, amount in CNY,
and explicit point-in-time eligibility/listing evidence. Unknown eligibility
never produces a signal. No production database or scheduler is used.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, asdict
from datetime import date
from math import isfinite
import re
from typing import Iterable, Iterator
from .backtest_engine import Bar

TREND = "ma_trend_research_v0"
REVERSAL = "oversold_reversion_v0"
MAINBOARD = re.compile(r"(?:(?:600|601|603|605)\d{3}\.SH|(?:000|001|002|003)\d{3}\.SZ)\Z")

@dataclass(frozen=True)
class Observation:
    bar: Bar
    factor: float | None
    amount_yuan: float | None
    eligible: bool | None
    listing_sessions: int | None
    # Consecutive market-session index from an independently verified calendar.
    session: int

@dataclass(frozen=True)
class TechnicalConfig:
    ma_fast: int = 20
    ma_slow: int = 50
    slope_days: int = 5
    pullback_days: int = 5
    pullback_tolerance: float = .02
    oversold_days: int = 10
    oversold_return: float = -.10
    rsi_limit: float = 30.
    bias_limit: float = -.08
    volume_ratio: float = 1.
    min_amount_yuan: float = 50_000_000.
    min_listing_sessions: int = 120

    def __post_init__(self):
        for n, lo, hi in ((self.ma_fast,5,50),(self.ma_slow,20,200),
                          (self.slope_days,1,30),(self.pullback_days,1,30),
                          (self.oversold_days,5,30),(self.min_listing_sessions,1,10000)):
            if type(n) is not int or not lo <= n <= hi:
                raise ValueError("Invalid integer strategy parameter")
        if self.ma_fast >= self.ma_slow:
            raise ValueError("Fast MA must be shorter than slow MA")
        for x,lo,hi in ((self.pullback_tolerance,0,.2),(self.oversold_return,-.9,-.01),
                        (self.rsi_limit,1,99),(self.bias_limit,-.9,-.01),
                        (self.volume_ratio,.1,10),(self.min_amount_yuan,0,1e12)):
            if not isfinite(x) or not lo <= x <= hi:
                raise ValueError("Invalid numeric strategy parameter")

@dataclass(frozen=True)
class Factors:
    day: date
    close: float
    high: float
    low: float
    volume: float
    ma_fast: float | None
    ma_slow: float | None
    ma20: float | None
    rsi14: float | None
    atr14_pct: float | None
    return_n: float | None
    bias20: float | None
    amount20: float | None
    volume5: float | None

@dataclass(frozen=True)
class Signal:
    strategy_id: str
    ts_code: str
    day: date
    score: float
    factors: Factors
    prior_return: float | None = None
    prior_rsi: float | None = None
    prior_bias: float | None = None

def _mean(values, n):
    return sum(list(values)[-n:])/n if len(values)>=n else None

class FactorState:
    """Bounded rolling state per security. Scaled OHLC = raw OHLC * factor.
    No end-date normalization; signals use only scale-invariant comparisons.
    A missing/invalid observation resets state instead of fabricating prices.
    """
    def __init__(self, cfg: TechnicalConfig):
        self.cfg=cfg
        self.closes=deque(maxlen=max(cfg.ma_slow,cfg.oversold_days+1,20))
        self.amounts=deque(maxlen=20)
        self.volumes=deque(maxlen=5)
        self.gain=self.loss=self.atr=0.
        self.changes=0
        self.prev=None

    def push(self, obs: Observation) -> Factors | None:
        b=obs.bar
        if (b.suspended or b.volume<=0 or obs.factor is None or
                not isfinite(obs.factor) or obs.factor<=0 or obs.amount_yuan is None or
                not isfinite(obs.amount_yuan) or obs.amount_yuan<0):
            return None
        c,h,l=b.close*obs.factor,b.high*obs.factor,b.low*obs.factor
        if not all(isfinite(v) and v>0 for v in (c,h,l)):
            return None
        if self.prev is not None:
            change=c-self.prev
            gain,loss=max(change,0.),max(-change,0.)
            tr=max(h-l,abs(h-self.prev),abs(l-self.prev))
            self.changes+=1
            if self.changes<=14:
                self.gain+=gain/14
                self.loss+=loss/14
                self.atr+=tr/14
            else:
                self.gain=(13*self.gain+gain)/14
                self.loss=(13*self.loss+loss)/14
                self.atr=(13*self.atr+tr)/14
        self.prev=c
        self.closes.append(c);self.amounts.append(obs.amount_yuan);self.volumes.append(b.volume)
        fast=_mean(self.closes,self.cfg.ma_fast)
        slow=_mean(self.closes,self.cfg.ma_slow)
        ma20=_mean(self.closes,20)
        rsi=None
        if self.changes>=14:
            rsi=(50. if self.gain==0 else 100.) if self.loss==0 else 100-100/(1+self.gain/self.loss)
        ret=c/self.closes[-self.cfg.oversold_days-1]-1 if len(self.closes)>self.cfg.oversold_days else None
        return Factors(b.day,c,h,l,b.volume,fast,slow,ma20,rsi,
                       self.atr/c if self.changes>=14 else None,ret,
                       c/ma20-1 if ma20 else None,_mean(self.amounts,20),_mean(self.volumes,5))

def generate_signals(observations: Iterable[Observation],
                     cfg: TechnicalConfig = TechnicalConfig()) -> Iterator[Signal]:
    """Consume strictly ordered (code, session) observations, one stock at a time.
    A calendar gap resets technical state conservatively; it is not a zero return.
    Only current and earlier observations are used. Unknown ST/listing eligibility
    blocks entry but does not retroactively alter valid historical price factors.
    """
    code=None
    last_session=-1
    last_day=None
    state=FactorState(cfg)
    history=deque(maxlen=max(cfg.slope_days,cfg.pullback_days,1))
    for obs in observations:
        b=obs.bar
        if type(obs.session) is not int or obs.session<0:
            raise ValueError("Invalid market-session index")
        if code is not None and b.ts_code<code:
            raise ValueError("Observations must be ordered by code")
        if b.ts_code!=code:
            code=b.ts_code;last_session=-1;last_day=None
            state=FactorState(cfg);history.clear()
        if last_day is not None and (b.day<=last_day or obs.session<=last_session):
            raise ValueError("Duplicate or unordered stock observation")
        if last_session>=0 and obs.session!=last_session+1:
            state=FactorState(cfg);history.clear()
        last_session=obs.session;last_day=b.day
        point=state.push(obs)
        if point is None:
            state=FactorState(cfg);history.clear();continue
        if (MAINBOARD.fullmatch(code) and obs.eligible is True and type(obs.listing_sessions) is int and
                obs.listing_sessions>=cfg.min_listing_sessions and
                point.amount20 is not None and point.amount20>=cfg.min_amount_yuan and history):
            prior=history[-1]
            rebound=point.close>prior.high
            if (rebound and point.ma_fast is not None and point.ma_slow is not None and
                    point.ma_fast>point.ma_slow and point.close>point.ma_fast and
                    len(history)>=cfg.slope_days):
                older=history[-cfg.slope_days]
                touched=any(p.ma_fast is not None and
                            p.low<=p.ma_fast*(1+cfg.pullback_tolerance) and
                            p.high>=p.ma_fast*(1-cfg.pullback_tolerance)
                            for p in list(history)[-cfg.pullback_days:])
                if older.ma_fast is not None and point.ma_fast>older.ma_fast and touched:
                    yield Signal(TREND,code,b.day,point.ma_fast/point.ma_slow-1,point)
            if (rebound and prior.return_n is not None and prior.return_n<=cfg.oversold_return and
                    prior.rsi14 is not None and prior.rsi14<cfg.rsi_limit and
                    prior.bias20 is not None and prior.bias20<cfg.bias_limit and
                    prior.volume5 is not None and point.volume>prior.volume5*cfg.volume_ratio):
                yield Signal(REVERSAL,code,b.day,-prior.return_n,point,
                             prior.return_n,prior.rsi14,prior.bias20)
        history.append(point)

def ranked_close_signals(signals: Iterable[Signal], strategy_id: str) -> dict[date,list[str]]:
    if strategy_id not in (TREND,REVERSAL):
        raise ValueError("Unknown technical strategy")
    by_day={}
    for s in signals:
        if s.strategy_id==strategy_id:
            by_day.setdefault(s.day,[]).append(s)
    return {d:[s.ts_code for s in sorted(rows,key=lambda s:(-s.score,s.ts_code))]
            for d,rows in sorted(by_day.items())}

def signal_record(s: Signal) -> dict:
    out=asdict(s)
    out["day"]=s.day.isoformat();out["factors"]["day"]=s.day.isoformat()
    return out
