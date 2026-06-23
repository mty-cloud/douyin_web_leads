"""线索分层数据库 — 浏览分层结果（联表 lead_analysis + candidates）。"""

import json
import streamlit as st
import pandas as pd
from src.db import Database
from src.analysis.lead_tier import get_tier_description

PAGE_SIZE = 30

TIER_OPTIONS = {
    "all": "全部分层",
    "A": "🔴 A级 - 强匹配",
    "B": "🟠 B级 - 较明确",
    "C": "🟡 C级 - 有相关",
    "D": "⚪ D级 - 不匹配",
}


def _get_db() -> Database:
    return Database()


st.title("📋 线索分层数据库")
st.caption("浏览所有线索分层结果，支持按层级、地区、关键词筛选")

db = _get_db()

# ── session_state ──
if "tierdb_page" not in st.session_state:
    st.session_state.tierdb_page = 0
if "tierdb_selected_id" not in st.session_state:
    st.session_state.tierdb_selected_id = None

# ── 筛选 ──
st.markdown("---")
st.subheader("🔍 筛选条件")

with st.expander("展开筛选", expanded=True):
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        filter_tier = st.selectbox(
            "分层",
            options=list(TIER_OPTIONS.keys()),
            format_func=lambda x: TIER_OPTIONS[x],
            key="tierdb_filter_tier",
        )
    with col_f2:
        filter_province = st.text_input("省份", placeholder="如：湖南省", key="tierdb_filter_prov")
    with col_f3:
        filter_keyword = st.text_input("搜索关键词", placeholder="模糊匹配", key="tierdb_filter_kw")

    if st.button("🔄 刷新", use_container_width=True):
        st.session_state.tierdb_page = 0
        st.rerun()

# ── 统计数据 ──
total = db.count_lead_analyses(
    tier=filter_tier if filter_tier != "all" else None,
    province=filter_province or None,
    keyword=filter_keyword or None,
)
total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
page = min(st.session_state.tierdb_page, total_pages - 1)
offset = page * PAGE_SIZE

# ── 筛选结果统计 ──
filter_tags = []
if filter_tier != "all": filter_tags.append(f"分层={filter_tier}")
if filter_province: filter_tags.append(f"省份={filter_province}")
if filter_keyword: filter_tags.append(f"关键词~{filter_keyword}")

col_cnt, col_info = st.columns([1, 3])
with col_cnt:
    st.metric("📊 筛选结果", total, help="当前筛选条件下的分层记录总数")
with col_info:
    if filter_tags:
        st.caption("筛选条件: " + " | ".join(filter_tags))
    else:
        st.caption("无筛选条件，显示全部分层记录")

st.caption(f"第 {page+1}/{total_pages} 页（每页 {PAGE_SIZE} 条）")

# ── 分页导航 ──
nav_cols = st.columns(5)
with nav_cols[0]:
    if st.button("⏮ 首页", disabled=(page == 0), use_container_width=True, key="tierdb_first"):
        st.session_state.tierdb_page = 0
        st.rerun()
with nav_cols[1]:
    if st.button("◀ 上一页", disabled=(page == 0), use_container_width=True, key="tierdb_prev"):
        st.session_state.tierdb_page = page - 1
        st.rerun()
with nav_cols[2]:
    st.write(f"　第 {page+1}/{total_pages} 页　")
with nav_cols[3]:
    if st.button("下一页 ▶", disabled=(page >= total_pages - 1), use_container_width=True, key="tierdb_next"):
        st.session_state.tierdb_page = page + 1
        st.rerun()
with nav_cols[4]:
    if st.button("末页 ⏭", disabled=(page >= total_pages - 1), use_container_width=True, key="tierdb_last"):
        st.session_state.tierdb_page = total_pages - 1
        st.rerun()

# ── 加载数据 ──
analyses = db.get_lead_analyses_with_candidates(
    tier=filter_tier if filter_tier != "all" else None,
    province=filter_province or None,
    keyword=filter_keyword or None,
    limit=PAGE_SIZE,
    offset=offset,
)

