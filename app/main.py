"""Entry point: `python -m app.main` starts the Feishu bot."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .agent.core import ElectionAgent
from .agent.llm import LLM
from lark_oapi.ws.client import loop as ws_loop

from .bot.health import Health, LarkConnWatcher, start_watchdog
from .bot.server import FeishuBot
from .config import Config
from .db.store import Store


def _disable_quick_edit() -> None:
    """Windows console QuickEdit: a mouse click in the window pauses every write
    to the console, which freezes logging and with it the whole bot."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # clear ENABLE_QUICK_EDIT_MODE (0x40), keep ENABLE_EXTENDED_FLAGS (0x80)
            kernel32.SetConsoleMode(handle, (mode.value & ~0x40) | 0x80)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    config = Config()
    log_file = config.db_path.parent / "bot.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5,
                                encoding="utf-8"),
            *([logging.StreamHandler()] if sys.stderr else []),
        ],
    )
    _disable_quick_edit()
    missing = config.missing()
    if missing:
        for key in missing:
            print(f"missing env: {key}", file=sys.stderr)
        print("copy .env.example to .env and fill it in", file=sys.stderr)
        return 2

    store = Store(config.db_path)
    llm = LLM(config.llm_provider, config.llm_api_key(), config.llm_model)
    agent = ElectionAgent(llm, store, config)
    health = Health()
    logging.getLogger("Lark").addHandler(LarkConnWatcher(health))
    bot = FeishuBot(config, agent, health)
    start_watchdog(ws_loop)
    bot.start()  # blocks on the WS loop
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
