# Import Library
import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import re
import base64
from io import BytesIO
from bcrypt import hashpw, checkpw, gensalt # Untuk hashing password

# Import untuk Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os 
# Catatan: Kita butuh 'os' untuk membaca credentials dari environment variable (Render)

# --- KONFIGURASI APLIKASI DAN INITIALISASI FIREBASE ---

# Set configuration page Streamlit
st.set_page_config(
    page_title="IDX Stock Screener",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Inisialisasi session state untuk status login
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'username' not in st.session_state:
    st.session_state.username = None


# Fungsi inisialisasi Firebase (hanya dijalankan sekali)
def initialize_firebase():
    """Menginisialisasi Firebase Admin SDK menggunakan kredensial JSON."""
    if not firebase_admin._apps:
        try:
            # Asumsi: Service Account JSON disimpan sebagai environment variable di Render, 
            # bernama FIREBASE_SERVICE_ACCOUNT (berisi konten JSON).
            
            # Mendapatkan string JSON dari environment variable
            service_account_json_string = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
            
            if not service_account_json_string:
                st.error("ENV VAR FIREBASE_SERVICE_ACCOUNT tidak ditemukan. Firebase gagal terinisialisasi.")
                return None
                
            service_account_info = json.loads(service_account_json_string)
            
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)
            
            st.success("Firebase Terhubung!")
            return firestore.client()
        except Exception as e:
            st.error(f"Gagal menginisialisasi Firebase. Pastikan ENV VAR FIREBASE_SERVICE_ACCOUNT sudah diset dan format JSON-nya benar. Error: {e}")
            return None
    
    # Jika sudah terinisialisasi, kembalikan client
    return firestore.client()

# Inisialisasi Firestore client
# Variabel DB akan menampung instance Firestore client
DB = initialize_firebase()


# --- FUNGSI KEAMANAN (BCRYPT) ---

def hash_password(password):
    """Mengubah password menjadi hash menggunakan bcrypt."""
    # Salt (garam) ditambahkan otomatis oleh gensalt()
    return hashpw(password.encode('utf-8'), gensalt()).decode('utf-8')

def check_password_local(password, hashed_password):
    """Memverifikasi password yang dimasukkan dengan hash yang tersimpan."""
    try:
        # Hashed password dari Firestore harus berupa byte string
        if isinstance(hashed_password, str):
            hashed_password = hashed_password.encode('utf-8')
            
        return checkpw(password.encode('utf-8'), hashed_password)
    except ValueError:
        return False # Jika hash tidak valid

# --- FUNGSI AUTENTIKASI DENGAN FIRESTORE ---

def get_user_from_firestore(username):
    """Mengambil data pengguna dari koleksi 'users' di Firestore."""
    if not DB:
        return None # Firebase gagal terinisialisasi
        
    try:
        # Struktur data: /users/{username}
        # Gunakan username sebagai Document ID (nomor HP)
        doc_ref = DB.collection("users").document(username)
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict()
        else:
            return None
    except Exception as e:
        st.error(f"Error saat mengambil data user dari Firestore: {e}")
        return None

def authenticate_user(username, password):
    """Mencoba mengautentikasi pengguna menggunakan Firestore."""
    
    # Pastikan koneksi DB ada
    if not DB:
        st.error("Sistem Autentikasi sedang tidak tersedia (Koneksi Database Gagal).")
        return False

    user_data = get_user_from_firestore(username)
    
    if user_data:
        # Ambil hashed password (diasumsikan kolomnya bernama 'password_hash')
        hashed_password = user_data.get('password_hash')
        
        if hashed_password and check_password_local(password, hashed_password):
            st.session_state.logged_in = True
            st.session_state.username = username
            st.success(f"Login Berhasil! Selamat datang, {username}.")
            st.rerun() # Refresh untuk menampilkan aplikasi utama
            return True
        else:
            return False
    else:
        return False

# --- UI LOGIN ---

def login_form():
    """Menampilkan form login."""
    st.title("🔒 IDX Screener: Akses Terbatas")
    st.subheader("Silakan Login untuk Melanjutkan")

    if not DB:
        # Tampilkan error inisialisasi di awal jika gagal
        st.warning("Perlu Inisialisasi Firebase. Silakan periksa pesan error merah di atas.")
        
    with st.form("login_form"):
        username = st.text_input("Nomor HP / Username", placeholder="08xxxxxxxxxx (Username Firestore)")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

        if submitted:
            if authenticate_user(username, password):
                pass # Autentikasi berhasil, redirect di dalam fungsi authenticate_user
            else:
                st.error("Nomor HP atau Password salah, atau user tidak terdaftar di Firestore.")

