from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from weipan.config import Config
from weipan.strategy import evaluate, partial_bar
from weipan.data import screen, load_data
from weipan.backtest import run_backtest
from test_engine import fixture


def entry_fixture():
    bars, cfg, data = fixture()
    cfg = replace(cfg, mode="minute", entry_filters="on", end_date=cfg.start_date)
    bars[35].update(close=11, high=11)
    bars[59]["volume"] = 150
    day = cfg.start_date
    slots = [f"{h:02d}:{m:02d}" for h in (9, 10, 11, 13, 14, 15) for m in range(0, 60, 5)
             if "09:35" <= f"{h:02d}:{m:02d}" <= "11:30" or "13:05" <= f"{h:02d}:{m:02d}" <= "15:00"]
    minutes = [{"datetime": day+" "+slot+":00", "open": 10.4, "high": 10.41,
                "low": 10.39, "close": 10.4, "volume": 4} for slot in slots]
    minutes[0].update(open=10, low=9.95)
    bars[60].update(open=10, high=10.41, low=9.95, close=10.4, volume=192, prev_close=10)
    data["stocks"]["600000"]["minutes"] = minutes
    return bars, cfg, data, minutes


def signal(bars, cfg, minutes):
    part = partial_bar(bars[59], minutes, cfg.start_date, cfg.selection_time, bars[60].get("prev_close"))
    return evaluate("600000", "测试股份", bars[:60] + [part], cfg)


