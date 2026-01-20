import streamlit as st
import csv
import os
import pandas as pd
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
LOG_SPREADSHEET_ID="1jXn8ijgcqHyohvTOmwGVbZJjpeuGV5JDqz1igtd-CNo"

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
    "Penambahan Inventaris" : "Data Inventaris Informasi Kualitas Udara BMKG PUSAT" ,
    "Penggunaan Inventaris" : "Data Barang yang Dikirim atau Digunakan",
}

SOURCE_FLOOR = "Data Inventaris Informasi Kualitas Udara BMKG PUSAT"         
DESTINATION_SHEET = "Data Barang yang Dikirim atau Digunakan"

HEADERS = ["No", "Kode Inventaris", "Nama Barang", "Tanggal Masuk", 
           "Tahun Pembuatan", "Tempat Penyimpanan", "Jumlah", 
           "Kondisi", "Petugas", "keterangan"]

LOG_HEADERS = ["No", "Kode Inventaris", "Nama Barang", "Tanggal Masuk", 
               "Tahun Pembuatan", "Tempat Penyimpanan", "Jumlah", "Kondisi", "Petugas", "keterangan"]



def ensure_header(ws):
    """Force the header row to be exactly HEADERS to avoid duplicates error."""
    try:
        # We read the first row to check
        current_first_row = ws.row_values(1)
        
        # If the length is different or the values don't match exactly
        if current_first_row != HEADERS:
            # Clear the first row first to be safe
            ws.update("A1:J1", [[""] * len(HEADERS)]) 
            # Write the correct headers
            ws.update("A1:J1", [HEADERS])
    except Exception as e:
        # Fallback: just try to overwrite it
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
    if target_sheet_name == "Data Barang yang Dikirim atau Digunakan":
        ws_tgt = spreadsheet.worksheet(target_sheet_name)
        is_used_sheet = True
    else:
        ws_tgt = get_ws(target_sheet_name)
        is_used_sheet = False

    # 2. Find Item in Source
    records = list_records(ws_src)
    
    # FIX: Changed r["Nama"] to r["Nama Barang"] to match your HEADERS
    match = next((r for r in records if r["Nama Barang"] == item_name and r["Kondisi"] == kondisi), None)
    
    if not match:
        raise ValueError(f"Item {item_name} ({kondisi}) tidak ada di {source_floor}")

    # Indexing logic (records.index(match) + 2 accounts for header row)
    actual_idx = records.index(match) + 2
    current_qty = int(match["Jumlah"])

    if current_qty < jumlah:
        raise ValueError(f"Stok tidak cukup. Sisa: {current_qty}")

    # 3. Update Source (Subtract or Delete)
    if current_qty == jumlah:
        ws_src.delete_rows(actual_idx)
    else:
        # Col 7 is 'Jumlah'
        ws_src.update_cell(actual_idx, 7, current_qty - jumlah)

    # 4. Build the New Row for Destination
    target_records = ws_tgt.get_all_records()
    
    # Safe logic for next No
    if not target_records:
        next_no = 1
    else:
        try:
            next_no = int(target_records[-1].get("No", 0)) + 1
        except:
            next_no = len(target_records) + 1

    if is_used_sheet:
        new_row = [
            next_no,
            match["Kode Inventaris"],
            item_name,
            match["Tanggal Masuk"],
            match["Tahun Pembuatan"],
            int(jumlah),
            kondisi,
            petugas,
            keterangan or f"Bekas dari {source_floor}"
        ]
    else:
        new_row = [
            next_no,
            match["Kode Inventaris"],
            item_name,
            match["Tanggal Masuk"],
            match["Tahun Pembuatan"],
            target_sheet_name,
            int(jumlah),
            kondisi,
            petugas,
            keterangan
        ]

    ws_tgt.append_row(new_row)
    
    # 5. LOGGING
    # Call write_log here to ensure history is recorded
    write_log(match, "TRANSFER", jumlah, petugas, keterangan)
    
    print(f"Berhasil! {item_name} dipindah ke {target_sheet_name}")

# Updated Log Headers to match your 10-column structure
LOG_HEADERS = [
    "No", "Kode Inventaris", "Nama Barang", "Tanggal Masuk", 
    "Tahun Pembuatan", "Tempat Penyimpanan", "Jumlah", 
    "Kondisi", "Petugas", "Keterangan"
]
def get_log_ws():
    """Return a worksheet for current month (create if not exists)."""
    month_tag = datetime.now().strftime("%Y_%m")
    sheet_name = f"Log_{month_tag}"

    try:
        ws = log_spreadsheet.worksheet(sheet_name)
    except:
        # Ensure cols=10 to match your 10-column HEADERS
        ws = log_spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=10)
        # Fix the range to A1:J1 (10 columns)
        ws.update("A1:J1", [LOG_HEADERS])
    return ws
