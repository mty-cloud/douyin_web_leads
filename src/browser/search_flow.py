"""搜索流程 — 自动搜索关键词并采集候选用户。

流程（v2）：
1. 关键词搜索 → 用户 tab → 全程滚动到 max_scroll 或到底
2. 所有唯一用户卡片评分后按评分倒序取 Top N
3. 只将 Top N 入库为 profile_pending
4. 滚动全程不因达标数量提早停止，确保每次搜索都滚完
"""

from pathlib import Path
from datetime import datetime

from loguru import logger

from src.risk_utils import (
    detect_risk_before_action,
    handle_risk_event,
    score_search_card,
)


class SearchFlow:
    """抖音搜索采集流程。"""

    # 噪音关键词（注意：版权年份在不同年份可能变化，但此列表当前未被引用）
    NOISE_KEYWORDS = [
        "开启读屏标签",
        "读屏标签已关闭",
        "下载抖音精选",
        "京ICP备",
        "京公网安",
        "认证徽章",
    ]

    def __init__(self, db, browser_manager, actions, extractor, state_detector, ocr, settings):
        self.db = db
        self.browser = browser_manager
        self.actions = actions
        self.extractor = extractor
        self.state_detector = state_detector
        self.ocr = ocr
        self.settings = settings
        self._stop_flag = False

    def stop(self):
        """设置停止标记。"""
        self._stop_flag = True
        logger.info("搜索流程已标记停止")

    def run_pending_tasks(self, limit: int = 5) -> list[dict]:
        """运行 N 个 pending 搜索任务，按 county 交错执行。"""
        tasks = self.db.get_pending_keyword_tasks(limit * 2)  # 多取一些用于交错

        # 交错排序：相同 county 不连续
        ordered = self._interleave_tasks(tasks)

        # 预算控制
        budget_limit = min(limit, self.settings.max_keywords_per_run)
        ordered = ordered[:budget_limit]

        results = []
        for task in ordered:
            if self._stop_flag:
                logger.info("搜索流程被手动停止")
                break
            result = self.run_keyword_task(task)
            results.append(result)

            # 检测到风控时自动停止后续任务
            if result.get("status") == "paused_need_human" and self.settings.stop_on_first_risk_event:
                logger.warning("遇到风控，停止后续搜索任务")
                break

        return results

    def _interleave_tasks(self, tasks: list[dict]) -> list[dict]:
        """交错排序：相同 county 不连续执行。"""
        if not tasks:
            return tasks

        # 按 county 分组
        by_county = {}
        for t in tasks:
            key = (t["province"], t["city"], t["county"])
            by_county.setdefault(key, []).append(t)

        # 按优先级排序，high 优先
        priority_order = {"high": 0, "medium": 1, "low": 2}
        for key in by_county:
            by_county[key].sort(key=lambda x: priority_order.get(x.get("priority", "medium"), 1))

        ordered = []
        while any(by_county.values()):
            for key in list(by_county.keys()):
                if by_county[key]:
                    ordered.append(by_county[key].pop(0))
                if not by_county[key]:
                    del by_county[key]

        return ordered

    def run_keyword_task(self, task: dict) -> dict:
        """执行单个关键词搜索任务。

        流程（v2 重写）：
        1. 搜索关键词 → 切用户 tab
        2. 全程滚动（到 max_scroll 或到底），提取所有卡片，不存库
        3. 所有卡片评分后，按评分倒序取 Top N（N = max_users / max_candidates）
        4. 只存 Top N 到数据库作为 profile_pending

        修复：
        - 使用 task 传入的 max_scroll/max_users 而非 settings 值（UI 控制才生效）
        - 不因达 candidate 上限提早停止滚动，确保每个关键词都滚完
        - 滚动完成后统一排名取 Top N，不浪费低分入库
        """
        task_id = task["id"]
        keyword = task["keyword"]
        # 优先使用 UI 传入的参数，fallback 到 settings
        max_scroll = task.get("max_scroll", self.settings.max_search_scrolls_per_keyword)
        max_users = task.get("max_users", self.settings.max_new_candidates_per_keyword)
        threshold = self.settings.card_score_threshold

        logger.info("=" * 50)
        logger.info("搜索任务 #{}: {} (滚动上限={}, 目标收集Top={}, 评分阈值={})",
                     task_id, keyword, max_scroll, max_users, threshold)
        logger.info("=" * 50)

        # 标记 running
        self.db.update_task_status(task_id, "running")

        try:
            page = self.browser.get_main_page()

            # 1. 检查当前页面状态
            current_state = self.state_detector.detect(page)
            if current_state in ("HOME", "SEARCH_RESULT_ALL", "SEARCH_RESULT_USER"):
                self.actions.close_safe_popup(page)
            else:
                logger.info("页面状态={}，导航到首页", current_state)
                if not self.actions.open_home(page):
                    raise RuntimeError("打开首页失败")

            # 2. 关闭弹窗
            self.actions.close_safe_popup(page)

            # 3. 搜索关键词
            searched = self.actions.search_keyword(page, keyword)
            if not searched:
                from urllib.parse import quote
                search_url = f"https://www.douyin.com/search/{quote(keyword)}"
                _search_ok = False
                for _nav_attempt in range(2):
                    try:
                        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(3000)
                        logger.info("通过直接导航搜索（第{}次）: {}", _nav_attempt + 1, keyword)
                        _search_ok = True
                        break
                    except Exception as e2:
                        logger.warning("导航搜索失败（第{}次）: {}", _nav_attempt + 1, e2)
                        if _nav_attempt == 0:
                            page.wait_for_timeout(2000)
                if not _search_ok:
                    raise RuntimeError(f"搜索关键词失败（重试2次仍失败）")

            # 风控检测
            is_risk, risk_word = detect_risk_before_action(page, "搜索后检测")
            if is_risk:
                handle_risk_event(page, self.db, keyword=keyword,
                                  action_when_triggered=f"搜索后检测到风控: {risk_word}")
                self.db.update_task_status(task_id, "paused_need_human",
                                           error_message=f"⚠️ 触发风控: {risk_word} — 请前往风控事件页面查看截图, 处理验证码后恢复采集")
                # 记录风控阻断到 collection_history（candidates_found=0 默认值，
                # 不会被 get_collected_counties_set() 识别为"已采集"）
                try:
                    self.db.add_collection_history(
                        province=task.get("province", ""),
                        city=task.get("city", ""),
                        county=task.get("county", ""),
                        keyword=keyword,
                        max_scroll=max_scroll,
                        status="risk_blocked",
                    )
                except Exception:
                    pass
                return {"task_id": task_id, "status": "paused_need_human",
                        "candidates": 0}

            # 4. 点击用户 tab
            self.actions.click_user_tab(page)

            # 5. ⭐ v2: 全程滚动收集所有卡片，滚动完成后排名取 Top N
            all_scored = []            # [(card_info, score_result), ...]
            seen_profile_urls = set()  # 去重
            no_new_scrolls = 0         # 连续几屏无新卡片
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            for scroll_index in range(max_scroll):
                if self._stop_flag:
                    break

                self.actions._random_delay(500, 1200)
                self.actions.close_safe_popup(page)

                # 风控检测
                is_risk, risk_word = detect_risk_before_action(page, "搜索滚动中检测")
                if is_risk:
                    handle_risk_event(page, self.db, keyword=keyword,
                                      action_when_triggered=f"搜索中检测到风控: {risk_word}")
                    self.db.update_task_status(task_id, "paused_need_human",
                                               error_message=f"⚠️ 触发风控: {risk_word} — 请前往风控事件页面查看截图, 处理验证码后恢复采集")
                    try:
                        self.db.add_collection_history(
                            province=task.get("province", ""),
                            city=task.get("city", ""),
                            county=task.get("county", ""),
                            keyword=keyword,
                            max_scroll=max_scroll,
                            status="risk_blocked",
                        )
                    except Exception:
                        pass
                    return {"task_id": task_id, "status": "paused_need_human",
                            "candidates": len(all_scored)}

                # 提取当前可见的所有用户卡片
                cards = self.extractor.extract_visible_user_cards(page)

                # 保存截图和 HTML（调试用）
                screenshot_path = f"data/screenshots/search/{task_id}_{scroll_index}_{timestamp}.png"
                self.browser.save_screenshot(page, screenshot_path)

                html_path = f"data/html_snapshots/search/{task_id}_{scroll_index}_{timestamp}.html"
                self.browser.save_html_snapshot(page, html_path)

                # 收集本屏所有新卡片（存内存，不存库）
                new_in_this_scroll = 0
                for card in cards:
                    if self._stop_flag:
                        break

                    profile_url = card.get("profile_url", "")
                    if not profile_url or profile_url.count("/") < 3:
                        continue
                    if "/user/self" in profile_url or "passport" in profile_url or "login" in profile_url:
                        continue
                    if profile_url in seen_profile_urls:
                        continue

                    seen_profile_urls.add(profile_url)
                    new_in_this_scroll += 1

                    card_text = card.get("search_card_text", "")
                    nick_candidate = ""
                    if card_text:
                        from src.risk_utils import extract_nickname_from_card_text
                        nick_candidate = extract_nickname_from_card_text(card_text)

                    followers_text = self._extract_followers_from_card(card_text)

                    # 评分
                    score_result = score_search_card(
                        nickname_candidate=nick_candidate,
                        search_card_text=card_text,
                        source_keyword=keyword,
                        followers_text=followers_text,
                    )

                    card_info = {
                        "nickname": nick_candidate or card.get("nickname", ""),
                        "douyin_id": card.get("douyin_id", "") or card.get("sec_uid", ""),
                        "profile_url": profile_url,
                        "search_card_text": card_text[:2000],
                        "followers_text": followers_text,
                        "screenshot_path": screenshot_path,
                    }
                    all_scored.append((card_info, score_result))

                self.actions._random_delay(200, 500)

                logger.info("第{}屏: 新增{}个唯一卡片, 累计{}个",
                            scroll_index + 1, new_in_this_scroll, len(all_scored))

                # 连续无新卡片 → 到底了
                if new_in_this_scroll == 0:
                    no_new_scrolls += 1
                    logger.info("连续 {} 屏无新卡片", no_new_scrolls)
                    if no_new_scrolls >= 2:
                        logger.info("连续 2 屏无新卡片，停止滚动")
                        break
                else:
                    no_new_scrolls = 0

                # 滚动到下一屏
                if scroll_index < max_scroll - 1:
                    self.actions.scroll_results(page)
                    self.actions._random_delay(500, 1000)

            # ⭐ v2: 全部滚动完成 → 先按阈值过滤 → 再按评分倒序取 Top N
            # 只有卡片自身内容含"女装/服装/服饰"的才过 threshold，
            # 旅游/装修/娱乐等完全不相关的卡片 score=0，直接被过滤掉
            above_threshold = [(c, s) for c, s in all_scored if s["card_score"] >= threshold]
            total_filtered_out = len(all_scored) - len(above_threshold)
            if total_filtered_out > 0:
                logger.info("阈值过滤: {}个低于{}分（不相关/无服装关键词）", total_filtered_out, threshold)

            above_threshold.sort(key=lambda x: x[1]["card_score"], reverse=True)
            top_n = above_threshold[:max_users]

            total_unique = len(all_scored)
            total_qualified = len(top_n)

            # 只存 Top N 到数据库
            for card_info, score_result in top_n:
                candidate_data = {
                    "nickname": card_info["nickname"],
                    "douyin_id": card_info["douyin_id"],
                    "profile_url": card_info["profile_url"],
                    "source_province": task.get("province", ""),
                    "source_city": task.get("city", ""),
                    "source_county": task.get("county", ""),
                    "source_keywords": keyword,
                    "source_category_tags": task.get("category_tag", ""),
                    "search_card_text": card_info["search_card_text"],
                    "search_page_url": page.url,
                    "search_screenshot_path": card_info["screenshot_path"],
                    "followers_text": card_info["followers_text"],
                    "dedupe_key": card_info["profile_url"],
                    "card_score": score_result["card_score"],
                    "card_evidence": score_result["card_evidence"],
                    "card_negative_evidence": score_result["card_negative_evidence"],
                    "status": "profile_pending",
                }
                cid = self.db.add_candidate(candidate_data)
                if cid:
                    self.db.update_candidate(
                        cid,
                        card_score=score_result["card_score"],
                        card_evidence=score_result["card_evidence"],
                        card_negative_evidence=score_result["card_negative_evidence"],
                    )

            # 更新搜索任务状态
            status = "done" if total_qualified > 0 else "done_no_result"
            self.db.update_task_status(task_id, status, found_count=total_qualified)

            # 写 collection_history
            # 修复：只在找到候选用户时写，避免 "已采集县城" 误标无数据县城
            if total_qualified > 0:
                try:
                    self.db.add_collection_history(
                        province=task.get("province", ""),
                        city=task.get("city", ""),
                        county=task.get("county", ""),
                        keyword=keyword,
                        max_scroll=max_scroll,
                    )
                    self.db.finish_collection_history(
                        self.db.get_conn().execute(
                            "SELECT MAX(id) as mid FROM collection_history"
                        ).fetchone()["mid"],
                        candidates_found=total_qualified,
                    )
                except Exception:
                    pass

            logger.info("搜索任务 #{} 完成: 关键词={}, 滚动收集{}个唯一用户, Top{}取{}个",
                        task_id, keyword, total_unique, max_users, total_qualified)

            return {"task_id": task_id, "status": status, "candidates": total_qualified,
                    "scored_pending": total_qualified}

        except Exception as e:
            logger.error("搜索任务 #{} 失败: {}", task_id, e)
            self.db.update_task_status(task_id, "failed", error_message=str(e))
            return {"task_id": task_id, "status": "failed", "error": str(e)}

    def _extract_followers_from_card(self, text: str) -> str:
        """从搜索卡片文本中提取粉丝数文本。"""
        if not text:
            return ""
        import re
        m = re.search(r'([0-9.]+)\s*万?\s*[粉丝关注]', text)
        if m:
            return m.group(0)
        return ""
