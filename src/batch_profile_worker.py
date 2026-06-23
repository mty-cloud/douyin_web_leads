"""批量主页采集 — 独立子进程脚本。

打开浏览器，从 candidates 表捞 profile_pending 的记录逐个采集主页。
实时进度写入 progress_file，供 Dashboard 轮询展示。

用法:
    python src/batch_profile_worker.py --limit 50 --progress-file /tmp/progress.json
"""
import argparse
import json
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.settings import Settings
from src.db import Database
from src.browser.page_state import PageStateDetector
from src.browser.actions import DouyinActions
from src.browser.dom_extractor import DOMExtractor
from src.analysis.rule_scorer import RuleScorer
from src.analysis.lead_tier import validate_and_assign_tier

# ---------- 工具函数 ----------
FOOTER_KW = ["京ICP备", "京公网安", "广播电视", "增值电信", "网络文化",
             "广告投放", "用户服务协议", "营业执照"]


def _write_progress(progress_file: str, data: dict):
    """写进度到 JSON 文件。"""
    try:
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _is_footer(text: str) -> bool:
    """判断行是否为页脚/版权行。"""
    return any(kw in text for kw in FOOTER_KW)


# ---------- 采集函数 ----------
def run_batch_profile_collection(limit: int, progress_file: str):
    """批量采集主页核心逻辑。

    每处理完一个候选就更新一次进度文件。
    """
    settings = Settings()
    # 批量采集关掉截图和HTML快照（省 ~3s/个），只要 profile_text 就行
    settings._data.setdefault("profile", {})
    settings._data["profile"]["save_screenshot"] = False
    settings._data["profile"]["save_html_snapshot"] = False
    # 覆盖配置文件的 15 上限，让用户自己决定采集数量
    settings._data.setdefault("collection_budget", {})
    settings._data["collection_budget"]["max_profiles_per_run"] = 9999
    settings._data["collection_budget"]["max_profile_failures_per_run"] = 9999
    db = Database()
    state_detector = PageStateDetector()
    actions = DouyinActions(settings, state_detector)
    extractor = DOMExtractor()

    from src.browser.profile_flow import ProfileFlow
    from src.browser.browser_manager import launch_playwright_browser

    pw = None
    context = None
    page = None
    tws = []  # task results

    try:
        # 使用统一浏览器启动函数（自动杀残存进程 + 清锁 + 超时保护）
        pw, context, page = launch_playwright_browser(settings, use_persistent_context=True)

        class LocalBrowser:
            def __init__(self, ctx, pg, sttngs):
                self._context = ctx
                self._page = pg
                self.settings = sttngs

            def start(self):
                return self._page

            def get_main_page(self):
                return self._page

            def is_page_alive(self, p=None) -> bool:
                try:
                    _ = (p or self._page).url
                    return True
                except Exception:
                    return False

            def ensure_page_alive(self):
                return self._page

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

        # 先打底
        page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        # 批量采集
        flow = ProfileFlow(db, bm, actions, extractor, state_detector, None, settings)
        results = flow.collect_pending_profiles(limit=limit)

        # 统计
        captured = sum(1 for r in results if r.get("status") == "profile_captured")
        failed = sum(1 for r in results if r.get("status") in ("failed", "profile_incomplete"))
        paused = sum(1 for r in results if r.get("status") == "paused_need_human")

        # ── 自动分层：已采集主页的用户立即执行规则打分入库 ──
        scorer = RuleScorer()
        scored = 0
        for r in results:
            if r.get("status") != "profile_captured":
                continue
            cid = r.get("candidate_id")
            if not cid:
                continue
            try:
                # 从 DB 读取已更新的候选数据（含 profile_text 等）
                candidate = db.get_candidate_by_id(cid)
                if not candidate:
                    continue
                rule_result = scorer.score_lead(candidate)
                result = validate_and_assign_tier(candidate, rule_result, None)
                result["candidate_id"] = cid
                db.add_lead_analysis(result)
                db.update_candidate(cid, status="analyzed")
                scored += 1
            except Exception as se:
                print(f"⚠️ 自动分层失败 #{cid}: {se}", flush=True)

        if scored > 0:
            print(f"✅ 自动分层: {scored} 个已入库", flush=True)

        tws = [
            {
                "candidate_id": r.get("candidate_id", ""),
                "status": r.get("status", ""),
                "nickname": r.get("nickname", r.get("error", "")),
            }
            for r in results
        ]

        final = {
            "done": True,
            "total": len(results),
            "captured": captured,
            "failed": failed,
            "paused": paused,
            "scored": scored,
            "items": tws,
            "error": None,
        }
        _write_progress(progress_file, final)

        print(f"\n✅ 主页采集完成: 成功{captured}, 失败{failed}, 风控暂停{paused}, 自动分层{scored}", flush=True)

    except Exception as e:
        traceback.print_exc()
        final = {
            "done": True,
            "total": len(tws),
            "captured": sum(1 for t in tws if t.get("status") == "profile_captured"),
            "failed": sum(1 for t in tws if not t.get("status") == "profile_captured"),
            "paused": 0,
            "scored": 0,
            "items": tws,
            "error": str(e)[:500],
        }
        _write_progress(progress_file, final)
        print(f"\n❌ 采集异常: {e}", flush=True)

    finally:
        try:
            if page:
                page.close()
        except Exception:
            pass
        try:
            if context:
                context.close()
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
    parser = argparse.ArgumentParser(description="批量主页采集")
    parser.add_argument("--limit", type=int, default=50, help="本次采集数量")
    parser.add_argument("--progress-file", type=str, required=True, help="进度文件路径")
    args = parser.parse_args()

    run_batch_profile_collection(args.limit, args.progress_file)


if __name__ == "__main__":
    main()
