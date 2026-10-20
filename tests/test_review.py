import copy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from weipan.config import Config
from weipan.universe import select_stocks
from weipan.charts import build_chart, get_chart, save_charts
from weipan.data import atomic_json


def sample():
    cfg=Config.from_saved({"symbols":"600000", "start_date":"2026-01-01", "end_date":"2026-03-31", "mode":"daily", "entry_filters":"off"})
    bars=[{"date":(date(2026,1,1)+timedelta(days=i)).isoformat(), "open":10+i*.1,
        "high":10.5+i*.1, "low":9.5+i*.1, "close":10.1+i*.1, "volume":100+i} for i in range(45)]
    result={"id":"012345abcdef", "kind":"backtest", "config":cfg.to_dict(), "holdings":[],
        "trades":[{"symbol":"600001", "buy_date":"2026-01-05", "sell_date":"2026-01-06"},
                  {"symbol":"600000", "buy_date":"2026-01-20", "sell_date":"2026-01-22", "buy_price":12,"sell_price":12.1}]}
    stock={"name":"测试股份", "daily":bars,"source":"akshare","note":"原始快照"}
    return cfg, result, stock


class UniverseTests(unittest.TestCase):
    def test_more_than_300_and_all_not_truncated(self):
        stocks=[{"symbol":str(600000+i),"name":str(i)} for i in range(1400)]
        self.assertEqual(len(select_stocks(stocks,300)),300)
        self.assertEqual(len(select_stocks(stocks,500)),500)
        self.assertEqual(len(select_stocks(stocks,1000)),1000)
        self.assertEqual(len(select_stocks(stocks,0)),1400)
        self.assertEqual(select_stocks(stocks,500),select_stocks(stocks[::-1],500))
        self.assertEqual(len(select_stocks(stocks+stocks,0)),1400)

    def test_expanded_config_and_zero_means_all(self):
        symbols=','.join(str(600000+i) for i in range(1000))
        c=Config.parse({"symbols":symbols,"universe_limit":0,"universe_scope":"all"})
        self.assertEqual(len(c.symbols.split(',')),1000)
        self.assertEqual(c.universe_limit,0)
        for p in ({"universe_limit":-1},{"universe_limit":8001},{"universe_scope":"unknown"}):
            with self.assertRaises(ValueError):Config.parse(p)


class ChartTests(unittest.TestCase):
    def test_ohlcv_and_global_trade_index_preserved(self):
        _,result,stock=sample()
        chart=build_chart(result,'600000',stock)
        self.assertEqual(chart['trades'][0]['index'],1)
        self.assertEqual(chart['trades'][0]['buy_price'],12)
        for key in ('date','open','high','low','close','volume'):
            self.assertEqual(chart['bars'][20][key],stock['daily'][20][key])

    def test_ma_uses_available_past_and_current_closes(self):
        _,result,stock=sample()
        before=build_chart(result,'600000',stock)
        self.assertAlmostEqual(before['bars'][20]['ma_fast'],mean(b['close'] for b in stock['daily'][18:21]),places=4)
        self.assertIsNone(before['bars'][0]['ma20'])
        stock['daily'][21]['close']=1000
        after=build_chart(result,'600000',stock)
        self.assertEqual(before['bars'][:21],after['bars'][:21])

    def test_snapshot_survives_input_changes(self):
        _,result,stock=sample()
        result['trades']=result['trades'][1:]
        dataset={'source':'demo','stocks':{'600000':stock}}
        with TemporaryDirectory() as folder,patch('weipan.charts.CHARTS',Path(folder)):
            save_charts(result['id'],dataset,result)
            original=stock['daily'][0]['close']
            stock['daily'][0]['close']=999
            with patch('weipan.charts.load_data',side_effect=AssertionError('must use snapshot')):
                chart=get_chart(result,'600000')
            self.assertEqual(chart['bars'][0]['close'],original)
            self.assertEqual(chart['source'],'demo')

    def test_rejects_stocks_outside_run(self):
        _,result,_=sample()
        with self.assertRaisesRegex(ValueError,'不在本次'):
            get_chart(result,'000001')

    def test_old_demo_is_not_rebuilt_from_different_generator(self):
        _,result,_=sample()
        with TemporaryDirectory() as folder,patch('weipan.charts.CHARTS',Path(folder)):
            with self.assertRaisesRegex(ValueError,'旧演示记录'):
                get_chart(result,'600000')

    def test_original_legacy_cache_preferred_to_redownload(self):
        cfg,result,stock=sample();result['config']['source']='akshare'
        begin=(date.fromisoformat(cfg.start_date)-timedelta(days=cfg.warmup*2+40)).isoformat()
        request={'kind':'stock','symbol':'600000','start':begin,'end':cfg.end_date,'minutes':False,'minute_start':cfg.start_date,'market_start':cfg.start_date}
        key=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()[:24]
        with TemporaryDirectory() as folder,patch('weipan.charts.CHARTS',Path(folder)/'charts'),patch('weipan.charts.CACHE',Path(folder)/'cache'):
            atomic_json(Path(folder)/'cache'/(key+'.json'),stock)
            with patch('weipan.charts.load_data',side_effect=AssertionError('must use original cache')):
                chart=get_chart(result,'600000')
            self.assertEqual(chart['source'],'akshare')
            self.assertIn('原始行情缓存',chart['note'])

    def test_chart_ends_at_run_cutoff(self):
        _,result,stock=sample()
        result['config']['end_date']='2026-01-25'
        chart=build_chart(result,'600000',stock)
        self.assertEqual(chart['bars'][-1]['date'],'2026-01-25')


if __name__=='__main__':unittest.main()
