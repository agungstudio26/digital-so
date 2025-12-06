import streamlit as st
import pandas as pd
from supabase import create_client
from datetime import datetime
import time
import io # [v2.2] Tambahan library untuk handle download file

# --- KONFIGURASI [v2.2] ---
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

# --- FUNGSI HELPER ---

# [v2.2] Fungsi Membuat Template Excel
def get_template_excel():
    # Membuat Data Dummy sebagai contoh cara pengisian
    data = {
        'Internal Reference': ['SKU-001-HP', 'SKU-002-CBL', 'SKU-003-DEMO'],
        'Product': ['Samsung Galaxy S24 (Barang SN)', 'Kabel Data USB-C (Barang Non-SN)', 'iPhone 15 Pro (Unit Demo)'],
        'Quantity': [1, 50, 1],
        'Serial Number': ['SN12345678', '', 'IMEI998877'], # Kosongkan jika Non-SN
        'LOKASI': ['Floor', 'Gudang', 'Floor'], # Wajib isi: Floor / Gudang
        'JENIS': ['Stok', 'Stok', 'Demo']       # Wajib isi: Stok / Demo
    }
    df = pd.DataFrame(data)
    
    # Simpan ke memory buffer agar bisa didownload
    output = io.BytesIO()
    # Menggunakan writer default pandas (biasanya openpyxl)
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Template_SO')
    return output.getvalue()

def get_data(lokasi=None, jenis=None, search_term=None):
    query = supabase.table("stock_opname").select("*")
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
                "updated_by": "-"
            }
            data_to_insert.append(item)
        
        for i in range(0, len(data_to_insert), 500):
            supabase.table("stock_opname").insert(data_to_insert[i:i+500]).execute()
            
        return True, len(data_to_insert)
    except Exception as e:
        return False, str(e)

# --- HALAMAN SALES ---
def page_sales():
    st.title("📱 Input Stock Opname")
    
    with st.container():
        st.info("ℹ️ Pilih Nama Anda dan Area Kerja sebelum mulai.")
        col_id, col_area = st.columns([1, 2])
        with col_id:
            nama_user = st.selectbox("👤 Nama Pemeriksa", DAFTAR_SALES)
        with col_area:
            c1, c2 = st.columns(2)
            lokasi = c1.selectbox("Lokasi", ["Floor", "Gudang"])
            jenis = c2.selectbox("Jenis", ["Stok", "Demo"])
    
    st.divider()
    search_txt = st.text_input("🔍 Cari Barang Cepat (Nama/SKU)", placeholder="Contoh: Samsung S24...")
    
    if st.button("🔄 Refresh Data Area Ini"):
        st.session_state['last_fetch'] = time.time()

    df = get_data(lokasi, jenis, search_txt)

    if df.empty:
        if search_txt:
            st.warning(f"Barang dengan kata kunci '{search_txt}' tidak ditemukan.")
        else:
            st.info("Data area ini kosong atau sudah sesuai filter.")
        return

    df_sn = df[df['kategori_barang'] == 'SN'].copy()
    df_non = df[df['kategori_barang'] == 'NON-SN'].copy()

    # TABEL SN
    if not df_sn.empty:
        st.subheader(f"📋 Validasi SN ({len(df_sn)} Unit)")
        df_sn['Ditemukan'] = df_sn['fisik_qty'] > 0
        
        edited_sn = st.data_editor(
            df_sn[['id', 'nama_barang', 'serial_number', 'updated_by', 'Ditemukan']],
            column_config={
                "Ditemukan": st.column_config.CheckboxColumn("Fisik Ada?", default=False),
                "updated_by": st.column_config.TextColumn("Dicek Oleh", disabled=True),
                "id": None
            },
            hide_index=True, use_container_width=True, key="sn_editor"
        )
        
        if st.button("Simpan Perubahan SN", type="primary"):
            n = 0
            for i, row in edited_sn.iterrows():
                orig = df_sn[df_sn['id'] == row['id']].iloc[0]
                if (orig['fisik_qty'] > 0) != row['Ditemukan']:
                    val_to_save = 1 if row['Ditemukan'] else 0
                    update_stock(row['id'], val_to_save, nama_user)
                    n += 1
            if n > 0: 
                st.toast(f"✅ {n} Data SN berhasil disimpan!", icon="💾"); time.sleep(1); st.rerun()

    # TABEL NON-SN
    if not df_non.empty:
        st.markdown("---")
        st.subheader(f"📦 Validasi Non-SN ({len(df_non)} SKU)")
        
        edited_non = st.data_editor(
            df_non[['id', 'sku', 'nama_barang', 'system_qty', 'fisik_qty', 'updated_by']],
            column_config={
                "fisik_qty": st.column_config.NumberColumn("Jml Fisik", min_value=0),
                "system_qty": st.column_config.NumberColumn("Sistem", disabled=True),
                "updated_by": st.column_config.TextColumn("Dicek Oleh", disabled=True),
                "id": None
            },
            hide_index=True, use_container_width=True, key="non_editor"
        )
        
        st.caption("Indikator Selisih Realtime:")
        preview = edited_non.copy()
        preview['selisih'] = preview['fisik_qty'] - preview['system_qty']
        
        def color_row(val):
            return 'background-color: #d4edda' if val == 0 else 'background-color: #f8d7da'
            
        st.dataframe(preview[['nama_barang', 'system_qty', 'fisik_qty', 'selisih']].style.map(color_row, subset=['selisih']), use_container_width=True, height=150)

        if st.button("Simpan Hitungan Non-SN", type="primary"):
            n = 0
            for i, row in edited_non.iterrows():
                orig = df_non[df_non['id'] == row['id']].iloc[0]['fisik_qty']
                if orig != row['fisik_qty']:
                    update_stock(row['id'], row['fisik_qty'], nama_user)
                    n += 1
            if n > 0: 
                st.toast(f"✅ {n} Data Non-SN berhasil disimpan!", icon="💾"); time.sleep(1); st.rerun()

