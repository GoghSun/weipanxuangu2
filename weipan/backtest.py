from math import sqrt
from datetime import datetime, timedelta
from statistics import mean, pstdev
from .strategy import evaluate, profit_cross, price_limit, limit_rate, partial_bar


def fees(value, cfg, sell=False):
    return max(cfg.min_commission, value * cfg.commission / 100) + value * cfg.transfer_fee / 100 + (value * cfg.stamp_tax / 100 if sell else 0)


def index_minutes(symbol, item, cfg):
    required = {f"{h:02d}:{m:02d}" for h in (9, 10, 11, 13, 14, 15) for m in range(0, 60, 5)
                if "09:35" <= f"{h:02d}:{m:02d}" <= "11:30" or "13:05" <= f"{h:02d}:{m:02d}" <= "15:00"}
    grouped = {}
    for b in sorted(item.get("minutes", []), key=lambda b: b["datetime"]):
        grouped.setdefault(b["datetime"][:10], []).append(b)
    for daily in item["daily"]:
        day = daily["date"]
        if not cfg.start_date <= day <= cfg.end_date or daily["volume"] <= 0:
            continue
        actual = {b["datetime"][11:16] for b in grouped.get(day, [])}
        if actual != required or len(grouped.get(day, [])) != 48:
            raise ValueError(f"{symbol} 在 {day} 缺少完整的 5 分钟行情或时点重复，不能进行当日尾盘回测。请缩短至有分钟数据的区间；不会改用次日开盘成交。")
    return grouped


