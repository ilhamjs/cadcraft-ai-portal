"""
CADCRAFT Selectable Skills Registry (`src/skills.py`).
4 preset workflows with multi-select support in the sidebar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class CADSkill:
    id: str
    icon: str
    name: str
    description: str
    system_instruction: str
    placeholder: str


SKILLS: List[CADSkill] = [
    CADSkill(
        id="general",
        icon="💬",
        name="Chat Bebas",
        description="Instruksi bebas sesuai permintaan Anda",
        system_instruction="",
        placeholder="Minta CADCRAFT mengerjakan sesuatu pada gambar Anda...",
    ),
    CADSkill(
        id="new_cad",
        icon="📐",
        name="Buat Gambar Baru",
        description="Buat gambar kerja CAD baru (dengan opsi Kop Drawing & skala otomatis)",
        system_instruction=(
            "[SKILL AKTIF: BUAT GAMBAR BARU]\n"
            "Jika tidak ada file input .dwg yang dilampirkan, mulai gambar baru menggunakan `cadcraft:new_drawing`. "
            "Bangun geometri, layer, dimensi, dan anotasi secara rapi dan presisi berdasarkan gambar referensi "
            "(PNG/JPG/PDF) dan deskripsi ukuran dari user.\n"
            "Jika user menyertakan data Kop Drawing:\n"
            "- Buat bingkai & tabel Kop Drawing yang rapi dan proporsional.\n"
            "- Jika ada file logo perusahaan, sisipkan menggunakan `imageattach` (`iat`) di area logo kop.\n"
            "- Jika tidak ada logo yang dilampirkan, sesuaikan layout kop tanpa area/gambar logo.\n"
            "- Cantumkan kolom standar berikut pada kop:\n"
            "  * Nama Perusahaan, Alamat, dan Judul Gambar sesuai data user.\n"
            "  * Skala: hitung dan cantumkan skala yang sesuai dengan ukuran gambar yang dibuat (misal 1:20, 1:50, 1:100, dll.).\n"
            "  * Digambar: `-` (biarkan kosong/strip `-` kecuali user meminta nama secara spesifik).\n"
            "  * Disahkan: `-` (JANGAN pakai kolom Pemohon; gunakan `Disahkan: -` kecuali ada request).\n"
            "  * Tanggal: `-` (biarkan kosong/strip `-` kecuali user meminta tanggal secara spesifik)."
        ),
        placeholder="Jelaskan gambar baru yang ingin dibuat (ukuran, tampak, atau lampirkan sketsa)...",
    ),
    CADSkill(
        id="analyze_revise",
        icon="🔍",
        name="Analisa/Revisi Gambar",
        description="Periksa, analisa, rapikan, atau revisi file DWG/DXF yang sudah ada",
        system_instruction=(
            "[SKILL AKTIF: ANALISA / REVISI GAMBAR]\n"
            "Buka dan periksa gambar menggunakan `cadcraft:inspect_drawing`, `cadcraft:query_entities`, "
            "dan `cadcraft:find_text`. Lakukan analisa atau revisi sesuai permintaan user (rapikan layer, teks/typo, "
            "kop, dimensi, atau perbaiki gambar/logo OLE2FRAME agar tampil menggunakan `imageattach`) dengan tetap "
            "menjaga keakuratan geometri."
        ),
        placeholder="Lampirkan file DWG/DXF dan jelaskan apa yang ingin dianalisa atau direvisi...",
    ),
    CADSkill(
        id="export_pdf",
        icon="📄",
        name="Export PDF",
        description="Ekspor/plot gambar ke PDF (Extents/Window, ukuran kertas, & ukuran file optimal)",
        system_instruction=(
            "[SKILL AKTIF: EXPORT PDF]\n"
            "Ekspor hasil gambar ke format PDF siap cetak:\n"
            "1. Gunakan `cadcraft:execute` dengan perintah `exportpdf` (atur `fit: true`, `center: true`, "
            "`landscape: true`, atau `plotArea: \"window\"` bila user meminta area/kop tertentu).\n"
            "2. Jika gambar memiliki linetype putus-putus yang terlalu rapat sehingga PDF berpotensi membengkak, "
            "sesuaikan `LTSCALE` saat plotting PDF agar ukuran file tetap ringan dan 100% vektor tajam "
            "(tanpa merusak `LTSCALE` asli pada file `.dwg` yang disimpan)."
        ),
        placeholder="Contoh: Export ke PDF A3 landscape (atau kombinasi dengan skill lain)...",
    ),
]


def get_skill_by_id(skill_id: str) -> CADSkill:
    for skill in SKILLS:
        if skill.id == skill_id:
            return skill
    return SKILLS[0]


def get_skills_by_ids(skill_ids: List[str]) -> List[CADSkill]:
    id_set = set(skill_ids)
    matched = [s for s in SKILLS if s.id in id_set]
    return matched or [SKILLS[0]]
