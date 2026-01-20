import streamlit as st
import csv
import os
from datetime import datetime
import json
import tempfile
import gspread
from google.oauth2.service_account import Credentials
from oauth2client.service_account import ServiceAccountCredentials
from gspread_formatting import *
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
import cloudinary
import cloudinary.uploader
import qrcode
from io import BytesIO

st.set_page_config(
    page_title=" Inventoria Untuk DIT",   # Title shown in browser tab
    page_icon="logo-bmkg.png",                                # Favicon (emoji or image path)
    layout="wide",                                 # "centered" or "wide"
    initial_sidebar_state="expanded",               # "expanded" or "collapsed"
   
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]

spreadsheet_id_1 = "16TyrN_dLzqCCPnc15K8REKxzGF4bbY6dZzU8QLzo1kA"
FOLDER_ID = "1Nfz9wDdW6SjY_2eXY_crxWLZUTJFt_IX"
LOG_SPREADSHEET_ID="1CBHd51k5_3XXvBJ093USsrkXXw5lPBLh6SjQIXdcKOA"

cloudinary.config(
    cloud_name=st.secrets["cloudinary"]["cloud_name"],
    api_key=st.secrets["cloudinary"]["api_key"],
    api_secret=st.secrets["cloudinary"]["api_secret"]
)

creds = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"],
    scopes=SCOPES
)
client = gspread.authorize(creds)

# Replace with your spreadsheet ID
SPREADSHEET_ID = st.secrets["gcp"]["spreadsheet_id_1"]
sheet = client.open_by_key(SPREADSHEET_ID).sheet1


if not SPREADSHEET_ID or not FOLDER_ID:
    st.warning("Set secrets: spreadsheet_id and drive_folder_id. See deploy checklist below.")

# Google clients
gs_client = gspread.authorize(creds)
drive_service = build("drive", "v3", credentials=creds)
spreadsheet = gs_client.open_by_key(SPREADSHEET_ID)
log_spreadsheet = gs_client.open_by_key(LOG_SPREADSHEET_ID)

# Map display names -> worksheet names
FLOOR_TO_SHEET = {
    "Data Inventaris Informasi Kualitas Udara BMKG PUSAT": "Penambahan Inventaris",
    "Data Barang yang Dikirim atau Digunakan": "Penggunaan Inventaris",
}

SOURCE_FLOOR = "Penambahan Inventaris"         
DESTINATION_SHEET = "Penggunaan Inventaris"


LOG_HEADERS = ["No", "Kode Inventaris", "Nama Barang", "Tanggal Masuk", 
               "Tahun Pembuatan", "Tempat Penyimpanan", "Jumlah", "Kondisi", "Petugas", "keterangan"]



def ensure_header(ws):
    """Ensure the header row is exactly HEADERS."""
    first_row = ws.row_values(1)
    if first_row != HEADERS:
        ws.update("A1:J1", [HEADERS])
        
def get_ws(floor_display_name):
    """Modified with safety check to catch naming errors."""
    try:
        # Get the internal sheet name from your dictionary
        sheet_name = FLOOR_TO_SHEET[floor_display_name]
        return spreadsheet.worksheet(sheet_name)
    except KeyError:
        st.error(f"❌ Key '{floor_display_name}' tidak ada di FLOOR_TO_SHEET.")
        st.stop()
    except gspread.exceptions.WorksheetNotFound:
        st.error(f"❌ Tab bernama '{sheet_name}' tidak ditemukan di Google Sheets Anda.")
        st.info("Pastikan nama tab di Google Sheets sama persis dengan yang ada di FLOOR_TO_SHEET.")
        st.stop()

def list_records(ws):
    """Return rows as list[dict] with forced headers."""
    ensure_header(ws)
    return ws.get_all_records(expected_headers=HEADERS)


