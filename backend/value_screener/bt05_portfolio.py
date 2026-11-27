"""BT05 bounded-memory, close-signal/next-open research portfolio.

Explicit fee assumptions and corporate-action safety gate. A factor change
during a holding is not a cash/share accounting event: fail rather than
silently treating a split/dividend as an investment loss.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date
from math import floor,isfinite,isclose,sqrt
from .bt05_actions import Actions
from .backtest_engine import Bar, BacktestConfig, Position, Trade, EquityPoint, _tradable

@dataclass(frozen=True)
class FeePeriod:
    start: date
    commission_rate: float
    min_commission: float
    sell_tax_rate: float
    slippage_rate: float
    transfer_rate: float=0.

    def __post_init__(self):
        if any(not isfinite(x) or not 0<=x<=.1 for x in
               (self.commission_rate,self.sell_tax_rate,self.slippage_rate,self.transfer_rate)):
            raise ValueError("Invalid dated fee assumption")
        if not isfinite(self.min_commission) or not 0<=self.min_commission<=1000:
            raise ValueError("Invalid minimum commission")

    def fee(self,notional,side):
        return max(self.min_commission,notional*self.commission_rate)+notional*(
            self.transfer_rate+(self.sell_tax_rate if side=="sell" else 0))

class FeeSchedule:
    def __init__(self, periods):
        self.periods=tuple(periods)
        if not self.periods or list(self.periods)!=sorted(self.periods,key=lambda p:p.start) or len({p.start for p in self.periods})!=len(self.periods):
            raise ValueError("Fee periods must be strictly increasing")

    @classmethod
    def fixed(cls,cfg):
        return cls([FeePeriod(date.min,cfg.commission_rate,cfg.min_commission,cfg.sell_tax_rate,cfg.slippage_rate)])

    def at(self,day):
        for p in reversed(self.periods):
            if p.start<=day:return p
        raise ValueError("Fee assumptions do not cover execution day")

    def record(self):
        return [{**asdict(p),"start":p.start.isoformat()} for p in self.periods]

class Portfolio:
    """Only live positions, pending exits, prior candidates and online statistics
    are retained. Daily results are emitted to the caller, not accumulated here.
    """
    def __init__(self,cfg:BacktestConfig,fees:FeeSchedule,actions:Actions|None=None):
        self.cfg=cfg;self.fees=fees;self.actions=actions or Actions()
        self.receivables=[];self.dividend_credit={};self.dividends_recognized=0.;self.dividends_paid=0.;self.action_count=0
        self.cash=cfg.initial_cash;self.holdings={};self.marks={};self.factors={}
        self.pending=set();self.previous=None;self.candidates=[];self.index=-1
        self.peak=cfg.initial_cash;self.worst=0.;self.last_equity=cfg.initial_cash
        self.closed=0;self.wins=0;self.profit=0.;self.loss=0.;self.stale=0
        self.total_fees=0.;self.turnover=0.;self.ret_sum=0.;self.ret_sq=0.
        self.blocked_buys=0;self.blocked_sells=0

    def step(self,day:date,bars:dict[str,Bar],eligible:set[str],factors:dict[str,float|None],candidates:list[str]):
        if self.previous is not None and day<=self.previous:
            raise ValueError("Portfolio sessions must be strictly increasing")
        if any(b.day!=day or b.ts_code!=code for code,b in bars.items()):
            raise ValueError("Invalid daily bar keys")
        self.index+=1;idx=self.index;cfg=self.cfg;fees=self.fees.at(day)
        fills=[];trades=[];action_records=[]
        if self.actions.blocked.get(day,set()) & self.holdings.keys():
            raise ValueError("Unsupported corporate action while holding; reviewed settlement required")
        events=self.actions.by_ex.get(day,{})
        # Validate every affected holding before changing any cash or shares.
        for code,pos in self.holdings.items():
            f=factors.get(code)
            event=events.get(code)
            if event:
                shares=pos.size*event.share_multiplier
                if (self.previous!=event.record or code not in bars or f is None
                        or not isfinite(f) or not isclose(f,event.factor_after,rel_tol=1e-9)
                        or not isclose(self.factors[code],event.factor_before,rel_tol=1e-9)
                        or not isclose(shares,round(shares),rel_tol=0,abs_tol=1e-7)):
                    raise ValueError(f"Unreconciled corporate action or fractional shares {code} {day}")
            elif code in bars and (f is None or not isfinite(f) or f<=0 or not isclose(f,self.factors[code],rel_tol=1e-9,abs_tol=0)):
                raise ValueError(f"Corporate action or factor gap while holding {code} {day}; verified cash/share evidence required")
        for code,pos in self.holdings.items():
            event=events.get(code)
            if event is None:continue
            old=pos.size;new=round(old*event.share_multiplier)
            dividend=old*event.cash_per_old_share_net
            pos.entry_price*=old/new
            pos.size=new
            self.factors[code]=event.factor_after
            self.dividend_credit[code]=self.dividend_credit.get(code,0.)+dividend
            self.dividends_recognized+=dividend;self.action_count+=1
            if dividend:
                self.receivables.append({"code":code,"pay":event.pay.isoformat(),"amount":dividend,"ex":day.isoformat()})
            action_records.append({"type":"ENTITLEMENT","code":code,"day":day.isoformat(),
                "old_shares":old,"new_shares":new,"net_cash_receivable":dividend,"source_ref":event.source_ref})
        for code,pos in list(self.holdings.items()):
            if idx-pos.entry_index<cfg.holding_sessions and code not in self.pending:continue
            b=bars.get(code)
            if not _tradable(b,cfg,"sell"):
                self.blocked_sells+=1;continue
            price=b.open*(1-fees.slippage_rate)
            # Slippage must not imply an execution below the exchange limit.
            if b.down_limit is not None:price=max(price,b.down_limit)
            notional=price*pos.size;fee=fees.fee(notional,"sell")
            self.cash+=notional-fee
            dividend=self.dividend_credit.pop(code,0.)
            pnl=notional-fee-pos.entry_price*pos.size-pos.buy_commission+dividend
            t=Trade(code,pos.entry_day.isoformat(),day.isoformat(),pos.size,pos.entry_price,price,pnl,pos.buy_commission+fee,idx-pos.entry_index)
            record=asdict(t)
            if dividend:record['net_dividend_entitlement']=dividend
            trades.append(record);self.closed+=1;self.wins+=pnl>0
            self.profit+=max(pnl,0);self.loss+=max(-pnl,0)
            fills.append(dict(day=day.isoformat(),side="SELL",ts_code=code,shares=pos.size,price=price,fees=fee))
            self.total_fees+=fee;self.turnover+=notional
            del self.holdings[code];self.marks.pop(code);self.factors.pop(code);self.pending.discard(code)
        if self.previous is not None:
            for code in self.candidates:
                if code in self.holdings or len(self.holdings)>=cfg.max_positions:continue
                b=bars.get(code);f=factors.get(code)
                if code in self.actions.blocked.get(day,set()) or code not in eligible or not _tradable(b,cfg,"buy") or f is None or not isfinite(f) or f<=0:
                    self.blocked_buys+=1;continue
                price=b.open*(1+fees.slippage_rate)
                if b.up_limit is not None:price=min(price,b.up_limit)
                equity=self.cash+sum(x['amount'] for x in self.receivables)+sum(p.size*(bars[k].open if k in bars else self.marks[k]) for k,p in self.holdings.items())
                budget=min(self.cash,equity/cfg.max_positions)
                size=max(0,int(floor((budget-fees.min_commission)/price/cfg.lot_size))*cfg.lot_size)
                while size>0:
                    notional=size*price;fee=fees.fee(notional,"buy")
                    if notional+fee<=budget+1e-8 and notional+fee<=self.cash+1e-8:break
                    size-=cfg.lot_size
                if size<=0:continue
                self.cash-=notional+fee
                self.holdings[code]=Position(code,size,day,idx,price,fee)
                self.marks[code]=price;self.factors[code]=f
                self.total_fees+=fee;self.turnover+=notional
                fills.append(dict(day=day.isoformat(),side="BUY",ts_code=code,signal_day=self.previous.isoformat(),shares=size,price=price,fees=fee))
        # Conservative settlement: credit cash at close of first session on or
        # after payment date, available for new orders only next session.
        remaining=[]
        for item in self.receivables:
            if item["pay"]<=day.isoformat():
                self.cash+=item["amount"];self.dividends_paid+=item["amount"]
                action_records.append({"type":"PAYMENT","day":day.isoformat(),**item})
            else:remaining.append(item)
        self.receivables=remaining
        equity=self.cash+sum(x['amount'] for x in self.receivables)
        for code,pos in self.holdings.items():
            b=bars.get(code)
            if b is not None:
                self.marks[code]=b.close
                if cfg.stop_loss_rate is not None and not b.suspended and b.volume>0 and b.close*pos.size+self.dividend_credit.get(code,0.)<=pos.entry_price*pos.size*(1-cfg.stop_loss_rate):
                    self.pending.add(code)
            else:self.stale+=1
            equity+=pos.size*self.marks[code]
        equity=round(equity,6)
        ret=equity/self.last_equity-1 if self.last_equity>0 else 0.
        self.ret_sum+=ret;self.ret_sq+=ret*ret
        self.peak=max(self.peak,equity);self.worst=min(self.worst,equity/self.peak-1)
        self.last_equity=equity;self.previous=day;self.candidates=list(dict.fromkeys(candidates))
        return {"equity":asdict(EquityPoint(day.isoformat(),equity,round(self.cash,6),len(self.holdings))),
                "fills":fills,"trades":trades,"corporate_actions":action_records,"dividend_receivable":sum(x["amount"] for x in self.receivables)}

    def checkpoint(self):
        return {"cash":self.cash,"receivables":self.receivables,"dividend_credit":self.dividend_credit,
                "dividends_recognized":self.dividends_recognized,"dividends_paid":self.dividends_paid,"action_count":self.action_count,"holdings":{k:{**asdict(v),"entry_day":v.entry_day.isoformat()} for k,v in self.holdings.items()},
                "marks":self.marks,"factors":self.factors,"pending":sorted(self.pending),
                "previous":self.previous.isoformat() if self.previous else None,"candidates":self.candidates,
                **{k:getattr(self,k) for k in ("index","peak","worst","last_equity","closed","wins","profit","loss","stale",
                   "total_fees","turnover","ret_sum","ret_sq","blocked_buys","blocked_sells")}}

    @classmethod
    def restore(cls,cfg,fees,state,actions=None):
        obj=cls(cfg,fees,actions)
        obj.receivables=list(state['receivables']);obj.dividend_credit=dict(state['dividend_credit'])
        for k in ('dividends_recognized','dividends_paid','action_count'):setattr(obj,k,state[k])
        for k in ("cash","index","peak","worst","last_equity","closed","wins","profit","loss","stale",
                  "total_fees","turnover","ret_sum","ret_sq","blocked_buys","blocked_sells"):
            setattr(obj,k,state[k])
        obj.holdings={k:Position(**{**v,"entry_day":date.fromisoformat(v["entry_day"])}) for k,v in state["holdings"].items()}
        obj.marks=dict(state["marks"]);obj.factors=dict(state["factors"]);obj.pending=set(state["pending"])
        obj.previous=date.fromisoformat(state["previous"]) if state["previous"] else None
        obj.candidates=list(state["candidates"])
        if set(obj.holdings)!=set(obj.marks) or set(obj.holdings)!=set(obj.factors) or not obj.pending<=set(obj.holdings) or not set(obj.dividend_credit)<=set(obj.holdings):
            raise ValueError("Invalid portfolio checkpoint")
        return obj

    def metrics(self):
        n=self.index+1;r=self.last_equity/self.cfg.initial_cash
        variance=max(0.,(self.ret_sq-self.ret_sum*self.ret_sum/n)/(n-1)) if n>1 else 0.
        return {"total_return":r-1,"annualized_return":r**(252/n)-1 if n and r>0 else None,
                "max_drawdown":self.worst,"closed_trades":self.closed,
                "win_rate":self.wins/self.closed if self.closed else None,
                "profit_factor":self.profit/self.loss if self.loss else None,
                "sharpe_zero_rf":sqrt(252)*self.ret_sum/n/sqrt(variance) if n>1 and variance>0 else None,
                "completed_sessions":n,"fees_paid":self.total_fees,"traded_notional":self.turnover,
                "open_positions":len(self.holdings),"stale_mark_count":self.stale,
                "blocked_buys":self.blocked_buys,"blocked_sell_attempts":self.blocked_sells,
                "net_dividends_recognized":self.dividends_recognized,"net_dividends_paid":self.dividends_paid,
                "dividend_receivable":sum(x["amount"] for x in self.receivables),"corporate_actions":self.action_count,
                "validation":"ENGINEERING_ONLY_NOT_A_VERIFIED_HISTORICAL_RETURN"}
