#!/usr/bin/env python3
"""
DataAuto - OHLCVT 数据自动拉取服务
用法:
    python main.py                    # 启动定时调度 (默认每 5 分钟增量更新)
    python main.py --once             # 只执行一次增量更新
    python main.py --backfill         # 历史回溯: 从 history_start 拉取到现在
    python main.py -c myconfig.yaml   # 指定配置文件
"""

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path
from typing import List

import yaml

from fetcher import OHLCVFetcher


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        print(f"配置文件不存在: {path.resolve()}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("log_file")

    handlers = [logging.StreamHandler(sys.stdout)]  # type: List[logging.Handler]
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def run_update(config: dict):
    fetcher = OHLCVFetcher(config)
    logging.info(
        "开始增量更新: %s × %s",
        config["symbols"], config["timeframes"],
    )
    fetcher.fetch_update()
    logging.info("本轮增量更新完成")


def run_backfill(config: dict):
    fetcher = OHLCVFetcher(config)
    history_start = config.get("history_start", "2025-01-01")
    logging.info(
        "开始历史回溯: %s × %s  从 %s 到现在",
        config["symbols"], config["timeframes"], history_start,
    )
    fetcher.fetch_backfill()
    logging.info("历史回溯全部完成")


def run_scheduler(config: dict):
    scheduler_cfg = config.get("scheduler", {})
    interval = scheduler_cfg.get("interval_minutes", 5) * 60
    run_on_start = scheduler_cfg.get("run_on_start", True)

    stop_event = threading.Event()

    def _signal_handler(signum, frame):
        logging.info("收到终止信号，正在退出...")
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logging.info(
        "定时调度已启动 — 每 %d 分钟增量更新一次 (Ctrl+C 停止)",
        scheduler_cfg.get("interval_minutes", 5),
    )

    if run_on_start:
        run_update(config)

    while not stop_event.is_set():
        stop_event.wait(timeout=interval)
        if stop_event.is_set():
            break
        run_update(config)

    logging.info("调度器已停止")


def main():
    parser = argparse.ArgumentParser(description="DataAuto OHLCVT 数据拉取服务")
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        action="store_true",
        help="只执行一次增量更新，不启动定时调度",
    )
    mode.add_argument(
        "--backfill",
        action="store_true",
        help="历史回溯: 从 history_start 拉取到当前时间",
    )

    args = parser.parse_args()
    config = load_config(args.config)
    setup_logging(config)

    if args.backfill:
        run_backfill(config)
    elif args.once:
        run_update(config)
    else:
        run_scheduler(config)


if __name__ == "__main__":
    main()
