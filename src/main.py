"""CLI 入口。

支持命令：
    python src/main.py init-db
    python src/main.py import-regions
    python src/main.py import-keywords
    python src/main.py collect --province 湖北省 --county 黄梅县 --keyword 女装店 --scroll 10
    python src/main.py open-browser
    python src/main.py score-leads
    python src/main.py export --tier A
"""

import argparse
import sys
from pathlib import Path

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.settings import Settings, load_env
from src.logger import setup_logger
from src.db import Database


def init_db(settings):
    db = Database()
    db.init_db()
    print("✅ 数据库初始化完成")


def import_regions(settings):
    """内置区域数据已通过 china_regions.py 自动提供，此命令可跳过。"""
    print("✅ 区域数据已内置，无需导入。请使用 china_regions.py 中的数据。")


def import_keywords(settings):
    db = Database()
    yaml_path = PROJECT_ROOT / "config" / "keyword_templates.yaml"
    if not yaml_path.exists():
        print(f"❌ 关键词模板文件不存在: {yaml_path}")
        return
    import yaml
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    templates = data.get("templates", [])
    count = 0
    for t in templates:
        db.add_keyword_template(
            template_text=t["template_text"],
            keyword_type=t.get("keyword_type", "template"),
            category_tag=t.get("category_tag", ""),
            priority=t.get("priority", "medium"),
            is_active=1 if t.get("is_active", True) else 0,
            note=t.get("note", ""),
        )
        count += 1
    print(f"✅ 导入 {count} 个关键词模板")


def open_browser(settings):
    from src.browser.browser_manager import BrowserManager, ensure_chrome_running
    ensure_chrome_running(settings)
    bm = BrowserManager(settings)
    page = bm.start()
    page.goto(settings.douyin_home_url)
    print(f"✅ 浏览器已打开: {settings.douyin_home_url}")
    print("请在浏览器中手动登录抖音网页版。")
    print("按 Ctrl+C 关闭浏览器。")
    try:
        import time
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n关闭浏览器...")
    finally:
        bm.close()


def run_collect(settings, province: str, county: str,
                keyword: str, scroll: int = 10, max_users: int = 20):
    """运行采集流程。

    两阶段：
    1. 搜索采集（SearchFlow）：搜索→提取卡片→入库 search_captured→卡片评分
    2. 主页采集（ProfileFlow）：提取 profile_pending 主页信息
    """
    from src.browser.browser_manager import BrowserManager
    from src.browser.page_state import PageStateDetector
    from src.browser.actions import DouyinActions
    from src.browser.dom_extractor import DOMExtractor
    from src.browser.search_flow import SearchFlow
    from src.browser.profile_flow import ProfileFlow

    db = Database()
    bm = BrowserManager(settings)
    state_detector = PageStateDetector()
    actions = DouyinActions(settings, state_detector)
    extractor = DOMExtractor()

    try:
        bm.start()
        # 从 china_regions 查找 city
        from src.china_regions import CHINA_REGIONS
        city = ""
        if province in CHINA_REGIONS:
            for c, counties in CHINA_REGIONS[province]:
                if county in counties:
                    city = c
                    break

        # 构造任务
        task = {
            "id": 0,
            "province": province,
            "city": city or province,
            "county": county,
            "keyword": keyword,
            "max_scroll": scroll,
            "max_users": max_users,
            "priority": "medium",
            "category_tag": "",
        }

        # 阶段 1：搜索采集
        search_flow = SearchFlow(db, bm, actions, extractor, state_detector, None, settings)
        search_result = search_flow.run_keyword_task(task)
        print(f"✅ 阶段1 搜索完成")
        print(f"  发现用户: {search_result.get('candidates', 0)}")
        print(f"  评分达标: {search_result.get('scored_pending', 0)}")

        # 阶段 2：主页采集
        if search_result.get("status") not in ("failed", "paused_need_human"):
            profile_flow = ProfileFlow(db, bm, actions, extractor, state_detector, None, settings)
            profile_results = profile_flow.collect_pending_profiles(limit=max_users)
            profiles_browsed = len([r for r in profile_results if r.get("status") == "profile_captured"])
            profiles_failed = len([r for r in profile_results if r.get("status") in ("failed", "profile_incomplete")])
            print(f"✅ 阶段2 主页采集完成")
            print(f"  采集主页: {profiles_browsed}")
            print(f"  采集失败: {profiles_failed}")
        else:
            print(f"⚠️ 搜索阶段未完成，跳过主页采集")

        print(f"✅ 采集流程结束")

    finally:
        bm.close()


def score_leads(settings):
    from src.analysis.rule_scorer import RuleScorer
    from src.analysis.zhipu_ai_analyzer import ZhipuAIAnalyzer
    from src.analysis.lead_tier import validate_and_assign_tier

    db = Database()
    scorer = RuleScorer()
    zhipu = ZhipuAIAnalyzer(settings)

    candidates = db.get_candidates_for_scoring()
    print(f"待分析线索: {len(candidates)} 个")

    scored = 0
    for c in candidates:
        rule_result = scorer.score_lead(c)
        ai_result = None
        if (zhipu.is_available()
                and rule_result["rule_score"] >= settings.zhipu_call_when_rule_score_gte):
            ai_result = zhipu.analyze_lead(c)
        result = validate_and_assign_tier(c, rule_result, ai_result)
        result["candidate_id"] = c["id"]
        db.add_lead_analysis(result)
        db.update_candidate(c["id"], status="analyzed")
        scored += 1

    print(f"✅ 完成 {scored} 个线索分层")


def export_leads(settings, tier: str = "all", province: str | None = None):
    from src.exporter import Exporter
    db = Database()
    exporter = Exporter(db)

    if tier == "all":
        path = exporter.export(province=province)
    elif tier == "AB":
        path = exporter.export(tier="AB", province=province)
    else:
        path = exporter.export(tier=tier, province=province)

    if path:
        print(f"✅ 导出成功: {path}")
    else:
        print("❌ 导出失败（没有数据）")


def main():
    load_env()

    parser = argparse.ArgumentParser(
        description="douyin_web_leads - 抖音客户线索采集系统 v2.0"
    )
    parser.add_argument("command", nargs="?", help="子命令")
    parser.add_argument("--province", type=str, default=None, help="省份筛选（collect/export）")
    parser.add_argument("--county", type=str, help="县城")
    parser.add_argument("--keyword", type=str, help="搜索关键词")
    parser.add_argument("--scroll", type=int, default=10, help="滚动次数")
    parser.add_argument("--max-users", type=int, default=20, help="最多采集用户数")
    parser.add_argument("--limit", type=int, default=5, help="数量限制")
    parser.add_argument("--tier", type=str, default="all", help="分层筛选")

    args = parser.parse_args()

    # 初始化
    settings = Settings()
    setup_logger()

    if args.command == "init-db":
        init_db(settings)
    elif args.command == "import-regions":
        import_regions(settings)
    elif args.command == "import-keywords":
        import_keywords(settings)
    elif args.command == "open-browser":
        open_browser(settings)
    elif args.command == "collect":
        run_collect(settings, args.province or "", args.county or "",
                    args.keyword or "", args.scroll, args.max_users)
    elif args.command == "score-leads":
        score_leads(settings)
    elif args.command == "export":
        export_leads(settings, args.tier, args.province)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
