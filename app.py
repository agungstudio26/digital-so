import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime, timezone
import time
import io
from openpyxl.styles import PatternFill, Font, Alignment
from postgrest.exceptions import APIError

# --- KONFIGURASI [v4.6 - Progress QTY Based] ---
SUPABASE_URL = st.secrets["SUPABASE_URL"] if "SUPABASE_URL" in st.secrets else ""
SUPABASE_KEY = st.secrets["SUPABASE_KEY"] if "SUPABASE_KEY" in st.secrets else ""
DAFTAR_SALES = ["Agung", "Al Fath", "Reza", "Rico", "Sasa", "Mita", "Supervisor"]
RESET_PIN = "123456" # PIN Reset

if not SUPABASE_URL:
    st.error("⚠️ Konfigurasi Database Belum Ada.")
    st.stop()

@st.cache_resource
def init_connection():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_connection()

# --- FUNGSI HELPER WAKTU & KONVERSI ---
def parse_supabase_timestamp(timestamp_str):
    """Mengubah string timestamp Supabase menjadi objek datetime yang aman"""
    try:
        if timestamp_str and timestamp_str.endswith('Z'):
             timestamp_str = timestamp_str[:-1] + '+00:00'
        return datetime.fromisoformat(timestamp_str) if timestamp_str else datetime(1970, 1, 1, tzinfo=timezone.utc)
    except Exception as e:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

