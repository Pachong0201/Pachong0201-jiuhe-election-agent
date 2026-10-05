"""Deterministic electoral math — the LLM never computes these by hand."""

from __future__ import annotations

from typing import Optional


def vote_share(votes: float, total: float) -> float:
    if not total:
        return 0.0
    return round(votes * 100.0 / total, 2)


def margin_pp(top: float, runner_up: float) -> float:
    """Winner's lead over runner-up in percentage points."""
    return round(top - runner_up, 2)


def swing_pp(share_now: Optional[float], share_prev: Optional[float]) -> Optional[float]:
    """Same-party (or same-camp) vote-share change between two elections, in pp."""
    if share_now is None or share_prev is None:
        return None
    return round(share_now - share_prev, 2)


def residual(share: Optional[float], baseline: Optional[float]) -> Optional[float]:
    """Candidate share minus its camp's historical baseline share, in pp.

    Positive = over-performing the baseline; negative = under-performing.
    """
    if share is None or baseline is None:
        return None
    return round(share - baseline, 2)


def competitiveness(rows: list[dict]) -> dict:
    """Summarize a list of {name, party, share} results into a verdict.

    Returns margin and a coarse classification used by the agent's wording:
      safe (>10pp), leaning (5-10), competitive (2-5), tossup (<2).
    """
    if not rows:
        return {"margin_pp": None, "class": "no_data", "leader": None, "runner_up": None}
    ordered = sorted(rows, key=lambda r: (r.get("share") or 0), reverse=True)
    top, second = ordered[0], (ordered[1] if len(ordered) > 1 else {"share": 0})
    m = round((top.get("share") or 0) - (second.get("share") or 0), 2)
    cls = "tossup" if m < 2 else "competitive" if m < 5 else "leaning" if m < 10 else "safe"
    return {
        "margin_pp": m,
        "class": cls,
        "leader": {"name": top.get("name"), "party": top.get("party"),
                   "share": top.get("share")},
        "runner_up": {"name": second.get("name"), "party": second.get("party"),
                      "share": second.get("share")},
    }