def upsert_item(ws, nama_barang: str, tanggal_masuk: str, 
                tahun_pembuatan: str, tempat_penyimpanan: str, jumlah: int, 
                kondisi: str, petugas: str, keterangan: str):
    
    records = list_records(ws)
    
    # 1. Automatic ID Logic
    if not records:
        next_no = 1
    else:
        last_no = int(records[-1].get("No", 0))
        next_no = last_no + 1

    date_slug = str(tanggal_masuk).replace("-", "").replace("/", "")
    auto_kode = f"INV-{date_slug}-{next_no:03d}"

    # 2. Match Check: Nama Barang + Tanggal Masuk + KONDISI
    # If all three match, we just add the quantity.
    for idx, row in enumerate(records, start=2):
        if (row["Nama Barang"] == nama_barang and 
            str(row["Tanggal Masuk"]) == str(tanggal_masuk) and
            row["Kondisi"] == kondisi): # <--- New condition check
            
            # Match found: Update Jumlah (Column 7)
            new_qty = int(row["Jumlah"]) + int(jumlah)
            ws.update_cell(idx, 7, new_qty) 
            
            # Optional: Update Keterangan if you want the latest note to show up
            ws.update_cell(idx, 10, keterangan)
            return

    # 3. Append New Row (If it's a new item OR a different condition)
    new_row = [
        next_no,            # Col 1: No
        auto_kode,          # Col 2: Kode Inventaris
        nama_barang,        # Col 3: Nama Barang
        tanggal_masuk,      # Col 4: Tanggal Masuk
        tahun_pembuatan,    # Col 5: Tahun Pembuatan
        tempat_penyimpanan, # Col 6: Tempat Penyimpanan
        int(jumlah),        # Col 7: Jumlah
        kondisi,            # Col 8: Kondisi (Status)
        petugas,            # Col 9: Petugas
        keterangan          # Col 10: keterangan
    ]
    
    ws.append_row(new_row)

# Destination (Used) Headers - 9 Columns (Removed 'Tempat Penyimpanan')
HEADERS_USED = ["No", "Kode Inventaris", "Nama", "Tanggal Digunakan", 
                "Tahun Pembuatan", "Jumlah", "Kondisi", "Petugas", "Keterangan"]

def transfer_item(source_floor: str, target_sheet_name: str, item_name: str, 
                  kondisi: str, jumlah: int, petugas: str, keterangan: str = ""):
    
    ws_src = get_ws(source_floor)
    
    # 1. Identify Target Worksheet
    if target_sheet_name == "Barang Terpakai":
        ws_tgt = spreadsheet.worksheet(target_sheet_name)
        is_used_sheet = True
    else:
        ws_tgt = get_ws(target_sheet_name)
        is_used_sheet = False

    # 2. Find Item in Source
    records = list_records(ws_src)
    match = next((r for r in records if r["Nama"] == item_name and r["Kondisi"] == kondisi), None)
    
    if not match:
        raise ValueError(f"Item {item_name} ({kondisi}) tidak ada di {source_floor}")

    actual_idx = records.index(match) + 2
    current_qty = int(match["Jumlah"])

    if current_qty < jumlah:
        raise ValueError(f"Stok tidak cukup. Sisa: {current_qty}")

    # 3. Update Source (Subtract or Delete)
    if current_qty == jumlah:
        ws_src.delete_rows(actual_idx)
    else:
        ws_src.update_cell(actual_idx, 7, current_qty - jumlah)

    # 4. Build the New Row for Destination
    target_records = ws_tgt.get_all_records()
    next_no = int(target_records[-1].get("No", 0)) + 1 if target_records else 1

    if is_used_sheet:
        # --- 9 COLUMNS (No 'Tempat Penyimpanan') ---
        new_row = [
            next_no,
            match["Kode Inventaris"],
            item_name,
            match["Tanggal Masuk"],   # Maps to 'Tanggal Digunakan'
            match["Tahun Pembuatan"],
            int(jumlah),              # Skips 'Tempat Penyimpanan'
            kondisi,
            petugas,
            keterangan or f"Bekas dari {source_floor}"
        ]
    else:
        # --- 10 COLUMNS (Standard Move between Floors) ---
        new_row = [
            next_no,
            match["Kode Inventaris"],
            item_name,
            match["Tanggal Masuk"],
            match["Tahun Pembuatan"],
            target_sheet_name,        # Includes 'Tempat Penyimpanan'
            int(jumlah),
            kondisi,
            petugas,
            keterangan
        ]

    ws_tgt.append_row(new_row)
    print(f"Berhasil! {item_name} dipindah ke {target_sheet_name}")

# Updated Log Headers to match your 10-column structure
LOG_HEADERS = [
    "No", "Kode Inventaris", "Nama Barang", "Tanggal Masuk", 
    "Tahun Pembuatan", "Tempat Penyimpanan", "Jumlah", 
    "Kondisi", "Petugas", "Keterangan"
]

def get_log_ws():
    """Return a worksheet for current month (create if not exists)."""
    month_tag = datetime.now().strftime("%Y_%m")  # e.g. "2025_10"
    sheet_name = f"Log_{month_tag}"

    try:
        ws = log_spreadsheet.worksheet(sheet_name)
    except:
        ws = log_spreadsheet.add_worksheet(title=sheet_name, rows=100, cols=len(LOG_HEADERS))
        ws.update("A1:H1", [LOG_HEADERS])
    return ws


