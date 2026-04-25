"""Streamlit app entry point and navigation.

Multi-page app with sidebar navigation to Drive Management, Review Queue,
Progress Dashboard, and Export pages.  Uses st.navigation / st.Page so that
there is a single sidebar menu whose links update the browser URL.

Requirements: 1.8
"""

import streamlit as st

from src.config import AppConfig
from src.db.schema import init_db
from src.db.repository import Repository


def get_connection():
    """Get or create a cached database connection."""
    if "db_conn" not in st.session_state:
        config = AppConfig()
        conn = init_db(config.db_path)
        st.session_state.db_conn = conn
    return st.session_state.db_conn


def get_repo() -> Repository:
    """Get or create a cached Repository instance."""
    if "repo" not in st.session_state:
        st.session_state.repo = Repository(get_connection())
    return st.session_state.repo


def _drive_management_page():
    from src.ui.pages.drive_management import render
    render()


def _review_queue_page():
    from src.ui.pages.review_queue import render
    render()


def _progress_dashboard_page():
    from src.ui.pages.progress_dashboard import render
    render()


def _export_page():
    from src.ui.pages.export import render
    render()


def main():
    st.set_page_config(
        page_title="Drive Backup Triage",
        page_icon="💾",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Initialize session state defaults
    if "selected_drive_id" not in st.session_state:
        st.session_state.selected_drive_id = None

    pages = st.navigation(
        [
            st.Page(_drive_management_page, title="Drive Management", icon="💾", url_path="drive-management", default=True),
            st.Page(_review_queue_page, title="Review Queue", icon="📋", url_path="review-queue"),
            st.Page(_progress_dashboard_page, title="Progress Dashboard", icon="📊", url_path="progress-dashboard"),
            st.Page(_export_page, title="Export", icon="📥", url_path="export"),
        ],
        position="sidebar",
    )

    st.sidebar.title("💾 Drive Backup Triage")
    pages.run()


if __name__ == "__main__":
    main()
