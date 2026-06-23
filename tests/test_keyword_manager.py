"""测试关键词管理和任务生成逻辑。"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import Database


def _create_test_db():
    """创建临时测试数据库。"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
    tmp.close()
    db = Database(tmp.name)
    db.connect()
    db.init_db()
    return db, tmp.name


def _cleanup(db, db_path):
    db.close()
    os.unlink(db_path)


def test_template_replaces_county():
    """template 类型可以替换 {county}。"""
    db, path = _create_test_db()
    try:
        # 添加地区
        db.add_region("湖北省", "黄冈市", "黄梅县")
        # 添加模板
        db.add_keyword_template("{county}女装店", keyword_type="template")
        # 生成任务
        regions = db.get_active_regions()
        templates = db.get_active_templates()
        count = db.generate_tasks([r["id"] for r in regions], [t["id"] for t in templates])
        assert count > 0
        # 验证
        tasks = db.get_pending_keyword_tasks()
        assert any("黄梅县女装店" == t["keyword"] for t in tasks)
    finally:
        _cleanup(db, path)


def test_fixed_no_replace():
    """fixed 类型不替换。"""
    db, path = _create_test_db()
    try:
        db.add_region("湖北省", "黄冈市", "黄梅县")
        db.add_keyword_template("武汉妈妈装批发", keyword_type="fixed")
        regions = db.get_active_regions()
        templates = db.get_active_templates()
        count = db.generate_tasks([r["id"] for r in regions], [t["id"] for t in templates])
        assert count > 0
        tasks = db.get_pending_keyword_tasks()
        assert any("武汉妈妈装批发" == t["keyword"] for t in tasks)
        assert not any("黄梅县" in t["keyword"] and t["keyword_type"] == "fixed" for t in tasks)
    finally:
        _cleanup(db, path)


def test_inactive_template_not_generated():
    """停用关键词不生成任务。"""
    db, path = _create_test_db()
    try:
        db.add_region("湖北省", "黄冈市", "黄梅县")
        db.add_keyword_template("{county}女装店", keyword_type="template", is_active=0)
        regions = db.get_active_regions()
        templates = db.get_active_templates()
        assert len(templates) == 0  # 没有活跃模板
        count = db.generate_tasks([r["id"] for r in regions], [t["id"] for t in templates])
        assert count == 0
    finally:
        _cleanup(db, path)


def test_no_duplicate_tasks():
    """重复任务不会重复插入。"""
    db, path = _create_test_db()
    try:
        db.add_region("湖北省", "黄冈市", "黄梅县")
        db.add_keyword_template("{county}女装店", keyword_type="template")
        regions = db.get_active_regions()
        templates = db.get_active_templates()
        # 第一次生成
        count1 = db.generate_tasks([r["id"] for r in regions], [t["id"] for t in templates])
        # 第二次生成
        count2 = db.generate_tasks([r["id"] for r in regions], [t["id"] for t in templates])
        assert count1 > 0
        assert count2 == 0  # 没有新增
    finally:
        _cleanup(db, path)
