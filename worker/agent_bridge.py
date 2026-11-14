"""
Zero-Cost AI Execution Engine (`worker/agent_bridge.py`).
Connects the Streamlit Web UI / Local Worker directly to:
1. Direct MCP JSON-RPC initialization via `cadcraft-cli.exe mcp --connect 127.0.0.1:7980`
   to force `new_drawing` (or `open`) BEFORE every job so requests NEVER overwrite or draw on top
   of previous drawings.
2. Antigravity CLI (`C:\\Users\\ASUS\\AppData\\Local\\agy\\bin\\agy.exe`) with `--dangerously-skip-permissions`
   and `--model <selected_model>`.
3. Direct MCP `save` / `cadcraft-cli.exe convert` post-step to guarantee `<waktu_dan_tanggal>.dwg`
   and `<waktu_dan_tanggal>.pdf` are always saved.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

from src.models import get_current_cli_model
from src.supabase_client import format_timestamp_stem
from worker.dwg_optimizer import (
    convert_ole2frames_in_dwg,
    export_optimized_pdf_from_dwg,
    optimize_pdf_stream_and_embed_images,
)
from worker.prompt_templates import build_cadcraft_agent_prompt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=True)


@dataclass
class AgentExecutionResult:
    success: bool
    output_dwg_path: Optional[Path]
    output_pdf_path: Optional[Path]
    summary_text: str
    error_message: Optional[str] = None


def _ensure_bom_free_mcp_config() -> None:
    """Ensure `mcp_config.json` has no UTF-8 BOM so `agy.exe` loads MCP servers cleanly."""
    gemini_dir = Path.home() / ".gemini"
    for p in [
        gemini_dir / "config" / "mcp_config.json",
        gemini_dir / "antigravity" / "mcp_config.json",
        gemini_dir / "antigravity-cli" / "mcp_config.json",
    ]:
        try:
            if p.exists():
                raw = p.read_bytes()
                if raw.startswith(b"\xef\xbb\xbf"):
                    p.write_bytes(raw[3:])
        except Exception:
            pass


def is_cadcraft_port_listening(host: str = "127.0.0.1", port: int = 7980) -> bool:
    """Check if CADCraft Bridged Mode is listening on localhost:7980."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.5)
        return sock.connect_ex((host, port)) == 0


def ensure_cadcraft_running() -> bool:
    """Ensure CADCraft is running on port 7980; launch it if not yet running."""
    port = int(os.getenv("CADCRAFT_PORT", "7980"))
    if is_cadcraft_port_listening(port=port):
        return True

    exe_path = os.getenv("CADCRAFT_EXE_PATH", r"C:\Program Files\CADCraft\cadcraft.exe")
    if not Path(exe_path).exists():
        print(f"[WARN] CADCraft executable not found at {exe_path}.")
        return False

    print(f"[INFO] Launching CADCraft Bridged Mode on port {port}...")
    subprocess.Popen([exe_path, "--control", str(port)], close_fds=True)

    for _ in range(25):
        time.sleep(1)
        if is_cadcraft_port_listening(port=port):
            print(f"[INFO] CADCraft port {port} is now LISTENING!")
            return True

    print(f"[WARN] Timed out waiting for CADCraft port {port}.")
    return False


