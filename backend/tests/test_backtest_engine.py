import unittest
from dataclasses import replace
from datetime import date, timedelta
from value_screener.backtest_engine import (
    Bar, BacktestConfig, generate_ma_trend_signals, run_backtest,
)
from value_screener.strategy_catalog import load_catalog, live_strategy_ids
from value_screener.history_parquet import valid_daily


def bars(count=65, *, close=10.0, up=11.0, down=9.0, code="000001.SZ"):
    start=date(2025,1,1)
    return [
        Bar(code,start+timedelta(days=i),
            close,close,close,close,10_000,up,down)
        for i in range(count)
    ]


class TestHistoryArchive(unittest.TestCase):
    def test_validated_daily_quotes(self):
        day="20250102"
        row={"ts_code":"000001.SZ","trade_date":day,
             "open":10,"high":11,"low":9,"close":10.5,"vol":3000,"amount":5000}
        self.assertEqual(len(valid_daily(row,day)),8)
        self.assertIsNone(valid_daily({**row,"trade_date":"20250103"},day))
        self.assertIsNone(valid_daily({**row,"high":9},day))
        self.assertIsNone(valid_daily({**row,"close":-1},day))

    def test_registry_does_not_auto_activate_planned_strategy(self):
        items=load_catalog()
        self.assertGreaterEqual(len(items),4)
        self.assertEqual(live_strategy_ids(),{"netnet_v1"})
        self.assertTrue(any(r["status"]=="research" for r in items))


class TestBacktestKernel(unittest.TestCase):
    def test_order_fills_only_next_open_and_respects_tplus1(self):
        rows=bars(15)
        signal={rows[0].day:["000001.SZ"]}
        result=run_backtest(rows,signal,BacktestConfig(holding_sessions=5))
        fills=result["fills"]
        self.assertEqual(fills[0]["side"],"BUY")
        self.assertEqual(fills[0]["signal_day"],rows[0].day.isoformat())
        self.assertEqual(fills[0]["day"],rows[1].day.isoformat())
        self.assertEqual(fills[1]["side"],"SELL")
        self.assertEqual(fills[1]["day"],rows[6].day.isoformat())
        self.assertEqual(len(result["trades"]),1)
        self.assertEqual(result["trades"][0]["held_sessions"],5)
        self.assertLess(result["metrics"]["total_return"],0)
        self.assertEqual(result["metrics"]["validation"],
                         "ENGINEERING_ONLY_NOT_A_VERIFIED_HISTORICAL_RETURN")

    def test_cannot_buy_limit_up_and_cannot_sell_limit_down(self):
        items=bars(10)
        # Day one will be limit-up, so no buy.
        items[1]=replace(items[1],open=11,high=11,low=10,close=10.0,up_limit=11)
        out=run_backtest(items,{items[0].day:["000001.SZ"]},
                         BacktestConfig(holding_sessions=2))
        self.assertEqual(len(out["fills"]),0)

        items=bars(10)
        # Sell due on day 3, but held through limit-down day 3.
        items[3]=replace(items[3],open=9,high=10,low=9,close=10,down_limit=9)
        out=run_backtest(items,{items[0].day:["000001.SZ"]},
                         BacktestConfig(holding_sessions=2))
        self.assertEqual(out["trades"][0]["exit_day"],items[4].day.isoformat())

    def test_stops_if_strict_limits_not_provided(self):
        rows=[replace(item,up_limit=None,down_limit=None) for item in bars(10)]
        out=run_backtest(rows,{rows[0].day:["000001.SZ"]})
        self.assertEqual(len(out["fills"]),0)

    def test_suspended_day_retries_sale(self):
        rows=bars(8)
        rows[2]=replace(rows[2],suspended=True,volume=0)
        out=run_backtest(rows,{rows[0].day:["000001.SZ"]},
                         BacktestConfig(holding_sessions=1))
        self.assertEqual(out["trades"][0]["exit_day"],rows[3].day.isoformat())

    def test_lot_size_and_no_leverage(self):
        rows=bars(10)
        out=run_backtest(rows,{rows[0].day:["000001.SZ"]},
                         BacktestConfig(initial_cash=5000,max_positions=2,
                                        lot_size=100,holding_sessions=10))
        buys=[f for f in out["fills"] if f["side"]=="BUY"]
        self.assertEqual(len(buys),1)
        self.assertEqual(buys[0]["shares"]%100,0)
        self.assertTrue(all(point["cash"]>=0 for point in out["equity"]))
        self.assertEqual(out["open_positions"],1)

    def test_no_future_leak_for_ma_signal(self):
        rows=bars(60)
        earlier=generate_ma_trend_signals(rows[:55])
        changed=rows[:55]+[replace(bar,open=20,high=20,low=20,close=20)
                           for bar in rows[55:]]
        allsignals=generate_ma_trend_signals(changed)
        past={day:c for day,c in allsignals.items() if day<=rows[54].day}
        self.assertEqual(earlier,past)

    def test_same_day_signal_cannot_buy_without_next_bar(self):
        rows=bars(2)
        out=run_backtest(rows,{rows[1].day:["000001.SZ"]})
        self.assertEqual(len(out["fills"]),0)

    def test_duplicate_daily_prices_raise(self):
        rows=bars(2)
        with self.assertRaises(ValueError):
            run_backtest([rows[0],rows[0],rows[1]],{})


if __name__=="__main__":
    unittest.main()
