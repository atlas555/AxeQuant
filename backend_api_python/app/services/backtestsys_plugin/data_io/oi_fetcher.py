"""
Open Interest 数据拉取模块
使用 ccxt 从 Binance 拉取永续合约 OI 历史数据。
支持月粒度文件存储与增量更新。

注意: Binance 仅提供最近 30 天的 OI 历史，无法深度回溯。

数据目录结构:
  data_future/
  ├── BTCUSDT/
  │   ├── oi/
  │   │   ├── 2026-03.csv
  │   │   ├── 2026-04.csv
  │   │   └── ...
  │   └── ...
  └── ...

API 端点: GET /futures/data/openInterestHist
  参数: symbol, period, limit (max 500), startTime, endTime
  响应: [{symbol, sumOpenInterest, sumOpenInterestValue, timestamp}, ...]
"""

import time
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)


class OIFetcher:
    """Open Interest 数据拉取器，按月存储，支持增量更新。"""

    COLUMNS = ["timestamp", "open_interest", "open_interest_value"]
    FETCH_LIMIT = 500  # Binance OI 端点最大 limit

    def __init__(self, config: dict):
        self.config = config
        self.data_dir = Path(config["storage"]["data_dir"])
        self.file_format = config["storage"].get("file_format", "csv")

        oi_cfg = config.get("oi", {})
        self.enabled = oi_cfg.get("enabled", False)
        self.period = oi_cfg.get("period", "15m")

        exc_cfg = config["exchange"]
        exchange_cls = getattr(ccxt, exc_cfg["id"])
        init_params = dict(exc_cfg.get("options", {}))
        proxies = exc_cfg.get("proxies")
        if proxies:
            init_params["proxies"] = proxies
        self.exchange: ccxt.Exchange = exchange_cls(init_params)

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
    def _ts_to_month_key(ts_ms: int) -> str:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return f"{dt.year:04d}-{dt.month:02d}"

    @staticmethod
    def _ts_to_utc_str(ts_ms: int) -> str:
        return datetime.fromtimestamp(
            ts_ms / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M")

    # ------------------------------------------------------------------
    # Symbol 转换
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_symbol(symbol: str) -> str:
        """'BTC/USDT:USDT' → 'BTCUSDT:USDT'"""
        return symbol.replace("/", "")

    @staticmethod
    def _binance_symbol(ccxt_symbol: str) -> str:
        """'BTC/USDT:USDT' → 'BTCUSDT'"""
        return ccxt_symbol.replace("/", "").split(":")[0]

    # ------------------------------------------------------------------
    # 文件 I/O
    # ------------------------------------------------------------------

    def _oi_dir(self, symbol: str) -> Path:
        d = self.data_dir / self._safe_symbol(symbol) / "oi"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _month_file(self, symbol: str, year: int, month: int) -> Path:
        ext = "parquet" if self.file_format == "parquet" else "csv"
        return self._oi_dir(symbol) / f"{year:04d}-{month:02d}.{ext}"

    def _read_file(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=self.COLUMNS)
        if self.file_format == "parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path)

    def _save_file(self, df: pd.DataFrame, path: Path):
        if self.file_format == "parquet":
            df.to_parquet(path, index=False)
        else:
            df.to_csv(path, index=False)

    def _get_latest_ts(self, symbol: str) -> Optional[int]:
        """扫描所有月文件，找到最新的时间戳。"""
        oi_dir = self._oi_dir(symbol)
        files = sorted(oi_dir.glob("*.csv" if self.file_format == "csv" else "*.parquet"))
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

    def _fetch_oi_range(self, symbol: str, since_ms: int) -> List[dict]:
        """从 since_ms 开始拉取 OI 数据到最新，返回原始响应列表。"""
        api_symbol = self._binance_symbol(symbol)
        all_records = []  # type: List[dict]
        fetch_since = since_ms

        while True:
            try:
                raw = self.exchange.fapidata_get_openinteresthist({
                    "symbol": api_symbol,
                    "period": self.period,
                    "limit": self.FETCH_LIMIT,
                    "startTime": fetch_since,
                })
            except Exception as e:
                logger.warning("OI 拉取异常 (%s): %s", symbol, e)
                break

            if not raw:
                break

            all_records.extend(raw)

            last_ts = int(raw[-1]["timestamp"])
            # 前进 1ms 避免重复拉取最后一条
            next_since = last_ts + 1

            if len(raw) < self.FETCH_LIMIT:
                break
            if next_since <= fetch_since:
                break

            fetch_since = next_since
            time.sleep(self.exchange.rateLimit / 1000)

        return all_records

    def _parse_records(self, records: List[dict]) -> List[list]:
        """将 Binance 响应转为 [timestamp, open_interest, open_interest_value] 行。"""
        rows = []
        for r in records:
            rows.append([
                int(r["timestamp"]),
                float(r["sumOpenInterest"]),
                float(r["sumOpenInterestValue"]),
            ])
        return rows

    def _dispatch_to_months(
        self, symbol: str, rows: List[list]
    ) -> Dict[str, int]:
        """将 OI 行按月拆分并合并写入对应月文件，返回 {月份: 新增条数}。"""
        if not rows:
            return {}

        df = pd.DataFrame(rows, columns=self.COLUMNS)
        df["timestamp"] = df["timestamp"].astype(int)
        df["_month"] = df["timestamp"].apply(self._ts_to_month_key)

        stats = {}  # type: Dict[str, int]
        for month_key, group in df.groupby("_month"):
            year, month = int(month_key[:4]), int(month_key[5:7])
            path = self._month_file(symbol, year, month)
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
    # 公共接口: 增量更新
    # ------------------------------------------------------------------

    def fetch_update(self):
        """增量更新: 从每个 symbol 的最新时间戳开始拉取新 OI 数据。"""
        if not self.enabled:
            logger.debug("OI 采集未启用，跳过")
            return

        symbols = self.config["symbols"]
        for symbol in symbols:
            try:
                self._do_update(symbol)
            except Exception:
                logger.exception("OI 增量更新失败: %s", symbol)

    def _do_update(self, symbol: str):
        latest_ts = self._get_latest_ts(symbol)

        if latest_ts is not None:
            since_ms = latest_ts + 1
        else:
            # 无已有数据: 从 30 天前开始 (Binance OI 历史上限)
            now = datetime.now(timezone.utc)
            since_ms = int((now.timestamp() - 30 * 86400) * 1000)

        records = self._fetch_oi_range(symbol, since_ms)
        if not records:
            logger.info("OI 无新数据: %s (period=%s)", symbol, self.period)
            return

        rows = self._parse_records(records)
        stats = self._dispatch_to_months(symbol, rows)
        total_new = sum(stats.values())
        latest = self._ts_to_utc_str(rows[-1][0])
        months_str = ", ".join(f"{k}(+{v})" for k, v in sorted(stats.items()) if v > 0)
        logger.info(
            "OI 增量更新: %s period=%s  新增 %d 条  %s  截至 %s UTC",
            symbol, self.period, total_new, months_str, latest,
        )

    # ------------------------------------------------------------------
    # 公共接口: 全量拉取 (仅最近 30 天)
    # ------------------------------------------------------------------

    def fetch_all(self):
        """全量拉取: 从 30 天前到现在，拉取所有 symbol 的 OI 数据。
        注意: Binance 仅提供最近 30 天历史，此方法等效于 fetch_update 的首次运行。
        """
        if not self.enabled:
            logger.debug("OI 采集未启用，跳过")
            return

        symbols = self.config["symbols"]
        now = datetime.now(timezone.utc)
        since_ms = int((now.timestamp() - 30 * 86400) * 1000)

        for symbol in symbols:
            try:
                logger.info("OI 全量拉取: %s (最近 30 天, period=%s)", symbol, self.period)
                records = self._fetch_oi_range(symbol, since_ms)
                if not records:
                    logger.info("OI 无数据: %s", symbol)
                    continue

                rows = self._parse_records(records)
                stats = self._dispatch_to_months(symbol, rows)
                total = sum(len(g) for _, g in pd.DataFrame(rows, columns=self.COLUMNS).groupby(
                    pd.DataFrame(rows, columns=self.COLUMNS)["timestamp"].apply(self._ts_to_month_key)
                ))
                logger.info(
                    "OI 全量完成: %s  共 %d 条  %s",
                    symbol, total,
                    ", ".join(f"{k}(+{v})" for k, v in sorted(stats.items()) if v > 0),
                )
            except Exception:
                logger.exception("OI 全量拉取失败: %s", symbol)
