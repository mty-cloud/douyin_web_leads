"""采集预算设置页面。

展示和修改当前配置：
- 每轮关键词上限
- 每关键词搜索滚动上限
- 每轮主页采集上限
- card_score 阈值
- 是否遇到风控立即停止
"""

import streamlit as st
import yaml
from pathlib import Path

from src.settings import Settings, PROJECT_ROOT


def _get_settings() -> Settings:
    return Settings()


def _get_config_path() -> Path:
    return PROJECT_ROOT / "config" / "app_config.yaml"


st.set_page_config(
    page_title="采集预算设置",
    page_icon="⚙️",
    layout="wide",
)

st.title("⚙️ 采集预算设置")
st.caption("查看和修改采集预算与评分阈值配置")

settings = _get_settings()
config_path = _get_config_path()

# 读取当前 YAML 配置
try:
    with open(config_path, encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
except Exception as e:
    st.error(f"读取配置文件失败: {e}")
    config_data = {}

# ├── 采集预算 ──
st.markdown("---")
st.subheader("📦 采集预算 (collection_budget)")

budget = config_data.get("collection_budget", {})
# 显示默认值
default_budget = {
    "max_keywords_per_run": 10,
    "max_search_scrolls_per_keyword": 3,
    "max_new_candidates_per_keyword": 30,
    "max_profiles_per_run": 50,
    "max_profile_failures_per_run": 5,
    "stop_on_first_risk_event": True,
}

col1, col2 = st.columns(2)

with col1:
    new_max_keywords = st.number_input(
        "每轮关键词上限 (max_keywords_per_run)",
        min_value=1, max_value=50,
        value=budget.get("max_keywords_per_run", default_budget["max_keywords_per_run"]),
        help="每轮采集最多处理多少个关键词",
    )
    new_max_scrolls = st.number_input(
        "每关键词搜索滚动上限 (max_search_scrolls_per_keyword)",
        min_value=1, max_value=20,
        value=budget.get("max_search_scrolls_per_keyword", default_budget["max_search_scrolls_per_keyword"]),
        help="每个关键词搜索结果页最多滚动多少屏",
    )
    new_max_candidates = st.number_input(
        "每关键词新增候选上限 (max_new_candidates_per_keyword)",
        min_value=5, max_value=200,
        value=budget.get("max_new_candidates_per_keyword", default_budget["max_new_candidates_per_keyword"]),
        help="每个关键词最多入库多少个候选用户",
    )

with col2:
    new_max_profiles = st.number_input(
        "每轮主页采集上限 (max_profiles_per_run)",
        min_value=5, max_value=200,
        value=budget.get("max_profiles_per_run", default_budget["max_profiles_per_run"]),
        help="每轮最多采集多少个用户主页",
    )
    new_max_failures = st.number_input(
        "每轮主页失败上限 (max_profile_failures_per_run)",
        min_value=1, max_value=50,
        value=budget.get("max_profile_failures_per_run", default_budget["max_profile_failures_per_run"]),
        help="主页连续失败多少次后停止本轮采集",
    )
    new_stop_on_risk = st.checkbox(
        "遇到风控立即停止本轮采集 (stop_on_first_risk_event)",
        value=budget.get("stop_on_first_risk_event", default_budget["stop_on_first_risk_event"]),
        help="开启：任一任务触发风控后，不再执行后续任务",
    )

# ├── Profile 评分 ──
st.markdown("---")
st.subheader("🎯 主页采集评分阈值 (profile)")

profile_config = config_data.get("profile", {})
new_card_threshold = st.number_input(
    "搜索卡片评分阈值 (card_score_threshold)",
    min_value=0, max_value=100,
    value=profile_config.get("card_score_threshold", 30),
    help="搜索卡片评分 >= 此值时，候选用户才会进入主页采集阶段（0-100 分）",
)

st.caption("评分规则说明：")
st.caption("""
- 基础分 30 分
- 命中正向关键词（女装/服装/女装店等）+20 分
- 命中负向关键词（童装/男装/美妆等）-30 分
- 只有噪音文本（开启读屏标签等）→ 0 分
- 粉丝数过高（≥1万）→ 降低优先级 -20 分
- 评分 >= 阈值才进入主页采集
""")

# 保存按钮
st.markdown("---")
st.subheader("💾 保存配置")

if st.button("保存修改到 app_config.yaml", type="primary", use_container_width=True):
    try:
        # 更新 collection_budget
        if "collection_budget" not in config_data:
            config_data["collection_budget"] = {}
        config_data["collection_budget"]["max_keywords_per_run"] = int(new_max_keywords)
        config_data["collection_budget"]["max_search_scrolls_per_keyword"] = int(new_max_scrolls)
        config_data["collection_budget"]["max_new_candidates_per_keyword"] = int(new_max_candidates)
        config_data["collection_budget"]["max_profiles_per_run"] = int(new_max_profiles)
        config_data["collection_budget"]["max_profile_failures_per_run"] = int(new_max_failures)
        config_data["collection_budget"]["stop_on_first_risk_event"] = bool(new_stop_on_risk)

        # 更新 profile.card_score_threshold
        if "profile" not in config_data:
            config_data["profile"] = {}
        config_data["profile"]["card_score_threshold"] = int(new_card_threshold)

        # 写回文件
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        st.success("✅ 配置已保存！重启采集后生效。")
        st.info("💡 修改的配置将在下一次启动采集任务时生效，当前运行中的采集不受影响。")
    except Exception as e:
        st.error(f"保存配置失败: {e}")

# 显示当前生效配置
st.markdown("---")
st.subheader("📋 当前生效配置")

col1, col2 = st.columns(2)

with col1:
    st.write("**collection_budget:**")
    st.json({
        "max_keywords_per_run": settings.max_keywords_per_run,
        "max_search_scrolls_per_keyword": settings.max_search_scrolls_per_keyword,
        "max_new_candidates_per_keyword": settings.max_new_candidates_per_keyword,
        "max_profiles_per_run": settings.max_profiles_per_run,
        "max_profile_failures_per_run": settings.max_profile_failures_per_run,
        "stop_on_first_risk_event": settings.stop_on_first_risk_event,
    })

with col2:
    st.write("**profile:**")
    st.json({
        "card_score_threshold": settings.card_score_threshold,
    })
