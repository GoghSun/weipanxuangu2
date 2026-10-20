"""Controlled engine output for the desktop timing review; never saved as a user run."""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_entry_filters import entry_fixture
from weipan.backtest import run_backtest
from weipan.charts import build_chart


def make_fixture():
    bars, cfg, data, minutes = entry_fixture()
    cfg.end_date = bars[61]['date']
    bars[61].update(open=10.1, high=10.2, low=10, close=10.1, volume=192, prev_close=10.4)
    for bar in copy.deepcopy(minutes):
        bar.update(datetime=cfg.end_date + bar['datetime'][10:], open=10.1, high=10.2, low=10, close=10.1)
        minutes.append(bar)
    result = run_backtest(data, cfg)
    result.update(id='aabbccddeeff', kind='backtest', created_at='2026-10-19T11:00:00')
    assert len(result['trades']) == 1
    trade = result['trades'][0]
    assert trade['signal_time'][:10] == trade['buy_date']
    chart = build_chart(result, '600000', {'daily': bars, 'source': 'demo',
        'name': '自动化测试合成股票', 'note': '仅用于自动化验证的合成行情'})
    return {'result': result, 'chart': chart}


if __name__ == '__main__':
    print(json.dumps(make_fixture(), ensure_ascii=False))