def write_log(item_data, action, qty_used, petugas, keterangan=""):
    ws = get_log_ws()
    
    # ... (all your existing code to get next_no and display_location) ...

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log_row = [
        next_no,
        item_data.get("Kode Inventaris", "AUTO"),
        item_data.get("Nama Barang", "Unknown"),
        timestamp,
        item_data.get("Tahun Pembuatan", "-"),
        display_location,
        qty_used,
        item_data.get("Kondisi", "Baik"),
        petugas,
        f"[{action}] {keterangan}"
    ]
    
    # 1. Write to Google Sheets Log
    ws.append_row(log_row)

    # 2. Trigger Google Doc Creation (The "disappeared" function)
    notify_gas_log(
        nama=item_data.get("Nama Barang", "Unknown"),
        jumlah=qty_used,
        kondisi=item_data.get("Kondisi", "Baik"),
        tempat=display_location,
        timestamp=timestamp
    )


# =========================
# UI
# =========================
st.title(" 🌍 Dashboard Inventorir Bidang Informasi Kualitas Udara ")


st.markdown("""
<style>
/* Page background */
[data-testid="stAppViewContainer"] {
    background-color: #f0f6fa; /* Light blue */
}

/* Sidebar */
[data-testid="stSidebar"] {
    background-color: #ffffff; /* Slightly darker blue */
}

/* Text */
body, [data-testid="stMarkdownContainer"] {
    color: #000000; /* Black text */
}




/* 🟢 Change 'Simpan' button color */
div.stButton > button {
    background-color: #e6ebfa;      /* BMKG green */
                  /* text color */
    border: None;
    border-radius: 8px;
    padding: 0.5em 1.5em;
    font-weight: bold;
    transition: 0.3s;
}
div.stButton > button:hover {
    background-color: #1e7a1e;      /* darker green on hover */
}

/* 🔵 Change 'Browse files' upload button color */
[data-testid="stFileUploader"] section div div button {
    background-color: #0f1729;      /* BMKG blue */
   
    border-radius: 6px;
    border: none;
    font-weight: bold;
    transition: 0.3s;
}
[data-testid="stFileUploader"] section div div button:hover {
    background-color: #00008B;      /* darker blue on hover */
}
</style>

<style>

    /* GENERAL INPUT WRAPPER (Text, Number, Select, etc.) */
    .stTextInput > div,
    .stNumberInput > div,
    .stSelectbox > div,
    .stTextArea > div,
    .stDateInput > div {
        border: 2px solid #000 !important;
        border-radius: 8px !important;
        padding: 4px !important;
        background-color: white !important;
    }

   

    /* NUMBER INPUT BUTTONS ( + and - ) */
    .stNumberInput button {
        border: none !important;
        background: transparent !important;
    }

    /* FILE UPLOADER */
    .stFileUploader > div {
        border: 2px solid #000 !important;
        border-radius: 8px !important;
        padding: 10px !important;
    }

</style>
""", unsafe_allow_html=True)
menu = st.selectbox(
    "Menu",
    ["Tambahkan Inventori", "Menggunakan atau Mengirimkan barang", "Lihat Data"],
)

