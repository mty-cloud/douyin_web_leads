"""采集控制台 — 主页采集（批量采集已选择候选的主页信息）。

从 candidates 表中读取 profile_pending 或 profile_captured 状态的候选，
逐一访问抖音主页，采集 profile_bio、profile_text、粉丝数等数据。
profile_captured 为重新采集覆盖模式。
"""

import sys
import os
import json
import tempfile
import subprocess
import time
from pathlib import Path

import streamlit as st
import pandas as pd

from src.db import Database
from src.settings import Settings


def _write_progress(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _get_db() -> Database:
    return Database()


STATUS_DISPLAY = {
    "profile_pending": "⏳ 未采集",
    "profile_captured": "✅ 已采集",
    "analyzed": "📊 已分析",
    "profile_failed": "❌ 失败",
    "profile_incomplete": "📄 信息不全",
}


# ═════════════════════════════════════════════════════════════════════════════
# 页面 UI
# ═════════════════════════════════════════════════════════════════════════════

st.title("📸 采集控制台 — 主页采集")
st.caption("批量采集候选的抖音主页信息（简介、粉丝、文本等），支持重新采集覆盖旧数据")

db = _get_db()

# ── session_state 初始化 ──
for key in (
    "prof_filt_status", "profile_batch_running", "profile_batch_proc",
    "profile_batch_progress_file", "profile_batch_limit", "batch_all_default",
    "prof_page", "prof_selected_id",
):
    if key not in st.session_state:
        if key == "prof_filt_status":
            st.session_state[key] = "profile_pending"
        elif key in ("profile_batch_limit", "batch_all_default"):
            st.session_state[key] = 50
        elif key in ("profile_batch_running",):
            st.session_state[key] = False
        elif key in ("profile_batch_proc", "profile_batch_progress_file", "prof_selected_id"):
            st.session_state[key] = None
        elif key == "prof_page":
            st.session_state[key] = 0


# ── 统计概览 ──
st.markdown("---")
st.subheader("📊 主页采集概览")

pending_count = 0
captured_count = 0
failed_count = 0
analyzed_count = 0
incomplete_count = 0
try:
    conn = db.get_conn()
    cur = conn.execute("SELECT COUNT(*) FROM candidates WHERE status='profile_pending'")
    pending_count = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM candidates WHERE status='profile_captured'")
    captured_count = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM candidates WHERE status='profile_failed'")
    failed_count = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM candidates WHERE status='analyzed'")
    analyzed_count = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM candidates WHERE status='profile_incomplete'")
    incomplete_count = cur.fetchone()[0]
except Exception:
    pass

col_p, col_c, col_f, col_a, col_i = st.columns(5)
col_p.metric("⏳ 待采集", pending_count)
col_c.metric("✅ 已采集", captured_count)
col_f.metric("❌ 失败", failed_count)
col_a.metric("📊 已分析", analyzed_count)
col_i.metric("📄 信息不全", incomplete_count)


# ── 筛选条件 ──
if not st.session_state.profile_batch_running:
    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    with col_f1:
        filter_ps = st.selectbox(
            "状态",
            options=["all", "profile_pending", "profile_captured", "profile_failed", "profile_incomplete"],
            format_func=lambda x: {
                "all": "全部",
                "profile_pending": "⏳ 未采集",
                "profile_captured": "✅ 已采集（可覆盖）",
                "profile_failed": "❌ 采集失败",
                "profile_incomplete": "📄 信息不全",
            }[x],
            key="prof_filt_status",
        )
    with col_f2:
        filter_province = st.text_input("省份", placeholder="如：湖南省", key="prof_filt_prov")
    with col_f3:
        filter_keyword = st.text_input("搜索关键词", placeholder="如：女装", key="prof_filt_kw")
    with col_f4:
        st.caption("&nbsp;")
        if st.button("🔄 刷新", use_container_width=True):
            st.session_state.prof_page = 0
            st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# 嵌入式数据浏览库（筛选条件下方）
# ═════════════════════════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("📋 数据浏览库")

PAGE_SIZE = 10
filter_ps = st.session_state.get("prof_filt_status", "all")
filter_prov = st.session_state.get("prof_filt_prov", "")
filter_kw = st.session_state.get("prof_filt_kw", "")

total = db.count_profile_captured(
    status=filter_ps if filter_ps != "all" else None,
    province=filter_prov or None,
    keyword=filter_kw or None,
)
total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
page = min(st.session_state.prof_page, total_pages - 1)
offset = page * PAGE_SIZE

# 筛选标签
filter_tags = []
if filter_ps != "all":
    filter_tags.append(f"状态={filter_ps}")
if filter_prov:
    filter_tags.append(f"省份={filter_prov}")
if filter_kw:
    filter_tags.append(f"关键词~{filter_kw}")

col_cnt, col_tags = st.columns([1, 3])
with col_cnt:
    st.metric("📊 匹配记录", total)
with col_tags:
    if filter_tags:
        st.caption("筛选: " + " | ".join(filter_tags))
    else:
        st.caption("无筛选条件，显示全部记录")

if total > 0:
    # 分页导航
    nav_cols = st.columns(5)
    with nav_cols[0]:
        if st.button("⏮ 首页", disabled=(page == 0), key="db_first"):
            st.session_state.prof_page = 0
            st.rerun()
    with nav_cols[1]:
        if st.button("◀ 上一页", disabled=(page == 0), key="db_prev"):
            st.session_state.prof_page = page - 1
            st.rerun()
    with nav_cols[2]:
        st.write(f"第 {page+1}/{total_pages} 页")
    with nav_cols[3]:
        if st.button("下一页 ▶", disabled=(page >= total_pages - 1), key="db_next"):
            st.session_state.prof_page = page + 1
            st.rerun()
    with nav_cols[4]:
        if st.button("末页 ⏭", disabled=(page >= total_pages - 1), key="db_last"):
            st.session_state.prof_page = total_pages - 1
            st.rerun()

    # 加载数据
    candidates = db.get_profile_captured_candidates(
        status=filter_ps if filter_ps != "all" else None,
        province=filter_prov or None,
        keyword=filter_kw or None,
        limit=PAGE_SIZE,
        offset=offset,
    )

    if candidates:
        rows = []
        for c in candidates:
            rows.append({
                "ID": c["id"],
                "状态": STATUS_DISPLAY.get(c.get("status", ""), c.get("status", "")),
                "昵称": (c.get("nickname") or "")[:20],
                "抖音号": (c.get("douyin_id") or "")[:15],
                "地区": f'{c.get("source_province","")} {c.get("source_city","")} {c.get("source_county","")}',
                "关键词": (c.get("source_keywords", "") or "")[:20],
                "微信": (c.get("wechat_id") or "")[:12],
            })

        df = pd.DataFrame(rows)
        event = st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="prof_db_embed",
        )

        # 选中行 → 详情
        selected_id = None
        if event and hasattr(event, "selection") and event.selection and event.selection.rows:
            idx = event.selection.rows[0]
            c = candidates[idx]
            selected_id = c["id"]
            st.session_state.prof_selected_id = selected_id
        else:
            selected_id = st.session_state.get("prof_selected_id")

        if selected_id:
            c = next((x for x in candidates if x["id"] == selected_id), None)
            if c:
                with st.expander(f"📋 #{c['id']} {c.get('nickname', '?')} 详情", expanded=True):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**昵称**: {c.get('nickname', '')}")
                        st.write(f"**抖音号**: {c.get('douyin_id', '')}")
                        st.write(f"**地区**: {c.get('source_province', '')} {c.get('source_city', '')} {c.get('source_county', '')}")
                        st.write(f"**关键词**: {c.get('source_keywords', '')}")
                        st.write(f"**状态**: {STATUS_DISPLAY.get(c.get('status', ''), c.get('status', ''))}")
                        profile_url = c.get("profile_url", "")
                        if profile_url:
                            st.markdown(f"**主页**: [{profile_url[:50]}...]({profile_url})")
                    with col2:
                        bio = c.get("profile_bio", "") or ""
                        if bio:
                            st.text_area("简介", value=bio[:500], height=80, disabled=True, key=f"bio_{c['id']}")
                        followers = c.get("followers_text", "") or ""
                        following = c.get("following_text", "") or ""
                        likes = c.get("likes_text", "") or ""
                        works = c.get("works_text", "") or ""
                        st.write(f"**粉丝**: {followers} | **关注**: {following} | **获赞**: {likes} | **作品**: {works}")
                        profile_text = c.get("profile_text", "") or ""
                        if profile_text:
                            with st.expander("📄 主页完整文本"):
                                st.text(profile_text[:2000])
                        ocr_text = c.get("profile_ocr_text", "") or ""
                        if ocr_text:
                            with st.expander("🔍 OCR 文本"):
                                st.text(ocr_text[:1000])

                    # 快捷操作
                    col_a1, col_a2, col_a3 = st.columns(3)
                    with col_a1:
                        st.caption("&nbsp;")
                    with col_a2:
                        if c.get("status") == "profile_pending":
                            if st.button("📸 立即采集", key=f"do_now_{c['id']}"):
                                st.info("请使用下方的批量采集按钮，或先标记后统一采集")
                    with col_a3:
                        if c.get("status") in ("profile_failed", "profile_incomplete", "profile_captured"):
                            if st.button("🔄 重置为待采集", key=f"reset_{c['id']}"):
                                db.update_candidate(c["id"], status="profile_pending",
                                                    profile_incomplete_reason="")
                                st.success("✅ 已重置为待采集")
                                st.rerun()
    else:
        st.info("本页无记录")
