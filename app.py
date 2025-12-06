import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime
import time
import io

# --- KONFIGURASI [v3.1] ---
SUPABASE_URL = st.secrets["SUPABASE_URL"] if "SUPABASE_URL" in st.secrets else ""
SUPABASE_KEY = st.secrets["SUPABASE_KEY"] if "SUPABASE_KEY" in st.secrets else ""
DAFTAR_SALES = ["Sales A", "Sales B", "Sales C", "Supervisor", "Store Manager"]

if not SUPABASE_URL:
    st.error("⚠️ Database belum dikonfigurasi. Cek secrets.toml")
    st.stop()

@st.cache_resource
def init_connection():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_connection()

# --- FUNGSI HELPER EXCEL [v3.1] ---
def convert_df_to_excel(df):
    """Mengubah DataFrame menjadi file Excel di memory"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Data_SO')
    return output.getvalue()

# --- FUNGSI HELPER DATABASE ---

def get_active_session_info():
    """Mengambil nama sesi yang sedang aktif saat ini"""
    try:
        res = supabase.table("stock_opname").select("batch_id").eq("is_active", True).limit(1).execute()
        if res.data:
            return res.data[0]['batch_id']
        return "Belum Ada Sesi Aktif"
    except:
        return "-"

def get_data(lokasi=None, jenis=None, search_term=None, only_active=True, batch_id=None):
    query = supabase.table("stock_opname").select("*")
    
    if only_active:
        query = query.eq("is_active", True)
    elif batch_id:
        query = query.eq("batch_id", batch_id)
        
    if lokasi: query = query.eq("lokasi", lokasi)
    if jenis: query = query.eq("jenis", jenis)
    
    response = query.order("nama_barang").execute()
    df = pd.DataFrame(response.data)
    
    if not df.empty and search_term:
        df = df[df['nama_barang'].str.contains(search_term, case=False, na=False) | 
                df['sku'].str.contains(search_term, case=False, na=False)]
    return df

def update_stock(id_barang, qty_fisik, nama_sales):
    now = datetime.utcnow().isoformat()
    supabase.table("stock_opname").update({
        "fisik_qty": qty_fisik, 
        "updated_at": now,
        "updated_by": nama_sales 
    }).eq("id", id_barang).execute()

# --- FUNGSI ADMIN SUPER ---

def start_new_session(df, session_name):
    try:
        supabase.table("stock_opname").update({"is_active": False}).eq("is_active", True).execute()
        
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
                "updated_by": "-",
                "is_active": True,
                "batch_id": session_name
            }
            data_to_insert.append(item)
        
        for i in range(0, len(data_to_insert), 500):
            supabase.table("stock_opname").insert(data_to_insert[i:i+500]).execute()
            
        return True, len(data_to_insert)
    except Exception as e:
        return False, str(e)

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
                    "fisik_qty": int(qty_to_update),
                    "updated_by": "Offline Upload",
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("sku", sku_excel).eq("is_active", True).execute()
                success_count += 1
            
            my_bar.progress((i + 1) / total_rows)
            
        return True, success_count
    except Exception as e:
        return False, str(e)

def get_template_excel():
    data = {
        'Internal Reference': ['SKU-001'],
        'Product': ['Contoh Barang'],
        'Serial Number': [''],
        'LOKASI': ['Floor'],
        'JENIS': ['Stok'],
        'Quantity': [10],
        'Hitungan Fisik': [8]
    }
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Offline_Input')
    return output.getvalue()

# --- HALAMAN SALES ---
def page_sales():
    session_name = get_active_session_info()
    st.title(f"📱 SO: {session_name}")
    
    with st.container():
        col_id, col_area = st.columns([1, 2])
        with col_id:
            nama_user = st.selectbox("👤 Nama Pemeriksa", DAFTAR_SALES)
        with col_area:
            c1, c2 = st.columns(2)
            lokasi = c1.selectbox("Lokasi", ["Floor", "Gudang"])
            jenis = c2.selectbox("Jenis", ["Stok", "Demo"])
    
    st.divider()
    search_txt = st.text_input("🔍 Cari Barang (Nama/SKU)", placeholder="Ketik nama barang...")
    
    if st.button("🔄 Refresh"):
        st.session_state['last_fetch'] = time.time()

    df = get_data(lokasi, jenis, search_txt, only_active=True)

    if df.empty:
        st.info("Data tidak ditemukan atau belum ada Sesi Aktif.")
        return

    df_sn = df[df['kategori_barang'] == 'SN'].copy()
    df_non = df[df['kategori_barang'] == 'NON-SN'].copy()

    # --- TABEL SN ---
    if not df_sn.empty:
        st.subheader(f"📋 SN ({len(df_sn)})")
        df_sn['Ditemukan'] = df_sn['fisik_qty'] > 0
        edited_sn = st.data_editor(
            df_sn[['id', 'nama_barang', 'serial_number', 'updated_by', 'Ditemukan']],
            column_config={
                "Ditemukan": st.column_config.CheckboxColumn("Ada?", default=False),
                "updated_by": st.column_config.TextColumn("Checker", disabled=True),
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

    # --- TABEL NON-SN ---
    if not df_non.empty:
        st.subheader(f"📦 Non-SN ({len(df_non)})")
        edited_non = st.data_editor(
            df_non[['id', 'sku', 'nama_barang', 'system_qty', 'fisik_qty', 'updated_by']],
            column_config={
                "fisik_qty": st.column_config.NumberColumn("Fisik", min_value=0),
                "system_qty": st.column_config.NumberColumn("Sistem", disabled=True),
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
    st.title("🛡️ Admin Dashboard (v3.1)")
    
    active_session = get_active_session_info()
    st.info(f"📅 Sesi Aktif: **{active_session}**")
    
    tab1, tab2, tab3 = st.tabs(["🚀 Sesi Baru", "📥 Upload Offline", "🗄️ Laporan & Backup"])
    
    with tab1:
        st.markdown("### Buat Sesi Baru")
        st.warning("Membuat sesi baru akan mengarsipkan sesi yang sedang berjalan.")
        new_session_name = st.text_input("Nama Sesi Baru", placeholder="Contoh: SO Pekan 2 Nov")
        file_master = st.file_uploader("Upload Master Odoo", type="xlsx", key="u1")
        
        if file_master and new_session_name:
            if st.button("🔥 MULAI SESI BARU", type="primary"):
                with st.spinner("Proses..."):
                    df = pd.read_excel(file_master)
                    ok, msg = start_new_session(df, new_session_name)
                    if ok: st.success(f"Sesi dimulai! {msg} data."); time.sleep(2); st.rerun()
                    else: st.error(f"Gagal: {msg}")
    
    with tab2:
        st.markdown("### Upload Susulan (Internet Mati)")
        st.download_button("⬇️ Download Template Offline", get_template_excel(), "Template_Offline.xlsx")
        file_offline = st.file_uploader("Upload File Sales", type="xlsx", key="u2")
        if file_offline and st.button("Merge Data"):
            with st.spinner("Merging..."):
                df_off = pd.read_excel(file_offline)
                if 'Hitungan Fisik' not in df_off.columns: st.error("Format salah!")
                else:
                    ok, count = merge_offline_data(df_off)
                    if ok: st.success(f"Berhasil update {count} data."); time.sleep(2); st.rerun()
                    else: st.error(f"Gagal: {count}")

    with tab3:
        # Pilihan Mode
        mode_view = st.radio("Pilih Data:", ["Sesi Aktif Sekarang", "Arsip / History Lama"], horizontal=True)
        df = pd.DataFrame()
        
        if mode_view == "Sesi Aktif Sekarang":
            df = get_data(only_active=True)
        else:
            try:
                res = supabase.table("stock_opname").select("batch_id").eq("is_active", False).execute()
                batches = sorted(list(set([x['batch_id'] for x in res.data])), reverse=True)
                selected_batch = st.selectbox("Pilih Sesi Lama:", batches) if batches else None
                if selected_batch:
                    df = get_data(only_active=False, batch_id=selected_batch)
            except: st.error("Gagal load history.")

        if not df.empty:
            st.markdown("---")
            total = len(df)
            checked = len(df[df['updated_by'] != '-'])
            selisih = len(df[df['fisik_qty'] != df['system_qty']])
            
            k1, k2, k3 = st.columns(3)
            k1.metric("Total SKU", total)
            k2.metric("Sudah Dicek", f"{checked}")
            k3.metric("Selisih", selisih, delta_color="inverse")
            
            st.dataframe(df)
            
            # [v3.1] LOGIKA DOWNLOAD EXCEL BARU
            st.subheader("📥 Download Backup Excel")
            
            # Buat nama file dinamis dengan Tanggal
            tanggal_hari_ini = datetime.now().strftime("%Y-%m-%d")
            nama_file_excel = f"Laporan_SO_{tanggal_hari_ini}.xlsx"
            
            # Konversi ke Excel
            excel_data = convert_df_to_excel(df)
            
            st.download_button(
                label="📥 Download Laporan (Excel .xlsx)",
                data=excel_data,
                file_name=nama_file_excel,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

# --- MAIN ---
def main():
    st.set_page_config(page_title="SO System v3.1", page_icon="📦", layout="wide")
    
    st.sidebar.title("SO Apps v3.1")
    active_sess = get_active_session_info()
    st.sidebar.success(f"Sesi: {active_sess}")
    
    menu = st.sidebar.radio("Navigasi", ["Sales Input", "Admin Panel"])
    
    if menu == "Sales Input": page_sales()
    elif menu == "Admin Panel":
        pwd = st.sidebar.text_input("Password Admin", type="password")
        if pwd == "admin123": page_admin()

if __name__ == "__main__":
    main()
