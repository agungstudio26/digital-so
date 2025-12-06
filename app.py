import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime
import time
import io
from openpyxl.styles import PatternFill, Font, Alignment

# --- KONFIGURASI [v3.9 - Fix] ---
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

# [v3.9] Tambahkan filter waktu untuk cek konflik
def get_data(lokasi=None, jenis=None, owner=None, search_term=None, only_active=True, batch_id=None):
    query = supabase.table("stock_opname").select("*")
    if only_active: query = query.eq("is_active", True)
    elif batch_id: query = query.eq("batch_id", batch_id)
    
    if lokasi: query = query.eq("lokasi", lokasi)
    if jenis: query = query.eq("jenis", jenis)
    if owner: query = query.eq("owner_category", owner)
    
    # Ambil data dan catat waktu pengambilan data
    start_time = datetime.now()
    response = query.order("nama_barang").execute()
    df = pd.DataFrame(response.data)
    
    if not df.empty and search_term:
        df = df[df['nama_barang'].str.contains(search_term, case=False, na=False) | 
                df['brand'].str.contains(search_term, case=False, na=False) |
                df['sku'].str.contains(search_term, case=False, na=False)]
    
    # Simpan waktu pengambilan data di session state untuk konflik check
    st.session_state['data_loaded_time'] = start_time
    # Simpan dataframe asli di session state untuk perbandingan perubahan
    st.session_state['current_df'] = df.copy()
    
    return df

def get_db_updated_at(id_barang):
    """Fungsi helper untuk mengambil updated_at dari DB saat ini"""
    res = supabase.table("stock_opname").select("updated_at, updated_by").eq("id", id_barang).single().execute()
    return res.data['updated_at'], res.data['updated_by']

