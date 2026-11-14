"""
Main Streamlit Web Application (`app.py`) for CADCRAFT.
Clean Gemini/ChatGPT-style interface for team users, with an Admin Queue view
only when Ilham unlocks Admin mode.
"""

from __future__ import annotations

import streamlit as st

from src.auth import render_login_gate
from src.supabase_client import get_db
from src.ui_admin import render_admin_dashboard
from src.ui_chat import render_chat_interface, render_sidebar_Sessions

st.set_page_config(
    page_title="CADCRAFT",
    page_icon="📐",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    if not render_login_gate():
        return

    db = get_db()
    worker_status = db.get_worker_status()

    render_sidebar_Sessions(db, worker_status)

    if st.session_state.get("is_admin") and st.session_state.get("admin_view_active"):
        render_admin_dashboard(db, worker_status)
    else:
        render_chat_interface(db, worker_status)


if __name__ == "__main__":
    main()
