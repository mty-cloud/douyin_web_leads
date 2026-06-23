"""主页采集数据库 — 已采集/待采集/采集失败的主页候选列表。"""

import streamlit as st
import pandas as pd
from src.db import Database

PAGE_SIZE = 30

STATUS_OPTIONS = {
    "all": "全部",
    "profile_pending": "⏳ 待采集主页",
    "profile_captured": "✅ 已采集主页",
    "profile_done": "📊 已分析/已采集",
    "profile_failed": "❌ 采集失败",
}


def _get_db() -> Database:
    return Database()


st.title("🏠 主页采集数据库")
st.caption("浏览已进入主页采集流程的候选用户（profile_pending / profile_captured / profile_failed）")

db = _get_db()

# ── session_state ──
if "prof_page" not in st.session_state:
    st.session_state.prof_page = 0
if "prof_selected_id" not in st.session_state:
    st.session_state.prof_selected_id = None

# ── 筛选 ──
st.markdown("---")
st.subheader("🔍 筛选条件")

with st.expander("展开筛选", expanded=True):
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        filter_status = st.selectbox(
            "状态",
            options=list(STATUS_OPTIONS.keys()),
            format_func=lambda x: STATUS_OPTIONS[x],
            key="prof_filter_status",
        )
    with col_f2:
        filter_province = st.text_input("省份", placeholder="如：湖南省", key="prof_filter_prov")
    with col_f3:
        filter_keyword = st.text_input("搜索关键词", placeholder="模糊匹配", key="prof_filter_kw")

    if st.button("🔄 刷新", use_container_width=True):
        st.session_state.prof_page = 0
        st.rerun()

# ── 数据 ──
total = db.count_profile_captured(
    status=filter_status if filter_status != "all" else None,
    province=filter_province or None,
    keyword=filter_keyword or None,
)
total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
page = min(st.session_state.prof_page, total_pages - 1)
offset = page * PAGE_SIZE

# ── 筛选结果统计 ──
filter_tags = []
if filter_status != "all": filter_tags.append(f"状态={STATUS_OPTIONS.get(filter_status, filter_status)}")
if filter_province: filter_tags.append(f"省份={filter_province}")
if filter_keyword: filter_tags.append(f"关键词~{filter_keyword}")

col_cnt, col_info = st.columns([1, 3])
with col_cnt:
    st.metric("📊 筛选结果", total, help="当前筛选条件下的记录总数")
with col_info:
    if filter_tags:
        st.caption("筛选条件: " + " | ".join(filter_tags))
    else:
        st.caption("无筛选条件，显示全部记录")

st.caption(f"第 {page+1}/{total_pages} 页（每页 {PAGE_SIZE} 条）")

# ── 分页导航（统一组件） ──
nav_cols = st.columns(5)
with nav_cols[0]:
    if st.button("⏮ 首页", disabled=(page == 0), use_container_width=True, key="prof_first"):
        st.session_state.prof_page = 0
        st.rerun()
with nav_cols[1]:
    if st.button("◀ 上一页", disabled=(page == 0), use_container_width=True, key="prof_prev"):
        st.session_state.prof_page = page - 1
        st.rerun()
with nav_cols[2]:
    st.write(f"　第 {page+1}/{total_pages} 页　")
with nav_cols[3]:
    if st.button("下一页 ▶", disabled=(page >= total_pages - 1), use_container_width=True, key="prof_next"):
        st.session_state.prof_page = page + 1
        st.rerun()
with nav_cols[4]:
    if st.button("末页 ⏭", disabled=(page >= total_pages - 1), use_container_width=True, key="prof_last"):
        st.session_state.prof_page = total_pages - 1
        st.rerun()

# ── 加载数据 ──
candidates = db.get_profile_captured_candidates(
    status=filter_status if filter_status != "all" else None,
    province=filter_province or None,
    keyword=filter_keyword or None,
    limit=PAGE_SIZE,
    offset=offset,
)

if candidates:
    status_display = {
        "profile_pending": "⏳ 未采集",
        "profile_captured": "✅ 已采集",
        "analyzed": "📊 已分析",
        "profile_failed": "❌ 失败",
    }
    rows = []
    for c in candidates:
        rows.append({
            "ID": c["id"],
            "状态": status_display.get(c.get("status", ""), c.get("status", "")),
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
        key="prof_db_df",
    )

    if event and hasattr(event, "selection") and event.selection and event.selection.rows:
        idx = event.selection.rows[0]
        c = candidates[idx]
        st.session_state.prof_selected_id = c["id"]
    else:
        st.session_state.prof_selected_id = None

    # ── 详情面板 ──
    if st.session_state.prof_selected_id:
        c = next(
            (x for x in candidates if x["id"] == st.session_state.prof_selected_id),
            None,
        )
        if c:
            with st.expander(f"📋 #{c['id']} {c.get('nickname', '?')} 详情", expanded=True):
                col1, col2 = st.columns(2)
                with col1:
                    st.write(f"**昵称**: {c.get('nickname', '')}")
                    st.write(f"**抖音号**: {c.get('douyin_id', '')}")
                    st.write(f"**地区**: {c.get('source_province', '')} {c.get('source_city', '')} {c.get('source_county', '')}")
                    st.write(f"**关键词**: {c.get('source_keywords', '')}")
                    st.write(f"**状态**: {status_display.get(c.get('status', ''), c.get('status', ''))}")
                    profile_url = c.get("profile_url", "")
                    if profile_url:
                        st.markdown(f"**主页链接**: [{profile_url[:50]}...]({profile_url})")
                with col2:
                    bio = c.get("profile_bio", "") or ""
                    if bio:
                        st.text_area("简介", value=bio[:500], height=80, disabled=True)
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
                        with st.expander("🔍 OCR 识别文本"):
                            st.text(ocr_text[:1000])

                # 快捷操作
                col_a1, col_a2 = st.columns(2)
                with col_a1:
                    if c.get("status") == "profile_pending":
                        if st.button("📸 采集主页", key=f"prof_do_{c['id']}"):
                            # 标记为待采集（已经是）
                            pass
                with col_a2:
                    if c.get("status") == "profile_failed":
                        if st.button("🔄 重试", key=f"retry_{c['id']}"):
                            db.update_candidate_status(c["id"], "profile_pending")
                            st.success("✅ 已重置为待采集")
                            st.rerun()
else:
    st.info("无匹配记录")
