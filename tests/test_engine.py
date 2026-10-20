import copy
from datetime import date, timedelta
import unittest
from unittest.mock import patch

from weipan.config import Config
from weipan.strategy import evaluate, price_limit, limit_rate, partial_bar, profit_cross
from weipan.data import demo_data, screen, load_data
from weipan.backtest import run_backtest, fees


def fixture():
    days, day = [], date(2026, 1, 5)
    while len(days) < 65:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    bars = [{"date": d, "open": 10, "high": 10.05, "low": 9.95, "close": 10,
             "volume": 100, "market_cap": 100e8} for d in days]
    cfg = Config.parse({"start_date": days[60], "end_date": days[-1], "symbols": "600000", "initial_cash": 100000,
                        "mode": "minute", "entry_filters": "off",
                        "max_positions": 1, "commission": 0, "min_commission": 0, "stamp_tax": 0, "transfer_fee": 0, "slippage": 0})
    return bars, cfg, {"stocks": {"600000": {"daily": bars, "name": "测试股份", "minutes": []}}, "source": "demo", "warnings": []}


def with_minutes(data, cfg):
    slots = [f"{h:02d}:{m:02d}:00" for h in (9, 10, 11, 13, 14, 15) for m in range(0, 60, 5)
             if "09:35" <= f"{h:02d}:{m:02d}" <= "11:30" or "13:05" <= f"{h:02d}:{m:02d}" <= "15:00"]
    for item in data['stocks'].values():
        item['minutes'] = [{"datetime": b['date']+' '+slot,
                            **{k: b[k] for k in ('open','high','low','close')}, 'volume': b['volume']/48}
                           for b in item['daily'] if cfg.start_date <= b['date'] <= cfg.end_date for slot in slots]
    return data


def force_entry(day):
    def signal(symbol, name, bars, cfg):
        return {"symbol": symbol, "name": name, "date": bars[-1]["date"], "eligible": bars[-1]["date"] == day,
                "checks": {}, "score": 1}
    return signal


class ConfigTests(unittest.TestCase):
    def test_defaults_match_user(self):
        c = Config.parse()
        self.assertEqual((c.market_cap_min, c.market_cap_max, c.limit_lookback, c.sideways_days, c.sideways_range), (50, 200, 50, 7, 10))
        self.assertEqual((c.volume_recent, c.volume_base, c.volume_ratio, c.stop_loss, c.ma_fast, c.ma_slow), (1, 5, 1.5, 2, 3, 5))

    def test_invalid_parameters_rejected(self):
        for p in ({"market_cap_min":201},{"volume_ratio":"nan"},{"sideways_days":7.5},{"ma_fast":5},
                  {"selection_time":"15:00"},{"selection_time":"14:31"},{"max_positions":0},
                  {"symbols":"../../etc/passwd"},{"source":"made-up"},{"unexpected":1},{"stop_loss":101}):
            with self.subTest(p=p), self.assertRaises((ValueError, TypeError)):
                Config.parse(p)

    def test_symbols_normalized(self):
        self.assertEqual(Config.parse({"symbols":"600000，600001\n600000"}).symbols, "600000,600001")


