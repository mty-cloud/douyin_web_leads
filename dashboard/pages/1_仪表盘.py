"""首页仪表盘 — 系统运行状态概览 + 数据可视化 + 快速入口。"""

import streamlit as st
import pandas as pd
import plotly.express as px

from src.db import Database
from src.settings import Settings
from src.analysis.background_task_manager import (
    start_task, get_task, run_scoring_task,
)


def _get_db() -> Database:
    return Database()


st.title("📊 首页仪表盘")
st.caption("系统运行状态概览")

db = _get_db()

# 确保 settings 已初始化（直接访问时 app.py 的初始化不生效）
if "settings" not in st.session_state:
    st.session_state.settings = Settings()

stats = db.get_dashboard_stats()
recent_errors = db.get_recent_errors(limit=10)

# ── 指标卡片 ──
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("👤 候选用户总计", stats["total_candidates"])
    st.metric("📥 今日新增", stats["today_candidates"])
with col2:
    st.metric("🏠 已采集主页", stats["profile_captured"])
    st.metric("📝 活跃关键词", stats["active_template_count"])
with col3:
    st.metric("⏳ 待运行任务", stats["pending_tasks"])
    st.metric("✅ 已完成任务", stats["done_tasks"])
with col4:
    tier_counts = stats["tier_counts"]
    st.metric("🔴 A级线索", tier_counts.get("A", 0))
    st.metric("🟠 B级线索", tier_counts.get("B", 0))
    st.metric("🟡 C级线索", tier_counts.get("C", 0))
    st.metric("⚪ D级线索", tier_counts.get("D", 0))

# ── 分层饼图 + 状态分布 ──
st.markdown("---")
st.subheader("📈 数据可视化")

chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    # 分层饼图
    tier_data = {
        "分层": ["A - 强匹配", "B - 较明确", "C - 有相关", "D - 不匹配"],
        "数量": [tier_counts.get("A", 0), tier_counts.get("B", 0),
                 tier_counts.get("C", 0), tier_counts.get("D", 0)],
    }
    tier_df = pd.DataFrame(tier_data)
    tier_df = tier_df[tier_df["数量"] > 0]
    if not tier_df.empty:
        colors = {"A - 强匹配": "#FF4444", "B - 较明确": "#FF8C00",
                  "C - 有相关": "#FFD700", "D - 不匹配": "#AAAAAA"}
        fig_pie = px.pie(
            tier_df, values="数量", names="分层",
            title="线索分层分布",
            color="分层", color_discrete_map=colors,
            hole=0.4,
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("暂无分层数据")

with chart_col2:
    # 各状态数量柱状图
    status_counts = db.get_candidate_status_counts()
    if status_counts:
        status_names = {
            "new": "🆕 新采集", "search_captured": "🔍 搜索捕获",
            "card_low_score": "📉 卡片低分", "profile_pending": "⏳ 待采集主页",
            "profile_captured": "✅ 已采集主页", "analyzed": "📊 已分析",
            "profile_failed": "❌ 主页失败", "duplicate": "🔁 重复",
            "ignored": "⛔ 忽略", "profile_incomplete": "📄 信息不全",
        }
        status_df_data = []
        for st_key, st_val in status_counts.items():
            label = status_names.get(st_key, st_key)
            status_df_data.append({"状态": label, "数量": st_val})
        status_df = pd.DataFrame(status_df_data).sort_values("数量", ascending=True)
        fig_bar = px.bar(
            status_df, x="数量", y="状态", orientation="h",
            title="候选用户状态分布",
            color="数量", color_continuous_scale="Blues",
        )
        fig_bar.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("暂无状态数据")

# ── 快速入口 ──
st.markdown("---")
st.subheader("⚡ 快速操作")

quick_col1, quick_col2, quick_col3, quick_col4 = st.columns(4)

with quick_col1:
    if st.button("🌐 打开自动化 Chrome", use_container_width=True):
        from src.browser.browser_manager import open_browser_window
        settings = st.session_state.settings
        open_browser_window(settings)
        st.success("✅ 自动化 Chrome 已启动")

with quick_col2:
    if st.button("📊 快速评分分层", use_container_width=True):
        candidates = db.get_candidates_for_scoring()
        if not candidates:
            st.warning("没有待分析的线索")
        else:
            task_id = start_task(
                name=f"快速评分 ({len(candidates)}条)",
                target=run_scoring_task,
                candidates=candidates,
                settings=st.session_state.settings,
                with_ai=True,
                update_db=True,
            )
            st.session_state["scoring_task_id"] = task_id
            st.success(f"✅ 评分任务已启动（{len(candidates)} 条），ID: {task_id}")
            st.rerun()

with quick_col3:
    if st.button("📥 快速导出 Excel", use_container_width=True):
        from src.exporter import Exporter
        exporter = Exporter(db)
        path = exporter.export()
        if path:
            st.success(f"✅ 导出成功: {path}")
        else:
            st.warning("没有数据可导出")

with quick_col4:
    st.markdown(
        f"<div style='padding:0.5rem; border:1px solid #4CAF50; border-radius:8px; text-align:center;'>"
        f"<a href='/2_用户采集' target='_self' style='text-decoration:none; font-weight:bold; font-size:1rem;'>"
        f"🎯 前往用户采集 →</a></div>",
        unsafe_allow_html=True,
    )

# ── 采集历史摘要 ──
st.markdown("---")
st.subheader("📋 最近采集记录")
history = db.get_collection_history(limit=10)
if history:
    for h in history[:5]:
        status_icon = "✅" if h["status"] == "completed" else "❌"
        st.text(
            f"{status_icon} {h.get('province','')} {h.get('county','')} "
            f"→ {h.get('keyword','')} "
            f"| 发现: {h.get('candidates_found',0)} "
            f"| 匹配: {h.get('candidates_matched',0)} "
            f"| {h.get('created_at','')}"
        )
else:
    st.info("暂无采集记录 — 前往【用户采集】开始第一次采集")

# ── 最近错误 ──
st.markdown("---")
st.subheader("⚠️ 最近错误 / 待处理")

if recent_errors:
    for err in recent_errors:
        with st.expander(f"任务 #{err['id']}: {err.get('keyword', '?')} - {err['status']}"):
            st.write(f"错误信息: {err.get('error_message', '无')}")
            st.write(f"时间: {err.get('finished_at', '')}")
else:
    st.info("暂无错误记录")

# ── 已选择县城管理 ──
st.markdown("---")
st.subheader("🗑️ 已选择县城管理")
st.caption("删除已采集过的县城记录后，可在用户采集页重新选择该地区进行采集")

collected_counties = db.get_all_collected_counties_detail()

if not collected_counties:
    st.info("暂无已采集的县城记录")
else:
    tab1, tab2 = st.tabs(["📋 按县城删除", "⚡ 批量操作"])

    with tab1:
        st.caption(f"共 {len(collected_counties)} 个已采集县城 — 点击删除可移除记录")

        for county_info in collected_counties:
            prov = county_info["province"]
            city = county_info["city"]
            county = county_info["county"]
            task_count = county_info["task_count"]
            last_time = county_info.get("last_collected_at", "未知")

            cols = st.columns([3, 1, 1, 1])
            with cols[0]:
                st.write(f"**{prov} > {city} > {county}**")
                st.caption(f"任务数: {task_count} | 最近采集: {last_time}")
            with cols[1]:
                if st.button("🗑️ 删除", key=f"del_county_{prov}_{city}_{county}"):
                    deleted = db.delete_collection_history_by_county(prov, city, county)
                    if deleted:
                        st.success(f"✅ 已删除 {deleted} 条记录")
                        st.rerun()
                    else:
                        st.warning("没有记录被删除")

    with tab2:
        county_count = len(collected_counties)
        total_tasks = sum(c["task_count"] for c in collected_counties)
        st.warning(f"⚠️ 共 {county_count} 个县城、{total_tasks} 条采集记录")
        if st.button("🗑️ 一键删除所有已采集县城记录", type="primary", use_container_width=True):
            deleted = db.delete_all_collection_history()
            if deleted:
                st.success(f"✅ 已清空所有采集历史（{deleted} 条记录）")
                st.rerun()