# --- FUNGSI UTAMA APLIKASI ---

@st.cache_data(show_spinner=False)
def fetch_data(tickers, period_data="3y"):
    """Mengambil data harga dari Yahoo Finance."""
    
    # Menghindari tickers kosong jika input area kosong
    if not tickers:
        st.warning("Silakan masukkan minimal satu Ticker Saham (mis. BBCA).")
        return pd.DataFrame()

    with st.spinner(f"Mengambil data {len(tickers)} saham..."):
        try:
            data = yf.download(tickers, period=period_data, progress=False)
            
            if data.empty:
                st.error("Gagal mengambil data. Pastikan tickers yang dimasukkan sudah benar.")
                return pd.DataFrame()
            
            # Jika hanya satu ticker, yf.download tidak mengembalikan MultiIndex. Kita perbaiki strukturnya.
            if len(tickers) == 1:
                data.columns = pd.MultiIndex.from_product([data.columns, tickers])
            
            return data
        except Exception as e:
            st.error(f"Error saat mengambil data: {e}")
            return pd.DataFrame()

def parse_tickers(text_input):
    """Membersihkan dan memformat input teks menjadi daftar ticker yang valid."""
    # Bersihkan input: pisahkan berdasarkan spasi, koma, atau baris baru
    tickers = re.split(r'[,\s\n]+', text_input.strip())
    
    # Filter dan format
    cleaned_tickers = []
    for t in tickers:
        t = t.strip().upper()
        if t:
            # Tambahkan .JK jika belum ada, kecuali untuk IHSG (^JKSE)
            if t != "^JKSE" and not t.endswith(".JK"):
                cleaned_tickers.append(t + ".JK")
            else:
                cleaned_tickers.append(t)
                
    # Pastikan IHSG (indeks makro) selalu ada untuk analisis
    if "^JKSE" not in cleaned_tickers:
        cleaned_tickers.insert(0, "^JKSE") 
        
    return list(set(cleaned_tickers)) # Hapus duplikat

