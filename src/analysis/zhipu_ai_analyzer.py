"""智谱 AI 分析模块。

使用 OpenAI SDK 调用智谱 AI 接口，对候选用户做文本分层复核。
"""

import json
import os

from loguru import logger
from openai import OpenAI


class ZhipuAIAnalyzer:
    """智谱 AI 客户端封装。"""

    def __init__(self, settings):
        self.settings = settings
        self.enabled = settings.zhipu_enabled
        self.client = None

        if self.enabled:
            api_key = os.getenv(settings.zhipu_api_key_env)
            if api_key:
                self.client = OpenAI(
                    api_key=api_key,
                    base_url=settings.zhipu_base_url,
                )
                logger.info("智谱 AI 客户端已初始化")
            else:
                logger.warning("未找到 {} 环境变量，智谱 AI 不可用", settings.zhipu_api_key_env)

    def is_available(self) -> bool:
        """检查 AI 是否可用。"""
        return self.enabled and self.client is not None

    def analyze_lead(self, lead: dict) -> dict | None:
        """分析单个线索，返回 AI 判断结果。"""
        if not self.is_available():
            return None

        prompt = self._build_prompt(lead)

        try:
            response = self.client.chat.completions.create(
                model=self.settings.zhipu_model,
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个严谨的女装批发客户线索筛选助手。你必须只输出 JSON，不要输出其他文字。"
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=self.settings.zhipu_temperature,
                max_tokens=self.settings.zhipu_max_tokens,
                # 智谱兼容 OpenAI 格式
                extra_body={"response_format": {"type": "json_object"}} if self.settings.get("zhipu_ai.require_json", True) else None,
            )

            content = response.choices[0].message.content
            logger.debug("智谱 AI 返回: {}...", content[:200] if content else "")
            return self._parse_json(content)

        except Exception as e:
            logger.error("智谱 AI 调用失败: {}", e)
            return {
                "error": str(e),
                "is_target": None,
                "score": None,
                "tier": None,
            }

    def _build_prompt(self, lead: dict) -> str:
        return f"""你是女装批发业务的线索筛选助手。

业务背景：
我是一家二级女装批发店，主营 35-60 岁女性服装，重点品类是打底衫、内搭、妈妈装、针织衫、秋冬基础款。
目标客户是县城或下沉市场的女装实体店、妈妈装店、中老年女装店、服装零售店老板。

请根据以下信息判断这个抖音用户是否是潜在供货客户：

昵称：{lead.get("nickname", "")}
抖音号：{lead.get("douyin_id", "")}
主页简介：{lead.get("profile_bio", "")}
主页文本：{lead.get("profile_text", "")}
OCR 文本：{lead.get("profile_ocr_text", "")}
来源地区：{lead.get("source_province", "")}-{lead.get("source_city", "")}-{lead.get("source_county", "")}
粉丝：{lead.get("followers_text", "")}
关注：{lead.get("following_text", "")}
获赞：{lead.get("likes_text", "")}
作品数：{lead.get("works_text", "")}

⚠️ 注意：排除搜索关键词的干扰。该用户是通过关键词搜索发现的，但请只根据用户自己的主页内容来判断，不要因为搜索关键词中包含行业词就判定该用户相关。

请判断：
1. 是否女装相关；
2. 是否可能是实体店/零售店/店主；
3. 是否符合 35-60 岁女装客群；
4. 是否与打底衫、内搭、妈妈装、针织衫、秋冬基础款相关；
5. 是否值得后续人工触达。

分层标准：
A：强匹配，优先人工查看；
B：相关但需要复核；
C：弱相关，暂存；
D：不匹配，排除。

请只输出 JSON：
{{
  "is_target": true,
  "score": 0,
  "tier": "A/B/C/D",
  "business_type": "",
  "matched_categories": [],
  "evidence": [],
  "negative_evidence": [],
  "recommended_action": ""
}}"""

    def _parse_json(self, content: str) -> dict:
        content = content.strip()
        if content.startswith("```"):
            content = content.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("JSON 解析失败: {}", e)
            return {
                "error": f"JSON parse error: {e}",
                "is_target": None,
                "score": None,
                "tier": None,
            }

    def analyze_batch(self, leads: list[dict]) -> list[dict]:
        """批量分析线索（逐个调用）。"""
        results = []
        for lead in leads:
            result = self.analyze_lead(lead)
            results.append(result)
        return results
