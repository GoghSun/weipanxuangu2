"""Local fixture data and process-isolated AKShare downloads with an on-disk cache."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
import hashlib
import json
import math
import random
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def demo_data(cfg, progress=lambda *_: None):
    start = date.fromisoformat(cfg.start_date) - timedelta(days=cfg.warmup * 2 + 40)
    end = date.fromisoformat(cfg.end_date)
    days = []
    while start <= end:
        if start.weekday() < 5:
            days.append(start.isoformat())
        start += timedelta(days=1)
    stocks = {}
    slots = [f"{h:02d}:{m:02d}:00" for h in (9, 10, 11, 13, 14, 15) for m in range(0, 60, 5)
             if "09:35" <= f"{h:02d}:{m:02d}" <= "11:30" or "13:05" <= f"{h:02d}:{m:02d}" <= "15:00"]
    for si, symbol in enumerate(cfg.symbols.split(",")):
        seed = int(symbol) % 97
        base_price = 6 + seed * .3
        shares = (55 + (seed * 13) % 180) * 1e8 / base_price
        def sample_close(index):
            phase = (index + seed * 5) % 42
            jitter = random.Random(int(symbol) * 100000 + index).uniform(-.002, .002)
            direction = .012 if seed % 3 else -.018
            if phase < 4:
                factor = 1 + jitter
            elif phase == 4:
                from .strategy import price_limit, limit_rate
                return price_limit(sample_close(index-1), limit_rate(symbol, "2026-01-01"))
            elif phase <= 19:
                factor = 1.1 + jitter
            elif phase <= 22:
                factor = 1.1 * (1 + direction * (phase - 19)) + jitter
            else:
                factor = 1.1 * (1 + direction * 3)
                factor += (1-factor) * (phase-22)/20 + jitter
            return round(base_price * factor, 2)
        daily, minutes = [], []
        for di, day in enumerate(days):
            day_value = date.fromisoformat(day)
            distance = (day_value - date(2000, 1, 3)).days
            market_index = distance // 7 * 5 + day_value.weekday()
            phase = (market_index + seed * 5) % 42
            rng = random.Random(int(symbol) * 100000 + market_index)
            previous, price = sample_close(market_index-1), sample_close(market_index)
            opening = round(previous * (1 + rng.gauss(0, .002)), 2)
            high = round(max(opening, price) * (1 + rng.uniform(.002, .008)), 2)
            low = round(min(opening, price) * (1 - rng.uniform(.002, .008)), 2)
            if phase == 4:
                high = price
            vol = int((120000 + seed * 2000) * rng.uniform(.8, 1.1) * (2.6 if phase in (18, 19) else 1))
            daily.append({"date": day, "open": opening, "high": high, "low": low, "close": price,
                          "volume": vol, "market_cap": price * shares, "prev_close": previous})
            if cfg.mode == "minute" and day >= cfg.start_date:
                prev_minute = opening
                remainder = vol
                for mi, slot in enumerate(slots):
                    close = round(opening + (price-opening)*(mi+1)/48, 2)
                    volume = vol // 48 if mi < 47 else remainder
                    remainder -= volume
                    minutes.append({"datetime": day + " " + slot, "open": prev_minute, "close": close,
                                    "high": max(prev_minute, close), "low": min(prev_minute, close), "volume": volume})
                    prev_minute = close
                group = minutes[-48:]
                group[10]["high"] = high
                group[14]["low"] = low
        stocks[symbol] = {"name": f"模拟股票 {si+1:02d}", "daily": daily, "minutes": minutes}
    progress(68, "模拟行情已生成，准备运行策略")
    return {"stocks": stocks, "source": "demo", "label": "合成演示数据", "downloaded_at": datetime.now().isoformat(timespec="seconds"),
            "warnings": ["当前为固定随机种子生成的合成行情，股票名称和价格均为模拟，仅验证策略流程，不代表真实 A 股收益。模拟日历只排除周末。"]}


def run_fetch(request, timeout=150):
    try:
        result = subprocess.run([sys.executable, "-m", "weipan.fetch"], input=json.dumps(request),
            capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired:
        raise ValueError("AKShare 数据请求超时，请稍后重试；已完成的下载会保留在缓存中") from None
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError("AKShare 下载进程异常，请检查依赖安装和网络连接") from None
    if payload.get("error"):
        raise ValueError(payload["error"])
    return payload


def load_data(cfg, progress=lambda *_: None, for_screen=False, *, price_only=False):
    if not price_only:
        cfg.validate_execution()
    if cfg.source == "demo":
        if for_screen:
            from dataclasses import replace
            return demo_data(replace(cfg, mode="minute", start_date=cfg.end_date), progress)
        return demo_data(cfg, progress)
    begin = (date.fromisoformat(cfg.start_date) - timedelta(days=cfg.warmup * 2 + 40)).isoformat()
    symbols = cfg.symbols.split(",")
    stocks, failures, warnings = {}, [], []
    cache_hits = 0
    names = {}
    catalog_path = CACHE / "universe.json"
    if catalog_path.exists() and time.time() - catalog_path.stat().st_mtime < 86400:
        try:
            names = {r["symbol"]: r["name"] for r in json.loads(catalog_path.read_text(encoding="utf-8"))["stocks"]}
        except (ValueError, KeyError):
            pass

    def fetch(symbol):
        request = {"kind": "stock", "symbol": symbol, "start": begin, "end": cfg.end_date,
            "minutes": cfg.mode == "minute" or for_screen, "minute_start": cfg.end_date if for_screen else cfg.start_date, "market_start": cfg.start_date}
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:24]
        path = CACHE / (key + ".json")
        # Today's data refreshes every minute. Completed history refreshes daily.
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        today = now.date().isoformat()
        close_refresh = now.replace(hour=15, minute=10, second=0, microsecond=0)
        live = cfg.end_date > today or (cfg.end_date == today and now < close_refresh)
        ttl = 60 if live else (86400 * 30 if cfg.end_date < today else 86400)
        refresh_close = cfg.end_date == today and now >= close_refresh and path.exists() and path.stat().st_mtime < close_refresh.timestamp()
        if path.exists() and not refresh_close and time.time() - path.stat().st_mtime < ttl:
            return symbol, json.loads(path.read_text(encoding="utf-8")), True
        if symbol in names:
            request["name"] = names[symbol]
        try:
            value = run_fetch(request)
        except ValueError as exc:
            if any(marker in str(exc) for marker in ("尚未安装", "缺少", "不一致", "无重合", "包含无效")):
                raise
            value = run_fetch(request)  # one bounded retry for transient provider failures
        atomic_json(path, value)
        return symbol, value, False

    # Check connectivity with one symbol before queueing hundreds of doomed requests.
    progress(5, f"检查 AKShare 行情与估值接口：{symbols[0]}")
    s, value, hit = fetch(symbols[0])
    if cfg.mode == "minute" and not for_screen:
        # Detect unsupported historical intervals before downloading a large stock pool.
        from .backtest import index_minutes
        index_minutes(s, value, cfg)
    stocks[s] = value
    warnings.extend(value.pop("warnings", []))
    cache_hits += int(hit)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, s): s for s in symbols[1:]}
        for i, future in enumerate(as_completed(futures)):
            try:
                s, value, hit = future.result()
                stocks[s] = value
                warnings.extend(value.pop("warnings", []))
                cache_hits += int(hit)
            except Exception as exc:
                failures.append(f"{futures[future]}：{str(exc)[:240]}")
            progress(5 + int(62 * (i+2) / len(symbols)), f"已加载 {i+2}/{len(symbols)} 只股票，失败 {len(failures)} 只")
    if failures:
        raise ValueError("股票池数据不完整，本次运行未生成收益结果。" + "；".join(failures[:5]) + (f"；共 {len(failures)} 只失败" if len(failures) > 5 else ""))
    return {"stocks": stocks, "source": "akshare", "label": "AKShare · 真实行情与历史市值", "downloaded_at": datetime.now().isoformat(timespec="seconds"),
            "cache_hits": cache_hits, "requested_count": len(symbols), "warnings": sorted(set(warnings))}


def screen(dataset, cfg):
    cfg.validate_execution()
    from .strategy import evaluate, partial_bar, entry_description
    signals, omitted = [], []
    for s, stock in dataset["stocks"].items():
        daily = [b for b in stock["daily"] if b["date"] < cfg.end_date]
        minutes = stock.get("minutes", [])
        reference = next((b.get("prev_close") for b in stock["daily"] if b["date"] == cfg.end_date), None)
        bar = partial_bar(daily[-1], minutes, cfg.end_date, cfg.selection_time, reference) if daily else None
        slots = {b["datetime"][11:16] for b in minutes if b["datetime"][:10] == cfg.end_date and b["datetime"][11:16] <= cfg.selection_time}
        expected = 24 + (int(cfg.selection_time[:2])-13)*12 + int(cfg.selection_time[3:])//5
        if bar is None or len(slots) != expected or (cfg.entry_filters == "on" and bar.get("price_1430") is None):
            omitted.append(s)
        else:
            signals.append(evaluate(s, stock["name"], daily + [bar], cfg))
    if omitted:
        raise ValueError(f"{cfg.end_date} {cfg.selection_time} 缺少 {len(omitted)} 只股票的完整盘中行情（如 {', '.join(omitted[:5])}）。请确认是交易日且选股时间已到，或检查 AKShare 分钟数据覆盖。")
    signals.sort(key=lambda s: (not s["eligible"], -sum(s["checks"].values()), -s["score"]))
    return {"config": cfg.to_dict(), "signals": signals, "screen_date": cfg.end_date,
            "universe_count": len(signals), "data": {k: v for k,v in dataset.items() if k != "stocks"},
            "warnings": dataset.get("warnings", []) + ["选股使用指定日期截至选股时间的分钟行情；市值按上一交易日已知总股本估算，不外推全天成交量。选股结果只产生研究信号，不连接券商。",
                f"放量条件：当日累计量 / 此前 {cfg.volume_base} 个完整交易日均量，严格大于 {cfg.volume_ratio:g} 倍。"] + ([entry_description(cfg)] if cfg.entry_filters == "on" else [])}
