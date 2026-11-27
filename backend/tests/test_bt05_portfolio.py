import unittest
import json
from datetime import date,timedelta
from dataclasses import replace
from value_screener.backtest_engine import Bar,BacktestConfig,run_backtest
from value_screener.bt05_portfolio import Portfolio,FeeSchedule,FeePeriod

CODE="000001.SZ"
START=date(2024,12,27)

def bars(i,price=10,code=CODE,**kw):
    d=START+timedelta(days=i)
    return Bar(code,d,price,price*1.1,price*.9,price,1000,price*1.1,price*.9,**kw)

class BT05PortfolioTests(unittest.TestCase):
    def test_stream_matches_legacy_on_constant_factor_and_fees(self):
        cfg=BacktestConfig(holding_sessions=3,max_positions=2,stop_loss_rate=.05)
        data=[bars(i,10+i*.1) for i in range(12)]
        signals={data[i].day:[CODE] for i in (0,3,6,9)}
        expected=run_backtest(data,signals,cfg,entry_eligible={b.day:{CODE} for b in data})
        p=Portfolio(cfg,FeeSchedule.fixed(cfg));records=[]
        for i,b in enumerate(data):
            records.append(p.step(b.day,{CODE:b},{CODE},{CODE:1.},signals.get(b.day,[])))
            if i==4:
                self.assertTrue(p.holdings)
                p=Portfolio.restore(cfg,FeeSchedule.fixed(cfg),json.loads(json.dumps(p.checkpoint())))
        self.assertEqual([r["equity"] for r in records],expected["equity"])
        self.assertEqual([f for r in records for f in r["fills"]],expected["fills"])
        for x,y in zip([t for r in records for t in r["trades"]],expected["trades"]):
            self.assertEqual(x.keys(),y.keys())
            for k in x:
                if isinstance(x[k],float):self.assertAlmostEqual(x[k],y[k])
                else:self.assertEqual(x[k],y[k])
        self.assertAlmostEqual(p.metrics()["total_return"],expected["metrics"]["total_return"])
        self.assertAlmostEqual(p.metrics()["max_drawdown"],expected["metrics"]["max_drawdown"])

    def test_dated_fee_and_blocked_exit_survive_checkpoint(self):
        cfg=BacktestConfig(max_positions=1,holding_sessions=1,stop_loss_rate=.05)
        fees=FeeSchedule([FeePeriod(START,0,0,0,0),FeePeriod(START+timedelta(days=3),0,0,.01,0,.001)])
        p=Portfolio(cfg,fees)
        b=bars(0);p.step(b.day,{CODE:b},{CODE},{CODE:1},[CODE])
        b=bars(1);p.step(b.day,{CODE:b},{CODE},{CODE:1},[])
        b=replace(bars(2),down_limit=10)
        self.assertFalse(p.step(b.day,{CODE:b},{CODE},{CODE:1},[])["fills"])
        p=Portfolio.restore(cfg,fees,json.loads(json.dumps(p.checkpoint())))
        b=bars(3);r=p.step(b.day,{CODE:b},{CODE},{CODE:1},[])
        self.assertEqual(len(r["fills"]),1)
        self.assertEqual(r["fills"][0]["side"],"SELL")
        self.assertAlmostEqual(r["fills"][0]["fees"],1100)
        self.assertEqual(p.metrics()["blocked_sell_attempts"],1)

    def test_corporate_action_and_unknown_factor_block_before_execution(self):
        for factor in (2.,None):
            p=Portfolio(BacktestConfig(),FeeSchedule.fixed(BacktestConfig()))
            b=bars(0);p.step(b.day,{CODE:b},{CODE},{CODE:1.},[CODE])
            b=bars(1);p.step(b.day,{CODE:b},{CODE},{CODE:1.},[])
            b=bars(2)
            with self.assertRaisesRegex(ValueError,"Corporate action or factor gap"):
                p.step(b.day,{CODE:b},{CODE},{CODE:factor},[])

    def test_missing_session_bar_stale_mark_and_no_fake_liquidation(self):
        cfg=BacktestConfig(holding_sessions=1)
        p=Portfolio(cfg,FeeSchedule.fixed(cfg))
        for i in (0,1):
            b=bars(i);p.step(b.day,{CODE:b},{CODE},{CODE:1.},[CODE] if i==0 else [])
        r=p.step(START+timedelta(days=2),{},set(),{},[])
        self.assertFalse(r["fills"])
        self.assertEqual(p.metrics()["stale_mark_count"],1)
        self.assertEqual(p.metrics()["open_positions"],1)

    def test_schedule_requires_sorted_unique_periods(self):
        with self.assertRaises(ValueError):
            FeeSchedule([FeePeriod(START,0,0,0,0),FeePeriod(START,0,0,0,0)])
        with self.assertRaises(ValueError):
            FeeSchedule([FeePeriod(START,0,0,0,0)]).at(START-timedelta(days=1))

    def test_slippage_clamped_to_price_limit(self):
        cfg=BacktestConfig(slippage_rate=.01)
        p=Portfolio(cfg,FeeSchedule.fixed(cfg))
        b=bars(0);p.step(b.day,{CODE:b},{CODE},{CODE:1.},[CODE])
        b=replace(bars(1),up_limit=10.01)
        r=p.step(b.day,{CODE:b},{CODE},{CODE:1.},[])
        self.assertEqual(r["fills"][0]["price"],10.01)

if __name__=="__main__":unittest.main()
