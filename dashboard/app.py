"""Streamlit 多页面后台入口。"""

import sys
from pathlib import Path

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from dotenv import load_dotenv

# 加载环境变量
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv()  # 也尝试当前目录

st.set_page_config(
    page_title="douyin_web_leads - 抖音客户线索识别系统",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 初始化全局 session_state
if "db_initialized" not in st.session_state:
    st.session_state.db_initialized = False
if "settings" not in st.session_state:
    from src.settings import Settings
    st.session_state.settings = Settings()
if "logger" not in st.session_state:
    from src.logger import setup_logger
    st.session_state.logger = setup_logger()
if "browser_manager" not in st.session_state:
    st.session_state.browser_manager = None

st.sidebar.title("🎯 douyin_web_leads")
st.sidebar.caption("抖音客户线索识别系统")

st.sidebar.markdown("---")
st.sidebar.markdown("### 📋 前台 — 工作流")
st.sidebar.page_link("pages/1_仪表盘.py", label="📊 仪表盘", icon="1️⃣")
st.sidebar.page_link("pages/2_用户采集.py", label="🎯 用户采集", icon="2️⃣")
st.sidebar.page_link("pages/3_主页采集.py", label="📸 主页采集", icon="3️⃣")
st.sidebar.page_link("pages/4_线索分层.py", label="📊 线索分层", icon="4️⃣")
st.sidebar.page_link("pages/5_用户采集数据库.py", label="👤 用户采集数据库", icon="5️⃣")
st.sidebar.page_link("pages/6_主页采集数据库.py", label="🏠 主页采集数据库", icon="6️⃣")
st.sidebar.page_link("pages/7_线索分层数据库.py", label="📋 线索分层数据库", icon="7️⃣")
st.sidebar.page_link("pages/8_人工审核与导出.py", label="✅ 人工审核与导出", icon="8️⃣")

st.sidebar.markdown("---")
st.sidebar.markdown("### ⚙️ 后台 — 管理")
st.sidebar.page_link("pages/9_关键词管理.py", label="📝 关键词管理", icon="9️⃣")
st.sidebar.page_link("pages/10_风控事件.py", label="⚠️ 风控事件", icon="🔟")
st.sidebar.page_link("pages/11_采集预算设置.py", label="🔧 采集预算与数据修复", icon="🔧")
st.sidebar.page_link("pages/12_系统日志.py", label="📋 系统日志", icon="🔟")

# ── 侧边栏：后台任务状态监控（所有页面可见） ──
st.sidebar.markdown("---")
st.sidebar.markdown("### ⚙️ 后台任务")

from src.analysis.background_task_manager import list_tasks, stop_task, cleanup_completed

cleanup_completed(older_than_seconds=600)  # 自动清理 10 分钟前的已完成任务

active_tasks = list_tasks()
if active_tasks:
    for t in active_tasks:
        p = t["progress"]
        status_icon = {
            "running": "🔄", "completed": "✅", "failed": "❌",
            "stopped": "⏹️", "pending": "⏳",
        }.get(p["status"], "❓")
        st.sidebar.markdown(
            f"**{status_icon} {t['name']}**  \n"
            f"`{t['task_id']}` {p.get('text', '')[:60]}"
        )
        if p["status"] == "running":
            st.sidebar.progress(min(p["value"], 1.0))
            if st.sidebar.button(f"🛑 停止", key=f"stop_{t['task_id']}"):
                stop_task(t["task_id"])
                st.rerun()
        elif p["status"] in ("completed", "failed", "stopped"):
            st.sidebar.caption(f"状态: {p['status']} | {p.get('text', '')[:40]}")
    st.sidebar.divider()
else:
    st.sidebar.caption("暂无运行中的后台任务")

st.sidebar.markdown("---")
st.sidebar.caption("v3.0 | 重构仪表盘 | Playwright + Streamlit + 智谱 AI")
