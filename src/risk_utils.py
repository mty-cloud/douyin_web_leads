"""风控检测、昵称验证、卡片评分工具函数。

根据《风控解决方案》文档实现：
- risk_events 检测和记录
- 噪音昵称过滤
- 搜索卡片评分
- 主页文本有效性校验
"""

import re
from pathlib import Path
from datetime import datetime

from loguru import logger


# ======================================================================
# 风险检测
# ======================================================================

RISK_WORDS = [
    "验证码",
    "安全验证",
    "访问过于频繁",
    "请稍后再试",
    "滑块",
    "验证",
    "登录异常",
]

RISK_URL_PATTERNS = [
    "passport.douyin.com",
    "captcha",
    "verify",
    "slide",
    "security",
]

# 风控暂停标记文件 — batch_worker 用此判断是否保持浏览器存活
RISK_SENTINEL_FILE = "data/risk_sentinel.flag"

# 抖音动态年份版权匹配（避免年份硬编码，到期年份自动适配）
DOUYIN_COPYRIGHT_PATTERN = re.compile(r"20\d{2}\s*©\s*抖音")

def has_douyin_copyright(text: str) -> bool:
    """检测文本中是否包含抖音年份版权声明（动态匹配任意年份）。"""
    return bool(DOUYIN_COPYRIGHT_PATTERN.search(text))


def detect_risk_in_text(text: str) -> tuple[bool, str]:
    """检测文本中是否包含风控关键词。

    Returns:
        (is_risk, matched_word): 是否检测到风险，以及命中的第一个关键词
    """
    if not text:
        return False, ""
    for word in RISK_WORDS:
        if word in text:
            return True, word
    return False, ""


def detect_risk_in_url(url: str) -> tuple[bool, str]:
    """检测 URL 是否指向风控/验证页面。

    Returns:
        (is_risk, matched_pattern): 是否风控，匹配的 URL 模式
    """
    if not url:
        return False, ""
    url_lower = url.lower()
    for pattern in RISK_URL_PATTERNS:
        if pattern in url_lower:
            return True, pattern
    return False, ""


def detect_risk_on_page(page) -> tuple[bool, str]:
    """检测页面是否处于风控状态（文本 + URL 双重检测）。

    Args:
        page: Playwright page 对象

    Returns:
        (is_risk, risk_word): 是否风控，命中关键词/URL模式
    """
    # 1. URL 检测（有些验证页面是独立域名，body 文本可能为空）
    try:
        url = page.url
        is_risk_url, matched_pattern = detect_risk_in_url(url)
        if is_risk_url:
            return True, f"URL风控:{matched_pattern}"
    except Exception:
        pass

    # 2. 文本检测
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        try:
            text = page.evaluate("() => document.body.innerText || ''")
        except Exception:
            return False, ""

    return detect_risk_in_text(text)


def detect_risk_before_action(page, action_name: str = "") -> tuple[bool, str]:
    """在关键操作前检测风控，带出错保护。

    使用短超时，如果页面不可达也视为风控。

    Returns:
        (is_risk, risk_detail): 是否风控及详情
    """
    try:
        # 极短超时检测页面是否正常
        text = page.locator("body").inner_text(timeout=1000)
    except Exception:
        # 页面崩溃或不可达 → 可能是验证页面强制跳转导致
        # 尝试 URL 检测
        try:
            url = page.url
            is_risk_url, p = detect_risk_in_url(url)
            if is_risk_url:
                return True, f"URL风控:{p}"
        except Exception:
            pass
        # 再尝试 evaluate
        try:
            text = page.evaluate("() => document.body.innerText || ''")
        except Exception:
            return True, "page_unreachable"

    # 文本检测
    hit, word = detect_risk_in_text(text)
    if hit:
        return True, word

    # URL 检测
    try:
        url = page.url
        hit_url, pattern = detect_risk_in_url(url)
        if hit_url:
            return True, f"URL风控:{pattern}"
    except Exception:
        pass

    return False, ""


