"""AKShare worker: one JSON request on stdin, one JSON result on stdout."""
import contextlib
import io
import json
import math
import sys


def fetch(request):
    try:
        import akshare as ak
    except ImportError:
        raise ValueError("尚未安装 AKShare，请执行 python3 -m pip install -r requirements.txt") from None
    # AKShare helpers sometimes omit a timeout; the parent process also enforces a hard deadline.
    import requests
    original_request = requests.sessions.Session.request
    def bounded_request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 15)
        return original_request(self, method, url, **kwargs)
    requests.sessions.Session.request = bounded_request
    if request["kind"] == "universe":
        source = "AKShare · 东方财富"
        try:
            frame = ak.stock_zh_a_spot_em()
            if frame.empty:
                raise ValueError("股票列表为空")
        except Exception:
            frame = ak.stock_zh_a_spot_tx().rename(columns={"code": "代码", "name": "名称", "zsz": "总市值"})
            frame["代码"] = frame["代码"].str.replace(r"^(sh|sz|bj)", "", regex=True)
            frame["总市值"] = frame["总市值"].astype(float) * 1e8
            source = "AKShare · 腾讯证券（总市值单位由亿元转换为元）"
        output = []
        for r in frame.to_dict("records"):
            code, name = str(r["代码"]).zfill(6), str(r["名称"])
            cap = float(r["总市值"])
            if code.startswith(("60", "00", "30", "68")) and "ST" not in name.upper() and "退" not in name and math.isfinite(cap):
                if request.get("scope") == "all" or request["min"] * 1e8 <= cap <= request["max"] * 1e8:
                    output.append({"symbol": code, "name": name, "market_cap": cap / 1e8})
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return {"stocks": sorted({r["symbol"]:r for r in output}.values(), key=lambda r: r["symbol"]),
                "source": source, "as_of": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")}
    symbol = request["symbol"]
    warnings = []
    try:
        frame = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=request["start"].replace("-", ""),
                                  end_date=request["end"].replace("-", ""), adjust="")
        if frame.empty:
            raise ValueError("日线为空")
    except Exception:
        prefix = "sh" if symbol.startswith("6") else "sz"
        frame = ak.stock_zh_a_daily(symbol=prefix+symbol, start_date=request["start"].replace("-", ""),
                                   end_date=request["end"].replace("-", ""), adjust="")
        frame = frame.rename(columns={"date":"日期", "open":"开盘", "close":"收盘", "high":"最高", "low":"最低", "volume":"成交量"})
        frame["成交量"] = frame["成交量"] / 100  # Sina shares -> Eastmoney lots; minute data also uses lots.
        warnings.append("东方财富日线不可用，改用 AKShare 新浪未复权日线；成交量已统一为手。")
    if frame.empty:
        raise ValueError(f"{symbol} 未返回日线行情")
    valuation = ak.stock_value_em(symbol=symbol)
    if valuation.empty:
        raise ValueError(f"{symbol} 未返回历史总市值，无法执行市值条件")
    caps = {str(r["数据日期"])[:10]: float(r["总市值"]) for r in valuation.to_dict("records") if math.isfinite(float(r["总市值"]))}
    daily = []
    for r in frame.to_dict("records"):
        b = {"date": str(r["日期"])[:10], **{k: float(r[v]) for k, v in {"open": "开盘", "high": "最高", "low": "最低", "close": "收盘", "volume": "成交量"}.items()}}
        if not all(math.isfinite(b[k]) for k in ("open", "high", "low", "close", "volume")) or b["close"] <= 0:
            raise ValueError(f"{symbol} 日线包含无效行情")
        if b["date"] in caps:
            b["market_cap"] = caps[b["date"]]
        else:
            b["market_cap"] = None
        # Eastmoney change amount references the exchange's previous close, including ex-right reference adjustments.
        change = r.get("涨跌额")
        if change is not None and math.isfinite(float(change)):
            b["prev_close"] = round(b["close"] - float(change), 2)
        daily.append(b)
    daily.sort(key=lambda b: b["date"])
    if not any(b["market_cap"] is not None for b in daily):
        raise ValueError(f"{symbol} 行情与历史市值日期无重合")
    missing = [b["date"] for b in daily if b["date"] >= request.get("market_start", request["start"]) and b["market_cap"] is None and b["volume"] > 0
               and not (request.get("minutes") and b["date"] == request["end"])]
    if missing:
        raise ValueError(f"{symbol} 缺少 {len(missing)} 个交易日的历史总市值（如 {missing[0]}），本次不生成回测")
    name = request.get("name", symbol)
    try:
        if not request.get("name"):
            info = ak.stock_individual_info_em(symbol=symbol, timeout=10)
            values = dict(zip(info["item"], info["value"]))
            name = str(values.get("股票简称", symbol))
    except Exception:
        try:
            if symbol.startswith("6"):
                names = ak.stock_info_sh_name_code(symbol="科创板" if symbol.startswith("68") else "主板A股")
                names = dict(zip(names["证券代码"].astype(str).str.zfill(6), names["证券简称"]))
            else:
                names = ak.stock_info_sz_name_code(symbol="A股列表")
                names = dict(zip(names["A股代码"].astype(str).str.zfill(6), names["A股简称"]))
            name = str(names[symbol])
            warnings.append("个股信息接口不可用，证券简称改由 AKShare 交易所证券列表核实。")
        except Exception:
            raise ValueError(f"{symbol} 无法读取证券简称，不能核实 ST 状态，请稍后重试") from None
    minutes = []
    if request.get("minutes"):
        try:
            minute_frame = ak.stock_zh_a_hist_min_em(symbol=symbol, period="5", adjust="",
                start_date=request["minute_start"] + " 09:30:00", end_date=request["end"] + " 15:00:00")
            if minute_frame.empty:
                raise ValueError("分钟行情为空")
        except Exception:
            prefix = "sh" if symbol.startswith("6") else "sz"
            minute_frame = ak.stock_zh_a_minute(symbol=prefix+symbol, period="5", adjust="")
            minute_frame = minute_frame.rename(columns={"day":"时间", "open":"开盘", "close":"收盘", "high":"最高", "low":"最低", "volume":"成交量"})
            minute_frame["成交量"] = minute_frame["成交量"].astype(float) / 100
            minute_frame = minute_frame[(minute_frame["时间"] >= request["minute_start"]+" 09:30:00") & (minute_frame["时间"] <= request["end"]+" 15:00:00")]
            warnings.append("东方财富分钟行情不可用，改用 AKShare 新浪 5 分钟行情；成交量已统一为手。")
        for r in minute_frame.to_dict("records"):
            b = {"datetime": str(r["时间"]), **{k: float(r[v]) for k,v in {"open": "开盘", "close": "收盘", "high": "最高", "low": "最低", "volume": "成交量"}.items()}}
            if not all(math.isfinite(b[k]) for k in ("open", "high", "low", "close", "volume")):
                raise ValueError(f"{symbol} 分钟行情包含无效数值")
            minutes.append(b)
        # Catch unit errors or incomplete volume aggregation across independent providers.
        for day in {b["datetime"][:10] for b in minutes}:
            group = [b for b in minutes if b["datetime"][:10] == day]
            full_day = next((b for b in daily if b["date"] == day), None)
            if len(group) == 48 and group[-1]["datetime"][11:16] == "15:00" and full_day and full_day["volume"] > 0:
                ratio = sum(b["volume"] for b in group) / full_day["volume"]
                if not .95 <= ratio <= 1.05:
                    raise ValueError(f"{symbol} {day} 分钟汇总量与日线成交量不一致（{ratio:.3f} 倍），拒绝使用")
    return {"name": name, "daily": daily, "minutes": sorted(minutes, key=lambda b: b["datetime"]), "warnings": warnings}


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.read())
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            response = fetch(request)
        print(json.dumps(response, ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:500]}"}, ensure_ascii=False))
