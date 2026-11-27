"""Explicit operator-reviewed corporate-action research evidence, not certification.

Supported: net cash dividend assumption and integer bonus/split shares, with
record date immediately before ex-date and bonus shares tradable on ex-date.
Cash is recognized as receivable on ex-date and becomes spendable on payment.
Unsupported rights, delayed share listing, fractional allocations, delisting
and missing events must block affected holdings; never infer cash from factors.
"""
from dataclasses import dataclass
from datetime import date
from math import isfinite
from .technical_strategies import MAINBOARD
from .technical_research import sha256
import json
from pathlib import Path

@dataclass(frozen=True)
class Action:
    code: str
    record: date
    ex: date
    pay: date
    cash_per_old_share_net: float
    share_multiplier: float
    factor_before: float
    factor_after: float
    source_ref: str

    def __post_init__(self):
        if not MAINBOARD.fullmatch(self.code) or not self.record<self.ex<=self.pay:
            raise ValueError("Invalid action identity/dates")
        if (not isinstance(self.source_ref,str) or not self.source_ref.strip()
                or any(not isfinite(x) for x in (self.cash_per_old_share_net,self.share_multiplier,self.factor_before,self.factor_after))
                or self.cash_per_old_share_net<0 or self.share_multiplier<1
                or min(self.factor_before,self.factor_after)<=0
                or (self.cash_per_old_share_net==0 and self.share_multiplier==1)):
            raise ValueError("Unsupported or invalid corporate action")

class Actions:
    def __init__(self,events=(),blocked=()):
        self.by_ex={};self.blocked={}
        for a in events:
            bucket=self.by_ex.setdefault(a.ex,{})
            if a.code in bucket:raise ValueError("Duplicate corporate action")
            bucket[a.code]=a
        for day,code in blocked:
            if not MAINBOARD.fullmatch(code):raise ValueError("Invalid blocked action code")
            if code in self.blocked.setdefault(day,set()) or code in self.by_ex.get(day,{}):
                raise ValueError("Duplicate/conflicting blocked action")
            self.blocked[day].add(code)

    @classmethod
    def load(cls,path,sessions,start,end):
        if path is None:return cls(),None
        path=Path(path);h=sha256(path)
        if path.is_symlink():raise ValueError("Symlink action evidence")
        doc=json.loads(path.read_text(encoding="utf-8"))
        if doc.get("status")!="OPERATOR_REVIEWED_RESEARCH_NOT_CERTIFIED" or not date.fromisoformat(doc["from"])<=start<=end<=date.fromisoformat(doc["through"]):
            raise ValueError("Corporate action evidence does not cover run")
        prev={d:p for p,d in zip(sessions,sessions[1:])}
        events=[];blocked=[]
        for row in doc["events"]:
            ex=date.fromisoformat(row["ex"])
            if not start<=ex<=end:continue
            if row["kind"] not in ("cash_bonus_same_day","cash_only"):
                if ex not in prev or not row.get("source_ref"):raise ValueError("Invalid unsupported action evidence")
                blocked.append((ex,row["code"]));continue
            a=Action(row["code"],date.fromisoformat(row["record"]),ex,date.fromisoformat(row["pay"]),
                    row["cash_per_old_share_net"],row["share_multiplier"],row["factor_before"],row["factor_after"],row["source_ref"])
            if prev.get(ex)!=a.record or (a.share_multiplier!=1 and row.get("shares_tradable_on")!=ex.isoformat()):
                raise ValueError("Unsupported record-date or share availability")
            if row["kind"]=="cash_only" and a.share_multiplier!=1:
                raise ValueError("Cash-only action includes shares")
            events.append(a)
        if sha256(path)!=h:raise ValueError("Action evidence changed")
        return cls(events,blocked),h
