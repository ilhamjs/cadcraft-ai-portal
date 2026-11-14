"""
Real-time Antigravity CLI Model Manager (`src/models.py`).
Reads and syncs supported models with `C:\\Users\\ASUS\\.gemini\\antigravity-cli\\settings.json`
and passes the user-selected model to `agy.exe --model`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

AGY_CLI_SETTINGS_PATH = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"

# Models supported by Antigravity CLI 1.0.8 for this Google AI Pro account
DEFAULT_ANTIGRAVITY_MODELS: List[Dict[str, str]] = [
    {"id": "Gemini 4 Hydrogen (Medium)", "label": "⚡ Gemini 4 Hydrogen (Medium) — Cepat & Direkomendasikan"},
    {"id": "Gemini 4 Hydrogen (High)", "label": "⚡ Gemini 4 Hydrogen (High) — Presisi Tinggi"},
    {"id": "Gemini 4 Hydrogen (Low)", "label": "⚡ Gemini 4 Hydrogen (Low) — Super Cepat"},
    {"id": "Gemini 4 Argon (Medium)", "label": "🧠 Gemini 4 Argon (Medium) — Deep Reasoning"},
    {"id": "Gemini 4 Argon (High)", "label": "🧠 Gemini 4 Argon (High) — Kompleks"},
    {"id": "Gemini 3.8 Flash (Medium)", "label": "🚀 Gemini 3.8 Flash (Medium) — Ringan"},
    {"id": "Gemini 3.8 Flash (High)", "label": "🚀 Gemini 3.8 Flash (High)"},
    {"id": "Claude Sonnet 5.5 (Medium)", "label": "🤖 Claude Sonnet 5.5 (Medium)"},
    {"id": "Claude Sonnet 5.5 (High)", "label": "🤖 Claude Sonnet 5.5 (High)"},
    {"id": "Claude Opus 5.5 (Low)", "label": "🤖 Claude Opus 5.5 (Low)"},
    {"id": "Claude Opus 5.5 (Medium)", "label": "🤖 Claude Opus 5.5 (Medium)"},
    {"id": "GPT-OSS 120B (Medium)", "label": "🌐 GPT-OSS 120B (Medium)"},
]


def get_available_models() -> List[str]:
    """
    Return the list of model names supported by Antigravity CLI,
    including any custom active model currently saved in settings.json.
    """
    model_ids = [m["id"] for m in DEFAULT_ANTIGRAVITY_MODELS]
    current = get_current_cli_model()
    if current and current not in model_ids and "4.6" not in current:
        model_ids.insert(0, current)
    return model_ids


def get_model_display_label(model_id: str) -> str:
    for m in DEFAULT_ANTIGRAVITY_MODELS:
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
    return "Gemini 4 Hydrogen (Medium)"


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
