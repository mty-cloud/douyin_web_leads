"""日志配置。"""

import sys
from pathlib import Path

from loguru import logger

from src.settings import PROJECT_ROOT


def setup_logger(log_dir: str | Path | None = None):
    """配置 loguru 日志。"""
    if log_dir is None:
        log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # 移除默认 handler
    logger.remove()

    # 控制台输出
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
        level="DEBUG",
        colorize=True,
    )

    # 文件输出
    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
    )

    # 错误日志单独文件
    logger.add(
        log_dir / "error_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="ERROR",
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
    )

    return logger


# 便捷引用
get_logger = logger
