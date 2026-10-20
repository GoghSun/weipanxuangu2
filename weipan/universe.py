"""Build repeatable research universes without truncating at 300 securities."""
import hashlib
import json
import time
from .config import PRESET_PATH
from .data import CACHE, atomic_json, run_fetch


def select_stocks(stocks, limit):
    unique = {r["symbol"]: r for r in stocks}
    ranked = sorted(unique.values(), key=lambda r: hashlib.sha256(("weipan-pool-v1:" + r["symbol"]).encode()).digest())
    chosen = ranked[:limit] if limit else ranked
    return sorted(chosen, key=lambda r: r["symbol"])


def load_universe(cfg, progress=lambda *_: None):
    path = CACHE / "universe.json"
    request = {"kind": "universe", "min": cfg.market_cap_min, "max": cfg.market_cap_max, "scope": cfg.universe_scope}
    catalog = None
    if path.exists() and time.time() - path.stat().st_mtime < 3600:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("request") == request:
            catalog = saved
    if catalog is None:
        progress(10, "读取沪深 A 股列表与总市值，主数据源不可用时尝试备用接口…")
        catalog = {**run_fetch(request, timeout=240), "request": request}
        atomic_json(path, catalog)
    stocks = select_stocks(catalog["stocks"], cfg.universe_limit)
    if not stocks:
        raise ValueError("未找到符合范围的股票，请调整市值区间")
    result = {"stocks": stocks, "total_count": len(catalog["stocks"]), "selected_count": len(stocks),
              "requested_limit": cfg.universe_limit, "scope": cfg.universe_scope,
              "source": catalog["source"], "as_of": catalog["as_of"],
              "note": "按股票代码固定抽样，选择不参考回测收益。当前证券池存在存活偏差；按当前市值导入另有历史选择偏差。"}
    atomic_json(PRESET_PATH, result)
    return result
