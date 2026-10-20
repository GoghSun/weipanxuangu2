"""Run-specific daily candles: keep executed trades and their source bars together."""
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import hashlib
import json
from .config import Config
from .data import ROOT, CACHE, atomic_json, demo_data, load_data

CHARTS = ROOT / "data" / "charts"


def save_charts(run_id, dataset, result):
    symbols = {t["symbol"] for t in result.get("trades", []) + result.get("holdings", [])}
    for symbol in symbols:
        stock = dataset["stocks"][symbol]
        atomic_json(CHARTS / run_id / (symbol + ".json"), {"name": stock["name"], "daily": stock["daily"],
            "source": dataset["source"], "note": "本次回测的原始日线快照，未复权。"})


def build_chart(result, symbol, stock):
    cfg = Config.from_saved(result["config"])
    bars = sorted([b for b in stock["daily"] if b["date"] <= cfg.end_date], key=lambda b: b["date"])
    if not bars:
        raise ValueError("该股票没有可显示的日线")
    prefix = [0.0]
    output = []
    for i, b in enumerate(bars):
        prefix.append(prefix[-1] + b["close"])
        row = {k:b[k] for k in ("date", "open", "high", "low", "close", "volume")}
        for key, length in (("ma_fast", cfg.ma_fast), ("ma_slow", cfg.ma_slow), ("ma20", 20)):
            row[key] = round((prefix[i+1] - prefix[i+1-length]) / length, 4) if i+1 >= length else None
        output.append(row)
    trades = [{**t, "index": i} for i, t in enumerate(result.get("trades", [])) if t["symbol"] == symbol]
    return {"run_id": result["id"], "symbol": symbol, "name": stock["name"], "source": stock["source"],
        "note": stock["note"], "bars": output, "trades": trades,
        "holdings": [h for h in result.get("holdings", []) if h["symbol"] == symbol],
        "ma_fast": cfg.ma_fast, "ma_slow": cfg.ma_slow, "stop_loss": cfg.stop_loss,
        "mode": cfg.mode, "strategy_version": cfg.strategy_version,
        "start_date": cfg.start_date, "end_date": cfg.end_date}


def get_chart(result, symbol):
    cfg = Config.from_saved(result["config"])
    if symbol not in cfg.symbols.split(","):
        raise ValueError("这只股票不在本次回测股票池内")
    path = CHARTS / result["id"] / (symbol + ".json")
    if path.exists():
        return build_chart(result, symbol, json.loads(path.read_text(encoding="utf-8")))
    stock = None
    if cfg.source == "akshare":
        begin = (date.fromisoformat(cfg.start_date) - timedelta(days=cfg.warmup * 2 + 40)).isoformat()
        # Legacy runs did not store chart snapshots. Their original unexpired/expired cache
        # remains valid for reviewing that saved run; do not replace it with today's candles.
        for minute_mode in (cfg.mode == "minute", False):
            request = {"kind":"stock", "symbol":symbol, "start":begin, "end":cfg.end_date,
                "minutes":minute_mode, "minute_start":cfg.end_date if result.get("kind") == "screen" else cfg.start_date,
                "market_start":cfg.start_date}
            key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:24]
            cache = CACHE / (key + ".json")
            if cache.exists():
                stock = json.loads(cache.read_text(encoding="utf-8"))
                stock.update(source="akshare", note="从该历史记录的原始行情缓存恢复，未复权。")
                break
        if stock is None:
            dataset = load_data(replace(cfg, symbols=symbol, mode="daily", entry_filters="off"), price_only=True)
            stock = dataset["stocks"][symbol]
            stock.update(source="akshare", note="旧记录没有原始行情快照；此日线为重新下载的历史行情，供应商修订可能使其与原回测略有差异。")
    else:
        # Never reconstruct a legacy synthetic run from a newer generator and call it original.
        raise ValueError("该旧演示记录未保存日 K 快照，请重新运行一次演示回测后查看。真实行情记录可以从原缓存恢复。")
    atomic_json(path, {k:stock[k] for k in ("name", "daily", "source", "note")})
    return build_chart(result, symbol, stock)
