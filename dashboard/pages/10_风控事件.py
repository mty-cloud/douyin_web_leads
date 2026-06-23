"""风控事件管理页面。

显示风控发生时间、event_type、keyword、page_url、截图、HTML 快照等信息。
支持标记已处理、重置任务、暂停所有任务、一键恢复采集。
"""

import streamlit as st
import os
import time
from pathlib import Path

from src.db import Database
from src.risk_utils import clear_risk_sentinel


def _get_db() -> Database:
    return Database()


st.set_page_config(
    page_title="风控事件",
    page_icon="⚠️",
    layout="wide",
)

st.title("⚠️ 风控事件管理")
st.caption("查看和处置采集过程中触发的风控/验证码事件")

db = _get_db()

# ═══════════════════════════════════════════════════════════════════════════════
# 顶层：一键恢复区
# ═══════════════════════════════════════════════════════════════════════════════
paused_tasks = db.get_keyword_tasks(status="paused_need_human")
risk_events_count = len(db.get_risk_events(limit=1, is_handled=0))

if paused_tasks or risk_events_count > 0:
    st.error("⚠️ **当前有待处理的风控事件**")

    col_guide, col_actions = st.columns([3, 2])

    with col_guide:
        st.markdown("""
        **处理步骤：**
        1. 查看下方截图确认风控类型
        2. 点击 **「🌐 打开浏览器处理验证码」**
        3. 手动完成验证码 / 重新扫码登录
        4. 关闭浏览器窗口（登录态自动保存）
        5. 点击 **「🔄 一键恢复采集」**
        """)

    with col_actions:
        if st.button("🌐 打开 Chromium（处理验证码）",
                     use_container_width=True, type="secondary"):
            from src.browser.browser_manager import safe_kill_chromium
            import sys
            safe_kill_chromium(force=True)
            time.sleep(1)
            project_root = Path(__file__).resolve().parent.parent.parent
            login_helper = str(project_root / "src" / "login_helper.py")
            subprocess.Popen(
                [sys.executable, login_helper],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=str(project_root),
            )
            st.success("✅ Chromium 已启动，请完成验证码/登录后关闭窗口")
            st.rerun()

        if st.button("🔄 一键恢复采集", use_container_width=True, type="primary"):
            clear_risk_sentinel()
            reset_count = db.reset_failed_tasks()
            st.success(f"✅ 已重置 {reset_count} 个暂停任务为 pending，可前往用户采集页重新启动")
            time.sleep(1)
            st.rerun()

    st.markdown("---")

# ═══════════════════════════════════════════════════════════════════════════════
# 筛选
# ═══════════════════════════════════════════════════════════════════════════════
col1, col2 = st.columns(2)
with col1:
    filter_handled = st.selectbox("状态筛选", ["全部", "未处理", "已处理"], key="risk_filter_handled")
with col2:
    st.caption("风控事件说明：当采集过程中检测到验证码、安全验证、访问过于频繁等关键词时，"
               "系统会自动记录事件并暂停相关任务。")

handled_filter = None
if filter_handled == "未处理":
    handled_filter = 0
elif filter_handled == "已处理":
    handled_filter = 1

events = db.get_risk_events(limit=100, is_handled=handled_filter)

st.subheader(f"风控事件列表 ({len(events)} 条)")

if not events:
    st.info("暂无风控事件记录")
else:
    for event in events:
        with st.container():
            st.markdown("---")

            is_handled = event.get('is_handled')
            cols = st.columns([2, 2, 2, 3])
            with cols[0]:
                st.write(f"**#{event['id']}** {'✅ 已处理' if is_handled else '❌ 未处理'}")
                st.write(f"🕐 {event.get('created_at', '-')}")
                st.write(f"📋 {event.get('event_type', '-')}")

            with cols[1]:
                st.write(f"**关键词**: {event.get('keyword', '-')}")
                url = event.get('page_url', '')
                if url:
                    st.write(f"🔗 [页面链接]({url})")
                st.write(f"**操作**: {event.get('action_when_triggered', '-')}")

            with cols[2]:
                screenshot = event.get('screenshot_path', '')
                if screenshot and os.path.exists(screenshot):
                    try:
                        st.image(screenshot, caption="风控截图", width=200)
                    except Exception:
                        st.write("📷 截图加载失败")
                elif screenshot:
                    st.write(f"📷 截图路径: `{screenshot}`")

            with cols[3]:
                html_path = event.get('html_snapshot_path', '')
                if html_path:
                    st.write(f"📄 HTML: `{html_path}`")
                page_text = event.get('page_text', '')
                if page_text:
                    with st.expander("页面文本"):
                        st.text(page_text[:500])

            # 操作按钮
            op_cols = st.columns(5)
            with op_cols[0]:
                if not is_handled and st.button("✅ 标记已处理", key=f"handle_{event['id']}"):
                    db.mark_risk_event_handled(event['id'])
                    st.rerun()
            with op_cols[1]:
                if st.button("🔄 重置任务", key=f"reset_{event['id']}"):
                    db.reset_task_from_risk(event['id'])
                    st.success("已重置关联任务")
                    st.rerun()
            with op_cols[2]:
                if st.button("⏸️ 暂停所有", key=f"pause_all_{event['id']}"):
                    count = db.pause_all_tasks()
                    st.success(f"已暂停 {count} 个任务")
                    st.rerun()
            with op_cols[3]:
                if not is_handled:
                    st.markdown("**建议:** ①截图确认风控类型 → ②打开浏览器处理 → ③标记已处理")

# ═══════════════════════════════════════════════════════════════════════════════
# 批量操作
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("⚡ 批量操作")

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("✅ 批量标记所有未处理为已处理", use_container_width=True):
        unhandled = db.get_risk_events(limit=500, is_handled=0)
        for e in unhandled:
            db.mark_risk_event_handled(e['id'])
        st.success(f"已标记 {len(unhandled)} 条为已处理")
        st.rerun()

with col2:
    if st.button("🔄 重置所有暂停任务为 pending", use_container_width=True):
        from src.db import Database as DB
        conn = db.get_conn()
        cursor = conn.execute(
            "UPDATE keyword_tasks SET status = 'pending', error_message = NULL WHERE status = 'paused_need_human'"
        )
        conn.commit()
        st.success(f"已重置 {cursor.rowcount} 个任务为 pending")
        st.rerun()

with col3:
    if st.button("🗑️ 清除风控标记", use_container_width=True):
        from src.risk_utils import clear_risk_sentinel
        clear_risk_sentinel()
        st.success("风控标记已清除")
        st.rerun()
