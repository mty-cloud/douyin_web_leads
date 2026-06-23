"""测试规则打分模块。"""

import sys
from pathlib import Path

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analysis.rule_scorer import RuleScorer


def test_high_score_for_target_customer():
    """'黄梅XX女装，主营妈妈装打底衫实体店' 应得高分。"""
    scorer = RuleScorer()
    data = {
        "nickname": "黄梅XX女装",
        "search_card_text": "黄梅女装店，主营妈妈装打底衫实体店",
        "profile_bio": "主营妈妈装、打底衫，实体店批发",
        "profile_text": "黄梅县女装实体店，专注妈妈装中老年女装",
        "source_keywords": "黄梅县女装店",
        "source_province": "湖北省",
        "source_city": "黄冈市",
        "source_county": "黄梅县",
    }
    result = scorer.score_lead(data)
    assert result["rule_score"] >= 60, f"预期高分，实际: {result['rule_score']}"
    assert result["region_score"] > 0
    assert result["industry_score"] > 0
    assert result["store_score"] > 0


def test_low_score_for_unrelated_business():
    """'XX童装，主营儿童服饰' 应得低分。"""
    scorer = RuleScorer()
    data = {
        "nickname": "XX童装",
        "search_card_text": "XX童装，主营儿童服饰",
        "profile_bio": "专注儿童服装",
        "profile_text": "童装批发，儿童服饰零售",
        "source_keywords": "童装",
        "source_province": "湖北省",
        "source_city": "武汉市",
        "source_county": "武昌区",
    }
    result = scorer.score_lead(data)
    assert result["rule_score"] < 50, f"预期低分，实际: {result['rule_score']}"
    assert len(result["negative_evidence"]) > 0


def test_medium_score_for_fashion_sharing():
    """'小美穿搭分享' 应为 C 或低分。"""
    scorer = RuleScorer()
    data = {
        "nickname": "小美穿搭分享",
        "search_card_text": "日常穿搭分享，时尚搭配",
        "profile_bio": "分享穿搭日常",
        "profile_text": "喜欢穿搭的朋友关注我",
        "source_keywords": "穿搭",
        "source_province": "湖北省",
        "source_city": "武汉市",
        "source_county": "武昌区",
    }
    result = scorer.score_lead(data)
    assert result["rule_score"] < 70, f"预期中低分，实际: {result['rule_score']}"
