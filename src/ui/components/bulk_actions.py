"""Bulk action bar for applying decisions to multiple entries at once.

Requirements: 3.6
"""

from __future__ import annotations

import streamlit as st

from src.db.models import Entry
from src.db.status import apply_transition, InvalidTransitionError
from src.db.repository import Repository


def render_bulk_actions(
    selected_entries: list[Entry],
    conn,
    repo: Repository,
) -> None:
    """Render the bulk action bar when entries are selected."""
    if not selected_entries:
        return

    st.info(f"**{len(selected_entries)}** entries selected")

    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    with col1:
        bulk_dest = st.text_input(
            "Bulk destination", key="bulk_dest", placeholder="Optional"
        )
        bulk_notes = st.text_input(
            "Bulk notes", key="bulk_notes", placeholder="Optional"
        )

    for col, (decision, label) in zip(
        [col2, col3, col4],
        [("include", "✅ Include All"), ("exclude", "❌ Exclude All"), ("defer", "⏸️ Defer All")],
    ):
        with col:
            if st.button(label, key=f"bulk_{decision}", use_container_width=True):
                _apply_bulk_decision(
                    selected_entries, decision,
                    bulk_dest, bulk_notes, conn, repo,
                )
                st.rerun()


def _apply_bulk_decision(
    entries: list[Entry],
    decision: str,
    destination: str,
    notes: str,
    conn,
    repo: Repository,
) -> None:
    """Apply a decision to all selected entries."""
    succeeded = 0
    failed = 0
    for entry in entries:
        try:
            conn.execute(
                "UPDATE entries SET decision_destination = ?, decision_notes = ? WHERE id = ?",
                (destination.strip() or None, notes.strip() or None, entry.id),
            )
            conn.commit()

            refreshed = repo.get_entry(entry.id)
            if refreshed and refreshed.review_status != "reviewed":
                apply_transition(conn, entry.id, "review_status", "reviewed")

            refreshed = repo.get_entry(entry.id)
            if refreshed and refreshed.decision_status != decision:
                apply_transition(conn, entry.id, "decision_status", decision)

            succeeded += 1
        except (InvalidTransitionError, Exception):
            failed += 1

    st.success(f"Bulk decision applied: {succeeded} succeeded, {failed} failed.")
