import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta

# --- KONSTANTA & INISIALISASI ---
# Tickers contoh IDX (Gunakan format .JK untuk Yahoo Finance)
DEFAULT_TICKERS = ["BBCA.JK", "TLKM.JK", "ASII.JK", "UNVR.JK"]
IHSG_TICKER = "^JKSE"
LOOKBACK_DAYS = 120 # Periode historis untuk perhitungan dan analisis

# Jendela Moving Average Tetap yang akan dihitung
FIXED_MA_WINDOWS = [3, 5, 10, 20, 50]

st.set_page_config(
    page_title="IDX Stock Screener (Dynamic Rules)",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- FUNGSI PENGAMBILAN DATA ---

@st.cache_data(ttl=3600) # Cache data selama 1 jam
def fetch_data(tickers, period="2y"):
    """Mengambil data historis untuk semua tickers yang dipilih."""
    st.info(f"Mengambil data historis untuk {len(tickers)} saham dan IHSG (^JKSE)... Mohon tunggu sebentar.")
    try:
        # Mengambil data IHSG
        ihsg_data = yf.download(IHSG_TICKER, period=period, interval="1d", progress=False)

        # Mengambil data saham
        stock_data = yf.download(tickers, period=period, interval="1d", progress=False)
        
        # Jika hanya satu ticker, yfinance mengembalikan Series, kita ubah agar konsisten
        if len(tickers) == 1 and isinstance(stock_data.columns, pd.Index):
            df_stock = stock_data
            # Set nama kolom agar sesuai dengan multi-index structure
            df_stock.columns = pd.MultiIndex.from_product([df_stock.columns, [tickers[0]]])
        else:
            df_stock = stock_data
        
        st.success("Data berhasil diambil!")
        return df_stock, ihsg_data
    except Exception as e:
        st.error(f"Gagal mengambil data dari Yahoo Finance. Cek koneksi internet Anda atau ticker yang dimasukkan. Error: {e}")
        return None, None

# --- FUNGSI PERHITUNGAN INDIKATOR ---
# Signature diperbarui, window SMA/EMA dihapus
def calculate_indicators(df, rsi_period, vol_avg_days, pct_change_days, data_ihsg):
    """Menghitung semua indikator teknikal untuk setiap saham dan menyiapkan DataFrame."""
    
    # Ambil daftar tickers
    if isinstance(df.columns, pd.MultiIndex):
        tickers = df.columns.get_level_values(1).unique()
    else:
        tickers = df.name.unique()
    
    all_results = []
    
    for ticker in tickers:
        try:
            # Drop level ticker agar mudah diakses
            df_ticker = df.loc[:, (slice(None), ticker)]
            df_ticker.columns = df_ticker.columns.droplevel(1) 
            
            close = df_ticker['Close']
            open_price = df_ticker['Open']
            volume = df_ticker['Volume']
            
            # 1. Fixed Moving Averages (SMA)
            ma_data = {}
            for window in FIXED_MA_WINDOWS:
                sma_col = f'SMA_{window}'
                df_ticker[sma_col] = close.rolling(window=window).mean()
                # Tidak perlu EMA, tapi jika diperlukan, bisa ditambahkan di sini
                # df_ticker[f'EMA_{window}'] = close.ewm(span=window, adjust=False).mean()
                ma_data[sma_col] = None # Placeholder untuk hasil akhir

            # 2. RSI (Relative Strength Index)
            df_ticker['RSI'] = ta.momentum.RSIIndicator(close, window=rsi_period).rsi()

            # 3. Volume Average
            df_ticker['Vol_Avg'] = volume.rolling(window=vol_avg_days).mean()
            
            # 4. Historical Percentage Change (Gain X Hari)
            df_ticker['Pct_Change_N'] = close.pct_change(periods=pct_change_days) * 100
            
            # 5. Nilai Penutupan Kemarin dan 2 Hari Lalu
            df_ticker['Prev_Close'] = close.shift(1)
            df_ticker['Prev_2_Close'] = close.shift(2)
            
            # 6. Open/Close Ratio (Gap Overnight)
            df_ticker['Open_Close_Ratio'] = open_price / df_ticker['Prev_Close']
            
            # Ambil data hari terakhir yang valid (terakhir non-NaN)
            latest = df_ticker.iloc[-1].dropna()
            
            if not latest.empty:
                result = {
                    "Ticker": ticker,
                    "Price": latest.get('Close', np.nan),
                    "Open": latest.get('Open', np.nan),
                    "High": latest.get('High', np.nan),
                    "Low": latest.get('Low', np.nan),
                    "Volume": latest.get('Volume', np.nan),
                    "Prev_Close": latest.get('Prev_Close', np.nan),
                    "Prev_2_Close": latest.get('Prev_2_Close', np.nan),
                    "Open_Close_Ratio": latest.get('Open_Close_Ratio', np.nan),
                    "RSI": latest.get('RSI', np.nan),
                    "Vol_Avg": latest.get('Vol_Avg', np.nan),
                    "Pct_Change_N": latest.get('Pct_Change_N', np.nan),
                }
                
                # Tambahkan Fixed MAs ke hasil
                for window in FIXED_MA_WINDOWS:
                    sma_col = f'SMA_{window}'
                    result[sma_col] = latest.get(sma_col, np.nan)
                
                all_results.append(result)

        except Exception as e:
            st.warning(f"Gagal menghitung indikator untuk {ticker}. Error: {e}")
            continue
            
    df_final = pd.DataFrame(all_results).set_index("Ticker")
    
    # Tambahkan IHSG
    if not data_ihsg.empty:
        df_final['IHSG_Close'] = data_ihsg['Close'].iloc[-1]
        df_final['IHSG_Prev_Close'] = data_ihsg['Close'].iloc[-2]
        df_final['IHSG_Change_Pct'] = ((df_final['IHSG_Close'] - df_final['IHSG_Prev_Close']) / df_final['IHSG_Prev_Close']) * 100
        df_final.drop(columns=['IHSG_Close'], inplace=True) 

    return df_final

# --- FUNGSI LOGIKA REKOMENDASI DAN SCORING DINAMIS ---
def run_screener_logic(df_results, custom_rules, buy_threshold, sell_threshold):
    """Menerapkan logika rule-based dinamis dan scoring."""
    
    # Inisialisasi kolom skor dan alasan
    df_results['Score'] = 0
    df_results['Rationale'] = ""
    
    list_rules = [r.strip() for r in custom_rules.split('\n') if r.strip()]
    
    # Cek apakah ada aturan
    if not list_rules:
        df_results['Rekomendasi'] = "HOLD"
        return df_results
    
    # Eksekusi setiap aturan
    for i, rule in enumerate(list_rules):
        try:
            # Gunakan df.eval() untuk mengevaluasi ekspresi pada seluruh DataFrame
            condition = df_results.eval(rule, engine='python')
            
            # Tambahkan skor (+1) untuk setiap saham yang memenuhi aturan
            df_results['Score'] += condition.astype(int)
            
            # Tambahkan alasan (rationale)
            for ticker, passes in condition.items():
                if passes:
                    current_rationale = df_results.loc[ticker, 'Rationale']
                    if current_rationale:
                        df_results.loc[ticker, 'Rationale'] = f"{current_rationale} | RULE {i+1}: {rule} (PASSED)"
                    else:
                        df_results.loc[ticker, 'Rationale'] = f"RULE {i+1}: {rule} (PASSED)"
        
        except Exception as e:
            st.warning(f"Gagal mengevaluasi aturan kustom '{rule}'. Pastikan sintaks benar. Error: {e}")
            continue

    # Tentukan Rekomendasi berdasarkan Skor Akhir
    df_results['Rekomendasi'] = "HOLD"
    df_results.loc[df_results['Score'] >= buy_threshold, 'Rekomendasi'] = "BUY"
    df_results.loc[df_results['Score'] <= sell_threshold, 'Rekomendasi'] = "SELL"
    
    return df_results


# --- FUNGSI UTILITY: PARSING TICKERS ---
def parse_tickers(text, ihsg_ticker):
    """Membersihkan dan memformat teks input menjadi list of unique tickers."""
    if not text:
        return []
    
    # Ganti koma dengan spasi, lalu split by whitespace
    raw_tickers = text.replace(',', ' ').split()
    
    cleaned_tickers = []
    for ticker in raw_tickers:
        ticker = ticker.strip().upper()
        if not ticker:
            continue
        
        # Jika bukan IHSG dan tidak berakhiran .JK, tambahkan .JK
        if ticker != ihsg_ticker and not ticker.endswith(".JK"):
            ticker += ".JK"
        
        # Pastikan tidak ada duplikat dan bukan IHSG
        if ticker not in cleaned_tickers and ticker != ihsg_ticker:
            cleaned_tickers.append(ticker)
            
    return cleaned_tickers


# --- STREAMLIT UI DAN LOGIC UTAMA ---

st.title("💸 IDX Stock Screener (Dynamic Rules)")
st.caption("Buat Aturan Skrining Kustom Anda Sendiri dengan Ekspresi Python/Pandas.")

# --- SIDEBAR: KONFIGURASI PARAMETER ---
with st.sidebar:
    st.header("⚙️ Konfigurasi Analisis")
    
    # 1. Input Tickers BARU (Text Area)
    user_input_tickers = st.text_area(
        "📝 Tickers Saham (Copy-Paste dari Excel/Teks):",
        value="BBCA, TLKM, ASII\nUNVR.JK, BBNI.JK", 
        height=150,
        help="Masukkan kode saham. Pisahkan dengan baris baru, spasi, atau koma. Ticker IDX akan otomatis ditambahkan '.JK'."
    )
    
    # Parsing input
    selected_tickers = parse_tickers(user_input_tickers, IHSG_TICKER)
    
    # Tampilkan jumlah saham yang akan diproses
    if selected_tickers:
        st.success(f"✅ {len(selected_tickers)} saham siap diproses.")
    else:
        st.warning("⚠️ Masukkan minimal satu ticker saham.")

    # 2. Parameter Indikator
    st.subheader("Parameter Indikator (N Hari)")
    # SMA Sliders dihilangkan. Fixed SMAs: 3, 5, 10, 20, 50
    rsi_period = st.slider("Periode RSI:", min_value=5, max_value=30, value=14, step=1)
    vol_avg_days = st.slider("Volume Rata-rata (N Hari):", min_value=10, max_value=50, value=20, step=1)
    pct_change_days = st.slider("Persentase Kenaikan Historis (N Hari):", min_value=5, max_value=60, value=30, step=5)
    
    # 3. Aturan Skrining DINAMIS
    st.subheader("📝 Aturan Skrining Kustom (Ekspresi Pandas)")
    st.markdown("""
    Masukkan setiap aturan dalam baris baru. Skor akan dihitung berdasarkan jumlah aturan yang *LULUS*.
    
    **Variabel yang Tersedia:**
    - Harga: `Price`, `Open`, `High`, `Low`, `Prev_Close`, `Prev_2_Close`
    - Rasio/Gain: `Open_Close_Ratio`, `Pct_Change_N`
    - **MA Tetap:** `SMA_3`, `SMA_5`, `SMA_10`, `SMA_20`, `SMA_50`
    - Momentum: `RSI`, `Vol_Avg`
    - Makro: `IHSG_Prev_Close`, `IHSG_Change_Pct`
    """)

    default_rules = """
Open_Close_Ratio > 1.005 # Gap Pembukaan lebih dari 0.5% (Potensi momentum)
SMA_20 > SMA_50 # Golden Cross (Bullish MA Crossover)
RSI < 70 and RSI > 30 # Tidak Overbought/Oversold
Volume > 1.5 * Vol_Avg # Konfirmasi Volume Tinggi
IHSG_Change_Pct > 0 # Makro sedang Bullish
    """
    custom_rules = st.text_area(
        "Tulis Aturan Anda di Sini (1 Aturan/Baris):", 
        value=default_rules, 
        height=200
    )

    # 4. Parameter Threshold Akhir
    st.subheader("Ambang Batas Rekomendasi")
    
    buy_threshold = st.number_input("Skor Minimal untuk 'BUY': (LULUS minimal N Rules)", min_value=1, value=3, step=1)
    sell_threshold = st.number_input("Skor Maksimal untuk 'SELL': (LULUS maksimal N Rules)", max_value=10, value=0, step=1)
    st.caption("Saham dengan skor antara BUY dan SELL akan direkomendasikan 'HOLD'.")

# --- MAIN BODY: HASIL ANALISIS ---

# Simpan referensi data IHSG di luar if button agar bisa diakses
data_ihsg_global = None

if st.button("🚀 Jalankan Analisis Saham", type="primary"):
    if not selected_tickers:
        st.warning("Silakan masukkan minimal satu ticker saham yang valid di sidebar.")
    else:
        # 1. Ambil Data
        data_saham, data_ihsg = fetch_data(selected_tickers, period=f"{LOOKBACK_DAYS}d")
        data_ihsg_global = data_ihsg # Simpan untuk display IHSG di bawah

        if data_saham is not None and not data_saham.empty:
            
            # 2. Hitung Indikator
            with st.spinner("Menghitung indikator teknikal..."):
                # Signature function call diperbarui
                df_indicators = calculate_indicators(
                    data_saham,
                    rsi_period,
                    vol_avg_days,
                    pct_change_days,
                    data_ihsg
                )
            
            # 3. Terapkan Logika Skrining
            with st.spinner("Menerapkan aturan skrining dinamis..."):
                df_final = run_screener_logic(
                    df_indicators.copy(), 
                    custom_rules, 
                    buy_threshold, 
                    sell_threshold
                )
            
            # --- 4. SORTING HASIL ---
            sort_by_cols = ['Open_Close_Ratio', 'Pct_Change_N', 'Score']
            
            for col in sort_by_cols:
                if col not in df_final.columns:
                    st.warning(f"Kolom sorting {col} tidak ditemukan. Melewatkan sorting.")
                    sort_by_cols.remove(col)

            if sort_by_cols:
                 df_final = df_final.sort_values(
                    by=sort_by_cols, 
                    ascending=[False] * len(sort_by_cols),
                    na_position='last' # Pindahkan yang NaN ke bawah
                )

            # --- DISPLAY HASIL ---
            
            # Formatting untuk tampilan
            df_display = df_final.copy()
            
            # Kolom Fixed MA
            ma_cols = [f'SMA_{w}' for w in FIXED_MA_WINDOWS]

            # Kolom yang hanya perlu 2 desimal
            numeric_cols_2d = ['Price', 'Open', 'High', 'Low', 'Prev_Close', 'Prev_2_Close', 
                            'RSI', 'Open_Close_Ratio', 'Pct_Change_N', 'IHSG_Prev_Close', 
                            'IHSG_Change_Pct'] + ma_cols # Ditambahkan Fixed MA

            for col in numeric_cols_2d:
                if col in df_display.columns:
                    df_display[col] = df_display[col].apply(lambda x: f"{x:,.2f}" if pd.notna(x) else '-')
            
            # Kolom Volume (0 desimal)
            vol_cols = ['Volume', 'Vol_Avg']
            for col in vol_cols:
                 if col in df_display.columns:
                    df_display[col] = df_display[col].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else '-')

            # Styling untuk Rekomendasi
            def highlight_recommendation(s):
                if s['Rekomendasi'] == 'BUY':
                    return ['background-color: #d4edda; color: #155724'] * len(s)
                elif s['Rekomendasi'] == 'SELL':
                    return ['background-color: #f8d7da; color: #721c24'] * len(s)
                else:
                    return ['background-color: #fff3cd; color: #856404'] * len(s)

            st.header("Tabel Hasil Skrining & Rekomendasi (Diurutkan berdasarkan Rasio Open/Close dan Gain)")
            
            # Kolom yang akan ditampilkan secara default
            # Memasukkan Open_Close_Ratio dan Pct_Change_N di depan, diikuti SMA 10, 20, 50
            default_ma_display = ['SMA_10', 'SMA_20', 'SMA_50']
            
            display_cols = ['Rekomendasi', 'Score', 'Open_Close_Ratio', 'Pct_Change_N', 'Price', 'Open', 'Prev_Close'] + \
                           default_ma_display + \
                           ['RSI', 'Volume', 'Vol_Avg', 'IHSG_Change_Pct', 'Rationale']

            # Konfigurasi kolom untuk Fixed MA
            ma_col_config = {
                f"SMA_{w}": st.column_config.NumberColumn(label=f"SMA {w}", format="%.2f", width="small") 
                for w in FIXED_MA_WINDOWS
            }

            st.dataframe(
                df_display[display_cols].style.apply(highlight_recommendation, axis=1),
                use_container_width=True,
                column_config={
                    "Rekomendasi": st.column_config.Column(width="small"),
                    "Rationale": st.column_config.Column(width="large"),
                    "Score": st.column_config.NumberColumn(format="%d", width="small"),
                    "Open_Close_Ratio": st.column_config.NumberColumn(label="Open/Close Ratio", format="%.4f", width="small"),
                    "Pct_Change_N": st.column_config.NumberColumn(label=f"Gain {pct_change_days} Hari (%)", format="%.2f", width="small"),
                    **ma_col_config # Gabungkan konfigurasi Fixed MA
                }
            )

            # Fitur Opsional: Export CSV
            csv_export = df_final.to_csv(index=True).encode('utf-8')
            st.download_button(
                label="⬇️ Export Hasil ke CSV",
                data=csv_export,
                file_name='idx_stock_screener_results_dynamic.csv',
                mime='text/csv',
                key='export-csv'
            )
            
            # Ringkasan IHSG
            st.subheader(f"📊 IHSG ({IHSG_TICKER})")
            ihsg_final_change = df_final['IHSG_Change_Pct'].iloc[0] if not df_final.empty else 0.0
            ihsg_prev_close = df_final['IHSG_Prev_Close'].iloc[0] if not df_final.empty else 0.0

            if ihsg_final_change > 0:
                st.success(f"IHSG ditutup di {ihsg_prev_close:,.2f} (Kemarin) dan naik **{ihsg_final_change:,.2f}%** hari ini.")
            else:
                st.error(f"IHSG ditutup di {ihsg_prev_close:,.2f} (Kemarin) dan turun **{ihsg_final_change:,.2f}%** hari ini.")
            
            st.subheader("💡 Rangkuman Rekomendasi:")
            buy_count = (df_final['Rekomendasi'] == 'BUY').sum()
            hold_count = (df_final['Rekomendasi'] == 'HOLD').sum()
            sell_count = (df_final['Rekomendasi'] == 'SELL').sum()
            
            st.markdown(f"""
            - **BUY (Potensi Beli):** {buy_count} saham.
            - **HOLD (Tunggu dan Amati):** {hold_count} saham.
            - **SELL (Potensi Jual/Hindari):** {sell_count} saham.
            """)
            
        else:
             st.error("Tidak ada data yang tersedia untuk analisis. Pastikan tickers yang Anda masukkan benar.")
