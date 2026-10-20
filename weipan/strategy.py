from decimal import Decimal, ROUND_HALF_UP
from statistics import mean


def limit_rate(symbol, day, name=""):
    if "ST" in name.upper():
        return 0.05
    if symbol.startswith("68") or (symbol.startswith("30") and day >= "2020-08-24"):
        return 0.20
    return 0.10


def price_limit(previous_close, rate):
    return float((Decimal(str(previous_close)) * (Decimal("1") + Decimal(str(rate)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def entry_checks(bars, cfg):
    last = bars[-1]
    ma = mean(b["close"] for b in bars[-cfg.entry_ma_days:])
    # The upper-shadow filter may use a different baseline than the entry volume rule.
    base = mean(b["volume"] for b in bars[-cfg.burst_volume_days-1:-1])
    volume_ratio = last["volume"] / base if base > 0 else None
    span = Decimal(str(last["high"])) - Decimal(str(last["low"]))
    shadow = Decimal(str(last["high"])) - max(Decimal(str(last["open"])), Decimal(str(last["close"])))
    shadow_pct = max(Decimal(0), shadow) / span * 100 if span > 0 else Decimal(0)
    price_1430, previous = last.get("price_1430"), last.get("prev_close")
    gain = (Decimal(str(price_1430)) / Decimal(str(previous)) - 1) * 100 if price_1430 is not None and previous and previous > 0 else None
    burst = base > 0 and Decimal(str(last["volume"])) >= Decimal(str(base)) * Decimal(str(cfg.burst_volume_ratio))
    checks = {"above_entry_ma": last["close"] > ma,
              "no_burst_shadow": base > 0 and not (burst and shadow_pct >= Decimal(str(cfg.upper_shadow_pct))),
              "gain_1430": gain is not None and gain > Decimal(str(cfg.gain_1430_min))}
    metrics = {"entry_ma": ma, "day_volume_ratio": volume_ratio, "upper_shadow_pct": float(shadow_pct),
               "gain_1430": float(gain) if gain is not None else None,
               "price_1430": price_1430, "prev_close": previous}
    labels = {"above_entry_ma": f"价格未站上 MA{cfg.entry_ma_days}",
              "no_burst_shadow": "爆量上影线" if base > 0 else "缺少有效的前期成交量",
              "gain_1430": f"14:30 涨幅未超过 {cfg.gain_1430_min:g}%" if gain is not None else "缺少 14:30 行情或前收盘价"}
    return checks, metrics, labels


def entry_description(cfg):
    return (f"当日买入过滤：{cfg.selection_time} 价格 > MA{cfg.entry_ma_days}（含当时价格）；"
            f"截至该时点累计成交量 ≥ 前 {cfg.burst_volume_days} 个完整交易日均量的 {cfg.burst_volume_ratio:g} 倍，"
            f"且上影线 / 当日振幅 ≥ {cfg.upper_shadow_pct:g}% 时排除；"
            f"14:30 的 5 分钟 K 线收盘价相对前收盘价涨幅严格 > {cfg.gain_1430_min:g}%。"
            "不读取选股之后的全天成交量、最高价和收盘价来筛选。")


def evaluate(symbol, name, bars, cfg):
    row = {"symbol": symbol, "name": name, "eligible": False, "checks": {}, "reason": "历史数据不足", "score": 0}
    if len(bars) < cfg.warmup:
        return row
    last = bars[-1]
    cap = last.get("market_cap")
    cap_yi = cap / 1e8 if cap is not None else None
    window = bars[-cfg.sideways_days:]
    low = min(b["low"] for b in window)
    amplitude = (max(b["high"] for b in window) / low - 1) * 100 if low else 999
    previous_volumes = [Decimal(str(b["volume"])) for b in bars[-cfg.volume_base-1:-1]]
    base = sum(previous_volumes) / len(previous_volumes)
    current_volume = Decimal(str(last["volume"]))
    ratio = float(current_volume / base) if base > 0 else 0
    last_limit = None
    limit_indices = [i for i in range(1, len(bars)) if cfg.limit_type == "touch" or bars[i].get("complete", True)]
    for i in limit_indices[-cfg.limit_lookback:]:
        b = bars[i]
        previous = b.get("prev_close", bars[i-1]["close"])
        target = price_limit(previous, limit_rate(symbol, b["date"], name))
        if b["close" if cfg.limit_type == "close" else "high"] >= target - 0.001:
            last_limit = b["date"]
    checks = {
        "market_cap": cap_yi is not None and cfg.market_cap_min <= cap_yi <= cfg.market_cap_max,
        "limit_up": last_limit is not None,
        "sideways": amplitude <= cfg.sideways_range,
        "volume": base > 0 and current_volume * len(previous_volumes) > sum(previous_volumes) * Decimal(str(cfg.volume_ratio)),
        "tradable": "ST" not in name.upper() and "退" not in name and last["volume"] > 0,
    }
    labels = {"market_cap": "市值不符" if cap is not None else "缺少历史市值", "limit_up": "无涨停记录", "sideways": "横盘振幅超限", "volume": "量能不足", "tradable": "ST / 退市 / 停牌"}
    if cfg.entry_filters == "on":
        added_checks, metrics, added_labels = entry_checks(bars, cfg)
        checks.update(added_checks)
        labels.update(added_labels)
        row.update(metrics)
    row.update({"date": last["date"], "price": last["close"], "market_cap": cap_yi,
        "signal_time": last["date"] + " " + cfg.selection_time + ":00",
        "current_volume": last["volume"], "base_volume": float(base),
        "amplitude": round(amplitude, 3), "volume_ratio": round(ratio, 3), "last_limit": last_limit,
        "ma_fast": mean(b["close"] for b in bars[-cfg.ma_fast:]), "ma_slow": mean(b["close"] for b in bars[-cfg.ma_slow:]),
        "checks": checks, "eligible": all(checks.values()), "score": ratio,
        "reason": "满足全部条件" if all(checks.values()) else " / ".join(labels[k] for k, v in checks.items() if not v)})
    return row


def profit_cross(bars, cfg):
    if len(bars) < cfg.ma_slow + 1:
        return False
    closes = [b["close"] for b in bars]
    previous_fast = mean(closes[-cfg.ma_fast-1:-1])
    previous_slow = mean(closes[-cfg.ma_slow-1:-1])
    return previous_fast >= previous_slow and mean(closes[-cfg.ma_fast:]) < mean(closes[-cfg.ma_slow:])


def partial_bar(previous, minutes, day, selection_time, reference_close=None):
    selected = sorted([b for b in minutes if b["datetime"][:10] == day and b["datetime"][11:16] <= selection_time], key=lambda b: b["datetime"])
    # Require a bar exactly at the decision time; stale or missing bars cannot form a tail signal.
    if not selected or selected[-1]["datetime"][11:16] != selection_time:
        return None
    close = selected[-1]["close"]
    return {"date": day, "complete": False, "open": selected[0]["open"], "high": max(b["high"] for b in selected),
        "low": min(b["low"] for b in selected), "close": close, "volume": sum(b["volume"] for b in selected),
        "prev_close": reference_close if reference_close is not None else previous["close"],
        "price_1430": next((b["close"] for b in selected if b["datetime"][11:16] == "14:30"), None),
        "market_cap": previous["market_cap"] / previous["close"] * close if previous.get("market_cap") else None}
