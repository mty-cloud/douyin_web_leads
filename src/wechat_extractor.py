"""微信号提取模块。

从用户主页文本（简介、profile_text）中智能提取微信号。
支持多种隐含格式：
- 微信: xxx / 微信号: xxx / 微信：xxx
- 加v xxx / 加V xxx / +V xxx
- 微: xxx / 微：xxx
- V: xxx / v: xxx / VX: xxx / vx: xxx
- wx: xxx / WX: xxx
- 绿色软件 xxx
- WeChat: xxx / wechat: xxx
- 手机号（11位数字，可能作为微信号使用）
- 📱 + 数字
"""

import re
from typing import Optional


# 微信号字符范围：字母、数字、下划线、横线，通常 6-20 位
_WECHAT_ID_PATTERN = r'[a-zA-Z0-9_\-]{4,30}'

# 宽松版：允许包含中文（但中文通常不会是微信号本身，而是微信号前面的描述）
_WECHAT_ID_LAX = r'[a-zA-Z0-9_\-.—]{4,30}'


def extract_all(text: str) -> list[dict]:
    """从文本中提取所有疑似微信号。

    Args:
        text: 主页文本或简介

    Returns:
        [{"wechat_id": str, "method": str, "confidence": str}, ...]
        按可信度从高到低排序
    """
    if not text:
        return []

    results = []

    # ---- 策略1: 精确前缀匹配（高可信度） ----
    patterns_high = [
        # 微信: xxx / 微信号: xxx / 微信号：xxx
        (r'(?:微信号?|微信)\s*[:：]\s*(' + _WECHAT_ID_PATTERN + r')', '微信号'),
        # 加v xxx / 加V xxx / +V xxx
        (r'(?:加[Vv]|[+＋][Vv])\s*[:：]?\s*(' + _WECHAT_ID_PATTERN + r')', '加V'),
        # VX: xxx / vx: xxx
        (r'(?:[Vv][Xx])\s*[:：]\s*(' + _WECHAT_ID_PATTERN + r')', 'VX'),
        # wx: xxx / WX: xxx
        (r'(?:[Ww][Xx])\s*[:：]\s*(' + _WECHAT_ID_PATTERN + r')', 'WX'),
        # 绿色软件 xxx
        (r'绿色软件\s*[:：]?\s*(' + _WECHAT_ID_PATTERN + r')', '绿色软件'),
        # WeChat: xxx / wechat: xxx
        (r'(?:[Ww]e[Cc]hat)\s*[:：]\s*(' + _WECHAT_ID_PATTERN + r')', 'WeChat'),
    ]

    for pattern, method in patterns_high:
        for m in re.finditer(pattern, text):
            wid = m.group(1).strip()
            if _is_valid_wechat_id(wid):
                results.append({
                    "wechat_id": wid,
                    "method": method,
                    "confidence": "high",
                })

    # ---- 策略2: V:/v: 前缀（中等可信度，需排除 V: 在版本号等场景） ----
    patterns_medium = [
        # V: xxx / v: xxx（单独一行或结尾）
        (r'(?:^|[。，；;\n\r])\s*[Vv]\s*[:：]\s*(' + _WECHAT_ID_PATTERN + r')', 'V:'),
        # 微: xxx / 微：xxx
        (r'微\s*[:：]\s*(' + _WECHAT_ID_PATTERN + r')', '微:'),
        # 薇: xxx / 薇：xxx（同音）
        (r'薇\s*[:：]\s*(' + _WECHAT_ID_PATTERN + r')', '薇:'),
    ]

    for pattern, method in patterns_medium:
        for m in re.finditer(pattern, text):
            wid = m.group(1).strip()
            if _is_valid_wechat_id(wid):
                # 去重
                if not any(r["wechat_id"] == wid for r in results):
                    results.append({
                        "wechat_id": wid,
                        "method": method,
                        "confidence": "medium",
                    })

    # ---- 策略3: 手机号（可作为微信号使用，低可信度） ----
    phone_pattern = r'(?:电话|手机|手机号|tel|phone|☎|📞|联系)\s*[:：]?\s*(1[3-9]\d{9})'
    for m in re.finditer(phone_pattern, text):
        phone = m.group(1)
        if not any(r["wechat_id"] == phone for r in results):
            results.append({
                "wechat_id": phone,
                "method": "手机号",
                "confidence": "medium",
            })

    # 独立的手机号（无前缀，低可信度）
    standalone_phones = re.findall(r'(?<!\d)(1[3-9]\d{9})(?!\d)', text)
    for phone in standalone_phones:
        if not any(r["wechat_id"] == phone for r in results):
            results.append({
                "wechat_id": phone,
                "method": "独立手机号",
                "confidence": "low",
            })

    return results


def extract_best(text: str) -> Optional[str]:
    """提取最靠谱的一个微信号。

    优先返回高可信度的，其次中，最后低。
    """
    results = extract_all(text)
    if not results:
        return None

    # 按可信度排序取第一个
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda r: confidence_order.get(r["confidence"], 99))

    return results[0]["wechat_id"]


def _is_valid_wechat_id(wid: str) -> bool:
    """验证微信号格式是否合理。"""
    if not wid or len(wid) < 4 or len(wid) > 40:
        return False

    # 排除纯数字但长度不对的（微信号通常6-20位，纯数字可能是QQ号）
    if wid.isdigit() and len(wid) < 6:
        return False

    # 排除 HTML 标签残留
    if '<' in wid or '>' in wid:
        return False

    # 排除全是特殊字符的
    if not re.search(r'[a-zA-Z0-9一-鿿]', wid):
        return False

    return True
