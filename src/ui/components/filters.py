"""Sidebar filter components for the review queue.

Requirements: 3.1, 3.3
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from src.db.models import Drive
from src.db.repository import Repository

# Taxonomy values for filter dropdowns
FOLDER_PURPOSES = [
    "irreplaceable_personal",
    "important_personal",
    "project_or_work",
    "reinstallable_software",
    "media_archive",
    "redundant_duplicate",
    "system_or_temp",
    "unknown_review_needed",
]


@dataclass
class ReviewFilters:
    """Collected filter values from the sidebar."""

    drive_id: str | None
    category: str | None
    min_confidence: float
    max_confidence: float
    limit: int
    offset: int


def render_filters(repo: Repository) -> ReviewFilters | None:
    """Render sidebar filters and return the collected values.

    Returns None if no drives are available.
    """
    drives = repo.list_drives()
    if not drives:
        st.sidebar.warning("No drives registered.")
        return None

    st.sidebar.subheader("Filters")

    # Drive selector
    drive_options = {f"{d.label} ({d.id[:8]}…)": d.id for d in drives}
    selected = st.sidebar.selectbox("Drive", list(drive_options.keys()))
    drive_id = drive_options[selected]

    # Store selected drive in session state for other pages
    st.session_state.selected_drive_id = drive_id

    # Category filter
    category = st.sidebar.selectbox(
        "Category",
        ["All"] + FOLDER_PURPOSES,
        index=0,
    )

    # Confidence range
    conf_range = st.sidebar.slider(
        "Confidence Range",
        min_value=0.0,
        max_value=1.0,
        value=(0.0, 1.0),
        step=0.05,
    )

    # Pagination
    limit = st.sidebar.number_input("Page size", min_value=10, max_value=500, value=50)
    page = st.sidebar.number_input("Page", min_value=1, value=1)
    offset = (page - 1) * limit

    return ReviewFilters(
        drive_id=drive_id,
        category=category if category != "All" else None,
        min_confidence=conf_range[0],
        max_confidence=conf_range[1],
        limit=limit,
        offset=offset,
    )