# --- HALAMAN ADMIN ---
def page_admin():
    st.title("🛡️ Admin Dashboard")
    
    tab1, tab2 = st.tabs(["📤 Upload Master", "📊 Monitoring & Laporan"])
    
    with tab1:
        st.markdown("### Langkah 1: Download Template")
        st.info("Gunakan format template ini agar data terbaca dengan benar.")
        
        # [v2.2] Tombol Download Template
        template_bytes = get_template_excel()
        st.download_button(
            label="📥 Download Template Excel (.xlsx)",
            data=template_bytes,
            file_name="Template_Stock_Opname.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        
        st.divider()
        st.markdown("### Langkah 2: Upload Data")
        st.warning("⚠️ Upload file baru akan MENGHAPUS data berjalan (Reset).")
        
        file = st.file_uploader("Upload File Excel yang sudah diisi", type="xlsx")
        
        if file:
            st.write("File terpilih:", file.name)
            if st.button("🚀 PROSES UPLOAD & RESET DB", type="primary"):
                with st.spinner("Sedang memproses..."):
                    df = pd.read_excel(file)
                    ok, msg = reset_and_upload(df)
                    if ok: st.success(f"Berhasil! {msg} baris data masuk.")
                    else: st.error(f"Gagal: {msg}")

    with tab2:
        if st.button("🔄 Refresh Monitoring"): st.rerun()
        df = get_data()
        if df.empty: st.info("Belum ada data."); return
        
        total = len(df)
        checked = len(df[df['updated_by'] != '-'])
        df['selisih'] = df['fisik_qty'] - df['system_qty']
        df_selisih = df[df['selisih'] != 0].copy()
        
        st.write(f"Progres: {checked/total*100:.1f}%")
        st.progress(checked/total if total > 0 else 0)
        
        k1, k2, k3 = st.columns(3)
        k1.metric("Total SKU", total)
        k2.metric("Sudah Dicek", checked)
        k3.metric("Selisih", len(df_selisih), delta_color="inverse")
        
        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            if not df_selisih.empty:
                st.download_button("📥 Download Laporan Selisih", df_selisih.to_csv(index=False).encode('utf-8'), "Laporan_Selisih.csv", "text/csv")
            else: st.success("Tidak ada selisih.")
        with c2:
            st.download_button("📥 Download Full Backup", df.to_csv(index=False).encode('utf-8'), "Full_Backup.csv", "text/csv")
            
        if not df_selisih.empty:
            st.subheader("Preview Selisih")
            st.dataframe(df_selisih[['sku','nama_barang','lokasi','jenis','system_qty','fisik_qty','updated_by']])

# --- MAIN ---
def main():
    st.set_page_config(page_title="SO System v2.2", page_icon="📦", layout="wide")
    st.sidebar.title("Stock Opname v2.2")
    menu = st.sidebar.radio("Navigasi", ["Sales Input", "Admin Panel"])
    
    if menu == "Sales Input": page_sales()
    elif menu == "Admin Panel":
        pwd = st.sidebar.text_input("Password Admin", type="password")
        if pwd == "admin123": page_admin()

if __name__ == "__main__":
    main()
