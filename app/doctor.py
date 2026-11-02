"""Local troubleshooting: `python -m app.doctor`.

Reads data/bot.log on this machine and prints a plain-language diagnosis.
Nothing leaves the machine.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time

from .config import Config

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_EVENTS = {
    "start": "starting Feishu WS client",
    "connect": "connected to wss",
    "disconnect": "disconnected to",
    "message": "message route=",
    "report": "report done:",
    "heartbeat": "heartbeat",
    "watchdog": "watchdog:",
}


def scan_log(lines: list[str]) -> dict:
    last: dict[str, tuple[float, str]] = {}
    errors: list[str] = []
    for line in lines:
        m = _TS.match(line)
        if not m:
            continue
        ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        for key, needle in _EVENTS.items():
            if needle in line and not (key == "connect" and _EVENTS["disconnect"] in line):
                last[key] = (ts, line.strip())
        if " ERROR " in line or " CRITICAL " in line or "reply failed" in line:
            errors.append(line.strip()[:200])
    return {"last": last, "errors": errors[-8:]}


def bot_processes() -> int:
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
                 "? CommandLine -like '*app.main*' | Measure-Object).Count"],
                capture_output=True, text=True, timeout=30).stdout.strip()
            return int(out or 0)
        out = subprocess.run(["pgrep", "-fc", r"-m app\.main"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out or 0)
    except Exception:  # noqa: BLE001
        return -1


def _ago(ts: float) -> str:
    m = int((time.time() - ts) // 60)
    return f"{m} 分鐘前" if m < 120 else f"{m // 60} 小時前"


def main() -> int:
    log_file = Config().db_path.parent / "bot.log"
    n = bot_processes()
    print(f"機器人進程數：{n if n >= 0 else '無法查詢'}")
    if n == 0:
        print("  → 機器人沒有在運行。schtasks /run /tn \"JiuheElectionBot\" 或前台啟動。")
    elif n > 1:
        print("  → 有多個機器人同時連著飛書，消息會被隨機分走。只保留一個（結束前台窗口或計劃任務其一）。")

    if not log_file.is_file():
        print(f"找不到日誌 {log_file}（更新到最新版代碼後啟動一次才會產生）")
        return 1
    files = [p for p in (log_file.with_name("bot.log.1"), log_file) if p.is_file()]
    lines = [ln for p in files for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()]
    r = scan_log(lines)
    last = r["last"]
    labels = {"start": "最近啟動", "connect": "最近連上飛書", "disconnect": "最近斷線",
              "message": "最近收到消息", "report": "最近完成報告", "heartbeat": "最近心跳",
              "watchdog": "最近一次卡死自動重啟"}
    for key, label in labels.items():
        if key in last:
            print(f"{label}：{_ago(last[key][0])}")
    if n >= 1 and "heartbeat" in last and time.time() - last["heartbeat"][0] > 45 * 60:
        print("  → 進程還在但超過 45 分鐘沒有心跳，可能卡住了；結束進程後重新啟動。")
    if "message" not in last or ("start" in last and last["message"][0] < last["start"][0]):
        print("  → 本次啟動後沒收到過任何飛書消息：檢查是否還有別的實例在跑、群裡是否 @了機器人。")
    if r["errors"]:
        print("\n最近的錯誤（最多 8 條）：")
        for e in r["errors"]:
            print("  " + e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
