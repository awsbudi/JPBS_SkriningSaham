IDX Stock Screener AppAplikasi web sederhana berbasis Python (Streamlit) untuk melakukan skrining saham-saham yang terdaftar di Bursa Efek Indonesia (IDX) berdasarkan indikator teknikal dan fundamental yang dapat dikustomisasi. Aplikasi ini mendukung Aturan Skrining Dinamis menggunakan ekspresi Pandas/Python.PrasyaratPython 3.8+Koneksi internet (diperlukan untuk mengambil data harga saham dari Yahoo Finance).Instalasi dan SetupKloning Repositori (Jika ada) atau buat folder proyek.Instalasi Dependencies:Gunakan pip untuk menginstal semua library yang tercantum dalam requirements.txt.pip install -r requirements.txt









Konfigurasi Server Streamlit (Penting untuk Deployment):Buat folder baru bernama .streamlit di root proyek, dan simpan file config.toml di dalamnya. File ini membantu Streamlit berjalan dengan benar di lingkungan hosting seperti Render.Cara Menjalankan AplikasiJalankan skrip Python menggunakan Streamlit CLI dari direktori proyek:streamlit run stock\_screener\_app.py





Aplikasi akan otomatis terbuka di browser Anda (biasanya di http://localhost:8501).Cara MenggunakanSidebar Konfigurasi:Input Tickers Saham: Gunakan Text Area untuk copy-paste daftar ticker saham yang ingin Anda skrining. Ticker IDX akan otomatis ditambahkan .JK.Atur Parameter Indikator: Sesuaikan periode untuk RSI, Volume Rata-rata, dan Kenaikan Historis (N Hari). Catatan: Moving Average (SMA 3, 5, 10, 20, 50) dihitung secara tetap dan tidak dapat diubah di sini.Atur Aturan Skrining Kustom (Penting):Masukkan ekspresi boolean (True/False) satu per baris ke dalam Text Area.Aplikasi akan menghitung Skor berdasarkan berapa banyak aturan yang LULUS (bernilai True).Variabel yang Tersedia untuk Aturan Kustom:KategoriVariabel (Case Sensitive)DeskripsiHargaPrice, Open, High, LowHarga terbaru hari ini (atau hari terakhir data).HistorisPrev\_Close, Prev\_2\_CloseHarga Penutupan 1 dan 2 hari sebelumnya.Rasio/GainOpen\_Close\_Ratio, Pct\_Change\_NRasio Pembukaan/Penutupan Kemarin (Gap) dan Persentase Gain selama N Hari.MA TetapSMA\_3, SMA\_5, SMA\_10, SMA\_20, SMA\_50Moving Average Sederhana dengan periode tetap.MomentumRSIRelative Strength Index.VolumeVolume, Vol\_AvgVolume hari ini dan Volume Rata-rata N hari.MakroIHSG\_Prev\_Close, IHSG\_Change\_PctIHSG Penutupan Kemarin dan Perubahan % Harian IHSG.Contoh Aturan Kustom:Open\_Close\_Ratio > 1.005 # Gap Pembukaan lebih dari 0.5% (Potensi momentum)

SMA\_20 > SMA\_50   # Crossover MA (Golden Cross)

RSI < 70 and RSI > 30   # Tidak Overbought/Oversold

Volume > 2 \* Vol\_Avg         # Volume hari ini lebih dari 2x Rata-rata



* Run Analysis: Klik tombol "Jalankan Analisis Saham".Tabel Hasil: Lihat tabel yang berisi Skor Analisis, Rekomendasi, dan Diurutkan berdasarkan Open\_Close\_Ratio dan Pct\_Change\_N (Gain X Hari).Struktur Logika Rekomendasi (Customizable)Aplikasi ini menggunakan sistem skor sederhana:Setiap aturan kustom yang LULUS akan menambahkan +1 skor.Rekomendasi Akhir: Ditetapkan berdasarkan ambang batas skor (yang dapat Anda atur di sidebar).SkorRekomendasi>= BUY ThresholdBUY< SELL ThresholdSELLDi antaranyaHOLDOpsi Deployment (Web Hosting)Aplikasi Streamlit dapat di-deploy dengan mudah ke layanan seperti Streamlit Community Cloud, Render, atau Hugging Face Spaces.Langkah Umum (Misalnya, menggunakan Render):Buat repositori Git (GitHub/GitLab) untuk proyek ini (.py, requirements.txt, dan folder .streamlit/config.toml).Daftar ke Render dan buat Web Service baru.Hubungkan ke repositori Git Anda.Konfigurasi build command: pip install -r requirements.txtKonfigurasi start command: streamlit run stock\_screener\_app.pyRender akan otomatis membangun dan menjalankan aplikasi web Anda.