class SignalTests(unittest.TestCase):
    def qualifying_bars(self):
        bars, c, _ = fixture()
        bars[35]["close"] = 11
        bars[35]["high"] = 11
        bars[-1]["volume"] = 160
        return bars, c

    def test_all_conditions_and_nonoverlapping_volume(self):
        bars, c = self.qualifying_bars()
        r = evaluate("600000", "测试股份", bars, c)
        self.assertTrue(r["eligible"])
        self.assertEqual(r["volume_ratio"], 1.6)

    def test_cap_bounds_in_yi_and_no_current_cap_substitution(self):
        bars, c = self.qualifying_bars()
        for cap, expect in [(50e8, True), (200e8, True), (49e8, False), (201e8, False), (None, False)]:
            bars[-1]["market_cap"] = cap
            self.assertEqual(evaluate("600000", "测试股份", bars, c)["checks"]["market_cap"], expect)

    def test_amplitude_uses_high_low_not_close(self):
        bars, c = self.qualifying_bars()
        bars[-4]["high"] = 11.5
        self.assertFalse(evaluate("600000", "测试股份", bars, c)["checks"]["sideways"])

    def test_recent_limit_window_and_touch(self):
        bars, c = self.qualifying_bars()
        bars[35]["close"] = 10
        self.assertFalse(evaluate("600000", "测试股份", bars, c)["checks"]["limit_up"])
        c.limit_type = "touch"
        self.assertTrue(evaluate("600000", "测试股份", bars, c)["checks"]["limit_up"])
        c.limit_lookback = 10
        self.assertFalse(evaluate("600000", "测试股份", bars, c)["checks"]["limit_up"])

    def test_incomplete_bar_cannot_count_as_close_limit(self):
        bars, c = self.qualifying_bars()
        bars[35]["close"] = bars[35]["high"] = 10
        bars[-1].update(close=11, high=11, complete=False)
        self.assertFalse(evaluate("600000", "测试股份", bars, c)["checks"]["limit_up"])
        c.limit_type = "touch"
        self.assertTrue(evaluate("600000", "测试股份", bars, c)["checks"]["limit_up"])

    def test_limit_rounding_and_board_rates(self):
        self.assertEqual(price_limit(10.05, .1), 11.06)
        self.assertEqual(limit_rate("300001", "2020-08-23"), .1)
        self.assertEqual(limit_rate("300001", "2020-08-24"), .2)
        self.assertEqual(limit_rate("688001", "2026-01-01"), .2)

    def test_ma_cross_requires_transition(self):
        c = Config.parse()
        bars = [{"close": p} for p in [10,11,12,12,11,10]]
        self.assertTrue(profit_cross(bars,c))
        self.assertFalse(profit_cross(bars+[{"close":9}],c))

    def test_partial_volume_and_no_future_bar_leakage(self):
        previous={"close":10, "market_cap":100e8}
        minutes=[{"datetime":"2026-10-16 "+t,"open":10,"high":11,"low":9,"close":10.5,"volume":v} for t,v in [("09:35:00",100),("14:50:00",200),("14:55:00",999999)]]
        bar=partial_bar(previous,minutes,"2026-10-16","14:50")
        self.assertEqual(bar["volume"],300)
        self.assertEqual(bar["market_cap"],105e8)
        minutes[-1].update(high=1000,close=500)
        self.assertEqual(bar,partial_bar(previous,minutes,"2026-10-16","14:50"))
        self.assertIsNone(partial_bar(previous,minutes,"2026-10-16","14:45"))


