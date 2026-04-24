"""Streamlit app entry point and navigation.

Multi-page app with sidebar navigation to Drive Management, Review Queue,
Progress Dashboard, and Export pages.

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

    # Sidebar navigation
    st.sidebar.title("💾 Drive Backup Triage")
    page = st.sidebar.radio(
        "Navigate",
        ["Drive Management", "Review Queue", "Progress Dashboard", "Export"],
        label_visibility="collapsed",
    )

    # Route to pages
    if page == "Drive Management":
        from src.ui.pages.drive_management import render
        render()
    elif page == "Review Queue":
        from src.ui.pages.review_queue import render
        render()
    elif page == "Progress Dashboard":
        from src.ui.pages.progress_dashboard import render
        render()
    elif page == "Export":
        from src.ui.pages.export import render
        render()


if __name__ == "__main__":
    main()
