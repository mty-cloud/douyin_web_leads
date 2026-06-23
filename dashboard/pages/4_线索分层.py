"""线索分层 — 对候选用户进行规则评分 + AI 复核，输出 A/B/C/D 分层。

去掉数据库详情列表（已移至「线索分层数据库」），只保留：
- 评分运行配置和启动
- 分层统计概览
- 快速跳转到线索分层数据库
"""

import streamlit as st
from src.db import Database
from src.settings import Settings
from src.analysis.background_task_manager import (
    start_task, stop_task, get_task, get_task_result,
    run_scoring_task,
)
from src.analysis.lead_tier import get_tier_description


def _get_db() -> Database:
    return Database()


st.title("📊 线索分层")
st.caption("对候选用户进行规则评分 + AI 复核，输出 A/B/C/D 分层")

db = _get_db()

# ── session_state ──
if "scoring_task_id" not in st.session_state:
    st.session_state.scoring_task_id = None
if "settings" not in st.session_state:
    from src.settings import Settings
    st.session_state.settings = Settings()


# ── 正在运行/刚完成的分层任务 ──
current_task_id = st.session_state.get("scoring_task_id")
active_task = get_task(current_task_id) if current_task_id else None
task_result = get_task_result(current_task_id) if current_task_id and active_task else {}

if active_task and active_task["progress"]["status"] in ("running", "pending"):
    p = active_task["progress"]
    st.markdown("---")
    st.subheader("🔄 分层运行中（后台）")
    col_prog, col_stop = st.columns([4, 1])
    with col_prog:
        st.progress(min(p["value"], 1.0), text=p.get("text", ""))
    with col_stop:
        if st.button("🛑 停止", type="secondary"):
            stop_task(current_task_id)
            st.rerun()
    st.info("💡 可在其他页面操作，分层不会中断")
    if st.button("🔄 刷新"):
        st.rerun()
    st.stop()

if active_task and active_task["progress"]["status"] in ("completed", "failed", "stopped"):
    p = active_task["progress"]
    st.markdown("---")
    if p["status"] == "completed":
        st.success(f"✅ 分层完成！{p.get('text', '')}")
    elif p["status"] == "failed":
        st.error(f"❌ 分层失败: {p.get('text', '')}")
    else:
        st.warning("⏹️ 已停止")
    if st.button("关闭", use_container_width=True):
        st.session_state.scoring_task_id = None
        st.rerun()
    st.stop()


# ── 评分配置 ──
st.markdown("---")
st.subheader("⚙️ 评分配置")

col_scope, col_only = st.columns(2)
with col_scope:
    scope = st.radio(
        "评分范围",
        options=["captured", "not_captured", "all"],
        format_func=lambda x: {
            "captured": "📸 已采集主页",
            "not_captured": "🔍 搜索发现（未采集主页）",
            "all": "📋 全部候选",
        }[x],
        index=0,
        horizontal=True,
        key="tier_scope",
    )
with col_only:
    only_unanalyzed = st.checkbox("仅分析未评分的候选", value=True, key="tier_only_unanalyzed")

# ── 状态概览 ──
st.markdown("---")
st.subheader("📊 候选状态概览")

status_counts = db.get_candidate_status_counts()
col_s1, col_s2, col_s3, col_s4, col_s5, col_s6 = st.columns(6)
col_s1.metric("🆕 新采集", status_counts.get("new", 0))
col_s2.metric("📊 已分析", status_counts.get("analyzed", 0))
col_s3.metric("⏳ 待采集主页", status_counts.get("profile_pending", 0))
col_s4.metric("✅ 已采集主页", status_counts.get("profile_captured", 0))
col_s5.metric("❌ 主页失败", status_counts.get("profile_failed", 0))
col_s6.metric("🔁 重复+忽略", status_counts.get("duplicate", 0) + status_counts.get("ignored", 0))

# ── 分层操作按钮 ──
st.markdown("---")
st.subheader("▶️ 执行分层")

col_b1, col_b2, col_b3, col_b4 = st.columns(4)

with col_b1:
    if st.button("📊 规则打分", use_container_width=True, type="primary"):
        candidates = db.get_candidates_for_scoring(scope=scope, only_unanalyzed=only_unanalyzed)
        if not candidates:
            st.warning("没有待评分的候选")
        else:
            task_id = start_task(
                name=f"规则打分 ({len(candidates)}条/{scope})",
                target=run_scoring_task,
                candidates=candidates,
                settings=st.session_state.settings,
                with_ai=False,
                update_db=True,
            )
            st.session_state.scoring_task_id = task_id
            st.success(f"✅ 规则打分已启动（{len(candidates)} 条）")
            st.rerun()

