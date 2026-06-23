"""数据库管理模块。

使用 SQLite，提供建表和 CRUD 操作。
"""

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.settings import PROJECT_ROOT


def get_db_path() -> Path:
    """获取数据库文件路径。"""
    db_dir = PROJECT_ROOT / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "leads.sqlite"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    """数据库操作封装。"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else get_db_path()
        self.conn: sqlite3.Connection | None = None

    def connect(self):
        """建立数据库连接。"""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        # journal_mode 必须在设置 busy_timeout 之前执行
        self.conn.execute("PRAGMA journal_mode=WAL")
        # busy_timeout=30000 → 30 秒等待锁释放
        self.conn.execute("PRAGMA busy_timeout=30000")
        # WAL autocheckpoint 每 1000 页（约 4MB）自动 checkpoint，防止 WAL 无限增长
        self.conn.execute("PRAGMA wal_autocheckpoint=1000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        return self.conn

    def reconnect(self):
        """关闭旧连接并重新建立连接（解决 "database is locked" 后连接状态异常）。"""
        self.close()
        return self.connect()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def get_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            return self.connect()
        return self.conn

    # ------------------------------------------------------------------
    # 建表
    # ------------------------------------------------------------------
    def init_db(self):
        """创建所有表。"""
        conn = self.get_conn()
        cursor = conn.cursor()

        cursor.executescript("""
            -- 采集历史记录（替代任务生成 + 搜索采集的拆分逻辑）
            CREATE TABLE IF NOT EXISTS collection_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                province TEXT NOT NULL,
                city TEXT NOT NULL,
                county TEXT NOT NULL,
                keyword TEXT NOT NULL,
                max_scroll INTEGER DEFAULT 10,
                candidates_found INTEGER DEFAULT 0,
                candidates_matched INTEGER DEFAULT 0,
                search_task_id TEXT,
                status TEXT DEFAULT 'completed',
                error_message TEXT,
                started_at TEXT,
                finished_at TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS regions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                province TEXT NOT NULL,
                city TEXT NOT NULL,
                county TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                priority TEXT DEFAULT 'medium',
                note TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(province, city, county)
            );

            CREATE TABLE IF NOT EXISTS keyword_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_text TEXT NOT NULL,
                keyword_type TEXT DEFAULT 'template',
                category_tag TEXT,
                priority TEXT DEFAULT 'medium',
                is_active INTEGER DEFAULT 1,
                note TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS keyword_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region_id INTEGER,
                keyword_template_id INTEGER,
                province TEXT NOT NULL,
                city TEXT NOT NULL,
                county TEXT NOT NULL,
                keyword TEXT NOT NULL,
                keyword_type TEXT,
                category_tag TEXT,
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'pending',
                max_scroll INTEGER DEFAULT 6,
                found_count INTEGER DEFAULT 0,
                error_message TEXT,
                created_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                UNIQUE(province, city, county, keyword),
                FOREIGN KEY(region_id) REFERENCES regions(id),
                FOREIGN KEY(keyword_template_id) REFERENCES keyword_templates(id)
            );

            CREATE TABLE IF NOT EXISTS search_captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                keyword TEXT,
                page_url TEXT,
                screenshot_path TEXT,
                html_snapshot_path TEXT,
                ocr_text TEXT,
                dom_text TEXT,
                capture_index INTEGER,
                created_at TEXT,
                FOREIGN KEY(task_id) REFERENCES keyword_tasks(id)
            );

            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT DEFAULT 'douyin_web',

                nickname TEXT,
                douyin_id TEXT,
                profile_url TEXT,

                source_province TEXT,
                source_city TEXT,
                source_county TEXT,
                source_keywords TEXT,
                source_category_tags TEXT,

                search_card_text TEXT,
                search_page_url TEXT,
                search_screenshot_path TEXT,

                profile_bio TEXT,
                profile_text TEXT,
                profile_ocr_text TEXT,
                profile_screenshot_path TEXT,
                profile_html_snapshot_path TEXT,

                followers_text TEXT,
                following_text TEXT,
                likes_text TEXT,
                works_text TEXT,
                region_text TEXT,

                wechat_id TEXT DEFAULT '',
                wechat_extracted_at TEXT,

                dedupe_key TEXT,
                status TEXT DEFAULT 'new',
                manual_review_status TEXT DEFAULT 'pending',
                manual_note TEXT,

                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS profile_captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                profile_url TEXT,
                screenshot_path TEXT,
                html_snapshot_path TEXT,
                dom_text TEXT,
                ocr_text TEXT,
                created_at TEXT,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id)
            );

            CREATE TABLE IF NOT EXISTS lead_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,

                region_score INTEGER,
                industry_score INTEGER,
                age_group_score INTEGER,
                category_score INTEGER,
                store_score INTEGER,
                credibility_score INTEGER,

                rule_score INTEGER,
                zhipu_score INTEGER,
                final_score INTEGER,

                tier TEXT,
                is_target INTEGER,

                business_type TEXT,
                matched_categories TEXT,
                evidence TEXT,
                negative_evidence TEXT,
                recommended_action TEXT,
                zhipu_json TEXT,

                analyzed_at TEXT,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id)
            );

            CREATE TABLE IF NOT EXISTS review_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                old_tier TEXT,
                new_tier TEXT,
                human_decision TEXT,
                note TEXT,
                reviewed_at TEXT,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id)
            );

            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                page_url TEXT,
                keyword TEXT,
                screenshot_path TEXT,
                html_snapshot_path TEXT,
                page_text TEXT,
                action_when_triggered TEXT,
                is_handled INTEGER DEFAULT 0,
                note TEXT,
                created_at TEXT
            );
        """)

        # 兼容旧表：添加 wechat_id 列
        try:
            conn.execute("ALTER TABLE candidates ADD COLUMN wechat_id TEXT DEFAULT ''")
            conn.execute("ALTER TABLE candidates ADD COLUMN wechat_extracted_at TEXT")
            logger.info("已迁移 candidates 表: 添加 wechat_id/wechat_extracted_at 列")
        except Exception:
            pass

        # 新字段：profile_incomplete_reason
        try:
            conn.execute("ALTER TABLE candidates ADD COLUMN profile_incomplete_reason TEXT DEFAULT ''")
            logger.info("已迁移 candidates 表: 添加 profile_incomplete_reason 列")
        except Exception:
            pass

        # 新字段：card_score / card_evidence / card_negative_evidence
        try:
            conn.execute("ALTER TABLE candidates ADD COLUMN card_score INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE candidates ADD COLUMN card_evidence TEXT DEFAULT ''")
            conn.execute("ALTER TABLE candidates ADD COLUMN card_negative_evidence TEXT DEFAULT ''")
            logger.info("已迁移 candidates 表: 添加 card_score/card_evidence/card_negative_evidence 列")
        except Exception:
            pass

        # 兜底：risk_events 表（executescript 中已有 IF NOT EXISTS，
        # 但针对 executescript 执行过后才升级的情况单独确保）
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT, page_url TEXT, keyword TEXT,
                screenshot_path TEXT, html_snapshot_path TEXT,
                page_text TEXT, action_when_triggered TEXT,
                is_handled INTEGER DEFAULT 0, note TEXT, created_at TEXT
            )""")
            logger.info("已确保 risk_events 表存在")
        except Exception:
            pass

        # 索引：加速 douyin_id 去重查询
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_douyin_id "
                "ON candidates(douyin_id)"
            )
        except Exception:
            pass

        conn.commit()
        logger.info("数据库初始化完成: {}", self.db_path)
        return True

    # ------------------------------------------------------------------
    # regions
    # ------------------------------------------------------------------
    def get_regions(self, province: str | None = None,
                    city: str | None = None,
                    county: str | None = None,
                    is_active: int | None = None) -> list[dict]:
        conn = self.get_conn()
        sql = "SELECT * FROM regions WHERE 1=1"
        params: list[Any] = []
        if province:
            sql += " AND province LIKE ?"
            params.append(f"%{province}%")
        if city:
            sql += " AND city LIKE ?"
            params.append(f"%{city}%")
        if county:
            sql += " AND county LIKE ?"
            params.append(f"%{county}%")
        if is_active is not None:
            sql += " AND is_active = ?"
            params.append(is_active)
        sql += " ORDER BY province, city, county"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_active_regions(self) -> list[dict]:
        return self.get_regions(is_active=1)

    def get_region_by_id(self, region_id: int) -> dict | None:
        row = self.get_conn().execute(
            "SELECT * FROM regions WHERE id = ?", (region_id,)
        ).fetchone()
        return dict(row) if row else None

    def add_region(self, province: str, city: str, county: str,
                   priority: str = "medium", note: str = "",
                   is_active: int = 1) -> int | None:
        conn = self.get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO regions (province, city, county, priority, note, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (province, city, county, priority, note, is_active, _now(), _now()),
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            logger.warning("地区已存在: {}-{}-{}", province, city, county)
            return None

    def update_region(self, region_id: int, **kwargs) -> bool:
        allowed = {"province", "city", "county", "is_active", "priority", "note"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [region_id]
        self.get_conn().execute(f"UPDATE regions SET {sets} WHERE id = ?", vals)
        self.get_conn().commit()
        return True

    def delete_region(self, region_id: int) -> bool:
        self.get_conn().execute("DELETE FROM regions WHERE id = ?", (region_id,))
        self.get_conn().commit()
        return True

    def import_regions_from_csv(self, csv_path: str | Path) -> int:
        """从 CSV 导入地区，返回导入数量。"""
        import csv
        count = 0
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rid = self.add_region(
                    province=row.get("province", "").strip(),
                    city=row.get("city", "").strip(),
                    county=row.get("county", "").strip(),
                    priority=row.get("priority", "medium").strip(),
                    note=row.get("note", "").strip(),
                    is_active=int(row.get("is_active", "1")),
                )
                if rid:
                    count += 1
        return count

    def get_provinces(self) -> list[str]:
        rows = self.get_conn().execute(
            "SELECT DISTINCT province FROM regions ORDER BY province"
        ).fetchall()
        return [r["province"] for r in rows]

    def get_cities(self, province: str) -> list[str]:
        rows = self.get_conn().execute(
            "SELECT DISTINCT city FROM regions WHERE province = ? ORDER BY city",
            (province,),
        ).fetchall()
        return [r["city"] for r in rows]

    def get_counties(self, province: str, city: str) -> list[str]:
        rows = self.get_conn().execute(
            "SELECT DISTINCT county FROM regions WHERE province = ? AND city = ? ORDER BY county",
            (province, city),
        ).fetchall()
        return [r["county"] for r in rows]

    # ------------------------------------------------------------------
    # collection_history
    # ------------------------------------------------------------------
    def add_collection_history(self, province: str, city: str, county: str,
                                keyword: str, max_scroll: int = 10,
                                search_task_id: str = "",
                                status: str = "running") -> int:
        """添加采集历史记录。

        Args:
            status: 'running'=采集中, 'completed'=完成, 'risk_blocked'=风控阻断,
                    'no_data'=无结果（通常只写 candidates_found>0 的记录，
                    风控和无结果的记录不写，避免误标"已采集"县城）
        """
        now = _now()
        cursor = self.get_conn().execute(
            """INSERT INTO collection_history
               (province, city, county, keyword, max_scroll, search_task_id, status, started_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (province, city, county, keyword, max_scroll, search_task_id, status, now, now),
        )
        self.get_conn().commit()
        return cursor.lastrowid

    def finish_collection_history(self, history_id: int,
                                   candidates_found: int = 0,
                                   candidates_matched: int = 0,
                                   error_message: str = ""):
        updates = {
            "status": "failed" if error_message else "completed",
            "candidates_found": candidates_found,
            "candidates_matched": candidates_matched,
            "finished_at": _now(),
        }
        if error_message:
            updates["error_message"] = error_message[:500]
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [history_id]
        self.get_conn().execute(f"UPDATE collection_history SET {sets} WHERE id = ?", vals)
        self.get_conn().commit()

    def get_collection_history(self, province: str | None = None,
                                city: str | None = None,
                                county: str | None = None,
                                keyword: str | None = None,
                                limit: int = 50) -> list[dict]:
        conn = self.get_conn()
        sql = "SELECT * FROM collection_history WHERE 1=1"
        params: list[Any] = []
        if province:
            sql += " AND province = ?"
            params.append(province)
        if city:
            sql += " AND city = ?"
            params.append(city)
        if county:
            sql += " AND county = ?"
            params.append(county)
        if keyword:
            sql += " AND keyword LIKE ?"
            params.append(f"%{keyword}%")
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_county_collection_history(self, province: str, city: str,
                                       county: str) -> list[dict]:
        """获取某个县城的采集历史（用于提示之前是否采集过）。"""
        return self.get_collection_history(
            province=province, city=city, county=county, limit=10
        )

    def get_collected_counties_set(self) -> set[tuple[str, str, str]]:
        """获取所有已采集过的 (province, city, county) 集合。

        只返回确实发现候选用户（candidates_found > 0）的县城。
        风控阻断（risk_blocked）或无结果（done_no_result）的搜索
        不会写入 candidates_found > 0 的记录，因此这些县城不会被标记为"已采集"。
        这样在 Dashboard 上这些县城会显示为"未采集"，可以重新搜索。

        注意：search_flow 只会在 total_qualified > 0 时写入 collection_history，
        风控阻断返回 paused_need_human 时也可选写入 status='risk_blocked' 的审计记录，
        此查询的 status='completed' 条件将其排除，不影响已采集判断。
        """
        rows = self.get_conn().execute(
            "SELECT DISTINCT province, city, county FROM collection_history WHERE status = 'completed' AND candidates_found > 0"
        ).fetchall()
        return {(r["province"], r["city"], r["county"]) for r in rows}

    def get_county_collection_status(self) -> dict[str, set]:
        """获取县城采集状态，区分已采集完全和部分采集。

        规则：
        - full:      该县城所有关键词任务 candidates_found 总和 >= 30
        - partial:   candidates_found 总和 > 0 且 < 30
        - 未录入:     不在返回值中（从未采集或无候选用户）

        Returns:
            {"full": set of (province, city, county), "partial": set of ...}
        """
        rows = self.get_conn().execute(
            """SELECT province, city, county, SUM(candidates_found) as total_candidates
               FROM collection_history
               WHERE status = 'completed' AND candidates_found > 0
               GROUP BY province, city, county"""
        ).fetchall()
        full = set()
        partial = set()
        for r in rows:
            key = (r["province"], r["city"], r["county"])
            if r["total_candidates"] >= 30:
                full.add(key)
            else:
                partial.add(key)
        return {"full": full, "partial": partial}

    # ------------------------------------------------------------------
    # keyword_templates
    # ------------------------------------------------------------------
    def get_keyword_templates(self, is_active: int | None = None,
                              category_tag: str | None = None,
                              priority: str | None = None,
                              keyword_type: str | None = None,
                              search_text: str | None = None) -> list[dict]:
        conn = self.get_conn()
        sql = "SELECT * FROM keyword_templates WHERE 1=1"
        params: list[Any] = []
        if is_active is not None:
            sql += " AND is_active = ?"
            params.append(is_active)
        if category_tag:
            sql += " AND category_tag = ?"
            params.append(category_tag)
        if priority:
            sql += " AND priority = ?"
            params.append(priority)
        if keyword_type:
            sql += " AND keyword_type = ?"
            params.append(keyword_type)
        if search_text:
            sql += " AND template_text LIKE ?"
            params.append(f"%{search_text}%")
        sql += " ORDER BY priority, category_tag, id"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_active_templates(self) -> list[dict]:
        return self.get_keyword_templates(is_active=1)

    def add_keyword_template(self, template_text: str,
                             keyword_type: str = "template",
                             category_tag: str = "",
                             priority: str = "medium",
                             is_active: int = 1,
                             note: str = "") -> int:
        conn = self.get_conn()
        cursor = conn.execute(
            """INSERT INTO keyword_templates (template_text, keyword_type, category_tag, priority, is_active, note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (template_text, keyword_type, category_tag, priority, is_active, note, _now(), _now()),
        )
        conn.commit()
        return cursor.lastrowid

    def update_keyword_template(self, tid: int, **kwargs) -> bool:
        allowed = {"template_text", "keyword_type", "category_tag", "priority", "is_active", "note"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [tid]
        self.get_conn().execute(f"UPDATE keyword_templates SET {sets} WHERE id = ?", vals)
        self.get_conn().commit()
        return True

    def delete_keyword_template(self, tid: int) -> bool:
        self.get_conn().execute("DELETE FROM keyword_templates WHERE id = ?", (tid,))
        self.get_conn().commit()
        return True

    def get_category_tags(self) -> list[str]:
        rows = self.get_conn().execute(
            "SELECT DISTINCT category_tag FROM keyword_templates WHERE category_tag IS NOT NULL AND category_tag != '' ORDER BY category_tag"
        ).fetchall()
        return [r["category_tag"] for r in rows]

    # ------------------------------------------------------------------
    # keyword_tasks
    # ------------------------------------------------------------------
    def generate_tasks(self, region_ids: list[int],
                       template_ids: list[int]) -> int:
        """根据地区和模板生成任务。"""
        conn = self.get_conn()
        regions = []
        for rid in region_ids:
            r = self.get_region_by_id(rid)
            if r:
                regions.append(r)

        templates = []
        for tid in template_ids:
            t = self.get_keyword_template_by_id(tid)
            if t:
                templates.append(t)

        count = 0
        now = _now()
        for region in regions:
            for template in templates:
                if template["keyword_type"] == "template":
                    keyword = template["template_text"].format(
                        province=region["province"],
                        city=region["city"],
                        county=region["county"],
                    )
                else:
                    keyword = template["template_text"]

                try:
                    conn.execute(
                        """INSERT INTO keyword_tasks
                           (region_id, keyword_template_id, province, city, county,
                            keyword, keyword_type, category_tag, priority, status, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                        (region["id"], template["id"],
                         region["province"], region["city"], region["county"],
                         keyword, template["keyword_type"], template["category_tag"],
                         template["priority"], now),
                    )
                    count += 1
                except sqlite3.IntegrityError:
                    pass  # 已存在，跳过

        conn.commit()
        return count

    def get_keyword_template_by_id(self, tid: int) -> dict | None:
        row = self.get_conn().execute(
            "SELECT * FROM keyword_templates WHERE id = ?", (tid,)
        ).fetchone()
        return dict(row) if row else None

    def get_pending_keyword_tasks(self, limit: int = 5) -> list[dict]:
        rows = self.get_conn().execute(
            "SELECT * FROM keyword_tasks WHERE status = 'pending' ORDER BY priority, id LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_keyword_tasks(self, status: str | None = None,
                          province: str | None = None,
                          city: str | None = None,
                          county: str | None = None) -> list[dict]:
        conn = self.get_conn()
        sql = "SELECT * FROM keyword_tasks WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if province:
            sql += " AND province = ?"
            params.append(province)
        if city:
            sql += " AND city = ?"
            params.append(city)
        if county:
            sql += " AND county = ?"
            params.append(county)
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def update_task_status(self, task_id: int, status: str,
                           error_message: str | None = None,
                           found_count: int | None = None) -> bool:
        now = _now()
        updates = {"status": status}
        if error_message is not None:
            updates["error_message"] = error_message[:500]
        if found_count is not None:
            updates["found_count"] = found_count
        if status == "running" and not self._has_started(task_id):
            updates["started_at"] = now
        if status in ("done", "done_no_result", "failed", "paused_need_human"):
            updates["finished_at"] = now
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [task_id]
        self.get_conn().execute(f"UPDATE keyword_tasks SET {sets} WHERE id = ?", vals)
        self.get_conn().commit()
        return True

    def _has_started(self, task_id: int) -> bool:
        row = self.get_conn().execute(
            "SELECT started_at FROM keyword_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return bool(row and row["started_at"])

    def reset_task(self, task_id: int) -> bool:
        self.get_conn().execute(
            "UPDATE keyword_tasks SET status = 'pending', error_message = NULL WHERE id = ?",
            (task_id,),
        )
        self.get_conn().commit()
        return True

    def reset_failed_tasks(self) -> int:
        cursor = self.get_conn().execute(
            "UPDATE keyword_tasks SET status = 'pending', error_message = NULL WHERE status IN ('failed', 'paused_need_human')"
        )
        self.get_conn().commit()
        return cursor.rowcount

    def clear_pending_tasks(self) -> int:
        cursor = self.get_conn().execute(
            "DELETE FROM keyword_tasks WHERE status = 'pending'"
        )
        self.get_conn().commit()
        return cursor.rowcount

    def get_task_stats(self) -> dict:
        conn = self.get_conn()
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM keyword_tasks GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["cnt"] for r in rows}
        return stats

    # ------------------------------------------------------------------
    # candidates
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_douyin_url(url: str) -> str:
        """标准化抖音 URL 用于去重。

        统一为 https://www.douyin.com/user/<sec_uid> 格式，
        去掉 query params 和 trailing slash。
        """
        if not url:
            return ""
        # protocol-relative: //www.douyin.com/... → https://www.douyin.com/...
        if url.startswith("//"):
            url = "https:" + url
        # http → https
        if url.startswith("http://"):
            url = "https://" + url[7:]
        # 去掉 query params（? 后面的所有内容）
        q_idx = url.find("?")
        if q_idx >= 0:
            url = url[:q_idx]
        # 去掉尾部的 /
        url = url.rstrip("/")
        return url

    @staticmethod
    def _extract_sec_uid(url: str) -> str:
        """从抖音用户主页 URL 提取 sec_uid。"""
        if not url:
            return ""
        m = re.search(r"/user/([A-Za-z0-9_-]+)", url)
        return m.group(1) if m else ""

    def add_candidate(self, data: dict) -> int | None:
        """添加候选用户，三层去重：
        1. 标准化后的 profile URL（去 query params、统一 protocol）
        2. douyin_id
        3. sec_uid（URL 路径中的唯一标识）
        """
        conn = self.get_conn()
        raw_key = data.get("dedupe_key") or data.get("profile_url", "")
        normalized_key = self._normalize_douyin_url(raw_key)
        douyin_id = data.get("douyin_id", "")
        sec_uid = self._extract_sec_uid(normalized_key)

        existing = None

        # 去重 1: 标准化 URL（同时匹配旧格式带 params 和新格式已标准化）
        if normalized_key:
            existing = conn.execute(
                "SELECT id FROM candidates WHERE dedupe_key = ? OR dedupe_key LIKE ?",
                (normalized_key, f"{normalized_key}?%"),
            ).fetchone()

        # 去重 2: douyin_id（第二道防线）
        if not existing and douyin_id:
            existing = conn.execute(
                "SELECT id FROM candidates WHERE douyin_id = ? AND douyin_id != ''",
                (douyin_id,),
            ).fetchone()

        # 去重 3: sec_uid（第三道防线，通过 profile_url LIKE 匹配）
        if not existing and sec_uid:
            existing = conn.execute(
                "SELECT id FROM candidates WHERE profile_url LIKE ?",
                (f"%/user/{sec_uid}%",),
            ).fetchone()

        if existing:
            return None

        # 存储时使用标准化的 dedupe_key（不再带 query params）
        dedupe_key_to_store = normalized_key or raw_key

        now = _now()
        cursor = conn.execute(
            """INSERT INTO candidates
               (platform, nickname, douyin_id, profile_url,
                source_province, source_city, source_county,
                source_keywords, source_category_tags,
                search_card_text, search_page_url, search_screenshot_path,
                profile_bio, profile_text, profile_ocr_text,
                profile_screenshot_path, profile_html_snapshot_path,
                followers_text, following_text, likes_text, works_text, region_text,
                wechat_id, wechat_extracted_at, dedupe_key, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.get("platform", "douyin_web"),
             data.get("nickname", ""),
             data.get("douyin_id", ""),
             data.get("profile_url", ""),
             data.get("source_province", ""),
             data.get("source_city", ""),
             data.get("source_county", ""),
             data.get("source_keywords", ""),
             data.get("source_category_tags", ""),
             data.get("search_card_text", ""),
             data.get("search_page_url", ""),
             data.get("search_screenshot_path", ""),
             data.get("profile_bio", ""),
             data.get("profile_text", ""),
             data.get("profile_ocr_text", ""),
             data.get("profile_screenshot_path", ""),
             data.get("profile_html_snapshot_path", ""),
             data.get("followers_text", ""),
             data.get("following_text", ""),
             data.get("likes_text", ""),
             data.get("works_text", ""),
             data.get("region_text", ""),
             data.get("wechat_id", ""),
             data.get("wechat_extracted_at", ""),
             dedupe_key_to_store,
             data.get("status", "new"),
             now, now),
        )
        conn.commit()
        return cursor.lastrowid

    def update_candidate(self, candidate_id: int, **kwargs) -> bool:
        allowed = {
            "nickname", "douyin_id", "profile_url",
            "profile_bio", "profile_text", "profile_ocr_text",
            "profile_screenshot_path", "profile_html_snapshot_path",
            "followers_text", "following_text", "likes_text", "works_text",
            "region_text", "status", "manual_review_status", "manual_note",
            "wechat_id", "wechat_extracted_at",
            "profile_incomplete_reason",
            "card_score", "card_evidence", "card_negative_evidence",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [candidate_id]
        self.get_conn().execute(f"UPDATE candidates SET {sets} WHERE id = ?", vals)
        self.get_conn().commit()
        return True

    def get_candidates(self, status: str | None = None,
                       province: str | None = None,
                       city: str | None = None,
                       county: str | None = None,
                       has_profile_url: bool | None = None,
                       keyword: str | None = None,
                       limit: int | None = None,
                       offset: int | None = None) -> list[dict]:
        conn = self.get_conn()
        sql = "SELECT * FROM candidates WHERE 1=1"
        params: list[Any] = []
        if status:
            if status == "not_analyzed":
                sql += " AND status NOT IN ('analyzed', 'ignored', 'duplicate')"
            else:
                sql += " AND status = ?"
                params.append(status)
        if province:
            sql += " AND source_province = ?"
            params.append(province)
        if city:
            sql += " AND source_city = ?"
            params.append(city)
        if county:
            sql += " AND source_county = ?"
            params.append(county)
        if has_profile_url is True:
            sql += " AND profile_url IS NOT NULL AND profile_url != ''"
        elif has_profile_url is False:
            sql += " AND (profile_url IS NULL OR profile_url = '')"
        if keyword:
            sql += " AND source_keywords LIKE ?"
            params.append(f"%{keyword}%")
        sql += " ORDER BY id DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        if offset:
            sql += " OFFSET ?"
            params.append(offset)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_candidates(self, status: str | None = None,
                          province: str | None = None,
                          city: str | None = None,
                          county: str | None = None,
                          has_profile_url: bool | None = None,
                          keyword: str | None = None) -> int:
        conn = self.get_conn()
        sql = "SELECT COUNT(*) as c FROM candidates WHERE 1=1"
        params: list[Any] = []
        if status:
            if status == "not_analyzed":
                sql += " AND status NOT IN ('analyzed', 'ignored', 'duplicate')"
            else:
                sql += " AND status = ?"
                params.append(status)
        if province:
            sql += " AND source_province = ?"
            params.append(province)
        if city:
            sql += " AND source_city = ?"
            params.append(city)
        if county:
            sql += " AND source_county = ?"
            params.append(county)
        if has_profile_url is True:
            sql += " AND profile_url IS NOT NULL AND profile_url != ''"
        elif has_profile_url is False:
            sql += " AND (profile_url IS NULL OR profile_url = '')"
        if keyword:
            sql += " AND source_keywords LIKE ?"
            params.append(f"%{keyword}%")
        row = conn.execute(sql, params).fetchone()
        return row["c"] if row else 0

    def get_candidates_for_profile_collection(self, limit: int = 20) -> list[dict]:
        rows = self.get_conn().execute(
            """SELECT * FROM candidates
               WHERE profile_url IS NOT NULL AND profile_url != ''
               AND status = 'profile_pending'
               ORDER BY id
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_search_captured_candidates(self, limit: int = 50) -> list[dict]:
        """获取 search_captured 状态的候选（待卡片评分）。"""
        rows = self.get_conn().execute(
            """SELECT * FROM candidates
               WHERE status = 'search_captured'
               ORDER BY id
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_profile_pending(self) -> int:
        """统计当前 profile_pending 的候选数（用于预算控制）。"""
        row = self.get_conn().execute(
            "SELECT COUNT(*) as c FROM candidates WHERE status = 'profile_pending'"
        ).fetchone()
        return row["c"] if row else 0

    def count_profile_failures(self) -> int:
        """统计当前 run 中 profile_failed 的数量（用于预算控制）。"""
        row = self.get_conn().execute(
            "SELECT COUNT(*) as c FROM candidates WHERE status = 'profile_failed'"
        ).fetchone()
        return row["c"] if row else 0

    def get_candidate_by_id(self, cid: int) -> dict | None:
        row = self.get_conn().execute(
            "SELECT * FROM candidates WHERE id = ?", (cid,)
        ).fetchone()
        return dict(row) if row else None

    def get_candidates_for_scoring(self, scope: str = "captured",
                                   only_unanalyzed: bool = True) -> list[dict]:
        conn = self.get_conn()
        sql = "SELECT * FROM candidates WHERE 1=1"
        if scope == "not_captured":
            sql += (" AND status NOT IN ('analyzed', 'profile_captured', 'new', "
                    "'ignored', 'duplicate', 'profile_failed')")
        elif scope == "captured":
            # 已采集主页：包括所有 profile_captured 候选，
            # 即使已有 lead_analysis 记录（用户可能想重新评分）
            sql += " AND status IN ('profile_captured', 'new')"
        # scope == "all": no status filter
        if only_unanalyzed and scope != "captured":
            sql += (" AND id NOT IN (SELECT candidate_id FROM lead_analysis WHERE candidate_id IS NOT NULL)"
                    " AND status NOT IN ('ignored', 'duplicate', 'profile_failed')")
        elif scope == "captured":
            # captured 范围只排除已分析的非 captured 记录
            sql += " AND status NOT IN ('ignored', 'duplicate', 'profile_failed')"
        sql += " ORDER BY id"
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def get_candidate_counts(self) -> dict:
        conn = self.get_conn()
        total = conn.execute("SELECT COUNT(*) as c FROM candidates").fetchone()["c"]
        today = conn.execute(
            "SELECT COUNT(*) as c FROM candidates WHERE date(created_at) = date('now')"
        ).fetchone()["c"]
        return {"total": total, "today": today}

    def get_candidate_status_counts(self) -> dict:
        rows = self.get_conn().execute(
            "SELECT status, COUNT(*) as cnt FROM candidates GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ------------------------------------------------------------------
    # profile_captures
    # ------------------------------------------------------------------
    def add_profile_capture(self, candidate_id: int, profile_url: str,
                            screenshot_path: str = "",
                            html_snapshot_path: str = "",
                            dom_text: str = "",
                            ocr_text: str = "") -> int:
        cursor = self.get_conn().execute(
            """INSERT INTO profile_captures
               (candidate_id, profile_url, screenshot_path, html_snapshot_path, dom_text, ocr_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, profile_url, screenshot_path, html_snapshot_path, dom_text, ocr_text, _now()),
        )
        self.get_conn().commit()
        return cursor.lastrowid

    # ------------------------------------------------------------------
    # lead_analysis
    # ------------------------------------------------------------------
    def add_lead_analysis(self, data: dict) -> int:
        conn = self.get_conn()
        # 删除已有分析记录
        conn.execute("DELETE FROM lead_analysis WHERE candidate_id = ?", (data["candidate_id"],))
        cursor = conn.execute(
            """INSERT INTO lead_analysis
               (candidate_id, region_score, industry_score, age_group_score,
                category_score, store_score, credibility_score,
                rule_score, zhipu_score, final_score,
                tier, is_target, business_type, matched_categories,
                evidence, negative_evidence, recommended_action, zhipu_json, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["candidate_id"],
             data.get("region_score", 0),
             data.get("industry_score", 0),
             data.get("age_group_score", 0),
             data.get("category_score", 0),
             data.get("store_score", 0),
             data.get("credibility_score", 0),
             data.get("rule_score", 0),
             data.get("zhipu_score"),
             data.get("final_score", 0),
             data.get("tier", "D"),
             data.get("is_target"),
             data.get("business_type", ""),
             data.get("matched_categories", ""),
             data.get("evidence", ""),
             data.get("negative_evidence", ""),
             data.get("recommended_action", ""),
             data.get("zhipu_json", ""),
             _now()),
        )
        conn.commit()
        return cursor.lastrowid

    def get_lead_analyses(self, candidate_ids: list[int] | None = None) -> list[dict]:
        conn = self.get_conn()
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            rows = conn.execute(
                f"SELECT * FROM lead_analysis WHERE candidate_id IN ({placeholders}) ORDER BY id",
                candidate_ids,
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM lead_analysis ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_lead_analysis_by_candidate(self, candidate_id: int) -> dict | None:
        row = self.get_conn().execute(
            "SELECT * FROM lead_analysis WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_candidates_for_restratify(self, tiers: list[str] | None = None) -> list[dict]:
        """获取已有分层记录的候选，用于重新分层（覆盖已有记录）。

        Args:
            tiers: 可选，只重新分层指定层级，如 ["A", "B"]

        Returns:
            候选用户字典列表
        """
        conn = self.get_conn()
        sql = """SELECT c.* FROM candidates c
                 INNER JOIN lead_analysis la ON la.candidate_id = c.id
                 WHERE c.status NOT IN ('ignored', 'duplicate', 'profile_failed')"""
        params = []
        if tiers:
            placeholders = ",".join("?" for _ in tiers)
            sql += f" AND la.tier IN ({placeholders})"
            params.extend(tiers)
        sql += " ORDER BY c.id"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def update_lead_tier(self, candidate_id: int, new_tier: str) -> bool:
        """人工修改候选用户的线索分层。"""
        self.get_conn().execute(
            "UPDATE lead_analysis SET tier = ? WHERE candidate_id = ?",
            (new_tier, candidate_id),
        )
        self.get_conn().commit()
        return True

    def get_tier_counts(self) -> dict:
        rows = self.get_conn().execute(
            "SELECT tier, COUNT(*) as cnt FROM lead_analysis GROUP BY tier"
        ).fetchall()
        return {r["tier"]: r["cnt"] for r in rows}

    def get_lead_analyses_with_candidates(
        self, tier: str | None = None,
        province: str | None = None,
        keyword: str | None = None,
        limit: int = 30, offset: int = 0,
    ) -> list[dict]:
        """联表查询 lead_analysis + candidates，支持筛选分页。"""
        conn = self.get_conn()
        sql = """SELECT c.id, c.nickname, c.douyin_id, c.source_province, c.source_city,
                        c.source_county, c.source_keywords, c.profile_bio,
                        la.tier, la.rule_score, la.zhipu_score, la.final_score,
                        la.industry_score, la.age_group_score, la.category_score,
                        la.store_score, la.credibility_score, la.region_score,
                        la.evidence, la.negative_evidence, la.recommended_action
                 FROM lead_analysis la
                 JOIN candidates c ON c.id = la.candidate_id
                 WHERE 1=1"""
        params: list[Any] = []
        if tier and tier != "all":
            sql += " AND la.tier = ?"
            params.append(tier)
        if province:
            sql += " AND c.source_province = ?"
            params.append(province)
        if keyword:
            sql += " AND c.source_keywords LIKE ?"
            params.append(f"%{keyword}%")
        sql += " ORDER BY la.id DESC"
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_lead_analyses(
        self, tier: str | None = None,
        province: str | None = None,
        keyword: str | None = None,
    ) -> int:
        """统计分层结果总数。"""
        conn = self.get_conn()
        sql = """SELECT COUNT(*) as c
                 FROM lead_analysis la
                 JOIN candidates c ON c.id = la.candidate_id
                 WHERE 1=1"""
        params: list[Any] = []
        if tier and tier != "all":
            sql += " AND la.tier = ?"
            params.append(tier)
        if province:
            sql += " AND c.source_province = ?"
            params.append(province)
        if keyword:
            sql += " AND c.source_keywords LIKE ?"
            params.append(f"%{keyword}%")
        row = conn.execute(sql, params).fetchone()
        return row["c"] if row else 0

    def get_profile_captured_candidates(
        self, status: str | None = None,
        province: str | None = None,
        keyword: str | None = None,
        limit: int = 30, offset: int = 0,
    ) -> list[dict]:
        """获取主页采集状态的候选（含 profile 数据），支持筛选分页。"""
        conn = self.get_conn()
        sql = """SELECT * FROM candidates WHERE 1=1"""
        params: list[Any] = []
        if status and status != "all":
            if status == "profile_done":
                sql += " AND status IN ('profile_captured', 'analyzed', 'profile_failed')"
            else:
                sql += " AND status = ?"
                params.append(status)
        else:
            sql += " AND status IN ('profile_captured', 'profile_pending', 'profile_failed', 'analyzed')"
        if province:
            sql += " AND source_province = ?"
            params.append(province)
        if keyword:
            sql += " AND source_keywords LIKE ?"
            params.append(f"%{keyword}%")
        sql += " ORDER BY id DESC"
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_profile_captured(
        self, status: str | None = None,
        province: str | None = None,
        keyword: str | None = None,
    ) -> int:
        """统计主页采集候选总数。"""
        conn = self.get_conn()
        sql = "SELECT COUNT(*) as c FROM candidates WHERE 1=1"
        params: list[Any] = []
        if status and status != "all":
            if status == "profile_done":
                sql += " AND status IN ('profile_captured', 'analyzed', 'profile_failed')"
            else:
                sql += " AND status = ?"
                params.append(status)
        else:
            sql += " AND status IN ('profile_captured', 'profile_pending', 'profile_failed', 'analyzed')"
        if province:
            sql += " AND source_province = ?"
            params.append(province)
        if keyword:
            sql += " AND source_keywords LIKE ?"
            params.append(f"%{keyword}%")
        row = conn.execute(sql, params).fetchone()
        return row["c"] if row else 0

    # ------------------------------------------------------------------
    # review_logs
    # ------------------------------------------------------------------
    def add_review_log(self, candidate_id: int, old_tier: str,
                       new_tier: str, human_decision: str, note: str = "") -> int:
        cursor = self.get_conn().execute(
            """INSERT INTO review_logs (candidate_id, old_tier, new_tier, human_decision, note, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (candidate_id, old_tier, new_tier, human_decision, note, _now()),
        )
        self.get_conn().commit()
        return cursor.lastrowid

    def get_review_logs(self, candidate_id: int | None = None) -> list[dict]:
        conn = self.get_conn()
        if candidate_id:
            rows = conn.execute(
                "SELECT * FROM review_logs WHERE candidate_id = ? ORDER BY reviewed_at DESC",
                (candidate_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM review_logs ORDER BY reviewed_at DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # risk_events
    # ------------------------------------------------------------------
    def add_risk_event(self, event_type: str, page_url: str = "",
                       keyword: str = "", screenshot_path: str = "",
                       html_snapshot_path: str = "",
                       page_text: str = "",
                       action_when_triggered: str = "") -> int:
        cursor = self.get_conn().execute(
            """INSERT INTO risk_events
               (event_type, page_url, keyword, screenshot_path, html_snapshot_path,
                page_text, action_when_triggered, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_type[:100], page_url[:500], keyword[:200],
             screenshot_path, html_snapshot_path,
             page_text[:2000], action_when_triggered[:500], _now()),
        )
        self.get_conn().commit()
        return cursor.lastrowid

    def get_risk_events(self, limit: int = 50,
                        is_handled: int | None = None) -> list[dict]:
        conn = self.get_conn()
        sql = "SELECT * FROM risk_events WHERE 1=1"
        params = []
        if is_handled is not None:
            sql += " AND is_handled = ?"
            params.append(is_handled)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def mark_risk_event_handled(self, event_id: int, note: str = "") -> bool:
        self.get_conn().execute(
            "UPDATE risk_events SET is_handled = 1, note = ? WHERE id = ?",
            (note[:500], event_id),
        )
        self.get_conn().commit()
        return True

    def reset_task_from_risk(self, event_id: int) -> bool:
        """将风控事件关联的任务重置为 pending。"""
        conn = self.get_conn()
        # 尝试通过 keyword 查找对应任务
        event = conn.execute(
            "SELECT keyword FROM risk_events WHERE id = ?", (event_id,)
        ).fetchone()
        if event and event["keyword"]:
            conn.execute(
                "UPDATE keyword_tasks SET status = 'pending', error_message = NULL WHERE keyword = ? AND status = 'paused_need_human'",
                (event["keyword"],),
            )
            conn.commit()
            return True
        return False

    def pause_all_tasks(self) -> int:
        """暂停所有 running/pending 的任务。"""
        conn = self.get_conn()
        cursor = conn.execute(
            "UPDATE keyword_tasks SET status = 'paused_need_human', error_message = '已暂停：触发风控' WHERE status IN ('running', 'pending')"
        )
        conn.commit()
        return cursor.rowcount

    # ------------------------------------------------------------------
    # helper
    # ------------------------------------------------------------------
    def get_dashboard_stats(self) -> dict:
        conn = self.get_conn()
        region_count = conn.execute("SELECT COUNT(*) as c FROM regions").fetchone()["c"]
        template_count = conn.execute(
            "SELECT COUNT(*) as c FROM keyword_templates WHERE is_active = 1"
        ).fetchone()["c"]
        task_stats = self.get_task_stats()
        pending_tasks = task_stats.get("pending", 0)
        done_tasks = task_stats.get("done", 0) + task_stats.get("done_no_result", 0)
        cand_counts = self.get_candidate_counts()
        cand_status = self.get_candidate_status_counts()
        profile_captured = cand_status.get("profile_captured", 0)
        tier_counts = self.get_tier_counts()

        return {
            "region_count": region_count,
            "active_template_count": template_count,
            "pending_tasks": pending_tasks,
            "done_tasks": done_tasks,
            "total_candidates": cand_counts["total"],
            "today_candidates": cand_counts["today"],
            "profile_captured": profile_captured,
            "tier_counts": tier_counts,
        }

    # ------------------------------------------------------------------
    # collection_history delete (用于首页仪表盘管理已采集县城)
    # ------------------------------------------------------------------
    def delete_collection_history(self, history_id: int) -> bool:
        """按 ID 删除单条采集历史记录。"""
        self.get_conn().execute(
            "DELETE FROM collection_history WHERE id = ?", (history_id,)
        )
        self.get_conn().commit()
        return True

    def delete_collection_history_by_county(self, province: str, city: str, county: str) -> int:
        """删除某个县城的所有采集历史记录，返回删除条数。"""
        cursor = self.get_conn().execute(
            "DELETE FROM collection_history WHERE province = ? AND city = ? AND county = ?",
            (province, city, county),
        )
        self.get_conn().commit()
        return cursor.rowcount

    def delete_all_collection_history(self) -> int:
        """清空所有采集历史记录，返回删除条数。"""
        cursor = self.get_conn().execute("DELETE FROM collection_history")
        self.get_conn().commit()
        return cursor.rowcount

    def get_all_collected_counties_detail(self) -> list[dict]:
        """获取所有已采集县城详细信息（含最近采集时间）。

        修复：只返回有实际候选用户数据的县城，按最近采集时间倒序。
        """
        rows = self.get_conn().execute(
            """SELECT province, city, county, COUNT(*) as task_count,
                      MAX(finished_at) as last_collected_at
               FROM collection_history
               WHERE status = 'completed' AND candidates_found > 0
               GROUP BY province, city, county
               ORDER BY last_collected_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_errors(self, limit: int = 20) -> list[dict]:
        rows = self.get_conn().execute(
            """SELECT id, keyword, status, error_message, finished_at
               FROM keyword_tasks
               WHERE status IN ('failed', 'paused_need_human')
               ORDER BY finished_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_profile_failed_candidates(self, limit: int = 20) -> list[dict]:
        rows = self.get_conn().execute(
            "SELECT id, nickname, douyin_id, profile_url, status FROM candidates WHERE status = 'profile_failed' ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_noise_nickname_candidates(self, noise_keywords: list[str] | None = None,
                                      limit: int = 100) -> list[dict]:
        """查找 nickname 为噪音的记录（用于脏数据修复）。"""
        if not noise_keywords:
            noise_keywords = ["开启读屏标签", "读屏标签已关闭", "下载抖音精选", "抖音"]
        conditions = " OR ".join(f"nickname LIKE ?" for _ in noise_keywords)
        params = [f"%{kw}%" for kw in noise_keywords]
        rows = self.get_conn().execute(
            f"SELECT * FROM candidates WHERE ({conditions}) ORDER BY id LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_invalid_profile_candidates(self, limit: int = 100) -> list[dict]:
        """查找 profile_text 无效但仍为 profile_captured 的记录。"""
        rows = self.get_conn().execute(
            """SELECT * FROM candidates
               WHERE status IN ('profile_captured', 'new')
               AND (profile_text IS NULL OR profile_text = '' OR LENGTH(profile_text) < 80)
               ORDER BY id
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
