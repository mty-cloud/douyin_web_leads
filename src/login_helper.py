"""登录辅助脚本 — 打开独立 Chromium 供用户登录抖音。

用法: python src/login_helper.py

使用 launch_persistent_context 保存登录 Cookie 到 data/browser_profile/。
登录后关闭浏览器窗口即可，登录态自动持久化。
"""

import sys
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.settings import Settings


def main():
    settings = Settings()
    profile_dir = settings.browser_user_data_dir
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    # 清理锁文件
    for root, dirs, files in os.walk(profile_dir):
        for fname in files:
            if any(x in fname for x in ["Singleton", "LOCK", "lock", "shutdown"]):
                try:
                    os.unlink(os.path.join(root, fname))
                except Exception:
                    pass

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=False,
        no_viewport=True,
        args=[
            "--no-first-run",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )

    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(settings.douyin_home_url, timeout=60000)
    print("✅ 浏览器已打开，请在窗口中扫码登录抖音", flush=True)
    print("💡 登录后关闭浏览器窗口，登录态会自动保存", flush=True)

    try:
        while True:
            time.sleep(3)
            # 检查窗口是否还活着
            alive = False
            for p in ctx.pages:
                try:
                    _ = p.url
                    alive = True
                    break
                except Exception:
                    continue
            if not alive:
                break
    except KeyboardInterrupt:
        pass
    finally:
        # 保存 storage_state.json（供 dashboard 模式使用）
        try:
            storage_path = Path(settings.browser_user_data_dir).parent.parent / "data" / "browser_profile" / "storage_state.json"
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(storage_path))
            print(f"💾 storage_state.json 已保存", flush=True)
        except Exception:
            pass

        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass

    print("✅ 浏览器已关闭，登录态已保存", flush=True)


if __name__ == "__main__":
    main()
