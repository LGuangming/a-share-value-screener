"""Independent technical strategy tests using deterministic synthetic prices."""
import unittest
from dataclasses import replace
from datetime import date, timedelta
from value_screener.backtest_engine import Bar, BacktestConfig, run_backtest
from value_screener.technical_strategies import (
    Observation, TechnicalConfig, FactorState, TREND, REVERSAL,
    generate_signals, ranked_close_signals,
)

START=date(2020,1,1)

def obs(i,close,*,code="000001.SZ",factor=1.,vol=1000.,eligible=True,amount=60_000_000.,session=None):
    # Calendar indexes are synthetic sessions, not asserted real exchange dates.
    b=Bar(code,START+timedelta(days=i),close,close*1.005,close*.995,close,
          vol,close*1.1,close*.9)
    return Observation(b,factor,amount,eligible,120+i,i if session is None else session)

def trend():
    return [obs(i,100+i*.2) for i in range(125)]

def reversal():
    rows=[obs(i,100-i*.6) for i in range(120)]
    return rows+[obs(120,30.,vol=2000.)]

class TechnicalTests(unittest.TestCase):
    def test_trend_and_reversal_signal_on_known_rebound(self):
        trend_signals=list(generate_signals(trend()))
        self.assertTrue(any(s.strategy_id==TREND for s in trend_signals))
        self.assertFalse(any(s.strategy_id==REVERSAL for s in trend_signals))
        signals=list(generate_signals(reversal()))
        rev=[s for s in signals if s.strategy_id==REVERSAL]
        self.assertEqual(len(rev),1)
        self.assertEqual(rev[0].day,START+timedelta(days=120))
        self.assertLessEqual(rev[0].prior_return,-.1)
        self.assertLess(rev[0].prior_rsi,30)
        self.assertLess(rev[0].prior_bias,-.08)
        self.assertEqual(ranked_close_signals(signals,REVERSAL),{rev[0].day:["000001.SZ"]})

    def test_no_future_data_and_factor_scaling(self):
        rows=trend()
        cutoff=90
        prefix=list(generate_signals(rows[:cutoff]))
        self.assertEqual(prefix,[s for s in generate_signals(rows) if s.day<rows[cutoff].bar.day])
        changed=[]
        for i,x in enumerate(rows):
            if i<70:
                changed.append(x);continue
            b=x.bar
            changed.append(replace(x,factor=2.,bar=replace(b,open=b.open/2,high=b.high/2,
                           low=b.low/2,close=b.close/2,up_limit=b.up_limit/2,down_limit=b.down_limit/2)))
        self.assertEqual(list(generate_signals(rows)),list(generate_signals(changed)))

    def test_unknown_and_ineligible_fail_closed(self):
        for bad in (None,False):
            rows=[replace(x,eligible=bad) for x in trend()]
            self.assertEqual(list(generate_signals(rows)),[])
        self.assertEqual(list(generate_signals([replace(x,listing_sessions=None) for x in trend()])),[])
        self.assertEqual(list(generate_signals([replace(x,amount_yuan=1.) for x in trend()])),[])
        self.assertEqual(list(generate_signals([replace(x,listing_sessions=10) for x in trend()])),[])

    def test_bad_factor_and_calendar_gap_reset_history(self):
        rows=trend()[:60]
        rows[49]=replace(rows[49],factor=None)
        self.assertEqual(list(generate_signals(rows)),[])
        rows=trend()[:60]
        rows=[replace(x,session=x.session+1) if i>=49 else x for i,x in enumerate(rows)]
        self.assertEqual(list(generate_signals(rows)),[])
        with self.assertRaises(ValueError):
            list(generate_signals([rows[0],rows[0]]))
        with self.assertRaises(ValueError):
            TechnicalConfig(ma_fast=50,ma_slow=20)

    def test_wilder_indicators_and_no_early_factors(self):
        state=FactorState(TechnicalConfig())
        points=[state.push(obs(i,10.)) for i in range(50)]
        self.assertIsNone(points[13].rsi14)
        self.assertEqual(points[14].rsi14,50.)
        self.assertAlmostEqual(points[14].atr14_pct,.01)
        self.assertIsNone(points[48].ma_slow)
        self.assertEqual(points[49].ma_slow,10.)

    def test_deterministic_cross_section_ranking(self):
        s=next(x for x in generate_signals(trend()) if x.strategy_id==TREND)
        ranked=ranked_close_signals([replace(s,ts_code="600001.SH",score=2),
                                     replace(s,ts_code="000002.SZ",score=1),
                                     replace(s,ts_code="000001.SZ",score=1)],TREND)
        self.assertEqual(ranked[s.day],["600001.SH","000001.SZ","000002.SZ"])

class ExecutionTests(unittest.TestCase):
    def test_open_size_does_not_depend_on_same_day_close(self):
        codes=("000001.SZ","000002.SZ")
        rows=[obs(i,10.,code=c).bar for i in range(4) for c in codes]
        signals={START:[codes[0]],START+timedelta(days=1):[codes[1]]}
        cfg=BacktestConfig(max_positions=2,holding_sessions=10)
        alt=[replace(b,close=2.,low=2.) if b.ts_code==codes[0] and b.day==START+timedelta(days=2) else b for b in rows]
        def buys(bars):
            return [f for f in run_backtest(bars,signals,cfg)["fills"] if f["side"]=="BUY"]
        self.assertEqual(buys(rows),buys(alt))
        self.assertEqual(len(buys(rows)),2)

    def test_stop_at_close_executes_next_open_and_retries_blocked_sell(self):
        rows=[obs(i,10.).bar for i in range(5)]
        rows[1]=replace(rows[1],close=9.,low=9.)
        rows[2]=replace(rows[2],down_limit=10.)
        result=run_backtest(rows,{START:["000001.SZ"]},
                            BacktestConfig(holding_sessions=20,stop_loss_rate=.05))
        fills=result["fills"]
        self.assertEqual([f["side"] for f in fills],["BUY","SELL"])
        self.assertEqual(fills[1]["day"],(START+timedelta(days=3)).isoformat())

if __name__=="__main__":
    unittest.main()
