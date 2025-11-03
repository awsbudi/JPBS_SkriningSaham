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
import uuid # Untuk userId anonim jika diperlukan

# --- KONFIGURASI APLIKASI DAN INITIALISASI FIREBASE ---

# Konstanta untuk perhitungan Gap yang diminta user (3 bulan perdagangan = 60 hari)
GAP_FIXED_DAYS = 60

# Set configuration page Streamlit
st.set_page_config(
    page_title="IDX Stock Screener & Jurnal",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Inisialisasi session state untuk status login
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'username' not in st.session_state:
    st.session_state.username = None
if 'user_id' not in st.session_state:
    st.session_state.user_id = str(uuid.uuid4()) # Default ID unik jika tidak login


# Fungsi inisialisasi Firebase (hanya dijalankan sekali)
def initialize_firebase():
    """Menginisialisasi Firebase Admin SDK menggunakan kredensial JSON."""
    if not firebase_admin._apps:
        try:
            service_account_json_string = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
            
            if not service_account_json_string:
                # Ini akan terjadi di lokal jika ENV tidak diset, tapi biarkan UI Streamlit yang menampilkan error
                return None 
                
            service_account_info = json.loads(service_account_json_string)
            
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)
            
            # st.success("Firebase Terhubung!") # Jangan tampilkan di setiap refresh
            return firestore.client()
        except Exception as e:
            # st.error(f"Gagal menginisialisasi Firebase. Error: {e}")
            return None
    
    return firestore.client()

# Inisialisasi Firestore client
DB = initialize_firebase()


# --- FUNGSI BCRYPT & AUTENTIKASI ---

def check_password_local(password, hashed_password):
    """Memverifikasi password yang dimasukkan dengan hash yang tersimpan."""
    try:
        if isinstance(hashed_password, str):
            hashed_password = hashed_password.encode('utf-8')
            
        return checkpw(password.encode('utf-8'), hashed_password)
    except ValueError:
        return False

def get_user_from_firestore(username):
    """Mengambil data pengguna dari koleksi 'users' di Firestore."""
    if not DB: return None
        
    try:
        # Gunakan username sebagai Document ID (nomor HP)
        doc_ref = DB.collection("users").document(username)
        doc = doc_ref.get()
        if doc.exists:
            # Karena kita menggunakan nomor HP sebagai ID, kita bisa set user_id = username
            st.session_state.user_id = username 
            return doc.to_dict()
        else:
            return None
    except Exception as e:
        # st.error(f"Error saat mengambil data user dari Firestore: {e}")
        return None

def authenticate_user(username, password):
    """Mencoba mengautentikasi pengguna menggunakan Firestore."""
    
    if not DB:
        st.error("Sistem Autentikasi sedang tidak tersedia (Koneksi Database Gagal).")
        return False

    user_data = get_user_from_firestore(username)
    
    if user_data:
        hashed_password = user_data.get('password_hash')
        
        if hashed_password and check_password_local(password, hashed_password):
            st.session_state.logged_in = True
            st.session_state.username = username
            st.success(f"Login Berhasil! Selamat datang, {username}.")
            st.rerun()
            return True
        else:
            return False
    else:
        return False

def logout_user():
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.user_id = str(uuid.uuid4())
    st.rerun()

def login_form():
    """Menampilkan form login."""
    st.title("🔒 JPBS Screener: Akses Terbatas Hanya untuk Member JPBS")
    st.subheader("Silakan Login untuk Melanjutkan")

    if not DB:
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

# --- FIREBASE JOURNAL FUNCTIONS ---

def get_journal_ref(user_id):
    """Mendapatkan referensi koleksi jurnal berdasarkan user_id."""
    if not DB: return None
    # Path: /artifacts/{appId}/users/{userId}/journal/transactions
    # Karena kita tidak memiliki __app_id di lingkungan ini, kita pakai fixed path
    return DB.collection("users").document(user_id).collection("transactions")

