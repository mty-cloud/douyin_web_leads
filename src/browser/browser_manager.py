"""浏览器管理器。

为所有采集流程（dashboard、batch_worker、batch_profile_worker）提供：
1. 启动前杀死残存 Chrome 进程 + 清理锁文件
2. 启动带超时保护（60s），卡住自动杀进程重试
3. 登录态统一通过 storage_state.json 持久化
"""

import os
import subprocess
import threading
import time
import json
from pathlib import Path

from loguru import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STORAGE_FILE = str(_PROJECT_ROOT / "data" / "browser_profile" / "storage_state.json")


# ---------------------------------------------------------------------------
# 进程清理 + 锁文件清理
# ---------------------------------------------------------------------------

def _cleanup_chrome_processes(force: bool = True):
    """杀死所有残存 Chromium/Playwright 相关进程。

    在启动浏览器前调用，避免旧进程占用 profile 锁。
    """
    import signal
    patterns = ["chromium", "playwright-chromium", "Chrome for Testing", "chrome_crashpad"]
    for pat in patterns:
        try:
            r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True, timeout=5)
            pids = [int(p) for p in r.stdout.strip().split() if p.strip()]
            for pid in pids:
                try:
                    if force:
                        os.kill(pid, signal.SIGKILL)
                    else:
                        os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except Exception:
                    pass
        except Exception:
            pass


def _clean_lock_files(profile_dir: str):
    """清理 Chrome profile 目录的锁文件，防止 Chrome 启动时卡住。"""
    profile_path = Path(profile_dir)
    if not profile_path.exists():
        profile_path.mkdir(parents=True, exist_ok=True)
        return
    lock_names = {"SingletonLock", "SingletonCookie", "SingletonSocket",
                  "LOCK", "lock", "shutdown", "First Run",
                  "Crashpad", "BrowserMetrics"}
    for root, dirs, files in os.walk(str(profile_path)):
        for fname in files:
            if any(x in fname for x in lock_names):
                try:
                    os.unlink(os.path.join(root, fname))
                except Exception:
                    pass
        # 也清理空的 BrowserMetrics 目录
        for dname in dirs:
            if dname == "BrowserMetrics":
                try:
                    for mf in os.listdir(os.path.join(root, dname)):
                        os.unlink(os.path.join(root, dname, mf))
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# 一键：清理 → 启动（含重试）
# ---------------------------------------------------------------------------
# 注意：Playwright Sync API 使用 greenlet 实现，所有 API 调用必须在
# 同一线程执行。不能将 launch/launch_persistent_context 放到子线程做
# 超时保护，否则会触发 "Cannot switch to a different thread" 错误。
# 这里依靠 _cleanup_chrome_processes 的重试机制来做容错。


def launch_playwright_browser(settings, use_persistent_context: bool = False) -> tuple:
    """完整浏览器启动流程：杀进程 → 清锁 → 启动 → 重试。

    Args:
        settings: Settings 实例
        use_persistent_context: True = 用 launch_persistent_context（batch_worker用）
                               False = 用 launch() + new_context()（dashboard用）

    Returns:
        (pw, browser_or_context, page_or_context)
        use_persistent_context=True  → (pw, context, page)
        use_persistent_context=False → (pw, browser, context)
    """
    profile_dir = settings.browser_user_data_dir

    # 1. 清理残存进程和锁文件
    _cleanup_chrome_processes(force=True)
    _clean_lock_files(profile_dir)
    time.sleep(1)

    from playwright.sync_api import sync_playwright

    last_error = None
    for attempt in range(2):  # 最多重试 1 次
        pw = None
        try:
            pw = sync_playwright()
            pw = pw.start()

            if use_persistent_context:
                # batch_worker 模式: launch_persistent_context
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=False,
                    no_viewport=True,
                    args=[
                        f"--window-size={settings.viewport_width},{settings.viewport_height}",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-gpu",
                        "--no-sandbox",
                    ],
                )
                page = context.pages[0] if context.pages else context.new_page()
                return pw, context, page
            else:
                # dashboard 模式: launch() + new_context()
                browser = pw.chromium.launch(
                    headless=False,
                    args=[
                        f"--window-size={settings.viewport_width},{settings.viewport_height}",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                    ],
                )
                # 加载登录态
                storage_state_path = Path(STORAGE_FILE)
                if storage_state_path.exists():
                    context = browser.new_context(
                        no_viewport=True,
                        storage_state=str(storage_state_path),
                    )
                else:
                    context = browser.new_context(no_viewport=True)
                return pw, browser, context

        except Exception as e:
            last_error = e
            logger.warning("浏览器启动第 {} 次失败: {}，准备重试（清理+重启）", attempt + 1, e)
            # 清理本次尝试的 Playwright 实例
            if pw:
                try:
                    pw.stop()
                except Exception:
                    pass
            _cleanup_chrome_processes(force=True)
            _clean_lock_files(profile_dir)
            time.sleep(2)

    raise RuntimeError(f"浏览器启动失败（已重试 2 次）: {last_error}")


