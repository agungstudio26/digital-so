import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import datetime
import time

# --- KONFIGURASI SUPABASE ---
# Disarankan simpan ini di st.secrets untuk production
# Untuk demo, user bisa mengganti string kosong di bawah
SUPABASE_URL = st.secrets["SUPABASE_URL"] if "SUPABASE_URL" in st.secrets else ""
SUPABASE_KEY = st.secrets["SUPABASE_KEY"] if "SUPABASE_KEY" in st.secrets else ""

# Cek koneksi
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("⚠️ Konfigurasi Database Belum Ada. Masukkan SUPABASE_URL dan SUPABASE_KEY di .streamlit/secrets.toml atau di kode.")
    st.stop()

@st.cache_resource
def init_connection():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_connection()

# --- FUNGSI HELPER ---

def get_data(lokasi=None, jenis=None):
    """Mengambil data dari Supabase dengan filter"""
    query = supabase.table("stock_opname").select("*")
    
    if lokasi:
        query = query.eq("lokasi", lokasi)
    if jenis:
        query = query.eq("jenis", jenis)
    
    # Order by nama barang agar rapi
    response = query.order("nama_barang").execute()
    return pd.DataFrame(response.data)

def update_stock(id_barang, qty_fisik):
    """Update jumlah fisik ke database"""
    now = datetime.utcnow().isoformat()
    supabase.table("stock_opname").update({
        "fisik_qty": qty_fisik, 
        "updated_at": now
    }).eq("id", id_barang).execute()

def reset_and_upload_master(df):
    """Menghapus data lama dan upload data baru dari Excel"""
    try:
        # 1. Hapus semua data lama
        # Supabase delete all rows trick: delete where id > 0
        supabase.table("stock_opname").delete().gt("id", 0).execute()
        
        # 2. Siapkan data baru
        data_to_insert = []
        for _, row in df.iterrows():
            # Logika deteksi SN vs Non-SN
            # Jika kolom Serial Number ada isinya, maka Kategori = SN
            is_sn = pd.notna(row.get('Serial Number')) and str(row.get('Serial Number')).strip() != ''
            kategori = 'SN' if is_sn else 'NON-SN'
            
            # Sanitasi nilai NaN menjadi 0 atau string kosong
            sn_val = str(row.get('Serial Number')) if is_sn else None
            qty_system = int(row.get('Quantity', 0))
            
            # Jika SN, biasanya qty system per baris adalah 1. Jika Non-SN, bisa banyak.
            
            item = {
                "sku": str(row.get('Internal Reference', '')),
                "nama_barang": row.get('Product', 'Unknown'),
                "serial_number": sn_val,
                "kategori_barang": kategori,
                "lokasi": row.get('LOKASI'), # Wajib ada di Excel
                "jenis": row.get('JENIS'),   # Wajib ada di Excel
                "system_qty": qty_system,
                "fisik_qty": 0 # Default mulai dari 0
            }
            data_to_insert.append(item)
        
        # 3. Bulk Insert (Batching jika data > 1000 baris untuk keamanan)
        batch_size = 500
        for i in range(0, len(data_to_insert), batch_size):
            batch = data_to_insert[i:i + batch_size]
            supabase.table("stock_opname").insert(batch).execute()
            
        return True, len(data_to_insert)
    except Exception as e:
        return False, str(e)