def save_transaction(user_id, ticker, price, shares, action):
    """Menyimpan transaksi beli atau jual ke Firestore."""
    ref = get_journal_ref(user_id)
    if not ref: return False

    try:
        ref.add({
            'ticker': ticker,
            'price': price,
            'shares': shares,
            'action': action,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'is_open': True if action == 'BUY' else False
        })
        return True
    except Exception as e:
        st.error(f"Gagal menyimpan transaksi ke Firestore: {e}")
        return False

def fetch_portfolio_summary(user_id):
    """Mengambil dan merangkum transaksi jurnal user."""
    ref = get_journal_ref(user_id)
    if not ref: 
        df = pd.DataFrame(columns=['ticker', 'total_shares', 'total_cost', 'BEP'])
        return df

    try:
        # Hanya ambil transaksi beli yang belum terjual (is_open = True)
        # Query ini memerlukan Indeks Komposit di Firestore: where('is_open') + order_by('ticker')
        open_trades_docs = ref.where('is_open', '==', True).order_by('ticker').get()
        
        records = [doc.to_dict() for doc in open_trades_docs]
        if not records:
            # Pastikan DataFrame kosong memiliki kolom yang diharapkan untuk mencegah KeyError
            df = pd.DataFrame(columns=['ticker', 'total_shares', 'total_cost', 'BEP'])
            return df

        df_trades = pd.DataFrame(records)
        
        # Grouping untuk mendapatkan BEP (Break-Even Price) dan Total Shares
        df_summary = df_trades.groupby('ticker').agg(
            total_shares=('shares', 'sum'),
            total_cost=('price', lambda x: (x * df_trades.loc[x.index, 'shares']).sum())
        ).reset_index()

        df_summary['BEP'] = df_summary['total_cost'] / df_summary['total_shares']
        
        # Filter keluar trades dengan 0 shares (sudah terjual habis)
        df_summary = df_summary[df_summary['total_shares'] > 0]
        
        return df_summary
        
    except Exception as e:
        # st.error(f"Gagal mengambil jurnal dari Firestore: {e}") # Jangan tampilkan ke user
        df = pd.DataFrame(columns=['ticker', 'total_shares', 'total_cost', 'BEP'])
        return df

# --- FUNGSI DATA & INDIKATOR ---

@st.cache_data(show_spinner=False)
def fetch_data(tickers, period_data="3y"):
    """Mengambil data harga dari Yahoo Finance."""
    if not tickers:
        st.warning("Silakan masukkan minimal satu Ticker Saham.")
        return pd.DataFrame()

    with st.spinner(f"Mengambil data {len(tickers)} saham..."):
        try:
            # Force download untuk mendapatkan MultiIndex (Walaupun hanya 1 Ticker)
            data = yf.download(tickers, period=period_data, progress=False)
            
            if data.empty:
                st.error("Gagal mengambil data. Pastikan tickers yang dimasukkan sudah benar.")
                return pd.DataFrame()
            
            # Menangani kasus satu ticker yang tidak menghasilkan MultiIndex (walaupun sudah di-force)
            if len(tickers) == 1 and data.columns.nlevels == 1:
                 # Buat MultiIndex manual
                data.columns = pd.MultiIndex.from_product([data.columns, [tickers[0]]])
            
            return data
        except Exception as e:
            st.error(f"Error saat mengambil data: {e}")
            return pd.DataFrame()

def parse_tickers(text_input):
    """Membersihkan dan memformat input teks menjadi daftar ticker yang valid."""
    tickers = re.split(r'[,\s\n]+', text_input.strip())
    
    cleaned_tickers = []
    for t in tickers:
        t = t.strip().upper()
        if t:
            if t != "^JKSE" and not t.endswith(".JK"):
                cleaned_tickers.append(t + ".JK")
            else:
                cleaned_tickers.append(t)
                
    if "^JKSE" not in cleaned_tickers:
        cleaned_tickers.insert(0, "^JKSE") 
        
    return list(set(cleaned_tickers))

