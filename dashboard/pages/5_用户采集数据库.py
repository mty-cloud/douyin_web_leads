"""用户采集数据库 — 所有候选用户列表浏览 + 筛选 + 分页。"""

import streamlit as st
import pandas as pd
from src.db import Database
from src.settings import Settings

PAGE_SIZE = 30

STATUS_DISPLAY = {
    "": "全部状态",
    "new": "🆕 新采集",
    "search_captured": "🔍 搜索捕获",
    "card_low_score": "📉 卡片低分",
    "profile_pending": "⏳ 待采集主页",
    "profile_captured": "✅ 已采集主页",
    "analyzed": "📊 已分析",
    "profile_failed": "❌ 主页失败",
    "duplicate": "🔁 重复",
    "ignored": "⛔ 忽略",
}


def _get_db() -> Database:
    return Database()


st.title("👤 用户采集数据库")
st.caption("浏览所有搜索发现的候选用户，支持筛选、查看详情、管理操作")

db = _get_db()

# ── session_state ──
if "cand_page" not in st.session_state:
    st.session_state.cand_page = 0
if "cand_selected_id" not in st.session_state:
    st.session_state.cand_selected_id = None

# ── 筛选区域 ──
st.markdown("---")
st.subheader("🔍 筛选条件")

with st.expander("展开筛选", expanded=True):
    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    with col_f1:
        filter_status = st.selectbox(
            "状态",
            options=list(STATUS_DISPLAY.keys()),
            format_func=lambda x: STATUS_DISPLAY[x],
            key="cand_filter_status",
        )
    with col_f2:
        filter_province = st.text_input("省份", placeholder="如：湖南省", key="cand_filter_prov")
    with col_f3:
        filter_keyword = st.text_input("搜索关键词", placeholder="模糊匹配", key="cand_filter_kw")
    with col_f4:
        has_url_opts = {"": "全部", "yes": "有主页链接", "no": "无主页链接"}
        filter_url = st.selectbox(
            "主页链接",
            options=list(has_url_opts.keys()),
            format_func=lambda x: has_url_opts[x],
            key="cand_filter_url",
        )

    col_btn1, col_btn2 = st.columns([1, 5])
    with col_btn1:
        if st.button("🔄 刷新", use_container_width=True):
            st.session_state.cand_page = 0
            st.rerun()

# ── 转换筛选参数 ──
filter_status_val = filter_status if filter_status else None
has_url = None
if filter_url == "yes":
    has_url = True
elif filter_url == "no":
    has_url = False

# ── 统计数据 ──
total = db.count_candidates(
    status=filter_status_val,
    province=filter_province or None,
    keyword=filter_keyword or None,
    has_profile_url=has_url,
)
total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
page = min(st.session_state.cand_page, total_pages - 1)
offset = page * PAGE_SIZE

# ── 筛选结果统计 ──
filter_tags = []
if filter_status_val: filter_tags.append(f"状态={STATUS_DISPLAY.get(filter_status_val, filter_status_val)}")
if filter_province: filter_tags.append(f"省份={filter_province}")
if filter_keyword: filter_tags.append(f"关键词~{filter_keyword}")
if has_url is True: filter_tags.append("有主页链接")
elif has_url is False: filter_tags.append("无主页链接")

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
    if st.button("⏮ 首页", disabled=(page == 0), use_container_width=True, key="cand_first"):
        st.session_state.cand_page = 0
        st.rerun()
with nav_cols[1]:
    if st.button("◀ 上一页", disabled=(page == 0), use_container_width=True, key="cand_prev"):
        st.session_state.cand_page = page - 1
        st.rerun()
with nav_cols[2]:
    st.write(f"　第 {page+1}/{total_pages} 页　")
with nav_cols[3]:
    if st.button("下一页 ▶", disabled=(page >= total_pages - 1), use_container_width=True, key="cand_next"):
        st.session_state.cand_page = page + 1
        st.rerun()
with nav_cols[4]:
    if st.button("末页 ⏭", disabled=(page >= total_pages - 1), use_container_width=True, key="cand_last"):
        st.session_state.cand_page = total_pages - 1
        st.rerun()

# ── 数据加载 ──
candidates = db.get_candidates(
    status=filter_status_val,
    province=filter_province or None,
    keyword=filter_keyword or None,
    has_profile_url=has_url,
    limit=PAGE_SIZE,
    offset=offset,
)

if candidates:
    status_display = {
        "new": "🆕 新采集",
        "profile_pending": "⏳ 待采集",
        "profile_captured": "✅ 已采集主页",
        "analyzed": "📊 已分析",
        "profile_failed": "❌ 失败",
        "duplicate": "🔁 重复",
        "ignored": "⛔ 忽略",
        "search_captured": "🔍 搜索捕获",
        "card_low_score": "📉 低分",
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
        key="cand_db_df",
    )

    # 选中某行 → 展开详情
    if event and hasattr(event, "selection") and event.selection and event.selection.rows:
        idx = event.selection.rows[0]
        c = candidates[idx]
        st.session_state.cand_selected_id = c["id"]
    else:
        st.session_state.cand_selected_id = None

    # ── 详情面板 ──
    if st.session_state.cand_selected_id:
        c = next(
            (x for x in candidates if x["id"] == st.session_state.cand_selected_id),
            None,
        )
        if c:
            with st.expander(f"📋 #{c['id']} {c.get('nickname', '?')} 详情", expanded=True):
                col1, col2 = st.columns(2)
                with col1:
                    new_nick = st.text_input("昵称", value=c.get("nickname", ""), key=f"ed_nick_{c['id']}")
                    new_dy = st.text_input("抖音号", value=c.get("douyin_id", ""), key=f"ed_dy_{c['id']}")
                    new_url = st.text_input("主页链接", value=c.get("profile_url", ""), key=f"ed_url_{c['id']}")
                    if st.button("💾 保存编辑", key=f"save_{c['id']}"):
                        db.update_candidate(
                            c["id"],
                            nickname=new_nick,
                            douyin_id=new_dy,
                            profile_url=new_url,
                        )
                        st.success("✅ 已保存")
                        st.rerun()
                with col2:
                    bio = c.get("profile_bio", "") or ""
                    st.text_area("简介", value=bio[:500] if bio else "", key=f"bio_{c['id']}", height=80, disabled=True)
                    card_text = c.get("search_card_text", "") or ""
                    if card_text:
                        with st.popover("查看搜索卡片文本"):
                            st.text(card_text[:2000])

                # 快捷操作
                col_a1, col_a2, col_a3 = st.columns(3)
                with col_a1:
                    if st.button("📸 采集主页", key=f"profile_{c['id']}"):
                        db.update_candidate_status(c["id"], "profile_pending")
                        st.success("✅ 已标记为待采集主页")
                        st.rerun()
                with col_a2:
                    if st.button("🔁 标记重复", key=f"dup_{c['id']}"):
                        db.update_candidate_status(c["id"], "duplicate")
                        st.rerun()
                with col_a3:
                    if st.button("⛔ 忽略", key=f"ign_{c['id']}"):
                        db.update_candidate_status(c["id"], "ignored")
                        st.rerun()

else:
    st.info("无匹配记录")
