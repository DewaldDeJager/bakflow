"""Entry card component for displaying a single entry in the review queue.

Requirements: 3.2, 3.4, 3.5
"""

from __future__ import annotations

import streamlit as st

from src.db.models import Entry
from src.db.status import apply_transition, InvalidTransitionError
from src.db.repository import Repository
from src.ui.components.filters import FOLDER_PURPOSES


def _format_size(size_bytes: int) -> str:
    """Format bytes into a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


def render_entry_card(
    entry: Entry,
    conn,
    repo: Repository,
    key_prefix: str = "",
    show_checkbox: bool = True,
) -> bool:
    """Render a single entry card with action buttons.

    Returns True if the entry checkbox is selected (for bulk operations).
    """
    selected = False
    classification = entry.folder_purpose or entry.file_class or "—"
    conf_str = f"{entry.confidence:.0%}" if entry.confidence is not None else "—"
    icon = "📁" if entry.entry_type == "folder" else "📄"
    label = f"{icon} {entry.name}  —  {classification} ({conf_str})"

    col_check, col_expand = st.columns([0.05, 0.95])
    with col_check:
        if show_checkbox:
            selected = st.checkbox(
                "sel", key=f"{key_prefix}sel_{entry.id}", label_visibility="collapsed"
            )
    with col_expand:
        with st.expander(label, expanded=False):
            # Info row
            c1, c2, c3 = st.columns(3)
            c1.text(f"Path: {entry.path}")
            c2.text(f"Size: {_format_size(entry.size_bytes)}")
            c3.text(f"Modified: {entry.last_modified or '—'}")

            st.text(f"Status: {entry.classification_status} / {entry.review_status} / {entry.decision_status}")

            if entry.priority_review:
                st.warning("⚠️ Flagged for priority review (low confidence)")

            # Classification override
            if entry.entry_type == "folder":
                override_options = ["(keep current)"] + FOLDER_PURPOSES
            else:
                override_options = ["(keep current)", classification] if classification != "—" else ["(keep current)"]
            override = st.selectbox(
                "Override classification",
                override_options,
                index=0,
                key=f"{key_prefix}override_{entry.id}",
            )

            # Decision actions
            dest = st.text_input(
                "Destination path", key=f"{key_prefix}dest_{entry.id}", placeholder="Optional"
            )
            notes = st.text_input(
                "Notes", key=f"{key_prefix}notes_{entry.id}", placeholder="Optional"
            )

            # Cascade option for folders
            cascade = False
            if entry.entry_type == "folder":
                cascade = st.checkbox(
                    "Cascade to undecided children",
                    key=f"{key_prefix}cascade_{entry.id}",
                )

            btn_cols = st.columns(3)
            for idx, (decision, btn_label, color) in enumerate([
                ("include", "✅ Include", "primary"),
                ("exclude", "❌ Exclude", "secondary"),
                ("defer", "⏸️ Defer", "secondary"),
            ]):
                if btn_cols[idx].button(
                    btn_label,
                    key=f"{key_prefix}{decision}_{entry.id}",
                    use_container_width=True,
                ):
                    _apply_decision(
                        entry, decision, dest, notes,
                        override if override != "(keep current)" else None,
                        cascade, conn, repo,
                    )
                    st.rerun()

    return selected


def _apply_decision(
    entry: Entry,
    decision: str,
    destination: str,
    notes: str,
    override_classification: str | None,
    cascade: bool,
    conn,
    repo: Repository,
) -> None:
    """Apply a decision to an entry, handling transitions and cascade."""
    try:
        # Handle classification override
        if override_classification:
            conn.execute(
                "UPDATE entries SET user_override_classification = ? WHERE id = ?",
                (override_classification, entry.id),
            )
            if entry.entry_type == "file":
                conn.execute(
                    "UPDATE entries SET file_class = ? WHERE id = ?",
                    (override_classification, entry.id),
                )
            else:
                conn.execute(
                    "UPDATE entries SET folder_purpose = ? WHERE id = ?",
                    (override_classification, entry.id),
                )
            conn.commit()

        # Store destination and notes
        conn.execute(
            "UPDATE entries SET decision_destination = ?, decision_notes = ? WHERE id = ?",
            (destination.strip() or None, notes.strip() or None, entry.id),
        )
        conn.commit()

        # Transition review_status → reviewed
        refreshed = repo.get_entry(entry.id)
        if refreshed and refreshed.review_status != "reviewed":
            apply_transition(conn, entry.id, "review_status", "reviewed")

        # Transition decision_status
        refreshed = repo.get_entry(entry.id)
        if refreshed and refreshed.decision_status != decision:
            apply_transition(conn, entry.id, "decision_status", decision)

        # Cascade
        if cascade and entry.entry_type == "folder":
            children = repo.get_child_entries(entry.drive_id, entry.path)
            for child in children:
                if child.decision_status != "undecided":
                    continue
                try:
                    if child.review_status != "reviewed" and child.classification_status == "ai_classified":
                        apply_transition(conn, child.id, "review_status", "reviewed")
                    conn.execute(
                        "UPDATE entries SET decision_destination = ?, decision_notes = ? WHERE id = ?",
                        (destination.strip() or None, notes.strip() or None, child.id),
                    )
                    conn.commit()
                    child_refreshed = repo.get_entry(child.id)
                    if child_refreshed and child_refreshed.review_status == "reviewed":
                        apply_transition(conn, child.id, "decision_status", decision)
                except InvalidTransitionError:
                    pass

        st.success(f"Decision '{decision}' recorded for {entry.name}")
    except InvalidTransitionError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"Failed to record decision: {exc}")