@st.cache_data(show_spinner=False)
def calculate_indicators(data, ihsg_ticker="^JKSE", rsi_period=14, vol_avg_period=20, pct_change_period=5):
    """Menghitung semua indikator yang diminta pada data historis."""
    
    # DataFrame kosong untuk hasil
    results = []
    
    # Ambil data IHSG
    ihsg_data = data['Close'][ihsg_ticker].ffill().iloc[-1]
    ihsg_prev_close = data['Close'][ihsg_ticker].ffill().iloc[-2]
    ihsg_change_pct = (ihsg_data - ihsg_prev_close) / ihsg_prev_close * 100

    for ticker in [t for t in data.columns.levels[1] if t != ihsg_ticker]:
        df = data.loc[:, (slice(None), ticker)]
        df.columns = df.columns.droplevel(1)

        if df.empty or len(df) < max(2, rsi_period, vol_avg_period, pct_change_period, 50):
            continue

        # --- INDIKATOR HARGA & RASIO ---
        price = df['Close'].iloc[-1]
        open_price = df['Open'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        prev_2_close = df['Close'].iloc[-3]
        
        # Rasio Pembukaan / Penutupan Kemarin (Gap Overnight)
        open_close_ratio = open_price / prev_close
        
        # Persentase Kenaikan Historis (N hari)
        pct_change_n = (price / df['Close'].iloc[-pct_change_period]) - 1

        # --- INDIKATOR MA TETAP ---
        df['SMA_3'] = ta.trend.sma_indicator(df['Close'], window=3)
        df['SMA_5'] = ta.trend.sma_indicator(df['Close'], window=5)
        df['SMA_10'] = ta.trend.sma_indicator(df['Close'], window=10)
        df['SMA_20'] = ta.trend.sma_indicator(df['Close'], window=20)
        df['SMA_50'] = ta.trend.sma_indicator(df['Close'], window=50)

        # --- INDIKATOR RSI ---
        df['RSI'] = ta.momentum.rsi(df['Close'], window=rsi_period)

        # --- INDIKATOR VOLUME ---
        vol_avg = df['Volume'].iloc[-vol_avg_period:].mean()

        # --- INDIKATOR GAP HISTORIS (N HARI) ---
        # Hitung Gap Ratio: Open[t] / Close[t-1]
        df['Gap_Ratio'] = df['Open'] / df['Close'].shift(1)
        
        # Filter data dalam N hari terakhir (pct_change_period)
        historical_gap_df = df['Gap_Ratio'].iloc[-pct_change_period:]
        
        # Hitung Gap Up, Gap Down, dan Average Gap Up %
        gap_up_count = (historical_gap_df > 1).sum()
        gap_down_count = (historical_gap_df < 1).sum()
        
        # Hitung persentase gap (hanya untuk gap up)
        gap_up_pct = (historical_gap_df[historical_gap_df > 1] - 1) * 100
        avg_gap_up_pct = gap_up_pct.mean() if gap_up_count > 0 else 0.0
        
        # Ambil nilai terakhir
        last_row = df.iloc[-1].fillna(0)
        
        # Siapkan dictionary hasil untuk scoring
        result = {
            'Ticker': ticker,
            'Price': price,
            'Open': open_price,
            'High': df['High'].iloc[-1],
            'Low': df['Low'].iloc[-1],
            'Volume': df['Volume'].iloc[-1],
            'Vol_Avg': vol_avg,
            'Prev_Close': prev_close,
            'Prev_2_Close': prev_2_close,
            'Open_Close_Ratio': open_close_ratio,
            'Pct_Change_N': pct_change_n,
            
            # Nilai MA
            'SMA_3': last_row['SMA_3'],
            'SMA_5': last_row['SMA_5'],
            'SMA_10': last_row['SMA_10'],
            'SMA_20': last_row['SMA_20'],
            'SMA_50': last_row['SMA_50'],
            
            # Nilai Momentum
            'RSI': last_row['RSI'],
            
            # Nilai Gap Historis (Baru)
            'Gap_Up_Count': gap_up_count,
            'Gap_Down_Count': gap_down_count,
            'Avg_Gap_Up_Pct': avg_gap_up_pct,

            # Nilai Makro (IHSG)
            'IHSG_Prev_Close': ihsg_prev_close,
            'IHSG_Change_Pct': ihsg_change_pct,
            
            # Placeholder untuk Scoring
            'Score': 0,
            'Rationale': []
        }
        results.append(result)

    return pd.DataFrame(results)

def apply_custom_rules(df, rules, buy_threshold, sell_threshold):
    """Menerapkan aturan kustom dinamis dan menghitung skor."""
    
    # Pastikan rules tidak kosong
    if not rules or not rules.strip():
        df['Rationale'] = 'No Rules Applied'
        df['Rekomendasi'] = 'HOLD'
        return df

    # Bersihkan rules (hapus baris kosong)
    rule_list = [r.strip() for r in rules.split('\n') if r.strip()]
    
    # Hitung skor untuk setiap saham
    for index, row in df.iterrows():
        score = 0
        rationale = []
        
        # Iterasi melalui setiap rule
        for rule_text in rule_list:
            # Gunakan row.to_dict() untuk membuat namespace variabel lokal
            local_vars = row.to_dict()
            
            try:
                # Menjalankan rule (eval) di konteks variabel lokal saham
                # Catatan: Ini adalah fitur kuat, tapi harus hati-hati di production!
                if eval(rule_text, {'__builtins__': None}, local_vars):
                    score += 1
                    rationale.append(f"LULUS: {rule_text}")
            except Exception as e:
                # Jika rule gagal (misal: penamaan variabel salah)
                rationale.append(f"ERROR: {rule_text} ({e})")
                
        # Simpan skor dan alasan
        df.loc[index, 'Score'] = score
        df.loc[index, 'Rationale'] = ' | '.join(rationale)
        
    # Tentukan rekomendasi berdasarkan skor
    df['Rekomendasi'] = np.select(
        [df['Score'] >= buy_threshold, df['Score'] < sell_threshold],
        ['BUY', 'SELL'],
        default='HOLD'
    )
    
    return df

def main_app():
    """Formulir dan tampilan utama aplikasi setelah login."""
    
    st.title("📈 IDX Stock Screener (Akses Aman)")
    
    st.sidebar.button("Logout", on_click=logout_user)
    st.sidebar.write(f"Selamat datang, User: **{st.session_state.username}**!")
    
    # --- SIDEBAR: KONFIGURASI ANALISIS ---
    st.sidebar.header("⚙️ Konfigurasi Analisis")
    
    # 1. Input Tickers Saham
    default_tickers = "BBCA\nTLKM\nASII\nANTM\n^JKSE"
    ticker_input = st.sidebar.text_area("Input Tickers Saham (Pisahkan dengan baris baru/koma/spasi)", default_tickers, height=150)
    
    # Parse Tickers
    tickers = parse_tickers(ticker_input)
    
    # 2. Parameter Indikator
    st.sidebar.subheader("Parameter Indikator")
    rsi_period = st.sidebar.slider("Periode RSI", min_value=7, max_value=30, value=14)
    vol_avg_period = st.sidebar.slider("Periode Volume Rata-rata (N Hari)", min_value=10, max_value=50, value=20)
    pct_change_period = st.sidebar.slider("Kenaikan Historis (N Hari)", min_value=1, max_value=60, value=5)
    
    # 3. Aturan Kustom
    st.sidebar.subheader("Aturan Skrining Kustom (Skor)")
    default_rules = """
Price > SMA_50
SMA_5 > SMA_20
RSI < 70
Open_Close_Ratio > 1.000
"""
    custom_rules = st.sidebar.text_area("Masukkan Aturan Boolean (1 baris = 1 skor)", default_rules, height=200)

    # --- PANDUAN VARIABEL (Tambahan Baru) ---
    with st.sidebar.expander("❓ Lihat Daftar Variabel untuk Rules"):
        st.markdown(
            """
            Gunakan nama variabel ini **persis** (case-sensitive) saat membuat aturan.
            Contoh: `RSI < 30` atau `SMA_20 > SMA_50`.
            
            | Kategori | Variabel | Deskripsi |
            | :--- | :--- | :--- |
            | **Harga** | `Price`, `Open`, `High`, `Low` | Harga hari ini. |
            | **Historis** | `Prev_Close`, `Prev_2_Close` | Penutupan 1 & 2 hari lalu. |
            | **Rasio** | `Open_Close_Ratio` | Pembukaan / Penutupan Kemarin. |
            | **Gain** | `Pct_Change_N` | Gain % selama N Hari (lihat slider). |
            | **MA** | `SMA_3`, `SMA_5`, `SMA_10`, `SMA_20`, `SMA_50` | Moving Average Tetap. |
            | **Momentum** | `RSI` | Relative Strength Index (sesuai slider). |
            | **Volume** | `Volume`, `Vol_Avg` | Volume hari ini & rata-rata N hari. |
            | **Gap Histori** | `Gap_Up_Count`, `Gap_Down_Count`, `Avg_Gap_Up_Pct` | Metrik Gap dalam N Hari. |
            | **Makro** | `IHSG_Change_Pct` | Perubahan % IHSG. |
            """
        )

    # 4. Threshold Skor
    buy_threshold = st.sidebar.number_input("Skor Minimal untuk Rekomendasi BUY", min_value=1, value=3)
    sell_threshold = st.sidebar.number_input("Skor Minimal untuk Rekomendasi SELL", min_value=0, max_value=buy_threshold - 1, value=1)
    
    st.sidebar.markdown("---")
    
    # Tombol Jalankan
    run_analysis = st.sidebar.button("🚀 Jalankan Analisis Saham", type="primary")

    # --- BODY UTAMA: HASIL ANALISIS ---

    if run_analysis or 'df_results' in st.session_state:
        # Panggil data
        data = fetch_data(tickers)
        
        if not data.empty:
            
            # 1. Hitung Indikator
            df_results = calculate_indicators(data, rsi_period=rsi_period, vol_avg_period=vol_avg_period, pct_change_period=pct_change_period)
            
            # 2. Tampilkan Status IHSG (Makro)
            ihsg_change = df_results['IHSG_Change_Pct'].iloc[0]
            ihsg_status = "Menguat" if ihsg_change > 0 else "Melemah"
            ihsg_color = "green" if ihsg_change > 0 else "red"
            
            st.markdown(f"**Indeks Makro (IHSG):** IHSG ditutup {ihsg_status} sebesar **{ihsg_change:.2f}%**.")
            
            # 3. Terapkan Aturan Kustom
            df_results = apply_custom_rules(df_results, custom_rules, buy_threshold, sell_threshold)
            
            # 4. Sortir Hasil
            # Urutkan berdasarkan Rasio Pembukaan, Gain N Hari, dan Skor (Descending)
            df_results = df_results.sort_values(by=['Open_Close_Ratio', 'Pct_Change_N', 'Score'], ascending=[False, False, False]).reset_index(drop=True)
            
            # 5. Styling dan Tampilan Akhir
            
            # Kolom yang ditampilkan di tabel hasil
            display_cols = [
                'Rekomendasi', 'Score', 'Ticker', 
                'Open_Close_Ratio', 'Pct_Change_N', 
                'Gap_Up_Count', 'Gap_Down_Count', 'Avg_Gap_Up_Pct', # Metrik Gap Baru
                'Price', 'Volume', 'Vol_Avg',
                'RSI', 
                'SMA_10', 'SMA_20', 'SMA_50', 
                'Rationale'
            ]
            
            df_display = df_results[display_cols].copy()
            
            # Format kolom numerik untuk tampilan yang lebih rapi
            df_display['Open_Close_Ratio'] = (df_display['Open_Close_Ratio'] - 1) * 100
            df_display['Pct_Change_N'] = df_display['Pct_Change_N'] * 100

            df_display = df_display.round({
                'Open_Close_Ratio': 2,
                'Pct_Change_N': 2,
                'Price': 0,
                'RSI': 2,
                'Volume': 0,
                'Vol_Avg': 0,
                'SMA_10': 0, 'SMA_20': 0, 'SMA_50': 0,
                'Avg_Gap_Up_Pct': 2, # Rounding untuk Avg Gap Up
            })
            
            df_display = df_display.rename(columns={
                'Open_Close_Ratio': 'Gap %',
                'Pct_Change_N': f'Gain {pct_change_period} Hari %',
                'Vol_Avg': 'Vol Avg',
                'RSI': 'RSI',
                'Price': 'Close Price',
                'Rationale': 'Alasan LULUS Rules',
                'Gap_Up_Count': f'Up {pct_change_period} Hari', # Rename kolom Gap Up Count
                'Gap_Down_Count': f'Down {pct_change_period} Hari', # Rename kolom Gap Down Count
                'Avg_Gap_Up_Pct': 'Avg Up %', # Rename kolom Avg Gap Up
            })
            
            # Styling Warna untuk Rekomendasi
            def color_recommendation(val):
                color = 'background-color: #38c47a30' if val == 'BUY' else \
                        'background-color: #ff4b4b30' if val == 'SELL' else \
                        'background-color: #ffc40030'
                return color
            
            st.dataframe(
                df_display.style.applymap(color_recommendation, subset=['Rekomendasi']),
                use_container_width=True,
                hide_index=True
            )
            
            # 6. Rangkuman Hasil
            buy_count = len(df_results[df_results['Rekomendasi'] == 'BUY'])
            hold_count = len(df_results[df_results['Rekomendasi'] == 'HOLD'])
            sell_count = len(df_results[df_results['Rekomendasi'] == 'SELL'])

            st.markdown("---")
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Rekomendasi BUY", buy_count)
            col2.metric("Total Rekomendasi HOLD", hold_count)
            col3.metric("Total Rekomendasi SELL", sell_count)
            
            # 7. Download CSV
            csv_data = df_results.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="⬇️ Export Hasil Analisis ke CSV",
                data=csv_data,
                file_name=f'screener_results_{pd.Timestamp.now().strftime("%Y%m%d")}.csv',
                mime='text/csv',
                help='Download seluruh tabel hasil skrining (termasuk data mentah).'
            )

def logout_user():
    """Melakukan logout dan reset session state."""
    st.session_state.logged_in = False
    st.session_state.username = None
    st.rerun()

# --- ENTRY POINT APLIKASI ---

def app_entry():
    """Fungsi utama untuk menentukan apakah user harus login atau masuk ke aplikasi."""
    
    if st.session_state.logged_in:
        main_app()
    else:
        login_form()

# Panggil entry point
app_entry()
