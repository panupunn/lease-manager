# app_gsheets.py — ระบบจัดการสัญญาร้านเช่า (บันทึกลง Google Sheets ถาวร)
# เทคโนโลยี: Streamlit + Google Sheets (Service Account)
# ฟีเจอร์: เพิ่มสัญญา/คำนวณวันสิ้นสุด/ค้นหา/แจ้งเตือน 30–15 วัน/ดาวน์โหลด Excel & CSV
# หมายเหตุ: ปุ่มดาวน์โหลด Excel ใช้ engine=xlsxwriter ⇒ เพิ่ม "xlsxwriter" ใน requirements.txt

from datetime import date
from io import BytesIO

import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta

import gspread
from gspread_dataframe import set_with_dataframe

COLUMNS = [
    "id",
    "shop_name",
    "contact_name",
    "phone",
    "start_date",
    "months",
    "end_date",
]

# ---------------------- Google Sheets ----------------------
def _connect_ws():
    """เชื่อม Google Sheets จากค่าใน st.secrets['gsheets'] และคืน worksheet ที่พร้อมใช้งาน"""
    cfg = st.secrets["gsheets"]

    creds_dict = {
        "type": cfg["type"],
        "project_id": cfg["project_id"],
        "private_key_id": cfg["private_key_id"],
        "private_key": cfg["private_key"],  # สามารถเป็นแบบหลายบรรทัด (ใช้ใน secrets ด้วย """ ... """)
        "client_email": cfg["client_email"],
        "client_id": cfg["client_id"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": cfg.get("client_x509_cert_url", ""),
        "universe_domain": "googleapis.com",
    }
    gc = gspread.service_account_from_dict(creds_dict)

    sh = gc.open_by_url(cfg["sheet_url"])  # แชร์ไฟล์ให้ client_email เป็น Editor แล้ว
    title = cfg.get("worksheet", "leases")

    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows="1000", cols="20")
        ws.append_row(COLUMNS)

    # ให้แน่ใจว่าหัวตารางถูกต้อง
    headers = ws.row_values(1)
    if headers != COLUMNS:
        ws.clear()
        ws.append_row(COLUMNS)

    return ws

@st.cache_data(ttl=5)
def load_data():
    ws = _connect_ws()
    rows = ws.get_all_records()  # ใช้แถวแรกเป็น header
    df = pd.DataFrame(rows)

    # เติมคอลัมน์ที่อาจขาด
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA

    # แปลงชนิดข้อมูล
    for col in ["start_date", "end_date"]:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    if not df.empty:
        df["id"] = pd.to_numeric(df["id"], errors="coerce")
        df["months"] = pd.to_numeric(df["months"], errors="coerce")
        df = df.sort_values(by=["end_date", "id"], ascending=[True, True]).reset_index(drop=True)
    return df

def save_data(df: pd.DataFrame):
    ws = _connect_ws()
    out = df.copy()
    # เขียนวันที่เป็นสตริง ISO เพื่อความชัดเจนในชีต
    for c in ["start_date", "end_date"]:
        out[c] = out[c].apply(lambda d: d.isoformat() if pd.notna(d) else "")
    ws.clear()
    set_with_dataframe(ws, out[COLUMNS], include_index=False, include_column_header=True, resize=True)

# ------------------------- Utils ---------------------------
def next_id(df: pd.DataFrame) -> int:
    if df.empty or "id" not in df:
        return 1
    return int((pd.to_numeric(df["id"], errors="coerce").fillna(0)).max() + 1)

def calc_end_date(start: date, months: int) -> date:
    return start + relativedelta(months=+int(months))

def days_until(d: date):
    if pd.isna(d):
        return None
    return (d - date.today()).days

def add_record(df: pd.DataFrame, record: dict) -> pd.DataFrame:
    return pd.concat([df, pd.DataFrame([record])], ignore_index=True)

def filter_by_query(df: pd.DataFrame, q: str) -> pd.DataFrame:
    if not q:
        return df
    q = q.strip().lower()
    mask = (
        df["shop_name"].fillna("").str.lower().str.contains(q)
        | df["contact_name"].fillna("").str.lower().str.contains(q)
        | df["phone"].fillna("").str.lower().str.contains(q)
    )
    return df[mask]