def call_cadcraft_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> bool:
    """
    Directly invoke a CADCraft MCP tool (`new_drawing`, `open`, `save`, etc.)
    against the running CADCraft Bridged Mode instance (`127.0.0.1:7980`)
    via `cadcraft-cli.exe mcp --connect 127.0.0.1:7980`.
    """
    cli_path = os.getenv("CADCRAFT_CLI_PATH", r"C:\Program Files\CADCraft\cadcraft-cli.exe")
    port = int(os.getenv("CADCRAFT_PORT", "7980"))
    if not Path(cli_path).exists():
        return False

    requests_payload = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cadcraft-local-worker", "version": "1.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    ]

    try:
        proc = subprocess.Popen(
            [cli_path, "mcp", "--connect", f"127.0.0.1:{port}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdin_str = "\n".join(json.dumps(r) for r in requests_payload) + "\n"
        out, _ = proc.communicate(stdin_str, timeout=15)
        return '"id":2' in out.replace(" ", "")
    except Exception as exc:
        print(f"[WARN] Direct MCP call `{tool_name}` failed: {exc}")
        return False


def _resolve_agy_executable() -> Optional[str]:
    """Find `agy.exe` on the system."""
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    configured = os.getenv("AGY_EXE_PATH", r"C:\Users\ASUS\AppData\Local\agy\bin\agy.exe").strip()
    if configured and Path(configured).exists():
        return configured

    default_local = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
    if default_local.exists():
        return str(default_local)

    return shutil.which("agy") or shutil.which("antigravity")


def _extract_latest_cli_transcript_response(since_timestamp: float) -> str:
    """
    Extract the final PLANNER_RESPONSE content from the conversation created during this run.
    """
    brain_dir = Path.home() / ".gemini" / "antigravity-cli" / "brain"
    if not brain_dir.exists():
        return ""

    try:
        candidates = []
        for conv_dir in brain_dir.iterdir():
            if not conv_dir.is_dir():
                continue
            transcript = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if transcript.exists() and transcript.stat().st_mtime >= (since_timestamp - 5):
                candidates.append((transcript.stat().st_mtime, transcript))

        if not candidates:
            return ""

        candidates.sort(key=lambda x: x[0], reverse=True)
        latest_transcript = candidates[0][1]

        last_response = ""
        for line in latest_transcript.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("type") == "PLANNER_RESPONSE" and obj.get("content"):
                    last_response = str(obj["content"]).strip()
            except Exception:
                continue
        return last_response
    except Exception:
        return ""


def _ensure_dwg_and_pdf_saved(dwg_path: Path, pdf_path: Path) -> None:
    """
    Ensure both `dwg_path` and `pdf_path` are saved at the end of the job:
    1. If the AI drew on the active CADCraft canvas but didn't call `save` to `dwg_path`,
       call MCP `save` directly to `dwg_path`.
    2. Convert any remaining `OLE2FRAME` objects in `dwg_path` to `IMAGEATTACH` (.png)
       without altering `LTSCALE` in the saved `.dwg`.
    3. Export & optimize `pdf_path` (in-memory `LTSCALE` adjustment + sub-pixel dash
       simplification + PyMuPDF raster image embedding) so PDF is small and all logos appear.
    """
    if not dwg_path.exists() or dwg_path.stat().st_size == 0:
        print(f"[BRIDGE] Menyimpan gambar aktif CADCraft ke DWG -> {dwg_path.name}...")
        call_cadcraft_mcp_tool("save", {"path": str(dwg_path.resolve())})

    if dwg_path.exists() and dwg_path.stat().st_size > 0:
        try:
            ole_post = convert_ole2frames_in_dwg(dwg_path, image_output_dir=dwg_path.parent)
            if ole_post.get("converted", 0) > 0:
                print(
                    f"[OPTIMIZER] Mengonversi {ole_post['converted']} gambar OLE2FRAME -> IMAGEATTACH pada {dwg_path.name}"
                )
        except Exception as exc:
            print(f"[WARN] Gagal menjalankan konversi OLE2FRAME pasca-job: {exc}")

        try:
            print(f"[OPTIMIZER] Mengekspor & mengoptimalkan ukuran PDF -> {pdf_path.name}...")
            pdf_opt = export_optimized_pdf_from_dwg(dwg_path, pdf_path)
            if pdf_opt.get("ok"):
                orig_kb = pdf_opt.get("original_size", 0) / 1024.0
                final_kb = pdf_opt.get("final_size", 0) / 1024.0
                imgs_emb = pdf_opt.get("images_embedded", 0)
                print(
                    f"[OPTIMIZER] PDF selesai dioptimasi: {orig_kb:.1f} KB -> {final_kb:.1f} KB "
                    f"(Gambar disematkan: {imgs_emb}, LTSCALE DWG tetap asli)."
                )
        except Exception as exc:
            print(f"[WARN] Gagal mengoptimalkan PDF via dwg_optimizer: {exc}")

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        print(f"[BRIDGE] Mengekspor gambar aktif CADCraft ke PDF -> {pdf_path.name}...")
        call_cadcraft_mcp_tool("save", {"path": str(pdf_path.resolve())})
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            optimize_pdf_stream_and_embed_images(pdf_path)

    if (
        dwg_path.exists()
        and dwg_path.stat().st_size > 0
        and (not pdf_path.exists() or pdf_path.stat().st_size == 0)
    ):
        cli_path = os.getenv("CADCRAFT_CLI_PATH", r"C:\Program Files\CADCraft\cadcraft-cli.exe")
        if Path(cli_path).exists():
            try:
                subprocess.run(
                    [cli_path, "convert", str(dwg_path), str(pdf_path)],
                    timeout=60,
                    check=False,
                )
                if pdf_path.exists() and pdf_path.stat().st_size > 0:
                    optimize_pdf_stream_and_embed_images(pdf_path)
            except Exception as exc:
                print(f"[WARN] Gagal menjalankan cadcraft-cli convert: {exc}")


def execute_cad_job_with_agent(
    job_id: str,
    requester_name: str,
    user_prompt: str,
    input_dwg_path: Path,
    reference_image_paths: List[Path],
    output_dir: Path,
    heartbeat_callback: Optional[Callable[[], None]] = None,
    model_name: Optional[str] = None,
) -> AgentExecutionResult:
    """
    Execute a CAD job directly via Antigravity CLI (`agy.exe`) connected to CADCraft MCP (port 7980).
    Always initializes a fresh drawing (`new_drawing`) when no input DWG is provided so requests
    never draw on top of previous drawings.
    """
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    _ensure_bom_free_mcp_config()
    ensure_cadcraft_running()

    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 0: Force a clean canvas (`new_drawing`) or pre-optimize OLE2FRAME & open input DWG
    if input_dwg_path.exists() and input_dwg_path.stat().st_size > 0:
        try:
            ole_pre = convert_ole2frames_in_dwg(input_dwg_path, image_output_dir=input_dwg_path.parent)
            if ole_pre.get("converted", 0) > 0:
                print(
                    f"[OPTIMIZER] Berhasil mengekstrak & memasang {ole_pre['converted']} gambar "
                    f"OLE2FRAME -> IMAGEATTACH dari {input_dwg_path.name}."
                )
                for img_str in ole_pre.get("images", []):
                    img_p = Path(img_str)
                    if img_p.exists():
                        shutil.copy2(img_p, output_dir / img_p.name)
        except Exception as exc:
            print(f"[WARN] Pre-konversi OLE2FRAME gagal: {exc}")

        print(f"[BRIDGE] Membuka file DWG input di CADCraft: {input_dwg_path.name}")
        call_cadcraft_mcp_tool("open", {"path": str(input_dwg_path.resolve())})
    else:
        print("[BRIDGE] Mereset kanvas CADCraft dengan `new_drawing` (Metric) agar tidak menimpa gambar lama...")
        call_cadcraft_mcp_tool("new_drawing", {"metric": True})
    ts_stem = format_timestamp_stem()
    output_dwg_path = output_dir / f"{ts_stem}.dwg"
    output_pdf_path = output_dir / f"{ts_stem}.pdf"
    summary_txt_path = output_dir / f"summary_{ts_stem}.txt"

    full_prompt = build_cadcraft_agent_prompt(
        job_id=job_id,
        requester_name=requester_name,
        user_prompt=user_prompt,
        input_dwg_path=input_dwg_path,
        reference_image_paths=reference_image_paths,
        output_dwg_path=output_dwg_path,
        output_pdf_path=output_pdf_path,
        summary_txt_path=summary_txt_path,
    )

    job_dir = input_dwg_path.parent
    prompt_file = job_dir / f"PROMPT_{ts_stem}.md"
    prompt_file.write_text(full_prompt, encoding="utf-8")

    agy_exe = _resolve_agy_executable()
    selected_model = (model_name or "").strip() or get_current_cli_model()
    timeout_sec = int(os.getenv("JOB_TIMEOUT_SEC", "900"))

    if not agy_exe:
        return AgentExecutionResult(
            success=False,
            output_dwg_path=None,
            output_pdf_path=None,
            summary_text="",
            error_message="Antigravity CLI (`agy.exe`) tidak ditemukan.",
        )

    cmd = [
        agy_exe,
        "-p",
        full_prompt,
        "--model",
        selected_model,
        "--dangerously-skip-permissions",
        "--print-timeout",
        "15m0s",
        "--add-dir",
        str(job_dir.resolve()),
    ]

    print(
        "\n======================================================================\n"
        f"[AGY CLI + MCP CADCRAFT] Menjalankan Job ID: {job_id}\n"
        f"  Pemohon    : {requester_name}\n"
        f"  Model AI   : {selected_model}\n"
        f"  Target DWG : {output_dwg_path}\n"
        f"  Target PDF : {output_pdf_path}\n"
        "======================================================================\n"
    )

    run_start_ts = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(job_dir.resolve()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        while proc.poll() is None:
            if heartbeat_callback:
                heartbeat_callback()

            if (
                output_dwg_path.exists()
                and output_dwg_path.stat().st_size > 0
                and output_pdf_path.exists()
                and output_pdf_path.stat().st_size > 0
                and summary_txt_path.exists()
                and summary_txt_path.stat().st_size > 0
            ):
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                break

            if (time.time() - run_start_ts) > timeout_sec:
                proc.kill()
                return AgentExecutionResult(
                    success=False,
                    output_dwg_path=None,
                    output_pdf_path=None,
                    summary_text="",
                    error_message=f"Eksekusi melebihi batas waktu ({timeout_sec} detik).",
                )
            time.sleep(2.0)

        stdout_text, stderr_text = proc.communicate(timeout=10)
        return_code = proc.returncode
    except Exception as exc:
        return AgentExecutionResult(
            success=False,
            output_dwg_path=None,
            output_pdf_path=None,
            summary_text="",
            error_message=f"Gagal menjalankan Antigravity CLI: {exc}",
        )

    combined_out = f"{stdout_text or ''}\n{stderr_text or ''}"
    if "authentication timed out" in combined_out.lower() or "authentication required" in combined_out.lower():
        return AgentExecutionResult(
            success=False,
            output_dwg_path=None,
            output_pdf_path=None,
            summary_text="",
            error_message=(
                "Antigravity CLI membutuhkan login OAuth 1 kali. "
                "Jalankan `scripts\\login_agy_once.bat` terlebih dahulu."
            ),
        )

    # Guarantee DWG and PDF are saved with the exact timestamp when finished
    _ensure_dwg_and_pdf_saved(output_dwg_path, output_pdf_path)

    dwg_ok = output_dwg_path.exists() and output_dwg_path.stat().st_size > 0
    pdf_ok = output_pdf_path.exists() and output_pdf_path.stat().st_size > 0

    transcript_reply = _extract_latest_cli_transcript_response(run_start_ts)

    if summary_txt_path.exists() and summary_txt_path.stat().st_size > 0:
        summary_text = summary_txt_path.read_text(encoding="utf-8", errors="replace").strip()
    elif transcript_reply:
        summary_text = transcript_reply
    elif stdout_text and stdout_text.strip():
        summary_text = stdout_text.strip()
    else:
        summary_text = (
            "✅ Gambar telah selesai dibuat/dimodifikasi melalui CADCraft AI Agent."
            if (dwg_ok or pdf_ok)
            else (stderr_text.strip() or "Proses selesai.")
        )

    if return_code not in (0, None) and not dwg_ok and not transcript_reply:
        return AgentExecutionResult(
            success=False,
            output_dwg_path=None,
            output_pdf_path=None,
            summary_text="",
            error_message=f"Antigravity CLI berhenti (kode {return_code}): {summary_text[:300]}",
        )

    return AgentExecutionResult(
        success=True,
        output_dwg_path=output_dwg_path if dwg_ok else None,
        output_pdf_path=output_pdf_path if pdf_ok else None,
        summary_text=summary_text,
    )
