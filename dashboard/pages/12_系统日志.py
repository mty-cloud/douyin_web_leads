"""系统日志页面。"""

import streamlit as st
from pathlib import Path

from src.db import Database
from src.settings import PROJECT_ROOT


def _get_db() -> Database:
    return Database()


st.title("📋 系统日志")
st.caption("查看系统运行日志、失败任务和错误信息")

db = _get_db()

# 日志文件查看
st.subheader("📄 日志文件")
log_dir = PROJECT_ROOT / "data" / "logs"
if log_dir.exists():
    log_files = sorted(log_dir.glob("*.log"), reverse=True)
    if log_files:
        selected_log = st.selectbox("选择日志文件", log_files, format_func=lambda p: p.name)
        if selected_log:
            content = selected_log.read_text(encoding="utf-8", errors="replace")
            st.text_area("日志内容", content[-10000:], height=400)
    else:
        st.info("暂无日志文件")
else:
    st.info("日志目录不存在")

# 失败任务
st.markdown("---")
st.subheader("❌ 失败任务")

failed_tasks = db.get_keyword_tasks(status="failed")
paused_tasks = db.get_keyword_tasks(status="paused_need_human")

col1, col2 = st.columns(2)
with col1:
    st.write(f"**失败任务**: {len(failed_tasks)} 个")
    for t in failed_tasks[:20]:
        with st.expander(f"#{t['id']} {t.get('keyword', '?')}"):
            st.write(f"错误: {t.get('error_message', '无')}")
            st.write(f"时间: {t.get('finished_at', '')}")
            if st.button(f"重置为待运行", key=f"reset_f_{t['id']}"):
                db.reset_task(t["id"])
                st.rerun()

with col2:
    st.write(f"**待人工处理**: {len(paused_tasks)} 个")
    for t in paused_tasks[:20]:
        with st.expander(f"#{t['id']} {t.get('keyword', '?')}"):
            st.write(f"原因: {t.get('error_message', '无')}")
            if st.button(f"重置为待运行", key=f"reset_p_{t['id']}"):
                db.reset_task(t["id"])
                st.rerun()

# 主页采集失败
st.markdown("---")
st.subheader("🏠 主页采集失败")

failed_profiles = db.get_profile_failed_candidates(limit=50)
if failed_profiles:
    st.write(f"**采集失败**: {len(failed_profiles)} 个")
    for c in failed_profiles[:20]:
        col1, col2, col3 = st.columns([4, 2, 1])
        with col1:
            st.write(f"#{c['id']} {c.get('nickname', '?')} ({c.get('douyin_id', '')})")
        with col2:
            st.write(f"{c.get('profile_url', '')[:50]}")
        with col3:
            if st.button("重试", key=f"retry_pf_{c['id']}"):
                db.update_candidate(c["id"], status="profile_pending")
                st.rerun()
else:
    st.info("暂无主页采集失败记录")

# 最近截图和快照路径
st.markdown("---")
st.subheader("📸 最近截图路径")

screenshot_dir = PROJECT_ROOT / "data" / "screenshots" / "search"
if screenshot_dir.exists():
    screenshots = sorted(screenshot_dir.glob("*.png"), reverse=True)[:10]
    for s in screenshots:
        st.write(f"- {s.name} ({s.stat().st_size / 1024:.1f} KB)")
else:
    st.info("暂无截图")

html_dir = PROJECT_ROOT / "data" / "html_snapshots" / "search"
if html_dir.exists():
    snapshots = sorted(html_dir.glob("*.html"), reverse=True)[:10]
    for s in snapshots:
        st.write(f"- {s.name} ({s.stat().st_size / 1024:.1f} KB)")

# 数据库信息
st.markdown("---")
st.subheader("🗄️ 数据库信息")
db_path = PROJECT_ROOT / "data" / "leads.sqlite"
if db_path.exists():
    st.write(f"路径: {db_path}")
    st.write(f"大小: {db_path.stat().st_size / 1024:.1f} KB")
else:
    st.info("数据库文件不存在，请先在首页初始化")
