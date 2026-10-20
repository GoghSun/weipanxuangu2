from dataclasses import dataclass, asdict, fields, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import math

LEGACY_SYMBOLS = "600000,600008,600017,600033,600039,600075,600081,600110,600160,600171,600201,600210,600256,600273,600282,600307,600350,600354,600366,600397,600500,600510,600517,600528,600580,600597,600601,600611,600660,600741"
PRESET_PATH = Path(__file__).resolve().parents[1] / "data" / "universe-preset.json"


def default_symbols():
    try:
        preset = json.loads(PRESET_PATH.read_text(encoding="utf-8"))
        symbols = [r["symbol"] for r in preset["stocks"]]
        if 1 <= len(symbols) <= 8000 and all(len(s) == 6 and s.isdigit() and s.startswith(("60", "00", "30", "68")) for s in symbols):
            return ",".join(symbols)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return LEGACY_SYMBOLS


@dataclass
class Config:
    strategy_version: int = 2
    market_cap_min: float = 50
    market_cap_max: float = 200
    limit_lookback: int = 50
    limit_type: str = "close"
    sideways_days: int = 7
    sideways_range: float = 10
    volume_recent: int = 1  # Retained to read old saved configurations; new runs use today only.
    volume_base: int = 5
    volume_ratio: float = 1.5
    entry_filters: str = "on"
    entry_ma_days: int = 5
    burst_volume_days: int = 5
    burst_volume_ratio: float = 2.0
    upper_shadow_pct: float = 40.0
    gain_1430_min: float = 3.0
    stop_loss: float = 2
    ma_fast: int = 3
    ma_slow: int = 5
    selection_time: str = "14:50"
    initial_cash: float = 1000000
    max_positions: int = 5
    commission: float = 0.025
    min_commission: float = 5
    stamp_tax: float = 0.05
    transfer_fee: float = 0.001
    slippage: float = 0.05
    mode: str = "minute"
    source: str = "demo"
    start_date: str = ""
    end_date: str = ""
    symbols: str = field(default_factory=default_symbols)
    universe_limit: int = 300
    universe_scope: str = "market_cap"

    @classmethod
    def parse(cls, payload=None, *, historical=False):
        payload = dict(payload or {})
        valid = {f.name for f in fields(cls)}
        extra = set(payload) - valid
        if extra:
            raise ValueError("未知参数: " + ", ".join(sorted(extra)))
        c = cls()
        for name, value in payload.items():
            old = getattr(c, name)
            if isinstance(old, int):
                number = float(value)
                if not math.isfinite(number) or not number.is_integer():
                    raise ValueError(name + " 必须是整数")
                value = int(number)
            elif isinstance(old, float):
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError(name + " 必须是有限数值")
            elif not isinstance(value, str):
                raise ValueError(name + " 必须是文本")
            setattr(c, name, value)
        bounds = {
            "market_cap_min": (0.1, 100000), "market_cap_max": (0.1, 100000),
            "limit_lookback": (1, 250), "sideways_days": (2, 120), "sideways_range": (0.1, 100),
            "volume_recent": (1, 30), "volume_base": (1, 120), "volume_ratio": (0.1, 50),
            "entry_ma_days": (2, 250), "burst_volume_days": (1, 120),
            "burst_volume_ratio": (0.1, 50), "upper_shadow_pct": (0.1, 100), "gain_1430_min": (0, 30),
            "stop_loss": (0.1, 50), "ma_fast": (1, 120), "ma_slow": (2, 250),
            "initial_cash": (10000, 1e10), "max_positions": (1, 100),
            "commission": (0, 5), "min_commission": (0, 100), "stamp_tax": (0, 5),
            "transfer_fee": (0, 1), "slippage": (0, 5),
            "universe_limit": (0, 8000),
        }
        for key, (low, high) in bounds.items():
            if not low <= getattr(c, key) <= high:
                raise ValueError(f"{key} 必须在 {low} 到 {high} 之间")
        if c.market_cap_min > c.market_cap_max:
            raise ValueError("市值下限不能大于上限")
        if c.ma_fast >= c.ma_slow:
            raise ValueError("短期均线必须小于长期均线")
        if c.source not in ("demo", "akshare") or c.mode not in ("daily", "minute"):
            raise ValueError("无效的数据来源或回测模式")
        if c.entry_filters not in ("on", "off"):
            raise ValueError("当日买入过滤必须为 on 或 off")
        if not historical:
            c.validate_execution()
        if c.limit_type not in ("close", "touch"):
            raise ValueError("涨停口径必须是 close 或 touch")
        if c.universe_scope not in ("market_cap", "all"):
            raise ValueError("股票池范围必须为市值范围或全部沪深 A 股")
        try:
            t = datetime.strptime(c.selection_time, "%H:%M")
        except ValueError:
            raise ValueError("选股时间格式应为 HH:MM") from None
        if not "14:30" <= t.strftime("%H:%M") <= "14:55" or t.minute % 5:
            raise ValueError("选股时间须为 14:30–14:55 的 5 分钟刻度，预留下一根 K 线成交")
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        completed = now.date() - timedelta(days=int(now.strftime("%H:%M") < "15:10"))
        while completed.weekday() >= 5:
            completed -= timedelta(days=1)
        c.end_date = c.end_date or completed.isoformat()
        c.start_date = c.start_date or (date.fromisoformat(c.end_date) - timedelta(days=6)).isoformat()
        start, end = date.fromisoformat(c.start_date), date.fromisoformat(c.end_date)
        if start > end or (end - start).days > 1096:
            raise ValueError("回测区间须按时间顺序，跨度不超过 3 年")
        symbols = list(dict.fromkeys(c.symbols.replace("，", ",").replace("\n", ",").replace(" ", ",").split(",")))
        symbols = [s for s in symbols if s]
        if not symbols or len(symbols) > 8000 or any(len(s) != 6 or not s.isdigit() or not s.startswith(("60", "00", "30", "68")) for s in symbols):
            raise ValueError("请输入 1–8000 个沪深 A 股代码，逗号分隔；暂不支持北交所")
        c.symbols = ",".join(symbols)
        return c

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_saved(cls, payload):
        # Historical runs predate these rules; never attribute new filters to old results.
        return cls.parse({"mode": "daily", "entry_filters": "off", "volume_recent": 2,
                          "strategy_version": 1, **payload}, historical=True)

    def validate_execution(self):
        if self.mode != "minute":
            raise ValueError("尾盘策略仅支持当日 14:30 后买入，已停用次日开盘的日线近似模式。请使用分钟行情重新回测。")
        if self.strategy_version != 2 or self.volume_recent != 1:
            raise ValueError("旧版策略仅供查看历史记录。新回测使用当日累计量 / 此前 N 日均量，请更新参数后运行。")

    @property
    def warmup(self):
        entry = max(self.entry_ma_days, self.burst_volume_days + 1) if self.entry_filters == "on" else 0
        return max(self.limit_lookback + 1, self.sideways_days, self.volume_recent + self.volume_base, self.ma_slow + 1, entry, 60)
