"""主页采集流程 — 自动打开候选用户主页并采集信息。

根据《风控解决方案》重构：
1. 使用新标签页打开主页（不依赖 go_back）
2. 主页文本有效性校验（is_valid_profile_text）
3. 正确昵称选择逻辑（优先主页昵称，回退到搜索卡片文本）
4. 主页失效时设置 profile_incomplete 并记录原因
5. 不提取昵称、不运行匹配、不标记 profile_captured
"""

from pathlib import Path
from datetime import datetime
import time

from loguru import logger

from src.risk_utils import (
    detect_risk_before_action,
    handle_risk_event,
    is_valid_profile_text,
    is_valid_nickname,
    extract_nickname_from_card_text,
)


class ProfileFlow:
    """用户主页采集流程。"""

    def __init__(self, db, browser_manager, actions, extractor, state_detector, ocr, settings):
        self.db = db
        self.browser = browser_manager
        self.actions = actions
        self.extractor = extractor
        self.state_detector = state_detector
        self.ocr = ocr
        self.settings = settings
        self._stop_flag = False
        self._profile_failures = 0

    def stop(self):
        self._stop_flag = True
        logger.info("主页采集已标记停止")

    @property
    def profile_failures(self) -> int:
        return self._profile_failures

    def collect_pending_profiles(self, limit: int = 20) -> list[dict]:
        """采集 N 个候选用户主页。

        遵循预算约束：
        - max_profiles_per_run
        - max_profile_failures_per_run
        """
        # 预算控制
        max_profiles = self.settings.max_profiles_per_run
        max_failures = self.settings.max_profile_failures_per_run
        actual_limit = min(limit, max_profiles)

        candidates = self.db.get_candidates_for_profile_collection(actual_limit)
        results = []
        for candidate in candidates:
            if self._stop_flag:
                break

            # 检查失败次数预算
            if self._profile_failures >= max_failures:
                logger.warning("主页失败次数已达上限 ({})，停止采集", max_failures)
                break

            result = self.collect_one_profile(candidate)
            results.append(result)

            if result.get("status") == "profile_failed":
                self._profile_failures += 1

            # 每次采集之间留 1-2s 间隔，确保页面完全关闭、下次加载充分
            if not self._stop_flag:
                time.sleep(2)

        return results

    def collect_one_profile(self, candidate: dict) -> dict:
        """采集单个用户主页 — 使用新标签页。"""
        cid = candidate["id"]
        nickname = candidate.get("nickname", "")
        profile_url = candidate.get("profile_url", "")
        search_card_text = candidate.get("search_card_text", "")
        logger.info("开始采集主页: #{} {} -> {}", cid, nickname, profile_url)

        if not profile_url:
            self.db.update_candidate(cid, status="profile_failed",
                                     profile_incomplete_reason="无主页链接")
            return {"candidate_id": cid, "status": "failed", "error": "无主页链接"}

        # 标记 profile_pending（确保状态正确）
        self.db.update_candidate(cid, status="profile_pending")

        context = None
        profile_page = None
        try:
            # 从主页面 context 获取（BrowserManager 管理）
            context = self.browser.get_main_page().context

            # 新标签页打开
            profile_page = context.new_page()
            profile_page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            # 等待主要内容加载完成，不用硬等 2s
            try:
                profile_page.wait_for_function(
                    "() => document.body.innerText.includes('抖音号')",
                    timeout=8000,
                )
            except Exception:
                profile_page.wait_for_timeout(1000)  # 8s 内没出现也最多等 1s

            # 关闭弹窗
            self.actions.close_safe_popup(profile_page)

            # --- 风控检测（增强版：文本检测 + URL检测 + 页面不可达检测） ---
            is_risk, risk_word = detect_risk_before_action(profile_page, "主页采集")
            if is_risk:
                handle_risk_event(profile_page, self.db,
                                  keyword=candidate.get("source_keywords", ""),
                                  action_when_triggered=f"主页采集风控: {risk_word}")
                self.db.update_candidate(cid, status="profile_failed",
                                         profile_incomplete_reason=f"风控: {risk_word}")
                return {"candidate_id": cid, "status": "paused_need_human", "risk_word": risk_word}

            # 点击「更多」展开
            self.actions.click_more_button(profile_page)
            self.actions._random_delay(200, 400)

            # --- 提取主页信息 ---
            profile_info = self.extractor.extract_profile_info(profile_page)

            # --- 主页文本有效性校验 ---
            profile_text = profile_info.get("profile_text", "")
            valid, reason = is_valid_profile_text(profile_text)

            if not valid:
                logger.warning("主页文本无效 #{}: reason={}", cid, reason)
                self.db.update_candidate(
                    cid,
                    status="profile_incomplete",
                    profile_incomplete_reason=reason,
                )
                return {"candidate_id": cid, "status": "profile_incomplete", "reason": reason}

            # --- 保存截图和 HTML ---
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = ""
            html_path = ""

            if self.settings.profile_save_screenshot:
                screenshot_path = f"data/screenshots/profile/{cid}_{timestamp}.png"
                self.browser.save_screenshot(profile_page, screenshot_path)

            if self.settings.profile_save_html_snapshot:
                html_path = f"data/html_snapshots/profile/{cid}_{timestamp}.html"
                self.browser.save_html_snapshot(profile_page, html_path)

            # --- 昵称选择逻辑 ---
            profile_nick = profile_info.get("nickname", "")
            card_nick = extract_nickname_from_card_text(search_card_text)

            final_nick = ""
            if is_valid_nickname(profile_nick):
                final_nick = profile_nick
            elif is_valid_nickname(card_nick):
                final_nick = card_nick
            else:
                final_nick = ""

            # --- 匹配目标画像 ---
            mr = self.extractor.match_target_profile(profile_info)
            is_match = mr["matched"]

            # 提取微信
            wechat_id = self._extract_wechat(profile_info)

            # 抖音号
            profile_douyin_id = profile_info.get("douyin_id", "")
            fallback_sec_uid = candidate.get("douyin_id", "")
            sec_uid_from_url = profile_info.get("sec_uid", "")
            final_douyin_id = profile_douyin_id or sec_uid_from_url or fallback_sec_uid or ""

            # --- 更新候选用户信息 ---
            # 只写入主页独有的字段，不覆盖搜索阶段已有的 nickname/指标等
            update_data = {
                "status": "profile_captured",
                "douyin_id": final_douyin_id,
                "profile_bio": (profile_info.get("profile_bio") or "")[:500],
                "profile_text": profile_text[:5000],
                "profile_screenshot_path": screenshot_path,
                "profile_html_snapshot_path": html_path,
                "wechat_id": wechat_id or "",
                "wechat_extracted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S") if wechat_id else "",
            }
            # 仅当搜索阶段没采到昵称时才用主页的
            if not candidate.get("nickname") and final_nick:
                update_data["nickname"] = final_nick
            self.db.update_candidate(cid, **update_data)

            # 写入 profile_captures 记录
            self.db.add_profile_capture(
                candidate_id=cid,
                profile_url=profile_url,
                screenshot_path=screenshot_path,
                html_snapshot_path=html_path,
                dom_text=profile_text[:5000],
            )

            logger.info("主页采集成功: #{} {} (匹配={})", cid, final_nick or nickname, is_match)
            return {
                "candidate_id": cid,
                "status": "profile_captured",
                "is_match": is_match,
                "nickname": final_nick,
            }

        except Exception as e:
            logger.error("采集主页失败 #{} {}: {}", cid, nickname, e)
            self.db.update_candidate(cid, status="profile_failed",
                                     profile_incomplete_reason=str(e)[:200])
            return {"candidate_id": cid, "status": "failed", "error": str(e)}

        finally:
            if profile_page:
                try:
                    profile_page.close()
                except Exception:
                    pass

    def _extract_wechat(self, profile_info: dict) -> str:
        try:
            from src.wechat_extractor import extract_best
            combined = " ".join([
                profile_info.get("profile_bio", ""),
                profile_info.get("profile_text", ""),
            ])
            return extract_best(combined) or ""
        except Exception:
            return ""
