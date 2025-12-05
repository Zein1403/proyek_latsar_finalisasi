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

spreadsheet_id_1 = "1pwpsng3Uoxp2WV-JTrwAk8j7Ct174qbk1rK3R1X2C7I"
FOLDER_ID = "1Nfz9wDdW6SjY_2eXY_crxWLZUTJFt_IX"
LOG_SPREADSHEET_ID="1CBHd51k5_3XXvBJ093USsrkXXw5lPBLh6SjQIXdcKOA"

cloudinary.config(
    cloud_name=st.secrets["cloudinary"]["cloud_name"],
    api_key=st.secrets["cloudinary"]["api_key"],
    api_secret=st.secrets["cloudinary"]["api_secret"]
)

def get_qr_by_nama(ws, nama_barang):
    data = ws.get_all_records()

    for row in data:
        if row["Nama"].strip().lower() == nama_barang.strip().lower():
            return row.get("url") or row.get("URL") or row.get("qr_url")

    raise Exception("QR Code barang tidak ditemukan di Google Sheet.")

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
    "gudang lantai 1": "Lantai1",
    "gudang A lantai 4": "Lantai4A",
    "gudang B lantai 4": "Lantai4B",
    "shelter taman alat": "shelter",
}

HEADERS = ["Nama", "Jumlah", "Satuan", "Tempat", "Tanggal", "url","QR"]



# =========================
# SHEETS HELPERS
# =========================
def get_ws(floor_display_name: str):
    """Return the worksheet object for a given floor display name."""
    sheet_name = FLOOR_TO_SHEET[floor_display_name]
    return spreadsheet.worksheet(sheet_name)


def ensure_header(ws):
    """Ensure the header row is exactly HEADERS."""
    first_row = ws.row_values(1)
    if first_row != HEADERS:
        ws.update("A1:G1", [HEADERS])


def list_records(ws):
    """Return rows as list[dict] with forced headers."""
    ensure_header(ws)
    return ws.get_all_records(expected_headers=HEADERS)


def upsert_item(ws, nama: str, jumlah: int, satuan: str, tempat: str, timestamp: str,
                image_url: str = "", qr_url: str = ""):


    """
    Add 'jumlah' to an existing row that matches (nama+satuan),
    else append a fresh row.
    """
    records = list_records(ws)
    for idx, row in enumerate(records, start=2):  # 1 is header
        if row["Nama"] == nama and row["Satuan"] == satuan:
            new_qty = int(row["Jumlah"]) + int(jumlah)
            ws.update_cell(idx, 2, new_qty)   # Jumlah
            ws.update_cell(idx, 5, timestamp) # Tanggal
            #ws.update_cell(idx, 7, image_url)
            ws.update_cell(idx, 8, f'=IMAGE("{qr_url}", 4, 100, 100)') 
            return
    ws.append_row([nama, int(jumlah), satuan, tempat, timestamp, image_url])