def update_stock(id_barang, qty_fisik, nama_sales):
    """
    [v3.9] Fungsi update ini tidak lagi dipakai langsung. 
    Logika update dan konflik check dipindahkan ke page_sales.
    """
    pass 

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
        # [v3.9 Fix] Mengatur ulang kolom input agar lebih sejajar
        c_pemeriksa, c_owner, c_lokasi, c_jenis = st.columns([1, 1, 0.7, 0.7])

        with c_pemeriksa:
            opsi_sales = ["-- Silahkan Pilih Nama Petugas --"] + DAFTAR_SALES
            nama_user = st.selectbox("👤 Nama Pemeriksa", opsi_sales)
        
        with c_owner:
            st.caption("Sumber Barang:") # Menggunakan caption sebagai label atas
            owner_opt = st.radio(" ", ["Reguler", "Konsinyasi"], horizontal=True, label_visibility="collapsed")
            owner_filter = "Reguler" if "Reguler" in owner_opt else "Konsinyasi"

        with c_lokasi:
            lokasi = st.selectbox("Lokasi", ["Floor", "Gudang"])
        
        with c_jenis:
            opsi_jenis = ["Stok", "Demo"]
            if lokasi == "Gudang": opsi_jenis = ["Stok"]
            jenis = st.selectbox("Jenis", opsi_jenis)
    
    st.divider()
    
    if "Silahkan Pilih" in nama_user:
        st.info("👋 Halo! Untuk memulai Stock Opname, mohon **pilih nama Anda** terlebih dahulu di menu kiri atas.")
        st.stop()
        
    search_txt = st.text_input("🔍 Cari (Ketik Brand/Nama)", placeholder="Contoh: Samsung, Robot...")
    
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear() # Clear cache data agar ambil data baru
        st.session_state.pop('current_df', None)
        st.rerun()

    # Ambil data. Waktu dan data disimpan ke st.session_state['data_loaded_time'] dan ['current_df'] di dalam fungsi get_data
    df = get_data(lokasi, jenis, owner_filter, search_term=search_txt, only_active=True)
    loaded_time = st.session_state.get('data_loaded_time', datetime(1970, 1, 1))
    
    if df.empty:
        st.info(f"Tidak ada data barang **{owner_filter}** di {lokasi}-{jenis}.")
        return

    df_sn = df[df['kategori_barang'] == 'SN'].copy()
    df_non = df[df['kategori_barang'] == 'NON-SN'].copy()

    # --- TABEL SN ---
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
            updates_count = 0
            conflict_found = False
            
            # [v3.9] Iterasi dan cek konflik sebelum save
            for i, row in edited_sn.iterrows():
                # Dapatkan baris asli dari sesi state (yang kita punya saat loading)
                original_row = st.session_state['current_df'].loc[st.session_state['current_df']['id'] == row['id']].iloc[0]
                
                # Cek apakah user membuat perubahan pada item ini
                original_checked = original_row['fisik_qty'] > 0
                new_checked = row['Ditemukan']
                
                if original_checked != new_checked:
                    # Perubahan terdeteksi, lakukan cek konflik!
                    db_updated_at_str, updated_by_db = get_db_updated_at(row['id'])
                    db_updated_at = datetime.fromisoformat(db_updated_at_str.replace('Z', '+00:00')) # Konversi ke datetime object
                    
                    if db_updated_at > loaded_time:
                        # KONFLIK TERDETEKSI!
                        st.error(f"⚠️ KONFLIK DATA di SN {row['serial_number']} ({row['nama_barang']})! Data ini baru saja diubah oleh {updated_by_db} pada {db_updated_at.strftime('%H:%M:%S')}. Mohon tekan **Refresh Data** dan ulangi input Anda.")
                        conflict_found = True
                        break # Stop proses simpan
                    
                    # Jika tidak ada konflik, lakukan update
                    supabase.table("stock_opname").update({
                        "fisik_qty": 1 if new_checked else 0, 
                        "updated_at": datetime.utcnow().isoformat(), 
                        "updated_by": nama_user
                    }).eq("id", row['id']).execute()
                    updates_count += 1
            
            if not conflict_found:
                if updates_count > 0: st.toast(f"✅ {updates_count} SN Tersimpan!"); time.sleep(0.5); st.rerun()

    # --- TABEL NON-SN ---
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
            updates_count = 0
            conflict_found = False

            for i, row in edited_non.iterrows():
                original_row = st.session_state['current_df'].loc[st.session_state['current_df']['id'] == row['id']].iloc[0]
                
                # Cek apakah user membuat perubahan pada item ini
                if original_row['fisik_qty'] != row['fisik_qty']:
                    # Perubahan terdeteksi, lakukan cek konflik!
                    db_updated_at_str, updated_by_db = get_db_updated_at(row['id'])
                    db_updated_at = datetime.fromisoformat(db_updated_at_str.replace('Z', '+00:00'))
                    
                    if db_updated_at > loaded_time:
                        # KONFLIK TERDETEKSI!
                        st.error(f"⚠️ KONFLIK DATA di {row['nama_barang']}! Data ini baru saja diubah oleh {updated_by_db} pada {db_updated_at.strftime('%H:%M:%S')}. Mohon tekan **Refresh Data** dan ulangi hitungan Anda.")
                        conflict_found = True
                        break 
                        
                    # Jika tidak ada konflik, lakukan update
                    supabase.table("stock_opname").update({
                        "fisik_qty": row['fisik_qty'], 
                        "updated_at": datetime.utcnow().isoformat(), 
                        "updated_by": nama_user
                    }).eq("id", row['id']).execute()
                    updates_count += 1

            if not conflict_found:
                if updates_count > 0: st.toast(f"✅ {updates_count} Data Tersimpan!"); time.sleep(0.5); st.rerun()

# --- HALAMAN ADMIN ---
def page_admin():
    st.title("🛡️ Admin Dashboard (v3.9)")
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
        
        # [Final UI Fix - Tombol Pindah di bawah Checkbox] 
        # Menggunakan satu kolom input dan menempatkan tombol di baris berikutnya.

        input_pin = st.text_input("Masukkan PIN Keamanan", type="password", placeholder="PIN Standar: 123456", key="final_pin")
        
        # Checkbox di baris berikutnya
        st.session_state['confirm_reset_state'] = st.checkbox("Saya sadar data sesi ini akan hilang permanen.", key="final_check")
        
        st.write("") # Spacer ringan
        
        # Tombol di baris paling bawah, full width
        if st.button("🔥 HAPUS SESI INI", use_container_width=True):
            if input_pin == RESET_PIN:
                if st.session_state.get('confirm_reset_state', False): 
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
    st.set_page_config(page_title="SO System v3.9", page_icon="📦", layout="wide")
    st.sidebar.title("SO Apps v3.9")
    st.sidebar.success(f"Sesi: {get_active_session_info()}")
    menu = st.sidebar.radio("Navigasi", ["Sales Input", "Admin Panel"])
    if menu == "Sales Input": page_sales()
    elif menu == "Admin Panel":
        pwd = st.sidebar.text_input("Password Admin", type="password")
        if pwd == "admin123": page_admin()

if __name__ == "__main__":
    main()
