"""用户采集 — 按关键词搜索发现候选用户。"""

import subprocess
import sys
from pathlib import Path
import threading
import time
import os
import json
import tempfile

import streamlit as st
import pandas as pd
from loguru import logger

from src.china_regions import CHINA_REGIONS, PROVINCES
from src.db import Database
from src.settings import Settings
from src.analysis.background_task_manager import (
    start_task, stop_task, get_task, get_task_result,
)


def _get_db() -> Database:
    return Database()


def _display_template(text: str) -> str:
    return text.replace("{county}", "【县城名】")


# ── 后台采集任务函数（子进程版，修复参数传递漏洞） ──
def _run_collection_task(
    *,
    task_id: str,
    progress: dict,
    stop_event: threading.Event,
    result_holder: dict,
    tasks: list,
):
    """后台采集任务：对每个任务启动子进程进行采集。"""
    from datetime import datetime
    project_root = Path(__file__).resolve().parent.parent.parent
    worker_script = str(project_root / "src" / "batch_worker.py")

    all_results = []
    logs = []
    total = len(tasks)

    for i, t in enumerate(tasks):
        if stop_event.is_set():
            logs.append(f"[{i+1}/{total}] ⏹️ 用户停止")
            break

        log_msg = f"[{i+1}/{total}] {t['province']} {t['county']} → {t['keyword']}"
        logs.append(log_msg)
        progress["text"] = log_msg
        progress["value"] = (i + 0.5) / total

        try:
            # ── 关键修复：batch_worker.py 接收的是 <task_json> <output_file>
            #    不是 --province=xxx 等 CLI 参数 ──
            task_json_str = json.dumps(t, ensure_ascii=False)
            # 使用固定路径而非 tempfile.mktemp() —— mktemp 不创建文件，
            # 子进程崩溃时输出文件不存在，导致 "子进程未生成输出文件"
            _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            _rand = os.urandom(4).hex()
            _kw_tag = t['keyword'].replace('/', '_')[-20:]
            output_file = str(project_root / "data" / "exports" / f"batch_{_ts}_{_rand}_{_kw_tag}.json")
            # 预创建文件（保证子进程崩溃时文件也存在）
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            Path(output_file).touch()
            _kw_safe = t['keyword'].replace('/', '_')[:30]
            _err_log = str(project_root / "data" / "logs" / f"batch_{_ts}_{_kw_safe}.log")
            proc = subprocess.Popen(
                [sys.executable, worker_script, task_json_str, output_file],
                stdout=subprocess.DEVNULL, stderr=open(_err_log, "w"),
                cwd=str(project_root),
            )

            # 等待子进程完成，期间检查停止信号 + 读取实时进度
            _timeout_sec = 600  # 每个子进程最多 10 分钟
            _start_wait = time.time()
            # 使用 .progress 后缀匹配 batch_worker.py 的 _set_progress_file 修正
            status_file = output_file + ".progress"
            while proc.poll() is None:
                if stop_event.is_set():
                    proc.kill()
                    logs.append(f"[{i+1}/{total}] ⏹️ 用户停止（等待子进程退出）")
                    break
                # 超时保护
                if time.time() - _start_wait > _timeout_sec:
                    proc.kill()
                    logs.append(f"[{i+1}/{total}] ⏰ 子进程超时（{_timeout_sec}秒），已强制终止")
                    break
                # 读取子进程实时进度
                if os.path.exists(status_file):
                    try:
                        with open(status_file, "r") as sf:
                            st = json.load(sf)
                        if st.get("detail"):
                            progress["text"] = f"[{i+1}/{total}] {st['detail']}"
                            if st.get("value"):
                                # 在当前任务区间内映射进度
                                task_start = (i + 0.0) / total
                                task_end = (i + 1.0) / total
                                progress["value"] = task_start + st["value"] * (task_end - task_start)
                    except Exception:
                        pass
                time.sleep(1)

            # 子进程结束，读取输出文件
            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                try:
                    with open(output_file, "r", encoding="utf-8") as f:
                        result_data = json.load(f)
                    try:
                        os.unlink(output_file)
                    except Exception:
                        pass

                    # batch_worker.py 输出格式: {"all_results": [result]}
                    all_results_list = result_data.get("all_results", [])
                    if all_results_list:
                        r = all_results_list[0]
                        t["candidates_found"] = r.get("candidates_found", 0) or r.get("candidates_added", 0)
                        t["candidates_added"] = r.get("candidates_added", 0)
                        t["scored_pending"] = r.get("scored_pending", 0)
                        t["status"] = r.get("status", "failed")
                        if r.get("errors"):
                            t["errors"] = "; ".join(r["errors"]) if isinstance(r["errors"], list) else r["errors"]
                    elif result_data.get("error"):
                        t["status"] = "failed"
                        t["errors"] = result_data["error"]

                except Exception as e:
                    t["status"] = "failed"
                    t["errors"] = f"解析输出文件失败: {e}"
            else:
                # 子进程崩溃，文件为空或不存在 — 读取 stderr 日志获取错误原因
                exit_code = proc.poll()
                t["status"] = "failed"
                if os.path.exists(_err_log) and os.path.getsize(_err_log) > 0:
                    try:
                        with open(_err_log, "r") as ef:
                            err_text = ef.read().strip()
                        t["errors"] = f"子进程退出码={exit_code}, 错误: {err_text[:300]}"
                    except Exception:
                        t["errors"] = f"子进程退出码={exit_code}, 输出文件为空"
                else:
                    t["errors"] = f"子进程退出码={exit_code}, stderr 无输出"

            # 检查是否风控暂停
            errors_str = str(t.get("errors", ""))
            if "paused_need_human" in errors_str:
                t["status"] = "paused_need_human"

            all_results.append(t)

            # ⛔ 风控触发 → 立即停止后续任务，避免频繁打开关闭浏览器
            if t.get("status") == "paused_need_human":
                logs.append(f"[{i+1}/{total}] ⛔ 触发风控，停止后续任务（请处理验证码后恢复采集）")
                progress["text"] = f"⛔ 风控触发，已停止（处理风控后可继续）"
                progress["value"] = (i + 1) / total
                break

        except Exception as e:
            logs.append(f"[{i+1}/{total}] ❌ 异常: {e}")
            t["status"] = "failed"
            t["errors"] = str(e)
            all_results.append(t)

        progress["value"] = (i + 1) / total

    # 汇总
    result_holder["all_results"] = all_results
    result_holder["logs"] = logs
    result_holder["total"] = total

    succeeded = sum(1 for r in all_results if r.get("status") == "completed")
    failed = sum(1 for r in all_results if r.get("status") == "failed")
    paused = sum(1 for r in all_results if r.get("status") == "paused_need_human")

    if paused > 0:
        progress["status"] = "completed"
        progress["text"] = f"完成（{succeeded}成功，{paused}风控暂停需处理）"
    elif failed > 0 and succeeded == 0:
        progress["status"] = "failed"
        progress["text"] = f"全部失败（{failed}个失败）"
    elif failed > 0 and succeeded > 0:
        progress["status"] = "completed"
        progress["text"] = f"部分完成（{succeeded}成功，{failed}失败）"
    else:
        progress["status"] = "completed"
        progress["text"] = f"采集完成（{succeeded}成功）"


