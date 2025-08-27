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
    page_title="📦 Inventory Management System",   # Title shown in browser tab
    page_icon="📦",                                # Favicon (emoji or image path)
    layout="wide",                                 # "centered" or "wide"
    initial_sidebar_state="expanded"               # "expanded" or "collapsed"
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]
spreadsheet_id = "1pwpsng3Uoxp2WV-JTrwAk8j7Ct174qbk1rK3R1X2C7I"
FOLDER_ID = "1Nfz9wDdW6SjY_2eXY_crxWLZUTJFt_IX"


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
SPREADSHEET_ID = st.secrets["gcp"]["spreadsheet_id"]
sheet = client.open_by_key(SPREADSHEET_ID).sheet1


if not SPREADSHEET_ID or not FOLDER_ID:
    st.warning("Set secrets: spreadsheet_id and drive_folder_id. See deploy checklist below.")

# Google clients
gs_client = gspread.authorize(creds)
drive_service = build("drive", "v3", credentials=creds)
spreadsheet = gs_client.open_by_key(SPREADSHEET_ID)

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


def upsert_item(ws, nama: str, jumlah: int, satuan: str, tempat: str, timestamp: str, image_url: str, qr_url :str):

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
              source_display: str, target_display: str):
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
            )
            return
    raise ValueError(f"Barang '{item_name}' ({satuan}) tidak ditemukan di {source_display}.")


# =========================
# UI
# =========================
st.title("📦 Inventory + 📤 Upload to Shared Drive")

menu = st.selectbox(
    "Menu",
    ["Tambahkan barang", "Kurangi Barang", "Pindahkan Barang", "Lihat Data"],
)

if menu == "Tambahkan barang":
    st.subheader("➕ Tambah Barang")
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
            f'=IMAGE("{qr_url}", 4, 100, 100)'
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
elif menu == "Kurangi Barang":
    st.subheader("➖ Kurangi Barang (Pelepasan)")
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
                st.success("✅ Stok berhasil dikurangi.")
            except Exception as e:
                st.error(str(e))

elif menu == "Pindahkan Barang":
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
                move_item(source_ws, target_ws, nama, jumlah, satuan, source_display, target_display)
                st.success("✅ Barang berhasil dipindahkan.")
            except Exception as e:
                st.error(str(e))
                                           
elif menu == "Lihat Data":
    st.subheader("📊 Data Gudang")
    tempat_display = st.selectbox("Pilih Gudang", list(FLOOR_TO_SHEET.keys()))
    ws = get_ws(tempat_display)
    ensure_header(ws)
    st.dataframe(ws.get_all_records(expected_headers=HEADERS))