def decrease_item(ws, nama: str, jumlah: int, satuan: str, tempat_display: str):
    """
    Decrease quantity from a matching row (nama+satuan). Delete row if qty becomes 0.
    """
    records = list_records(ws)
    for idx, row in enumerate(records, start=2):
        if row["Nama"] == nama and row["Satuan"] == satuan:
            current = int(row["Jumlah"])
            if current < jumlah:
                raise ValueError(f"Stok {nama} tidak cukup di {tempat_display}. Sisa: {current}")
            new_qty = current - jumlah
            if new_qty == 0:
                ws.delete_rows(idx)
            else:
                ws.update_cell(idx, 2, new_qty)
                ws.update_cell(idx, 5, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            return
    raise ValueError(f"Item {nama} ({satuan}) tidak ditemukan di {tempat_display}.")


def move_item(source_ws, target_ws, item_name: str, jumlah: int, satuan: str,
              source_display: str, target_display: str, qr_url:str):
    """
    Move 'jumlah' from source to target for the matching item (nama+satuan).
    """
    # 1) decrease from source
    records = list_records(source_ws)
    for idx, row in enumerate(records, start=2):
        if row["Nama"] == item_name and row["Satuan"] == satuan:
            current = int(row["Jumlah"])
            if current < jumlah:
                raise ValueError(f"Stok {item_name} tidak cukup di {source_display}. Sisa: {current}")
            # decrease
            new_qty = current - jumlah
            if new_qty == 0:
                source_ws.delete_rows(idx)
            else:
                source_ws.update_cell(idx, 2, new_qty)
                source_ws.update_cell(idx, 5, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            # 2) upsert to target
            upsert_item(
                target_ws,
                item_name,
                jumlah,
                satuan,
                target_display,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                qr_url
            )
            return
    raise ValueError(f"Barang '{item_name}' ({satuan}) tidak ditemukan di {source_display}.")

LOG_HEADERS = ["Item", "Action", "Jumlah", "Satuan", "Tempat Asal", "Tempat Tujuan", "Waktu", "QR_Code"]

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


def write_log(item, action, jumlah, satuan, tempat_asal="", tempat_tujuan="", qr_url=""):
    """Append a new row to monthly log sheet."""
    ws = get_log_ws()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Add IMAGE formula for QR code in last column
    image_formula = f'=IMAGE("{qr_url}", 4, 100, 100)' if qr_url else ""
    ws.append_row([item, action, jumlah, satuan, tempat_asal, tempat_tujuan, timestamp, image_formula])


def notify_gas_log(nama, jumlah, satuan, tempat, timestamp, qr_url=None):
    GAS_URL = "https://script.google.com/macros/s/AKfycbwUL8BrggWowmOOAO20xV0TEYqwXhucSdYwxAU8ppZifj20uxJL83p1JXMk-bztVm-WeQ/exec"
    payload = {
        "nama": nama,
        "jumlah": jumlah,
        "satuan": satuan,
        "tempat": tempat,
        "timestamp": timestamp,
        "qr_url": qr_url,
    }

    response = requests.post(GAS_URL, data=json.dumps(payload))
    try:
        result = response.json()
        if result.get("status") == "success":
            print("✅ Google Doc created:", result["doc_url"])
        else:
            print("❌ GAS Error:", result.get("message"))
    except Exception as e:
        print("❌ Response Error:", e, response.text)


# =========================
# UI
# =========================
st.title(" 🌍 Dashboard Inventorir Bidang Informasi Kualitas Udara ")

hide_streamlit_style = """
    <style>
    #MainMenu {visibility: hidden;}  /* hides hamburger menu top right */
    footer {visibility: hidden;}     /* hides "Made with Streamlit" */
    header {visibility: hidden;}     /* hides top header that contains "Fork" link */
    </style>
"""
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

/* Hide footer */
footer {visibility: hidden;}
header {visibility: hidden;}


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

    /* REMOVE Streamlit's default gray box background */
    .stTextInput > div > div,
    .stNumberInput > div > div,
    .stSelectbox > div > div,
    .stTextArea > div > div {
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
    ["Tambahkan peralatan atau suku cadang", "Kurangi alat atau suku cadang", "Pindahkan Barang atau suku cadang", "Lihat Data"],
)

if menu == "Tambahkan peralatan atau suku cadang":
    st.subheader("➕ Tambah Barang + 📤 Upload Gambar")
    nama = st.text_input("Nama Barang")
    jumlah = st.number_input("Jumlah", min_value=1, step=1)
    satuan = st.selectbox("Satuan", ["Meter", "kg", "liter", "buah"])
    tempat_display = st.selectbox("Tempat", list(FLOOR_TO_SHEET.keys()))
    gambar = st.file_uploader("📷 Upload Gambar Barang", type=["jpg", "jpeg", "png"])  # NEW

    if st.button("Simpan"):
        if not nama:
            st.error("Nama wajib diisi.")
        elif not gambar:  # NEW RULE
            st.error("Wajib upload gambar barang.")
        else:
            # Upload gambar ke Cloudinary
            upload_result = cloudinary.uploader.upload(gambar, folder="inventory_items")
            image_url = upload_result["secure_url"]
       # 2. Generate QR Code dari image_url
            qr = qrcode.make(image_url)
            buffer = BytesIO()
            qr.save(buffer, format="PNG")
            buffer.seek(0)

        # Upload QR ke Cloudinary
            qr_upload = cloudinary.uploader.upload(
                buffer,
                folder="qr_codes",
                public_id=f"qr_{nama}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            
        qr_url = qr_upload["secure_url"]
        ws = get_ws(tempat_display)
        upsert_item(
            ws,
            nama,
            jumlah,
            satuan,
            tempat_display,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            #image_url,
            #,
            qr_url,
           f'=IMAGE("{qr_url}", 4, 100, 100)',
        )    
        write_log(
        item=nama,
        action="Menambahkan",
        jumlah=jumlah,
        satuan=satuan,
        tempat_asal="-",
        tempat_tujuan=tempat_display,
        qr_url=qr_url,
        )

       # st.success("✅ Data berhasil disimpan / diperbarui.")
       # st.image(image_url)
       # st.write(f"🔗 [Lihat Gambar]({image_url})")

       # st.success("✅ Data berhasil disimpan / diperbarui.")
      #  st.image(img_url, caption="QR Code Barang", width=200)
      #  st.success("✅ Data + QR berhasil disimpan ke Google Sheet!")

        st.success("✅ Data berhasil disimpan / diperbarui.")
        st.image(image_url, caption="📷 Gambar Barang", width=200)
        st.image(qr_url, caption="📱 QR Code Barang", width=200)
        st.write(f"🔗 [Lihat Gambar Barang]({image_url})")
        st.write(f"🔗 [Lihat QR Code]({qr_url})")
        
elif menu == "Kurangi alat atau suku cadang":
    st.subheader("➖ Kurangi Barang")
    tempat_display = st.selectbox("Gudang", list(FLOOR_TO_SHEET.keys()))
    nama = st.text_input("Nama Barang")
    jumlah = st.number_input("Jumlah yang dikurangi", min_value=1, step=1)
    satuan = st.selectbox("Satuan", ["Meter", "kg", "liter", "buah"])
    if st.button("Kurangi"):
        if not nama:
            st.error("Nama wajib diisi.")
        else:
            try:
                ws = get_ws(tempat_display)
                decrease_item(ws, nama, jumlah, satuan, tempat_display)
                write_log(
                item=nama,
                action="Mengurangi",
                jumlah=jumlah,
                satuan=satuan,
                tempat_asal=tempat_display,
                tempat_tujuan="-"
                )

                st.success("✅ Stok berhasil dikurangi.")
            except Exception as e:
                st.error(str(e))

elif menu == "Pindahkan Barang atau suku cadang":
    st.subheader("🔄 Pindahkan Barang")

    source_display = st.selectbox("Dari", list(FLOOR_TO_SHEET.keys()))
    target_display = st.selectbox("Ke", list(FLOOR_TO_SHEET.keys()))
    nama = st.text_input("Nama Barang")
    jumlah = st.number_input("Jumlah yang dipindahkan", min_value=1, step=1)
    satuan = st.selectbox("Satuan", ["Meter", "kg", "liter", "buah"])

    if st.button("Pindahkan"):
        if source_display == target_display:
            st.error("Gudang asal dan tujuan tidak boleh sama.")
        elif not nama:
            st.error("Nama wajib diisi.")
        else:
            try:
                source_ws = get_ws(source_display)
                target_ws = get_ws(target_display)

                # ✅ AMBIL QR LANGSUNG DARI SHEET
                qr_url = get_qr_by_nama(source_ws, nama)

                move_item(
                    source_ws,
                    target_ws,
                    nama,
                    jumlah,
                    satuan,
                    source_display,
                    target_display,
                    qr_url=qr_url
                )

                write_log(
                    item=nama,
                    action="Memindahkan",
                    jumlah=jumlah,
                    satuan=satuan,
                    tempat_asal=source_display,
                    tempat_tujuan=target_display,
                    qr_url=qr_url
                )

                st.success("✅ Barang berhasil dipindahkan.")
                st.image(qr_url, caption="📱 QR Barang yang Dipindahkan", width=200)

            except Exception as e:
                st.error(str(e))

                                           
elif menu == "Lihat Data":
    st.subheader("📊 Data Gudang")
    tempat_display = st.selectbox("Pilih Gudang", list(FLOOR_TO_SHEET.keys()))
    ws = get_ws(tempat_display)
    ensure_header(ws)
    st.dataframe(ws.get_all_records(expected_headers=HEADERS))







