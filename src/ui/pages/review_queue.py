"""Review Queue page — browse, filter, decide on classified entries.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9
"""

from __future__ import annotations

import os
from collections import defaultdict

import streamlit as st

from src.db.models import Entry
from src.ui.app import get_connection, get_repo
from src.ui.components.filters import render_filters
from src.ui.components.entry_card import render_entry_card
from src.ui.components.bulk_actions import render_bulk_actions


def _group_by_parent(entries: list[Entry]) -> dict[str, list[Entry]]:
    """Group entries by their parent folder path."""
    groups: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        parent = os.path.dirname(entry.path) or "(root)"
        groups[parent].append(entry)
    return dict(groups)


def render() -> None:
    """Main render function for the Review Queue page."""
    st.title("Review Queue")

    repo = get_repo()
    conn = get_connection()

    filters = render_filters(repo)
    if filters is None or filters.drive_id is None:
        st.info("Select a drive to begin reviewing.")
        return

    # Fetch entries
    filter_dict: dict = {
        "limit": filters.limit,
        "offset": filters.offset,
    }
    if filters.category:
        filter_dict["category"] = filters.category
    if filters.min_confidence > 0.0:
        filter_dict["min_confidence"] = filters.min_confidence
    if filters.max_confidence < 1.0:
        filter_dict["max_confidence"] = filters.max_confidence

    entries = repo.get_review_queue(filters.drive_id, filter_dict)

    if not entries:
        st.info("No entries in the review queue matching the current filters.")
        return

    st.caption(f"Showing {len(entries)} entries (lowest confidence first)")

    # Collect selected entries for bulk actions
    selected: list[Entry] = []

    # Group by parent folder
    groups = _group_by_parent(entries)
    for parent_path, group_entries in sorted(groups.items()):
        st.subheader(f"📂 {parent_path}")
        for entry in group_entries:
            is_selected = render_entry_card(
                entry, conn, repo, key_prefix=f"rq_",
            )
            if is_selected:
                selected.append(entry)

    # Bulk action bar
    st.divider()
    render_bulk_actions(selected, conn, repo)
