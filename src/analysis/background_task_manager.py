"""后台任务管理器 — 跨页面持久化执行长任务。

所有任务在 daemon 线程中运行，通过模块级注册表共享状态，
确保 Streamlit 页面切换时任务不受影响。
"""

import threading
import uuid
import time
from typing import Any, Callable, Optional

# ── 模块级任务注册表（跨 Streamlit 页面共享，页面切换不丢失） ──
_task_registry: dict[str, dict] = {}
_registry_lock = threading.Lock()


def start_task(name: str, target: Callable, **kwargs: Any) -> str:
    """启动后台任务。

    Args:
        name: 任务名称（显示用）
        target: 目标函数，接收 task_id, progress, stop_event 等关键字参数
        **kwargs: 传递给 target 的额外参数

    Returns:
        task_id: 任务 ID（用于后续查询/停止）
    """
    task_id = str(uuid.uuid4())[:8]
    stop_event = threading.Event()
    progress: dict[str, Any] = {"value": 0.0, "text": "", "status": "pending"}
    result_holder: dict[str, Any] = {}

    def wrapper() -> None:
        try:
            progress["status"] = "running"
            target(
                task_id=task_id,
                progress=progress,
                stop_event=stop_event,
                result_holder=result_holder,
                **kwargs,
            )
            if not stop_event.is_set() and progress["status"] == "running":
                progress["status"] = "completed"
            progress["value"] = 1.0
        except Exception as e:
            progress["status"] = "failed"
            progress["text"] = str(e)

    t = threading.Thread(target=wrapper, daemon=True)
    entry = {
        "thread": t,
        "progress": progress,
        "result_holder": result_holder,
        "name": name,
        "stop_event": stop_event,
        "started_at": time.time(),
    }

    with _registry_lock:
        _task_registry[task_id] = entry

    t.start()
    return task_id


def stop_task(task_id: str) -> bool:
    """请求停止任务。"""
    with _registry_lock:
        task = _task_registry.get(task_id)
    if task:
        task["stop_event"].set()
        return True
    return False


def get_task(task_id: str) -> Optional[dict]:
    """获取任务状态快照。"""
    with _registry_lock:
        task = _task_registry.get(task_id)
    if task:
        return {
            "task_id": task_id,
            "name": task["name"],
            "progress": dict(task["progress"]),
            "started_at": task.get("started_at"),
        }
    return None


def get_task_result(task_id: str) -> dict:
    """获取任务的结果数据。"""
    with _registry_lock:
        task = _task_registry.get(task_id)
    if task:
        return task.get("result_holder", {})
    return {}


def list_tasks() -> list[dict]:
    """列出所有后台任务。"""
    with _registry_lock:
        items = list(_task_registry.items())

    result = []
    for tid, t in items:
        result.append({
            "task_id": tid,
            "name": t["name"],
            "progress": dict(t["progress"]),
            "started_at": t.get("started_at"),
        })
    return result


def cleanup_completed(older_than_seconds: float = 300) -> int:
    """清理已完成/失败/停止的任务（超过指定时间）。

    Args:
        older_than_seconds: 超过此秒数的已完成任务会被清理

    Returns:
        清理的任务数量
    """
    now = time.time()
    to_delete: list[str] = []

    with _registry_lock:
        for tid, t in _task_registry.items():
            status = t["progress"]["status"]
            if status in ("completed", "failed", "stopped"):
                if now - t.get("started_at", 0) > older_than_seconds:
                    to_delete.append(tid)
        for tid in to_delete:
            del _task_registry[tid]

    return len(to_delete)


# ── 内置任务函数 ──

def run_scoring_task(
    *,
    task_id: str,
    progress: dict,
    stop_event: threading.Event,
    result_holder: dict,
    candidates: list[dict],
    settings: Any,
    with_ai: bool = True,
    update_db: bool = True,
) -> None:
    """运行分层评分的后台任务。"""
    from src.analysis.rule_scorer import RuleScorer
    from src.analysis.lead_tier import validate_and_assign_tier

    zhipu = None
    if with_ai:
        from src.analysis.zhipu_ai_analyzer import ZhipuAIAnalyzer
        zhipu = ZhipuAIAnalyzer(settings)

    scorer = RuleScorer()
    total = len(candidates)
    scored = 0

    for i, c in enumerate(candidates):
        if stop_event.is_set():
            progress["text"] = "任务已停止"
            progress["status"] = "stopped"
            return

        rule_result = scorer.score_lead(c)

        ai_result = None
        if with_ai and zhipu and zhipu.is_available():
            try:
                if rule_result["rule_score"] >= settings.zhipu_call_when_rule_score_gte:
                    ai_result = zhipu.analyze_lead(c)
            except Exception:
                pass

        result = validate_and_assign_tier(c, rule_result, ai_result)
        result["candidate_id"] = c["id"]

        if update_db:
            from src.db import Database
            db = Database()
            try:
                db.add_lead_analysis(result)
                db.update_candidate(c["id"], status="analyzed")
            finally:
                db.close()

        scored += 1
        progress["value"] = (i + 1) / total
        progress["text"] = f"[{i + 1}/{total}] #{c['id']} {c.get('nickname', '?')} → {result['tier']}"

    result_holder["total"] = total
    result_holder["scored"] = scored


def run_wechat_extraction_task(
    *,
    task_id: str,
    progress: dict,
    stop_event: threading.Event,
    result_holder: dict,
) -> None:
    """运行批量微信号提取的后台任务。"""
    from src.db import Database
    from src.wechat_extractor import extract_best
    from datetime import datetime

    db = Database()
    try:
        all_candidates = db.get_candidates(status="analyzed")
        target = [c for c in all_candidates if not c.get("wechat_id")]
        total = len(target)
        extracted = 0

        for i, c in enumerate(target):
            if stop_event.is_set():
                progress["status"] = "stopped"
                return

            combined = " ".join([
                c.get("profile_bio", "") or "",
                c.get("profile_text", "") or "",
                c.get("search_card_text", "") or "",
            ])
            wid = extract_best(combined)
            if wid:
                db.update_candidate(
                    c["id"],
                    wechat_id=wid,
                    wechat_extracted_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                extracted += 1

            progress["value"] = (i + 1) / total
            progress["text"] = f"[{i + 1}/{total}] 扫描中... 已提取 {extracted} 个"

        result_holder["total_scanned"] = total
        result_holder["extracted"] = extracted
    finally:
        db.close()
