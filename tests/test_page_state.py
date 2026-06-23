"""测试页面状态检测模块。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.browser.page_state import PageStateDetector


def test_detect_profile():
    """包含"关注、粉丝、获赞、作品、抖音号"的文本识别为 PROFILE。"""
    detector = PageStateDetector()
    text = "XX女装店  关注 1.2万  粉丝 5.6万  获赞 10万  作品 200  抖音号 xxxx"
    state = detector.detect_from_text("https://www.douyin.com/user/xxxx", text)
    assert state == PageStateDetector.PROFILE, f"预期 PROFILE，实际: {state}"


def test_detect_verify_risk():
    """包含"验证码、安全验证、访问过于频繁"的文本识别为 VERIFY_OR_RISK。"""
    detector = PageStateDetector()
    text = "安全验证 请完成验证码验证 访问过于频繁 请稍后再试"
    state = detector.detect_from_text("https://www.douyin.com", text)
    assert state == PageStateDetector.VERIFY_OR_RISK, f"预期 VERIFY_OR_RISK，实际: {state}"

    text2 = "滑块验证，请拖动滑块完成验证"
    state2 = detector.detect_from_text("https://www.douyin.com", text2)
    assert state2 == PageStateDetector.VERIFY_OR_RISK, f"预期 VERIFY_OR_RISK，实际: {state2}"


def test_detect_no_result():
    """包含"暂无相关结果"的文本识别为 NO_RESULT。"""
    detector = PageStateDetector()
    text = "暂无相关结果 没有找到相关内容"
    state = detector.detect_from_text("https://www.douyin.com/search", text)
    assert state == PageStateDetector.NO_RESULT, f"预期 NO_RESULT，实际: {state}"


def test_detect_login():
    """登录页面识别。"""
    detector = PageStateDetector()
    text = "扫码登录 手机号登录 密码登录"
    state = detector.detect_from_text("https://www.douyin.com", text)
    assert state == PageStateDetector.LOGIN_REQUIRED, f"预期 LOGIN_REQUIRED，实际: {state}"


def test_detect_home():
    """首页识别。"""
    detector = PageStateDetector()
    state = detector.detect_from_text("https://www.douyin.com", "推荐 关注 直播")
    assert state == PageStateDetector.HOME, f"预期 HOME，实际: {state}"


def test_detect_search_user():
    """搜索结果用户 tab 识别。"""
    detector = PageStateDetector()
    text = "综合 视频 用户 直播 商品 关注 粉丝"
    state = detector.detect_from_text("https://www.douyin.com/search/keyword", text)
    assert state == PageStateDetector.SEARCH_RESULT_USER, f"预期 SEARCH_RESULT_USER，实际: {state}"