def run_backtest(dataset, cfg, progress=lambda *_: None):
    cfg.validate_execution()
    stocks = dataset["stocks"]
    index = {s: {b["date"]: i for i, b in enumerate(v["daily"])} for s, v in stocks.items()}
    days = sorted({d for m in index.values() for d in m if cfg.start_date <= d <= cfg.end_date})
    if not days:
        raise ValueError("所选区间没有交易日数据")
    minute_index = {s: index_minutes(s, item, cfg) for s, item in stocks.items()}

    cash = cfg.initial_cash
    positions, trades, curve = {}, [], []
    skipped, latest_signals = [], []
    last_prices, benchmark_start = {}, {}
    peak = cfg.initial_cash
    total_fees = 0.0

    def history(symbol, day):
        i = index[symbol].get(day)
        return stocks[symbol]["daily"][:i+1] if i is not None else []

    def skip(symbol, day, reason):
        skipped.append({"symbol": symbol, "date": day, "reason": reason})

    def execute_sell(symbol, bar, target, day, di, reason):
        nonlocal cash, total_fees
        p = positions[symbol]
        if p["buy_date"] == day:
            return False
        h = history(symbol, day)
        prev = h[-1].get("prev_close", h[-2]["close"] if len(h) > 1 else bar["open"])
        lower = price_limit(prev, -limit_rate(symbol, day, stocks[symbol]["name"]))
        price = round(max(bar["low"], target * (1 - cfg.slippage / 100)), 2)
        if bar["volume"] <= 0 or price <= lower + 0.001:
            skip(symbol, day, "跌停或无成交量，延后卖出")
            return False
        value = price * p["quantity"]
        cost = fees(value, cfg, True)
        pnl = value - cost - p["cost"]
        if reason == "均线下穿止盈" and pnl <= 0:
            return False
        cash += value - cost
        total_fees += cost
        trades.append({"symbol": symbol, "name": p["name"], "buy_date": p["buy_date"], "sell_date": day,
            "signal_time": p["signal_time"], "buy_window_start": p["buy_window_start"],
            "buy_time": p["buy_time"], "sell_time": bar["datetime"],
            "sell_window_start": (datetime.fromisoformat(bar["datetime"]) - timedelta(minutes=5)).isoformat(sep=" "),
            "buy_price": p["buy_price"], "sell_price": price, "quantity": p["quantity"], "pnl": round(pnl, 2),
            "return_pct": round(pnl / p["cost"] * 100, 3), "holding_days": di-p["buy_index"],
            "fees": round(p["buy_fee"] + cost, 2), "reason": reason,
            "entry_signal": p.get("entry_signal")})
        del positions[symbol]
        return True

    def check_stop(symbol, bars, day, di):
        for b in bars:
            if symbol not in positions or positions[symbol]["buy_date"] == day:
                return
            stop = positions[symbol]["buy_price"] * (1 - cfg.stop_loss / 100)
            if b["low"] <= stop:
                if execute_sell(symbol, b, min(b["open"], stop), day, di, "止损"):
                    return

    def execute_buys(candidates, fills, day, di):
        nonlocal cash, total_fees
        equity = cash + sum(p["quantity"] * last_prices.get(s, p["buy_price"]) for s, p in positions.items())
        allocation = equity / cfg.max_positions
        for signal in candidates:
            s = signal["symbol"]
            if s in positions or len(positions) >= cfg.max_positions or s not in fills:
                continue
            b = fills[s]
            if signal.get("date") != day or b["datetime"][:10] != day or b["datetime"][11:16] <= cfg.selection_time:
                raise ValueError("买入信号与成交时点不一致：仅允许信号当日选股时点之后成交")
            h = history(s, day)
            prev = h[-1].get("prev_close", h[-2]["close"] if len(h) > 1 else b["open"])
            upper = price_limit(prev, limit_rate(s, day, stocks[s]["name"]))
            price = round(min(b["high"], b["open"] * (1 + cfg.slippage / 100)), 2)
            if b["volume"] <= 0 or price >= upper - 0.001:
                skip(s, day, "涨停或无成交量，放弃买入")
                continue
            budget = min(cash, allocation)
            quantity = int(budget / price / 100) * 100
            # STAR first orders require >= 200 shares; use 100-share increments throughout.
            minimum = 200 if s.startswith("68") else 100
            while quantity >= minimum and quantity * price + fees(quantity * price, cfg) > budget:
                quantity -= 100
            if quantity < minimum:
                continue
            cost = fees(quantity * price, cfg)
            cash -= quantity * price + cost
            total_fees += cost
            positions[s] = {"symbol": s, "name": stocks[s]["name"], "buy_date": day,
                "signal_time": day + " " + cfg.selection_time + ":00",
                "buy_window_start": (datetime.fromisoformat(b["datetime"]) - timedelta(minutes=5)).isoformat(sep=" "),
                "buy_time": b["datetime"], "buy_price": price,
                "quantity": quantity, "cost": quantity * price + cost, "buy_fee": cost, "buy_index": di}
            positions[s]["entry_signal"] = {k: signal.get(k) for k in (
                "date", "price", "signal_time", "current_volume", "base_volume", "volume_ratio",
                "entry_ma", "day_volume_ratio", "upper_shadow_pct", "gain_1430", "price_1430", "prev_close")}
            positions[s]["entry_signal"]["selection_time"] = cfg.selection_time

    for di, day in enumerate(days):
        if di % 10 == 0:
            progress(70 + int(di / len(days) * 28), f"回放交易日 {day}")
        today = {s: history(s, day)[-1] for s in stocks if day in index[s]}
        signals, fills = [], {}
        for s in list(positions):
            morning = [b for b in minute_index[s].get(day, []) if b["datetime"][11:16] <= cfg.selection_time]
            check_stop(s, morning, day, di)
        for s in today:
            h = history(s, day)
            minute_bars = minute_index[s].get(day, [])
            if len(h) < 2:
                continue
            partial = partial_bar(h[-2], minute_bars, day, cfg.selection_time, h[-1].get("prev_close"))
            remaining = [b for b in minute_bars if b["datetime"][11:16] > cfg.selection_time]
            if partial is None or not remaining:
                continue
            signal_h = h[:-1] + [partial]
            signals.append(evaluate(s, stocks[s]["name"], signal_h, cfg))
            fills[s] = remaining[0]
            last_prices[s] = partial["close"]
            if s in positions and profit_cross(signal_h, cfg):
                execute_sell(s, remaining[0], remaining[0]["open"], day, di, "均线下穿止盈")
        candidates = sorted([s for s in signals if s["eligible"]], key=lambda r: (-r["score"], r["symbol"]))
        execute_buys(candidates, fills, day, di)
        for s in list(positions):
            remaining = [b for b in minute_index[s].get(day, []) if b["datetime"][11:16] > cfg.selection_time]
            check_stop(s, remaining, day, di)
        latest_signals = sorted(signals, key=lambda s: (not s["eligible"], -sum(s["checks"].values()), -s["score"]))
        for s, b in today.items():
            last_prices[s] = b["close"]
            benchmark_start.setdefault(s, b["close"])
        market_value = sum(p["quantity"] * last_prices.get(s, p["buy_price"]) for s, p in positions.items())
        equity = cash + market_value
        peak = max(peak, equity)
        curve.append({"date": day, "equity": round(equity, 2), "strategy": round(equity/cfg.initial_cash, 6),
            "benchmark": round(mean(last_prices[s]/p for s, p in benchmark_start.items()), 6),
            "drawdown": round((equity/peak-1)*100, 4), "cash": round(cash, 2), "positions": len(positions)})

    final_value = curve[-1]["equity"]
    daily_returns = [curve[0]["equity"] / cfg.initial_cash - 1] + [curve[i]["equity"]/curve[i-1]["equity"]-1 for i in range(1, len(curve))]
    std = pstdev(daily_returns) if len(daily_returns) > 1 else 0
    winners = [t for t in trades if t["pnl"] > 0]
    losers = [t for t in trades if t["pnl"] < 0]
    holding_rows = []
    for s, p in positions.items():
        holding_rows.append({**p, "price": last_prices[s], "unrealized_pnl": round(last_prices[s]*p["quantity"]-p["cost"], 2)})
    metrics = {"total_return": (final_value/cfg.initial_cash-1)*100,
        "annual_return": ((final_value/cfg.initial_cash)**(252/len(days))-1)*100,
        "max_drawdown": min(p["drawdown"] for p in curve), "win_rate": len(winners)/len(trades)*100 if trades else None,
        "trade_count": len(trades), "sharpe": mean(daily_returns)/std*sqrt(252) if std else None,
        "profit_factor": sum(t["pnl"] for t in winners)/abs(sum(t["pnl"] for t in losers)) if losers else None,
        "avg_holding_days": mean(t["holding_days"] for t in trades) if trades else None,
        "final_equity": final_value, "cash": cash, "fees": total_fees, "open_positions": len(positions),
        "benchmark_return": (curve[-1]["benchmark"]-1)*100, "trading_days": len(days)}
    warnings = list(dataset.get("warnings", []))
    warnings += ["固定股票池含存活偏差；收益使用未复权价格，不计分红送转。遇除权区间需单独核验。", "涨跌停按板块规则和前收盘价计算，缺少历史 ST、特别限价及停复牌状态；不适合据此判断真实成交。", "成交假设不模拟盘口排队和冲击成本；期末持仓按最后收盘价估值，不强制卖出。",
                 f"入场放量：当日截至 {cfg.selection_time} 的累计成交量 / 此前 {cfg.volume_base} 个完整交易日均量，严格大于 {cfg.volume_ratio:g} 倍才通过；不含当日、不外推全天量。"]
    warnings.append("当日尾盘回放：选股后立即在同一交易日下一根 5 分钟 K 线开盘模拟成交，不保留次日买单。成交以 5 分钟时段展示，结束时间不是精确逐笔成交时间；市值采用前一交易日总股本估算。")
    if cfg.entry_filters == "on":
        from .strategy import entry_description
        warnings.append(entry_description(cfg))
    return {"config": cfg.to_dict(), "metrics": metrics, "curve": curve, "trades": trades,
        "holdings": holding_rows, "signals": latest_signals, "skipped": skipped[-300:],
        "warnings": warnings, "data": {k: v for k, v in dataset.items() if k != "stocks"},
        "screen_date": days[-1], "universe_count": len(stocks)}
