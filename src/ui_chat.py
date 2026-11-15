"""
Minimalist Gemini/ChatGPT-style Chatbot UI (`src/ui_chat.py`) for CADCRAFT.
- Left sidebar: Percakapan baru + Selectable Skills + minimal status & Admin/Logout footer.
- No persistent history across logins (ephemeral single sign-in session).
- Main area: Clean drag-and-drop file uploader (.dwg, .dxf, .jpg, .png, .pdf) + prompt input,
  live processing animation, and temporary file download when completed.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from src.auth import logout_user, verify_admin_password
from src.models import (
    get_available_models,
    get_current_cli_model,
    get_model_display_label,
    set_current_cli_model,
)
from src.skills import SKILLS, get_skill_by_id, get_skills_by_ids
from src.supabase_client import (
    CADPortalDatabase,
    format_timestamp_stem,
    get_requester_folder_name,
)

SUPPORTED_FILE_TYPES = ["dwg", "dxf", "jpg", "jpeg", "png", "pdf"]
EXECUTION_MODES = ["fast_flash", "fast_optimized", "extra_time"]
EXECUTION_MODE_LABELS = {
    "fast_flash": "🚀 Fast-Flash",
    "fast_optimized": "⚡ Fast-Optimized",
    "extra_time": "🐢 Extra-Time",
}


def _sanitize_filename(name: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "._- ") else "_" for c in name)
    return keep.strip().replace(" ", "_") or "file"


def _inject_clean_chat_css() -> None:
    st.markdown(
        """
        <style>
        /* Clean minimalist Gemini-like spacing */
        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
            max-width: 880px;
        }
        .cadcraft-greeting {
            text-align: center;
            margin-top: 3.5rem;
            margin-bottom: 1.75rem;
        }
        .cadcraft-greeting h1 {
            font-size: 2.15rem;
            font-weight: 600;
            margin-bottom: 0.35rem;
        }
        .cadcraft-greeting p {
            color: #9ca3af;
            font-size: 1.05rem;
            margin: 0;
        }
        .skill-badge {
            display: inline-block;
            padding: 0.3rem 0.85rem;
            border-radius: 999px;
            font-size: 0.85rem;
            background: rgba(99, 102, 241, 0.14);
            border: 1px solid rgba(99, 102, 241, 0.35);
            margin-bottom: 0.85rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_Sessions(db: CADPortalDatabase, worker_status: Dict[str, Any]) -> None:
    """Render clean Gemini-style sidebar: Percakapan baru + Skills + minimal footer."""
    if "selected_skill_ids" not in st.session_state:
        st.session_state.selected_skill_ids = [SKILLS[0].id]
    if "admin_view_active" not in st.session_state:
        st.session_state.admin_view_active = False
    if "session_job_ids" not in st.session_state:
        st.session_state.session_job_ids = []

    active_id = st.session_state.get("active_job_id")
    if active_id and active_id not in st.session_state.session_job_ids:
        st.session_state.session_job_ids.insert(0, active_id)

    with st.sidebar:
        st.markdown("## 📐 CADCRAFT")
        st.caption('*"CAD Everything. Create Anything"*')

        # Minimal live status indicator
        if worker_status["is_online"]:
            st.caption("🟢 Sistem: Online (Siap Memproses)")
        else:
            st.caption("🔴 Sistem: Offline (Mode Antrean)")

        st.write("")

        # 1. Percakapan baru
        if st.button("✏️ Percakapan baru", use_container_width=True, type="primary"):
            st.session_state.active_job_id = None
            st.session_state.admin_view_active = False
            st.session_state.pop("pending_new_cad_prompt", None)
            st.rerun()

        st.divider()

        # 2. Kumpulan Skills (4 preset, multi-select checklist)
        st.markdown("**Skills**")
        prev_ids: List[str] = list(st.session_state.selected_skill_ids)
        checked_ids: List[str] = []
        for skill in SKILLS:
            if st.checkbox(
                skill.name,
                value=(skill.id in prev_ids),
                key=f"skill_chk_{skill.id}",
            ):
                checked_ids.append(skill.id)

        # Keep "Chat Bebas" mutually exclusive with specific workflow skills
        if "general" in checked_ids and "general" not in prev_ids:
            new_ids = ["general"]
        elif any(sid != "general" for sid in checked_ids):
            new_ids = [sid for sid in checked_ids if sid != "general"]
        else:
            new_ids = ["general"]

        if new_ids != prev_ids:
            st.session_state.selected_skill_ids = new_ids
            st.rerun()

        active_skills = get_skills_by_ids(st.session_state.selected_skill_ids)
        for sk in active_skills:
            if sk.id != "general":
                st.caption(f"• **{sk.name}:** {sk.description}")

        # 3. Daftar Percakapan dalam Sesi Login Ini (Ephemeral per Session)
        session_ids: List[str] = st.session_state.get("session_job_ids", [])
        if session_ids:
            st.divider()
            st.markdown("**💬 Percakapan Sesi Ini**")
            for jid in session_ids:
                job_info = db.get_cad_job(jid)
                if not job_info:
                    continue
                status = job_info.get("status", "queued")
                icon = {
                    "completed": "✅",
                    "processing": "⚙️",
                    "approved": "⚙️",
                    "queued": "⏳",
                    "waiting_offline": "⏳",
                    "failed": "❌",
                }.get(status, "💬")

                raw_prompt = _strip_model_tag((job_info.get("user_prompt") or "").strip())
                if "[INSTRUKSI USER]:" in raw_prompt:
                    raw_prompt = raw_prompt.split("[INSTRUKSI USER]:")[-1].strip()
                first_line = raw_prompt.splitlines()[0] if raw_prompt else "Percakapan CAD"
                short_title = first_line[:28] + ("..." if len(first_line) > 28 else "")

                is_current = (st.session_state.get("active_job_id") == jid) and not st.session_state.get("admin_view_active")
                if st.button(
                    f"{icon} {short_title}",
                    key=f"sess_chat_{jid}",
                    use_container_width=True,
                    type="primary" if is_current else "secondary",
                ):
                    st.session_state.active_job_id = jid
                    st.session_state.admin_view_active = False
                    st.rerun()

        st.divider()

        # 3. Minimal Footer: User name, Admin access (if needed), Logout
        requester = st.session_state.get("requester_name", "Tim Internal")
        st.caption(f"👤 {requester}")

        if st.session_state.get("is_admin"):
            toggle_label = (
                "💬 Kembali ke Chat"
                if st.session_state.get("admin_view_active")
                else "🛠️ Buka Antrean Admin"
            )
            if st.button(toggle_label, use_container_width=True):
                st.session_state.admin_view_active = not st.session_state.admin_view_active
                st.rerun()
        else:
            with st.expander("⚙️ Admin", expanded=False):
                with st.form("sidebar_admin_login"):
                    adm_pw = st.text_input("Admin Password", type="password", label_visibility="collapsed", placeholder="Admin password")
                    if st.form_submit_button("Masuk Admin", use_container_width=True):
                        if verify_admin_password(adm_pw):
                            st.session_state.is_admin = True
                            st.session_state.admin_view_active = True
                            st.rerun()
                        else:
                            st.error("Password salah.")

        if st.button("🚪 Keluar", use_container_width=True):
            logout_user()


def _upload_raw_files(
    db: CADPortalDatabase,
    raw_files: List[tuple[str, bytes]],
    requester_name: str,
) -> tuple[str, List[str]]:
    """
    Upload `(filename, bytes)` pairs under `jobs/<requester_name>/input/` and `refs/`
    with a timestamp prefix so files in the same user folder never overwrite each other.
    """
    user_folder = get_requester_folder_name(requester_name)
    ts_prefix = format_timestamp_stem()
    dwg_storage_path = ""
    ref_storage_paths: List[str] = []

    for orig_name, content in raw_files or []:
        safe_name = _sanitize_filename(orig_name)
        ext = Path(safe_name).suffix.lower()
        if ext in (".dwg", ".dxf") and not dwg_storage_path:
            storage_key = f"jobs/{user_folder}/input/{ts_prefix}_{safe_name}"
            db.upload_file(content, storage_key)
            dwg_storage_path = storage_key
        else:
            storage_key = f"jobs/{user_folder}/refs/{ts_prefix}_{safe_name}"
            db.upload_file(content, storage_key)
            ref_storage_paths.append(storage_key)

    return dwg_storage_path or "NEW_DRAWING", ref_storage_paths


def _process_uploaded_files(
    db: CADPortalDatabase,
    uploaded_files: List[Any],
    requester_name: str,
) -> tuple[str, List[str]]:
    raw_files = [(f.name, f.getvalue()) for f in (uploaded_files or [])]
    return _upload_raw_files(db, raw_files, requester_name)


def _build_combined_instruction(
    skill_ids: List[str],
    user_prompt: str,
    kop_block: str = "",
    model_name: str = "",
    execution_mode: str = "extra_time",
) -> str:
    """Combine execution mode tag, per-job model tag, skill instructions, and Kop data."""
    parts: List[str] = []
    if execution_mode.strip():
        parts.append(f"[EXEC_MODE: {execution_mode.strip()}]")
    if model_name.strip():
        parts.append(f"[MODEL: {model_name.strip()}]")
    parts.extend(s.system_instruction for s in get_skills_by_ids(skill_ids) if s.system_instruction)
    if kop_block:
        parts.append(kop_block)
    header = "\n\n".join(parts)
    clean_prompt = user_prompt.strip()
    return f"{header}\n\n[INSTRUKSI USER]:\n{clean_prompt}" if header else clean_prompt


def _extract_header_tag(raw_prompt: str, prefix: str) -> Optional[str]:
    for line in raw_prompt.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(prefix) and s.endswith("]"):
            val = s[len(prefix):-1].strip()
            return val or None
        if not s.startswith("["):
            break
    return None


def _extract_job_exec_mode(job: Dict[str, Any], fallback: str = "extra_time") -> str:
    """Read `execution_mode` from job field or `[EXEC_MODE: ...]` header tag."""
    direct = (job.get("execution_mode") or "").strip()
    if direct in EXECUTION_MODES:
        return direct
    parsed = _extract_header_tag(job.get("user_prompt") or "", "[EXEC_MODE:")
    return parsed if parsed in EXECUTION_MODES else fallback


def _extract_job_model(job: Dict[str, Any], fallback: str) -> str:
    """Read the job-specific `[MODEL: ...]` tag so parallel tabs never overwrite each other."""
    parsed = _extract_header_tag(job.get("user_prompt") or "", "[MODEL:")
    return parsed or fallback


def _strip_model_tag(text: str) -> str:
    """Keep user chat bubbles clean by hiding internal `[EXEC_MODE: ...]`, `[MODEL: ...]`, and skill headers."""
    if "[INSTRUKSI USER]:" in text:
        return text.split("[INSTRUKSI USER]:", 1)[-1].strip()
    lines = text.lstrip().splitlines()
    while lines:
        first = lines[0].strip()
        if (first.startswith("[EXEC_MODE:") or first.startswith("[MODEL:")) and first.endswith("]"):
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
        else:
            break
    return "\n".join(lines).strip() if lines else ""


def _render_temporary_downloads(db: CADPortalDatabase, job: Dict[str, Any]) -> None:
    """Render temporary download buttons for finished .dwg and .pdf outputs."""
    output_dwg_path = job.get("output_dwg_url")
    output_pdf_path = job.get("output_pdf_url")
    if not output_dwg_path and not output_pdf_path:
        return

    st.warning(
        "⚠️ **File bersifat sementara (temporary).** Mohon segera download sekarang — "
        "jika halaman ditutup atau login ulang, riwayat & file tidak disimpan."
    )

    col_dwg, col_pdf = st.columns(2)
    with col_dwg:
        if output_dwg_path:
            dwg_bytes = db.download_file(output_dwg_path)
            dwg_name = Path(output_dwg_path).name
            if dwg_bytes:
                st.download_button(
                    label=f"⬇️ Download DWG ({dwg_name})",
                    data=dwg_bytes,
                    file_name=dwg_name,
                    mime="application/acad",
                    use_container_width=True,
                    type="primary",
                    key=f"dl_tmp_dwg_{job['id']}",
                )
    with col_pdf:
        if output_pdf_path:
            pdf_bytes = db.download_file(output_pdf_path)
            pdf_name = Path(output_pdf_path).name
            if pdf_bytes:
                st.download_button(
                    label=f"📄 Download PDF ({pdf_name})",
                    data=pdf_bytes,
                    file_name=pdf_name,
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                    key=f"dl_tmp_pdf_{job['id']}",
                )


def _render_pending_kop_prompt(db: CADPortalDatabase, requester_name: str) -> None:
    """Immediate assistant reply asking about Kop Drawing when 'Buat Gambar Baru' starts."""
    pending: Dict[str, Any] = st.session_state["pending_new_cad_prompt"]
    user_prompt: str = pending["prompt_text"]
    raw_files: List[tuple[str, bytes]] = pending["files"]
    skill_ids: List[str] = pending["skill_ids"]

    with st.chat_message("user"):
        st.markdown(user_prompt)
        for fname, _ in raw_files:
            st.caption(f"📎 `{fname}`")

    with st.chat_message("assistant"):
        st.markdown(
            "Apakah butuh **Kop Drawing**? Jika iya, silakan isi detail di bawah ini "
            "(jika tidak upload logo, kop akan disesuaikan tanpa logo; skala otomatis disesuaikan dengan gambar):"
        )

        use_kop = st.radio(
            "Pilihan Kop Drawing",
            options=["Ya, pakai Kop Drawing", "Tidak perlu Kop, langsung buat gambar"],
            horizontal=True,
            label_visibility="collapsed",
            key="kop_choice_radio",
        )

        logo_file = None
        company_name = ""
        company_addr = ""
        drawing_title = ""

        if use_kop == "Ya, pakai Kop Drawing":
            logo_file = st.file_uploader(
                "Drop Logo Perusahaan (opsional)",
                type=["png", "jpg", "jpeg", "bmp"],
                key="kop_logo_uploader",
            )
            company_name = st.text_input("Nama Perusahaan", key="kop_company_name", placeholder="Contoh: PT Jababeka Infrastruktur")
            company_addr = st.text_area("Alamat", height=68, key="kop_company_address", placeholder="Alamat lengkap perusahaan...")
            drawing_title = st.text_input("Judul Gambar", key="kop_drawing_title", placeholder="Contoh: Tampak Depan Bangunan TPLB3")

        col_go, col_cancel = st.columns([2, 1])
        with col_go:
            if st.button("🚀 Lanjutkan & Proses Gambar", type="primary", use_container_width=True):
                with st.spinner("Mengirim ke CADCRAFT AI..."):
                    all_raw_files = list(raw_files)
                    logo_note = "Tidak ada logo (buat layout kop tanpa logo)"
                    if use_kop == "Ya, pakai Kop Drawing" and logo_file is not None:
                        logo_safe = _sanitize_filename(logo_file.name)
                        all_raw_files.append((f"LOGO_{logo_safe}", logo_file.getvalue()))
                        logo_note = f"Ada file logo perusahaan (lampiran dengan awalan LOGO_{logo_safe}) — sisipkan di area logo kop menggunakan `imageattach`"

                    dwg_path, ref_paths = _upload_raw_files(db, all_raw_files, requester_name)

                    if use_kop == "Ya, pakai Kop Drawing":
                        kop_block = (
                            "[KOP DRAWING]\n"
                            "User membutuhkan Kop Drawing dengan detail berikut:\n"
                            f"- Logo Perusahaan: {logo_note}\n"
                            f"- Nama Perusahaan: {company_name.strip() or '-'}\n"
                            f"- Alamat: {company_addr.strip() or '-'}\n"
                            f"- Judul Gambar: {drawing_title.strip() or '-'}\n"
                            "- Skala: Hitung dan cantumkan skala yang sesuai dengan ukuran gambar yang dibuat.\n"
                            "- Digambar: - (cantumkan kolom `Digambar` dalam keadaan kosongan `-`, jangan diisi kecuali ada request)\n"
                            "- Disahkan: - (jangan pakai kolom `Pemohon`, gunakan `Disahkan` dalam keadaan kosongan `-`)\n"
                            "- Tanggal: - (cantumkan kolom `Tanggal` dalam keadaan kosongan `-`, jangan diisi kecuali ada request)"
                        )
                    else:
                        kop_block = "[KOP DRAWING]\nUser tidak membutuhkan Kop Drawing; langsung buat gambar kerja sesuai instruksi."

                    chosen_model = pending.get("model_name") or st.session_state.get("selected_model") or get_current_cli_model()
                    exec_mode = pending.get("execution_mode", st.session_state.get("selected_exec_mode", "extra_time"))
                    full_instruction = _build_combined_instruction(
                        skill_ids,
                        user_prompt,
                        kop_block=kop_block,
                        model_name=chosen_model,
                        execution_mode=exec_mode,
                    )
                    created_job = db.create_cad_job(
                        requester_name=requester_name,
                        user_prompt=full_instruction,
                        input_dwg_url=dwg_path,
                        reference_img_urls=ref_paths,
                    )

                st.session_state.pop("pending_new_cad_prompt", None)
                new_jid = created_job["id"]
                if "session_job_ids" not in st.session_state:
                    st.session_state.session_job_ids = []
                if new_jid not in st.session_state.session_job_ids:
                    st.session_state.session_job_ids.insert(0, new_jid)
                st.session_state.active_job_id = new_jid
                st.rerun()

        with col_cancel:
            if st.button("Batal", use_container_width=True):
                st.session_state.pop("pending_new_cad_prompt", None)
                st.rerun()


def render_chat_interface(db: CADPortalDatabase, worker_status: Dict[str, Any]) -> None:
    """Render minimalist Gemini-style chat interface."""
    _inject_clean_chat_css()

    active_job_id: Optional[str] = st.session_state.get("active_job_id")
    selected_skill_ids: List[str] = st.session_state.get("selected_skill_ids", [SKILLS[0].id])
    active_skills = get_skills_by_ids(selected_skill_ids)
    requester_name = st.session_state.get("requester_name", "").strip() or "Rekan"

    # =========================================================================
    # CASE A0: PENDING KOP DRAWING FORM FOR "BUAT GAMBAR BARU"
    # =========================================================================
    if not active_job_id and "pending_new_cad_prompt" in st.session_state:
        _render_pending_kop_prompt(db, requester_name)
        return

    # =========================================================================
    # CASE A: ACTIVE CONVERSATION / PROCESSING / COMPLETED
    # =========================================================================
    if active_job_id:
        job = db.get_cad_job(active_job_id)
        if not job:
            st.session_state.active_job_id = None
            st.rerun()
            return

        messages = db.get_chat_messages(active_job_id)
        for msg in messages:
            role = "user" if msg.get("sender") == "user" else "assistant"
            with st.chat_message(role):
                st.markdown(_strip_model_tag(msg.get("message", "")))
                attachments = msg.get("attachments") or []
                for att in attachments:
                    att_path = att.get("path", "")
                    if not att_path or att_path == "NEW_DRAWING":
                        continue
                    fname = Path(att_path).name
                    if att.get("type") == "image" and Path(fname).suffix.lower() in (".png", ".jpg", ".jpeg"):
                        img_bytes = db.download_file(att_path)
                        if img_bytes:
                            st.image(img_bytes, caption=fname, width=280)
                    elif att.get("type") in ("dwg", "image"):
                        st.caption(f"📎 `{fname}`")

        status = job.get("status", "queued")
        job_exec_mode = _extract_job_exec_mode(
            job, st.session_state.get("selected_exec_mode", "extra_time")
        )
        if job_exec_mode == "fast_flash":
            active_model_name = "Fast-Flash (Auto: Hydrogen Med → Low)"
        elif job_exec_mode == "fast_optimized":
            active_model_name = "Fast-Optimized (Auto: Argon/Hydrogen High → Low → Verify)"
        else:
            active_model_name = _extract_job_model(
                job, st.session_state.get("selected_model") or get_current_cli_model()
            )

        # Animated processing status while waiting/running
        if status in ("waiting_offline", "queued", "approved", "processing"):
            status_text = {
                "waiting_offline": "⏳ Laptop Ilham sedang offline — request Anda aman di antrean...",
                "queued": f"📥 Request diterima — menyiapkan {active_model_name}...",
                "approved": f"⚙️ Memulai CADCRAFT AI ({active_model_name})...",
                "processing": f"⚙️ Sedang memproses gambar di CADCRAFT menggunakan {active_model_name}...",
            }.get(status, "⚙️ Sedang memproses...")

            with st.chat_message("assistant"):
                with st.status(status_text, expanded=True, state="running"):
                    st.write(f"Model aktif: **{active_model_name}** · Terhubung ke **MCP CADCraft (Port 7980)**.")
                    st.caption("Halaman akan memperbarui hasil secara otomatis begitu file selesai dibuat.")

            time.sleep(4)
            st.rerun()
            return

        # Completed: Auto-send temporary downloadable files
        if status == "completed":
            with st.chat_message("assistant"):
                _render_temporary_downloads(db, job)

        # Follow-up prompt + optional file drop
        with st.expander("📎 Lampirkan file tambahan untuk revisi (opsional)", expanded=False):
            followup_files = st.file_uploader(
                "Lampirkan file (.dwg, .dxf, .jpg, .png, .pdf)",
                type=SUPPORTED_FILE_TYPES,
                accept_multiple_files=True,
                key=f"followup_files_{active_job_id}",
                label_visibility="collapsed",
            )

        followup_prompt = st.chat_input("Tulis instruksi lanjutan atau revisi...")
        if followup_prompt:
            new_dwg_url: Optional[str] = None
            new_ref_urls: List[str] = []
            if followup_files:
                dwg_res, ref_res = _process_uploaded_files(
                    db,
                    followup_files,
                    job.get("requester_name", "user"),
                )
                if dwg_res != "NEW_DRAWING":
                    new_dwg_url = dwg_res
                new_ref_urls = ref_res

            db.add_followup_revision(
                job_id=active_job_id,
                user_prompt=followup_prompt,
                new_dwg_url=new_dwg_url,
                new_ref_img_urls=new_ref_urls,
            )
            st.rerun()
        return

    # =========================================================================
    # CASE B: NEW CHAT (MINIMALIST GEMINI-STYLE HOME)
    # =========================================================================
    first_name = requester_name.split("-")[0].strip().split()[0]

    st.markdown(
        f"""
        <div class="cadcraft-greeting">
            <h1>Halo, {first_name}</h1>
            <p>Sebaiknya kita mulai dari mana?</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    non_general = [s for s in active_skills if s.id != "general"]
    if non_general:
        badges_html = " ".join(
            f'<span class="skill-badge"><b>{s.name}</b></span>' for s in non_general
        )
        st.markdown(f'<div style="text-align:center;">{badges_html}</div>', unsafe_allow_html=True)

    uploaded_files = st.file_uploader(
        "Drag & drop file di sini (.dwg, .dxf, .jpg, .png, .jpeg, .pdf)",
        type=SUPPORTED_FILE_TYPES,
        accept_multiple_files=True,
        key="main_dropzone_uploader",
    )

    # Execution Mode + Model Selector row right below the upload box
    available_models = get_available_models(db)
    if "selected_model" not in st.session_state or st.session_state.selected_model not in available_models:
        st.session_state.selected_model = get_current_cli_model()
    if "selected_exec_mode" not in st.session_state or st.session_state.selected_exec_mode not in EXECUTION_MODES:
        st.session_state.selected_exec_mode = "extra_time"

    current_model = st.session_state.selected_model
    default_idx = available_models.index(current_model) if current_model in available_models else 0
    current_exec_mode = st.session_state.selected_exec_mode

    col_mode, col_model = st.columns([1.8, 2.2])
    with col_mode:
        chosen_exec_mode = st.selectbox(
            "Execution Mode",
            options=EXECUTION_MODES,
            index=EXECUTION_MODES.index(current_exec_mode),
            format_func=lambda m: EXECUTION_MODE_LABELS.get(m, m),
            label_visibility="collapsed",
            key="chatbox_exec_mode_selector",
        )
        if chosen_exec_mode != current_exec_mode:
            st.session_state.selected_exec_mode = chosen_exec_mode
            st.rerun()

    with col_model:
        if current_exec_mode == "fast_flash":
            st.selectbox(
                "Model AI",
                options=["Auto: Hydrogen Med → Low"],
                index=0,
                disabled=True,
                label_visibility="collapsed",
                key="chatbox_model_locked_ff",
            )
        elif current_exec_mode == "fast_optimized":
            st.selectbox(
                "Model AI",
                options=["Auto: Argon/Hydrogen High → Low → Verify"],
                index=0,
                disabled=True,
                label_visibility="collapsed",
                key="chatbox_model_locked_fo",
            )
        else:
            chosen_model = st.selectbox(
                "Model AI (Antigravity CLI)",
                options=available_models,
                index=default_idx,
                format_func=get_model_display_label,
                label_visibility="collapsed",
                key="chatbox_model_selector",
            )
            if chosen_model != current_model:
                st.session_state.selected_model = chosen_model

    prompt_text = st.chat_input(active_skills[0].placeholder)

    if prompt_text:
        if "new_cad" in selected_skill_ids:
            st.session_state.pending_new_cad_prompt = {
                "prompt_text": prompt_text.strip(),
                "files": [(f.name, f.getvalue()) for f in (uploaded_files or [])],
                "skill_ids": list(selected_skill_ids),
                "model_name": st.session_state.selected_model,
                "execution_mode": st.session_state.selected_exec_mode,
            }
            st.rerun()

        with st.spinner("Mengirim ke CADCRAFT AI..."):
            dwg_path, ref_paths = _process_uploaded_files(db, uploaded_files or [], requester_name)
            full_instruction = _build_combined_instruction(
                selected_skill_ids,
                prompt_text,
                model_name=st.session_state.selected_model,
                execution_mode=st.session_state.selected_exec_mode,
            )

            created_job = db.create_cad_job(
                requester_name=requester_name,
                user_prompt=full_instruction,
                input_dwg_url=dwg_path,
                reference_img_urls=ref_paths,
            )

        new_jid = created_job["id"]
        if "session_job_ids" not in st.session_state:
            st.session_state.session_job_ids = []
        if new_jid not in st.session_state.session_job_ids:
            st.session_state.session_job_ids.insert(0, new_jid)
        st.session_state.active_job_id = new_jid
        st.rerun()