class EntryFilterTests(unittest.TestCase):
    def test_current_day_volume_strict_threshold_and_previous_day_not_averaged_in_numerator(self):
        bars, cfg, _, minutes = entry_fixture()
        part = partial_bar(bars[59], minutes, cfg.start_date, cfg.selection_time)
        # Previous five volumes sum to 550, so the strict 1.5x boundary is 165.
        for volume, expected in [(164, False), (165, False), (166, True)]:
            part['volume'] = volume
            r = evaluate('600000','测试股份',bars[:60]+[part],cfg)
            self.assertEqual(r['checks']['volume'],expected)
            self.assertEqual(r['base_volume'],110)
        bars[59]['volume']=400
        part['volume']=150
        r=evaluate('600000','测试股份',bars[:60]+[part],cfg)
        self.assertFalse(r['checks']['volume'])  # Old (400 + 150) / 2 / 100 would pass.
        self.assertEqual(r['base_volume'],160)
        self.assertEqual(r['current_volume'],150)

    def test_daily_mode_rejected_even_when_optional_entry_filters_are_off(self):
        for payload in ({'mode':'daily','entry_filters':'off'}, {'volume_recent':2}, {'strategy_version':1}):
            with self.subTest(payload=payload),self.assertRaises(ValueError):
                Config.parse(payload)

    def test_default_rule_and_legacy_saved_config(self):
        c = Config.parse()
        self.assertEqual((c.mode, c.entry_filters, c.entry_ma_days, c.burst_volume_days,
                          c.burst_volume_ratio, c.upper_shadow_pct, c.gain_1430_min),
                         ("minute", "on", 5, 5, 2, 40, 3))
        legacy = Config.from_saved({"mode": "daily"})
        self.assertEqual(legacy.entry_filters, "off")
        with self.assertRaisesRegex(ValueError, "14:30"):
            Config.parse({"mode": "daily"})
        bars, c, data, _ = entry_fixture()
        c.mode = "daily"
        with self.assertRaisesRegex(ValueError, "14:30"):
            run_backtest(data, c)

    def test_new_parameters_validate_and_extend_warmup(self):
        for payload in ({"entry_filters": True}, {"entry_ma_days": 1}, {"entry_ma_days": 5.5},
                        {"burst_volume_days": 0}, {"burst_volume_ratio": "nan"},
                        {"upper_shadow_pct": 101}, {"gain_1430_min": -1}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                Config.parse(payload)
        self.assertEqual(Config.parse({"entry_ma_days": 200}).warmup, 200)
        self.assertEqual(Config.parse({"burst_volume_days": 120}).warmup, 121)

    def test_all_entry_conditions_and_exact_windows(self):
        bars, cfg, _, minutes = entry_fixture()
        r = signal(bars, cfg, minutes)
        self.assertTrue(r["eligible"], r["reason"])
        self.assertAlmostEqual(r["entry_ma"], 10.08)
        self.assertEqual(r["gain_1430"], 4)
        self.assertAlmostEqual(r["day_volume_ratio"], 184 / 110)
        self.assertAlmostEqual(r["volume_ratio"], round(184 / 110,3))

    def test_strictly_greater_than_three_percent(self):
        bars, cfg, _, minutes = entry_fixture()
        at_1430 = next(b for b in minutes if "14:30" in b["datetime"])
        for price, expected in [(10.29, False), (10.30, False), (10.31, True)]:
            at_1430["close"] = price
            r = signal(bars, cfg, minutes)
            self.assertEqual(r["checks"]["gain_1430"], expected)
        cfg.gain_1430_min = 3.1
        self.assertFalse(signal(bars, cfg, minutes)["checks"]["gain_1430"])

    def test_above_ma_strict_not_equal_and_not_daily_low(self):
        bars, cfg, _, minutes = entry_fixture()
        at_selection = next(b for b in minutes if cfg.selection_time in b["datetime"])
        for price, expected in [(9.99, False), (10, False), (10.01, True)]:
            at_selection["close"] = price
            self.assertEqual(signal(bars, cfg, minutes)["checks"]["above_entry_ma"], expected)
        self.assertLess(minutes[0]["low"], 10)

    def test_excludes_only_combined_burst_and_upper_shadow_including_boundary(self):
        bars, cfg, _, minutes = entry_fixture()
        part = partial_bar(bars[59], minutes, cfg.start_date, cfg.selection_time)
        # prior five-day mean is 110. Exactly 2x volume and exactly 40% shadow exclude.
        part.update(open=10.5, close=10.6, high=11, low=10)
        for vol, high, expected in [(220, 11, False), (219, 11, True), (220, 10.99, True), (221, 11.1, False)]:
            part.update(volume=vol, high=high)
            r = evaluate("600000", "测试", bars[:60] + [part], cfg)
            self.assertEqual(r["checks"]["no_burst_shadow"], expected)

    def test_shadow_uses_higher_of_open_and_price(self):
        bars, cfg, _, minutes = entry_fixture()
        part = partial_bar(bars[59], minutes, cfg.start_date, cfg.selection_time)
        part.update(open=10.9, close=10.5, high=11, low=10, volume=300)
        r = evaluate("600000", "测试", bars[:60] + [part], cfg)
        self.assertEqual(r["upper_shadow_pct"], 10)
        self.assertTrue(r["checks"]["no_burst_shadow"])

    def test_zero_range_and_zero_baseline(self):
        bars, cfg, _, minutes = entry_fixture()
        part = partial_bar(bars[59], minutes, cfg.start_date, cfg.selection_time)
        part.update(open=10.4, close=10.4, high=10.4, low=10.4, volume=1000)
        r = evaluate("600000", "测试", bars[:60] + [part], cfg)
        self.assertEqual(r["upper_shadow_pct"], 0)
        self.assertTrue(r["checks"]["no_burst_shadow"])
        for b in bars[55:60]:
            b["volume"] = 0
        r = evaluate("600000", "测试", bars[:60] + [part], cfg)
        self.assertIsNone(r["day_volume_ratio"])
        self.assertFalse(r["checks"]["no_burst_shadow"])

    def test_fixed_1430_price_does_not_follow_later_price(self):
        bars, cfg, _, minutes = entry_fixture()
        next(b for b in minutes if "14:30" in b["datetime"])["close"] = 10.3
        before = signal(bars, cfg, minutes)
        for b in minutes:
            if b["datetime"][11:16] > "14:30":
                b.update(close=10.9, high=10.9)
        after = signal(bars, cfg, minutes)
        self.assertEqual(before["gain_1430"], after["gain_1430"])
        self.assertFalse(after["checks"]["gain_1430"])

    def test_missing_1430_fails_instead_of_using_close(self):
        bars, cfg, data, minutes = entry_fixture()
        minutes[:] = [b for b in minutes if "14:30" not in b["datetime"]]
        r = signal(bars, cfg, minutes)
        self.assertIsNone(r["gain_1430"])
        self.assertFalse(r["eligible"])
        with self.assertRaisesRegex(ValueError, "完整"):
            run_backtest(data, cfg)
        with self.assertRaisesRegex(ValueError, "完整"):
            screen(data, cfg)

    def test_selection_at_1430_and_no_post_selection_leakage(self):
        bars, cfg, _, minutes = entry_fixture()
        cfg.selection_time = "14:30"
        before = signal(bars, cfg, minutes)
        for b in minutes:
            if b["datetime"][11:16] > "14:30":
                b.update(close=999, high=999, low=1, volume=9999999)
        self.assertEqual(before, signal(bars, cfg, minutes))

    def test_reference_close_for_gain_and_screen_matches_backtest(self):
        bars, cfg, data, minutes = entry_fixture()
        bars[60]["prev_close"] = 10.1
        r = signal(bars, cfg, minutes)
        self.assertFalse(r["checks"]["gain_1430"])
        self.assertAlmostEqual(r["gain_1430"], (10.4 / 10.1 - 1) * 100)
        self.assertEqual(screen(data, cfg)["signals"][0], run_backtest(data, cfg)["signals"][0])

    def test_buy_same_day_after_decision_with_snapshot_and_t_plus_one(self):
        bars, cfg, data, minutes = entry_fixture()
        # Today's full closing candle and post-buy fall are unavailable at selection.
        bars[60].update(close=9.5, low=9, high=20, volume=999999)
        minutes[-1].update(close=9.5, low=9)
        r = run_backtest(data, cfg)
        self.assertEqual(r["metrics"]["trade_count"], 0)
        self.assertEqual(len(r["holdings"]), 1)
        p = r["holdings"][0]
        self.assertEqual(p["buy_date"], cfg.start_date)
        self.assertEqual(p["buy_time"], cfg.start_date + " 14:55:00")
        self.assertEqual(p["buy_price"], 10.4)
        self.assertEqual(p["entry_signal"]["gain_1430"], 4)
        self.assertEqual(p["entry_signal"]["price"], 10.4)
        self.assertEqual(p["entry_signal"]["selection_time"], "14:50")
        self.assertEqual(p["signal_time"][:10],p["buy_date"])
        self.assertEqual(p["buy_window_start"],cfg.start_date + " 14:50:00")

    def test_filtered_candidates_never_enter(self):
        bars, cfg, data, minutes = entry_fixture()
        next(b for b in minutes if "14:30" in b["datetime"])["close"] = 10.3
        r = run_backtest(data, cfg)
        self.assertEqual(r["holdings"], [])
        self.assertEqual(r["trades"], [])

    def test_missing_minutes_rejected_before_downloading_rest_of_pool(self):
        bars, cfg, data, minutes = entry_fixture()
        cfg = replace(cfg, source="akshare", symbols="600000,600001")
        minutes.pop()
        with TemporaryDirectory() as folder, patch('weipan.data.CACHE', Path(folder)), \
                patch('weipan.data.run_fetch', return_value=data['stocks']['600000']) as fetch:
            with self.assertRaisesRegex(ValueError, "完整"):
                load_data(cfg)
            self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
