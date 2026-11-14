"""
System Prompt Templates (`worker/prompt_templates.py`) for the CADCraft MCP AI Agent.
Wraps every user CAD request with exact file paths and step-by-step instructions
for the CADCraft MCP server running on localhost:7980.
"""

from __future__ import annotations

from pathlib import Path
from typing import List


def build_cadcraft_agent_prompt(
    job_id: str,
    requester_name: str,
    user_prompt: str,
    input_dwg_path: Path,
    reference_image_paths: List[Path],
    output_dwg_path: Path,
    output_pdf_path: Path,
    summary_txt_path: Path,
) -> str:
    """
    Build the standardized prompt injected into Antigravity / Gemini CLI
    to execute CAD modifications via the `cadcraft` MCP server (port 7980).
    """
    in_dwg_str = str(input_dwg_path.resolve())
    out_dwg_str = str(output_dwg_path.resolve())
    out_pdf_str = str(output_pdf_path.resolve())
    out_plot_json_str = str(output_pdf_path.with_suffix(".plot.json").resolve())
    sum_txt_str = str(summary_txt_path.resolve())

    if reference_image_paths:
        ref_lines = "\n".join(f"  - `{p.resolve()}`" for p in reference_image_paths)
        ref_section = (
            "### Lampiran Gambar Referensi / Coretan User:\n"
            "Baca dan periksa gambar referensi berikut terlebih dahulu menggunakan tool `read_file`:\n"
            f"{ref_lines}\n"
        )
    else:
        ref_section = "### Lampiran Gambar Referensi:\n- Tidak ada gambar referensi tambahan.\n"

    if input_dwg_path.exists():
        step1_text = (
            "1. **Buka File Input DWG:**\n"
            "   Panggil tool MCP `cadcraft:open` dengan parameter:\n"
            f'   `{{"path": "{in_dwg_str}"}}`'
        )
    else:
        step1_text = (
            "1. **Buat Gambar CAD Baru:**\n"
            "   Tidak ada file input `.dwg` yang dilampirkan. Panggil tool MCP `cadcraft:new_drawing` "
            "untuk memulai kanvas gambar baru dari nol."
        )

    prompt = f"""[CADCRAFT AI AGENT - AUTOMATED JOB EXECUTION]
Job ID: {job_id}
Pemohon / Divisi: {requester_name}

Anda terhubung dengan **CADCraft MCP Server** (port `7980`). Kerjakan tugas modifikasi/pembuatan gambar AutoCAD (`.dwg`) berikut secara berurutan dan tuntas:

{ref_section}
### Instruksi Spesifik dari User:
\"\"\"
{user_prompt.strip()}
\"\"\"

### Aturan Utama Sistem (Wajib Dipatuhi di Setiap Pekerjaan):
1. **Penanganan Gambar & Logo (`OLE2FRAME` -> `IMAGEATTACH`):**
   - Engine CADCraft tidak merender objek `OLE2FRAME` (terbaca sebagai `Proxy`), tetapi mendukung penuh **`imageattach`** (`iat`) untuk file `PNG/JPEG/BMP/TIFF`.
   - Sistem worker telah mengekstrak seluruh gambar `OLE2FRAME` dari file DWG menjadi file `.png` (`ole_img_<handle>.png`) di folder yang sama dengan DWG dan memasangnya via `imageattach` pada layer `CADCRAFT_OLE_IMG`.
   - Jangan menghapus entitas gambar (`Image`) pada layer `CADCRAFT_OLE_IMG`. Jika Anda perlu menyisipkan logo/gambar tambahan, gunakan perintah `imageattach` (`iat`) dengan path relatif di folder output atau konversi menjadi vektor DWG (`polyline` + `solid hatch`).
2. **Keamanan File `.dwg` untuk Drafter AutoCAD vs Optimasi Ukuran `.pdf`:**
   - File `.dwg` hasil akhir (`{out_dwg_str}`) akan direvisi kembali oleh drafter menggunakan AutoCAD. Oleh karena itu, **JANGAN mengubah `LTSCALE` atau merusak properti linetype asli saat menyimpan file `.dwg`** (kecuali diminta eksplisit oleh user).
   - Simpan file `.dwg` terlebih dahulu ke `{out_dwg_str}` dalam kondisi murni.
   - Setelah file `.dwg` tersimpan, barulah lakukan penyesuaian `LTSCALE` (misal menaikkan `LTSCALE` sementara jika terdapat garis putus-putus `HIDDEN`/`DASHED`/`ACAD_ISO03W100` yang terlalu rapat) **HANYA untuk proses ekspor ke `.pdf`** (`{out_pdf_str}`), agar jutaan segmen garis mikroskopis hilang dan ukuran PDF menjadi kecil namun tetap 100% vektor tajam.

### Langkah Eksekusi Wajib (Gunakan MCP `cadcraft`):
{step1_text}
2. **Inspeksi Struktur Gambar:**
   Panggil `cadcraft:inspect_drawing`, `cadcraft:query_entities`, atau `cadcraft:find_text` untuk memeriksa daftar layer, warna layer, teks, dan entitas yang berkaitan dengan instruksi user. (Jika ada gambar referensi di atas, baca gambar tersebut dengan `read_file`).
3. **Terapkan Modifikasi & Perapihan:**
   Gunakan `cadcraft:command_line`, `cadcraft:execute`, atau `cadcraft:script` untuk menjalankan seluruh permintaan user (seperti penyeragaman warna layer, perbaikan typo teks, penambahan anotasi/objek, atau perapihan gambar).
   Gunakan `cadcraft:render` bila perlu untuk memverifikasi tampilan hasil perubahan secara visual.
4. **Simpan File Hasil DWG (Tanpa Mengubah LTSCALE Asli):**
   Panggil tool MCP `cadcraft:save` ke path output berikut:
   `{{"path": "{out_dwg_str}"}}`
5. **Ekspor / Plot File Preview PDF:**
   - **Jika user meminta plot area tertentu, `Window`, bagian kop/etiket saja, atau jika ada objek nyasar di luar bingkai utama:**
     **JANGAN** gunakan `cadcraft:save` untuk `.pdf` (karena `save` selalu mencetak `Extents` penuh).
     Sebagai gantinya:
     a) Cari koordinat sudut kiri-bawah `[x1, y1]` dan kanan-atas `[x2, y2]` dari bingkai kop/area yang diminta menggunakan `cadcraft:query_entities` atau `cadcraft:find_text`.
     b) Tulis file konfigurasi plot ke `{out_plot_json_str}` berisi JSON:
        `{{"plotArea": "window", "window": [[x1, y1], [x2, y2]], "paper": "ISO A3 (297.00 x 420.00 MM)", "landscape": true, "fit": true, "center": true, "lineweights": false}}`
     c) Lalu panggil `cadcraft:execute` dengan `command: "exportpdf"` dan parameter JSON yang sama (ditambah `"path": "{out_pdf_str}"`). Worker akan menjaga area `Window` tersebut sekaligus mengoptimalkan font & gambar logo secara otomatis!
   - **Jika tidak ada permintaan area/Window khusus:**
     Panggil tool MCP `cadcraft:save` dengan ekstensi `.pdf` ke path `{out_pdf_str}` (worker akan mengoptimalkan stream PDF & menyematkan gambar raster secara otomatis).
6. **Tulis Ringkasan Perubahan (`summary.txt`):**
   Tulis ringkasan singkat (dalam Bahasa Indonesia yang jelas dan sopan untuk pemohon) berisi daftar perubahan yang telah berhasil dilakukan ke file:
   `{sum_txt_str}`

PENTING: Worker lokal sedang memantau keberadaan file `{out_dwg_str}` dan `{out_pdf_str}`. Pastikan kedua file tersebut beserta `{sum_txt_str}` benar-benar tersimpan di disk sebelum Anda mengakhiri tugas!
"""
    return prompt