def filter_by_expiry_window(
    df: pd.DataFrame,
    within_days: int | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    tmp = df.copy()
    tmp["days_left"] = tmp["end_date"].apply(lambda d: days_until(d) if pd.notna(d) else None)

    if within_days is not None:
        tmp = tmp[(tmp["days_left"].notna()) & (tmp["days_left"] >= 0) & (tmp["days_left"] <= within_days)]

    if start is not None:
        tmp = tmp[tmp["end_date"].apply(lambda d: pd.notna(d) and d >= start)]

    if end is not None:
        tmp = tmp[tmp["end_date"].apply(lambda d: pd.notna(d) and d <= end)]

    return tmp

def style_status(days_left: int | None) -> str:
    if days_left is None:
        return "-"
    if days_left < 0:
        return f"หมดอายุมาแล้ว {-days_left} วัน"
    if days_left <= 15:
        return f"⚠️ ใกล้หมดอายุ (≤15 วัน) - เหลือ {days_left} วัน"
    if days_left <= 30:
        return f"⏰ เตือนล่วงหน้า (≤30 วัน) - เหลือ {days_left} วัน"
    return f"เหลือ {days_left} วัน"

def to_download_bytes(df: pd.DataFrame, as_excel: bool = True) -> bytes:
    if as_excel:
        bio = BytesIO()
        # ใช้ xlsxwriter เพื่อไม่ต้องติดตั้ง openpyxl
        with pd.ExcelWriter(bio, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="filtered")
        bio.seek(0)
        return bio.read()
    return df.to_csv(index=False).encode("utf-8-sig")

# --------------------------- UI ----------------------------
st.set_page_config(page_title="สัญญาร้านเช่า - Google Sheets", page_icon="📑", layout="wide")
st.title("📑 ระบบจัดการสัญญาร้านเช่า (บันทึกลง Google Sheets)")

with st.sidebar:
    st.header("เมนู")
    page = st.radio("ไปที่หน้า:", ["➕ เพิ่มสัญญา", "🔎 ค้นหา/แจ้งเตือน", "📋 ข้อมูลทั้งหมด"], index=0)
    st.caption("ข้อมูลถูกเก็บถาวรใน Google Sheets")

_df = load_data()

# ----------------------- หน้า: เพิ่มสัญญา ------------------
if page.startswith("➕"):
    st.subheader("เพิ่ม/บันทึกสัญญาใหม่")
    with st.form("add_form", clear_on_submit=True):
        c1, c2 = st.columns([2, 2])
        with c1:
            shop_name = st.text_input("ชื่อร้านค้า *")
            contact_name = st.text_input("ชื่อผู้ติดต่อ *")
            phone = st.text_input("เบอร์โทรศัพท์ *")
        with c2:
            start_date = st.date_input("วันเริ่มสัญญา *", value=date.today())
            months = st.number_input("ระยะเวลาเช่า (เดือน) *", min_value=1, max_value=240, value=12, step=1)
            end_date = calc_end_date(start_date, months)
            st.info(f"วันสิ้นสุดสัญญา: **{end_date.strftime('%Y-%m-%d')}**")
        if st.form_submit_button("บันทึกสัญญา"):
            if not all([shop_name.strip(), contact_name.strip(), phone.strip(), start_date, months]):
                st.error("กรุณากรอกข้อมูลให้ครบ")
            else:
                rec = {
                    "id": next_id(_df),
                    "shop_name": shop_name.strip(),
                    "contact_name": contact_name.strip(),
                    "phone": phone.strip(),
                    "start_date": start_date,
                    "months": int(months),
                    "end_date": end_date,
                }
                new_df = add_record(_df, rec)
                save_data(new_df)
                st.cache_data.clear()  # รีเฟรช cache ให้เห็นข้อมูลใหม่ทันที
                st.success("บันทึกสำเร็จ!")
                st.rerun()

# -------------------- หน้า: ค้นหา/แจ้งเตือน -----------------
elif page.startswith("🔎"):
    st.subheader("ค้นหา & แจ้งเตือนวันหมดสัญญา")

    with st.expander("ตัวกรองการค้นหา", expanded=True):
        c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
        with c1:
            q = st.text_input("ค้นหาจากชื่อร้าน/ผู้ติดต่อ/เบอร์")
        with c2:
            within = st.selectbox("จะแหมดอายุภายใน", ["ทั้งหมด", "15 วัน", "30 วัน", "กำหนดเอง"], index=2)
        with c3:
            start = st.date_input("หมดสัญญาตั้งแต่วันที่", value=None, format="YYYY-MM-DD")
        with c4:
            end = st.date_input("ถึงวันที่", value=None, format="YYYY-MM-DD")

        within_days = None
        if within == "15 วัน":
            within_days = 15
        elif within == "30 วัน":
            within_days = 30
        elif within == "กำหนดเอง":
            within_days = st.number_input("กำหนดเอง (วัน)", min_value=1, max_value=3650, value=60, step=1)

    df_q = filter_by_query(_df, q)
    df_f = filter_by_expiry_window(
        df_q,
        within_days=within_days,
        start=start if start else None,
        end=end if end else None,
    )

    # เพิ่มคอลัมน์สถานะ
    df_f = df_f.copy()
    df_f["days_left"] = df_f["end_date"].apply(lambda d: days_until(d) if pd.notna(d) else None)
    df_f["สถานะ"] = df_f["days_left"].apply(style_status)

    # สรุปแจ้งเตือนรวมจากข้อมูลทั้งหมด
    df_30 = filter_by_expiry_window(_df, within_days=30)
    df_15 = filter_by_expiry_window(_df, within_days=15)
    if not df_15.empty:
        st.error(f"มี {len(df_15)} สัญญาใกล้หมดภายใน 15 วัน")
    elif not df_30.empty:
        st.warning(f"มี {len(df_30)} สัญญาจะหมดภายใน 30 วัน")
    else:
        st.success("ยังไม่มีสัญญาที่จะหมดภายใน 30 วัน")

    st.markdown("### ผลการค้นหา")
    if df_f.empty:
        st.info("ไม่พบข้อมูลตามเงื่อนไข")
    else:
        st.dataframe(
            df_f[["id", "shop_name", "contact_name", "phone", "start_date", "months", "end_date", "สถานะ"]],
            use_container_width=True,
        )

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "ดาวน์โหลดเป็น Excel (.xlsx)",
            data=to_download_bytes(df_f, as_excel=True),
            file_name="leases_filtered.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with c2:
        st.download_button(
            "ดาวน์โหลดเป็น CSV",
            data=to_download_bytes(df_f, as_excel=False),
            file_name="leases_filtered.csv",
            mime="text/csv",
        )

# --------------------- หน้า: ข้อมูลทั้งหมด ------------------
else:
    st.subheader("ข้อมูลทั้งหมด")

    if _df.empty:
        st.info("ยังไม่มีข้อมูล")
    else:
        dfv = _df.copy()
        dfv["days_left"] = dfv["end_date"].apply(lambda d: days_until(d) if pd.notna(d) else None)
        dfv["สถานะ"] = dfv["days_left"].apply(style_status)
        st.dataframe(
            dfv[["id", "shop_name", "contact_name", "phone", "start_date", "months", "end_date", "สถานะ"]],
            use_container_width=True,
        )
