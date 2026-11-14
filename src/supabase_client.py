"""
Supabase Service Layer (Database CRUD, Storage Upload/Download, & Worker Heartbeat).
Includes an automatic Local Fallback adapter when Supabase credentials are not yet configured,
allowing immediate local testing between Streamlit Web UI and local_worker.py.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

HEARTBEAT_THRESHOLD_SEC = int(os.getenv("WORKER_ONLINE_THRESHOLD_SEC", "90"))
DEFAULT_BUCKET = os.getenv("SUPABASE_BUCKET", "cad-files")

OFFLINE_REPLY_MESSAGE = (
    "Maaf, laptop Ilham saat ini sedang offline/meeting. Request, file DWG, "
    "dan instruksi Anda sudah masuk ke dalam antrean aman dan akan otomatis "
    "diproses begitu laptop aktif."
)

ONLINE_REPLY_MESSAGE = (
    "Request diterima! Mohon ditunggu, file DWG Anda sedang dalam antrean pemrosesan."
)


def _get_config_value(key: str, default: str = "") -> str:
    """Read configuration from environment variables or Streamlit secrets."""
    val = os.getenv(key, "")
    if val:
        return val
    try:
        import streamlit as st

        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return default


def _parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 timestamp string into timezone-aware UTC datetime."""
    if not dt_str:
        return None
    try:
        cleaned = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _guess_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".dwg":
        return "application/acad"
    if ext == ".dxf":
        return "application/dxf"
    if ext == ".pdf":
        return "application/pdf"
    if ext in (".png", ".jpg", ".jpeg"):
        return f"image/{'jpeg' if ext in ('.jpg', '.jpeg') else 'png'}"
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def get_requester_folder_name(requester_name: str) -> str:
    """
    Normalize requester_name into a clean, consistent folder name under `jobs/<requester_folder>`.
    Reuses the existing folder if the same user logs in multiple times with the same name.
    """
    raw = (requester_name or "user").strip()
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if c in forbidden else c for c in raw).strip(" .")
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        cleaned = "user"

    # Check if a folder with the same name (case-insensitive) already exists in local storage
    jobs_dir = PROJECT_ROOT / ".local_data" / "storage" / DEFAULT_BUCKET / "jobs"
    if jobs_dir.exists():
        for existing in jobs_dir.iterdir():
            if existing.is_dir() and existing.name.lower() == cleaned.lower():
                return existing.name
    return cleaned


def format_timestamp_stem(dt: Optional[datetime] = None) -> str:
    """
    Return local time and date string formatted as `HH-MM-SS_DD-MM-YYYY`
    (waktu dan tanggal ketika file disimpan).
    """
    now_local = (dt or datetime.now()).astimezone()
    return now_local.strftime("%H-%M-%S_%d-%m-%Y")


