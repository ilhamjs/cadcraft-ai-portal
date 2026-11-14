"""
Admin Dashboard & Queue Controller (`src/ui_admin.py`).
Allows Ilham to inspect incoming CAD requests, preview reference sketches,
click "Accept & Run Job" or "Process All Queued Jobs", and perform Manual Override Uploads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

from src.auth import render_admin_unlock_widget
from src.supabase_client import (
    CADPortalDatabase,
    format_timestamp_stem,
    get_requester_folder_name,
)


def _render_job_card(db: CADPortalDatabase, job: Dict[str, Any]) -> None:
    """Render an expandable/actionable card for a single CAD job in the Admin panel."""
    jid = job["id"]
    status = job.get("status", "queued")
    requester = job.get("requester_name", "Unknown")
    created_at = (job.get("created_at") or "")[:19].replace("T", " ")
    input_dwg_url = job.get("input_dwg_url", "")
    dwg_name = Path(input_dwg_url).name if input_dwg_url else "drawing.dwg"
    ref_urls: List[str] = job.get("reference_img_urls") or []

    badge_map = {
        "waiting_offline": "🟠 WAITING (SAAT OFFLINE)",
        "queued": "🔵 QUEUED (MENUNGGU ACCEPT)",
        "approved": "🟣 APPROVED (ANTRIAN WORKER LOKAL)",
        "processing": "⚙️ PROCESSING (SEDANG DIKERJAKAN CADCRAFT)",
        "completed": "🟢 COMPLETED",
        "failed": "🔴 FAILED",
    }
    status_badge = badge_map.get(status, status.upper())

    with st.container(border=True):
        top_c1, top_c2 = st.columns([3.5, 1.5])
        with top_c1:
            st.markdown(f"### 📐 `{dwg_name}` — **{requester}**")
            st.caption(f"Job ID: `{jid}` · Waktu Request: `{created_at} UTC`")
        with top_c2:
            st.markdown(f"**Status:** {status_badge}")

        st.markdown("**📝 Instruksi User:**")
        st.info(job.get("user_prompt", ""))

        # Download Original DWG & Preview Reference Images
        file_col1, file_col2 = st.columns([1.4, 2.6])
        with file_col1:
            dwg_bytes = db.download_file(input_dwg_url)
            if dwg_bytes:
                st.download_button(
                    label=f"⬇️ Download Input DWG ({dwg_name})",
                    data=dwg_bytes,
                    file_name=dwg_name,
                    mime="application/acad",
                    key=f"admin_dl_in_{jid}",
                    use_container_width=True,
                )
            else:
                st.caption(f"Storage Path: `{input_dwg_url}`")

        with file_col2:
            if ref_urls:
                st.caption(f"🖼️ Lampiran Gambar Referensi ({len(ref_urls)} file):")
                img_cols = st.columns(min(len(ref_urls), 3))
                for idx, r_url in enumerate(ref_urls):
                    r_bytes = db.download_file(r_url)
                    if r_bytes:
                        img_cols[idx % len(img_cols)].image(
                            r_bytes,
                            caption=Path(r_url).name,
                            use_container_width=True,
                        )
            else:
                st.caption("Tidak ada lampiran gambar referensi (`.png`/`.jpg`).")

        st.divider()

        # Action Controls based on status
        act_c1, act_c2, act_c3 = st.columns(3)

        with act_c1:
            if status in ("waiting_offline", "queued", "failed"):
                if st.button(
                    "✅ Accept & Run Job (Kirim ke Local Worker)",
                    key=f"btn_accept_{jid}",
                    type="primary",
                    use_container_width=True,
                ):
                    db.update_job_status(jid, "approved")
                    db.add_chat_message(
                        job_id=jid,
                        sender="assistant",
                        message="⚙️ Request Anda telah di-Accept oleh Ilham! Sedang diproses oleh CADCraft AI Agent...",
                    )
                    st.success(f"Job `{jid[:8]}` berstatus APPROVED dan akan dieksekusi oleh `local_worker.py`.")
                    st.rerun()
            elif status in ("approved", "processing"):
                if st.button(
                    "🔄 Kembalikan ke Antrean (Re-Queue)",
                    key=f"btn_requeue_{jid}",
                    use_container_width=True,
                ):
                    db.update_job_status(jid, "queued")
                    st.rerun()

        with act_c2:
            if status in ("approved", "processing"):
                if st.button(
                    "⚡ Trigger Ulang (Set Approved)",
                    key=f"btn_reapprove_{jid}",
                    use_container_width=True,
                ):
                    db.update_job_status(jid, "approved")
                    st.rerun()

        with act_c3:
            if status != "completed":
                if st.button(
                    "❌ Tandai Gagal / Tolak",
                    key=f"btn_fail_{jid}",
                    use_container_width=True,
                ):
                    db.update_job_status(
                        jid,
                        "failed",
                        agent_response="Request ditolak atau membutuhkan klarifikasi lebih lanjut dari pemohon.",
                    )
                    db.add_chat_message(
                        job_id=jid,
                        sender="assistant",
                        message="❌ Maaf, request ini membutuhkan klarifikasi tambahan. Silakan kirim revisi instruksi pada kolom chat.",
                    )
                    st.rerun()

        # Manual Override Upload Section (PRD 3.3)
        with st.expander("🛠️ Manual Override Upload (Upload Hasil DWG & PDF Secara Manual)", expanded=False):
            st.caption(
                "Gunakan fitur ini jika gambar terlalu kompleks dan Anda sudah merapikannya langsung "
                "di CADCraft, lalu ingin mengirim hasilnya langsung ke ruang chat pemohon."
            )
            with st.form(f"manual_override_form_{jid}"):
                m_col1, m_col2 = st.columns(2)
                with m_col1:
                    manual_dwg = st.file_uploader(
                        "Upload File DWG Hasil (`fixed_*.dwg`)",
                        type=["dwg", "dxf"],
                        key=f"man_dwg_{jid}",
                    )
                with m_col2:
                    manual_pdf = st.file_uploader(
                        "Upload File PDF Preview (`preview_*.pdf`)",
                        type=["pdf"],
                        key=f"man_pdf_{jid}",
                    )
                manual_summary = st.text_area(
                    "Catatan / Ringkasan Perubahan untuk Pemohon",
                    value="✅ Gambar DWG telah selesai dirapikan dan diverifikasi oleh Admin menggunakan CADCraft. Silakan unduh file DWG dan PDF terlampir.",
                    key=f"man_sum_{jid}",
                )
                submit_manual = st.form_submit_button(
                    "📤 Upload & Selesaikan Tiket (Mark Completed)",
                    type="primary",
                )

                if submit_manual:
                    if not manual_dwg and not manual_pdf:
                        st.error("Mohon pilih minimal file DWG atau PDF hasil terlebih dahulu.")
                    else:
                        out_dwg_path = job.get("output_dwg_url")
                        out_pdf_path = job.get("output_pdf_url")

                        user_folder = get_requester_folder_name(requester)
                        ts_stem = format_timestamp_stem()
                        if manual_dwg:
                            ext_dwg = Path(manual_dwg.name).suffix.lower() or ".dwg"
                            out_dwg_path = f"jobs/{user_folder}/output/{ts_stem}{ext_dwg}"
                            db.upload_file(manual_dwg.getvalue(), out_dwg_path)
                        if manual_pdf:
                            out_pdf_path = f"jobs/{user_folder}/output/{ts_stem}.pdf"
                            db.upload_file(manual_pdf.getvalue(), out_pdf_path)

                        db.update_job_status(
                            job_id=jid,
                            status="completed",
                            output_dwg_url=out_dwg_path,
                            output_pdf_url=out_pdf_path,
                            agent_response=manual_summary.strip(),
                        )
                        db.add_chat_message(
                            job_id=jid,
                            sender="assistant",
                            message=f"✅ **Selesai!** {manual_summary.strip()}",
                        )
                        st.success("Tiket berhasil diselesaikan secara manual!")
                        st.rerun()


def render_admin_dashboard(db: CADPortalDatabase, worker_status: Dict[str, Any]) -> None:
    """Render the Admin Queue Controller tab."""
    st.subheader("🛠️ Admin Dashboard & Local Worker Queue Controller")

    if not render_admin_unlock_widget():
        return

    # 1. Worker Status & Global Batch Controls
    all_jobs = db.list_cad_jobs()
    waiting_jobs = [j for j in all_jobs if j.get("status") in ("waiting_offline", "queued")]
    active_jobs = [j for j in all_jobs if j.get("status") in ("approved", "processing")]
    completed_jobs = [j for j in all_jobs if j.get("status") == "completed"]
    failed_jobs = [j for j in all_jobs if j.get("status") == "failed"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("⏳ Menunggu Accept", len(waiting_jobs))
    m2.metric("⚙️ Sedang Berjalan / Approved", len(active_jobs))
    m3.metric("✅ Selesai (Completed)", len(completed_jobs))
    m4.metric(
        "💻 Status Worker Laptop",
        "ONLINE" if worker_status["is_online"] else "OFFLINE",
        delta=f"{worker_status['seconds_since_heartbeat']}s ago"
        if worker_status["seconds_since_heartbeat"] < 86400
        else "Belum aktif",
    )

    ctrl_c1, ctrl_c2, ctrl_c3 = st.columns([2, 1.5, 1])
    with ctrl_c1:
        if st.button(
            f"🚀 Process All Queued Jobs ({len(waiting_jobs)} Tiket)",
            type="primary",
            use_container_width=True,
            disabled=(len(waiting_jobs) == 0),
        ):
            approved_count = db.approve_all_queued_jobs()
            st.success(
                f"{approved_count} tiket telah diubah menjadi APPROVED! "
                "`local_worker.py` akan memprosesnya secara berurutan (1 per 1)."
            )
            st.rerun()

    with ctrl_c2:
        if st.button("🟢 Simulasikan Heartbeat Laptop Online (90s)", use_container_width=True):
            db.update_worker_heartbeat(is_online=True)
            st.toast("Heartbeat Laptop Ilham diperbarui menjadi ONLINE!")
            st.rerun()

    with ctrl_c3:
        if st.button("🔄 Refresh Antrean", use_container_width=True):
            st.rerun()

    st.divider()

    # 2. Sub-tabs for Queue Management
    tab_waiting, tab_active, tab_done = st.tabs(
        [
            f"📥 Antrean Masuk ({len(waiting_jobs)})",
            f"⚙️ Approved / Sedang Diproses ({len(active_jobs)})",
            f"✅ Riwayat Selesai & Gagal ({len(completed_jobs) + len(failed_jobs)})",
        ]
    )

    with tab_waiting:
        if not waiting_jobs:
            st.info("Tidak ada antrean request baru yang menunggu persetujuan.")
        else:
            for job in waiting_jobs:
                _render_job_card(db, job)

    with tab_active:
        if not active_jobs:
            st.info("Tidak ada job yang sedang berstatus `approved` atau `processing`.")
        else:
            st.caption(
                "Pastikan `scripts/run_quick_worker.bat` atau `python worker/local_worker.py` "
                "sedang berjalan di laptop Anda untuk mengeksekusi antrean di bawah ini."
            )
            for job in active_jobs:
                _render_job_card(db, job)

    with tab_done:
        done_and_failed = completed_jobs + failed_jobs
        if not done_and_failed:
            st.info("Belum ada riwayat tiket yang selesai.")
        else:
            for job in done_and_failed:
                _render_job_card(db, job)
