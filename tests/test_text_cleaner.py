"""测试文本清洗模块。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analysis.text_cleaner import clean_text, merge_ocr_texts, extract_chinese_keywords


def test_clean_whitespace():
    """清理多余空格和换行。"""
    assert clean_text("  hello  世界  ") == "hello 世界"
    assert clean_text("a\n\nb\nc") == "a b c"


def test_clean_special_chars():
    """清理特殊字符，保留中英文。"""
    result = clean_text("hello!@#$世界。，")
    assert "世界" in result
    assert "hello" in result


def test_merge_ocr_texts():
    """合并 OCR 文本并去重。"""
    result = merge_ocr_texts(["hello world", "hello world", "test"])
    assert "hello world" in result
    assert "test" in result
    # 去重后应该只有两段
    assert result.count("hello world") == 1


def test_extract_chinese_keywords():
    """提取中文关键词。"""
    words = extract_chinese_keywords("hello你好世界中国", min_len=2)
    # 连续中文被作为一个整体提取
    assert len(words) > 0
    # 全是连续中文不分词
    words2 = extract_chinese_keywords("女装 服装 打底衫 妈妈装", min_len=2)
    assert len(words2) > 0
    # 空文本返回空列表
    assert extract_chinese_keywords("") == []
    assert extract_chinese_keywords("abc") == []
