"""页面状态识别。"""

from loguru import logger


class PageStateDetector:
    """检测抖音网页版当前页面状态。"""

    HOME = "HOME"
    SEARCH_INPUT = "SEARCH_INPUT"
    SEARCH_RESULT_ALL = "SEARCH_RESULT_ALL"
    SEARCH_RESULT_USER = "SEARCH_RESULT_USER"
    PROFILE = "PROFILE"
    NO_RESULT = "NO_RESULT"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    VERIFY_OR_RISK = "VERIFY_OR_RISK"
    POPUP = "POPUP"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"

    def detect_from_text(self, url: str, text: str) -> str:
        """根据 URL 和页面文本判断状态。"""
        text = text or ""
        url = url or ""

        # 风控 / 验证码优先识别（扩展关键词）
        risk_words = [
            "验证码", "验证", "安全验证", "访问过于频繁", "请稍后再试",
            "滑块", "操作太频繁", "被限制", "账号异常",
            "请求过于频繁", "请稍后重试", "系统检测到异常",
            "行为异常", "滑动", "拼图", "拖动滑块",
        ]
        if any(w in text for w in risk_words):
            logger.warning("检测到风控/验证页面")
            return self.VERIFY_OR_RISK

        # 登录页
        login_words = ["登录", "扫码登录", "手机号登录", "密码登录"]
        if any(w in text for w in login_words) and "粉丝" not in text:
            logger.info("检测到登录页面")
            return self.LOGIN_REQUIRED

        # 无结果
        no_result_words = ["暂无相关结果", "没有找到相关内容", "无搜索结果", "没有更多了"]
        if any(w in text for w in no_result_words):
            return self.NO_RESULT

        # 用户主页：关注、粉丝、获赞、作品、抖音号同时出现
        profile_words = ["关注", "粉丝", "获赞", "作品", "抖音号"]
        profile_hit = sum(1 for w in profile_words if w in text)
        if profile_hit >= 3:
            return self.PROFILE

        # 搜索结果页：综合、视频、用户、直播等tab
        search_words = ["综合", "视频", "用户", "直播", "商品"]
        search_hit = sum(1 for w in search_words if w in text)
        if search_hit >= 2 and "用户" in text:
            if "粉丝" in text or "关注" in text:
                return self.SEARCH_RESULT_USER
            return self.SEARCH_RESULT_ALL

        # 首页
        if "douyin.com" in url and url.strip().rstrip("/") in (
            "https://www.douyin.com", "https://www.douyin.com/"
        ):
            return self.HOME

        # about:blank → 明确返回 UNKNOWN（不是首页，需要导航）
        if url in ("about:blank", ""):
            return self.UNKNOWN

        # 搜索输入框页
        if "douyin.com/search" in url or "search" in url:
            return self.SEARCH_RESULT_ALL

        return self.UNKNOWN

    def detect(self, page) -> str:
        """检测页面状态。"""
        try:
            url = page.url
            # 尝试获取 body 文本
            try:
                text = page.locator("body").inner_text(timeout=3000)
            except Exception:
                text = ""
            return self.detect_from_text(url, text)
        except Exception as e:
            logger.warning("页面状态检测异常: {}", e)
            return self.UNKNOWN
