"""Auditable value-screening rules, free of market-provider/database dependencies.

Accounting conventions:
* Cash, current assets, and liabilities are CNY (yuan).
* Market capitalisation is CNY (Tushare daily_basic.total_mv is in 10,000 CNY).
* We never substitute zero for an unknown accounting line item in the stressed NCAV.
* An NCAV screen is a research filter, NOT a trading or liquidation recommendation.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


NETNET_IGNORED_INDUSTRIES = ("银行", "证券", "保险", "多元金融")


def netnet_financial_industry(industry: Any) -> bool:
    return isinstance(industry, str) and industry.strip() in NETNET_IGNORED_INDUSTRIES


@dataclass(frozen=True)
class ScreenConfig:
    max_price: float | None = 10.0
    max_pb: float | None = 1.2  # Optional display/research bounds.
    min_ncav_coverage: float = 1.25
    min_stress_coverage: float = 1.0
    max_liability_ratio: float = 0.60
    exclude_st: bool = True
    debt_filter_is_hard: bool = False


def _n(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
        if result != result or result in (float("inf"), float("-inf")):
            return None
        return result
    except (ValueError, TypeError, OverflowError):
        return None


def assess(stock: dict, quote: dict, balance: dict | None,
           income: dict | None = None, cashflow: dict | None = None,
           cfg: ScreenConfig = ScreenConfig()) -> dict:
    """Compute as-of a trading day. Caller must preselect disclosed-as-of statements."""
    code = str(stock["ts_code"])
    name = str(stock.get("name") or code)
    price = _n(quote.get("close"))
    pb = _n(quote.get("pb"))
    # Tushare daily_basic.total_mv is 万元, financial statements are 元.
    mv_10k = _n(quote.get("total_mv"))
    market_cap = (mv_10k * 10000.0) if mv_10k is not None else None
    result = {
        "ts_code": code, "name": name, "price": price, "pb": pb,
        "market_cap": market_cap, "ncav": None, "coverage": None,
        "stress_ncav": None, "stress_coverage": None, "liability_ratio": None,
        "report_date": None, "ann_date": None,
        "operating_cf": None, "net_profit": None, "is_candidate": False,
        "status": "DATA_GAP", "signal": "NOT_EVALUATED", "flags": [],
        "assessment": "尚缺少可用于本交易日的合并资产负债表，暂不能判定估值。",
    }
    flags: list[str] = result["flags"]
    if bool(stock.get("is_st")) or "ST" in name.upper():
        flags.append("ST_RISK")
    if price is None or price <= 0 or market_cap is None or market_cap <= 0:
        flags.append("INVALID_MARKET_DATA")
        return result
    if cfg.max_price is not None and price > cfg.max_price:
        result["status"] = "PRICE_EXCLUDED"
        result["assessment"] = "价格高于筛选上限，仅保留行情记录。"
        return result
    if cfg.exclude_st and "ST_RISK" in flags:
        result["status"] = "RISK_EXCLUDED"
        result["assessment"] = "股票名称带ST风险标记，按策略规则排除。"
        return result
    if cfg.max_pb is not None and pb is not None and pb > cfg.max_pb:
        result["status"] = "PB_EXCLUDED"
        result["assessment"] = "PB超过当前初筛上限。"
        return result
    if netnet_financial_industry(stock.get("industry")):
        result.update(status="FINANCIAL_IGNORED", signal="NOT_ELIGIBLE",
                      assessment="金融行业报表结构不适用Net-Net，本策略忽略，不属于财报数据短缺。")
        return result
    if balance is None:
        return result
    ca = _n(balance.get("total_cur_assets"))
    liab = _n(balance.get("total_liab"))
    assets = _n(balance.get("total_assets"))
    if ca is None or liab is None or ca < 0 or liab < 0:
        flags.append("MISSING_MATERIAL_BALANCE_ITEM")
        return result
    if assets is not None and (assets <= 0 or ca > assets * 1.01):
        flags.append("BALANCE_INCONSISTENCY")
        return result
    ncav = ca - liab
    cover = ncav / market_cap
    lev = liab / assets if assets and assets > 0 else None
    result.update(ncav=ncav, coverage=cover, liability_ratio=lev)
    if lev is not None and lev > cfg.max_liability_ratio:
        flags.append("HIGH_LEVERAGE")
    receiv = _n(balance.get("accounts_receiv"))
    inv = _n(balance.get("inventories"))
    prepay = _n(balance.get("prepayment"))
    minority = _n(balance.get("minority_int"))
    # Some genuinely absent line items are shown as null. They require manual
    # reconciliation; never silently treat unknown as zero.
    components = (receiv, inv, prepay, minority)
    if any(item is None for item in components):
        flags.append("STRESS_INPUT_INCOMPLETE")
    elif all(item >= 0 for item in components):
        stressed = ncav - minority - 0.20 * receiv - 0.30 * inv - 0.50 * prepay
        result["stress_ncav"] = stressed
        result["stress_coverage"] = stressed / market_cap
    else:
        flags.append("NEGATIVE_STRESS_INPUT")
    if result["stress_coverage"] is not None and result["stress_coverage"] < cfg.min_stress_coverage:
        flags.append("STRESS_COVERAGE_BELOW_100")
    if receiv is not None and ca > 0 and receiv / ca >= 0.40:
        flags.append("RECEIVABLES_CONCENTRATED")
    if inv is not None and ca > 0 and inv / ca >= 0.40:
        flags.append("INVENTORY_CONCENTRATED")
    op = _n((cashflow or {}).get("n_cashflow_act"))
    net_profit = _n((income or {}).get("n_income"))
    result["operating_cf"] = op
    result["net_profit"] = net_profit
    if op is None:
        flags.append("CASHFLOW_NOT_VERIFIED")
    elif op < 0:
        flags.append("NEGATIVE_OPERATING_CASHFLOW")
    if net_profit is not None and net_profit < 0:
        flags.append("LOSS_MAKING")
    if lev is None:
        flags.append("LEVERAGE_NOT_VERIFIED")
    passed = cover >= cfg.min_ncav_coverage and (not cfg.debt_filter_is_hard or (lev is not None and lev <= cfg.max_liability_ratio))
    result["is_candidate"] = passed
    if passed:
        result["status"] = "NET_NET"
    elif cover >= 1.0:
        result["status"] = "NEAR_NET_NET"
    else:
        result["status"] = "BELOW_NET_NET"
    if cover >= cfg.min_ncav_coverage:
        base = f"账面NCAV覆盖市值{cover:.2f}倍，达到当前筛选阈值。"
    elif cover >= 1.0:
        base = f"NCAV覆盖市值{cover:.2f}倍，尚未达到安全边际筛选阈值。"
    else:
        base = f"NCAV覆盖市值{cover:.2f}倍，不满足严格账面折价条件。"
    stress = result["stress_coverage"]
    if stress is None:
        base += "应收、存货、预付或少数股东权益数据不完整，压力价值待核验。"
    elif stress >= cfg.min_stress_coverage:
        base += f"统一假设压力价值覆盖市值{stress:.2f}倍；仍须核验实际可回收性。"
    else:
        base += f"统一假设压力价值仅覆盖市值{stress:.2f}倍，账面安全边际可能不足。"
    if op is not None:
        base += "本报告期经营现金流为负。" if op < 0 else "本报告期经营现金流为正。"
    else:
        base += "经营现金流数据不足。"
    if net_profit is not None and net_profit < 0:
        base += "公司处于亏损状态，需评估持续消耗资产的风险。"
    result["assessment"] = base
    return result


def technical_signal(closes: list[float], is_candidate: bool, flags: list[str]) -> str:
    """Research signal only; 50 completed sessions required, no forward prices."""
    if not is_candidate:
        return "NOT_ELIGIBLE"
    if len(closes) < 50:
        return "INSUFFICIENT_HISTORY"
    if any(x <= 0 for x in closes[-50:]):
        return "INVALID_HISTORY"
    ma20 = sum(closes[-20:]) / 20
    prev20 = sum(closes[-21:-1]) / 20
    ma50 = sum(closes[-50:]) / 50
    fundamental_blocks = {"NEGATIVE_OPERATING_CASHFLOW", "CASHFLOW_NOT_VERIFIED",
                          "STRESS_INPUT_INCOMPLETE", "STRESS_COVERAGE_BELOW_100",
                          "RECEIVABLES_CONCENTRATED", "INVENTORY_CONCENTRATED",
                          "LOSS_MAKING", "HIGH_LEVERAGE", "FINANCIAL_PERIOD_MISMATCH"}
    if fundamental_blocks.intersection(flags):
        return "FUNDAMENTAL_REVIEW"
    if closes[-1] > ma20 >= ma50 and ma20 >= prev20:
        return "BUY_WATCH"  # Not a broker order or confirmed edge.
    if closes[-1] < ma20 and closes[-1] < ma50:
        return "RISK_WATCH"
    return "WAIT"
