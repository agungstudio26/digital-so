import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime
import time

# --- KONFIGURASI ---
# Ganti dengan kredensial Anda atau gunakan st.secrets
SUPABASE_URL = st.secrets["SUPABASE_URL"] if "SUPABASE_URL" in st.secrets else ""
SUPABASE_KEY = st.secrets["SUPABASE_KEY"] if "SUPABASE_KEY" in st.secrets else ""

# Daftar Nama Sales (Bisa diedit manual di sini sesuai tim toko)
DAFTAR_SALES = ["Budi", "Siti", "Anto", "Dewi", "Reza", "Supervisor"]

if not SUPABASE_URL:
    st.error("⚠️ Database belum dikonfigurasi.")
    st.stop()

@st.cache_resource
def init_connection():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_connection()

# --- FUNGSI ---

def get_data(lokasi=None, jenis=None, search_term=None):
    query = supabase.table("stock_opname").select("*")
    if lokasi: query = query.eq("lokasi", lokasi)
    if jenis: query = query.eq("jenis", jenis)
    
    # Ambil data lalu filter search di pandas (lebih fleksibel untuk search text partial)
    response = query.order("nama_barang").execute()
    df = pd.DataFrame(response.data)
    
    if not df.empty and search_term:
        # Filter pencarian (case insensitive)
        df = df[df['nama_barang'].str.contains(search_term, case=False, na=False) | 
                df['sku'].str.contains(search_term, case=False, na=False)]
    
    return df

def update_stock(id_barang, qty_fisik, nama_sales):
    now = datetime.utcnow().isoformat()
    supabase.table("stock_opname").update({
        "fisik_qty": qty_fisik, 
        "updated_at": now,
        "updated_by": nama_sales # Mencatat siapa yang update
    }).eq("id", id_barang).execute()

def reset_and_upload(df):
    try:
        supabase.table("stock_opname").delete().gt("id", 0).execute()
        data_to_insert = []
        for _, row in df.iterrows():
            is_sn = pd.notna(row.get('Serial Number')) and str(row.get('Serial Number')).strip() != ''
            item = {
                "sku": str(row.get('Internal Reference', '')),
                "nama_barang": row.get('Product', 'Unknown'),
                "serial_number": str(row.get('Serial Number')) if is_sn else None,
                "kategori_barang": 'SN' if is_sn else 'NON-SN',
                "lokasi": row.get('LOKASI'),
                "jenis": row.get('JENIS'),
                "system_qty": int(row.get('Quantity', 0)),
                "fisik_qty": 0,
                "updated_by": "-" # Default strip
            }
            data_to_insert.append(item)
        
        # Batch insert
        for i in range(0, len(data_to_insert), 500):
            supabase.table("stock_opname").insert(data_to_insert[i:i+500]).execute()
        return True, len(data_to_insert)
    except Exception as e:
        return False, str(e)