# ═════════════════════════════════════════════════════════════════════════════
# 页面主体
# ═════════════════════════════════════════════════════════════════════════════

st.title("🎯 用户采集")
st.caption("选择地区 → 选择关键词模板 → 设置参数 → 启动后台采集")

# 确保 session_state
for key in ("collection_task_id", "selected_counties", "county_filter",
            "selected_templates"):
    if key not in st.session_state:
        if key == "collection_task_id":
            st.session_state[key] = None
        elif key == "county_filter":
            st.session_state[key] = "all"
        elif key == "selected_templates":
            st.session_state[key] = []
        else:
            st.session_state[key] = []

db = _get_db()

# ── 风控暂停处理区 ──
paused_tasks = db.get_keyword_tasks(status="paused_need_human")[:5]
if paused_tasks:
    st.error("🚨 **有任务因风控暂停 — 请处理验证码**")
    col_pz1, col_pz2, col_pz3 = st.columns([2, 1, 1])
    with col_pz1:
        if st.button("🌐 打开 Chromium（处理验证码）", use_container_width=True):
            from src.browser.browser_manager import safe_kill_chromium
            safe_kill_chromium(force=True)
            time.sleep(2)
            login_helper = str(Path(__file__).resolve().parent.parent.parent / "src" / "login_helper.py")
            subprocess.Popen(
                [sys.executable, login_helper],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            st.success("✅ Chromium 已启动，请扫码或处理验证码")
    with col_pz2:
        if st.button("🔄 清除风控并恢复", use_container_width=True):
            from src.risk_utils import clear_risk_sentinel
            clear_risk_sentinel()
            for ev in db.get_risk_events(limit=100, is_handled=0):
                db.mark_risk_event_handled(ev["id"], note="用户手动恢复")
            reset_count = db.reset_failed_tasks()
            st.success(f"✅ 已清除风控标记，重置 {reset_count} 个暂停任务")
            st.rerun()
    with col_pz3:
        st.caption("处理完验证码后，点击恢复按钮继续采集")
    st.markdown("---")


# ── 检查是否有正在运行/刚完成的采集任务 ──
current_task_id = st.session_state.get("collection_task_id")
active_task = get_task(current_task_id) if current_task_id else None
task_result = get_task_result(current_task_id) if current_task_id and active_task else {}


# =====================================================================
# 模式 A：有活跃任务 → 显示监控面板
# =====================================================================
if active_task and active_task["progress"]["status"] in ("running", "pending"):
    p = active_task["progress"]

    st.markdown("---")
    st.subheader("🔄 采集运行中（后台任务，可切换页面）")

    col_prog, col_stop = st.columns([4, 1])
    with col_prog:
        st.progress(min(p["value"], 1.0), text=p.get("text", ""))
    with col_stop:
        if st.button("🛑 停止采集", type="secondary", use_container_width=True):
            stop_task(current_task_id)
            st.rerun()

    logs = task_result.get("logs", [])
    if logs:
        with st.expander("📋 采集日志", expanded=True):
            st.text("\n".join(logs[-40:]))

    st.info("💡 正在后台运行中，可切换到其他页面操作，采集不会中断")
    if st.button("🔄 刷新状态"):
        st.rerun()
    st.stop()


# =====================================================================
# 模式 B：任务已完成/失败/风控暂停 → 显示结果摘要
# =====================================================================
if active_task and active_task["progress"]["status"] in ("completed", "failed", "stopped"):
    p = active_task["progress"]
    all_results = task_result.get("all_results", [])

    st.markdown("---")

    has_paused = any(r.get("status") == "paused_need_human" for r in all_results) if all_results else False

    if p["status"] == "completed":
        if has_paused:
            st.error("⛔ **采集被风控中断 — 请查看上方处理区操作**")
        else:
            st.success("✅ 采集完成！")
    elif p["status"] == "failed":
        st.error(f"❌ 采集失败: {p.get('text', '')}")
    else:
        st.warning("⏹️ 采集已停止")

    if all_results:
        total_found = sum(r.get("candidates_found", 0) for r in all_results)
        total_scored = sum(r.get("scored_pending", 0) for r in all_results)
        total_added = sum(r.get("candidates_added", 0) for r in all_results)
        succeeded = sum(1 for r in all_results if r["status"] == "completed")
        failed = sum(1 for r in all_results if r["status"] == "failed")
        paused = sum(1 for r in all_results if r["status"] == "paused_need_human")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("✅ 成功任务", succeeded)
        col2.metric("❌ 失败任务", failed)
        col3.metric("⛔ 风控暂停", paused)
        col4.metric("👤 搜索发现", total_found)

        col5, col6 = st.columns(2)
        col5.metric("📥 评分达标（待采集主页）", total_scored)
        col6.metric("📦 新增候选用户", total_added)

        if has_paused:
            st.error("⚠️ **部分任务因风控暂停 — 请处理验证码后点击恢复按钮继续**")

        st.info("💡 搜索采集完成！评分达标的用户已进入「待主页采集」池。请前往 **「主页采集」页面** 打开浏览器批量采集主页信息。")

        df = pd.DataFrame(all_results)
        display_map = {
            "province": "省份", "city": "城市", "county": "县城",
            "keyword": "关键词",
            "candidates_found": "搜索发现", "scored_pending": "评分达标",
            "status": "状态", "errors": "错误信息",
        }
        show_cols = [c for c in display_map if c in df.columns]
        df_display = df[show_cols].rename(columns=display_map)

        def _color_status(val):
            if val == "paused_need_human":
                return "color: red; font-weight: bold"
            if val == "failed":
                return "color: orange"
            if val == "completed":
                return "color: green"
            return ""
        st.dataframe(
            df_display.style.map(_color_status, subset=["状态"]),
            use_container_width=True, hide_index=True,
        )

    logs = task_result.get("logs", [])
    if logs:
        with st.expander("📋 完整采集日志"):
            st.text("\n".join(logs))

    # 底部操作按钮
    col_resume, col_new, col_close = st.columns(3)
    with col_resume:
        if has_paused:
            if st.button("🔄 恢复未完成任务", type="primary", use_container_width=True):
                from src.risk_utils import clear_risk_sentinel
                clear_risk_sentinel()
                for ev in db.get_risk_events(limit=100, is_handled=0):
                    db.mark_risk_event_handled(ev["id"], note="用户手动恢复")
                reset_count = db.reset_failed_tasks()
                st.session_state.collection_task_id = None
                st.success(f"✅ 已清除风控标记，重置 {reset_count} 个暂停任务，可以重新启动采集")
                st.rerun()
    with col_new:
        if st.button("🔄 开始新的采集", type="primary", use_container_width=True):
            st.session_state.collection_task_id = None
            st.rerun()
    with col_close:
        if st.button("关闭", use_container_width=True):
            st.session_state.collection_task_id = None
            st.rerun()
    st.stop()


# =====================================================================
# 模式 C：设置表单（默认）
# =====================================================================

# ── 步骤1: 选择省份和县城 ──
st.markdown("---")
st.subheader("📍 步骤1: 选择地区范围")

col1, col2 = st.columns([1, 3])

with col1:
    selected_provinces = st.multiselect(
        "选择省份（可多选）",
        options=PROVINCES,
        default=[],
        placeholder="点击选择省份...",
        key="provinces_select",
    )

# 采集状态标记：full(≥30) / partial(>0且<30) / 未录入(从未采或0候选)
county_status = db.get_county_collection_status()
full_set = county_status["full"]
partial_set = county_status["partial"]

# 根据选中的省份生成县城选项
all_counties = []
for prov in selected_provinces:
    if prov in CHINA_REGIONS:
        for city, counties in CHINA_REGIONS[prov]:
            for county in counties:
                all_counties.append((prov, city, county))

with col2:
    st.info(f"已选择 {len(selected_provinces)} 个省份，共 {len(all_counties)} 个县城")

# 县城选择
if all_counties:
    county_options = {}
    for prov, city, county in all_counties:
        label = f"{prov} > {city} > {county}"
        key = (prov, city, county)
        if key in full_set:
            status = "full"
            label += " ⏳ 已采集"
        elif key in partial_set:
            status = "partial"
            label += " 🔄 部分采集(<30)"
        else:
            status = ""
        county_options[label] = (prov, city, county, status)

    county_labels = list(county_options.keys())

    # 筛选按钮（4档）
    sc_filter = st.session_state.get("county_filter", "all")
    col_filter_all, col_filter_new, col_filter_partial, col_filter_done = st.columns(4)
    with col_filter_all:
        if st.button("📋 全部", use_container_width=True,
                     type="primary" if sc_filter == "all" else "secondary"):
            st.session_state.county_filter = "all"
            st.rerun()
    with col_filter_new:
        if st.button("🆕 未采集（含部分）", use_container_width=True,
                     type="primary" if sc_filter == "new" else "secondary"):
            st.session_state.county_filter = "new"
            st.rerun()
    with col_filter_partial:
        if st.button("🔄 部分采集", use_container_width=True,
                     type="primary" if sc_filter == "partial" else "secondary"):
            st.session_state.county_filter = "partial"
            st.rerun()
    with col_filter_done:
        if st.button("⏳ 已采集", use_container_width=True,
                     type="primary" if sc_filter == "collected" else "secondary"):
            st.session_state.county_filter = "collected"
            st.rerun()

    # 根据筛选过滤县城列表
    if sc_filter == "collected":
        filtered_labels = [l for l in county_labels if county_options[l][3] == "full"]
    elif sc_filter == "partial":
        filtered_labels = [l for l in county_labels if county_options[l][3] == "partial"]
    elif sc_filter == "new":
        filtered_labels = [l for l in county_labels if county_options[l][3] in ("", "partial")]
    else:
        filtered_labels = county_labels

    col_select_all, col_clear_all = st.columns([1, 1])
    with col_select_all:
        if st.button("✅ 全选当前筛选", use_container_width=True):
            st.session_state.selected_counties = filtered_labels[:]
            st.rerun()
    with col_clear_all:
        if st.button("🗑️ 清空选择", use_container_width=True):
            st.session_state.selected_counties = []
            st.rerun()

    # 确保已选县城在可见筛选范围内，不在则自动移除
    current_selection = st.session_state.get("selected_counties", [])
    cleaned_selection = [l for l in current_selection if l in filtered_labels]
    if len(cleaned_selection) != len(current_selection):
        st.session_state.selected_counties = cleaned_selection
        st.rerun()

    selected_labels = st.multiselect(
        "选择县城（可多选，🆕未采集=从未采集+部分采集）",
        options=filtered_labels,
        default=st.session_state.get("selected_counties", []),
        key="selected_counties",
        placeholder=f"共 {len(filtered_labels)} 个县城，点击选择...",
    )

    selected_counties_data = [county_options[lab] for lab in selected_labels]
    prev_full = sum(1 for _, _, _, s in selected_counties_data if s == "full")
    prev_partial = sum(1 for _, _, _, s in selected_counties_data if s == "partial")
    total_selected = len(selected_counties_data)

    parts = []
    if prev_partial > 0:
        parts.append(f"{prev_partial}个部分采集")
    if prev_full > 0:
        parts.append(f"{prev_full}个已采集")
    if parts:
        st.warning(f"⚠️ 已选 {total_selected} 个县城（含{'、'.join(parts)}），将会覆盖更新")
    else:
        st.info(f"已选择 {total_selected} 个县城")
else:
    selected_counties_data = []
    if selected_provinces:
        st.warning("所选省份暂无数据")
    else:
        st.info("请先选择省份")

# ── 步骤2: 选择关键词模板 ──
st.markdown("---")
st.subheader("📝 步骤2: 选择关键词模板")

all_templates = db.get_keyword_templates(is_active=1)

if not all_templates:
    st.warning("⚠️ 尚未导入关键词模板，请前往「关键词管理」导入或先在下方导入 YAML")
    with st.expander("📤 从 keyword_templates.yaml 导入"):
        yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "keyword_templates.yaml"
        if yaml_path.exists():
            import yaml
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            templates_data = data.get("templates", [])
            st.info(f"发现 {len(templates_data)} 个模板")
            if st.button("🚀 一键导入 YAML 关键词模板"):
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
                st.success(f"✅ 导入 {count} 个关键词模板，请刷新页面")
                st.rerun()
else:
    high_pri = [t for t in all_templates if t["priority"] == "high"]
    med_pri = [t for t in all_templates if t["priority"] == "medium"]
    low_pri = [t for t in all_templates if t["priority"] == "low"]

    st.caption(f"共 {len(all_templates)} 个启用模板 — 【县城名】= 自动替换为实际县城名称")

    col_sel_all, col_sel_none = st.columns([1, 1])
    with col_sel_all:
        if st.button("✅ 全选所有模板", use_container_width=True, key="sel_all_tpl"):
            st.session_state.selected_templates = [t["id"] for t in all_templates]
            for t in all_templates:
                st.session_state[f"tpl_{t['id']}"] = True
            st.rerun()
    with col_sel_none:
        if st.button("🗑️ 清空模板选择", use_container_width=True, key="sel_none_tpl"):
            st.session_state.selected_templates = []
            for t in all_templates:
                st.session_state[f"tpl_{t['id']}"] = False
            st.rerun()

    selected_template_ids = []
    default_selected = st.session_state.get("selected_templates", [t["id"] for t in high_pri])

    st.markdown("**🔴 高优先级**（核心品类/目标客群）")
    for t in high_pri:
        label = f"{_display_template(t['template_text'])}  🏷️ {t['category_tag']}  — {t.get('note','')}"
        checked = st.checkbox(label, value=t["id"] in default_selected, key=f"tpl_{t['id']}")
        if checked:
            selected_template_ids.append(t["id"])

    st.markdown("**🟡 中优先级**（扩展覆盖）")
    for t in med_pri:
        label = f"{_display_template(t['template_text'])}  🏷️ {t['category_tag']}  — {t.get('note','')}"
        checked = st.checkbox(label, value=t["id"] in default_selected, key=f"tpl_{t['id']}")
        if checked:
            selected_template_ids.append(t["id"])

    st.markdown("**⚪ 低优先级**（补充覆盖）")
    for t in low_pri:
        label = f"{_display_template(t['template_text'])}  🏷️ {t['category_tag']}  — {t.get('note','')}"
        checked = st.checkbox(label, value=t["id"] in default_selected, key=f"tpl_{t['id']}")
        if checked:
            selected_template_ids.append(t["id"])

    st.session_state.selected_templates = selected_template_ids

    # 预览
    if selected_counties_data and selected_template_ids:
        with st.expander("👁️ 预览展开后的搜索任务", expanded=False):
            preview_count = 0
            for prov, city, county, _ in selected_counties_data[:3]:
                for tid in selected_template_ids:
                    if preview_count >= 20:
                        break
                    tpl = next((t for t in all_templates if t["id"] == tid), None)
                    if not tpl:
                        continue
                    if tpl["keyword_type"] == "template":
                        full_keyword = tpl["template_text"].format(county=f"{city}{county}")
                    else:
                        full_keyword = tpl["template_text"]
                    preview_count += 1
                    st.text(f"  {prov} {county} → **{full_keyword}** ({tpl['category_tag']})")
            if preview_count == 0:
                st.info("无匹配项")
            else:
                total_tasks = len(selected_counties_data) * len(selected_template_ids)
                st.caption(f"共 {len(selected_counties_data)} 县城 × {len(selected_template_ids)} 模板 = **{total_tasks} 个搜索任务**（预览前 20 条）")

# ── 步骤3: 滚动和采集设定 ──
st.markdown("---")
st.subheader("⚙️ 步骤3: 采集设置")

col1, col2 = st.columns(2)

with col1:
    max_scroll = st.number_input(
        "每个关键词滚动次数",
        min_value=1, max_value=50, value=10,
        help="搜索结果页滚动多少次来发现用户",
        key="collect_scroll",
    )

with col2:
    max_users = st.number_input(
        "每个关键词最多采集用户数",
        min_value=1, max_value=100, value=20,
        help="每个关键词最多浏览并入库多少个用户",
        key="collect_max_users",
    )

# ── 步骤4: 启动采集（后台任务） ──
st.markdown("---")
st.subheader("▶️ 步骤4: 启动后台采集")

can_start = (
    len(selected_counties_data) > 0
    and len(st.session_state.get("selected_templates", [])) > 0
)

st.info(
    "💡 采集将在后台运行，启动后可以切换到其他页面操作，采集不会中断。\n\n"
    "侧边栏「后台任务」区域显示运行状态。\n\n"
    "⚠️ 如触发验证码/风控，系统会自动暂停并显示处理指引。"
)

if st.button("🚀 开始后台采集", type="primary", disabled=not can_start, use_container_width=True):
    selected_template_ids = st.session_state.selected_templates
    selected_tpl_objs = [t for t in all_templates if t["id"] in selected_template_ids]

    tasks = []
    for prov, city, county, _ in selected_counties_data:
        for tpl in selected_tpl_objs:
            if tpl["keyword_type"] == "template":
                full_keyword = tpl["template_text"].format(county=f"{city}{county}")
            else:
                full_keyword = tpl["template_text"]
            tasks.append({
                "province": prov,
                "city": city,
                "county": county,
                "keyword": full_keyword,
                "category_tag": tpl.get("category_tag", ""),
                "template_text": tpl["template_text"],
                "max_scroll": int(max_scroll),
                "max_users": int(max_users),
            })

    total_tasks = len(tasks)
    st.info(f"⏳ 即将启动: {len(selected_counties_data)} 县城 × {len(selected_tpl_objs)} 模板 = {total_tasks} 次搜索")

    task_id = start_task(
        name=f"采集 {len(selected_counties_data)}县城×{len(selected_tpl_objs)}模板",
        target=_run_collection_task,
        tasks=tasks,
    )
    st.session_state.collection_task_id = task_id
    st.success(f"✅ 后台采集任务已启动（ID: {task_id}），可切换到其他页面查看")
    time.sleep(0.5)
    st.rerun()

# ── 采集历史展示 ──
st.markdown("---")
st.subheader("📋 采集历史记录")

history = db.get_collection_history(limit=100)
if history:
    df_history = pd.DataFrame(history)
    display_map = {
        "province": "省份", "city": "城市", "county": "县城",
        "keyword": "关键词", "max_scroll": "滚动次数",
        "candidates_found": "发现用户", "candidates_matched": "匹配用户",
        "status": "状态", "created_at": "采集时间",
    }
    show_cols = [c for c in display_map if c in df_history.columns]
    df_display = df_history[show_cols].rename(columns=display_map)
    st.dataframe(df_display, use_container_width=True, hide_index=True)
else:
    st.info("暂无采集历史记录")

# ── 浏览器管理 ──
st.markdown("---")
st.subheader("🌐 浏览器管理")

col1, col2, col3 = st.columns([1, 1, 2])
with col1:
    if st.button("🛑 关闭所有 Chromium 进程", use_container_width=True):
        from src.browser.browser_manager import safe_kill_chromium
        safe_kill_chromium(force=True)
        time.sleep(2)
        st.success("所有 Chromium 进程已清理")
        st.info("💡 现在可以点击「开始后台采集」，子进程会自动打开 Chromium 窗口")

with col2:
    if st.button("🌐 打开独立 Chromium（用于登录）", use_container_width=True):
        st.info("⏳ 正在打开独立 Chromium 窗口...")
        from src.browser.browser_manager import safe_kill_chromium
        safe_kill_chromium(force=True)
        time.sleep(2)
        project_root = Path(__file__).resolve().parent.parent.parent
        login_helper = str(project_root / "src" / "login_helper.py")
        subprocess.Popen(
            [sys.executable, login_helper],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(project_root),
        )
        st.success("✅ Chromium 已启动，请扫码登录抖音后关闭窗口")
        st.info("💡 登录态会自动保存，下次采集可直接复用")

with col3:
    st.caption("登录态保存在 data/browser_profile/，采集子进程自动加载。每次采集前会清理锁文件。")

# ── 最近错误 ──
st.markdown("---")
st.subheader("⚠️ 最近错误 / 待处理")

recent_errors = db.get_recent_errors(limit=10)

if recent_errors:
    for err in recent_errors:
        with st.expander(f"任务 #{err['id']}: {err.get('keyword', '?')} - {err['status']}"):
            st.write(f"错误信息: {err.get('error_message', '无')}")
            st.write(f"时间: {err.get('finished_at', '')}")
else:
    st.info("暂无错误记录")

# ── 已选择县城管理 ──
st.markdown("---")
st.subheader("🗑️ 已选择县城管理")
st.caption("删除已采集过的县城记录后，可在上方重新选择该地区进行采集（数据来自仪表盘共享）")

collected_counties = db.get_all_collected_counties_detail()

if not collected_counties:
    st.info("暂无已采集的县城记录")
else:
    tab1, tab2 = st.tabs(["📋 按县城删除", "⚡ 批量操作"])

    with tab1:
        st.caption(f"共 {len(collected_counties)} 个已采集县城 — 点击删除可移除记录")

        for county_info in collected_counties:
            prov = county_info["province"]
            city = county_info["city"]
            county = county_info["county"]
            task_count = county_info["task_count"]
            last_time = county_info.get("last_collected_at", "未知")

            cols = st.columns([3, 1, 1, 1])
            with cols[0]:
                st.write(f"**{prov} > {city} > {county}**")
                st.caption(f"任务数: {task_count} | 最近采集: {last_time}")
            with cols[1]:
                if st.button("🗑️ 删除", key=f"del_county_{prov}_{city}_{county}"):
                    deleted = db.delete_collection_history_by_county(prov, city, county)
                    if deleted:
                        st.success(f"✅ 已删除 {deleted} 条记录")
                        st.rerun()
                    else:
                        st.warning("没有记录被删除")

    with tab2:
        county_count = len(collected_counties)
        total_tasks = sum(c["task_count"] for c in collected_counties)
        st.warning(f"⚠️ 共 {county_count} 个县城、{total_tasks} 条采集记录")
        if st.button("🗑️ 一键删除所有已采集县城记录", type="primary", use_container_width=True):
            deleted = db.delete_all_collection_history()
            if deleted:
                st.success(f"✅ 已清空所有采集历史（{deleted} 条记录）")
                st.rerun()