def convert_df_to_excel(df):
    """Mengubah DataFrame menjadi file Excel dengan Header Cantik, termasuk Keterangan"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        cols = ['batch_id', 'sku', 'brand', 'nama_barang', 'owner_category', 'lokasi', 'jenis', 'system_qty', 'fisik_qty', 'keterangan', 'updated_by', 'updated_at']
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
    
    start_time = datetime.now(timezone.utc)
    response = query.order("nama_barang").execute()
    df = pd.DataFrame(response.data)

    if 'keterangan' not in df.columns:
        df['keterangan'] = ""

    if not df.empty and search_term:
        df = df[df['nama_barang'].str.contains(search_term, case=False, na=False) | 
                df['brand'].str.contains(search_term, case=False, na=False) |
                df['sku'].str.contains(search_term, case=False, na=False)]
    
    st.session_state['data_loaded_time'] = start_time
    st.session_state['current_df'] = df.copy()
    
    return df

def get_db_updated_at(id_barang):
    """Fungsi helper untuk mengambil updated_at dari DB saat ini"""
    try:
        res = supabase.table("stock_opname").select("updated_at, updated_by").eq("id", id_barang).limit(1).execute()
        
        if res.data and len(res.data) > 0:
            data = res.data[0]
            return data.get('updated_at'), data.get('updated_by')
        else:
            return datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(), "SYSTEM"
            
    except Exception as e:
        return datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(), "SYSTEM_ERROR"

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
            "fisik_qty": 0, "updated_by": "-", "is_active": True, "batch_id": session_name,
            "keterangan": None
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
            keterangan_offline = row.get('Keterangan', '')
            if pd.notna(qty_to_update):
                supabase.table("stock_opname").update({
                    "fisik_qty": int(qty_to_update), "updated_by": "Offline Upload",
                    "updated_at": datetime.utcnow().isoformat(),
                    "keterangan": str(keterangan_offline) if pd.notna(keterangan_offline) and str(keterangan_offline).strip() else None
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
        'Hitungan Fisik': [10, 98, 50],
        'Keterangan': ['Hitungan Fisik Sesuai', 'Hilang 2 unit', '']
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


# --- LOGIKA CARD VIEW & UPDATE ---

def handle_update(row, new_qty, is_sn, nama_user, loaded_time, keterangan=""):
    """Logika pemrosesan, cek konflik, dan update untuk satu baris data"""
    id_barang = row['id']
    updates_count = 0
    conflict_found = False

    original_row_match = st.session_state['current_df'].loc[st.session_state['current_df']['id'] == id_barang]
    
    if original_row_match.empty:
        st.error(f"Error: Item ID {id_barang} tidak ditemukan di sesi awal.")
        return 0, True

    original_row = original_row_match.iloc[0]
    original_qty = original_row['fisik_qty']

    original_notes = original_row.get('keterangan', '') if original_row.get('keterangan') is not None else ''
    keterangan_to_save = keterangan if keterangan.strip() else None

    is_qty_changed = (original_qty != new_qty)
    is_notes_changed = (original_notes.strip() != (keterangan_to_save.strip() if keterangan_to_save else ''))

    if is_qty_changed or is_notes_changed:
        
        # Cek Konflik
        db_updated_at_str, updated_by_db = get_db_updated_at(id_barang)
        db_updated_at = parse_supabase_timestamp(db_updated_at_str)
        
        if db_updated_at > loaded_time:
            st.error(f"⚠️ KONFLIK DATA: **{row['nama_barang']}**! Data diubah oleh **{updated_by_db}** pada {db_updated_at.astimezone(None).strftime('%H:%M:%S')}. Mohon **Muat Ulang Data**.")
            return 0, True

        # Lakukan Update
        update_payload = {
            "fisik_qty": new_qty, 
            "updated_at": datetime.utcnow().isoformat(), 
            "updated_by": nama_user,
            "keterangan": keterangan_to_save
        }

        try:
            supabase.table("stock_opname").update(update_payload).eq("id", id_barang).execute()
            updates_count += 1
        except APIError as api_e:
            st.error(f"❌ Gagal Simpan Item {row['nama_barang']}. Mohon Cek Database/SKU. Detail: {api_e}")
            return 0, True 
        
    return updates_count, conflict_found

# --- HALAMAN SALES ---
def page_sales():
    session_name = get_active_session_info()
    st.title(f"📱 SO: {session_name}")
    
    with st.container():
        c_pemeriksa, c_owner, c_lokasi, c_jenis = st.columns([1, 1, 0.7, 0.7])

        with c_pemeriksa:
            opsi_sales = ["-- Silahkan Pilih Nama Petugas --"] + DAFTAR_SALES
            nama_user = st.selectbox("👤 Nama Pemeriksa", opsi_sales)
        
        with c_owner:
            st.caption("Sumber Barang:")
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
    
    if st.button("🔄 Muat Ulang Data"):
        st.cache_data.clear()
        st.session_state.pop('current_df', None)
        st.rerun()

    df = get_data(lokasi, jenis, owner_filter, search_term=search_txt, only_active=True)
    loaded_time = st.session_state.get('data_loaded_time', datetime(1970, 1, 1, tzinfo=timezone.utc))
    
    if df.empty:
        st.info(f"Tidak ada data barang **{owner_filter}** di {lokasi}-{jenis}.")
        return

    df_sn = df[df['kategori_barang'] == 'SN'].copy()
    df_non = df[df['kategori_barang'] == 'NON-SN'].copy()
    
    # [v4.6] Progress Monitoring - QTY Based
    total_qty_sistem = df['system_qty'].sum()
    total_qty_fisik_tercatat = df['fisik_qty'].sum()
    progress_percent = total_qty_fisik_tercatat / total_qty_sistem if total_qty_sistem > 0 else 0
    
    st.markdown("---")
    col_metric, col_bar = st.columns([1, 3])
    
    with col_metric:
        st.metric("Total Unit Dicatat", f"{total_qty_fisik_tercatat} / {total_qty_sistem}")
    with col_bar:
        st.write("")
        st.caption(f"Progress: {progress_percent * 100:.1f}%")
        st.progress(progress_percent)
    st.markdown("---")


    # [v4.6] LIST BARANG SN (Keterangan Opsional)
    if not df_sn.empty:
        st.subheader(f"📋 SN ({len(df_sn)}) - {owner_filter}")
        
        for index, row in df_sn.iterrows():
            item_id = row['id']
            is_checked = row['fisik_qty'] > 0
            
            status_text = "Ditemukan" if is_checked else "Belum Dicek"
            status_color = "green" if is_checked else "gray"
            
            checkbox_key = f"sn_check_{item_id}"
            notes_key = f"notes_sn_{item_id}"
            
            current_notes = row.get('keterangan', '') if row.get('keterangan') is not None else ''
            
            with st.expander(f"**{row['brand']}** | {row['nama_barang']} | Status: :{status_color}[{status_text}]", expanded=False):
                col_info, col_input = st.columns([2, 1])

                with col_info:
                    st.markdown(f"**SKU:** {row['sku']}")
                    st.markdown(f"**SN:** `{row['serial_number']}`")
                    st.markdown(f"**Dicek Oleh:** {row['updated_by']}")
                    if current_notes:
                         st.markdown(f"**Catatan Sebelumnya:** `{current_notes}`")
                
                with col_input:
                    new_check = st.checkbox("ADA FISIK?", value=is_checked, key=checkbox_key)
                    
                    keterangan = st.text_area("Keterangan/Isu (Opsional)", value=current_notes, key=notes_key, height=50)

                    if st.button("Simpan Item SN", key=f"btn_sn_{item_id}", type="primary", use_container_width=True):
                        new_qty = 1 if new_check else 0
                        
                        is_qty_changed = (new_qty != row['fisik_qty'])
                        is_notes_changed = (current_notes.strip() != keterangan.strip())
                        
                        if not is_qty_changed and not is_notes_changed:
                            st.info("Tidak ada perubahan yang tersimpan.")
                            continue

                        updates, conflict = handle_update(row, new_qty, True, nama_user, loaded_time, keterangan.strip())
                        if not conflict and updates > 0:
                            st.toast(f"✅ SN {row['nama_barang']} disimpan!", icon="💾")
                            time.sleep(0.5)
                            st.rerun()
                        elif not conflict:
                            st.info("Tidak ada perubahan yang tersimpan.")
                            
    st.markdown("---")

    # [v4.6] LIST BARANG NON-SN (Keterangan Opsional)
    if not df_non.empty:
        st.subheader(f"📦 Non-SN ({len(df_non)}) - {owner_filter}")

        for index, row in df_non.iterrows():
            item_id = row['id']
            
            default_qty = row['fisik_qty']
            selisih_sistem = default_qty - row['system_qty']
            
            status_text = "MATCH" if selisih_sistem == 0 else ("LEBIH" if selisih_sistem > 0 else "KURANG")
            status_color = "green" if selisih_sistem == 0 else "red"
            
            header_text = f"**{row['brand']}** | {row['nama_barang']} | Selisih: :{status_color}[{selisih_sistem}]"
            
            notes_key = f"notes_non_{item_id}"
            current_notes = row.get('keterangan', '') if row.get('keterangan') is not None else ''
            
            with st.expander(header_text, expanded=False):
                col_info, col_input = st.columns([2, 1])
                
                with col_info:
                    st.markdown(f"**Odoo Qty:** `{row['system_qty']}`")
                    st.markdown(f"**SKU:** {row['sku']}")
                    st.markdown(f"**Dicek Oleh:** {row['updated_by']}")
                    if current_notes:
                         st.markdown(f"**Catatan Sebelumnya:** `{current_notes}`")
                
                with col_input:
                    new_qty = st.number_input("JML FISIK", value=default_qty, min_value=0, step=1, key=f"qty_non_{item_id}", label_visibility="collapsed")
                    
                    keterangan = st.text_area("Keterangan/Isu (Opsional)", value=current_notes, key=notes_key, height=50)

                    if st.button("Simpan Item Non-SN", key=f"btn_non_{item_id}", type="primary", use_container_width=True):
                        
                        is_qty_changed = (new_qty != row['fisik_qty'])
                        is_notes_changed = (current_notes.strip() != keterangan.strip())

                        if not is_qty_changed and not is_notes_changed:
                             st.info("Tidak ada perubahan yang tersimpan.")
                             continue

                        updates, conflict = handle_update(row, new_qty, False, nama_user, loaded_time, keterangan.strip())
                        if not conflict and updates > 0:
                            st.toast(f"✅ Qty {row['nama_barang']} disimpan!", icon="💾")
                            time.sleep(0.5)
                            st.rerun()
                        elif not conflict:
                            st.info("Tidak ada perubahan yang tersimpan.")


# --- FUNGSI ADMIN ---
def page_admin():
    st.title("🛡️ Admin Dashboard (v4.6)")
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
        st.caption("Jika internet mati, sales pakai Excel ini. Admin upload disini untuk merge. File harus ada kolom 'Keterangan'.")
        st.download_button("⬇️ Download Template Offline", get_template_excel(), "Template_Offline_v4.6.xlsx")
        
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
            
            st.dataframe(df[['sku', 'nama_barang', 'system_qty', 'fisik_qty', 'keterangan', 'updated_by', 'updated_at']])
            
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
        
        input_pin = st.text_input("Masukkan PIN Keamanan", type="password", placeholder="PIN Standar: 123456", key="final_pin")
        
        st.session_state['confirm_reset_state'] = st.checkbox("Saya sadar data sesi ini akan hilang permanen.", key="final_check")
        
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
    st.set_page_config(page_title="SO System v4.6", page_icon="📦", layout="wide")
    st.sidebar.title("SO Apps v4.6")
    st.sidebar.success(f"Sesi: {get_active_session_info()}")
    menu = st.sidebar.radio("Navigasi", ["Sales Input", "Admin Panel"])
    if menu == "Sales Input": page_sales()
    elif menu == "Admin Panel":
        pwd = st.sidebar.text_input("Password Admin", type="password")
        if pwd == "admin123": page_admin()

if __name__ == "__main__":
    main()