# --- HALAMAN ADMIN ---
def page_admin():
    st.title("🛡️ Admin Dashboard")
    st.markdown("Upload Master Data dari Odoo dan Pantau Progres.")

    tab1, tab2 = st.tabs(["📤 Upload Master Data", "📊 Laporan & Progres"])

    with tab1:
        st.warning("⚠️ **PERHATIAN:** Upload file baru akan MENGHAPUS seluruh data Stock Opname yang sedang berjalan!")
        
        uploaded_file = st.file_uploader("Upload File Excel Odoo (.xlsx)", type=['xlsx'])
        
        if uploaded_file:
            try:
                df = pd.read_excel(uploaded_file)
                
                # Validasi Kolom Wajib
                required_cols = ['LOKASI', 'JENIS', 'Product', 'Internal Reference', 'Quantity']
                missing = [c for c in required_cols if c not in df.columns]
                
                if missing:
                    st.error(f"Kolom wajib tidak ditemukan: {', '.join(missing)}")
                    st.info("Pastikan Excel memiliki kolom: LOKASI, JENIS, Product, Internal Reference, Quantity, Serial Number (Opsional)")
                else:
                    st.dataframe(df.head())
                    if st.button("🚀 Reset & Mulai Stock Opname Baru", type="primary"):
                        with st.spinner("Sedang memproses database..."):
                            success, msg = reset_and_upload_master(df)
                            if success:
                                st.success(f"Berhasil! {msg} baris data telah diupload. Sales sudah bisa mulai bekerja.")
                            else:
                                st.error(f"Gagal upload: {msg}")
            except Exception as e:
                st.error(f"Error membaca file: {e}")

    with tab2:
        if st.button("🔄 Refresh Data"):
            st.rerun()
            
        # Ambil semua data
        df_all = get_data()
        
        if df_all.empty:
            st.info("Belum ada data.")
            return

        # Hitung Progres
        # Barang dianggap 'checked' jika fisik_qty > 0 (asumsi kasar) atau user sudah visit
        # Untuk presisi lebih baik, kita hitung progress match vs unmatch
        
        df_all['selisih'] = df_all['fisik_qty'] - df_all['system_qty']
        df_all['status'] = df_all['selisih'].apply(lambda x: 'MATCH' if x == 0 else ('KURANG' if x < 0 else 'LEBIH'))
        
        match_count = len(df_all[df_all['status'] == 'MATCH'])
        total_items = len(df_all)
        progress = (match_count / total_items) * 100 if total_items > 0 else 0
        
        st.progress(progress / 100)
        st.metric("Akurasi Data (Match)", f"{progress:.1f}%", f"{match_count}/{total_items} Items")
        
        st.subheader("⚠️ Laporan Selisih (Discrepancy)")
        # Filter hanya yang selisih
        df_selisih = df_all[df_all['selisih'] != 0].copy()
        
        if not df_selisih.empty:
            st.error(f"Ditemukan {len(df_selisih)} item dengan selisih!")
            
            # Formatting untuk display
            st.dataframe(
                df_selisih[['sku', 'nama_barang', 'lokasi', 'jenis', 'system_qty', 'fisik_qty', 'selisih']],
                use_container_width=True
            )
            
            # Download Button
            csv = df_selisih.to_csv(index=False).encode('utf-8')
            st.download_button(
                "📥 Download Laporan Selisih (CSV)",
                csv,
                "laporan_selisih_so.csv",
                "text/csv",
                key='download-csv'
            )
        else:
            st.success("🎉 Sempurna! Belum ada selisih ditemukan.")

