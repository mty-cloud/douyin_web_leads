"""测试分层模块（v3 — strong_positive_match 决定 D 级，has_positive_match 只用于 C 级保底）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analysis.lead_tier import decide_final_score, decide_tier


def test_85_plus_is_a():
    """85 分以上 + 强匹配 → A。"""
    assert decide_tier(85, strong_positive_match=True) == "A"
    assert decide_tier(90, strong_positive_match=True) == "A"
    assert decide_tier(100, strong_positive_match=True) == "A"


def test_70_to_84_is_b():
    """70–84 分 + 强匹配 → B。"""
    assert decide_tier(70, strong_positive_match=True) == "B"
    assert decide_tier(75, strong_positive_match=True) == "B"
    assert decide_tier(84, strong_positive_match=True) == "B"


def test_50_to_69_is_c():
    """50–69 分 + 强匹配 → C。"""
    assert decide_tier(50, strong_positive_match=True) == "C"
    assert decide_tier(60, strong_positive_match=True) == "C"
    assert decide_tier(69, strong_positive_match=True) == "C"


def test_no_strong_match_is_d():
    """无 strong_positive_match → D（无视分数和 has_positive_match）。"""
    assert decide_tier(0, has_positive_match=False, strong_positive_match=False) == "D"
    assert decide_tier(30, has_positive_match=True, strong_positive_match=False) == "D"
    assert decide_tier(49, has_positive_match=True, strong_positive_match=False) == "D"
    assert decide_tier(60, has_positive_match=True, strong_positive_match=False) == "D"
    assert decide_tier(90, has_positive_match=True, strong_positive_match=False) == "D"


def test_strong_match_low_score_is_c():
    """有 strong_positive_match + 低分 → C 保底。"""
    assert decide_tier(10, has_positive_match=True, strong_positive_match=True) == "C"
    assert decide_tier(40, strong_positive_match=True) == "C"


def test_strong_match_has_positive_c_floor():
    """strong_positive_match 时，低分 has_positive_match=False → C。"""
    assert decide_tier(10, has_positive_match=False, strong_positive_match=True) == "C"
    assert decide_tier(40, has_positive_match=False, strong_positive_match=True) == "C"


def test_final_score_with_ai():
    """AI 分数加权组合。"""
    assert decide_final_score(80, None) == 80
    assert decide_final_score(80, 100) == round(80 * 0.7 + 100 * 0.3)
    assert decide_final_score(60, 40) == round(60 * 0.7 + 40 * 0.3)