class BacktestTests(unittest.TestCase):
    def test_t_plus_one_and_gap_stop(self):
        bars,c,data=fixture()
        bars[60].update(low=8.5,close=9)
        bars[61].update(open=9.5,low=9.4,high=9.7,close=9.6)
        with patch('weipan.backtest.evaluate', side_effect=force_entry(bars[60]["date"])):
            r=run_backtest(with_minutes(data,c),c)
        t=r["trades"][0]
        self.assertEqual(t["buy_date"],bars[60]["date"])
        self.assertEqual(t["signal_time"][:10],t["buy_date"])
        self.assertEqual(t["buy_time"][11:16],"14:55")
        self.assertEqual(t["sell_date"],bars[61]["date"])
        self.assertEqual(t["sell_price"],9.5)
        self.assertEqual(t["return_pct"],-5)
        self.assertEqual(t["holding_days"],1)

    def test_limit_down_cannot_sell_then_retries(self):
        bars,c,data=fixture()
        bars[61].update(open=9,high=9,low=9,close=9)
        bars[62].update(open=9.2,high=9.3,low=9.1,close=9.2)
        with patch('weipan.backtest.evaluate',side_effect=force_entry(bars[60]["date"])):
            r=run_backtest(with_minutes(data,c),c)
        self.assertEqual(r["trades"][0]["sell_date"],bars[62]["date"])
        self.assertTrue(r["skipped"])

    def test_limit_up_cannot_buy(self):
        bars,c,data=fixture()
        bars[60].update(open=11,high=11,low=11,close=11)
        with patch('weipan.backtest.evaluate',side_effect=force_entry(bars[60]["date"])):
            r=run_backtest(with_minutes(data,c),c)
        self.assertEqual(r["metrics"]["open_positions"],0)
        self.assertEqual(r["metrics"]["trade_count"],0)

    def test_fee_accounting_lots_and_mark_to_market(self):
        bars,c,data=fixture()
        c.commission=.025;c.min_commission=5;c.transfer_fee=.001
        with patch('weipan.backtest.evaluate',side_effect=force_entry(bars[60]["date"])):
            r=run_backtest(with_minutes(data,c),c)
        p=r["holdings"][0]
        self.assertEqual(p["quantity"]%100,0)
        self.assertGreaterEqual(r["metrics"]["cash"],0)
        self.assertAlmostEqual(r["metrics"]["final_equity"],c.initial_cash-p["buy_fee"],places=2)
        self.assertAlmostEqual(r["metrics"]["cash"]+p["quantity"]*10,r["metrics"]["final_equity"],places=2)

    def test_stop_after_selection_cannot_finance_earlier_tail_buy_or_carry_it_to_tomorrow(self):
        bars,c,data=fixture()
        data["stocks"]["600001"]={"daily":copy.deepcopy(bars),"name":"测试乙","minutes":[]}
        with_minutes(data,c)
        data['stocks']['600000']['minutes'][95].update(low=9.5,close=9.6)
        def signals(symbol,name,h,cfg):
            yes=(symbol=="600000" and h[-1]["date"]==bars[60]["date"]) or (symbol=="600001" and h[-1]["date"]==bars[61]["date"])
            return {"symbol":symbol,"date":h[-1]["date"],"eligible":yes,"checks":{},"score":1}
        with patch('weipan.backtest.evaluate',side_effect=signals):
            r=run_backtest(data,c)
        self.assertEqual(len(r["trades"]),1)
        self.assertEqual(len(r["holdings"]),0)

    def test_profit_exit_requires_profit_after_fees(self):
        bars,c,data=fixture()
        c.commission=.2;c.min_commission=5
        with patch('weipan.backtest.evaluate',side_effect=force_entry(bars[60]["date"])),patch('weipan.backtest.profit_cross',return_value=True):
            r=run_backtest(with_minutes(data,c),c)
        self.assertEqual(r["metrics"]["trade_count"],0)
        self.assertEqual(r["metrics"]["open_positions"],1)

    def test_future_data_cannot_change_past_equity(self):
        c=Config.parse({"start_date":"2026-08-03","end_date":"2026-08-31","symbols":"600000,600008,600017"})
        data=demo_data(c); before=run_backtest(data,c)
        for stock in data["stocks"].values():
            for b in stock["daily"]:
                if b["date"]>"2026-08-20":
                    b.update(close=b["close"]*1.2,high=b["high"]*1.2,volume=b["volume"]*4,market_cap=9999e8)
        after=run_backtest(data,c)
        self.assertEqual([p for p in before["curve"] if p["date"]<="2026-08-20"],[p for p in after["curve"] if p["date"]<="2026-08-20"])

    def test_strict_minute_refuses_incomplete_data(self):
        c=Config.parse({"start_date":"2026-08-03","end_date":"2026-08-07","symbols":"600000","mode":"minute"})
        data=demo_data(c)
        run_backtest(data,c)
        data["stocks"]["600000"]["minutes"].pop(0)
        with self.assertRaisesRegex(ValueError,"缺少完整"):
            run_backtest(data,c)

    def test_screen_requires_complete_intraday_volume(self):
        c=Config.parse({"start_date":"2026-08-03","end_date":"2026-08-07","symbols":"600000","mode":"minute"})
        data=demo_data(c)
        self.assertEqual(screen(data,c)["screen_date"],c.end_date)
        data["stocks"]["600000"]["minutes"]=[b for b in data["stocks"]["600000"]["minutes"] if b["datetime"]!=c.end_date+" 10:00:00"]
        with self.assertRaisesRegex(ValueError,"完整盘中行情"):
            screen(data,c)

    def test_real_data_failure_never_substitutes_demo(self):
        _,c,_=fixture();c.source='akshare'
        with patch('weipan.data.run_fetch',side_effect=ValueError('network failure')),patch('weipan.data.demo_data') as mock:
            with self.assertRaisesRegex(ValueError,'network failure'):
                load_data(c)
            mock.assert_not_called()

    def test_sell_tax_and_minimum_commission(self):
        c=Config.parse()
        self.assertAlmostEqual(fees(1000,c),5.01)
        self.assertAlmostEqual(fees(1000,c,True),5.51)

    def test_demo_prices_stable_across_parameters_and_universe(self):
        c=Config.parse({"start_date":"2026-08-03","end_date":"2026-08-07","symbols":"600000,600017"})
        before=demo_data(c)["stocks"]["600017"]["daily"]
        c.start_date="2026-07-01";c.limit_lookback=100;c.symbols="600017"
        after=demo_data(c)["stocks"]["600017"]["daily"]
        self.assertEqual([b for b in before if b["date"]>="2026-08-03"],[b for b in after if b["date"]>="2026-08-03"])


if __name__ == '__main__':
    unittest.main()
