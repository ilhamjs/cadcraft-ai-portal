"""
Authentication & Access Control Gate for CADCraft AI Agent Web Portal.
- Enforces single shared Team Username & Password before rendering any UI.
- Provides Admin Password verification to unlock Ilham's Admin Queue Controller.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _get_secret(key: str, default: str) -> str:
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    val = os.getenv(key, "")
    if val:
        return val
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return default


def init_auth_state() -> None:
    """Initialize authentication keys in st.session_state."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "is_admin" not in st.session_state:
        st.session_state.is_admin = False
    if "requester_name" not in st.session_state:
        st.session_state.requester_name = ""
    if "session_job_ids" not in st.session_state:
        st.session_state.session_job_ids = []


def verify_team_password(password: str) -> bool:
    expected_pass = _get_secret("TEAM_PASSWORD", "")
    if not expected_pass:
        return False
    return hmac.compare_digest(password, expected_pass)


def verify_admin_password(password: str) -> bool:
    expected_admin = _get_secret("ADMIN_PASSWORD", "")
    if not expected_admin:
        return False
    return hmac.compare_digest(password, expected_admin)


def render_login_gate() -> bool:
    """
    Render the full-page Login Gate if the user is not yet authenticated.
    Returns True when authenticated, False otherwise.
    """
    init_auth_state()
    if st.session_state.authenticated:
        return True

    st.markdown(
        """
        <div style="max-width: 480px; margin: 3rem auto 1rem auto; text-align: center;">
            <h1 style="margin-bottom: 0.25rem; letter-spacing: 0.04em;">📐 CADCRAFT</h1>
            <p style="color: #6b7280; font-size: 1.02rem; font-style: italic;">
                "CAD Everything. Create Anything"
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_left, col_center, col_right = st.columns([1, 1.6, 1])
    with col_center:
        with st.container(border=True):
            if not _get_secret("TEAM_PASSWORD", ""):
                st.error("⚠️ `TEAM_PASSWORD` belum dikonfigurasi di `.env` atau Streamlit Secrets.")
                return False

            with st.form("team_login_form", clear_on_submit=False):
                requester_name = st.text_input(
                    "Nama",
                    value=st.session_state.requester_name,
                    placeholder="Masukkan nama",
                )
                password = st.text_input(
                    "Password",
                    type="password",
                    placeholder="Masukkan password",
                )
                admin_pass_opt = st.text_input(
                    "Admin Password",
                    type="password",
                    placeholder="Opsional",
                )

                submitted = st.form_submit_button(
                    "Masuk ke CADCRAFT",
                    use_container_width=True,
                    type="primary",
                )

                if submitted:
                    if not requester_name.strip():
                        st.error("Mohon isi Nama Anda terlebih dahulu (wajib diisi).")
                    elif verify_team_password(password):
                        st.session_state.authenticated = True
                        st.session_state.requester_name = requester_name.strip()
                        if admin_pass_opt and verify_admin_password(admin_pass_opt):
                            st.session_state.is_admin = True
                        st.rerun()
                    else:
                        st.error("Password Tim tidak sesuai. Silakan coba lagi.")

    return False


def render_admin_unlock_widget() -> bool:
    """
    Render an unlock widget for the Admin Queue Approval Panel if not yet unlocked.
    Returns True if Admin mode is unlocked.
    """
    init_auth_state()
    if st.session_state.is_admin:
        col1, col2 = st.columns([4, 1])
        with col1:
            st.success("🔓 Mode Admin Aktif (Ilham - Queue & Local Worker Controller)")
        with col2:
            if st.button("Kunci Mode Admin", use_container_width=True):
                st.session_state.is_admin = False
                st.rerun()
        return True

    with st.container(border=True):
        st.markdown("### 🔒 Panel Admin (Khusus Ilham)")
        st.caption(
            "Masukkan `ADMIN_PASSWORD` untuk membuka antrean semua divisi, menyetujui (`Accept & Execute`) "
            "request DWG, atau mengunggah hasil manual."
        )
        with st.form("admin_unlock_form"):
            admin_pw = st.text_input("Admin Password", type="password")
            unlock_btn = st.form_submit_button("Buka Dashboard Admin", type="primary")
            if unlock_btn:
                if verify_admin_password(admin_pw):
                    st.session_state.is_admin = True
                    st.rerun()
                else:
                    st.error("Admin Password salah.")
    return False


def logout_user() -> None:
    """Clear session authentication state and ephemeral session chat history."""
    st.session_state.authenticated = False
    st.session_state.is_admin = False
    st.session_state.active_job_id = None
    st.session_state.session_job_ids = []
    st.rerun()
