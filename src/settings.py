"""应用配置管理。"""

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env():
    """加载 .env 文件。"""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


class Settings:
    """应用配置，从 app_config.yaml 加载。"""

    def __init__(self, config_path: str | Path | None = None):
        if config_path is None:
            config_path = PROJECT_ROOT / "config" / "app_config.yaml"
        self._config_path = Path(config_path)
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self):
        with open(self._config_path, encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

    # --- 抖音 ---
    @property
    def douyin_home_url(self) -> str:
        return self._data.get("douyin_home_url", "https://www.douyin.com")

    # --- 浏览器 ---
    @property
    def browser_headless(self) -> bool:
        return self._data.get("browser", {}).get("headless", False)

    @property
    def browser_user_data_dir(self) -> str:
        return str(PROJECT_ROOT / self._data.get("browser", {}).get("user_data_dir", "data/browser_profile"))

    @property
    def browser_slow_mo(self) -> int:
        return self._data.get("browser", {}).get("slow_mo", 200)

    @property
    def viewport_width(self) -> int:
        return self._data.get("browser", {}).get("viewport_width", 1440)

    @property
    def viewport_height(self) -> int:
        return self._data.get("browser", {}).get("viewport_height", 900)

    # --- 搜索 ---
    @property
    def max_scroll_per_keyword(self) -> int:
        return self._data.get("search", {}).get("max_scroll_per_keyword", 6)

    @property
    def stop_after_no_new_pages(self) -> int:
        return self._data.get("search", {}).get("stop_after_no_new_pages", 2)

    @property
    def max_candidates_per_keyword(self) -> int:
        return self._data.get("search", {}).get("max_candidates_per_keyword", 80)

    @property
    def wait_after_search_seconds(self) -> int:
        return self._data.get("search", {}).get("wait_after_search_seconds", 2)

    @property
    def wait_after_scroll_seconds(self) -> float:
        return self._data.get("search", {}).get("wait_after_scroll_seconds", 1.5)

    # --- 主页采集 ---
    @property
    def profile_open_in_new_tab(self) -> bool:
        return self._data.get("profile", {}).get("open_in_new_tab", True)

    @property
    def profile_save_html_snapshot(self) -> bool:
        return self._data.get("profile", {}).get("save_html_snapshot", True)

    @property
    def profile_save_screenshot(self) -> bool:
        return self._data.get("profile", {}).get("save_screenshot", True)

    @property
    def profile_use_ocr_fallback(self) -> bool:
        return self._data.get("profile", {}).get("use_ocr_fallback", True)

    # --- 采集预算 ---
    @property
    def max_keywords_per_run(self) -> int:
        return self._data.get("collection_budget", {}).get("max_keywords_per_run", 10)

    @property
    def max_search_scrolls_per_keyword(self) -> int:
        return self._data.get("collection_budget", {}).get("max_search_scrolls_per_keyword", 3)

    @property
    def max_new_candidates_per_keyword(self) -> int:
        return self._data.get("collection_budget", {}).get("max_new_candidates_per_keyword", 30)

    @property
    def max_profiles_per_run(self) -> int:
        return self._data.get("collection_budget", {}).get("max_profiles_per_run",
                   self._data.get("profile", {}).get("max_profiles_per_run", 50))

    @property
    def max_profile_failures_per_run(self) -> int:
        return self._data.get("collection_budget", {}).get("max_profile_failures_per_run", 5)

    @property
    def stop_on_first_risk_event(self) -> bool:
        return self._data.get("collection_budget", {}).get("stop_on_first_risk_event", True)

    @property
    def card_score_threshold(self) -> int:
        return self._data.get("profile", {}).get("card_score_threshold", 30)

    # --- OCR ---
    @property
    def ocr_enabled(self) -> bool:
        return self._data.get("ocr", {}).get("enabled", True)

    @property
    def ocr_provider(self) -> str:
        return self._data.get("ocr", {}).get("provider", "paddleocr")

    # --- 智谱 AI ---
    @property
    def zhipu_enabled(self) -> bool:
        return self._data.get("zhipu_ai", {}).get("enabled", True)

    @property
    def zhipu_api_key_env(self) -> str:
        return self._data.get("zhipu_ai", {}).get("api_key_env", "ZHIPU_API_KEY")

    @property
    def zhipu_base_url(self) -> str:
        return self._data.get("zhipu_ai", {}).get("base_url", "https://open.bigmodel.cn/api/paas/v4/")

    @property
    def zhipu_model(self) -> str:
        return self._data.get("zhipu_ai", {}).get("model", "glm-5.2")

    @property
    def zhipu_temperature(self) -> float:
        return self._data.get("zhipu_ai", {}).get("temperature", 0.1)

    @property
    def zhipu_max_tokens(self) -> int:
        return self._data.get("zhipu_ai", {}).get("max_tokens", 1200)

    @property
    def zhipu_call_when_rule_score_gte(self) -> int:
        return self._data.get("zhipu_ai", {}).get("call_ai_when_rule_score_gte", 60)

    def get(self, key: str, default: Any = None) -> Any:
        """获取任意配置项。"""
        keys = key.split(".")
        val = self._data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
        return val if val is not None else default
