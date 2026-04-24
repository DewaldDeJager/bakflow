"""Progress Dashboard page — triage progress visualization per drive.

Requirements: 5.3
"""

from __future__ import annotations

import streamlit as st

from src.ui.app import get_repo


_STATUS_LABELS = {
    "classification_status": {
        "unclassified": "Unclassified",
        "ai_classified": "AI Classified",
        "classification_failed": "Failed",
        "needs_reclassification": "Needs Reclassification",
    },
    "review_status": {
        "pending_review": "Pending Review",
        "reviewed": "Reviewed",
    },
    "decision_status": {
        "undecided": "Undecided",
        "include": "Include",
        "exclude": "Exclude",
        "defer": "Defer",
    },
}


def _render_dimension(title: str, dimension: str, counts: dict, total: int) -> None:
    """Render progress bars for a single status dimension."""
    st.subheader(title)
    if total == 0:
        st.info("No entries.")
        return

    labels = _STATUS_LABELS.get(dimension, {})
    for status, count in sorted(counts.items()):
        pct = count / total
        display = labels.get(status, status)
        st.progress(pct, text=f"{display}: {count:,} ({pct:.0%})")


def render() -> None:
    """Main render function for the Progress Dashboard page."""
    st.title("Progress Dashboard")

    repo = get_repo()
    drives = repo.list_drives()

    if not drives:
        st.info("No drives registered yet.")
        return

    drive_options = {f"{d.label} ({d.id[:8]}…)": d.id for d in drives}
    selected = st.selectbox("Select Drive", list(drive_options.keys()))
    drive_id = drive_options[selected]

    progress = repo.get_drive_progress(drive_id)
    total = progress["total"]

    if total == 0:
        st.info("This drive has no entries.")
        return

    # Overall completion
    pct = progress["completion_pct"]
    st.metric("Overall Completion", f"{pct:.0%}", help="Reviewed / Total entries")
    st.progress(pct)

    st.divider()

    col1, col2, col3 = st.columns(3)
    with col1:
        _render_dimension(
            "Classification", "classification_status",
            progress["classification_status"], total,
        )
    with col2:
        _render_dimension(
            "Review", "review_status",
            progress["review_status"], total,
        )
    with col3:
        _render_dimension(
            "Decision", "decision_status",
            progress["decision_status"], total,
        )
