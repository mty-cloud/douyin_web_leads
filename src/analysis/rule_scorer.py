"""规则打分模块 v3 — 完全重写。

根据用户信息（昵称、简介、主页文本、搜索卡片文本、搜索关键词）综合打分。

核心变化：
1. search_card_text + source_keywords 参与评分（最重要的匹配信号）
2. strong_positive_match 包含所有关键词类型（行业/品类/实体/年龄层/搜索匹配)
3. 评分权重重新分配，产生真实区分度
"""

from pathlib import Path
from typing import Any

import yaml

from src.analysis.text_cleaner import clean_text


class RuleScorer:
    """基于规则的客户评分器。"""

    def __init__(self, rules_path: str | Path | None = None):
        if rules_path is None:
            from src.settings import PROJECT_ROOT
            rules_path = PROJECT_ROOT / "config" / "scoring_rules.yaml"
        self._rules = self._load_rules(rules_path)

    def _load_rules(self, path: str | Path) -> dict:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    @property
    def weights(self) -> dict:
        return self._rules.get("weights", {})

    @property
    def industry_keywords(self) -> list[str]:
        return self._rules.get("industry_keywords", [])

    @property
    def age_group_keywords(self) -> list[str]:
        return self._rules.get("age_group_keywords", [])

    @property
    def category_keywords(self) -> list[str]:
        return self._rules.get("category_keywords", [])

    @property
    def store_keywords(self) -> list[str]:
        return self._rules.get("store_keywords", [])

    @property
    def negative_keywords(self) -> list[str]:
        return self._rules.get("negative_keywords", [])

    @property
    def unrelated_industry_keywords(self) -> list[str]:
        """不相关行业关键词：用户自有内容含这些词且不含女装/服装 → 强制 D。"""
        return self._rules.get("unrelated_industry_keywords", [])

    def score_lead(self, data: dict) -> dict:
        """对候选用户评分。

        评分维度（总分 100）：
          - 行业匹配 25 分：直接命中"女装/服装"等大词
          - 品类匹配 20 分：命中"打底衫/针织衫"等品类词
          - 实体匹配 20 分：命中"实体/门店/档口"等经营词
          - 年龄层匹配 15 分：命中"妈妈装/中老年"等目标人群词
          - 可信度 10 分：有简介、粉丝数据、完整主页
          - 搜索匹配 10 分：搜索卡片文本与搜索关键词吻合
        """
        nickname = data.get("nickname", "") or ""
        search_card = data.get("search_card_text", "") or ""
        profile_bio = data.get("profile_bio", "") or ""
        profile_text = data.get("profile_text", "") or ""
        profile_ocr = data.get("profile_ocr_text", "") or ""
        source_keywords = data.get("source_keywords", "") or ""

        evidence = []
        negative_evidence = []

        # ── 用户自有文本（用于匹配关键词）──
        user_text = " ".join([nickname, profile_bio, profile_text, profile_ocr])
        user_text_clean = clean_text(user_text)

        # ── 搜索卡片文本（最重要的上下文）──
        search_clean = clean_text(search_card)

        # ── 所有关键词匹配（同时搜用户文本和搜索卡片）──
        def match_keywords(kw_list: list[str], text: str) -> list[str]:
            return [kw for kw in kw_list if kw in text]

        industry_matches = match_keywords(self.industry_keywords, user_text_clean)
        age_matches = match_keywords(self.age_group_keywords, user_text_clean)
        cat_matches = match_keywords(self.category_keywords, user_text_clean)
        store_matches = match_keywords(self.store_keywords, user_text_clean)

        # 搜索卡片文本也参与匹配（它直接包含"女装零售"这类关键词）
        card_industry = match_keywords(self.industry_keywords, search_clean)
        card_cat = match_keywords(self.category_keywords, search_clean)
        card_store = match_keywords(self.store_keywords, search_clean)
        card_age = match_keywords(self.age_group_keywords, search_clean)

        # 搜索关键词中是否含行业相关词（如搜索"女装实体店"）
        src_has_industry = any(kw in source_keywords for kw in self.industry_keywords)
        src_has_store = any(kw in source_keywords for kw in self.store_keywords)
        src_has_category = any(kw in source_keywords for kw in self.category_keywords)

        nickname_clean = clean_text(nickname)

        # ---- 1. 行业匹配 (max 25) ----
        industry_score = 0
        all_industry_hits = set(industry_matches + card_industry)
        if all_industry_hits:
            match_pct = min(len(all_industry_hits) / 3, 1.0)
            industry_score = int(25 * match_pct)
            evidence.append(f"行业匹配: {sorted(all_industry_hits)}")
        elif src_has_industry:
            industry_score = 10  # 搜索词含行业词，给半奖
            evidence.append("搜索关键词含行业词")

        # ---- 2. 品类匹配 (max 20) ----
        category_score = 0
        all_cat_hits = set(cat_matches + card_cat)
        if all_cat_hits:
            match_pct = min(len(all_cat_hits) / 3, 1.0)
            category_score = int(20 * match_pct)
            evidence.append(f"品类匹配: {sorted(all_cat_hits)}")
        elif src_has_category:
            category_score = 8
            evidence.append("搜索关键词含品类词")

        # ---- 3. 实体经营痕迹 (max 20) ----
        store_score = 0
        all_store_hits = set(store_matches + card_store)
        if all_store_hits:
            match_pct = min(len(all_store_hits) / 3, 1.0)
            store_score = int(20 * match_pct)
            evidence.append(f"实体经营: {sorted(all_store_hits)}")
        elif src_has_store:
            store_score = 8
            evidence.append("搜索关键词含实体经营词")

        # ---- 4. 年龄层匹配 (max 15) ----
        age_group_score = 0
        all_age_hits = set(age_matches + card_age)
        if all_age_hits:
            match_pct = min(len(all_age_hits) / 3, 1.0)
            age_group_score = int(15 * match_pct)
            evidence.append(f"年龄层匹配: {sorted(all_age_hits)}")

        # ---- 5. 可信度 (max 10) ----
        credibility_score = 0
        if profile_bio:
            credibility_score += 4
            evidence.append("有主页简介")
        if data.get("followers_text") or data.get("likes_text"):
            credibility_score += 3
            evidence.append("有粉丝/获赞数据")
        if profile_text and len(profile_text) > 200:
            credibility_score += 3
            evidence.append("主页内容丰富")

        # ---- 6. 搜索匹配度 (max 10) ----
        search_match_score = 0
        # 搜索卡片文本长度 — 有内容说明搜索结果找到了用户
        if len(search_clean) > 100:
            search_match_score += 5
        # 搜索卡片含"粉丝"或"抖音号" — 是完整用户卡片
        if "粉丝" in search_clean:
            search_match_score += 3
        if "抖音号" in search_clean:
            search_match_score += 2

        # ---- 判断匹配级别 ----
        # HAS positive match：任意关键词命中
        all_keyword_hits = (
            len(all_industry_hits) + len(all_cat_hits) + len(all_store_hits) + len(all_age_hits)
        )
        has_positive_match = all_keyword_hits >= 1

        # STRONG positive match：必须命中行业关键词（女装/服装/服饰）
        # 之前 any_keyword≥1 即可，导致"中老年旅游"靠年龄词+地址词混过。
        # 服装是第一门槛：只有账号自身内容或搜索卡片中出现"女装/服装/服饰"
        # 才算 strong 业务信号。仅品类词/年龄词/实体地址词不算。
        industry_in_user_or_card = len(all_industry_hits) >= 1
        strong_positive_match = industry_in_user_or_card

        # ---- 硬性排除：用户自身内容含男装/童装 → 强制降 D ----
        # 注意：只检查用户自己的昵称/简介/主页文本，不检查搜索卡片和搜索词
        # 因为搜索词本身就是"女装实体店"，搜出来的人可能是卖男装的误匹配
        # 另外：profile_text 包含了全部视频描述文本，女装实体店主偶尔发童装/男装相关
        # 内容很常见，只要用户自身也含"女装/服装"关键词，就不应硬性阻断（允许合理跨界）
        HARD_BLOCK_KEYWORDS = ["男装", "童装", "男士"]
        block_matches = [kw for kw in HARD_BLOCK_KEYWORDS
                         if kw in user_text_clean or kw in nickname_clean]
        if block_matches:
            # 检查用户自身内容是否含女装/服装相关词（允许合理跨界）
            user_has_apparel = any(
                kw in user_text_clean or kw in nickname_clean
                for kw in self.industry_keywords
            )
            if not user_has_apparel:
                negative_evidence.append(f"[硬性排除] 用户内容含: {block_matches}")
                # 强制覆盖 strong_positive_match，让 lead_tier 走 D 级
                strong_positive_match = False
                has_positive_match = False

        # ---- 不相关行业阻断：用户自身内容含化妆/新娘/摄影等 → 强制降 D ----
        # 仅检查用户自有内容（昵称+简介+主页），不检查搜索卡片和搜索词
        # 搜索词可能是"XX县女装实体店"，但用户自己是化妆师/摄影师，完全不相关
        # 注意：如果用户自己内容同时含"女装"和"化妆"（如"女装店 可化妆造型"），
        # 属于正常跨界经营描述，不阻断
        unrelated_matches = [kw for kw in self.unrelated_industry_keywords
                             if kw in user_text_clean or kw in nickname_clean]
        if unrelated_matches:
            # 检查用户自身内容是否含女装/服装相关词（允许合理跨界）
            user_has_apparel = any(
                kw in user_text_clean or kw in nickname_clean
                for kw in self.industry_keywords
            )
            if not user_has_apparel:
                negative_evidence.append(f"[不相关行业] 用户内容含: {unrelated_matches}")
                # 强制覆盖 strong_positive_match，让 lead_tier 走 D 级
                strong_positive_match = False
                has_positive_match = False

        # ---- 负面词扣除（非硬性阻断，仅扣分） ----
        hard_negatives = ["情感", "搞笑", "娱乐", "宠物",
                          "餐饮", "相亲", "招聘", "房产", "汽车"]
        hard_matches = [kw for kw in hard_negatives if kw in user_text_clean]
        deduction = len(hard_matches) * 10
        if hard_matches:
            negative_evidence.append(f"不相关类目: {hard_matches}")

        cross_matches = [kw for kw in ["美甲", "美妆"] if kw in user_text_clean]
        if cross_matches:
            negative_evidence.append(f"跨界类目提示: {cross_matches}")

        # 昵称匹配
        all_target_keywords = (self.industry_keywords + self.age_group_keywords
                               + self.category_keywords + self.store_keywords)
        nickname_has_match = any(kw in nickname_clean for kw in all_target_keywords)

        profile_is_empty = not any([profile_bio, profile_text, profile_ocr])

        # ---- 计算总分 ----
        rule_score = (industry_score + category_score + store_score
                      + age_group_score + credibility_score + search_match_score - deduction)
        rule_score = max(0, min(100, rule_score))

        return {
            "region_score": 0,  # region_score 已合并到其他维度，保留 0 兼容旧引用
            "industry_score": industry_score,
            "category_score": category_score,
            "store_score": store_score,
            "age_group_score": age_group_score,
            "credibility_score": credibility_score,
            "search_match_score": search_match_score,
            "rule_score": rule_score,
            "evidence": evidence,
            "negative_evidence": negative_evidence,
            "has_positive_match": has_positive_match,
            "strong_positive_match": strong_positive_match,
            "profile_is_empty": profile_is_empty,
            "nickname_has_match": nickname_has_match,
            # 返回原始匹配数据便于调试
            "industry_matches": sorted(all_industry_hits) if all_industry_hits else [],
            "cat_matches": sorted(all_cat_hits) if all_cat_hits else [],
            "store_matches": sorted(all_store_hits) if all_store_hits else [],
            "age_matches": sorted(all_age_hits) if all_age_hits else [],
        }