# --- HALAMAN SALES ---
def page_sales():
    st.title("📱 Validasi Stok")
    
    # 1. Filter Area Kerja
    col1, col2 = st.columns(2)
    with col1:
        lokasi_opt = st.selectbox("Pilih Lokasi", ["Floor", "Gudang"])
    with col2:
        jenis_opt = st.selectbox("Pilih Jenis", ["Stok", "Demo"])
    
    # Tombol Refresh manual (kadang perlu jika koneksi lambat)
    if st.button("🔄 Muat Data Area Ini"):
        st.session_state['last_fetch'] = time.time()
        
    # Ambil Data
    df = get_data(lokasi=lokasi_opt, jenis=jenis_opt)
    
    if df.empty:
        st.info(f"Tidak ada data barang di **{lokasi_opt} - {jenis_opt}**.")
        return

    st.markdown("---")
    
    # Pisahkan Barang SN dan Non-SN untuk UX yang lebih baik
    df_sn = df[df['kategori_barang'] == 'SN'].copy()
    df_non_sn = df[df['kategori_barang'] == 'NON-SN'].copy()

    # --- BAGIAN 1: BARANG SERIAL NUMBER (CHECKLIST STYLE) ---
    if not df_sn.empty:
        st.subheader(f"📋 Validasi Barang SN ({len(df_sn)} item)")
        st.caption("Centang jika barang fisik ditemukan.")
        
        # Persiapkan data untuk Data Editor
        # Kita ubah fisik_qty menjadi boolean (True/False) untuk checkbox
        # Asumsi: Jika fisik_qty > 0 berarti TRUE (Found)
        df_sn['Ditemukan'] = df_sn['fisik_qty'] > 0
        
        # Tampilkan kolom yang relevan saja
        cols_show = ['id', 'nama_barang', 'serial_number', 'Ditemukan']
        
        edited_sn = st.data_editor(
            df_sn[cols_show],
            column_config={
                "Ditemukan": st.column_config.CheckboxColumn(
                    "Ada Fisik?",
                    help="Centang jika barang ada",
                    default=False,
                ),
                "id": None, # Sembunyikan ID
            },
            disabled=["nama_barang", "serial_number"],
            hide_index=True,
            key="editor_sn",
            use_container_width=True
        )
        
        # Tombol Simpan Perubahan SN
        if st.button("Simpan Validasi SN", type="primary"):
            updates_count = 0
            progress_bar = st.progress(0)
            
            # Bandingkan data lama vs baru untuk update yang berubah saja
            # Tapi karena stateless, kita iterasi hasil editor
            for index, row in edited_sn.iterrows():
                original_row = df_sn[df_sn['id'] == row['id']].iloc[0]
                original_checked = original_row['fisik_qty'] > 0
                new_checked = row['Ditemukan']
                
                # Jika status berubah, update DB
                if original_checked != new_checked:
                    new_qty = 1 if new_checked else 0
                    update_stock(row['id'], new_qty)
                    updates_count += 1
            
            progress_bar.progress(100)
            if updates_count > 0:
                st.toast(f"✅ {updates_count} status SN berhasil disimpan!", icon="💾")
                time.sleep(1)
                st.rerun()
            else:
                st.info("Tidak ada perubahan data SN.")

    # --- BAGIAN 2: BARANG NON-SN (INPUT ANGKA) ---
    if not df_non_sn.empty:
        st.markdown("---")
        st.subheader(f"📦 Validasi Barang Non-SN ({len(df_non_sn)} item)")
        st.caption("Masukkan jumlah fisik yang dihitung.")

        # Indikator warna visual (Red/Green) sulit di dalam editor langsung, 
        # jadi kita bantu user dengan kolom 'Selisih' yang computed
        
        cols_show_nonsn = ['id', 'nama_barang', 'sku', 'system_qty', 'fisik_qty']
        
        edited_nonsn = st.data_editor(
            df_non_sn[cols_show_nonsn],
            column_config={
                "fisik_qty": st.column_config.NumberColumn(
                    "Jml Fisik",
                    help="Masukkan hitungan fisik",
                    min_value=0,
                    step=1,
                    format="%d"
                ),
                "system_qty": st.column_config.NumberColumn(
                    "Odoo Qty",
                    help="Data Sistem",
                    format="%d",
                    disabled=True 
                ),
                "id": None
            },
            disabled=["nama_barang", "sku", "system_qty"],
            hide_index=True,
            key="editor_nonsn",
            use_container_width=True
        )

        # Logic Update Non-SN
        if st.button("Simpan Hitungan Non-SN"):
            updates_count = 0
            for index, row in edited_nonsn.iterrows():
                original_qty = df_non_sn[df_non_sn['id'] == row['id']].iloc[0]['fisik_qty']
                new_qty = row['fisik_qty']
                
                if original_qty != new_qty:
                    update_stock(row['id'], new_qty)
                    updates_count += 1
            
            if updates_count > 0:
                st.toast(f"✅ {updates_count} hitungan berhasil disimpan!", icon="💾")
                time.sleep(1)
                st.rerun()
            else:
                st.info("Tidak ada perubahan hitungan.")
                
        # Tampilkan Status Visual di bawah tabel edit agar user sadar
        st.caption("Status Selisih (Realtime Preview):")
        preview_df = edited_nonsn.copy()
        preview_df['selisih'] = preview_df['fisik_qty'] - preview_df['system_qty']
        
        def highlight_row(row):
            if row['selisih'] == 0:
                return ['background-color: #d4edda'] * len(row) # Green
            else:
                return ['background-color: #f8d7da'] * len(row) # Red

        st.dataframe(
            preview_df[['nama_barang', 'system_qty', 'fisik_qty', 'selisih']].style.apply(highlight_row, axis=1),
            use_container_width=True
        )

# --- MAIN APP ROUTING ---
def main():
    st.set_page_config(page_title="Digital Stock Opname", page_icon="📦", layout="wide")
    
    st.sidebar.image("https://img.icons8.com/color/96/warehouse.png", width=80)
    st.sidebar.title("Digital SO")
    
    menu = st.sidebar.radio("Menu", ["Sales (Validasi)", "Admin (Laporan & Upload)"])
    
    if menu == "Sales (Validasi)":
        page_sales()
    else:
        # Simple Password check for Admin (Optional)
        pwd = st.sidebar.text_input("Admin Password", type="password")
        if pwd == "admin123": # Ganti dengan logic auth yang lebih aman
            page_admin()
        else:
            if pwd:
                st.sidebar.error("Password salah")
            st.warning("Masukkan password admin di sidebar untuk akses menu ini.")

if __name__ == "__main__":
    main()
