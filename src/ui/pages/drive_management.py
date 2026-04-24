"""Drive Management page — registration, label editing, CSV import.

Requirements: 1.1, 1.2, 1.5, 1.7, 1.8
"""

from __future__ import annotations

import tempfile
import os

import streamlit as st

from src.db.repository import Repository
from src.importer.csv_importer import (
    ColumnMapping,
    ConflictError,
    import_csv,
)
from src.ui.app import get_connection, get_repo


def _render_registration_form(repo: Repository) -> None:
    """Drive registration form."""
    st.subheader("Register a New Drive")
    with st.form("register_drive", clear_on_submit=True):
        label = st.text_input("Drive Label *", placeholder="e.g. My Backup Drive")
        col1, col2 = st.columns(2)
        with col1:
            volume_serial = st.text_input("Volume Serial", placeholder="Optional")
            volume_label = st.text_input("Volume Label", placeholder="Optional")
        with col2:
            capacity = st.number_input(
                "Capacity (bytes)", min_value=0, value=0, step=1,
                help="Total drive capacity in bytes. Leave 0 to skip.",
            )
        submitted = st.form_submit_button("Register Drive")

    if submitted:
        if not label.strip():
            st.error("Drive label is required.")
            return
        # Warn on duplicate volume serial
        if volume_serial.strip():
            existing = repo.get_drive_by_serial(volume_serial.strip())
            if existing:
                st.warning(
                    f"A drive with volume serial '{volume_serial.strip()}' "
                    f"already exists: **{existing.label}** ({existing.id}). "
                    "This may be a duplicate registration."
                )
        drive = repo.create_drive(
            label=label.strip(),
            volume_serial=volume_serial.strip() or None,
            volume_label=volume_label.strip() or None,
            capacity_bytes=capacity if capacity > 0 else None,
        )
        st.success(f"Drive registered: **{drive.label}** (`{drive.id}`)")
        st.rerun()


def _render_drive_list(repo: Repository) -> None:
    """List registered drives with edit-label capability."""
    st.subheader("Registered Drives")
    drives = repo.list_drives()
    if not drives:
        st.info("No drives registered yet.")
        return

    for drive in drives:
        with st.expander(f"💾 {drive.label}  —  `{drive.id[:8]}…`", expanded=False):
            st.text(f"UUID: {drive.id}")
            st.text(f"Volume Serial: {drive.volume_serial or '—'}")
            st.text(f"Volume Label: {drive.volume_label or '—'}")
            cap = drive.capacity_bytes
            if cap:
                gb = cap / (1024 ** 3)
                st.text(f"Capacity: {gb:,.1f} GB ({cap:,} bytes)")
            else:
                st.text("Capacity: —")
            entry_count = repo.count_entries_by_drive(drive.id)
            st.text(f"Entries: {entry_count:,}")

            # Edit label
            new_label = st.text_input(
                "Edit label", value=drive.label, key=f"edit_label_{drive.id}"
            )
            if st.button("Save Label", key=f"save_label_{drive.id}"):
                if new_label.strip() and new_label.strip() != drive.label:
                    repo.update_drive_label(drive.id, new_label.strip())
                    st.success("Label updated.")
                    st.rerun()


def _render_csv_import(repo: Repository) -> None:
    """CSV import form with file upload, drive selector, and options."""
    st.subheader("Import CSV")
    drives = repo.list_drives()
    if not drives:
        st.warning("Register a drive first before importing.")
        return

    drive_options = {f"{d.label} ({d.id[:8]}…)": d.id for d in drives}
    selected_label = st.selectbox("Select Drive", list(drive_options.keys()))
    drive_id = drive_options[selected_label]

    uploaded = st.file_uploader("Upload TreeSize CSV", type=["csv"])

    with st.expander("Advanced Options"):
        col1, col2 = st.columns(2)
        with col1:
            path_col = st.text_input("Path column", value="Path")
            name_col = st.text_input("Name column", value="Name")
            size_col = st.text_input("Size column", value="Size")
        with col2:
            modified_col = st.text_input("Last Modified column", value="Last Modified")
            type_col = st.text_input("Type column", value="Type")
            skip_rows = st.number_input("Skip preamble rows", min_value=0, value=0)
        force = st.checkbox("Force re-import (add entries even if drive already has data)")

    if st.button("Import", disabled=uploaded is None):
        if uploaded is None:
            return
        mapping = ColumnMapping(
            path=path_col, name=name_col, size=size_col,
            last_modified=modified_col, entry_type=type_col,
        )
        # Write uploaded file to a temp path for the importer
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".csv", mode="wb"
        ) as tmp:
            tmp.write(uploaded.getvalue())
            tmp_path = tmp.name

        conn = get_connection()
        try:
            result = import_csv(
                conn=conn,
                csv_path=tmp_path,
                drive_id=drive_id,
                column_mapping=mapping,
                force=force,
                skip_rows=skip_rows,
            )
            st.success(
                f"Import complete — **{result.entries_created}** entries created, "
                f"**{result.rows_skipped}** rows skipped."
            )
            if result.skip_details:
                with st.expander("Skip Details"):
                    for detail in result.skip_details:
                        st.text(f"Row {detail.row_number}: {detail.reason}")
        except ConflictError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Import failed: {exc}")
        finally:
            os.unlink(tmp_path)


def render() -> None:
    """Main render function for the Drive Management page."""
    st.title("Drive Management")
    repo = get_repo()
    _render_registration_form(repo)
    st.divider()
    _render_csv_import(repo)
    st.divider()
    _render_drive_list(repo)
