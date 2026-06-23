"""任务生成器 — 根据地区和关键词模板生成搜索任务。"""

from loguru import logger


class TaskGenerator:
    """搜索任务生成器。"""

    def __init__(self, db):
        self.db = db

    def generate(self, region_ids: list[int] | None = None,
                 template_ids: list[int] | None = None) -> int:
        """生成搜索任务。

        Args:
            region_ids: 地区 ID 列表，None 表示所有活跃地区
            template_ids: 关键词模板 ID 列表，None 表示所有活跃模板

        Returns:
            新增任务数量
        """
        if region_ids is None:
            regions = self.db.get_active_regions()
            region_ids = [r["id"] for r in regions]
        if template_ids is None:
            templates = self.db.get_active_templates()
            template_ids = [t["id"] for t in templates]

        if not region_ids:
            logger.warning("没有可用的地区，无法生成任务")
            return 0
        if not template_ids:
            logger.warning("没有可用的关键词模板，无法生成任务")
            return 0

        count = self.db.generate_tasks(region_ids, template_ids)
        logger.info("生成搜索任务: {} 个", count)
        return count

    def preview(self, region_ids: list[int] | None = None,
                template_ids: list[int] | None = None,
                max_preview: int = 20) -> list[dict]:
        """预览将要生成的任务。"""
        if region_ids is None:
            regions = self.db.get_active_regions()
            region_ids = [r["id"] for r in regions]
        if template_ids is None:
            templates = self.db.get_active_templates()
            template_ids = [t["id"] for t in templates]

        results = []
        for rid in region_ids:
            region = self.db.get_region_by_id(rid)
            if not region:
                continue
            for tid in template_ids:
                template = self.db.get_keyword_template_by_id(tid)
                if not template:
                    continue
                if template["keyword_type"] == "template":
                    keyword = template["template_text"].format(
                        province=region["province"],
                        city=region["city"],
                        county=region["county"],
                    )
                else:
                    keyword = template["template_text"]

                results.append({
                    "province": region["province"],
                    "city": region["city"],
                    "county": region["county"],
                    "keyword": keyword,
                    "category_tag": template.get("category_tag", ""),
                    "priority": template.get("priority", "medium"),
                })

                if len(results) >= max_preview:
                    return results

        return results
