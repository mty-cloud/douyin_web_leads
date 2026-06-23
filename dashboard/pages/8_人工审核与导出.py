"""人工审核与导出页面 — 审核线索分层结果 + Excel 导出。"""

import streamlit as st

from src.db import Database
from src.analysis.lead_tier import get_tier_description
from src.analysis.background_task_manager import (
    start_task, stop_task, get_task, get_task_result, run_wechat_extraction_task,
)

PAGE_SIZE = 30


def _get_db() -> Database:
    return Database()


def _update_tier(db: Database, cid: int, old_tier: str, new_tier: str, note: str = ""):
    db.update_candidate(cid, manual_review_status="approved")
    db.update_lead_tier(cid, new_tier)
    db.add_review_log(cid, old_tier, new_tier, "manual_tier_change", note or f"改为{new_tier}")


st.title("✅ 人工审核与导出")
st.caption("审核线索分层结果，修改分层，导出 Excel")

if "review_wechat_task_id" not in st.session_state:
    st.session_state.review_wechat_task_id = None
if "review_page" not in st.session_state:
    st.session_state.review_page = 0

db = _get_db()

# ── 微信提取任务状态 ──
wechat_task_id = st.session_state.get("review_wechat_task_id")
active_wechat = get_task(wechat_task_id) if wechat_task_id else None
wechat_result = get_task_result(wechat_task_id) if wechat_task_id and active_wechat else {}

if active_wechat and active_wechat["progress"]["status"] in ("running", "pending"):
    p = active_wechat["progress"]
    st.markdown("---")
    st.subheader("🔄 微信号提取中（后台运行）")
    st.progress(min(p["value"], 1.0), text=p.get("text", ""))
    if st.button("🛑 停止提取", type="secondary"):
        stop_task(wechat_task_id)
        st.rerun()
    st.info("💡 提取正在后台运行，可切换到其他页面操作")
    if st.button("🔄 刷新状态"):
        st.rerun()

if active_wechat and active_wechat["progress"]["status"] in ("completed", "failed", "stopped"):
    p = active_wechat["progress"]
    scanned = wechat_result.get("total_scanned", 0)
    extracted = wechat_result.get("extracted", 0)
    if p["status"] == "completed":
        st.success(f"✅ 批量提取完成！扫描 {scanned} 条，新增 {extracted} 个微信号")
    elif p["status"] == "failed":
        st.error(f"❌ 提取失败: {p.get('text', '')}")
    else:
        st.warning(f"⏹️ 提取已停止（已提取 {extracted} 个）")
    if st.button("🔄 关闭"):
        st.session_state.review_wechat_task_id = None
        st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# 📥 导出模块（放在页面上面，审核列表之前）
# ═════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("📥 导出 Excel")

export_col1, export_col2, export_col3, export_col4 = st.columns(4)
with export_col1:
    export_tiers = st.multiselect(
        "导出分层（可多选，不选=全部）",
        ["A", "B", "C", "D"],
        default=[],
        key="export_tier_sel",
        placeholder="选择分层...",
    )
with export_col2:
    export_prov = st.text_input("省份筛选（可选）", key="export_prov")
with export_col3:
    export_kw = st.text_input("关键词筛选（可选）", key="export_kw")
with export_col4:
    export_wechat = st.selectbox("微信筛选", ["全部", "有微信", "无微信"], key="export_wechat_sel")