# ---------------------------------------------------------------------------
# 全局状态（仅用于 dashboard 的 BrowserManager）
# ---------------------------------------------------------------------------

_launched_pw = None
_launched_browser = None
_launched_context = None
_browser_ok = False


def _force_stop():
    """完全停止 Playwright。"""
    global _launched_pw, _launched_browser, _launched_context, _browser_ok
    _browser_ok = False
    try:
        if _launched_context:
            try:
                Path(STORAGE_FILE).parent.mkdir(parents=True, exist_ok=True)
                _launched_context.storage_state(path=STORAGE_FILE)
                logger.info("💾 登录态已保存")
            except Exception:
                pass
            _launched_context.close()
    except Exception:
        pass
    try:
        if _launched_browser:
            _launched_browser.close()
    except Exception:
        pass
    try:
        if _launched_pw:
            _launched_pw.stop()
    except Exception:
        pass
    _launched_pw = None
    _launched_browser = None
    _launched_context = None


def ensure_chrome_running(settings) -> object:
    """启动 Playwright 浏览器 + 上下文（Dashboard 用）。"""
    global _launched_pw, _launched_browser, _launched_context, _browser_ok

    if _browser_ok and _launched_context is not None:
        try:
            if _launched_browser and _launched_browser.is_connected():
                return _launched_context
        except Exception:
            pass

    _force_stop()

    try:
        pw, browser, context = launch_playwright_browser(settings, use_persistent_context=False)
        _launched_pw = pw
        _launched_browser = browser
        _launched_context = context
        _browser_ok = True
        logger.info("✅ 浏览器已就绪")
        return context
    except Exception as e:
        _browser_ok = False
        raise RuntimeError(f"浏览器启动失败: {e}")


def get_main_page(settings):
    """获取可用的浏览器标签页。"""
    context = ensure_chrome_running(settings)
    try:
        page = context.new_page()
        logger.info("✅ 创建新标签页")
        return page
    except Exception as e:
        _browser_ok = False
        raise RuntimeError(f"创建标签页失败: {e}")


def close_browser(settings):
    """关闭浏览器并保存登录态。"""
    _force_stop()
    logger.info("浏览器已关闭")


def open_browser_window(settings):
    """打开浏览器并导航到抖音首页。"""
    page = get_main_page(settings)
    try:
        page.goto(settings.douyin_home_url, wait_until="commit", timeout=60000)
        logger.info("已打开抖音首页")
    except Exception as e:
        logger.warning("导航到首页失败: {}", e)
    return page


# ---------------------------------------------------------------------------
# 安全进程管理（供 Dashboard 按钮用）
# ---------------------------------------------------------------------------

def safe_kill_chromium(force: bool = False) -> None:
    """安全关闭 Chromium/Playwright 相关进程。"""
    _force_stop()
    _cleanup_chrome_processes(force=force)
    _clean_lock_files(str(_PROJECT_ROOT / "data" / "browser_profile"))
    logger.info("Chromium 进程已清理，锁文件已清除")


# ---------------------------------------------------------------------------
# BrowserManager（兼容旧接口，供 SearchFlow / ProfileFlow 使用）
# ---------------------------------------------------------------------------

class BrowserManager:
    def __init__(self, settings):
        self.settings = settings

    def start(self):
        return get_main_page(self.settings)

    def get_main_page(self):
        return get_main_page(self.settings)

    def is_page_alive(self, page) -> bool:
        if not page:
            return False
        try:
            _ = page.url
            return True
        except Exception:
            return False

    def ensure_page_alive(self, page) -> object:
        if self.is_page_alive(page):
            return page
        logger.warning("页面不可用，重启浏览器...")
        _force_stop()
        time.sleep(2)
        return self.start()

    def _force_reconnect(self) -> object:
        _force_stop()
        time.sleep(2)
        return self.start()

    def _close_connection(self):
        _force_stop()

    def safe_goto(self, page, url: str, timeout: int = 30000,
                   wait_until: str = "domcontentloaded", retries: int = 2) -> object:
        for attempt in range(retries + 1):
            if attempt > 0:
                try:
                    page.close()
                except Exception:
                    pass
                try:
                    page = self.start()
                except Exception:
                    page = self._force_reconnect()

            try:
                page.goto(url, wait_until=wait_until, timeout=timeout)
                return page
            except Exception as e:
                err_str = str(e)
                logger.warning("goto 失败（第 {} 次）: {}", attempt + 1, err_str[:120])
                if "cannot switch to a different thread" in err_str:
                    logger.warning("检测页面线程死亡，完全重启浏览器...")
                    _force_stop()
                    time.sleep(3)
                    if attempt < retries:
                        continue
                    raise RuntimeError(f"浏览器彻底不可用（已重试 {retries + 1} 次）: {e}")
                if attempt < retries:
                    time.sleep(2 + attempt * 2)
                else:
                    raise

    def new_page(self):
        context = ensure_chrome_running(self.settings)
        return context.new_page()

    def save_screenshot(self, page, path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=path, full_page=True)
        except Exception as e:
            logger.warning("截图失败: {}", e)
        return path

    def close(self):
        pass