# --- HALAMAN SALES ---
def page_sales():
    st.title("📱 Validasi Stok")
    
    # 1. Identitas & Filter (Header Sticky)
    with st.container():
        col_id, col_area = st.columns([1, 2])
        with col_id:
            # Dropdown Nama Sales (PENTING: Audit Trail)
            nama_user = st.selectbox("👤 Nama Anda", DAFTAR_SALES, index=0)
        with col_area:
            c1, c2 = st.columns(2)
            lokasi = c1.selectbox("Lokasi", ["Floor", "Gudang"])
            jenis = c2.selectbox("Jenis", ["Stok", "Demo"])
    
    st.divider()
    
    # 2. Search Bar
    search_txt = st.text_input("🔍 Cari Barang (Nama / SKU)", placeholder="Ketik Samsung, Kabel, atau SKU...")
    
    # Tombol Refresh
    if st.button("🔄 Muat Data"):
        st.session_state['last_fetch'] = time.time()

    # Get Data
    df = get_data(lokasi, jenis, search_txt)

    if df.empty:
        if search_txt:
            st.warning(f"Barang '{search_txt}' tidak ditemukan di {lokasi} - {jenis}.")
        else:
            st.info("Data kosong.")
        return

    # 3. Proses Validasi
    # Pisahkan SN dan Non-SN
    df_sn = df[df['kategori_barang'] == 'SN'].copy()
    df_non = df[df['kategori_barang'] == 'NON-SN'].copy()

    # --- TABEL SN ---
    if not df_sn.empty:
        st.subheader(f"📋 Barang SN ({len(df_sn)})")
        df_sn['Ditemukan'] = df_sn['fisik_qty'] > 0
        
        edited_sn = st.data_editor(
            df_sn[['id', 'nama_barang', 'serial_number', 'updated_by', 'Ditemukan']],
            column_config={
                "Ditemukan": st.column_config.CheckboxColumn("Ada?", default=False),
                "updated_by": st.column_config.TextColumn("Dicek Oleh", disabled=True),
                "id": None
            },
            hide_index=True, use_container_width=True, key="sn_editor"
        )
        
        if st.button("Simpan SN", type="primary"):
            n = 0
            for i, row in edited_sn.iterrows():
                # Logic: Cek apakah status berubah dari database asli
                # (Simplified logic: always update if row exists in editor implies user intent)
                # Untuk performa terbaik, kita idealnya bandingkan old vs new.
                # Disini kita update jika True/False berubah
                orig = df_sn[df_sn['id'] == row['id']].iloc[0]
                if (orig['fisik_qty'] > 0) != row['Ditemukan']:
                    update_stock(row['id'], 1 if row['Ditemukan'] else 0, nama_user)
                    n += 1
            if n > 0: st.success("Data SN Disimpan!"); time.sleep(0.5); st.rerun()

    # --- TABEL NON-SN ---
    if not df_non.empty:
        st.subheader(f"📦 Barang Non-SN ({len(df_non)})")
        
        edited_non = st.data_editor(
            df_non[['id', 'sku', 'nama_barang', 'system_qty', 'fisik_qty', 'updated_by']],
            column_config={
                "fisik_qty": st.column_config.NumberColumn("Fisik", min_value=0),
                "updated_by": st.column_config.TextColumn("Dicek Oleh", disabled=True),
                "system_qty": st.column_config.NumberColumn("Odoo", disabled=True),
                "id": None
            },
            hide_index=True, use_container_width=True, key="non_editor"
        )
        
        # Helper warna realtime
        st.caption("Indikator: 🟩 Pas | 🟥 Selisih")
        preview = edited_non.copy()
        preview['selisih'] = preview['fisik_qty'] - preview['system_qty']
        
        # Tampilkan preview kecil berwarna
        def color_row(val):
            color = '#d4edda' if val == 0 else '#f8d7da'
            return f'background-color: {color}'
        
        st.dataframe(
            preview[['nama_barang', 'selisih']].style.map(color_row, subset=['selisih']),
            use_container_width=True, height=150
        )

        if st.button("Simpan Non-SN", type="primary"):
            n = 0
            for i, row in edited_non.iterrows():
                orig = df_non[df_non['id'] == row['id']].iloc[0]['fisik_qty']
                if orig != row['fisik_qty']:
                    update_stock(row['id'], row['fisik_qty'], nama_user)
                    n += 1
            if n > 0: st.success("Data Non-SN Disimpan!"); time.sleep(0.5); st.rerun()

# --- HALAMAN ADMIN ---
def page_admin():
    st.title("🛡️ Admin Dashboard")
    tab1, tab2 = st.tabs(["Upload", "Monitoring"])
    
    with tab1:
        file = st.file_uploader("Upload Excel Odoo", type="xlsx")
        if file and st.button("🚀 Reset & Upload Baru"):
            df = pd.read_excel(file)
            ok, msg = reset_and_upload(df)
            if ok: st.success(f"Sukses! {msg} data masuk.")
            else: st.error(f"Gagal: {msg}")

    with tab2:
        if st.button("Refresh"): st.rerun()
        df = get_data()
        if df.empty: return
        
        # KPI Ringkas
        total = len(df)
        # Dianggap 'dicek' jika updated_by bukan "-" (default strip)
        dicek = len(df[df['updated_by'] != '-'])
        selisih = df[df['fisik_qty'] != df['system_qty']]
        
        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("Total Barang", total)
        kpi2.metric("Sudah Dicek", f"{dicek} ({dicek/total*100:.1f}%)")
        kpi3.metric("Item Selisih", len(selisih), delta_color="inverse")
        
        st.divider()
        st.subheader("Detail Selisih & Checker")
        st.dataframe(
            df[['nama_barang', 'lokasi', 'system_qty', 'fisik_qty', 'updated_by']],
            use_container_width=True
        )
        
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("Download Laporan Lengkap", csv, "laporan_so.csv", "text/csv")

# --- MAIN ---
def main():
    st.set_page_config(page_title="Stock Opname", page_icon="📦", layout="wide")
    menu = st.sidebar.radio("Menu", ["Sales", "Admin"])
    
    if menu == "Sales": page_sales()
    elif menu == "Admin":
        pwd = st.sidebar.text_input("Password", type="password")
        if pwd == "admin123": page_admin()

if __name__ == "__main__":
    main()