if st.button("📥 导出 Excel", key="export_btn", type="primary"):
    from src.exporter import Exporter
    exporter = Exporter(db)
    path = exporter.export(
        tier=export_tiers if export_tiers else None,
        province=export_prov or None,
        keyword=export_kw or None,
        has_wechat=export_wechat if export_wechat != "全部" else None,
    )
    if path:
        with open(path, "rb") as f:
            st.download_button("📥 下载 Excel", data=f, file_name=path.split("/")[-1],
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.success(f"✅ 导出完成: {path}")
    else:
        st.warning("没有数据可导出")


# ═════════════════════════════════════════════════════════════════════════════
# 🔍 筛选 + 审核列表
# ═════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("🔍 筛选与审核")

col1, col2, col3, col4 = st.columns(4)
with col1:
    filter_tiers = st.multiselect("分层", ["A", "B", "C", "D"],
                                  default=[], key="review_tier",
                                  placeholder="不选=全部")
with col2:
    filter_review = st.selectbox("审核状态", ["全部", "待审核", "已通过", "已排除", "稍后处理"],
                                 key="review_status")
with col3:
    filter_province = st.text_input("省份", key="review_prov")
with col4:
    filter_keyword = st.text_input("关键词", key="review_kw")

has_wechat_filter = st.selectbox("是否有微信", ["全部", "有微信", "无微信"], key="review_wechat")

# ── 获取所有 analyzed 候选，在内存中做完整筛选（避免 SQL 分页与内存筛选不一致） ──
all_candidates = db.get_candidates(
    status="analyzed",
    province=filter_province or None,
    keyword=filter_keyword or None,
)
cids = [c["id"] for c in all_candidates]
analyses = db.get_lead_analyses(cids)
analysis_map = {a["candidate_id"]: a for a in analyses}

# 内存筛选
review_map = {"全部": "", "待审核": "pending", "已通过": "approved", "已排除": "rejected", "稍后处理": "later"}
filter_review_val = review_map.get(filter_review, "")

filtered = []
for c in all_candidates:
    a = analysis_map.get(c["id"])

    # 分层筛选（多选）
    t = a.get("tier", "") if a else ""
    if filter_tiers and t not in filter_tiers:
        continue

    # 审核状态筛选
    if filter_review_val and c.get("manual_review_status") != filter_review_val:
        continue

    # 微信筛选
    if has_wechat_filter == "有微信" and not c.get("wechat_id"):
        continue
    if has_wechat_filter == "无微信" and c.get("wechat_id"):
        continue

    filtered.append(c)

# ── 分页 ──
total_filtered = len(filtered)
total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)
page = min(st.session_state.review_page, total_pages - 1)
page_start = page * PAGE_SIZE
page_end = page_start + PAGE_SIZE
page_candidates = filtered[page_start:page_end]

# ── 筛选结果统计 ──
filter_tags = []
if filter_tiers: filter_tags.append(f"分层∈{filter_tiers}")
if filter_review_val: filter_tags.append(f"审核={filter_review}")
if filter_province: filter_tags.append(f"省份={filter_province}")
if filter_keyword: filter_tags.append(f"关键词~{filter_keyword}")
if has_wechat_filter == "有微信": filter_tags.append("有微信")
elif has_wechat_filter == "无微信": filter_tags.append("无微信")

col_cnt, col_info = st.columns([1, 3])
with col_cnt:
    st.metric("📊 筛选结果", total_filtered, help="当前筛选条件下的总记录数")
with col_info:
    if filter_tags:
        st.caption("筛选条件: " + " | ".join(filter_tags))
    else:
        st.caption("无筛选条件，显示全部已分析候选")

st.caption(f"第 {page+1}/{total_pages} 页（每页 {PAGE_SIZE} 条）")

# ── 批量操作 ──
col_batch_wechat, _ = st.columns([1, 3])
with col_batch_wechat:
    if st.button("📱 批量提取微信号（后台运行）", type="secondary", use_container_width=True):
        task_id = start_task(name="批量提取微信号", target=run_wechat_extraction_task)
        st.session_state.review_wechat_task_id = task_id
        st.success(f"✅ 微信号提取任务已启动，ID: {task_id}")
        st.rerun()

st.markdown("---")

# ── 分页导航 ──
nav_cols = st.columns(5)
with nav_cols[0]:
    if st.button("⏮ 首页", disabled=(page == 0), key="rev_first"):
        st.session_state.review_page = 0
        st.rerun()
with nav_cols[1]:
    if st.button("◀ 上一页", disabled=(page == 0), key="rev_prev"):
        st.session_state.review_page = page - 1
        st.rerun()
with nav_cols[2]:
    st.write(f"　第 {page+1}/{total_pages} 页　")
with nav_cols[3]:
    if st.button("下一页 ▶", disabled=(page >= total_pages - 1), key="rev_next"):
        st.session_state.review_page = page + 1
        st.rerun()
with nav_cols[4]:
    if st.button("末页 ⏭", disabled=(page >= total_pages - 1), key="rev_last"):
        st.session_state.review_page = total_pages - 1
        st.rerun()

