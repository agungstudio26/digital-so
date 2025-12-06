import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime
import time
import io
from openpyxl.styles import PatternFill, Font, Alignment

# --- KONFIGURASI [v3.8] ---
SUPABASE_URL = st.secrets["SUPABASE_URL"] if "SUPABASE_URL" in st.secrets else ""
SUPABASE_KEY = st.secrets["SUPABASE_KEY"] if "SUPABASE_KEY" in st.secrets else ""
DAFTAR_SALES = ["Agung", "Al Fath", "Reza", "Rico", "Sasa", "Mita", "Supervisor"]
RESET_PIN = "123456" # PIN Reset

if not SUPABASE_URL:
    st.error("⚠️ Database belum dikonfigurasi. Cek secrets.toml")
    st.stop()

@st.cache_resource
def init_connection():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_connection()

# --- FUNGSI HELPER EXCEL ---
def convert_df_to_excel(df):
    """Mengubah DataFrame menjadi file Excel dengan Header Cantik"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        cols = ['batch_id', 'sku', 'brand', 'nama_barang', 'owner_category', 'lokasi', 'jenis', 'system_qty', 'fisik_qty', 'updated_by', 'updated_at']
        available_cols = [c for c in cols if c in df.columns]
        df_export = df[available_cols] if not df.empty else df
        
        df_export.to_excel(writer, index=False, sheet_name='Data_SO')
        worksheet = writer.sheets['Data_SO']
        
        blibli_blue_fill = PatternFill(start_color="0095DA", end_color="0095DA", fill_type="solid")
        white_bold_font = Font(color="FFFFFF", bold=True, size=11)
        center_align = Alignment(horizontal='center', vertical='center')
        
        for cell in worksheet[1]:
            cell.fill = blibli_blue_fill
            cell.font = white_bold_font
            cell.alignment = center_align
            
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value) if cell.value else "") for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = length + 5
    return output.getvalue()

# --- FUNGSI HELPER DATABASE ---
def get_active_session_info():
    try:
        res = supabase.table("stock_opname").select("batch_id").eq("is_active", True).limit(1).execute()
        if res.data: return res.data[0]['batch_id']
        return "Belum Ada Sesi Aktif"
    except: return "-"

def get_data(lokasi=None, jenis=None, owner=None, search_term=None, only_active=True, batch_id=None):
    query = supabase.table("stock_opname").select("*")
    if only_active: query = query.eq("is_active", True)
    elif batch_id: query = query.eq("batch_id", batch_id)
    
    if lokasi: query = query.eq("lokasi", lokasi)
    if jenis: query = query.eq("jenis", jenis)
    if owner: query = query.eq("owner_category", owner)
    
    response = query.order("nama_barang").execute()
    df = pd.DataFrame(response.data)
    
    if not df.empty and search_term:
        df = df[df['nama_barang'].str.contains(search_term, case=False, na=False) | 
                df['brand'].str.contains(search_term, case=False, na=False) |
                df['sku'].str.contains(search_term, case=False, na=False)]
    return df

def update_stock(id_barang, qty_fisik, nama_sales):
    now = datetime.utcnow().isoformat()
    supabase.table("stock_opname").update({
        "fisik_qty": qty_fisik, "updated_at": now, "updated_by": nama_sales 
    }).eq("id", id_barang).execute()

# --- FUNGSI ADMIN: PROSES DATA ---
def delete_active_session():
    try:
        supabase.table("stock_opname").delete().eq("is_active", True).execute()
        return True, "Sesi aktif berhasil dihapus total."
    except Exception as e: return False, str(e)

def start_new_session(df, session_name):
    try:
        supabase.table("stock_opname").update({"is_active": False}).eq("is_active", True).execute()
        return process_and_insert(df, session_name)
    except Exception as e: return False, str(e)

def add_to_current_session(df, current_session_name):
    try:
        return process_and_insert(df, current_session_name)
    except Exception as e: return False, str(e)

def process_and_insert(df, session_name):
    data_to_insert = []
    for _, row in df.iterrows():
        is_sn = pd.notna(row.get('Serial Number')) and str(row.get('Serial Number')).strip() != ''
        
        owner_val = row.get('OWNER', 'Reguler') 
        if pd.isna(owner_val) or str(owner_val).strip() == '': owner_val = 'Reguler'
        
        brand_val = row.get('BRAND', 'General')
        if pd.isna(brand_val) or str(brand_val).strip() == '': 
            brand_val = str(row.get('Product', '')).split()[0]

        item = {
            "sku": str(row.get('Internal Reference', '')),
            "nama_barang": row.get('Product', 'Unknown'),
            "brand": str(brand_val).upper(),
            "owner_category": str(owner_val).title(),
            "serial_number": str(row.get('Serial Number')) if is_sn else None,
            "kategori_barang": 'SN' if is_sn else 'NON-SN',
            "lokasi": row.get('LOKASI'),
            "jenis": row.get('JENIS'),
            "system_qty": int(row.get('Quantity', 0)),
            "fisik_qty": 0, "updated_by": "-", "is_active": True, "batch_id": session_name
        }
        data_to_insert.append(item)
    
    batch_size = 500
    for i in range(0, len(data_to_insert), batch_size):
        supabase.table("stock_opname").insert(data_to_insert[i:i+batch_size]).execute()
    return True, len(data_to_insert)

def merge_offline_data(df):
    try:
        success_count = 0
        my_bar = st.progress(0)
        total_rows = len(df)
        for i, row in df.iterrows():
            sku_excel = str(row.get('Internal Reference', ''))
            qty_to_update = row.get('Hitungan Fisik')
            if pd.notna(qty_to_update):
                supabase.table("stock_opname").update({
                    "fisik_qty": int(qty_to_update), "updated_by": "Offline Upload",
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("sku", sku_excel).eq("is_active", True).execute()
                success_count += 1
            my_bar.progress((i + 1) / total_rows)
        return True, success_count
    except Exception as e: return False, str(e)

def get_master_template_excel():
    data = {
        'Internal Reference': ['SAM-S24', 'VIV-CBL-01', 'TITIP-CASE-01'],
        'BRAND': ['SAMSUNG', 'VIVAN', 'ROBOT'],
        'Product': ['Samsung Galaxy S24', 'Vivan Kabel C', 'Robot Casing (Titipan)'],
        'OWNER': ['Reguler', 'Reguler', 'Konsinyasi'],
        'Serial Number': ['SN123', '', ''],
        'LOKASI': ['Floor', 'Gudang', 'Floor'],
        'JENIS': ['Stok', 'Stok', 'Stok'],
        'Quantity': [10, 100, 50]
    }
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Template_Master')
        worksheet = writer.sheets['Template_Master']
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value) if cell.value else "") for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = length + 5
    return output.getvalue()

def get_template_excel():
    data = {
        'Internal Reference': ['SAM-S24', 'VIV-CBL-01', 'TITIP-CASE-01'],
        'BRAND': ['SAMSUNG', 'VIVAN', 'ROBOT'],
        'Product': ['Samsung Galaxy S24', 'Vivan Kabel C', 'Robot Casing (Titipan)'],
        'OWNER': ['Reguler', 'Reguler', 'Konsinyasi'],
        'Serial Number': ['SN123', '', ''],
        'LOKASI': ['Floor', 'Gudang', 'Floor'],
        'JENIS': ['Stok', 'Stok', 'Stok'],
        'Quantity': [10, 100, 50],
        'Hitungan Fisik': [10, 98, 50]
    }
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Template_Master')
        worksheet = writer.sheets['Template_Master']
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value) if cell.value else "") for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = length + 5
    return output.getvalue()

# --- HALAMAN SALES ---
def page_sales():
    session_name = get_active_session_info()
    st.title(f"📱 SO: {session_name}")
    
    with st.container():
        col_id, col_area = st.columns([1, 2])
        with col_id:
            # [v3.8] Placeholder Nama
            opsi_sales = ["-- Silahkan Pilih Nama Petugas --"] + DAFTAR_SALES
            nama_user = st.selectbox("👤 Nama Pemeriksa", opsi_sales)
        
        with col_area:
            st.write("---")
            owner_opt = st.radio("Sumber Barang:", ["Reguler (Milik Toko)", "Konsinyasi (Titipan)"], horizontal=True)
            owner_filter = "Reguler" if "Reguler" in owner_opt else "Konsinyasi"
            st.write("---")

            c1, c2 = st.columns(2)
            lokasi = c1.selectbox("Lokasi", ["Floor", "Gudang"])
            opsi_jenis = ["Stok", "Demo"]
            if lokasi == "Gudang": opsi_jenis = ["Stok"]
            jenis = c2.selectbox("Jenis", opsi_jenis)
    
    st.divider()
    
    # [v3.8] Validasi Wajib Pilih Nama
    if "Silahkan Pilih" in nama_user:
        st.info("👋 Halo! Untuk memulai Stock Opname, mohon **pilih nama Anda** terlebih dahulu di menu kiri atas.")
        st.stop()
        
    search_txt = st.text_input("🔍 Cari (Ketik Brand/Nama)", placeholder="Contoh: Samsung, Robot...")
    
    if st.button("🔄 Refresh Data"):
        st.session_state['last_fetch'] = time.time()

    df = get_data(lokasi, jenis, owner_filter, search_txt, only_active=True)

    if df.empty:
        st.info(f"Tidak ada data barang **{owner_filter}** di {lokasi}-{jenis}.")
        return

    df_sn = df[df['kategori_barang'] == 'SN'].copy()
    df_non = df[df['kategori_barang'] == 'NON-SN'].copy()

    if not df_sn.empty:
        st.subheader(f"📋 SN ({len(df_sn)}) - {owner_filter}")
        df_sn['Ditemukan'] = df_sn['fisik_qty'] > 0
        edited_sn = st.data_editor(
            df_sn[['id', 'brand', 'nama_barang', 'serial_number', 'updated_by', 'Ditemukan']],
            column_config={
                "Ditemukan": st.column_config.CheckboxColumn("Ada?", default=False),
                "updated_by": st.column_config.TextColumn("Checker", disabled=True),
                "brand": st.column_config.TextColumn("Merek", disabled=True),
                "id": None
            },
            hide_index=True, use_container_width=True, key="sn"
        )
        if st.button("Simpan SN", type="primary"):
            n = 0
            for i, row in edited_sn.iterrows():
                orig = df_sn[df_sn['id'] == row['id']].iloc[0]
                if (orig['fisik_qty'] > 0) != row['Ditemukan']:
                    update_stock(row['id'], 1 if row['Ditemukan'] else 0, nama_user)
                    n += 1
            if n > 0: st.toast(f"✅ {n} SN Tersimpan!"); time.sleep(0.5); st.rerun()

    if not df_non.empty:
        st.subheader(f"📦 Non-SN ({len(df_non)}) - {owner_filter}")
        edited_non = st.data_editor(
            df_non[['id', 'brand', 'nama_barang', 'system_qty', 'fisik_qty', 'updated_by']],
            column_config={
                "fisik_qty": st.column_config.NumberColumn("Fisik", min_value=0),
                "system_qty": st.column_config.NumberColumn("Sistem", disabled=True),
                "brand": st.column_config.TextColumn("Merek", disabled=True),
                "updated_by": st.column_config.TextColumn("Checker", disabled=True),
                "id": None
            },
            hide_index=True, use_container_width=True, key="non"
        )
        
        preview = edited_non.copy()
        preview['selisih'] = preview['fisik_qty'] - preview['system_qty']
        def color_row(val): return 'background-color: #d4edda' if val == 0 else 'background-color: #f8d7da'
        st.dataframe(preview[['nama_barang', 'selisih']].style.map(color_row, subset=['selisih']), use_container_width=True, height=150)

        if st.button("Simpan Non-SN", type="primary"):
            n = 0
            for i, row in edited_non.iterrows():
                orig = df_non[df_non['id'] == row['id']].iloc[0]['fisik_qty']
                if orig != row['fisik_qty']:
                    update_stock(row['id'], row['fisik_qty'], nama_user)
                    n += 1
            if n > 0: st.toast(f"✅ {n} Data Tersimpan!"); time.sleep(0.5); st.rerun()

# --- HALAMAN ADMIN ---
def page_admin():
    st.title("🛡️ Admin Dashboard (v3.8)")
    active_session = get_active_session_info()
    
    if active_session == "Belum Ada Sesi Aktif":
        st.warning("⚠️ Belum ada sesi aktif. Silakan mulai sesi baru di bawah.")
    else:
        st.info(f"📅 Sesi Aktif: **{active_session}**")
    
    tab1, tab2, tab3, tab4 = st.tabs(["🚀 Master Data", "📥 Upload Offline", "🗄️ Laporan Akhir", "⚠️ Danger Zone"])
    
    with tab1:
        st.write("---")
        st.markdown("### 📁 Template Master Data")
        st.caption("Download template ini untuk menyusun data Master Barang (Toko/Konsinyasi) sebelum di-upload.")
        st.download_button("⬇️ Download Template Master Excel", get_master_template_excel(), "Template_Master_Data.xlsx")
        
        st.write("---")

        st.subheader("🅰️ Mulai Sesi Baru (Reset Data)")
        st.caption("Gunakan ini untuk upload File Master Utama (Barang Toko). Data lama akan diarsipkan.")
        
        c1, c2 = st.columns([2, 1])
        new_session_name = c1.text_input("Nama Sesi Baru", placeholder="Contoh: SO Pekan 2 Nov")
        file_master = c1.file_uploader("Upload Master Odoo (Toko)", type="xlsx", key="u_main")
        
        if file_master and new_session_name:
            if c1.button("🔥 MULAI SESI BARU", type="primary"):
                with st.spinner("Mereset & Upload..."):
                    df = pd.read_excel(file_master)
                    ok, msg = start_new_session(df, new_session_name)
                    if ok: st.success(f"Sesi '{new_session_name}' Dimulai! {msg} Data Toko Masuk."); time.sleep(2); st.rerun()
                    else: st.error(f"Gagal: {msg}")

        st.write("---")
        
        st.subheader("🅱️ Tambah Master Konsinyasi (Append)")
        st.caption("Gunakan ini untuk menambah barang Konsinyasi ke sesi yang sedang berjalan. **Data Toko TIDAK AKAN HILANG.**")
        
        if active_session == "Belum Ada Sesi Aktif":
            st.error("🚫 Buat sesi baru dulu di atas, baru bisa tambah data konsinyasi.")
        else:
            file_cons = st.file_uploader("Upload Master Konsinyasi", type="xlsx", key="u_cons")
            if file_cons:
                if st.button("➕ TAMBAHKAN KE SESI INI"):
                    with st.spinner("Menambahkan Data..."):
                        df_cons = pd.read_excel(file_cons)
                        if 'OWNER' not in df_cons.columns: df_cons['OWNER'] = 'Konsinyasi'
                        ok, msg = add_to_current_session(df_cons, active_session)
                        if ok: st.success(f"Berhasil menambahkan {msg} Data Konsinyasi ke sesi '{active_session}'."); time.sleep(2); st.rerun()
                        else: st.error(f"Gagal: {msg}")

    with tab2:
        st.markdown("### Upload Susulan (Offline Recovery)")
        st.caption("Jika internet mati, sales pakai Excel ini. Admin upload disini untuk merge.")
        st.download_button("⬇️ Download Template Offline", get_template_excel(), "Template_Offline_v3.5.xlsx")
        
        file_offline = st.file_uploader("Upload File Sales", type="xlsx", key="u2")
        if file_offline and st.button("Merge Data Offline"):
            with st.spinner("Merging..."):
                df_off = pd.read_excel(file_offline)
                if 'Hitungan Fisik' not in df_off.columns: st.error("Format salah! Wajib ada kolom 'Hitungan Fisik'.")
                else:
                    ok, count = merge_offline_data(df_off)
                    if ok: st.success(f"Berhasil update {count} data."); time.sleep(2); st.rerun()
                    else: st.error(f"Gagal: {count}")

    with tab3:
        mode_view = st.radio("Pilih Data:", ["Sesi Aktif Sekarang", "Arsip / History Lama"], horizontal=True)
        df = pd.DataFrame()
        if mode_view == "Sesi Aktif Sekarang": df = get_data(only_active=True)
        else:
            try:
                res = supabase.table("stock_opname").select("batch_id").eq("is_active", False).execute()
                batches = sorted(list(set([x['batch_id'] for x in res.data])), reverse=True)
                selected_batch = st.selectbox("Pilih Sesi Lama:", batches) if batches else None
                if selected_batch: df = get_data(only_active=False, batch_id=selected_batch)
            except: st.error("Gagal load history.")

        if not df.empty:
            st.markdown("---")
            reguler_count = len(df[df['owner_category'] == 'Reguler'])
            konsin_count = len(df[df['owner_category'] == 'Konsinyasi'])
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Total SKU", len(df))
            c2.metric("Milik Toko", reguler_count)
            c3.metric("Milik Vendor (Konsinyasi)", konsin_count)
            
            st.dataframe(df)
            
            st.markdown("### 📥 Download Laporan (Terpisah)")
            tgl = datetime.now().strftime('%Y-%m-%d')
            col_d1, col_d2, col_d3 = st.columns(3)
            with col_d1:
                st.download_button("📥 Laporan LENGKAP (All)", convert_df_to_excel(df), f"SO_Full_{tgl}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with col_d2:
                df_reg = df[df['owner_category'] == 'Reguler']
                if not df_reg.empty:
                    st.download_button("📥 Laporan REGULER (Toko)", convert_df_to_excel(df_reg), f"SO_Toko_{tgl}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                else: st.caption("Data Reguler Kosong")
            with col_d3:
                df_cons = df[df['owner_category'] == 'Konsinyasi']
                if not df_cons.empty:
                    st.download_button("📥 Laporan KONSINYASI", convert_df_to_excel(df_cons), f"SO_Konsinyasi_{tgl}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                else: st.caption("Data Konsinyasi Kosong")

    with tab4:
        st.header("⚠️ DANGER ZONE")
        st.error("Area Berbahaya. Tindakan di sini bersifat permanen.")
        
        st.markdown("""
        **Fungsi Reset Sesi Aktif:**
        - Menghapus **SELURUH** data pada sesi yang sedang berjalan.
        - Tidak membuat arsip/backup.
        - Gunakan hanya jika Anda salah upload master data dan ingin memulai ulang dari nol.
        """)
        
        st.divider()
        st.subheader("🔥 Hapus Sesi Aktif")
        
        dz_col1, dz_col2 = st.columns([3, 1]) 
        
        with dz_col1:
            input_pin = st.text_input("Masukkan PIN Keamanan", type="password", placeholder="PIN Standar: 123456")
            confirm_reset = st.checkbox("Saya sadar data sesi ini akan hilang permanen.")
        
        with dz_col2:
            st.text("") 
            st.write("") 
            if st.button("🔥 HAPUS SESI INI", use_container_width=True):
                if input_pin == RESET_PIN:
                    if confirm_reset:
                        with st.spinner("Menghapus Sesi Aktif..."):
                            ok, msg = delete_active_session()
                            if ok: st.success("Sesi berhasil di-reset!"); time.sleep(2); st.rerun()
                            else: st.error(f"Gagal: {msg}")
                    else:
                        st.error("Harap centang konfirmasi dulu.")
                else:
                    st.error("PIN Salah.")

# --- MAIN ---
def main():
    st.set_page_config(page_title="SO System v3.8", page_icon="📦", layout="wide")
    st.sidebar.title("SO Apps v3.8")
    st.sidebar.success(f"Sesi: {get_active_session_info()}")
    menu = st.sidebar.radio("Navigasi", ["Sales Input", "Admin Panel"])
    if menu == "Sales Input": page_sales()
    elif menu == "Admin Panel":
        pwd = st.sidebar.text_input("Password Admin", type="password")
        if pwd == "admin123": page_admin()

if __name__ == "__main__":
    main()
