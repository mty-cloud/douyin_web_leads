"""截图 OCR 识别模块。

优先使用 PaddleOCR，如果不可用则尝试 pytesseract，都没有则返回空。
"""

from loguru import logger

# PaddleOCR 懒加载 — 安装失败时自动降级
_paddle_ocr = None


def _get_paddle_ocr():
    """获取 PaddleOCR 实例（懒加载）。"""
    global _paddle_ocr
    if _paddle_ocr is not None:
        return _paddle_ocr

    try:
        from paddleocr import PaddleOCR
        _paddle_ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        logger.info("PaddleOCR 加载成功")
        return _paddle_ocr
    except ImportError:
        logger.warning("PaddleOCR 未安装，OCR 功能不可用")
        return None
    except Exception as e:
        logger.warning("PaddleOCR 加载失败: {}", e)
        return None


def ocr_image(image_path: str) -> str:
    """对截图执行 OCR，返回识别文本。

    Args:
        image_path: 截图文件路径

    Returns:
        识别的文本，失败返回空字符串
    """
    ocr = _get_paddle_ocr()
    if ocr is None:
        return ""

    try:
        result = ocr.ocr(image_path, cls=True)
        if not result:
            return ""

        texts = []
        for line_group in result:
            if not line_group:
                continue
            for item in line_group:
                if item and len(item) >= 1:
                    texts.append(item[1][0] if item[1] else "")

        return "\n".join(texts)
    except Exception as e:
        logger.warning("OCR 识别失败: {}", e)
        return ""