# ── 审核列表 ──
for c in page_candidates:
    a = analysis_map.get(c["id"])

    with st.container():
        st.markdown("---")
        cols = st.columns([2, 2, 2, 2])
        with cols[0]:
            st.write(f"**{c.get('nickname', '?')}**")
            st.write(f"抖音号: {c.get('douyin_id', '-')}")
            wechat = c.get("wechat_id", "") or ""
            if wechat:
                st.success(f"📱 微信: {wechat}")
            else:
                st.write("微信: 未提取到")
        with cols[1]:
            tier_val = a.get("tier", "?") if a else "?"
            st.write(f"🏷️ 分层: **{tier_val}** ({get_tier_description(tier_val)})")
            st.write(f"规则分: {a.get('rule_score', '?') if a else '?'} | "
                     f"AI分: {a.get('zhipu_score', '-') if a and a.get('zhipu_score') else '-'} | "
                     f"最终分: {a.get('final_score', '?') if a else '?'}")
        with cols[2]:
            st.write(f"📍 {c.get('source_province', '')} {c.get('source_city', '')} {c.get('source_county', '')}")
            st.write(f"🔑 {c.get('source_keywords', '')}")
        with cols[3]:
            if c.get("profile_url"):
                st.markdown(f"[🔗 抖音主页]({c['profile_url']})")
            review_status_label = {"pending": "待审核", "approved": "已通过", "rejected": "已排除", "later": "稍后处理"}
            st.write(f"审核: {review_status_label.get(c.get('manual_review_status', ''), '')}")

        # 审核操作按钮
        bt_cols = st.columns(8)
        old_tier = a.get("tier", "?") if a else "?"
        cid = c["id"]
        note_key = f"note_{cid}"

        with bt_cols[0]:
            if st.button("✅ 通过", key=f"app_{cid}"):
                db.update_candidate(cid, manual_review_status="approved")
                db.add_review_log(cid, old_tier, old_tier, "approved", "人工通过")
                st.rerun()
        with bt_cols[1]:
            if st.button("❌ 排除", key=f"rej_{cid}"):
                db.update_candidate(cid, manual_review_status="rejected")
                db.add_review_log(cid, old_tier, old_tier, "rejected", "人工排除")
                st.rerun()
        with bt_cols[2]:
            if st.button("⏰ 稍后", key=f"lat_{cid}"):
                db.update_candidate(cid, manual_review_status="later")
                st.rerun()
        with bt_cols[3]:
            if st.button("🅰 A", key=f"toa_{cid}"):
                _update_tier(db, cid, old_tier, "A", st.session_state.get(note_key, ""))
                st.rerun()
        with bt_cols[4]:
            if st.button("🅱 B", key=f"tob_{cid}"):
                _update_tier(db, cid, old_tier, "B", st.session_state.get(note_key, ""))
                st.rerun()
        with bt_cols[5]:
            if st.button("© C", key=f"toc_{cid}"):
                _update_tier(db, cid, old_tier, "C", st.session_state.get(note_key, ""))
                st.rerun()
        with bt_cols[6]:
            if st.button("🅳 D", key=f"tod_{cid}"):
                _update_tier(db, cid, old_tier, "D", st.session_state.get(note_key, ""))
                st.rerun()
        with bt_cols[7]:
            st.text_input("备注", key=note_key, label_visibility="collapsed", placeholder="备注")

        # 详情
        with st.expander(f"📋 查看 #{cid} 详情"):
            st.write(f"**搜索卡片文本**: {(c.get('search_card_text', '') or '')[:500]}")
            st.write(f"**主页简介**: {(c.get('profile_bio', '') or '')[:300]}")
            st.write(f"**主页文本**: {(c.get('profile_text', '') or '')[:500]}")
            if a:
                st.write(f"**各维度分**: 地区={a.get('region_score', 0)} 行业={a.get('industry_score', 0)} "
                         f"年龄={a.get('age_group_score', 0)} 品类={a.get('category_score', 0)} "
                         f"实体={a.get('store_score', 0)} 可信={a.get('credibility_score', 0)}")
                ev = a.get("evidence", "")
                if ev:
                    st.write(f"**判断理由**: {ev[:500]}")
                neg = a.get("negative_evidence", "")
                if neg:
                    st.write(f"**负面理由**: {neg[:500]}")
                if a.get("zhipu_json"):
                    st.write(f"**AI 原始**: {a['zhipu_json'][:500]}")

# ── 最近审核日志 ──
st.markdown("---")
st.subheader("📋 最近审核日志")
logs = db.get_review_logs()
if logs:
    decision_map = {"approved": "通过", "rejected": "排除", "manual_tier_change": "人工改级", "pending": "待审"}
    for log in logs[:10]:
        decision = decision_map.get(log.get("human_decision", ""), log.get("human_decision", ""))
        st.write(f"#{log['candidate_id']}: {log.get('old_tier', '?')} → {log.get('new_tier', '?')} "
                 f"| {decision} | {log.get('reviewed_at', '')}")