with col_b2:
    if st.button("🤖 AI 复核", use_container_width=True):
        candidates = db.get_candidates_for_scoring(scope=scope, only_unanalyzed=only_unanalyzed)
        if not candidates:
            st.warning("没有待评分的候选")
        else:
            task_id = start_task(
                name=f"AI 复核 ({len(candidates)}条/{scope})",
                target=run_scoring_task,
                candidates=candidates,
                settings=st.session_state.settings,
                with_ai=True,
                update_db=True,
            )
            st.session_state.scoring_task_id = task_id
            st.success(f"✅ AI 复核已启动（{len(candidates)} 条）")
            st.rerun()

with col_b3:
    if st.button("🚀 一键分层（规则+AI）", use_container_width=True, type="primary"):
        candidates = db.get_candidates_for_scoring(scope=scope, only_unanalyzed=only_unanalyzed)
        if not candidates:
            st.warning("没有待评分的候选")
        else:
            task_id = start_task(
                name=f"一键分层 ({len(candidates)}条/{scope})",
                target=run_scoring_task,
                candidates=candidates,
                settings=st.session_state.settings,
                with_ai=True,
                update_db=True,
            )
            st.session_state.scoring_task_id = task_id
            st.success(f"✅ 一键分层已启动（{len(candidates)} 条）")
            st.rerun()

with col_b4:
    if st.button("🔄 重新分层（全部覆盖）", use_container_width=True):
        candidates = db.get_candidates_for_restratify(tiers=None)
        if not candidates:
            st.warning("没有候选数据")
        else:
            task_id = start_task(
                name=f"全部重新分层 ({len(candidates)}条)",
                target=run_scoring_task,
                candidates=candidates,
                settings=st.session_state.settings,
                with_ai=True,
                update_db=True,
            )
            st.session_state.scoring_task_id = task_id
            st.success(f"✅ 全部重新分层已启动（{len(candidates)} 条）")
            st.rerun()


# ── 分层统计 ──
st.markdown("---")
st.subheader("📊 分层统计")

tier_counts = db.get_tier_counts()
col_t1, col_t2, col_t3, col_t4 = st.columns(4)
col_t1.metric("🔴 A 级 — 强匹配", tier_counts.get("A", 0))
col_t2.metric("🟠 B 级 — 较明确", tier_counts.get("B", 0))
col_t3.metric("🟡 C 级 — 有相关", tier_counts.get("C", 0))
col_t4.metric("⚪ D 级 — 不匹配", tier_counts.get("D", 0))

# ── 分层筛选 + 一键检查 ──
st.markdown("---")
st.subheader("🔍 分层筛选与重新检查")

col_filter, col_check = st.columns([3, 1])

with col_filter:
    selected_tiers_for_check = st.multiselect(
        "选择要重新检查的层级（多选，留空=全部重新检查）",
        options=["A", "B", "C", "D"],
        default=["A", "B", "C"],
        key="recheck_tiers",
        help="勾选的层级会被重新跑分。通常 A/B/C 需要复查，D 无需重复检查。",
    )

with col_check:
    st.caption("")  # spacer
    st.caption("")  # spacer
    target_label = "、".join(selected_tiers_for_check) if selected_tiers_for_check else "全部"
    if st.button(f"🔍 一键检查{target_label}", use_container_width=True, type="primary"):
        candidates = db.get_candidates_for_restratify(
            tiers=selected_tiers_for_check if selected_tiers_for_check else None
        )
        if not candidates:
            st.warning("没有需要重新检查的候选")
        else:
            task_id = start_task(
                name=f"一键检查 ({len(candidates)}条/{target_label})",
                target=run_scoring_task,
                candidates=candidates,
                settings=st.session_state.settings,
                with_ai=True,
                update_db=True,
            )
            st.session_state.scoring_task_id = task_id
            st.success(f"✅ 一键检查已启动（{len(candidates)} 条，层级={target_label}）")
            st.rerun()

st.caption("💡 选好层级后点击「一键检查」，系统会用最新规则重新评分覆盖旧数据。"
           "规则已更新：用户内容不含服装相关词 → 自动归为 D 级。")

# ── 快速跳转 ──
st.markdown("---")
st.subheader("🔗 快速入口")

col_link1, col_link2 = st.columns(2)
with col_link1:
    st.markdown(
        f"<div style='padding:0.5rem; border:1px solid #2196F3; border-radius:8px; text-align:center;'>"
        f"<a href='/7_线索分层数据库' target='_self' style='text-decoration:none; font-weight:bold;'>"
        f"📋 查看线索分层详情 →</a></div>",
        unsafe_allow_html=True,
    )
with col_link2:
    st.markdown(
        f"<div style='padding:0.5rem; border:1px solid #4CAF50; border-radius:8px; text-align:center;'>"
        f"<a href='/8_人工审核与导出' target='_self' style='text-decoration:none; font-weight:bold;'>"
        f"✅ 前往人工审核 →</a></div>",
        unsafe_allow_html=True,
    )

if not tier_counts:
    st.info("尚无分层数据，请先执行评分分层")
