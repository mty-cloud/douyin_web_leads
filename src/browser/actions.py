"""抖音网页版操作封装 —— 反爬虫优化版。

核心改进：
1. 所有操作加入随机延迟 + 鼠标轨迹模拟
2. 更自然的关键词输入（逐字 random 延迟）
3. 处理主页"更多"展开按钮
4. 滚动带随机偏移和间歇停顿
5. 智能弹窗关闭策略
"""

import random
import time

from loguru import logger
from playwright.sync_api import Page


class DouyinActions:
    """封装抖音网页版的常见操作（反爬虫优化版）。"""

    def __init__(self, settings, state_detector):
        self.settings = settings
        self.state_detector = state_detector

    # ------------------------------------------------------------------
    # 内部工具（反爬虫优化）
    # ------------------------------------------------------------------
    def _random_delay(self, min_ms: float = 100, max_ms: float = 400):
        """随机等待，模拟人类操作间隔。"""
        time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

    def _human_pause(self, base_seconds: float = 1.0):
        """人类化暂停：基准时间 + 随机抖动 + 偶尔的长停顿。

        模拟真实人类的操作间隙：大部分时候快，偶尔慢（看手机、思考等）。
        """
        jitter = random.uniform(-0.3, 0.8)
        pause = base_seconds + jitter
        # 15% 概率来一次「思考停顿」— 额外加 1-3s
        if random.random() < 0.15:
            pause += random.uniform(1.0, 3.0)
        time.sleep(max(0.3, pause))

    def _random_scroll_step(self) -> int:
        """随机滚动步长 300~1200 px。"""
        return random.randint(300, 1200)

    def _move_mouse_to(self, page: Page, x: int, y: int):
        """模拟鼠标移动到目标位置（带随机偏移和速度变化）。"""
        try:
            cur_x, cur_y = 200, 200
            try:
                current = page.evaluate("({x: window.mouseX || 200, y: window.mouseY || 200})")
                cur_x, cur_y = current["x"], current["y"]
            except Exception:
                pass

            steps = random.randint(5, 12)
            for i in range(1, steps + 1):
                t = i / steps
                # 带缓动效果的贝塞尔近似
                ease_t = t * t * (3 - 2 * t)
                mx = cur_x + (x - cur_x) * ease_t + random.randint(-3, 3)
                my = cur_y + (y - cur_y) * ease_t + random.randint(-3, 3)
                try:
                    page.mouse.move(mx, my, steps=1)
                except Exception:
                    pass
                time.sleep(random.uniform(0.01, 0.03))
        except Exception:
            # 兜底：直接移动
            try:
                page.mouse.move(x, y)
            except Exception:
                pass

    def _human_click(self, page: Page, x: int, y: int):
        """模拟人类点击：移动到目标 → 微抖 → 点击。"""
        self._move_mouse_to(page, x, y)
        self._random_delay(50, 200)
        # 微小的最后调整
        try:
            page.mouse.move(x + random.randint(-2, 2), y + random.randint(-2, 2), steps=1)
        except Exception:
            pass
        self._random_delay(30, 100)
        try:
            page.mouse.click(x, y, delay=random.randint(30, 120))
        except Exception:
            pass

    def _try_click_element(self, page: Page, locator, timeout: int = 5000):
        """安全点击一个元素，使用人类化点击。"""
        try:
            el = locator.first
            if el.count() == 0 or not el.is_visible(timeout=2000):
                return False
            box = el.bounding_box(timeout=2000)
            if box:
                cx = box["x"] + box["width"] / 2 + random.randint(-5, 5)
                cy = box["y"] + box["height"] / 2 + random.randint(-5, 5)
                self._human_click(page, cx, cy)
            else:
                el.click(timeout=timeout, delay=random.randint(30, 100))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 核心操作
    # ------------------------------------------------------------------

    def open_home(self, page: Page) -> bool:
        """打开抖音首页。"""
        try:
            page.goto(self.settings.douyin_home_url, wait_until="commit", timeout=60000)
            self._random_delay(500, 1000)
            logger.info("已打开抖音首页")
            return True
        except Exception as e:
            logger.warning("打开首页失败（重试一次）: {}", e)
            try:
                page.goto(self.settings.douyin_home_url, wait_until="commit", timeout=60000)
                self._random_delay(500, 1000)
                logger.info("重试成功，已打开抖音首页")
                return True
            except Exception as e2:
                logger.error("打开首页失败（重试也失败）: {}", e2)
                return False

    def search_keyword(self, page: Page, keyword: str) -> bool:
        """搜索关键词（反爬虫优化版）。"""
        logger.info("正在搜索关键词: {}", keyword)
        self._random_delay(300, 800)

        # 多策略寻找搜索框（修复：去掉泛匹配的 "input"，避免点到非搜索输入框）
        selectors = [
            "input[placeholder*='搜索']",
            "input[placeholder*='搜']",
            "[contenteditable='true']",
            "input[placeholder*='大家都在搜']",
        ]

        search_box = None
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible(timeout=1500):
                    search_box = loc
                    logger.debug("找到搜索框: selector={}", selector)
                    break
            except Exception:
                continue

        if search_box is None:
            try:
                search_box = page.get_by_role("textbox").first
                if search_box.count() == 0:
                    search_box = None
            except Exception:
                pass

        if search_box is None:
            logger.error("未找到搜索框")
            return False

        try:
            # 点击搜索框
            box = search_box.bounding_box(timeout=2000)
            if box:
                self._human_click(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            else:
                search_box.click(delay=random.randint(20, 50))
            self._random_delay(200, 400)

            # 清空已有内容
            page.keyboard.press("Control+A")
            self._random_delay(50, 150)
            page.keyboard.press("Backspace")
            self._random_delay(100, 300)

            # 逐字输入（模拟真人打字）
            for char in keyword:
                page.keyboard.type(char, delay=random.randint(20, 60))
                # 偶尔小停顿
                if random.random() < 0.1:
                    self._random_delay(100, 200)

            self._random_delay(200, 500)

            # 回车搜索
            page.keyboard.press("Enter")
            wait_sec = self.settings.wait_after_search_seconds + random.uniform(0.3, 0.8)
            page.wait_for_timeout(int(wait_sec * 1000))
            logger.info("已搜索关键词: {}", keyword)
            return True
        except Exception as e:
            logger.error("搜索操作失败: {}", e)
            return False

    def click_user_tab(self, page: Page) -> bool:
        """点击搜索结果页的「用户」标签页。"""
        logger.info("正在点击用户 tab")
        self._random_delay(500, 1000)

        approaches = [
            lambda: page.get_by_text("用户", exact=True).first,
            lambda: page.locator("span:has-text('用户')").first,
            lambda: page.locator("div:has-text('用户')").first,
            lambda: page.locator("//*[text()='用户']").first,
        ]

        for approach in approaches:
            try:
                tab = approach()
                if tab.count() > 0 and tab.is_visible(timeout=2000):
                    box = tab.bounding_box(timeout=1000)
                    if box:
                        self._human_click(page, box["x"] + box["width"] / 2,
                                          box["y"] + box["height"] / 2)
                    else:
                        tab.click(delay=random.randint(30, 80))
                    self._random_delay(800, 1500)
                    logger.info("已点击用户 tab")
                    return True
            except Exception:
                continue

        logger.warning("未找到用户 tab")
        return False

    def scroll_results(self, page: Page, times: int = 1):
        """向下滚动搜索结果（自然人类滚动模式）。

        特点：不规律节奏，偶尔回滚一点模拟"回头看一眼"。
        """
        for _ in range(times):
            try:
                step = self._random_scroll_step()
                page.mouse.wheel(0, step)
                # 每次滚动后的等待时间不固定：大部分短，偶尔长
                wait = random.uniform(0.3, 0.8)
                if random.random() < 0.1:
                    wait += random.uniform(0.5, 1.5)
                    # 偶尔回少量滚一点（模拟"回头看看"）
                    try:
                        page.mouse.wheel(0, -random.randint(50, 200))
                    except Exception:
                        pass
                page.wait_for_timeout(int(wait * 1000))
            except Exception as e:
                logger.warning("滚动页面失败: {}", e)

    def scroll_full_page(self, page: Page, total_scrolls: int = 10):
        """完整页面滚动（分段进行，每段间有停顿）。"""
        for i in range(total_scrolls):
            if random.random() < 0.15:
                self._random_delay(300, 800)
            self.scroll_results(page, times=1)

    def click_more_button(self, page: Page) -> bool:
        """点击用户主页的「更多」展开按钮。"""
        more_selectors = [
            "span:has-text('更多')",
            "div:has-text('更多')",
            "//span[contains(text(), '更多')]",
            "//div[contains(text(), '更多') and @role='button']",
            "//div[contains(@class, 'more')]",
            "//span[contains(@class, 'more')]",
            "button:has-text('更多')",
            "//*[text()='更多' and not(ancestor::div[contains(@style,'display:none')])]",
        ]

        for selector in more_selectors:
            try:
                loc = page.locator(selector)
                if loc.count() > 0:
                    for i in range(loc.count()):
                        try:
                            el = loc.nth(i)
                            if el.is_visible(timeout=1000):
                                box = el.bounding_box(timeout=1000)
                                if box:
                                    self._human_click(page, box["x"] + box["width"] / 2,
                                                      box["y"] + box["height"] / 2)
                                else:
                                    el.click(delay=random.randint(20, 50))
                                self._random_delay(300, 600)
                                logger.info("已点击「更多」按钮")
                                return True
                        except Exception:
                            continue
            except Exception:
                continue

        logger.debug("未找到「更多」按钮（可能已完全展开）")
        return False

    def close_safe_popup(self, page: Page) -> bool:
        """关闭安全弹窗。"""
        safe_texts = ["我知道了", "稍后", "取消", "关闭", "不同意", "忽略", "不再提示"]
        for text in safe_texts:
            try:
                btn = page.get_by_text(text, exact=True).first
                if btn.count() > 0 and btn.is_visible(timeout=800):
                    box = btn.bounding_box(timeout=500)
                    if box:
                        self._human_click(page, box["x"] + box["width"] / 2,
                                          box["y"] + box["height"] / 2)
                    else:
                        btn.click(delay=random.randint(20, 50))
                    self._random_delay(200, 400)
                    logger.debug("已关闭弹窗: {}", text)
                    return True
            except Exception:
                continue

        try:
            slider = page.locator("//div[contains(@class,'captcha') or contains(@class,'verify')]")
            if slider.count() > 0 and slider.is_visible(timeout=500):
                logger.warning("检测到滑块验证码，需要人工处理")
                return False
        except Exception:
            pass

        return False

    def wait_for_stable(self, page: Page, timeout: int = 8000):
        """等待页面稳定。"""
        try:
            page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass
        self._random_delay(300, 800)

    # ------------------------------------------------------------------
    # 用户列表交互
    # ------------------------------------------------------------------

    def click_user_card(self, page: Page, card_index: int = 0) -> bool:
        """点击搜索结果中第 N 个用户卡片。"""
        try:
            user_links = page.locator("a[href*='/user/']")
            count = user_links.count()
            if count == 0:
                logger.warning("未找到用户链接")
                return False

            if card_index >= count:
                card_index = count - 1

            href = user_links.nth(card_index).get_attribute("href")
            if href:
                if not href.startswith("http"):
                    href = "https://www.douyin.com" + href
                logger.info("通过 goto 打开用户主页: {}", href)
                page.goto(href, wait_until="domcontentloaded", timeout=30000)
                self._random_delay(500, 1000)
                return True

            link = user_links.nth(card_index)
            if link.is_visible(timeout=2000):
                box = link.bounding_box(timeout=1000)
                if box:
                    self._human_click(page, box["x"] + box["width"] / 2,
                                      box["y"] + box["height"] / 2)
                else:
                    link.click(delay=random.randint(20, 50))
                self._random_delay(500, 1000)
                return True
            return False
        except Exception as e:
            logger.warning("点击用户卡片失败: {}", e)
            return False

    def navigate_to_profile(self, page: Page, profile_url: str) -> bool:
        """直接导航到用户主页（最可靠的方式）。"""
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            self._random_delay(500, 1000)
            return True
        except Exception as e:
            logger.error("导航到用户主页失败: {}", e)
            return False

    def get_all_visible_profile_urls(self, page: Page) -> list[str]:
        """获取搜索结果页中所有可见的用户主页链接。"""
        urls = []
        try:
            user_links = page.locator("a[href*='/user/']")
            count = user_links.count()
            for i in range(count):
                try:
                    href = user_links.nth(i).get_attribute("href")
                    if href:
                        if not href.startswith("http"):
                            href = "https://www.douyin.com" + href
                        if href not in urls:
                            urls.append(href)
                except Exception:
                    continue
        except Exception as e:
            logger.warning("获取用户链接失败: {}", e)
        return urls

    def open_user_profile_new_tab(self, page: Page, profile_url: str) -> Page | None:
        """在新标签页打开用户主页。"""
        try:
            new_page = page.context.new_page()
            new_page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            self._random_delay(500, 1000)
            return new_page
        except Exception as e:
            logger.error("打开用户主页失败: {}", e)
            return None

    def human_scroll_profile(self, page: Page, max_scrolls: int = 3):
        """在用户主页自然滚动，触发懒加载内容并尝试展开更多。"""
        self.click_more_button(page)
        self._random_delay(300, 800)

        for i in range(max_scrolls):
            scroll_down = self._random_scroll_step()
            try:
                page.mouse.wheel(0, scroll_down)
                self._random_delay(300, 800)
            except Exception:
                pass

            if i > 0 and i % 2 == 0:
                self.click_more_button(page)
                self._random_delay(200, 500)

        try:
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        self._random_delay(200, 500)

    # ------------------------------------------------------------------
    # 反检测：随机鼠标移动
    # ------------------------------------------------------------------

    def random_mouse_move(self, page: Page):
        """执行一次随机鼠标移动（迷惑反爬虫）。"""
        try:
            vp = page.viewport_size or {"width": 1440, "height": 900}
            x = random.randint(50, vp["width"] - 50)
            y = random.randint(50, vp["height"] - 50)
            self._move_mouse_to(page, x, y)
        except Exception:
            pass

    def do_random_activity(self, page: Page):
        """执行一次随机的人类行为（滚动、移动鼠标等）。"""
        action = random.choice(["scroll", "move", "wait"])
        if action == "scroll":
            try:
                page.mouse.wheel(0, random.randint(-200, 400))
            except Exception:
                pass
            self._random_delay(100, 300)
        elif action == "move":
            self.random_mouse_move(page)
            self._random_delay(100, 300)
        else:
            self._random_delay(200, 500)
