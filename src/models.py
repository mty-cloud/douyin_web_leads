"""数据模型定义。"""

from pydantic import BaseModel, Field


class CandidateData(BaseModel):
    """候选用户数据。"""
    nickname: str = ""
    douyin_id: str = ""
    profile_url: str = ""
    search_card_text: str = ""
    search_page_url: str = ""
    source_province: str = ""
    source_city: str = ""
    source_county: str = ""
    source_keywords: str = ""
    source_category_tags: str = ""


class ProfileData(BaseModel):
    """主页采集数据。"""
    profile_text: str = ""
    profile_bio: str = ""
    followers_text: str = ""
    following_text: str = ""
    likes_text: str = ""
    works_text: str = ""
    region_text: str = ""
    profile_ocr_text: str = ""


class AnalysisResult(BaseModel):
    """分析结果。"""
    region_score: int = 0
    industry_score: int = 0
    age_group_score: int = 0
    category_score: int = 0
    store_score: int = 0
    credibility_score: int = 0
    rule_score: int = 0
    zhipu_score: int | None = None
    final_score: int = 0
    tier: str = "D"
    is_target: bool | None = None
    business_type: str = ""
    matched_categories: list[str] = []
    evidence: list[str] = []
    negative_evidence: list[str] = []
    recommended_action: str = ""
    zhipu_json: str = ""
