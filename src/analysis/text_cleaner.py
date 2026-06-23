"""文本清洗工具。"""

import re


def clean_text(text: str) -> str:
    """清理文本：去除多余空格、换行、特殊字符。"""
    if not text:
        return ""
    # 合并连续空白
    text = re.sub(r'\s+', ' ', text)
    # 去除首尾空白
    text = text.strip()
    # 保留中英文、数字、常见标点
    text = re.sub(r'[^一-鿿　-〿a-zA-Z0-9，。、；：！？“”‘’（）【】《》+.\s/#@-]', '', text)
    return text


def merge_ocr_texts(texts: list[str]) -> str:
    """合并多段 OCR 文本，去重。"""
    seen = set()
    result = []
    for t in texts:
        cleaned = clean_text(t)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return "\n".join(result)


def extract_chinese_keywords(text: str, min_len: int = 2) -> list[str]:
    """提取文本中的中文词汇，用于匹配关键词。"""
    if not text:
        return []
    # 提取所有连续中文
    words = re.findall(r'[一-鿿]{' + str(min_len) + r',}', text)
    return words


def normalize_text(text: str) -> str:
    """标准化文本用于匹配。"""
    text = clean_text(text)
    return text.lower()
