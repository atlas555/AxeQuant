"""
OHLCVT 数据拉取核心模块
使用 ccxt 从交易所拉取 K 线数据。
支持月粒度文件存储、历史回溯拉取与增量更新。

数据目录结构:
  data/
  ├── BTCUSDT/
  │   ├── 5m/
  │   │   ├── 2025-01.csv
  │   │   ├── 2025-02.csv
  │   │   └── ...
  │   ├── 15m/
  │   └── ...
  └── ...
"""

import calendar
import time
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)


class OHLCVFetcher:
    """K 线数据拉取器，按月存储，支持历史回溯与增量更新。"""

    TIMEFRAME_MS = {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
        "6h": 21_600_000,
        "8h": 28_800_000,
        "12h": 43_200_000,
        "1d": 86_400_000,
        "3d": 259_200_000,
        "1w": 604_800_000,
        "1M": 2_592_000_000,
    }

    # Full Binance kline field mapping (indices 0-11).
    _BINANCE_KLINE_COLUMNS = [
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "num_trades",
        "taker_buy_volume", "taker_buy_quote_volume", "_ignore",
    ]

    def __init__(self, config: dict):
        self.config = config
        self.data_dir = Path(config["storage"]["data_dir"])
        self.file_format = config["storage"].get("file_format", "csv")
        self.columns = config["storage"]["columns"]
        self.fetch_limit = config.get("fetch_limit", 1000)

        exc_cfg = config["exchange"]
        exchange_cls = getattr(ccxt, exc_cfg["id"])
        init_params = dict(exc_cfg.get("options", {}))
        proxies = exc_cfg.get("proxies")
        if proxies:
            init_params["proxies"] = proxies
        self.exchange: ccxt.Exchange = exchange_cls(init_params)

        # Detect if we can use the Binance implicit API for richer kline data.
        self._is_binance_futures = (
            exc_cfg["id"] == "binance"
            and exc_cfg.get("options", {}).get("defaultType") == "future"
        )

        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 时间工具
    # ------------------------------------------------------------------

    @staticmethod
    def _month_start_ms(year: int, month: int) -> int:
        dt = datetime(year, month, 1, tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    @staticmethod
    def _next_month(year: int, month: int) -> Tuple[int, int]:
        if month == 12:
            return year + 1, 1
        return year, month + 1

    @staticmethod
    def _iter_months(start_year: int, start_month: int, end_year: int, end_month: int):
        """生成 [start, end] 闭区间的 (year, month) 序列。"""
        y, m = start_year, start_month
        while (y, m) <= (end_year, end_month):
            yield y, m
            y, m = OHLCVFetcher._next_month(y, m)

    @staticmethod
    def _ts_to_month_key(ts_ms: int) -> str:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return f"{dt.year:04d}-{dt.month:02d}"

    @staticmethod
    def _ts_to_utc_str(ts_ms: int) -> str:
        return datetime.fromtimestamp(
            ts_ms / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M")

    # ------------------------------------------------------------------
    # 文件 I/O
    # ------------------------------------------------------------------

    def _safe_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "")

    def _tf_dir(self, symbol: str, timeframe: str) -> Path:
        d = self.data_dir / self._safe_symbol(symbol) / timeframe
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _month_file(self, symbol: str, timeframe: str, year: int, month: int) -> Path:
        ext = "parquet" if self.file_format == "parquet" else "csv"
        return self._tf_dir(symbol, timeframe) / f"{year:04d}-{month:02d}.{ext}"

    def _read_file(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=self.columns)
        if self.file_format == "parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path)

    def _save_file(self, df: pd.DataFrame, path: Path):
        if self.file_format == "parquet":
            df.to_parquet(path, index=False)
        else:
            df.to_csv(path, index=False)

    def _get_latest_ts(self, symbol: str, timeframe: str) -> Optional[int]:
        """扫描所有月文件，找到最新的时间戳。"""
        tf_dir = self._tf_dir(symbol, timeframe)
        files = sorted(tf_dir.glob("*.csv" if self.file_format == "csv" else "*.parquet"))
        if not files:
            return None
        last_file = files[-1]
        df = self._read_file(last_file)
        if df.empty:
            return None
        return int(df["timestamp"].iloc[-1])

    # ------------------------------------------------------------------
    # 拉取 & 分发到月文件
    # ------------------------------------------------------------------

    def _fetch_range(self, symbol: str, timeframe: str, since_ms: int) -> List[list]:
        """从 since_ms 开始拉取到最新，返回原始行（列数与 self.columns 对齐）。"""
        if self._is_binance_futures:
            return self._fetch_range_binance(symbol, timeframe, since_ms)
        return self._fetch_range_ccxt(symbol, timeframe, since_ms)

    def _fetch_range_ccxt(self, symbol: str, timeframe: str, since_ms: int) -> List[list]:
        """Fallback: standard CCXT fetch_ohlcv (6 columns only)."""
        tf_ms = self.TIMEFRAME_MS.get(timeframe, 60_000)
        all_rows = []  # type: List[list]
        fetch_since = since_ms

        while True:
            ohlcv = self.exchange.fetch_ohlcv(
                symbol, timeframe,
                since=fetch_since,
                limit=self.fetch_limit,
            )
            if not ohlcv:
                break

            all_rows.extend(ohlcv)
            last_ts = ohlcv[-1][0]
            next_since = last_ts + tf_ms

            if len(ohlcv) < self.fetch_limit:
                break
            if next_since <= fetch_since:
                break

            fetch_since = next_since
            time.sleep(self.exchange.rateLimit / 1000)

        return all_rows

    # ------------------------------------------------------------------
    # Binance implicit API: captures all 12 kline fields
    # ------------------------------------------------------------------

    @staticmethod
    def _binance_symbol(ccxt_symbol: str) -> str:
        """Convert CCXT symbol 'BTC/USDT:USDT' → Binance API symbol 'BTCUSDT'."""
        return ccxt_symbol.replace("/", "").split(":")[0]

    def _parse_binance_row(self, raw: list) -> list:
        """Extract columns matching self.columns from the 12-field Binance kline row."""
        full = dict(zip(self._BINANCE_KLINE_COLUMNS, raw))
        # Cast numeric fields.
        for k in ("open", "high", "low", "close", "volume",
                   "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"):
            if k in full:
                full[k] = float(full[k])
        full["timestamp"] = int(full["timestamp"])
        full["num_trades"] = int(full.get("num_trades", 0))
        return [full.get(c) for c in self.columns]

    def _fetch_range_binance(self, symbol: str, timeframe: str, since_ms: int) -> List[list]:
        """Binance implicit API: returns rows with all configured columns."""
        tf_ms = self.TIMEFRAME_MS.get(timeframe, 60_000)
        api_symbol = self._binance_symbol(symbol)
        all_rows = []  # type: List[list]
        fetch_since = since_ms

        while True:
            raw = self.exchange.fapiPublicGetKlines({
                "symbol": api_symbol,
                "interval": timeframe,
                "startTime": fetch_since,
                "limit": self.fetch_limit,
            })
            if not raw:
                break

            for r in raw:
                all_rows.append(self._parse_binance_row(r))

            last_ts = int(raw[-1][0])
            next_since = last_ts + tf_ms

            if len(raw) < self.fetch_limit:
                break
            if next_since <= fetch_since:
                break

            fetch_since = next_since
            time.sleep(self.exchange.rateLimit / 1000)

        return all_rows

    def _dispatch_to_months(
        self, symbol: str, timeframe: str, rows: List[list]
    ) -> Dict[str, int]:
        """将 OHLCV 行按月拆分并合并写入对应月文件，返回 {月份: 新增条数}。"""
        if not rows:
            return {}

        df = pd.DataFrame(rows, columns=self.columns)
        df["timestamp"] = df["timestamp"].astype(int)
        df["_month"] = df["timestamp"].apply(self._ts_to_month_key)

        stats = {}  # type: Dict[str, int]
        for month_key, group in df.groupby("_month"):
            year, month = int(month_key[:4]), int(month_key[5:7])
            path = self._month_file(symbol, timeframe, year, month)
            existing = self._read_file(path)

            new_data = group.drop(columns=["_month"])
            if not existing.empty:
                existing["timestamp"] = existing["timestamp"].astype(int)
                combined = pd.concat([existing, new_data], ignore_index=True)
            else:
                combined = new_data.copy()

            combined.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
            combined.sort_values("timestamp", inplace=True)
            combined.reset_index(drop=True, inplace=True)
            self._save_file(combined, path)

            added = len(combined) - len(existing)
            stats[month_key] = added

        return stats

    # ------------------------------------------------------------------
    # 公共接口: 增量更新 (用于定时调度)
    # ------------------------------------------------------------------

    def fetch_update(self):
        """增量更新: 从每个 symbol×tf 的最新时间戳开始拉取新数据。"""
        symbols = self.config["symbols"]
        timeframes = self.config["timeframes"]

        for symbol in symbols:
            for tf in timeframes:
                try:
                    self._do_update(symbol, tf)
                except Exception:
                    logger.exception("增量更新失败: %s %s", symbol, tf)

    def _do_update(self, symbol: str, timeframe: str):
        latest_ts = self._get_latest_ts(symbol, timeframe)
        tf_ms = self.TIMEFRAME_MS.get(timeframe, 60_000)

        if latest_ts is not None:
            since_ms = latest_ts + tf_ms
        else:
            since_ms = self._default_since_ms()

        rows = self._fetch_range(symbol, timeframe, since_ms)
        if not rows:
            logger.info("无新数据: %s %s", symbol, timeframe)
            return

        stats = self._dispatch_to_months(symbol, timeframe, rows)
        total_new = sum(stats.values())
        latest = self._ts_to_utc_str(rows[-1][0])
        months_str = ", ".join(f"{k}(+{v})" for k, v in sorted(stats.items()) if v > 0)
        logger.info(
            "增量更新: %s %s  新增 %d 条  %s  截至 %s UTC",
            symbol, timeframe, total_new, months_str, latest,
        )

    # ------------------------------------------------------------------
    # 公共接口: 历史回溯 (backfill)
    # ------------------------------------------------------------------

    def fetch_backfill(self):
        """历史回溯: 从 history_start 到当前时间，按月逐一拉取所有数据。"""
        history_start = self.config.get("history_start", "2025-01-01")
        start_dt = datetime.strptime(history_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)

        symbols = self.config["symbols"]
        timeframes = self.config["timeframes"]

        total_tasks = len(symbols) * len(timeframes)
        done = 0

        for symbol in symbols:
            for tf in timeframes:
                done += 1
                logger.info(
                    "=== [%d/%d] 回溯: %s %s  %s → 现在 ===",
                    done, total_tasks, symbol, tf, history_start,
                )
                try:
                    self._do_backfill_symbol_tf(
                        symbol, tf,
                        start_dt.year, start_dt.month,
                        now_utc.year, now_utc.month,
                    )
                except Exception:
                    logger.exception("回溯失败: %s %s", symbol, tf)

    def _do_backfill_symbol_tf(
        self, symbol: str, timeframe: str,
        start_year: int, start_month: int,
        end_year: int, end_month: int,
    ):
        tf_ms = self.TIMEFRAME_MS.get(timeframe, 60_000)
        grand_total = 0

        for year, month in self._iter_months(start_year, start_month, end_year, end_month):
            path = self._month_file(symbol, timeframe, year, month)
            existing = self._read_file(path)

            if not existing.empty:
                last_ts = int(existing["timestamp"].iloc[-1])
                since_ms = last_ts + tf_ms
            else:
                since_ms = self._month_start_ms(year, month)

            ny, nm = self._next_month(year, month)
            month_end_ms = self._month_start_ms(ny, nm)
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            upper_bound = min(month_end_ms, now_ms)

            if since_ms >= upper_bound:
                count = len(existing)
                if count > 0:
                    logger.debug("  %04d-%02d 已完整 (%d 条), 跳过", year, month, count)
                continue

            rows = self._fetch_range_until(symbol, timeframe, since_ms, upper_bound)
            if not rows:
                logger.debug("  %04d-%02d 无新数据", year, month)
                continue

            new_df = pd.DataFrame(rows, columns=self.columns)
            new_df["timestamp"] = new_df["timestamp"].astype(int)

            if not existing.empty:
                existing["timestamp"] = existing["timestamp"].astype(int)
                combined = pd.concat([existing, new_df], ignore_index=True)
            else:
                combined = new_df

            combined.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
            combined.sort_values("timestamp", inplace=True)
            combined.reset_index(drop=True, inplace=True)
            self._save_file(combined, path)

            added = len(combined) - len(existing)
            grand_total += added
            logger.info(
                "  %04d-%02d  +%d 条  总计 %d 条",
                year, month, added, len(combined),
            )

        logger.info(
            "回溯完成: %s %s  共新增 %d 条",
            symbol, timeframe, grand_total,
        )

    def _fetch_range_until(
        self, symbol: str, timeframe: str, since_ms: int, until_ms: int
    ) -> List[list]:
        """从 since_ms 拉取到 until_ms (不含)，自动分页。"""
        if self._is_binance_futures:
            return self._fetch_range_until_binance(symbol, timeframe, since_ms, until_ms)
        return self._fetch_range_until_ccxt(symbol, timeframe, since_ms, until_ms)

    def _fetch_range_until_ccxt(
        self, symbol: str, timeframe: str, since_ms: int, until_ms: int
    ) -> List[list]:
        """Fallback: standard CCXT fetch_ohlcv with upper bound."""
        tf_ms = self.TIMEFRAME_MS.get(timeframe, 60_000)
        all_rows = []  # type: List[list]
        fetch_since = since_ms

        while fetch_since < until_ms:
            ohlcv = self.exchange.fetch_ohlcv(
                symbol, timeframe,
                since=fetch_since,
                limit=self.fetch_limit,
            )
            if not ohlcv:
                break

            for row in ohlcv:
                if row[0] < until_ms:
                    all_rows.append(row)

            last_ts = ohlcv[-1][0]
            next_since = last_ts + tf_ms

            if len(ohlcv) < self.fetch_limit:
                break
            if next_since <= fetch_since:
                break
            if next_since >= until_ms:
                break

            fetch_since = next_since
            time.sleep(self.exchange.rateLimit / 1000)

        return all_rows

    def _fetch_range_until_binance(
        self, symbol: str, timeframe: str, since_ms: int, until_ms: int
    ) -> List[list]:
        """Binance implicit API with upper bound."""
        tf_ms = self.TIMEFRAME_MS.get(timeframe, 60_000)
        api_symbol = self._binance_symbol(symbol)
        all_rows = []  # type: List[list]
        fetch_since = since_ms

        while fetch_since < until_ms:
            raw = self.exchange.fapiPublicGetKlines({
                "symbol": api_symbol,
                "interval": timeframe,
                "startTime": fetch_since,
                "endTime": until_ms - 1,
                "limit": self.fetch_limit,
            })
            if not raw:
                break

            for r in raw:
                ts = int(r[0])
                if ts < until_ms:
                    all_rows.append(self._parse_binance_row(r))

            last_ts = int(raw[-1][0])
            next_since = last_ts + tf_ms

            if len(raw) < self.fetch_limit:
                break
            if next_since <= fetch_since:
                break
            if next_since >= until_ms:
                break

            fetch_since = next_since
            time.sleep(self.exchange.rateLimit / 1000)

        return all_rows

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _default_since_ms(self) -> int:
        """无已有数据时的默认起始时间 = history_start 或 30 天前。"""
        history_start = self.config.get("history_start")
        if history_start:
            dt = datetime.strptime(history_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        now = datetime.now(timezone.utc)
        return int((now.timestamp() - 30 * 86400) * 1000)
