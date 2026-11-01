"""Entry point: `python -m app.main` starts the Feishu bot."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .agent.core import ElectionAgent
from .agent.llm import LLM
from .bot.server import FeishuBot
from .config import Config
from .db.store import Store


def main() -> int:
    config = Config()
    log_file = config.db_path.parent / "bot.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5,
                                encoding="utf-8"),
        ],
    )
    missing = config.missing()
    if missing:
        for key in missing:
            print(f"missing env: {key}", file=sys.stderr)
        print("copy .env.example to .env and fill it in", file=sys.stderr)
        return 2

    store = Store(config.db_path)
    llm = LLM(config.llm_provider, config.llm_api_key(), config.llm_model)
    agent = ElectionAgent(llm, store, config)
    bot = FeishuBot(config, agent)
    bot.start()  # blocks on the WS loop
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