@st.cache_data(show_spinner=False)
def calculate_indicators(data, ihsg_ticker="^JKSE", rsi_period=14, vol_avg_period=20, pct_change_period=5):
    """Menghitung semua indikator yang diminta pada data historis."""
    
    results = []
    
    # Ambil data IHSG
    ihsg_data = data['Close'][ihsg_ticker].ffill()
    if len(ihsg_data) < 2: return pd.DataFrame()
    ihsg_change_pct = (ihsg_data.iloc[-1] / ihsg_data.iloc[-2] - 1) * 100

    for ticker in [t for t in data.columns.levels[1] if t != ihsg_ticker]:
        df = data.loc[:, (slice(None), ticker)]
        df.columns = df.columns.droplevel(1)

        if df.empty or len(df) < max(GAP_FIXED_DAYS, rsi_period, vol_avg_period, pct_change_period, 50):
            continue

        # --- INDIKATOR HARGA & RASIO HARI INI ---
        price = df['Close'].iloc[-1]
        open_price = df['Open'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        
        # Rasio Pembukaan / Penutupan Kemarin (Gap Overnight)
        open_close_ratio = open_price / prev_close
        
        # Persentase Kenaikan Historis (N hari) - Menggunakan slider
        if len(df) > pct_change_period:
            pct_change_n = (price / df['Close'].iloc[-pct_change_period - 1]) - 1
        else:
            pct_change_n = 0

        # --- INDIKATOR GAP 3 BULAN (FIXED 60 HARI) ---
        gap_data = df.tail(GAP_FIXED_DAYS)
        
        gap_up_count = 0
        gap_down_count = 0
        total_gap_up_pct = 0
        
        # Iterasi dari hari ke-2 data 60 hari (membandingkan Open hari ini dengan Close kemarin)
        for i in range(1, len(gap_data)):
            open_i = gap_data['Open'].iloc[i]
            close_prev = gap_data['Close'].iloc[i-1]
            
            if open_i > close_prev:
                gap_up_count += 1
                total_gap_up_pct += (open_i / close_prev) - 1
            elif open_i < close_prev:
                gap_down_count += 1

        avg_gap_up_pct = (total_gap_up_pct / gap_up_count) if gap_up_count > 0 else 0

        # --- INDIKATOR MA & RSI ---
        df['SMA_3'] = ta.trend.sma_indicator(df['Close'], window=3)
        df['SMA_5'] = ta.trend.sma_indicator(df['Close'], window=5)
        df['SMA_10'] = ta.trend.sma_indicator(df['Close'], window=10)
        df['SMA_20'] = ta.trend.sma_indicator(df['Close'], window=20)
        df['SMA_50'] = ta.trend.sma_indicator(df['Close'], window=50)
        df['RSI'] = ta.momentum.rsi(df['Close'], window=rsi_period)

        # --- INDIKATOR VOLUME ---
        vol_avg = df['Volume'].iloc[-vol_avg_period:].mean()

        last_row = df.iloc[-1].fillna(0)
        
        # Siapkan dictionary hasil untuk scoring
        result = {
            'Ticker': ticker,
            'Price': price,
            'Open_Close_Ratio': open_close_ratio,
            'Pct_Change_N': pct_change_n,
            'Gap_Up_Count': gap_up_count,
            'Gap_Down_Count': gap_down_count,
            'Avg_Gap_Up_Pct': avg_gap_up_pct,
            'Volume': df['Volume'].iloc[-1],
            'Vol_Avg': vol_avg,
            'SMA_3': last_row['SMA_3'], 'SMA_5': last_row['SMA_5'], 'SMA_10': last_row['SMA_10'],
            'SMA_20': last_row['SMA_20'], 'SMA_50': last_row['SMA_50'],
            'RSI': last_row['RSI'],
            'IHSG_Change_Pct': ihsg_change_pct,
            'Score': 0,
            'Rationale': []
        }
        results.append(result)

    return pd.DataFrame(results)

def apply_custom_rules(df, mandatory_rules, scoring_rules, buy_threshold, sell_threshold):
    """Menerapkan aturan kustom dua tahap dan menghitung skor."""
    
    df['Mandatory_Pass'] = True
    
    # 1. TAHAP WAJIB (MANDATORY)
    mandatory_list = [r.strip() for r in mandatory_rules.split('\n') if r.strip()]

    for index, row in df.iterrows():
        mandatory_pass = True
        for rule_text in mandatory_list:
            local_vars = row.to_dict()
            try:
                if not eval(rule_text, {'__builtins__': None}, local_vars):
                    mandatory_pass = False
                    break
            except Exception:
                mandatory_pass = False # Gagal jika rule syntax error

        df.loc[index, 'Mandatory_Pass'] = mandatory_pass
        df.loc[index, 'Rationale'] = ['Gagal Syarat Wajib'] if not mandatory_pass else []
        
    # 2. TAHAP SKORING (Hanya untuk yang LULUS atau Default)
    scoring_list = [r.strip() for r in scoring_rules.split('\n') if r.strip()]
    
    for index, row in df.iterrows():
        score = 0
        rationale = row['Rationale']
        
        if row['Mandatory_Pass']:
            rationale = []
            for rule_text in scoring_list:
                local_vars = row.to_dict()
                try:
                    if eval(rule_text, {'__builtins__': None}, local_vars):
                        score += 1
                        rationale.append(f"LULUS: {rule_text}")
                except Exception:
                    pass # Abaikan scoring rule yang error
                
            df.loc[index, 'Score'] = score
            df.loc[index, 'Rationale'] = ' | '.join(rationale)

    # 3. PENENTUAN REKOMENDASI
    df['Rekomendasi'] = np.select(
        [
            (df['Mandatory_Pass'] == True) & (df['Score'] >= buy_threshold),
            (df['Mandatory_Pass'] == True) & (df['Score'] < sell_threshold),
            (df['Mandatory_Pass'] == False)
        ],
        ['BUY', 'SELL', 'HOLD (Gagal Syarat Wajib)'],
        default='HOLD'
    )
    
    return df

# --- DOWNLOAD CSV ---
def convert_df_to_csv(df):
    return df.to_csv(index=False).encode('utf-8')

# --- UI APP MAIN ---

def display_screener(df_results_all):
    """Menampilkan tab Screener."""
    
    st.subheader("1. Tabel Hasil Skrining")
    
    # 1. Tampilkan Status IHSG (Makro)
    ihsg_change = df_results_all['IHSG_Change_Pct'].iloc[0]
    ihsg_status = "Menguat" if ihsg_change > 0 else "Melemah"
    color = "green" if ihsg_change > 0 else "red"
    
    if color == "green":
        st.success(f"**Indeks Makro (IHSG):** IHSG ditutup {ihsg_status} sebesar **{ihsg_change:.2f}%**.")
    else:
        st.error(f"**Indeks Makro (IHSG):** IHSG ditutup {ihsg_status} sebesar **{ihsg_change:.2f}%**.")

    # 2. Rangkuman Hasil
    buy_count = len(df_results_all[df_results_all['Rekomendasi'] == 'BUY'])
    hold_count = len(df_results_all[df_results_all['Rekomendasi'] == 'HOLD']) + len(df_results_all[df_results_all['Rekomendasi'] == 'HOLD (Gagal Syarat Wajib)'])
    sell_count = len(df_results_all[df_results_all['Rekomendasi'] == 'SELL'])

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Rekomendasi BUY", buy_count, delta_color="normal")
    col2.metric("Total Rekomendasi HOLD", hold_count, delta_color="off")
    col3.metric("Total Rekomendasi SELL", sell_count, delta_color="inverse")
    
    st.markdown("---")
    
    # 3. Tampilan Tabel
    # Kolom yang ditampilkan di tabel hasil
    display_cols = [
        'Rekomendasi', 'Score', 'Ticker', 
        'Open_Close_Ratio', 'Pct_Change_N', 
        'Gap_Up_Count', 'Gap_Down_Count', 'Avg_Gap_Up_Pct',
        'Price', 'Volume', 'Vol_Avg',
        'RSI', 
        'SMA_10', 'SMA_20', 'SMA_50', 
        'Rationale'
    ]
    
    df_display = df_results_all[display_cols].copy()
    
    # Format kolom numerik
    df_display['Open_Close_Ratio'] = (df_display['Open_Close_Ratio'] - 1) * 100
    df_display['Pct_Change_N'] = df_display['Pct_Change_N'] * 100
    df_display['Avg_Gap_Up_Pct'] = df_display['Avg_Gap_Up_Pct'] * 100

    df_display = df_display.round({
        'Open_Close_Ratio': 2, 'Pct_Change_N': 2, 'Avg_Gap_Up_Pct': 2,
        'Price': 0, 'Volume': 0, 'Vol_Avg': 0, 'RSI': 2,
        'SMA_10': 0, 'SMA_20': 0, 'SMA_50': 0,
    })
    
    df_display = df_display.rename(columns={
        'Open_Close_Ratio': 'Gap %',
        'Pct_Change_N': f'Gain {st.session_state.pct_change_period} Hari %',
        'Gap_Up_Count': f'Up {GAP_FIXED_DAYS} Hari',
        'Gap_Down_Count': f'Down {GAP_FIXED_DAYS} Hari',
        'Avg_Gap_Up_Pct': 'Avg Up %',
        'Vol_Avg': 'Vol Avg',
        'RSI': 'RSI',
        'Price': 'Close Price',
        'Rationale': 'Alasan LULUS Rules',
    })
    
    # Kolom untuk memilih saham yang akan dibeli (Selectable)
    df_display.insert(0, "Pilih", False)

    # Styling Warna untuk Rekomendasi
    def highlight_rekomendasi(row):
        color = 'background-color: #38c47a30' if row['Rekomendasi'] == 'BUY' else \
                'background-color: #ff4b4b30' if row['Rekomendasi'].startswith('SELL') else \
                'background-color: #ffc40030'
        return [color] * len(row)

    # Tampilkan DataFrame dengan selection
    selected_df = st.data_editor(
        df_display.style.apply(highlight_rekomendasi, axis=1),
        use_container_width=True,
        hide_index=True,
        column_order=["Pilih"] + list(df_display.columns[1:]),
        key="screener_table_editor"
    )
    
    # Simpan hasil analisis untuk digunakan saat jurnal (hanya saham yang terpilih)
    st.session_state.selected_picks = selected_df[selected_df["Pilih"] == True]

    # Tombol Aksi
    col_buy, col_csv = st.columns([1, 1])
    with col_buy:
        if st.session_state.selected_picks.empty:
            st.button("❌ Tambahkan ke Jurnal (Pilih Saham Dulu)", disabled=True)
        else:
            st.button("➕ Tambahkan ke Jurnal", on_click=add_picks_to_journal, type="primary")

    with col_csv:
        csv_data = convert_df_to_csv(df_results_all)
        st.download_button(
            label="⬇️ Export Semua Hasil ke CSV",
            data=csv_data,
            file_name=f'screener_results_{pd.Timestamp.now().strftime("%Y-%m-%d")}.csv',
            mime='text/csv'
        )
    
def add_picks_to_journal():
    """Mengambil saham yang terpilih dan menyimpannya ke state untuk form beli."""
    if 'selected_picks' not in st.session_state or st.session_state.selected_picks.empty:
        st.warning("Silakan pilih minimal satu saham dari tabel.")
        return
    
    st.session_state.journal_picks = st.session_state.selected_picks[['Ticker', 'Close Price']].to_dict('records')
    st.session_state.page = 'Jurnal'
    st.rerun()

# --- UI JURNAL ---

def display_journal(df_portfolio, current_prices):
    """Menampilkan tab Jurnal Portfolio."""
    
    st.subheader("2. Jurnal Portofolio dan Alokasi Dana")
    
    # 1. Total Modal dan Alokasi
    st.session_state.total_capital = st.number_input("Total Modal Investasi (Rp)", min_value=1000000, value=100000000, step=1000000)
    
    # 2. Form Pembelian Saham Terpilih (jika ada)
    if 'journal_picks' in st.session_state and st.session_state.journal_picks:
        st.markdown("### ➕ Tambahkan Saham Pilihan ke Jurnal")
        
        with st.form("buy_form"):
            ticker_list = [p['Ticker'].replace('.JK', '') for p in st.session_state.journal_picks]
            st.info(f"Pilihan Saham dari Screener: {', '.join(ticker_list)}")
            
            ticker_to_buy = st.selectbox("Saham yang Akan Dibeli", options=ticker_list)
            price_buy = st.number_input(f"Harga Beli Aktual {ticker_to_buy} (Rp)", min_value=1.0, step=1.0, value=float(st.session_state.journal_picks[0]['Close Price']))
            shares_buy = st.number_input("Jumlah Lot (1 Lot = 100 Saham)", min_value=1, value=10, step=1)
            
            submitted = st.form_submit_button("Simpan Transaksi BELI ke Firestore")
            
            if submitted:
                total_shares = shares_buy * 100
                if save_transaction(st.session_state.user_id, ticker_to_buy + '.JK', price_buy, total_shares, 'BUY'):
                    st.success(f"Berhasil menyimpan {total_shares} lembar {ticker_to_buy} di harga Rp{price_buy:,}.")
                    st.session_state.journal_picks = [] # Clear form
                    st.rerun()
                else:
                    st.error("Gagal menyimpan transaksi. Coba lagi.")

    st.markdown("---")
    
    # 3. Ringkasan Portofolio Aktif
    st.markdown("### 📊 Portofolio Aktif Anda")
    
    if df_portfolio.empty:
        st.info("Jurnal Anda masih kosong. Silakan beli saham dulu!")
    else:
        # Tambahkan Harga Terkini dan Hitung P&L
        tickers_to_fetch = df_portfolio['ticker'].unique().tolist()
        
        # Merge dengan harga terkini
        df_portfolio['current_price'] = df_portfolio['ticker'].apply(lambda t: current_prices.get(t, np.nan))
        df_portfolio = df_portfolio.dropna(subset=['current_price'])
        
        if not df_portfolio.empty:
            df_portfolio['current_value'] = df_portfolio['total_shares'] * df_portfolio['current_price']
            df_portfolio['P_L_nominal'] = df_portfolio['current_value'] - df_portfolio['total_cost']
            df_portfolio['P_L_persen'] = (df_portfolio['P_L_nominal'] / df_portfolio['total_cost']) * 100
            
            # Alokasi
            total_portfolio_value = df_portfolio['current_value'].sum()
            df_portfolio['allocation_pct'] = (df_portfolio['current_value'] / total_portfolio_value) * 100

            # Hitung Kas yang Tersisa
            total_invested = df_portfolio['total_cost'].sum()
            cash_available = st.session_state.total_capital - total_invested
            
            st.metric("Total Nilai Portofolio (Market Value)", f"Rp{total_portfolio_value:,.0f}")
            st.metric("Dana Kas Tersisa (dari Total Modal)", f"Rp{cash_available:,.0f}")
            
            df_display_journal = df_portfolio[['ticker', 'total_shares', 'BEP', 'current_price', 'current_value', 'P_L_nominal', 'P_L_persen', 'allocation_pct']].copy()
            
            df_display_journal = df_display_journal.rename(columns={
                'ticker': 'Ticker',
                'total_shares': 'Lembar (100)',
                'BEP': 'BEP (Rata-rata Beli)',
                'current_price': 'Harga Terkini',
                'current_value': 'Nilai Pasar',
                'P_L_nominal': 'P/L (Rp)',
                'P_L_persen': 'P/L (%)',
                'allocation_pct': 'Alokasi (%)'
            })
            
            df_display_journal['Lembar (100)'] = df_display_journal['Lembar (100)'] / 100

            st.dataframe(df_display_journal.round({'BEP': 0, 'Harga Terkini': 0, 'Nilai Pasar': 0, 'P/L (Rp)': 0, 'P/L (%)': 2, 'Alokasi (%)': 2}), use_container_width=True)
            
            # Form Jual (SELL)
            st.markdown("### ➖ Transaksi Jual (SELL)")
            with st.form("sell_form"):
                ticker_to_sell = st.selectbox("Saham yang Akan Dijual", options=df_portfolio['ticker'].unique().tolist())
                max_shares = df_portfolio[df_portfolio['ticker'] == ticker_to_sell]['total_shares'].iloc[0]
                
                price_sell = st.number_input(f"Harga Jual Aktual {ticker_to_sell} (Rp)", min_value=1.0, step=1.0, value=float(df_portfolio[df_portfolio['ticker'] == ticker_to_sell]['current_price'].iloc[0]))
                shares_sell = st.number_input(f"Jumlah Lot yang Dijual (Max: {max_shares/100} Lot)", min_value=1, max_value=int(max_shares/100), value=1, step=1)
                
                submitted_sell = st.form_submit_button("Simpan Transaksi JUAL ke Firestore")

                if submitted_sell:
                    total_shares_sell = shares_sell * 100
                    if total_shares_sell <= max_shares:
                        # Logic Jual Sederhana: Mencatat transaksi Jual
                        if save_transaction(st.session_state.user_id, ticker_to_sell, price_sell, -total_shares_sell, 'SELL'): # Shares negatif untuk Jual
                            st.success(f"Berhasil mencatat penjualan {total_shares_sell} lembar {ticker_to_sell} di harga Rp{price_sell:,}.")
                            st.rerun()
                        else:
                            st.error("Gagal mencatat transaksi jual.")
                    else:
                        st.error("Jumlah lot jual melebihi kepemilikan.")

# --- MAIN APP FLOW ---

def app_entry():
    """Mengelola alur tampilan: Login vs. Aplikasi Utama."""
    
    # Cek status inisialisasi Firebase di awal
    if not DB and not st.session_state.logged_in:
        # Hanya tampilkan pesan error jika DB gagal dan user belum login
        st.error("Koneksi Firebase Gagal. Silakan periksa ENV VAR FIREBASE_SERVICE_ACCOUNT.")
        
    if not st.session_state.logged_in:
        login_form()
    else:
        # Display tab selection
        st.sidebar.markdown("---")
        st.sidebar.markdown(f"**USER:** {st.session_state.username}")
        st.sidebar.button("Logout", on_click=logout_user)
        
        tab_titles = ["🚀 Screener", "📊 Jurnal & P&L"]
        tab1, tab2 = st.tabs(tab_titles)
        
        # Fetch portfolio summary and latest prices (for P&L)
        df_portfolio = fetch_portfolio_summary(st.session_state.user_id)
        
        # Tickers yang perlu diambil harganya (dari Screener + Jurnal)
        screener_tickers = parse_tickers(st.session_state.get('ticker_input', "BBCA\nTLKM\nASII\nANTM\n^JKSE"))
        journal_tickers = df_portfolio['ticker'].unique().tolist()
        
        all_tickers = list(set(screener_tickers + journal_tickers))
        
        # Fetch data historis untuk semua saham yang relevan
        data_all = fetch_data(all_tickers)
        current_prices = {}
        if not data_all.empty:
            # Ambil harga penutupan terakhir untuk P&L calculation
            for t in [tk for tk in all_tickers if tk != '^JKSE']:
                if ('Close', t) in data_all.columns:
                     # ffill() untuk memastikan nilai terakhir ada
                    price = data_all['Close'][t].ffill().iloc[-1]
                    current_prices[t] = price
        
        
        # --- TAB SCREENER ---
        with tab1:
            st.sidebar.header("⚙️ Konfigurasi Analisis")
            
            # 1. Input Tickers Saham
            default_tickers_input = st.session_state.get('ticker_input_default', "BBCA\nTLKM\nASII\nANTM")
            ticker_input = st.sidebar.text_area("1. Input Tickers Saham", default_tickers_input, height=150, key="ticker_input_screener")
            st.session_state.ticker_input_default = ticker_input
            
            # Parse Tickers
            tickers_for_screener = parse_tickers(ticker_input)
            
            # 2. Parameter Indikator
            st.sidebar.subheader("2. Parameter Indikator")
            rsi_period = st.sidebar.slider("Periode RSI", min_value=7, max_value=30, value=14, key='rsi_period')
            vol_avg_period = st.sidebar.slider("Periode Volume Rata-rata (N Hari)", min_value=10, max_value=50, value=20, key='vol_avg_period')
            pct_change_period = st.sidebar.slider("Kenaikan Historis (N Hari) - untuk Gain %", min_value=1, max_value=60, value=5, key='pct_change_period')
            st.session_state.pct_change_period = pct_change_period # Simpan untuk display

            # 3. Aturan Kustom
            st.sidebar.subheader("3. Aturan Skrining Kustom")
            
            # MANDATORY RULES (Wajib Lulus)
            default_mandatory = """
Open_Close_Ratio > 1.011
Volume > 500000000
Price > 50
RSI < 70
"""
            mandatory_rules = st.sidebar.text_area("Aturan Wajib Lulus (Mandatory Rules)", default_mandatory, height=150, key='mandatory_rules_input')

            # SCORING RULES
            default_scoring = """
SMA_5 > SMA_20
SMA_20 > SMA_50
Avg_Gap_Up_Pct > 0
"""
            scoring_rules = st.sidebar.text_area("Aturan Skoring (+1 per Lulus)", default_scoring, height=150, key='scoring_rules_input')

            # --- PANDUAN VARIABEL ---
            with st.sidebar.expander("❓ Lihat Daftar Variabel"):
                st.markdown(
                    """
                    Gunakan nama variabel ini **persis** (case-sensitive) saat membuat aturan.
                    **Gap/Frekuensi dihitung berdasarkan periode 60 hari tetap.**
                    
                    | Kategori | Variabel | Deskripsi |
                    | :--- | :--- | :--- |
                    | **Harga** | `Price`, `Open` | Harga hari ini. |
                    | **Historis** | `Prev_Close` | Penutupan 1 hari lalu. |
                    | **Rasio** | `Open_Close_Ratio` | Pembukaan / Penutupan Kemarin. |
                    | **Gain** | `Pct_Change_N` | Gain % selama N Hari (slider). |
                    | **Gap (60H)** | `Gap_Up_Count`, `Gap_Down_Count` | Frekuensi Gap Up/Down (60 hari). |
                    | **Gap (Avg)** | `Avg_Gap_Up_Pct` | Rata-rata % Gap Up (60 hari). |
                    | **MA** | `SMA_3`, `SMA_20`, `SMA_50` | MA Tetap (3, 20, 50 hari). |
                    | **Momentum** | `RSI` | Relative Strength Index (slider). |
                    """
                )

            # 4. Threshold Skor
            buy_threshold = st.sidebar.number_input("Skor Minimal untuk Rekomendasi BUY", min_value=1, value=3, key='buy_threshold')
            sell_threshold = st.sidebar.number_input("Skor Minimal untuk Rekomendasi SELL", min_value=0, max_value=buy_threshold - 1, value=1, key='sell_threshold')
            
            st.sidebar.markdown("---")
            
            run_analysis = st.sidebar.button("🚀 Jalankan Analisis Saham", type="primary", key="run_analysis_btn")

            # --- RUN LOGIC ---
            if run_analysis and data_all is not None and not data_all.empty:
                # Filter data_all hanya untuk tickers yang diinput di Screener
                data_screener_only = data_all.loc[:, (slice(None), tickers_for_screener)]

                with st.spinner("Menghitung indikator dan menerapkan aturan..."):
                    df_results = calculate_indicators(data_screener_only, 
                                                     rsi_period=rsi_period, 
                                                     vol_avg_period=vol_avg_period, 
                                                     pct_change_period=pct_change_period)
                    
                    if not df_results.empty:
                        df_results = apply_custom_rules(df_results, mandatory_rules, scoring_rules, buy_threshold, sell_threshold)
                        
                        # Sorting default untuk tampilan awal (momentum dan skor)
                        df_results = df_results.sort_values(by=['Open_Close_Ratio', 'Pct_Change_N', 'Score'], ascending=[False, False, False]).reset_index(drop=True)
                        
                        st.session_state.df_screener_results = df_results
                        
                        # Display results
                        display_screener(st.session_state.df_screener_results)
                    else:
                        st.warning("Tidak ada data yang valid untuk dianalisis.")
            
            elif 'df_screener_results' in st.session_state:
                 # Display cached results if not running new analysis
                display_screener(st.session_state.df_screener_results)
            
            else:
                st.info("Konfigurasikan parameter di sidebar dan klik 'Jalankan Analisis Saham'.")


        # --- TAB JURNAL ---
        with tab2:
            display_journal(df_portfolio, current_prices)

# --- EXECUTION ---
app_entry()