else:
    st.info("无匹配记录")


# ═════════════════════════════════════════════════════════════════════════════
# 启动批量主页采集
# ═════════════════════════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("🚀 启动批量采集")

# 根据筛选状态决定采集目标和提示文案
tgt_status = st.session_state.get("prof_filt_status", "profile_pending")

if tgt_status == "profile_failed":
    avail_count = failed_count
    action_label = "❌ 采集失败"
    btn_label = f"🔄 重试批量采集" if failed_count > 0 else "✅ 暂无失败记录"
    reset_note = "（自动将失败记录重置为待采集后执行）"
elif tgt_status == "profile_captured":
    avail_count = captured_count
    action_label = "✅ 已采集（可覆盖）"
    btn_label = f"🔄 重新采集（覆盖）" if captured_count > 0 else "✅ 暂无已采集记录"
    reset_note = "（自动将已采集记录重置后重新抓取，旧数据将被覆盖）"
elif tgt_status == "profile_incomplete":
    avail_count = incomplete_count
    action_label = "📄 信息不全"
    btn_label = f"🔄 重试信息不全" if incomplete_count > 0 else "✅ 暂无信息不全记录"
    reset_note = "（自动将信息不全记录重置为待采集后执行）"
else:
    avail_count = pending_count
    action_label = "⏳ 待采集"
    btn_label = f"🚀 启动批量主页采集" if pending_count > 0 else "✅ 暂无待采集"
    reset_note = ""

