"""线索分层模块。

综合规则评分和 AI 评分，输出最终分层。

分层原则（v3 优化）：
  - 只根据用户自有内容（昵称+主页简介+主页文本+OCR）判断
  - 行业词 + 实体经营 + 昵称匹配 → 业务信号 → 正常分层（A/B/C）
  - 仅有品类/年龄匹配 → 泛兴趣信号 → 不阻止 D 级
  - 无任何业务信号 → 直接 D 级（除非 AI 认为是 A/B）
  - 昵称不对 + 主页为空 → D 级
"""

import json

from loguru import logger


def decide_final_score(rule_score: int, zhipu_score: int | None) -> int:
    """计算最终分数。

    如果有 AI 评分，加权组合；否则只使用规则评分。
    """
    if zhipu_score is None:
        return rule_score
    return round(rule_score * 0.7 + zhipu_score * 0.3)


def decide_tier(final_score: int, has_positive_match: bool = False,
                strong_positive_match: bool = False) -> str:
    """根据最终分数和正向匹配证据决定分层。

    v3 核心逻辑：
      1. 无 strong_positive_match（无行业/实体/昵称匹配）→ D
      2. 有 strong_positive_match + 高分 → A/B/C
      3. has_positive_match 用于低分 C 级保底（不作 D 判定依据）
    """
    # 没有业务信号 → D
    if not strong_positive_match:
        return "D"

    # 有业务信号 → 分数分层（v3 评分体系阈值）
    if final_score >= 60:
        return "A"
    if final_score >= 40:
        return "B"
    if final_score >= 15:
        return "C"
    # 低分但有搜索关键词关联 → C 保底
    return "C"


def get_tier_description(tier: str) -> str:
    desc = {
        "A": "强匹配，优先人工查看",
        "B": "较明确意向，次优先",
        "C": "有相关线索，暂存",
        "D": "不匹配，排除",
    }
    return desc.get(tier, "未知")


def score_to_score(ai_result: dict | None) -> int | None:
    """从 AI 结果中提取分数。"""
    if ai_result is None:
        return None
    score = ai_result.get("score")
    if score is None:
        return None
    try:
        return max(0, min(100, int(score)))
    except (ValueError, TypeError):
        return None


def validate_and_assign_tier(candidate: dict,
                             rule_result: dict,
                             ai_result: dict | None) -> dict:
    """综合打分和 AI 分析，确定最终分层。

    v3 优化：
      - strong_positive_match（行业/实体/昵称）是 D 级判定的唯一依据
      - AI 高评级（A/B）可覆盖 D 级判定
      - 页面对话框中的 "has_positive_match" 仅用于 C 级保底

    Args:
        candidate: 候选用户原始数据
        rule_result: 规则评分结果
        ai_result: AI 分析结果（可能为 None）

    Returns:
        包含最终评分和分层的字典
    """
    rule_score = rule_result.get("rule_score", 0)
    evidence = list(rule_result.get("evidence", []))
    negative_evidence = list(rule_result.get("negative_evidence", []))
    has_positive_match = rule_result.get("has_positive_match", False)
    strong_positive_match = rule_result.get("strong_positive_match", False)
    profile_is_empty = rule_result.get("profile_is_empty", True)

    # 提取 AI 分数
    zhipu_score = score_to_score(ai_result)
    zhipu_tier = ai_result.get("tier") if ai_result else None

    # 合并 AI 证据
    if ai_result:
        ai_evidence = ai_result.get("evidence", [])
        if isinstance(ai_evidence, list):
            evidence.extend([f"[AI] {e}" for e in ai_evidence[:5]])
        ai_neg = ai_result.get("negative_evidence", [])
        if isinstance(ai_neg, list):
            negative_evidence.extend([f"[AI] {e}" for e in ai_neg[:3]])

    # 计算最终分数
    final_score = decide_final_score(rule_score, zhipu_score)

    # ---- D 级判定（业务信号优先） ----
    # strong_positive_match=False → 无行业/实体/昵称匹配 → D
    # 除非 AI 明确给 A/B
    if not strong_positive_match:
        if zhipu_tier in ("A", "B"):
            tier = zhipu_tier
            logger.info("AI 判定 {}，覆盖规则 D 级 (candidate_id={})",
                         zhipu_tier, candidate.get("id", ""))
        else:
            tier = "D"
            if profile_is_empty:
                reason = "用户主页信息为空且昵称不匹配"
            else:
                reason = "用户自有内容无行业/实体/昵称匹配"
            negative_evidence.append(f"[规则] {reason}")
            logger.info("D 级: {} (candidate_id={})", reason, candidate.get("id", ""))
    else:
        # 有业务信号 → 正常分层
        tier = decide_tier(final_score, has_positive_match, strong_positive_match=True)

        # AI 可微调：AI 明确给 A 但规则给得低，保守提升一级
        if zhipu_tier == "A" and tier in ("C", "D"):
            tier = "B"
            logger.info("AI 建议 A，规则给 {}，调整为 B", tier)

    return {
        "region_score": rule_result.get("region_score", 0),
        "industry_score": rule_result.get("industry_score", 0),
        "age_group_score": rule_result.get("age_group_score", 0),
        "category_score": rule_result.get("category_score", 0),
        "store_score": rule_result.get("store_score", 0),
        "credibility_score": rule_result.get("credibility_score", 0),
        "rule_score": rule_score,
        "zhipu_score": zhipu_score,
        "final_score": final_score,
        "tier": tier,
        "is_target": 1 if tier in ("A", "B") else 0,
        "evidence": json.dumps(evidence, ensure_ascii=False),
        "negative_evidence": json.dumps(negative_evidence, ensure_ascii=False),
        "zhipu_json": json.dumps(ai_result, ensure_ascii=False) if ai_result else "",
        "recommended_action": get_tier_description(tier),
    }
