"""关键词管理页面。"""

import streamlit as st
import pandas as pd
import yaml

from src.db import Database


def _get_db() -> Database:
    return Database()


st.title("📝 关键词管理")
st.caption("管理搜索关键词模板，支持模板类型和固定类型")

db = _get_db()

# 筛选条件
with st.expander("🔍 筛选", expanded=False):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        search_text = st.text_input("关键词搜索", key="kw_search")
    with col2:
        cat_filter = st.selectbox("品类标签", [""] + db.get_category_tags(), key="kw_cat")
    with col3:
        pri_filter = st.selectbox("优先级", ["", "高", "中", "低"], key="kw_pri")
    with col4:
        active_filter = st.selectbox("状态", ["全部", "启用", "停用"], key="kw_active")

is_active = None
if active_filter == "启用":
    is_active = 1
elif active_filter == "停用":
    is_active = 0

templates = db.get_keyword_templates(
    is_active=is_active,
    category_tag=cat_filter or None,
    priority={"高": "high", "中": "medium", "低": "low"}.get(pri_filter) if pri_filter else None,
    search_text=search_text or None,
)

# 新增关键词
with st.expander("➕ 新增关键词模板", expanded=False):
    with st.form("add_kw_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            new_text = st.text_input("关键词模板 *", placeholder="{county}女装店 或 武汉妈妈装")
        with col2:
            new_type = st.selectbox("类型", ["自动展开", "固定文本"])
        with col3:
            new_priority = st.selectbox("优先级", ["高", "中", "低"])
        new_category = st.text_input("品类标签", placeholder="打底衫、妈妈装、女装实体店...")
        new_note = st.text_input("备注")
        submitted = st.form_submit_button("✅ 添加")
        if submitted:
            if new_text.strip():
                db.add_keyword_template(
                    template_text=new_text.strip(),
                    keyword_type={"自动展开": "template", "固定文本": "fixed"}.get(new_type, "template"),
                    category_tag=new_category.strip(),
                    priority={"高": "high", "中": "medium", "低": "low"}.get(new_priority, "medium"),
                    note=new_note.strip(),
                )
                st.success("✅ 关键词模板已添加")
                st.rerun()
            else:
                st.error("关键词模板为必填项")

# 批量导入
with st.expander("📤 导入/导出", expanded=False):
    col1, col2 = st.columns(2)
    with col1:
        uploaded_file = st.file_uploader("导入 keyword_templates.yaml", type="yaml", key="kw_yaml")
        if uploaded_file is not None:
            import tempfile
            data = yaml.safe_load(uploaded_file)
            templates_data = data.get("templates", [])
            count = 0
            for t in templates_data:
                db.add_keyword_template(
                    template_text=t["template_text"],
                    keyword_type=t.get("keyword_type", "template"),
                    category_tag=t.get("category_tag", ""),
                    priority=t.get("priority", "medium"),
                    is_active=1 if t.get("is_active", True) else 0,
                    note=t.get("note", ""),
                )
                count += 1
            st.success(f"✅ 导入 {count} 个关键词模板")
            st.rerun()

    with col2:
        if st.button("📥 导出当前关键词"):
            export_data = {"templates": []}
            for t in templates:
                export_data["templates"].append({
                    "template_text": t["template_text"],
                    "keyword_type": t["keyword_type"],
                    "category_tag": t["category_tag"],
                    "priority": t["priority"],
                    "is_active": bool(t["is_active"]),
                    "note": t.get("note", ""),
                })
            st.download_button(
                "下载 YAML",
                data=yaml.dump(export_data, allow_unicode=True, default_flow_style=False),
                file_name="keyword_templates.yaml",
                mime="text/yaml",
            )

# 预览关键词生成效果
st.markdown("---")
st.subheader("👁️ 预览关键词生成效果")

preview_regions = db.get_active_regions()
if preview_regions:
    selected_region = st.selectbox(
        "选择地区预览",
        preview_regions,
        format_func=lambda r: f"{r['province']}-{r['city']}-{r['county']}",
        key="kw_preview_region",
    )
    if selected_region:
        preview_results = []
        for t in templates:
            if not t["is_active"]:
                continue
            if t["keyword_type"] == "template":
                keyword = t["template_text"].format(
                    province=selected_region["province"],
                    city=selected_region["city"],
                    county=selected_region["county"],
                )
            else:
                keyword = t["template_text"]
            preview_results.append({
                "关键词": keyword,
                "类型": t["keyword_type"],
                "品类标签": t.get("category_tag", ""),
                "优先级": t.get("priority", "medium"),
            })
        if preview_results:
            st.dataframe(pd.DataFrame(preview_results[:20]), use_container_width=True, hide_index=True)
            st.caption(f"预览前 {min(20, len(preview_results))} 条，共 {len(preview_results)} 条")
        else:
            st.info("没有可预览的关键词")
else:
    st.info("请先在地区管理中添加地区")

# 关键词列表
st.markdown("---")
st.subheader(f"关键词模板列表 ({len(templates)} 条)")

if templates:
    df = pd.DataFrame(templates)
    df["is_active"] = df["is_active"].apply(lambda x: "✅ 启用" if x else "⛔ 停用")
    display_map = {
        "id": "编号", "template_text": "关键词模板", "keyword_type": "类型",
        "category_tag": "品类标签", "priority": "优先级", "is_active": "状态", "note": "备注",
    }
    show_cols = [c for c in display_map if c in df.columns]
    df_display = df[show_cols].rename(columns=display_map)
    st.dataframe(df_display, use_container_width=True, hide_index=True)

    # 操作
    st.markdown("#### 操作")
    for t in templates[:20]:
        with st.container():
            c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
            with c1:
                type_label = "自动展开" if t["keyword_type"] == "template" else "固定文本"
                st.write(f"{t['template_text'].replace('{county}','【县城名】')}（{type_label}）")
            with c2:
                pri_label = {"high": "高", "medium": "中", "low": "低"}.get(t.get("priority", ""), t.get("priority", ""))
                st.write(f"🏷️ {t.get('category_tag', '')} | {pri_label}")
            with c3:
                if st.button("停用" if t["is_active"] else "启用", key=f"kw_act_{t['id']}"):
                    db.update_keyword_template(t["id"], is_active=0 if t["is_active"] else 1)
                    st.rerun()
            with c4:
                if st.button("🗑️", key=f"kw_del_{t['id']}"):
                    db.delete_keyword_template(t["id"])
                    st.rerun()

            # 编辑
            if st.button(f"✏️ 编辑 #{t['id']}", key=f"kw_edit_btn_{t['id']}"):
                st.session_state[f"editing_kw_{t['id']}"] = True

            if st.session_state.get(f"editing_kw_{t['id']}"):
                with st.form(key=f"edit_kw_form_{t['id']}"):
                    e_text = st.text_input("模板文本", value=t["template_text"])
                    e_type = st.selectbox("类型", ["自动展开", "固定文本"],
                                           index=0 if t["keyword_type"] == "template" else 1)
                    e_cat = st.text_input("品类标签", value=t.get("category_tag", ""))
                    e_pri = st.selectbox("优先级", ["高", "中", "低"],
                                          index={"high": 0, "medium": 1, "low": 2}.get(t.get("priority", "medium"), 1))
                    e_note = st.text_input("备注", value=t.get("note", ""))
                    if st.form_submit_button("保存"):
                        db.update_keyword_template(t["id"],
                                                     template_text=e_text.strip(),
                                                     keyword_type={"自动展开": "template", "固定文本": "fixed"}.get(e_type, "template"),
                                                     category_tag=e_cat.strip(),
                                                     priority={"高": "high", "中": "medium", "低": "low"}.get(e_pri, "medium"),
                                                     note=e_note.strip())
                        st.session_state[f"editing_kw_{t['id']}"] = False
                        st.rerun()
else:
    st.info("暂无关键词模板，请先添加或导入")