if menu == "Tambahkan Inventori":
    st.subheader("➕ Tambah Barang + 📤 Upload Gambar")
    nama = st.text_input("Nama Barang")
    jumlah = st.number_input("Jumlah", min_value=1, step=1)
    
    # NEW: Manual Date Input
    tanggal_input = st.date_input("Tanggal Masuk", datetime.now())
    # Convert date to string format YYYY-MM-DD
    tanggal_str = tanggal_input.strftime("%Y-%m-%d")

    # Update Kondisi and Keterangan (Based on your new 10-column system)
    kondisi = st.selectbox("Kondisi", ["Baik", "Rusak", "Perlu Perbaikan"])
    keterangan = st.text_area("Keterangan", "Stok baru")
    petugas = st.text_input("Nama Petugas")
    
    # Hidden Tahun Pembuatan (Optional: you can make this a text input too)
    tahun_pembuatan = st.text_input("Tahun Pembuatan", "2024")
    
    tempat_display = st.selectbox("Tempat", list(FLOOR_TO_SHEET.keys()))
    gambar = st.file_uploader("📷 Upload Gambar Barang", type=["jpg", "jpeg", "png"])

    if st.button("Simpan"):
        if not nama or not petugas:
            st.error("Nama Barang dan Petugas wajib diisi.")
        elif not gambar:
            st.error("Wajib upload gambar barang.")
        else:
            # Upload image to Cloudinary
            upload_result = cloudinary.uploader.upload(gambar, folder="inventory_items")
            image_url = upload_result["secure_url"]

            # QR Code Logic (Keeping your existing logic)
            qr = qrcode.make(image_url)
            buffer = BytesIO()
            qr.save(buffer, format="PNG")
            buffer.seek(0)
            qr_upload = cloudinary.uploader.upload(
                buffer,
                folder="qr_codes",
                public_id=f"qr_{nama}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            qr_url = qr_upload["secure_url"]

            ws = get_ws(tempat_display)
            
            # --- CALL UPSERT WITH MANUAL DATE ---
            upsert_item(
                ws=ws,
                nama_barang=nama,
                tanggal_masuk=tanggal_str, # Use manual date
                tahun_pembuatan=tahun_pembuatan,
                tempat_penyimpanan=tempat_display,
                jumlah=jumlah,
                kondisi=kondisi,
                petugas=petugas,
                keterangan=keterangan
            )
            
            st.success("✅ Data berhasil disimpan / diperbarui.")
            st.image(image_url, caption="📷 Gambar Barang", width=200)
            
elif menu == "Menggunakan atau Mengirimkan barang":
    st.subheader("➖ Kurangi Barang / Gunakan")
    tempat_display = st.selectbox("Gudang", list(FLOOR_TO_SHEET.keys()))
    nama = st.text_input("Nama Barang")
    kondisi = st.selectbox("Kondisi Barang yang Diambil", ["Baik", "Rusak"])
    jumlah = st.number_input("Jumlah yang dikurangi", min_value=1, step=1)
    
    # NEW: Manual Date Input for Usage
    tanggal_penggunaan = st.date_input("Tanggal Penggunaan", datetime.now())
    tgl_pakai_str = tanggal_penggunaan.strftime("%Y-%m-%d")
    
    petugas = st.text_input("Petugas yang mengambil")

    if st.button("Kurangi"):
        if not nama or not petugas:
            st.error("Nama dan Petugas wajib diisi.")
        else:
            try:
                # Use the transfer/cascade function we built earlier
                transfer_item(
                    source_floor=tempat_display,
                    target_sheet_name="Barang Terpakai",
                    item_name=nama,
                    kondisi=kondisi,
                    jumlah=jumlah,
                    petugas=petugas,
                    keterangan=f"Digunakan pada {tgl_pakai_str}" # Manual date in notes
                )
                st.success(f"✅ {jumlah} {nama} berhasil dipindahkan ke Barang Terpakai.")
            except Exception as e:
                st.error(str(e))
                                           
elif menu == "Lihat Data":
    st.subheader("📊 Data Gudang")
    
    # 1. Select the Floor/Warehouse
    tempat_display = st.selectbox("Pilih Gudang", list(FLOOR_TO_SHEET.keys()))
    ws = get_ws(tempat_display)
    
    # 2. Get data and convert to Pandas DataFrame
    data = ws.get_all_records(expected_headers=HEADERS)
    df = pd.DataFrame(data)

    if not df.empty:
        # --- SEARCH UI ---
        st.write("---")
        col1, col2 = st.columns(2)
        with col1:
            search_nama = st.text_input("🔍 Cari Nama Barang", "")
        with col2:
            search_date = st.text_input("📅 Cari Tanggal (YYYY-MM-DD)", "")

        # --- FILTERING LOGIC ---
        filtered_df = df.copy()
        if search_nama:
            filtered_df = filtered_df[filtered_df['Nama Barang'].str.contains(search_nama, case=False, na=False)]
        if search_date:
            filtered_df = filtered_df[filtered_df['Tanggal Masuk'].str.contains(search_date, na=False)]

        # --- HIGHLIGHTING FUNCTION ---
        def style_rows(row):
            """Apply colors based on the Kondisi column."""
            kondisi = row["Kondisi"]
            if kondisi == "Rusak":
                return ['background-color: #ffcccc'] * len(row) # Light Red
            elif kondisi == "Perlu Perbaikan":
                return ['background-color: #fff4cc'] * len(row) # Light Yellow
            return [''] * len(row)

        # Apply the styling
        styled_df = filtered_df.style.apply(style_rows, axis=1)

        # --- DISPLAY RESULTS ---
        st.write(f"Menampilkan {len(filtered_df)} data:")
        st.dataframe(styled_df, use_container_width=True)
        
    else:
        st.warning("Gudang ini masih kosong.")