class LocalFallbackStore:
    """
    Thread-safe & process-safe local JSON + filesystem store used ONLY when
    SUPABASE_URL / SUPABASE_KEY are not configured yet.
    Mirrors the exact schema of worker_status, cad_jobs, chat_messages, and cad-files bucket.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.db_file = base_dir / "local_db.json"
        self.lock_file = base_dir / "local_db.lock"
        self.storage_dir = base_dir / "storage" / DEFAULT_BUCKET
        self._lock = threading.Lock()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.db_file.exists():
            self._save_db(
                {
                    "worker_status": {
                        "id": 1,
                        "is_online": False,
                        "last_heartbeat": "2020-01-01T00:00:00+00:00",
                        "current_job_id": None,
                    },
                    "cad_jobs": [],
                    "chat_messages": [],
                }
            )

    @contextmanager
    def _cross_process_lock(self) -> Iterator[None]:
        """Serialize local_db.json reads/writes across Streamlit and local_worker processes."""
        with self._lock:
            fh = open(self.lock_file, "a+", encoding="utf-8")
            try:
                if os.name == "nt":
                    import msvcrt

                    for _ in range(100):
                        try:
                            fh.seek(0)
                            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            time.sleep(0.05)
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    try:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                fh.close()

    def _load_db(self) -> Dict[str, Any]:
        with self._cross_process_lock():
            for attempt in range(5):
                try:
                    return json.loads(self.db_file.read_text(encoding="utf-8"))
                except Exception:
                    if attempt < 4 and self.db_file.exists():
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    return {
                        "worker_status": {
                            "id": 1,
                            "is_online": False,
                            "last_heartbeat": "2020-01-01T00:00:00+00:00",
                            "current_job_id": None,
                        },
                        "cad_jobs": [],
                        "chat_messages": [],
                    }

    def _save_db(self, data: Dict[str, Any]) -> None:
        with self._cross_process_lock():
            payload = json.dumps(data, indent=2, ensure_ascii=False)
            tmp_file = self.db_file.with_name(f"local_db.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp_file.write_text(payload, encoding="utf-8")
            last_err: Optional[Exception] = None
            for attempt in range(12):
                try:
                    tmp_file.replace(self.db_file)
                    return
                except PermissionError as exc:
                    last_err = exc
                    time.sleep(0.05 * (attempt + 1))
            try:
                tmp_file.unlink(missing_ok=True)
            except Exception:
                pass
            if last_err is not None:
                raise last_err


class CADPortalDatabase:
    """Unified service layer for Supabase (Primary) with Local Fallback."""

    def __init__(self) -> None:
        self.supabase_url = _get_config_value("SUPABASE_URL", "").strip()
        self.supabase_key = _get_config_value("SUPABASE_KEY", "").strip()
        self.bucket_name = _get_config_value("SUPABASE_BUCKET", DEFAULT_BUCKET).strip()
        self.client = None
        self.is_cloud_configured = False

        if (
            self.supabase_url
            and self.supabase_key
            and "your-project-id" not in self.supabase_url
            and "your-supabase" not in self.supabase_key
        ):
            try:
                from supabase import create_client

                self.client = create_client(self.supabase_url, self.supabase_key)
                self.is_cloud_configured = True
            except Exception as exc:
                print(f"[WARN] Failed to initialize Supabase client, using local fallback: {exc}")
                self.is_cloud_configured = False

        self.local_store = LocalFallbackStore(PROJECT_ROOT / ".local_data")

    # =========================================================================
    # WORKER HEARTBEAT & ONLINE DETECTION
    # =========================================================================
    def get_worker_status(self) -> Dict[str, Any]:
        """
        Check whether Ilham's laptop worker heartbeat is active
        (now() - last_heartbeat < 90 seconds).
        """
        now_utc = datetime.now(timezone.utc)

        if self.is_cloud_configured and self.client:
            try:
                res = (
                    self.client.table("worker_status")
                    .select("*")
                    .eq("id", 1)
                    .limit(1)
                    .execute()
                )
                rows = res.data or []
                if not rows:
                    # Initialize singleton row
                    init_row = {
                        "id": 1,
                        "is_online": False,
                        "last_heartbeat": "2020-01-01T00:00:00+00:00",
                        "current_job_id": None,
                    }
                    self.client.table("worker_status").upsert(init_row).execute()
                    row = init_row
                else:
                    row = rows[0]
            except Exception as exc:
                return {
                    "is_online": False,
                    "last_heartbeat": None,
                    "seconds_since_heartbeat": 999999,
                    "current_job_id": None,
                    "backend_mode": f"Supabase Error ({exc})",
                }
        else:
            db = self.local_store._load_db()
            row = db.get("worker_status", {})

        last_hb = _parse_iso_datetime(row.get("last_heartbeat"))
        if last_hb:
            elapsed = (now_utc - last_hb).total_seconds()
        else:
            elapsed = 999999.0

        is_active = bool(row.get("is_online", False)) and (elapsed < HEARTBEAT_THRESHOLD_SEC)

        return {
            "is_online": is_active,
            "last_heartbeat": row.get("last_heartbeat"),
            "seconds_since_heartbeat": max(0, int(elapsed)),
            "current_job_id": row.get("current_job_id"),
            "backend_mode": "Supabase Cloud" if self.is_cloud_configured else "Local Queue Mode",
        }

    def update_worker_heartbeat(
        self,
        is_online: bool = True,
        current_job_id: Optional[str] = None,
    ) -> bool:
        """Ping worker_status table from local_worker.py every 30 seconds."""
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "id": 1,
            "is_online": is_online,
            "last_heartbeat": now_iso,
            "current_job_id": current_job_id,
        }

        if self.is_cloud_configured and self.client:
            try:
                self.client.table("worker_status").upsert(payload).execute()
                return True
            except Exception as exc:
                print(f"[ERROR] Failed to update Supabase heartbeat: {exc}")
                return False
        else:
            db = self.local_store._load_db()
            db["worker_status"] = payload
            self.local_store._save_db(db)
            return True

    # =========================================================================
    # STORAGE UPLOAD & DOWNLOAD (cad-files bucket)
    # =========================================================================
    def upload_file(self, file_bytes: bytes, storage_path: str, content_type: Optional[str] = None) -> str:
        """
        Upload a file (.dwg, .pdf, .png, .jpg) to Supabase Storage bucket `cad-files`.
        Returns the normalized storage_path.
        """
        normalized_path = storage_path.lstrip("/").replace("\\", "/")
        ctype = content_type or _guess_content_type(normalized_path)

        if self.is_cloud_configured and self.client:
            self.client.storage.from_(self.bucket_name).upload(
                path=normalized_path,
                file=file_bytes,
                file_options={"content-type": ctype, "upsert": "true"},
            )
            return normalized_path
        else:
            dest = self.local_store.storage_dir / normalized_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(file_bytes)
            return normalized_path

    def download_file(self, storage_path: str) -> Optional[bytes]:
        """Download file bytes from Supabase Storage bucket `cad-files`."""
        if not storage_path:
            return None
        normalized_path = storage_path.lstrip("/").replace("\\", "/")

        if self.is_cloud_configured and self.client:
            try:
                return self.client.storage.from_(self.bucket_name).download(normalized_path)
            except Exception as exc:
                print(f"[ERROR] Failed to download {normalized_path} from Supabase: {exc}")
                return None
        else:
            src = self.local_store.storage_dir / normalized_path
            if src.exists():
                return src.read_bytes()
            return None

    def get_public_url(self, storage_path: str) -> str:
        """Return public URL for a stored file if using Supabase Cloud."""
        normalized_path = storage_path.lstrip("/").replace("\\", "/")
        if self.is_cloud_configured and self.client:
            try:
                return self.client.storage.from_(self.bucket_name).get_public_url(normalized_path)
            except Exception:
                return normalized_path
        return str((self.local_store.storage_dir / normalized_path).resolve())

    # =========================================================================
    # CAD JOBS & CHAT MESSAGES CRUD
    # =========================================================================
    def create_cad_job(
        self,
        requester_name: str,
        user_prompt: str,
        input_dwg_url: str,
        reference_img_urls: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new CAD modification job and initialize its chat thread with
        either the online or offline automated assistant response.
        """
        worker_state = self.get_worker_status()
        is_online = worker_state["is_online"]

        job_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        ref_urls = reference_img_urls or []
        initial_status = "queued" if is_online else "waiting_offline"

        job_payload = {
            "id": job_id,
            "created_at": now_iso,
            "requester_name": requester_name.strip(),
            "status": initial_status,
            "input_dwg_url": input_dwg_url,
            "reference_img_urls": ref_urls,
            "user_prompt": user_prompt.strip(),
            "output_dwg_url": None,
            "output_pdf_url": None,
            "agent_response": None,
        }

        user_attachments = [{"path": input_dwg_url, "type": "dwg"}] + [
            {"path": img_url, "type": "image"} for img_url in ref_urls
        ]

        bot_reply = ONLINE_REPLY_MESSAGE if is_online else OFFLINE_REPLY_MESSAGE

        if self.is_cloud_configured and self.client:
            res = self.client.table("cad_jobs").insert(job_payload).execute()
            created_job = res.data[0] if res.data else job_payload
        else:
            db = self.local_store._load_db()
            db["cad_jobs"].insert(0, job_payload)
            self.local_store._save_db(db)
            created_job = job_payload

        # Insert initial user message & automated bot status reply
        self.add_chat_message(
            job_id=job_id,
            sender="user",
            message=user_prompt.strip(),
            attachments=user_attachments,
        )
        self.add_chat_message(
            job_id=job_id,
            sender="assistant",
            message=bot_reply,
            attachments=[],
        )

        return created_job

    def add_followup_revision(
        self,
        job_id: str,
        user_prompt: str,
        new_dwg_url: Optional[str] = None,
        new_ref_img_urls: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Allow a user to send a follow-up instruction or additional reference sketch
        in the same job session and re-queue the ticket.
        """
        job = self.get_cad_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        worker_state = self.get_worker_status()
        is_online = worker_state["is_online"]
        new_status = "queued" if is_online else "waiting_offline"

        combined_prompt = f"{job['user_prompt']}\n\n[Revisi/Tambahan User]: {user_prompt.strip()}"
        updated_dwg = new_dwg_url or job["input_dwg_url"]
        existing_imgs = list(job.get("reference_img_urls") or [])
        if new_ref_img_urls:
            existing_imgs.extend(new_ref_img_urls)

        attachments: List[Dict[str, str]] = []
        if new_dwg_url:
            attachments.append({"path": new_dwg_url, "type": "dwg"})
        for img in new_ref_img_urls or []:
            attachments.append({"path": img, "type": "image"})

        self.add_chat_message(
            job_id=job_id,
            sender="user",
            message=user_prompt.strip(),
            attachments=attachments,
        )

        bot_reply = ONLINE_REPLY_MESSAGE if is_online else OFFLINE_REPLY_MESSAGE
        self.add_chat_message(
            job_id=job_id,
            sender="assistant",
            message=bot_reply,
            attachments=[],
        )

        updates = {
            "status": new_status,
            "user_prompt": combined_prompt,
            "input_dwg_url": updated_dwg,
            "reference_img_urls": existing_imgs,
        }

        if self.is_cloud_configured and self.client:
            res = self.client.table("cad_jobs").update(updates).eq("id", job_id).execute()
            return res.data[0] if res.data else {**job, **updates}
        else:
            db = self.local_store._load_db()
            for item in db["cad_jobs"]:
                if item["id"] == job_id:
                    item.update(updates)
                    self.local_store._save_db(db)
                    return item
            return {**job, **updates}

    def list_cad_jobs(
        self,
        requester_name: Optional[str] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """List CAD jobs ordered by newest first, optionally filtered by requester or status."""
        if self.is_cloud_configured and self.client:
            query = self.client.table("cad_jobs").select("*").order("created_at", desc=True)
            if requester_name:
                query = query.ilike("requester_name", f"%{requester_name.strip()}%")
            if statuses:
                query = query.in_("status", statuses)
            res = query.execute()
            return res.data or []
        else:
            db = self.local_store._load_db()
            jobs = list(db.get("cad_jobs", []))
            if requester_name:
                q = requester_name.strip().lower()
                jobs = [j for j in jobs if q in j.get("requester_name", "").lower()]
            if statuses:
                jobs = [j for j in jobs if j.get("status") in statuses]
            jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return jobs

    def get_cad_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single CAD job by UUID."""
        if not job_id:
            return None
        if self.is_cloud_configured and self.client:
            res = self.client.table("cad_jobs").select("*").eq("id", job_id).limit(1).execute()
            return res.data[0] if res.data else None
        else:
            db = self.local_store._load_db()
            for item in db.get("cad_jobs", []):
                if item.get("id") == job_id:
                    return item
            return None

    def get_next_approved_job(self) -> Optional[Dict[str, Any]]:
        """
        Fetch the oldest pending job ('approved', 'queued', or 'waiting_offline') in FIFO order
        so local_worker.py processes incoming chat requests automatically one by one.
        """
        target_statuses = ["approved", "queued", "waiting_offline"]
        if self.is_cloud_configured and self.client:
            res = (
                self.client.table("cad_jobs")
                .select("*")
                .in_("status", target_statuses)
                .order("created_at", desc=False)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None
        else:
            db = self.local_store._load_db()
            pending = [
                j for j in db.get("cad_jobs", []) if j.get("status") in target_statuses
            ]
            pending.sort(key=lambda x: x.get("created_at", ""))
            return pending[0] if pending else None

    def update_job_status(
        self,
        job_id: str,
        status: str,
        output_dwg_url: Optional[str] = None,
        output_pdf_url: Optional[str] = None,
        agent_response: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update job status and optional output artifacts."""
        updates: Dict[str, Any] = {"status": status}
        if output_dwg_url is not None:
            updates["output_dwg_url"] = output_dwg_url
        if output_pdf_url is not None:
            updates["output_pdf_url"] = output_pdf_url
        if agent_response is not None:
            updates["agent_response"] = agent_response

        if self.is_cloud_configured and self.client:
            res = self.client.table("cad_jobs").update(updates).eq("id", job_id).execute()
            return res.data[0] if res.data else None
        else:
            db = self.local_store._load_db()
            updated_item = None
            for item in db.get("cad_jobs", []):
                if item.get("id") == job_id:
                    item.update(updates)
                    updated_item = item
                    break
            self.local_store._save_db(db)
            return updated_item

    def approve_all_queued_jobs(self) -> int:
        """Approve all jobs currently in 'waiting_offline' or 'queued' state."""
        waiting_jobs = self.list_cad_jobs(statuses=["waiting_offline", "queued"])
        count = 0
        for job in waiting_jobs:
            self.update_job_status(job["id"], "approved")
            self.add_chat_message(
                job_id=job["id"],
                sender="assistant",
                message="⚙️ Request telah disetujui Admin (Ilham) dan masuk antrean eksekusi CADCraft AI Agent...",
                attachments=[],
            )
            count += 1
        return count

    def add_chat_message(
        self,
        job_id: str,
        sender: str,
        message: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Insert a message into chat_messages."""
        msg_payload = {
            "id": str(uuid.uuid4()),
            "job_id": job_id,
            "sender": sender,
            "message": message,
            "attachments": attachments or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if self.is_cloud_configured and self.client:
            res = self.client.table("chat_messages").insert(msg_payload).execute()
            return res.data[0] if res.data else msg_payload
        else:
            db = self.local_store._load_db()
            db.setdefault("chat_messages", []).append(msg_payload)
            self.local_store._save_db(db)
            return msg_payload

    def get_chat_messages(self, job_id: str) -> List[Dict[str, Any]]:
        """Fetch chronological chat messages for a job."""
        if self.is_cloud_configured and self.client:
            res = (
                self.client.table("chat_messages")
                .select("*")
                .eq("job_id", job_id)
                .order("created_at", desc=False)
                .execute()
            )
            return res.data or []
        else:
            db = self.local_store._load_db()
            msgs = [m for m in db.get("chat_messages", []) if m.get("job_id") == job_id]
            msgs.sort(key=lambda x: x.get("created_at", ""))
            return msgs


# Singleton accessor
_db_instance: Optional[CADPortalDatabase] = None


def get_db() -> CADPortalDatabase:
    global _db_instance
    if _db_instance is None:
        _db_instance = CADPortalDatabase()
    return _db_instance
