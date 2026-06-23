"""Excel 导出模块。"""

from pathlib import Path
from datetime import datetime

import pandas as pd
from loguru import logger

from src.settings import PROJECT_ROOT


class Exporter:
    """线索导出器。"""

    def __init__(self, db):
        self.db = db

    def _query_candidates(self, province=None, city=None, county=None,
                          keyword=None, review_status=None, has_wechat=None):
        """查询候选用户。"""
        conn = self.db.get_conn()
        sql = "SELECT * FROM candidates WHERE 1=1"
        params = []
        if province:
            sql += " AND source_province = ?"
            params.append(province)
        if city:
            sql += " AND source_city = ?"
            params.append(city)
        if county:
            sql += " AND source_county = ?"
            params.append(county)
        if keyword:
            sql += " AND source_keywords LIKE ?"
            params.append(f"%{keyword}%")
        if review_status:
            sql += " AND manual_review_status = ?"
            params.append(review_status)
        if has_wechat == "yes":
            sql += " AND wechat_id IS NOT NULL AND wechat_id != ''"
        elif has_wechat == "no":
            sql += " AND (wechat_id IS NULL OR wechat_id = '')"
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def export(self, tier: str | list[str] | None = None,
               province: str | None = None,
               city: str | None = None,
               county: str | None = None,
               keyword: str | None = None,
               review_status: str | None = None,
               category_tag: str | None = None,
               has_wechat: str | None = None) -> str:
        """导出线索到 Excel。

        Args:
            tier: 'A'/'B'/'C'/'D'/['A','B',...]/None(全部)
            province: 筛选省份
            city: 筛选城市
            county: 筛选县
            keyword: 筛选关键词
            review_status: 人工审核状态
            category_tag: 品类标签
            has_wechat: "yes"/"no"/None(全部) 是否有微信号

        Returns:
            导出文件路径
        """
        candidates = self._query_candidates(province, city, county, keyword, review_status, has_wechat)
        if not candidates:
            logger.warning("没有符合条件的线索可导出")
            return ""

        # 获取分析结果
        cids = [c["id"] for c in candidates]
        analyses = self.db.get_lead_analyses(cids)
        analysis_map = {a["candidate_id"]: a for a in analyses}

        rows = []
        for c in candidates:
            a = analysis_map.get(c["id"])
            if a:
                final_score = a["final_score"]
                tier_val = a["tier"]
            else:
                final_score = 0
                tier_val = "未分析"

            # 分层筛选
            if tier:
                if isinstance(tier, list):
                    if tier_val not in tier:
                        continue
                elif tier == "AB":
                    if tier_val not in ("A", "B"):
                        continue
                elif tier_val != tier:
                    continue

            wechat = c.get("wechat_id", "") or ""
            rows.append({
                "ID": c["id"],
                "昵称": c.get("nickname", ""),
                "抖音号": c.get("douyin_id", ""),
                "微信号": wechat,
                "主页链接": c.get("profile_url", ""),
                "省份": c.get("source_province", ""),
                "城市": c.get("source_city", ""),
                "县": c.get("source_county", ""),
                "来源关键词": c.get("source_keywords", ""),
                "品类标签": c.get("source_category_tags", ""),
                "搜索卡片文本": c.get("search_card_text", ""),
                "主页简介": c.get("profile_bio", ""),
                "粉丝": c.get("followers_text", ""),
                "关注": c.get("following_text", ""),
                "获赞": c.get("likes_text", ""),
                "作品": c.get("works_text", ""),
                "IP属地": c.get("region_text", ""),
                "微信号提取来源": f"来自用户主页" if wechat else "",
                "分层": tier_val,
                "规则分": a["rule_score"] if a else "",
                "AI分": a["zhipu_score"] if a and a["zhipu_score"] else "" if a else "",
                "最终分": final_score,
                "审核状态": c.get("manual_review_status", ""),
                "备注": c.get("manual_note", ""),
                "发现时间": c.get("created_at", ""),
            })

        if not rows:
            logger.warning("筛选后没有可导出的线索")
            return ""

        df = pd.DataFrame(rows)

        # 保存
        export_dir = PROJECT_ROOT / "data" / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"leads_export_{timestamp}.xlsx"
        filepath = export_dir / filename

        with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="线索列表")

        logger.info("导出 Excel: {} ({} 行)", filepath, len(rows))
        return str(filepath)