def notify_gas_log(nama, jumlah, kondisi, tempat, timestamp):
    """Triggers the Google Apps Script to create a Doc."""
    GAS_URL = "https://script.google.com/macros/s/AKfycbwUL8BrggWowmOOAO20xV0TEYqwXhucSdYwxAU8ppZifj20uxJL83p1JXMk-bztVm-WeQ/exec"
    payload = {
        "nama": nama,
        "jumlah": jumlah,
        "kondisi": kondisi,
        "tempat": tempat,
        "timestamp": timestamp,
    }
    try:
        response = requests.post(GAS_URL, data=json.dumps(payload))
        # Silence successful prints to keep the UI clean, or st.toast for success
    except Exception as e:
        print(f"❌ GAS Error: {e}")
def write_log(item_data, action, qty_used, petugas, keterangan=""):
    """
    item_data: a dictionary or row object containing the original item details.
    action: 'ADD', 'TRANSFER', or 'USE'
    """
    ws = get_log_ws()
    
    # --- 1. Calculate next_no (THE FIX) ---
    records = ws.get_all_records()
    if not records:
        next_no = 1
    else:
        try:
            # Get 'No' from the last row and add 1
            last_no = int(records[-1].get("No", 0))
            next_no = last_no + 1
        except (ValueError, TypeError):
            # Fallback if the last row's 'No' is not a valid number
            next_no = len(records) + 1
    
    # --- 2. Logic for "Tempat Penyimpanan" ---
    if action.upper() in ["USE", "DIGUNAKAN", "USED"]:
        display_location = "--- DIGUNAKAN ---"
    else:
        # Get location from item_data, fallback to 'Inventory'
        display_location = item_data.get("Tempat Penyimpanan", "Inventory")

    # --- 3. Build the 10-Column Row ---
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    log_row = [
        next_no,                                            # Col 1: No
        item_data.get("Kode Inventaris", "AUTO"),           # Col 2
        item_data.get("Nama Barang", "Unknown"),            # Col 3
        timestamp,                                          # Col 4: Tanggal (Waktu Log)
        item_data.get("Tahun Pembuatan", "-"),              # Col 5
        display_location,                                   # Col 6: Tempat
        qty_used,                                           # Col 7: Jumlah
        item_data.get("Kondisi", "Baik"),                   # Col 8
        petugas,                                            # Col 9
        f"[{action}] {keterangan}"                          # Col 10: Keterangan
    ]

    # --- 4. Write and Notify ---
    ws.append_row(log_row)
    
    # Trigger your Google Doc creation
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
            write_log(
        item_data=item_data_for_log, 
        action="TAMBAH", 
        qty_used=jumlah, 
        petugas=petugas, 
        keterangan=keterangan
    )

    st.success("✅ Data berhasil disimpan dan dicatat di Log.")
            
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
                    target_sheet_name="Data Barang yang Dikirim atau Digunakan",
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
    
    tempat_display = st.selectbox("Pilih Gudang", list(FLOOR_TO_SHEET.keys()))
    
    # 1. Get the worksheet
    ws = get_ws(tempat_display)
    
    # 2. Get data SAFELY to avoid GSpreadException
    try:
        # We fetch raw values first (uses 1 API call)
        raw_values = ws.get_all_values()
        
        if len(raw_values) > 1:
            # Manually map the data to your HEADERS
            # This avoids the "duplicate headers" error entirely
            data_rows = raw_values[1:] # Skip the first row (actual sheet headers)
            
            # Create a list of dictionaries manually
            clean_data = []
            for row in data_rows:
                # Pad row with empty strings if it's shorter than HEADERS
                padded_row = row + [""] * (len(HEADERS) - len(row))
                # Only take the first 10 columns
                clean_data.append(dict(zip(HEADERS, padded_row[:10])))
            
            df = pd.DataFrame(clean_data)
        else:
            df = pd.DataFrame(columns=HEADERS) # Empty DataFrame with correct columns

    except Exception as e:
        st.error(f"Gagal mengambil data: {e}")
        df = pd.DataFrame(columns=HEADERS)

    # 3. Filtering and Display (Only if df has data)
    if not df.empty:
        # --- SEARCH UI ---
        st.write("---")
        col1, col2 = st.columns(2)
        with col1:
            search_nama = st.text_input("🔍 Cari Nama Barang", "")
        with col2:
            search_date = st.text_input("📅 Cari Tanggal (YYYY-MM-DD)", "")

        # Filtering
        filtered_df = df.copy()
        if search_nama:
            filtered_df = filtered_df[filtered_df['Nama Barang'].astype(str).str.contains(search_nama, case=False, na=False)]
        if search_date:
            filtered_df = filtered_df[filtered_df['Tanggal Masuk'].astype(str).str.contains(search_date, na=False)]

        # --- HIGHLIGHTING ---
        def style_rows(row):
            kondisi = str(row["Kondisi"])
            if kondisi == "Rusak":
                return ['background-color: #ffcccc'] * len(row)
            elif kondisi == "Perlu Perbaikan":
                return ['background-color: #fff4cc'] * len(row)
            return [''] * len(row)

        styled_df = filtered_df.style.apply(style_rows, axis=1)
        st.write(f"Menampilkan {len(filtered_df)} data:")
        st.dataframe(styled_df, use_container_width=True)
        
    else:
        st.warning("Gudang ini masih kosong atau data tidak valid.")