def handle_risk_event(page, db, keyword: str = "",
                      action_when_triggered: str = "",
                      screenshot_dir: str = "data/screenshots/risk",
                      html_dir: str = "data/html_snapshots/risk") -> int:
    """检测到风控时执行：保存截图、HTML、写入 risk_events。

    Args:
        page: Playwright page 对象
        db: Database 实例
        keyword: 当前关键词
        action_when_triggered: 触发时的操作描述
        screenshot_dir: 截图保存目录
        html_dir: HTML 快照保存目录

    Returns:
        event_id: risk_events 记录 ID，失败返回 0
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    event_type = "risk_detected"
    page_url = ""
    screenshot_path = ""
    html_path = ""
    page_text = ""

    try:
        page_url = page.url
    except Exception:
        pass

    # 保存截图
    try:
        screenshot_dir_path = Path(screenshot_dir)
        screenshot_dir_path.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(screenshot_dir_path / f"risk_{timestamp}.png")
        page.screenshot(path=screenshot_path, full_page=True)
        logger.info("风控截图已保存: {}", screenshot_path)
    except Exception as e:
        logger.warning("保存风控截图失败: {}", e)

    # 保存 HTML
    try:
        html_dir_path = Path(html_dir)
        html_dir_path.mkdir(parents=True, exist_ok=True)
        html_path = str(html_dir_path / f"risk_{timestamp}.html")
        html_content = page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info("风控 HTML 已保存: {}", html_path)
    except Exception as e:
        logger.warning("保存风控 HTML 失败: {}", e)

    # 获取页面文本
    try:
        page_text = page.locator("body").inner_text(timeout=2000)
    except Exception:
        pass

    # 写入 risk_events
    try:
        event_id = db.add_risk_event(
            event_type=event_type,
            page_url=page_url[:500],
            keyword=keyword[:200],
            screenshot_path=screenshot_path,
            html_snapshot_path=html_path,
            page_text=(page_text or "")[:2000],
            action_when_triggered=action_when_triggered[:500],
        )
        logger.warning("风控事件已记录: event_id={}, keyword={}", event_id, keyword)

        # 写入暂停标记（batch_worker 检查此标记决定是否保持浏览器打开）
        if event_id:
            write_risk_sentinel(event_id=event_id, keyword=keyword)

        return event_id
    except Exception as e:
        logger.error("写入 risk_events 失败: {}", e)
        return 0


def write_risk_sentinel(event_id: int, keyword: str = "",
                         task_id: int = 0) -> None:
    """写入风控暂停标记文件。

    batch_worker 的 finally 块检查此文件来决定是否保持浏览器存活。
    用户处理完风控后，通过恢复操作清除此标记。
    """
    import json
    try:
        sentinel_path = Path(RISK_SENTINEL_FILE)
        sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        with open(sentinel_path, "w", encoding="utf-8") as f:
            json.dump({
                "event_id": event_id,
                "keyword": keyword,
                "task_id": task_id,
                "timestamp": datetime.now().isoformat(),
            }, f, ensure_ascii=False)
        logger.warning("已写入风控暂停标记: {}", sentinel_path)
    except Exception as e:
        logger.warning("写入风控标记失败: {}", e)


def clear_risk_sentinel() -> bool:
    """清除风控暂停标记。用户恢复采集时调用。"""
    try:
        sentinel_path = Path(RISK_SENTINEL_FILE)
        if sentinel_path.exists():
            sentinel_path.unlink()
            logger.info("已清除风控暂停标记")
            return True
        return False
    except Exception as e:
        logger.warning("清除风控标记失败: {}", e)
        return False


def risk_sentinel_exists() -> bool:
    """检查风控暂停标记是否存在（表示浏览器正因风控保持打开）。"""
    return Path(RISK_SENTINEL_FILE).exists()


# ======================================================================
# 昵称噪音过滤
# ======================================================================

NOISE_NICKNAMES = {
    "开启读屏标签",
    "读屏标签已关闭",
    "下载抖音精选",
    "抖音",
}


def is_valid_nickname(nick: str) -> bool:
    """判断是否为有效昵称（非噪音）。

    规则：
    - 不能为空
    - 不能是已知的噪音文本
    - 不能包含噪音关键词
    - 长度 2~40 字符
    - 不能包含 © 符号
    """
    if not nick:
        return False

    nick = nick.strip()

    if nick in NOISE_NICKNAMES:
        return False

    if any(w in nick for w in NOISE_NICKNAMES):
        return False

    if len(nick) <= 1:
        return False

    if len(nick) > 40:
        return False

    if "©" in nick:
        return False

    return True


def extract_nickname_from_card_text(text: str) -> str:
    """从搜索卡片文本中提取可能的昵称。

    策略：
    1. 逐行遍历，跳过噪音行（读屏标签、关注、粉丝等）
    2. 长度在 2~40 之间的行作为候选
    3. 优先返回包含行业关键词的行（女装、服饰等）
    4. 返回第一个候选
    """
    if not text:
        return ""

    lines = [x.strip() for x in text.splitlines() if x.strip()]

    noise = [
        "开启读屏标签",
        "读屏标签已关闭",
        "关注",
        "粉丝",
        "获赞",
        "下载抖音精选",
    ]

    def _is_noise_line(line: str) -> bool:
        if any(n in line for n in noise):
            return True
        if has_douyin_copyright(line):
            return True
        return False

    candidates = []
    for line in lines:
        if _is_noise_line(line):
            continue
        if len(line) <= 1 or len(line) > 40:
            continue
        candidates.append(line)

    # 优先返回含行业关键词的行
    for line in candidates:
        if any(k in line for k in ["女装", "服饰", "服装", "店", "妈妈装"]):
            return line

    return candidates[0] if candidates else ""


# ======================================================================
# 搜索卡片评分
# ======================================================================

# 🔴 必须命中（服装行业门槛）：卡片自身内容含以下任一才通过初筛
MUST_HAVE_KEYWORDS = [
    "女装", "服装", "女装店", "服饰",
]

# 🟡 锦上添花（仅在有 must_have 后才加分）
NICE_TO_HAVE_KEYWORDS = [
    # 年龄/客群
    "妈妈装", "中老年", "中老年女装", "大码", "中年",
    # 品类
    "打底衫", "内搭", "针织衫", "毛衣", "羊毛衫", "保暖",
    # 实体经营
    "实体店", "店", "门店", "档口", "上新", "批发零售", "本店",
    # 经营行为
    "穿搭", "批发", "零售", "老板娘", "加工", "定制",
]

# ⛔ 硬性排除（不相关行业）
SCORE_NEGATIVE_KEYWORDS = [
    "童装", "男装", "美妆", "娱乐", "餐饮",
    "宠物", "游戏", "搞笑", "旅游", "旅行",
    "装修", "家政", "房产", "中介", "保险",
]


def score_search_card(nickname_candidate: str,
                      search_card_text: str,
                      source_keyword: str,
                      followers_text: str) -> dict:
    """对搜索卡片评分，判断是否值得进入主页采集。

    v3 评分规则（严格初筛）：
    - 基础分 0（不再送基础分，必须卡片自身内容含服装行业词才有分）
    - 命中 MUST_HAVE（女装/服装/服饰）+40 分 —— **通过初筛的关键**
    - 锦上添花 +15/项，最多 +30
    - 搜索关键词本身含服装词 +10 小加分（非通过关键）
    - 命中负向关键词 -40 分（强制淘汰）
    - 只有噪音文本 0 分
    - 粉丝过高降低优先级

    注：source_keyword 不参与卡片文本关键词匹配（避免搜索词本身含"女装"
    导致完全不相关的卡片也获得高分），只作为单独的加分项。
    """
    evidence = []
    negative_evidence = []

    # 检查是否只有噪音
    card_text = search_card_text or ""
    noise_markers = ["开启读屏标签", "下载抖音精选", "京ICP备",
                      "读屏标签已关闭", "认证徽章"]
    cleaned = card_text
    for marker in noise_markers:
        cleaned = cleaned.replace(marker, "")
    cleaned = DOUYIN_COPYRIGHT_PATTERN.sub("", cleaned)
    cleaned = "".join(c for c in cleaned if c.isalpha() or c.isdigit())

    if len(cleaned) < 2:
        logger.warning("卡片文本仅为噪音，评分为 0")
        return {
            "card_score": 0,
            "card_evidence": "",
            "card_negative_evidence": "卡片文本仅为噪音字符",
        }

    # ⭐ v3: 基础分 0
    # 只使用卡片自身文本（昵称 + 卡片描述）+ 搜索词
    # 注意：source_keyword 不参与 must_have 匹配，避免"搜索词=女装店"导致
    # 旅游/装修等完全不相关的卡片被误判为"服装相关"
    card_own_text = (nickname_candidate + " " + card_text).lower()

    # ---- 0. 硬性排除：卡片文本含不相关行业词 ----
    for kw in SCORE_NEGATIVE_KEYWORDS:
        if kw in card_own_text:
            negative_evidence.append(kw)
            logger.debug("卡片负向命中: {}", kw)
            return {
                "card_score": 0,
                "card_evidence": "",
                "card_negative_evidence": f"不相关类目: {kw}",
            }

    # ---- 1. 🔴 必须命中服装行业词（卡片自身文本） ----
    must_hit = [kw for kw in MUST_HAVE_KEYWORDS if kw in card_own_text]
    if not must_hit:
        # 没命中服装行业词 → 不入库
        logger.debug("卡片未命中服装行业词，评分 0")
        return {
            "card_score": 0,
            "card_evidence": "",
            "card_negative_evidence": "卡片内容未体现服装相关",
        }

    score = 40  # 通过服装门槛
    evidence.append(f"服装匹配:{must_hit}")

    # ---- 2. 锦上添花（年龄/品类/实体/经营等） ----
    nice_hits = [kw for kw in NICE_TO_HAVE_KEYWORDS if kw in card_own_text]
    if nice_hits:
        # 最多 +30，每条 +15
        nice_bonus = min(len(nice_hits), 2) * 15
        score += nice_bonus
        evidence.append(f"相关匹配:{nice_hits[:3]}")

    # ---- 3. 搜索关键词含服装词 → 小加分 ----
    keyword_lower = source_keyword.lower()
    keyword_has_clothing = any(kw in keyword_lower for kw in MUST_HAVE_KEYWORDS)
    if keyword_has_clothing:
        score += 10
        evidence.append("搜索关键词含服装词")

    # ---- 4. 粉丝过高降低优先级 ----
    if followers_text:
        import re
        m = re.search(r'([0-9.]+)\s*(万|w|W)\s*粉丝', followers_text)
        if m:
            try:
                val = float(m.group(1))
                if val >= 1.0:
                    negative_evidence.append(f"粉丝数过高:{val}万")
                    score = max(0, score - 20)
                elif val >= 0.5:
                    score = max(0, score - 10)
            except ValueError:
                pass

    score = max(0, min(100, score))

    return {
        "card_score": score,
        "card_evidence": "; ".join(evidence[:5]) if evidence else "",
        "card_negative_evidence": "; ".join(negative_evidence[:3]) if negative_evidence else "",
    }


# ======================================================================
# 主页有效性校验
# ======================================================================

PROFILE_MARKERS = ["抖音号", "关注", "粉丝", "获赞", "作品", "IP属地"]

NOISE_PROFILE_WORDS = [
    "开启读屏标签",
    "读屏标签已关闭",
    "下载抖音精选",
]


def is_valid_profile_text(text: str) -> tuple[bool, str]:
    """校验主页文本是否有效（而非加载失败/噪音页面）。

    Returns:
        (is_valid, reason): True 表示有效，False 时 reason 为失败原因
    """
    if not text:
        return False, "empty_profile_text"

    clean = text.strip()

    # 噪音检测：文本中大部分是噪音词/版权文字且长度不足
    if len(clean) < 80 and (any(w in clean for w in NOISE_PROFILE_WORDS) or has_douyin_copyright(clean)):
        return False, "noise_only_profile"

    # 关键标识检测：必须有抖音号/关注/粉丝等至少 2 个
    hit = sum(1 for w in PROFILE_MARKERS if w in clean)

    if hit < 2:
        return False, "missing_profile_markers"

    return True, "valid"
