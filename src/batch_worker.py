"""采集工作子进程 — 在独立进程中执行搜索采集任务。

每个子进程完全独立，不共享任何全局状态。
使用 launch_persistent_context 保存/恢复登录 Cookie。

流程（仅搜索采集，不含主页采集）：
搜索关键词 → 提取用户卡片 → 入库 search_captured → 卡片评分 → 标记 profile_pending

⚠️ 主页采集由 batch_profile_worker.py 独立完成（在 3_主页采集.py 页面触发）。

用法:
    python src/batch_worker.py '<task_json>' <output_file>
"""

import sys
import json
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 初始化日志（写文件，不依赖 stdout/stderr）
from src.logger import setup_logger
setup_logger()
from loguru import logger


# ── 进度文件写入（供 Dashboard 实时读取） ──
_progress_file: str | None = None


def _set_progress_file(output_file: str):
    """设置进度文件路径（与输出文件关联）。

    修复：原来使用 replace('_batch_output.json', '_batch_status.json')，
    但输出文件名不含 _batch_output.json，导致 _progress_file == output_file，
    子进程最后的 unlink 会误删输出文件，父进程读取不到结果。
    现改为 .progress 后缀，与输出文件完全分离。
    """
    global _progress_file
    _progress_file = output_file + ".progress"


def _write_progress(phase: str, status: str, detail: str = "", value: float = 0):
    """写入当前进度到状态文件，Dashboard 线程可轮询读取。"""
    if not _progress_file:
        return
    try:
        with open(_progress_file, "w", encoding="utf-8") as f:
            json.dump({
                "phase": phase,
                "status": status,
                "detail": detail,
                "value": value,
                "ts": time.time(),
            }, f, ensure_ascii=False)
    except Exception:
        pass


