"""Export page — manifest preview, CSV and JSON export.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
"""

from __future__ import annotations

import streamlit as st

from src.db.models import Entry
from src.export import build_summary, entries_to_csv, entries_to_json
from src.ui.app import get_repo


def render() -> None:
    """Main render function for the Export page."""
    st.title("Decision Manifest Export")

    repo = get_repo()
    drives = repo.list_drives()

    if not drives:
        st.info("No drives registered yet.")
        return

    drive_options = {f"{d.label} ({d.id[:8]}…)": d.id for d in drives}
    selected_label = st.selectbox("Select Drive", list(drive_options.keys()))
    drive_id = drive_options[selected_label]
    drive = repo.get_drive(drive_id)

    # Decision filter
    decision_filter = st.selectbox(
        "Filter by decision",
        ["include", "exclude", "defer", "All reviewed"],
        index=0,
    )

    filters: dict = {}
    if decision_filter != "All reviewed":
        filters["decision_status"] = decision_filter

    entries = repo.get_decision_manifest(drive_id, filters)

    if not entries:
        st.info("No entries match the current filter.")
        return

    # Summary
    summary = build_summary(drive, entries, decision_filter)
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Entries", len(entries))
    col2.metric("Drive", drive.label if drive else "—")
    col3.metric("Filter", decision_filter)

    # Preview table
    st.subheader("Preview")
    preview_data = []
    for e in entries:
        classification = e.folder_purpose or e.file_class or "—"
        preview_data.append({
            "Path": e.path,
            "Destination": e.decision_destination or "—",
            "Type": e.entry_type,
            "Classification": classification,
            "Confidence": f"{e.confidence:.0%}" if e.confidence is not None else "—",
            "Decision": e.decision_status,
            "Notes": e.decision_notes or "—",
        })
    st.dataframe(preview_data, use_container_width=True)

    # Export buttons
    st.subheader("Download")
    col_csv, col_json = st.columns(2)

    csv_data = entries_to_csv(entries, summary)
    with col_csv:
        st.download_button(
            "📥 Download CSV",
            data=csv_data,
            file_name=f"manifest_{drive_id[:8]}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    json_data = entries_to_json(entries, summary)
    with col_json:
        st.download_button(
            "📥 Download JSON",
            data=json_data,
            file_name=f"manifest_{drive_id[:8]}.json",
            mime="application/json",
            use_container_width=True,
        )
