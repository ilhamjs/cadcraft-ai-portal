"""
Dynamic Antigravity CLI Model Manager (`src/models.py`).
- Locally (when `agy.exe` is present): queries `agy.exe models` directly and caches results.
- From `local_worker.py`: syncs the live `agy.exe models` list to `system/available_models.json`
  in Supabase Storage (`cad-files` bucket) / `.local_data` so the cloud Streamlit UI always
  reflects the exact models currently supported by Antigravity CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

AGY_CLI_SETTINGS_PATH = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
MODELS_STORAGE_KEY = "system/available_models.json"
_CACHE_TTL_SEC = 120.0
_cached_models: List[Dict[str, str]] = []
_cached_models_ts: float = 0.0

DEFAULT_ANTIGRAVITY_MODELS: List[Dict[str, str]] = [
    {"id": "gemini-4-hydrogen-medium", "label": "Gemini 4 Hydrogen (Medium)"},
    {"id": "gemini-4-hydrogen-high", "label": "Gemini 4 Hydrogen (High)"},
    {"id": "gemini-4-hydrogen-low", "label": "Gemini 4 Hydrogen (Low)"},
    {"id": "gemini-4-argon-medium", "label": "Gemini 4 Argon (Medium)"},
    {"id": "gemini-4-argon-high", "label": "Gemini 4 Argon (High)"},
    {"id": "gemini-pro-orchestrator-g1-pro", "label": "Orchestrator (Argon)"},
    {"id": "gemini-3.8-flash-medium", "label": "Gemini 3.8 Flash (Medium)"},
    {"id": "gemini-3.8-flash-high", "label": "Gemini 3.8 Flash (High)"},
    {"id": "claude-sonnet-5-5-medium", "label": "Claude Sonnet 5.5 (Medium)"},
    {"id": "claude-opus-5-5-medium", "label": "Claude Opus 5.5 (Medium)"},
    {"id": "gpt-oss-120b-medium", "label": "GPT-OSS 120B (Medium)"},
]


def _resolve_agy_exe() -> Optional[str]:
    configured = os.getenv("AGY_EXE_PATH", "").strip()
    if configured and Path(configured).exists():
        return configured
    default_local = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
    if default_local.exists():
        return str(default_local)
    return shutil.which("agy") or shutil.which("antigravity")


def fetch_agy_cli_models(timeout_sec: int = 12) -> List[Dict[str, str]]:
    """Query `agy.exe models` and parse `<id>\\t<label>` lines."""
    agy_exe = _resolve_agy_exe()
    if not agy_exe:
        return []
    try:
        proc = subprocess.run(
            [agy_exe, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
        if proc.returncode != 0:
            return []
        parsed: List[Dict[str, str]] = []
        for raw in proc.stdout.splitlines():
            line = raw.strip()
            if not line or "\t" not in line:
                continue
            model_id, label = line.split("\t", 1)
            model_id = model_id.strip()
            label = label.strip()
            if model_id and label:
                parsed.append({"id": model_id, "label": label})
        return parsed
    except Exception:
        return []


def sync_available_models_to_storage(db: Any) -> List[Dict[str, str]]:
    """Called by `local_worker.py` to publish live `agy.exe models` to Supabase Storage."""
    global _cached_models, _cached_models_ts
    models = fetch_agy_cli_models()
    if not models:
        return []
    _cached_models = models
    _cached_models_ts = time.time()
    try:
        payload = json.dumps(models, ensure_ascii=False, indent=2).encode("utf-8")
        db.upload_file(payload, MODELS_STORAGE_KEY, content_type="application/json")
    except Exception as exc:
        print(f"[WARN] Gagal sinkronisasi daftar model ke storage: {exc}")
    return models


def _load_models_catalog(db: Optional[Any] = None) -> List[Dict[str, str]]:
    global _cached_models, _cached_models_ts
    now = time.time()
    if _cached_models and (now - _cached_models_ts) < _CACHE_TTL_SEC:
        return _cached_models

    # 1. If running on the local machine with `agy.exe`, query it directly
    live = fetch_agy_cli_models()
    if live:
        _cached_models = live
        _cached_models_ts = now
        return live

    # 2. On Streamlit Cloud (or when `agy.exe` is unavailable), read synced JSON from storage
    if db is not None:
        try:
            raw_bytes = db.download_file(MODELS_STORAGE_KEY)
            if raw_bytes:
                data = json.loads(raw_bytes.decode("utf-8"))
                if isinstance(data, list) and all(
                    isinstance(m, dict) and m.get("id") and m.get("label") for m in data
                ):
                    _cached_models = data
                    _cached_models_ts = now
                    return data
        except Exception:
            pass

    return DEFAULT_ANTIGRAVITY_MODELS


def get_available_models(db: Optional[Any] = None) -> List[str]:
    """Return supported model IDs dynamically from `agy.exe` / synced storage."""
    catalog = _load_models_catalog(db)
    model_ids = [m["id"] for m in catalog]
    current = get_current_cli_model()
    if current and current not in model_ids and "4.6" not in current:
        model_ids.insert(0, current)
    return model_ids


def get_model_display_label(model_id: str) -> str:
    catalog = _cached_models or DEFAULT_ANTIGRAVITY_MODELS
    for m in catalog:
        if m["id"] == model_id:
            return m["label"]
    return model_id


def get_current_cli_model() -> str:
    """Read the currently configured model from ~/.gemini/antigravity-cli/settings.json."""
    try:
        if AGY_CLI_SETTINGS_PATH.exists():
            data = json.loads(AGY_CLI_SETTINGS_PATH.read_text(encoding="utf-8"))
            model = data.get("model", "")
            if model and "4.6" not in model:
                return model
    except Exception:
        pass
    return "gemini-4-hydrogen-medium"


def set_current_cli_model(model_name: str) -> None:
    """Update ~/.gemini/antigravity-cli/settings.json with the chosen model."""
    if not model_name:
        return
    try:
        if AGY_CLI_SETTINGS_PATH.exists():
            data = json.loads(AGY_CLI_SETTINGS_PATH.read_text(encoding="utf-8"))
        else:
            AGY_CLI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {}
        data["model"] = model_name
        AGY_CLI_SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[WARN] Could not update settings.json model: {exc}")
