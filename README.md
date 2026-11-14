# 📐 CADCraft AI Agent Web Portal & Local Worker

Aplikasi Web **Asisten AI Modifikasi Gambar AutoCAD (`.dwg`)** berbasis **Streamlit + Supabase Cloud Queue + Local CADCraft MCP Worker (Port `7980`)**.

Dirancang khusus agar seluruh anggota tim/divisi dapat mengunggah file `.dwg`, melampirkan gambar referensi/coretan (`.png`/`.jpg`), dan menulis instruksi perapihan kapan saja (24/7), sementara eksekusi dilakukan secara aman di laptop Admin (**Ilham**) menggunakan sesi **Antigravity / Gemini Pro (Zero API Token Cost)** yang terhubung ke **CADCraft Bridged Mode (`localhost:7980`)**.

---

## 📂 Struktur Direktori

```text
cadcraft-ai-portal/
├── .env.example                 # Template konfigurasi Supabase, Password Tim, & Path Lokal
├── schema.sql                   # Script SQL 1-klik untuk membuat tabel & bucket di Supabase
├── requirements.txt             # Daftar dependensi Python
├── README.md                    # Dokumentasi instalasi & penggunaan
├── app.py                       # Main Streamlit Web App (Login Gate + Chat UI + Admin Panel)
├── src/
│   ├── auth.py                  # Login Gate kredensial bersama tim & autentikasi Admin
│   ├── supabase_client.py       # Service layer Supabase (DB, Storage, Heartbeat + Local Fallback)
│   ├── ui_chat.py               # Chatbot UI untuk pemohon/divisi (Upload DWG/Image & Download Hasil)
│   └── ui_admin.py              # Admin Queue Controller untuk Ilham (Accept & Run / Manual Override)
├── worker/
│   ├── local_worker.py          # Worker di laptop Ilham (Heartbeat 30s + Sequential Job Polling)
│   ├── agent_bridge.py          # Dual-Mode Executor (CLI Subprocess & Auto-Paste + File Watcher)
│   └── prompt_templates.py      # Template instruksi standar untuk MCP `cadcraft` (port 7980)
└── scripts/
    ├── run_quick_worker.bat     # Menjalankan CADCraft (7980) + Antigravity + Local Worker
    └── run_local_portal.bat     # Menjalankan Web Portal Streamlit secara lokal
```

> **Catatan:** File `run_quick.bat` asli milik Anda di folder utama `Jababeka\` **sama sekali tidak diubah**, sehingga Anda tetap bebas menggunakannya kapan saja untuk keperluan mandiri.

---

## 🚀 Langkah Setup Bertahap

### 1. Setup Database & Storage di Supabase (1-Klik)
1. Buat project gratis di [Supabase](https://supabase.com/).
2. Buka menu **SQL Editor** -> klik **New Query**.
3. Copy-paste seluruh isi file [`schema.sql`](./schema.sql) lalu klik **Run**.
   - Script ini otomatis membuat tabel `worker_status`, `cad_jobs`, `chat_messages`, serta storage bucket publik `cad-files`.
4. Buka **Project Settings -> API**, lalu salin **Project URL** (`SUPABASE_URL`) dan **anon / service_role key** (`SUPABASE_KEY`).

### 2. Konfigurasi File `.env`
Buka file `.env` di dalam folder `cadcraft-ai-portal/` (file ini sudah masuk `.gitignore` agar password tidak ikut ter-publish) dan sesuaikan nilainya:
```ini
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJhbGciOi...
SUPABASE_BUCKET=cad-files

TEAM_PASSWORD=isi-password-tim-anda
ADMIN_PASSWORD=isi-password-admin-anda
```
*(Tips: Jika `SUPABASE_URL` belum diisi, sistem otomatis berjalan dalam **Local Queue Mode** menggunakan penyimpanan lokal `.local_data/` sehingga Anda bisa langsung mencoba aplikasinya di laptop tanpa menunggu setup cloud!)*

### 3. Instalasi Dependensi Python (Di Laptop Ilham)
```powershell
cd c:\Users\ASUS\Documents\JAY\WORK\Jababeka\cadcraft-ai-portal
pip install -r requirements.txt
```

### 4. Menjalankan Web Portal & Local Worker
* **Menjalankan Web Portal (Lokal):**
  Klik dua kali `scripts\run_local_portal.bat` atau jalankan:
  ```powershell
  streamlit run app.py
  ```
* **Menjalankan Local Worker + CADCraft Port 7980:**
  Klik dua kali `scripts\run_quick_worker.bat` atau jalankan:
  ```powershell
  python worker\local_worker.py
  ```

### 5. Deploy Web UI ke Streamlit Community Cloud (Agar Online 24/7)
1. Push folder `cadcraft-ai-portal` ke repositori GitHub Anda.
2. Masuk ke [share.streamlit.io](https://share.streamlit.io/), pilih repo tersebut dan arahkan Main file path ke `app.py`.
3. Pada bagian **Advanced Settings -> Secrets**, masukkan isi `.env` Anda:
   ```toml
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "eyJhbGciOi..."
   SUPABASE_BUCKET = "cad-files"
   TEAM_PASSWORD = "isi-password-tim-anda"
   ADMIN_PASSWORD = "isi-password-admin-anda"
   ```
