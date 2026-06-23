"""错误恢复和重试工具。"""

import time
from functools import wraps

from loguru import logger


def retry_on_failure(max_retries: int = 2, delay: float = 1.0, backoff: float = 2.0):
    """重试装饰器。

    捕获 Exception，重试指定次数，失败则抛出。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            wait = delay
            for attempt in range(1, max_retries + 2):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt <= max_retries:
                        logger.warning("{} 第{}次失败，{} 秒后重试: {}",
                                       func.__name__, attempt, wait, e)
                        time.sleep(wait)
                        wait *= backoff
                    else:
                        logger.error("{} 重试{}次后仍然失败: {}",
                                     func.__name__, max_retries, e)
                        raise
            return None
        return wrapper
    return decorator


class RecoveryHandler:
    """浏览器恢复处理。"""

    @staticmethod
    def is_login_required(state: str) -> bool:
        return state in ("LOGIN_REQUIRED", "VERIFY_OR_RISK")

    @staticmethod
    def handle_human_intervention(state: str, keyword: str = "") -> str:
        """遇到需要人工处理的场景，返回状态标记。

        Returns:
            'paused_need_human' 或 'continue'
        """
        if state in ("LOGIN_REQUIRED", "VERIFY_OR_RISK"):
            msg = f"⚠️ 需要人工处理: {state}"
            if keyword:
                msg += f" (关键词: {keyword})"
            msg += "。请前往浏览器手动处理，然后在后台继续。"
            logger.warning(msg)
            return "paused_need_human"

        if state == "ERROR":
            logger.error("页面出错，跳过: {}", keyword)
            return "failed"

        return "continue"