def _run_one_task(task: dict) -> dict:
    """使用独立的 Playwright 实例执行一个采集任务。

    两阶段流程：
    1. 搜索 + 卡片评分（SearchFlow）
    2. 主页采集（ProfileFlow）
    """
    from src.settings import Settings
    from src.db import Database
    from src.browser.page_state import PageStateDetector
    from src.browser.actions import DouyinActions
    from src.browser.dom_extractor import DOMExtractor

    settings = Settings()
    db = Database()
    state_detector = PageStateDetector()
    actions = DouyinActions(settings, state_detector)
    extractor = DOMExtractor()

    pw = None
    context = None
    page = None
    _error_msg: str | None = None

    logger.info("=" * 50)
    logger.info("开始采集任务: {} {} → {} (关键词: {})",
                task.get("province",""), task.get("county",""),
                task.get("city",""), task.get("keyword",""))
    logger.info("=" * 50)

    try:
        _write_progress("browser", "starting", "正在启动浏览器...", 0.05)

        # 使用统一浏览器启动函数（自动杀残存进程 + 清锁 + 超时保护）
        from src.browser.browser_manager import launch_playwright_browser
        pw, context, page = launch_playwright_browser(settings, use_persistent_context=True)
        _write_progress("browser", "ok", "浏览器已启动", 0.1)

        # --- 极简 LocalBrowser 包装（供 search_flow / profile_flow 使用） ---
        class LocalBrowser:
            def __init__(self, ctx, pg, sttngs):
                self._context = ctx
                self._page = pg
                self.settings = sttngs

            def start(self):
                if self._context.pages:
                    self._page = self._context.pages[0]
                else:
                    self._page = self._context.new_page()
                return self._page

            def get_main_page(self):
                if self._page is None or not self._is_alive(self._page):
                    self._page = self.start()
                return self._page

            def _is_alive(self, p) -> bool:
                if not p:
                    return False
                try:
                    _ = p.url
                    return True
                except Exception:
                    return False

            def is_page_alive(self, p=None) -> bool:
                return self._is_alive(p or self._page)

            def ensure_page_alive(self, p=None) -> object:
                p = p or self._page
                if self._is_alive(p):
                    return p
                return self.start()

            def safe_goto(self, p, url, timeout=60000, wait_until="domcontentloaded", retries=3):
                for attempt in range(retries + 1):
                    if attempt > 0:
                        try:
                            p.close()
                        except Exception:
                            pass
                        p = self._context.new_page()
                    try:
                        p.goto(url, wait_until=wait_until, timeout=timeout)
                        return p
                    except Exception as e:
                        print(f"safegoto[{attempt+1}]: {str(e)[:100]}", flush=True)
                        if attempt < retries:
                            time.sleep(3 + attempt * 2)
                        else:
                            raise

            def save_screenshot(self, p, path):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                try:
                    p.screenshot(path=path, full_page=True)
                except Exception:
                    pass
                return path

            def save_html_snapshot(self, p, path):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                try:
                    html = p.content()
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(html)
                except Exception:
                    pass
                return path

            def new_page(self):
                return self._context.new_page()

            def close(self):
                pass

        bm = LocalBrowser(context, page, settings)

        # ── 首先导航到抖音首页（浏览器刚打开时在 about:blank，必须先打底） ──
        _write_progress("navigate", "loading", "正在导航到抖音首页...", 0.15)
        try:
            page.goto(settings.douyin_home_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            logger.info("已导航到抖音首页")
        except Exception as nav_e:
            logger.warning("导航首页失败（继续尝试搜索）: {}", nav_e)

        # ── 确保 task 有 id（Dashboard 创建的 ad-hoc 任务没有 id，需要先入库） ──
        if "id" not in task:
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _write_progress("db", "running", "正在写入任务到数据库...", 0.18)

            # 修复：先查是否有相同 province+city+county+keyword 的已有任务，复用其 id
            # 避免 UNIQUE(province, city, county, keyword) 约束冲突导致 INSERT 失败
            db.reconnect()
            existing_row = db.get_conn().execute(
                "SELECT id, status FROM keyword_tasks WHERE province=? AND city=? AND county=? AND keyword=?",
                (task.get("province",""), task.get("city",""), task.get("county",""), task.get("keyword",""))
            ).fetchone()
            if existing_row:
                task["id"] = existing_row["id"]
                # 重置为 pending，清除旧状态
                db.get_conn().execute(
                    "UPDATE keyword_tasks SET status='pending', error_message=NULL, started_at=NULL, finished_at=NULL WHERE id=?",
                    (task["id"],)
                )
                db.get_conn().commit()
                logger.info("已复用已有任务 #{} (原状态={}) → 重置为 pending", task["id"], existing_row["status"])
            else:
                _last_db_err = None
                for _db_attempt in range(3):
                    try:
                        # 每次重试都关闭旧连接重新连，避免 "database is locked" 后连接状态异常
                        db.reconnect()
                        conn = db.get_conn()
                        cur = conn.execute(
                            """INSERT INTO keyword_tasks
                               (province, city, county, keyword, category_tag, priority, status,
                                max_scroll, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                            (
                                task.get("province", ""),
                                task.get("city", ""),
                                task.get("county", ""),
                                task.get("keyword", ""),
                                task.get("category_tag", ""),
                                task.get("priority", "medium"),
                                task.get("max_scroll", 3),
                                now,
                            ),
                        )
                        conn.commit()
                        task["id"] = cur.lastrowid
                        _last_db_err = None
                        break
                    except Exception as db_err:
                        _last_db_err = db_err
                        logger.warning("DB 写入失败（第{}次）: {} — 等待{}秒后重连重试",
                                       _db_attempt + 1, db_err, 3 + _db_attempt * 3)
                        time.sleep(3 + _db_attempt * 3)
                if "id" not in task:
                    err_msg = f"数据库写入失败（重试 3 次）: {_last_db_err}"
                    logger.error(err_msg)
                    raise RuntimeError(err_msg)
            logger.info("keyword_task #{}: {}", task["id"], task.get('keyword',''))

        # ── 阶段 1：搜索采集 ──
        _write_progress("search", "running", f"正在搜索: {task.get('keyword','')}", 0.2)
        from src.browser.search_flow import SearchFlow
        search_flow = SearchFlow(db, bm, actions, extractor, state_detector, None, settings)
        search_result = search_flow.run_keyword_task(task)

        _search_status = search_result.get("status", "failed")
        _search_candidates = search_result.get("candidates", 0)
        _scored_pending = search_result.get("scored_pending", 0)

        # 汇总结果（仅搜索采集，主页采集由 batch_profile_worker.py 独立完成）
        # 修复：使用 _search_status 反映实际搜索状态，而非硬编码 "completed"
        # done -> completed, done_no_result -> completed (无结果也算完成), failed -> failed
        if _search_status in ("done", "done_no_result"):
            result_status = "completed"
        elif _search_status == "paused_need_human":
            result_status = "paused_need_human"
        else:
            result_status = "failed"

        result = {
            "province": task.get("province", ""),
            "city": task.get("city", ""),
            "county": task.get("county", ""),
            "keyword": task.get("keyword", ""),
            "candidates_found": _search_candidates,
            "scored_pending": _scored_pending,
            "candidates_added": _search_candidates,
            "status": result_status,
            "errors": [],
        }

        if _search_status == "paused_need_human":
            result["errors"].append("⚠️ 搜索阶段触发风控！请前往风控事件页面查看截图，处理验证码后恢复")

        return result

    except Exception as e:
        _error_msg = f"{type(e).__name__}: {e}"
        logger.error("采集任务异常: {}", _error_msg)
        traceback.print_exc()
        return {
            "province": task.get("province", ""),
            "county": task.get("county", ""),
            "keyword": task.get("keyword", ""),
            "candidates_found": 0,
            "scored_pending": 0,
            "candidates_added": 0,
            "status": "failed",
            "errors": [_error_msg],
        }
    finally:
        # ── 区分错误类型 ──
        # 只有浏览器/风控类错误才保持浏览器打开让人检查
        # DB locked / 网络不通 / 配置错误等系统级错误 → 直接退出
        _browser_kw = ["browser", "Browser", "Playwright", "playwright",
                       "Timeout", "timeout", "Navigation", "navigation",
                       "page", "Page", "context", "goto", "crash",
                       "risk", "paused_need_human", "风险", "验证", "unreachable"]
        _is_browser_err = _error_msg is not None and any(kw in _error_msg for kw in _browser_kw)

        if _error_msg and _is_browser_err:
            logger.warning("⚠️ 浏览器相关异常，保持浏览器打开供检查（最多 10 分钟后自动退出）")
            try:
                for _wait_sec in range(600):  # 最多等 10 分钟
                    time.sleep(1)
                    alive = False
                    if context and context.pages:
                        for p in context.pages:
                            try:
                                _ = p.url
                                alive = True
                                break
                            except Exception:
                                continue
                    if not alive:
                        logger.info("浏览器已关闭，子进程退出")
                        break
                else:
                    logger.warning("⏰ 10 分钟超时，强制关闭子进程")
            except Exception:
                pass

        # 关闭所有资源
        for obj in [page, context]:
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass


def main():
    if len(sys.argv) < 3:
        print("用法: python src/batch_worker.py '<task_json>' <output_file>")
        sys.exit(1)

    task_json = sys.argv[1]
    output_file = sys.argv[2]
    _set_progress_file(output_file)

    # ── 关键修复：先创建空输出文件，确保父进程永远能读到结果 ──
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("")

    try:
        _write_progress("init", "ok", f"子进程启动，准备解析 {len(task_json)} 字符任务数据...", 0.02)

        # 解析任务
        try:
            tasks = json.loads(task_json)
            if not isinstance(tasks, list):
                tasks = [tasks]
            _write_progress("init", "ok", f"解析到 {len(tasks)} 个任务", 0.03)
        except json.JSONDecodeError as e:
            result = {"error": f"JSON 解析失败: {e}"}
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
            sys.exit(1)

        # 执行采集
        results = [_run_one_task(task) for task in tasks]

        _write_progress("done", "ok", "所有任务已完成", 1.0)

        # 写结果
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({"all_results": results}, f, ensure_ascii=False, default=str)

    except Exception as main_e:
        # 兜底：任何未捕获的异常，也写入输出文件
        logger.critical("main() 未捕获异常: {}", main_e)
        traceback.print_exc()
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump({"error": f"子进程崩溃: {main_e}"}, f, ensure_ascii=False)
        except Exception:
            pass
        sys.exit(1)

    # 清理状态文件
    if _progress_file:
        try:
            Path(_progress_file).unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
