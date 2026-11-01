"""Runtime configuration, all from environment variables / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass(frozen=True)
class Config:
    # Feishu
    feishu_app_id: str = os.environ.get("FEISHU_APP_ID", "")
    feishu_app_secret: str = os.environ.get("FEISHU_APP_SECRET", "")
    # comma-separated whitelist of chat_ids / open_ids; empty = allow all
    feishu_allow: frozenset = field(
        default_factory=lambda: frozenset(
            x.strip() for x in os.environ.get("FEISHU_ALLOW", "").split(",") if x.strip()
        )
    )

    # LLM — OpenAI-compatible providers
    llm_provider: str = os.environ.get("LLM_PROVIDER", "deepseek")
    llm_model: str = os.environ.get("LLM_MODEL", "")
    deepseek_api_key: str = os.environ.get("DEEPSEEK_API_KEY", "")
    dashscope_api_key: str = os.environ.get("DASHSCOPE_API_KEY", "")  # Qwen
    zhipu_api_key: str = os.environ.get("ZHIPU_API_KEY", "")          # GLM

    # Web retrieval
    search_provider: str = os.environ.get("SEARCH_PROVIDER", "auto")
    tavily_api_key: str = os.environ.get("TAVILY_API_KEY", "")
    # tw-news-monitor-simple 的 data/news.db；留空則只用即時檢索
    news_db_path: str = os.environ.get("NEWS_DB_PATH", "")

    # Storage
    db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("KB_DB_PATH", str(ROOT / "data" / "kb.sqlite3"))
        )
    )
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("CACHE_DIR", str(ROOT / "data" / "cache"))
        )
    )

    # Agent
    max_tool_rounds: int = int(os.environ.get("MAX_TOOL_ROUNDS", "30"))
    session_history_turns: int = int(os.environ.get("SESSION_HISTORY_TURNS", "20"))
    race_cycle: str = os.environ.get("RACE_CYCLE", "2026")

    def missing(self) -> list[str]:
        out = []
        if not self.feishu_app_id:
            out.append("FEISHU_APP_ID")
        if not self.feishu_app_secret:
            out.append("FEISHU_APP_SECRET")
        if not self.llm_api_key():
            out.append(f"{self.llm_provider.upper()}_API_KEY")
        return out

    def llm_api_key(self) -> str:
        return {
            "deepseek": self.deepseek_api_key,
            "qwen": self.dashscope_api_key,
            "glm": self.zhipu_api_key,
        }.get(self.llm_provider, "")
