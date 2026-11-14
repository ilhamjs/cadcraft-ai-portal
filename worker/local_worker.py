"""
Local Laptop Worker (`worker/local_worker.py`).
Runs on Ilham's laptop alongside CADCraft (port 7980) and Antigravity:
1. Sends heartbeat every 30 seconds to Supabase `worker_status` table.
2. Polls `cad_jobs` sequentially (1-by-1) for jobs with status == 'approved'.
3. Downloads input `.dwg` and reference images to `C:\\CADCraft_Workspace\\job_<id>\\`.
4. Triggers the Antigravity/Gemini CLI or UI Bridge (`worker/agent_bridge.py`).
5. Uploads `fixed_<name>.dwg` and `preview_<name>.pdf` back to Supabase Storage and marks job `completed`.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from src.supabase_client import (
    CADPortalDatabase,
    format_timestamp_stem,
    get_db,
    get_requester_folder_name,
)
from worker.agent_bridge import ensure_cadcraft_running, execute_cad_job_with_agent


class LocalCADWorker:
    def __init__(self, db: CADPortalDatabase) -> None:
        self.db = db
        self.workspace_root = Path(
            os.getenv("LOCAL_WORKSPACE_DIR", r"C:\CADCraft_Workspace")
        )
        self.heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "30"))
        self.poll_interval = int(os.getenv("POLL_INTERVAL_SEC", "5"))
        self.current_job_id: Optional[str] = None
        self._stop_event = threading.Event()
        self._last_hb_ts = 0.0

    def send_heartbeat_if_due(self, force: bool = False) -> None:
        now = time.time()
        if force or (now - self._last_hb_ts >= self.heartbeat_interval):
            self.db.update_worker_heartbeat(
                is_online=True,
                current_job_id=self.current_job_id,
            )
            self._last_hb_ts = now

    def _heartbeat_thread_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.send_heartbeat_if_due(force=True)
            except Exception as exc:
                print(f"[HEARTBEAT WARN] {exc}")
            self._stop_event.wait(self.heartbeat_interval)

    def process_single_job(self, job: dict) -> None:
        job_id = job["id"]
        requester = job.get("requester_name", "Tim Internal")
        raw_prompt = job.get("user_prompt", "")
        job_model_name: Optional[str] = None
        user_prompt = raw_prompt
        if raw_prompt.startswith("[MODEL:"):
            first_nl = raw_prompt.find("\n")
            header_line = raw_prompt[:first_nl] if first_nl != -1 else raw_prompt
            if header_line.endswith("]"):
                job_model_name = header_line[len("[MODEL:"):-1].strip()
                user_prompt = raw_prompt[first_nl + 1:].lstrip() if first_nl != -1 else ""

        input_dwg_storage_path = job.get("input_dwg_url", "")
        ref_storage_paths: List[str] = job.get("reference_img_urls") or []

        self.current_job_id = job_id
        self.send_heartbeat_if_due(force=True)

        print(f"\n[WORKER] >>> Memulai Pemrosesan Job {job_id} (Pemohon: {requester}) <<<")
        self.db.update_job_status(job_id, "processing")
        self.db.add_chat_message(
            job_id=job_id,
            sender="assistant",
            message="⚙️ Laptop worker telah mengunduh file DWG Anda dan sedang memproses modifikasi di CADCraft...",
        )

        # Prepare isolated local workspace directory per job_id so parallel/concurrent jobs never collide
        user_folder = get_requester_folder_name(requester)
        job_dir = self.workspace_root / user_folder / f"job_{job_id[:8]}"
        input_dir = job_dir / "input"
        refs_dir = job_dir / "refs"
        output_dir = job_dir / "output"
        job_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        refs_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Download input .dwg (or prepare a new drawing if NEW_DRAWING)
        if input_dwg_storage_path and input_dwg_storage_path != "NEW_DRAWING":
            dwg_filename = Path(input_dwg_storage_path).name or "input.dwg"
            local_dwg_path = input_dir / dwg_filename
            dwg_bytes = self.db.download_file(input_dwg_storage_path)
            if not dwg_bytes:
                err_msg = f"Gagal mengunduh file input DWG dari storage: {input_dwg_storage_path}"
                print(f"[ERROR] {err_msg}")
                self.db.update_job_status(job_id, "failed", agent_response=err_msg)
                self.db.add_chat_message(job_id=job_id, sender="assistant", message=f"❌ {err_msg}")
                self.current_job_id = None
                return

            local_dwg_path.write_bytes(dwg_bytes)
            print(f"[WORKER] Input DWG tersimpan di: {local_dwg_path}")
        else:
            # Non-existent path signals agent_bridge to initialize a fresh `new_drawing`
            local_dwg_path = job_dir / f"__new_drawing_{job_id[:8]}__.dwg"
            print("[WORKER] Mode Buat CAD Baru (new_drawing kanvas baru).")

        # 2. Download reference images (.png / .jpg)
        local_ref_paths: List[Path] = []
        for idx, ref_path in enumerate(ref_storage_paths):
            ref_bytes = self.db.download_file(ref_path)
            if ref_bytes:
                ref_name = Path(ref_path).name or f"ref_{idx + 1}.png"
                local_ref = refs_dir / ref_name
                local_ref.write_bytes(ref_bytes)
                local_ref_paths.append(local_ref)
                print(f"[WORKER] Gambar referensi tersimpan di: {local_ref}")

        # 3. Execute AI Agent (CLI or UI Bridge + Output Watcher)
        result = execute_cad_job_with_agent(
            job_id=job_id,
            requester_name=requester,
            user_prompt=user_prompt,
            input_dwg_path=local_dwg_path,
            reference_image_paths=local_ref_paths,
            output_dir=output_dir,
            heartbeat_callback=lambda: self.send_heartbeat_if_due(force=False),
            model_name=job_model_name,
        )

        if not result.success:
            err_msg = result.error_message or "Gagal menjalankan CADCraft AI Agent."
            print(f"[WORKER ERROR] Job {job_id} gagal: {err_msg}")
            self.db.update_job_status(job_id, "failed", agent_response=err_msg)
            self.db.add_chat_message(
                job_id=job_id,
                sender="assistant",
                message=f"❌ Pemrosesan mengalami kendala: {err_msg}",
            )
            self.current_job_id = None
            return

        # 4. Upload finished .dwg and .pdf to `jobs/<nama_request>/output/<waktu_dan_tanggal>.dwg/.pdf`
        out_dwg_storage: Optional[str] = None
        out_pdf_storage: Optional[str] = None
        attachments = []
        saved_ts = format_timestamp_stem()

        if result.output_dwg_path and result.output_dwg_path.exists():
            out_dwg_storage = f"jobs/{user_folder}/output/{saved_ts}.dwg"
            print(f"[WORKER] Menyimpan hasil DWG ke: {out_dwg_storage}...")
            self.db.upload_file(result.output_dwg_path.read_bytes(), out_dwg_storage)
            attachments.append({"path": out_dwg_storage, "type": "dwg"})

            # Also copy any extracted OLE/attached images (`ole_img_*.png`) into the same output storage folder
            # so relative IMAGEATTACH paths in the DWG always work seamlessly.
            for img_p in output_dir.glob("ole_img_*.png"):
                if img_p.is_file():
                    self.db.upload_file(img_p.read_bytes(), f"jobs/{user_folder}/output/{img_p.name}")

        if result.output_pdf_path and result.output_pdf_path.exists():
            out_pdf_storage = f"jobs/{user_folder}/output/{saved_ts}.pdf"
            print(f"[WORKER] Menyimpan hasil PDF ke: {out_pdf_storage}...")
            self.db.upload_file(result.output_pdf_path.read_bytes(), out_pdf_storage)
            attachments.append({"path": out_pdf_storage, "type": "pdf"})

        # 5. Mark job as completed and notify user in chat thread
        self.db.update_job_status(
            job_id=job_id,
            status="completed",
            output_dwg_url=out_dwg_storage,
            output_pdf_url=out_pdf_storage,
            agent_response=result.summary_text,
        )
        self.db.add_chat_message(
            job_id=job_id,
            sender="assistant",
            message=result.summary_text,
            attachments=attachments,
        )

        print(f"[WORKER] <<< Job {job_id} SELESAI dan diunggah ke Portal! >>>\n")
        self.current_job_id = None
        self.send_heartbeat_if_due(force=True)

    def run_forever(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        status_info = self.db.get_worker_status()

        print("======================================================================")
        print("  CADCraft AI Agent - Local Laptop Worker (Ilham's Laptop)")
        print("======================================================================")
        print(f"  Backend Mode   : {status_info['backend_mode']}")
        print(f"  Workspace Dir  : {self.workspace_root}")
        print(f"  Heartbeat Intv : {self.heartbeat_interval} detik")
        print("======================================================================")

        ensure_cadcraft_running()

        hb_thread = threading.Thread(target=self._heartbeat_thread_loop, daemon=True)
        hb_thread.start()

        print("[WORKER] Status Laptop Ilham kini ONLINE. Menunggu job berstatus 'approved'...")
        try:
            while True:
                next_job = self.db.get_next_approved_job()
                if next_job:
                    self.process_single_job(next_job)
                else:
                    time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            print("\n[WORKER] Menghentikan worker dan mengubah status Laptop menjadi OFFLINE...")
            self._stop_event.set()
            self.db.update_worker_heartbeat(is_online=False, current_job_id=None)
            print("[WORKER] Selesai.")


if __name__ == "__main__":
    worker = LocalCADWorker(get_db())
    worker.run_forever()