col_stats, col_action = st.columns([2, 1])

with col_stats:
    st.metric(f"当前{action_label}数", avail_count)
    if reset_note:
        st.caption(reset_note)
    else:
        st.caption(f"待采集: {pending_count} | 已采集: {captured_count} | 失败: {failed_count}")

with col_action:
    col_input, col_all = st.columns([3, 1])
    with col_input:
        safe_default = max(1, min(st.session_state.batch_all_default, avail_count)) if avail_count > 0 else 1
        st.number_input(
            f"采集数量",
            min_value=1, max_value=max(avail_count, 1),
            value=safe_default,
            key="batch_profile_limit",
            disabled=(avail_count == 0 or st.session_state.profile_batch_running),
        )
    with col_all:
        st.caption("&nbsp;")
        if st.button("全选", use_container_width=True,
                     disabled=(avail_count == 0 or st.session_state.profile_batch_running)):
            st.session_state.pop("batch_profile_limit", None)
            st.session_state.batch_all_default = avail_count
            st.rerun()

    if st.button(
        btn_label,
        type="primary",
        use_container_width=True,
        disabled=(avail_count == 0 or st.session_state.profile_batch_running),
    ):
        limit = st.session_state.get("batch_profile_limit", 50)
        conn = db.get_conn()

        # 根据目标状态，先将对应记录重置为 profile_pending
        if tgt_status == "profile_failed":
            conn.execute(
                "UPDATE candidates SET status='profile_pending', profile_incomplete_reason='' "
                "WHERE id IN (SELECT id FROM candidates WHERE status='profile_failed' ORDER BY id LIMIT ?)",
                (limit,),
            )
        elif tgt_status == "profile_captured":
            conn.execute(
                "UPDATE candidates SET status='profile_pending', profile_incomplete_reason='' "
                "WHERE id IN (SELECT id FROM candidates WHERE status='profile_captured' ORDER BY id LIMIT ?)",
                (limit,),
            )
        elif tgt_status == "profile_incomplete":
            conn.execute(
                "UPDATE candidates SET status='profile_pending', profile_incomplete_reason='' "
                "WHERE id IN (SELECT id FROM candidates WHERE status='profile_incomplete' ORDER BY id LIMIT ?)",
                (limit,),
            )
        conn.commit()

        project_root = Path(__file__).resolve().parent.parent.parent
        worker_script = str(project_root / "src" / "batch_profile_worker.py")
        progress_file = tempfile.mktemp(suffix="_profile_progress.json")

        _write_progress(progress_file, {
            "done": False, "total": 0, "captured": 0, "failed": 0, "scored": 0, "items": [],
        })

        proc = subprocess.Popen(
            [sys.executable, worker_script,
             f"--limit={limit}",
             f"--progress-file={progress_file}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(project_root),
        )

        st.session_state.profile_batch_running = True
        st.session_state.profile_batch_proc = proc
        st.session_state.profile_batch_progress_file = progress_file
        st.session_state.profile_batch_limit = limit
        st.rerun()


# ── 正在运行时的监控面板 ──
if st.session_state.get("profile_batch_running", False):
    proc = st.session_state.get("profile_batch_proc")
    progress_file = st.session_state.get("profile_batch_progress_file", "")

    st.markdown("---")
    st.subheader("🔄 主页采集中...")

    progress_data = {
        "done": False, "total": 0, "captured": 0, "failed": 0,
        "paused": 0, "scored": 0, "items": [], "error": None,
    }
    if progress_file and os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                progress_data = json.load(f)
        except Exception:
            pass

    total = progress_data.get("total", 0)
    captured = progress_data.get("captured", 0)
    failed = progress_data.get("failed", 0)
    paused = progress_data.get("paused", 0)
    scored = progress_data.get("scored", 0)
    done = progress_data.get("done", False)

    if total > 0:
        pbar_val = min(total / max(total, 1), 1.0)
        st.progress(pbar_val, text=f"已处理: {captured + failed}/{total}")
    else:
        st.progress(0.5, text="正在启动浏览器...")

    col_p, col_f, col_pz, col_s = st.columns(4)
    col_p.metric("✅ 采集成功", captured)
    col_f.metric("❌ 失败", failed)
    col_pz.metric("⛔ 风控", paused)
    col_s.metric("📊 自动分层", scored)

    # 最新结果列表
    items = progress_data.get("items", [])
    if items:
        with st.expander("📋 采集详情", expanded=True):
            recent = items[-20:]
            for item in recent:
                sid = item.get("candidate_id", "")
                s = item.get("status", "")
                nm = item.get("nickname", "")
                if s == "profile_captured":
                    st.markdown(f"  ✅ #{sid} {nm}")
                elif s == "paused_need_human":
                    st.markdown(f"  ⛔ #{sid} {nm} — 风控暂停")
                else:
                    st.markdown(f"  ❌ #{sid} {nm}")

    # 停止/刷新按钮
    col_stop, col_refresh = st.columns([1, 1])
    with col_stop:
        if not done:
            if st.button("🛑 停止采集", use_container_width=True, type="secondary"):
                try:
                    if proc and proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass
                st.session_state.profile_batch_running = False
                st.rerun()

    with col_refresh:
        if st.button("🔄 刷新", use_container_width=True):
            st.rerun()

    # 检查子进程是否结束
    is_alive = proc and proc.poll() is None if proc else False

    if done or (not is_alive and total > 0):
        if paused > 0:
            st.warning("⚠️ 部分用户触发风控，请处理验证码后重新采集")
        elif progress_data.get("error"):
            st.error(f"❌ 采集异常: {progress_data['error']}")
        else:
            st.success(f"✅ 批量主页采集完成！成功 {captured} 条，自动分层 {scored} 个")

        if st.button("关闭", use_container_width=True, type="secondary"):
            st.session_state.profile_batch_running = False
            if progress_file and os.path.exists(progress_file):
                try:
                    os.unlink(progress_file)
                except Exception:
                    pass
            st.rerun()

    elif paused > 0:
        st.error("⛔ **触发风控 — 请处理验证码**")

    # 如果进程已结束但 done 还是 False
    if not is_alive and not done:
        if progress_data.get("total", 0) > 0 or progress_data.get("error"):
            progress_data["done"] = True
            if progress_file:
                _write_progress(progress_file, progress_data)
            st.rerun()


# ── 快速入口 ──
st.markdown("---")
st.subheader("🔗 快速入口")

col_q1, col_q2 = st.columns(2)
with col_q1:
    st.markdown(
        f"<div style='padding:0.5rem; border:1px solid #2196F3; border-radius:8px; text-align:center;'>"
        f"<a href='/6_主页采集数据库' target='_self' style='text-decoration:none; font-weight:bold;'>"
        f"📋 查看完整主页采集数据库 →</a></div>",
        unsafe_allow_html=True,
    )
with col_q2:
    st.markdown(
        f"<div style='padding:0.5rem; border:1px solid #4CAF50; border-radius:8px; text-align:center;'>"
        f"<a href='/4_线索分层' target='_self' style='text-decoration:none; font-weight:bold;'>"
        f"📊 前往线索分层 →</a></div>",
        unsafe_allow_html=True,
    )