if analyses:
    rows = []
    for a in analyses:
        tier_icon = {"A": "🔴", "B": "🟠", "C": "🟡", "D": "⚪"}.get(a.get("tier", ""), "⚪")
        rows.append({
            "ID": a["id"],
            "Tier": f"{tier_icon} {a.get('tier', '?')}",
            "昵称": (a.get("nickname") or "")[:18],
            "地区": f'{a.get("source_province","")} {a.get("source_city","")}',
            "关键词": (a.get("source_keywords", "") or "")[:18],
            "规则分": a.get("rule_score", 0),
            "最终分": a.get("final_score", 0),
        })

    df = pd.DataFrame(rows)
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="tierdb_df",
    )

    if event and hasattr(event, "selection") and event.selection and event.selection.rows:
        idx = event.selection.rows[0]
        st.session_state.tierdb_selected_id = analyses[idx]["id"]
    else:
        st.session_state.tierdb_selected_id = None

    # ── 详情面板 ──
    if st.session_state.tierdb_selected_id:
        a = next(
            (x for x in analyses if x["id"] == st.session_state.tierdb_selected_id),
            None,
        )
        if a:
            tier_icon = {"A": "🔴", "B": "🟠", "C": "🟡", "D": "⚪"}.get(a.get("tier", ""), "⚪")
            with st.expander(f"📋 #{a['id']} {a.get('nickname', '?')} 分层详情", expanded=True):
                col1, col2 = st.columns(2)
                with col1:
                    st.write(f"**分层**: {tier_icon} {a.get('tier', '?')} — {get_tier_description(a.get('tier', '?'))}")
                    st.write(f"**地区**: {a.get('source_province', '')} {a.get('source_city', '')} {a.get('source_county', '')}")
                    st.write(f"**关键词**: {a.get('source_keywords', '')}")
                    st.write(f"**简介**: {(a.get('profile_bio', '') or '')[:300]}")
                with col2:
                    st.write(f"**各维度分**: ")
                    st.write(f"  行业={a.get('industry_score', 0)} 品类={a.get('category_score', 0)}")
                    st.write(f"  实体={a.get('store_score', 0)} 年龄={a.get('age_group_score', 0)}")
                    st.write(f"  可信={a.get('credibility_score', 0)} 地区={a.get('region_score', 0)}")
                    st.write(f"**规则分**: {a.get('rule_score', 0)} | **最终分**: {a.get('final_score', 0)}")

            # 证据
            evidence_raw = a.get("evidence", "") or ""
            if evidence_raw:
                with st.expander("📌 匹配证据"):
                    try:
                        evidence_list = json.loads(evidence_raw) if isinstance(evidence_raw, str) else evidence_raw
                        for e in evidence_list:
                            st.write(f"- {e}")
                    except Exception:
                        st.text(evidence_raw[:1000])

            neg_evidence_raw = a.get("negative_evidence", "") or ""
            if neg_evidence_raw:
                with st.expander("⚠️ 负面证据"):
                    try:
                        neg_list = json.loads(neg_evidence_raw) if isinstance(neg_evidence_raw, str) else neg_evidence_raw
                        for e in neg_list:
                            st.write(f"- {e}")
                    except Exception:
                        st.text(neg_evidence_raw[:1000])
else:
    st.info("无匹配记录")

# ── 快速入口 ──
st.markdown("---")
col_q1, col_q2 = st.columns(2)
with col_q1:
    st.markdown(
        f"<div style='padding:0.5rem; border:1px solid #4CAF50; border-radius:8px; text-align:center;'>"
        f"<a href='/8_人工审核与导出' target='_self' style='text-decoration:none; font-weight:bold;'>"
        f"✅ 前往人工审核与导出 →</a></div>",
        unsafe_allow_html=True,
    )
with col_q2:
    st.markdown(
        f"<div style='padding:0.5rem; border:1px solid #FF8C00; border-radius:8px; text-align:center;'>"
        f"<a href='/4_线索分层' target='_self' style='text-decoration:none; font-weight:bold;'>"
        f"📊 返回线索分层控制台 →</a></div>",
        unsafe_allow_html=True,
    )
