"""Run the full data pipeline: history -> candidates -> polls.

Usage:  python -m pipeline.run_all [--skip-history] [--skip-candidates] [--skip-polls]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import candidates_2026, cec_history, polls_tvbs  # noqa: E402

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "kb.sqlite3"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-history", action="store_true")
    ap.add_argument("--skip-candidates", action="store_true")
    ap.add_argument("--skip-polls", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.skip_history:
        log.info("=== cec_history")
        cec_history.download(cec_history.DEFAULT_ZIP)
        cec_history.run(
            cec_history.DEFAULT_ZIP, DB,
            {"county_mayor", "president", "regional_legislator"},
        )
    if not args.skip_candidates:
        log.info("=== candidates_2026")
        try:
            candidates_2026.run(candidates_2026.DEFAULT_PAGE, DB, "2026")
        except Exception as e:  # noqa: BLE001
            log.warning("candidates step failed (non-fatal): %s", e)
    if not args.skip_polls:
        log.info("=== polls_tvbs")
        try:
            polls_tvbs.run(DB, polls_tvbs.INDEX_URL)
        except Exception as e:  # noqa: BLE001
            log.warning("polls step failed (non-fatal): %s", e)


if __name__ == "__main__":
    main()
