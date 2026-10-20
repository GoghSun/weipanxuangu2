from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlparse
import argparse
import csv
import io
import json
import mimetypes
import re
import uuid

from .config import Config
from .data import ROOT, atomic_json, load_data, run_fetch, screen
from .backtest import run_backtest
from .charts import save_charts, get_chart
from .universe import load_universe

RUNS = ROOT / "data" / "runs"
JOBS = {}
LOCK = Lock()


def work(job_id, kind, cfg):
    def progress(percent, message):
        with LOCK:
            JOBS[job_id].update(progress=percent, message=message)
    try:
        if kind == "universe":
            progress(10, "读取 AKShare 当前市场总市值…")
            result = load_universe(cfg, progress)
        else:
            dataset = load_data(cfg, progress, for_screen=kind == "screen")
            result = screen(dataset, cfg) if kind == "screen" else run_backtest(dataset, cfg, progress)
            result.update(id=job_id, kind=kind, created_at=datetime.now().isoformat(timespec="seconds"))
            save_charts(job_id, dataset, result)
            atomic_json(RUNS / (job_id + ".json"), result)
        with LOCK:
            JOBS[job_id].update(status="done", progress=100, message="运行完成", result=result)
    except Exception as exc:
        with LOCK:
            JOBS[job_id].update(status="error", message=str(exc), progress=100)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/jobs/"):
            super().log_message(fmt, *args)

    def send(self, status, payload, content_type="application/json; charset=utf-8", headers=None):
        if not isinstance(payload, bytes):
            payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/config":
                return self.send(200, Config.parse().to_dict())
            if path == "/api/health":
                import importlib.util
                return self.send(200, {"status": "ok", "akshare_installed": importlib.util.find_spec("akshare") is not None})
            if path == "/api/active-job":
                with LOCK:
                    active = next((j for j in JOBS.values() if j["status"] == "running"), None)
                    return self.send(200, {k:active[k] for k in ("id", "kind", "config", "progress", "message")} if active else None)
            if path == "/api/runs":
                paths = sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
                runs = []
                for p in paths:
                    value = json.loads(p.read_text(encoding="utf-8"))
                    runs.append({k: value.get(k) for k in ("id", "kind", "created_at", "metrics", "config")})
                return self.send(200, runs)
            candles = re.fullmatch(r"/api/runs/([a-f0-9]{12})/candles/(\d{6})", path)
            if candles:
                file = RUNS / (candles[1] + ".json")
                if not file.exists():
                    return self.send(404, {"error": "回测记录不存在"})
                try:
                    result = get_chart(json.loads(file.read_text(encoding="utf-8")), candles[2])
                    return self.send(200, result)
                except ValueError as exc:
                    return self.send(422, {"error": str(exc)})
            if re.fullmatch(r"/api/jobs/[a-f0-9]{12}", path):
                with LOCK:
                    value = JOBS.get(path.rsplit("/", 1)[-1])
                    return self.send(200 if value else 404, value or {"error": "任务不存在"})
            match = re.fullmatch(r"/api/runs/([a-f0-9]{12})(/trades.csv)?", path)
            if match:
                file = RUNS / (match[1] + ".json")
                if not file.exists():
                    return self.send(404, {"error": "记录不存在"})
                result = json.loads(file.read_text(encoding="utf-8"))
                if match[2]:
                    stream = io.StringIO()
                    writer = csv.writer(stream)
                    keys = ["symbol", "name", "signal_time", "buy_date", "buy_window_start", "buy_time", "sell_date", "sell_window_start", "sell_time", "buy_price", "sell_price", "quantity", "pnl", "return_pct", "holding_days", "fees", "reason"]
                    writer.writerow(["代码", "名称", "信号时间", "买入日期", "买入时段开始", "买入成交K线结束", "卖出日期", "卖出时段开始", "卖出成交K线结束", "买入价", "卖出价", "股数", "净盈亏", "收益率%", "持有交易日", "费用", "卖出原因"])
                    for row in result.get("trades", []):
                        values = [row.get(k, "") for k in keys]
                        writer.writerow(["'" + v if isinstance(v, str) and (v[:1] in ("=", "+", "-", "@", "\t", "\r") or k == "symbol") else v for k, v in zip(keys, values)])
                    return self.send(200, stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", {"Content-Disposition": 'attachment; filename="weipan-trades.csv"'})
                return self.send(200, result)
            assets = {"/": "index.html", "/style.css": "style.css", "/app.js": "app.js", "/candles.js": "candles.js"}
            if path in assets:
                file = ROOT / "static" / assets[path]
                return self.send(200, file.read_bytes(), (mimetypes.guess_type(file.name)[0] or "text/plain") + "; charset=utf-8")
            return self.send(404, {"error": "页面不存在"})
        except Exception as exc:
            self.send(500, {"error": str(exc)})

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self.send(403, {"error": "仅允许本地页面提交"})
        path = urlparse(self.path).path
        if path not in ("/api/backtest", "/api/screen", "/api/universe"):
            return self.send(404, {"error": "接口不存在"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 100000:
                return self.send(413, {"error": "请求过大"})
            config = Config.parse(json.loads(self.rfile.read(length)))
            kind = path.rsplit("/", 1)[-1]
            with LOCK:
                if any(j["status"] == "running" for j in JOBS.values()):
                    return self.send(409, {"error": "已有任务运行中，请等待完成后再操作"})
                job_id = uuid.uuid4().hex[:12]
                JOBS[job_id] = {"id": job_id, "kind": kind, "config": config.to_dict(), "status": "running", "progress": 1, "message": "正在准备数据…"}
                if len(JOBS) > 40:
                    del JOBS[next(iter(JOBS))]
            Thread(target=work, args=(job_id, kind, config), daemon=True).start()
            self.send(202, {"id": job_id})
        except (ValueError, TypeError, OverflowError) as exc:
            self.send(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="尾盘策略工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"尾盘策略工作台 http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
