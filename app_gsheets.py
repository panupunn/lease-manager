# app_gsheets.py — ระบบจัดการสัญญาร้านเช่า (บันทึกลง Google Sheets ถาวร)
# ฟีเจอร์:
#  - เพิ่มสัญญาพร้อม "หมายเลขสัญญา" อัตโนมัติรูปแบบ YYYYMM-XXX (รันนิงต่อเดือน)
#  - คำนวณวันสิ้นสุด, แจ้งเตือน ≤30/≤15 วัน
#  - ค้นหาได้จาก: หมายเลขสัญญา/ชื่อร้าน/ผู้ติดต่อ/เบอร์
#  - หน้า "ค้นหา" ติ๊กยกเลิก/คืนค่า สัญญาได้ แล้วกดบันทึกการเปลี่ยนแปลง
#  - หน้า "ข้อมูลทั้งหมด" แสดงทุกสัญญา รวมถึงที่ยกเลิก
#  - ดาวน์โหลด Excel/CSV
#  - แก้บั๊กการค้นหาด้วยเบอร์โทร (บังคับ dtype เป็น string)
# หมายเหตุ: ปุ่มดาวน์โหลด Excel ใช้ engine=xlsxwriter ⇒ เพิ่ม "xlsxwriter" ใน requirements.txt

from datetime import date
from io import BytesIO
import re

import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta

import gspread
from gspread_dataframe import set_with_dataframe

# ลำดับคอลัมน์หลักในชีต
COLUMNS = [
    "id",            # เลขลำดับภายใน (เพิ่มเอง)
    "contract_no",   # หมายเลขสัญญา YYYYMM-XXX (อัตโนมัติ)
    "shop_name",
    "contact_name",
    "phone",
    "start_date",
    "months",
    "end_date",
    "cancelled",     # True/False
]

# ---------------------- Google Sheets ----------------------
def _connect_ws():
    """เชื่อม Google Sheets จากค่าใน st.secrets['gsheets'] และคืน worksheet ที่พร้อมใช้งาน"""
    cfg = st.secrets["gsheets"]

    creds_dict = {
        "type": cfg["type"],
        "project_id": cfg["project_id"],
        "private_key_id": cfg["private_key_id"],
        # รองรับทั้งคีย์บรรทัดเดียวที่มี \\n และแบบหลายบรรทัดจริง
        "private_key": str(cfg["private_key"]).replace("\\n", "\n"),
        "client_email": cfg["client_email"],
        "client_id": cfg["client_id"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": cfg.get("client_x509_cert_url", ""),
        "universe_domain": "googleapis.com",
    }
    gc = gspread.service_account_from_dict(creds_dict)

    # รองรับทั้ง URL และ ID
    sheet_url = cfg.get("sheet_url", "")
    sheet_id = cfg.get("sheet_id", "")
    try:
        if sheet_url:
            sh = gc.open_by_url(sheet_url)
        elif sheet_id:
            sh = gc.open_by_key(sheet_id)
        else:
            raise ValueError("กรุณาใส่ gsheets.sheet_url หรือ gsheets.sheet_id ใน Secrets")
    except gspread.exceptions.NoValidUrlKeyFound:
        if sheet_id:
            sh = gc.open_by_key(sheet_id)
        else:
            raise

    title = cfg.get("worksheet", "leases")
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows="1000", cols="20")
        ws.append_row(COLUMNS)

    # ไม่ clear ชีตที่มีอยู่ เพื่อลดความเสี่ยงข้อมูลหาย (จะเขียนทับเมื่อ save เท่านั้น)
    return ws

@st.cache_data(ttl=5)
def load_data():
    ws = _connect_ws()
    rows = ws.get_all_records()  # ใช้แถวแรกเป็น header
    df = pd.DataFrame(rows)

    # เติมคอลัมน์ที่อาจขาด และตั้งค่าเริ่มต้น
    for c in COLUMNS:
        if c not in df.columns:
            if c == "cancelled":
                df[c] = False
            else:
                df[c] = pd.NA

    # แปลงชนิดข้อมูลสำคัญ
    for col in ["start_date", "end_date"]:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    if not df.empty:
        df["id"] = pd.to_numeric(df["id"], errors="coerce")
        df["months"] = pd.to_numeric(df["months"], errors="coerce")

    # บังคับคอลัมน์ข้อความให้เป็น string (กัน .str error)
    for c in ["contract_no", "shop_name", "contact_name", "phone"]:
        df[c] = df[c].astype("string")

    # cancelled: map เป็น bool
    df["cancelled"] = df["cancelled"].map(
        lambda v: str(v).strip().lower() in {"true","1","yes","y","t","ใช่","ยกเลิก","cancel","cancelled"}
    )

    if not df.empty:
        df = df.sort_values(by=["end_date", "id"], ascending=[True, True]).reset_index(drop=True)
    return df


def save_data(df: pd.DataFrame):
    ws = _connect_ws()
    out = df.copy()
    # แปลงวันที่เป็นสตริง ISO
    for c in ["start_date", "end_date"]:
        out[c] = out[c].apply(lambda d: d.isoformat() if pd.notna(d) else "")
    # แปลง cancelled เป็น True/False
    out["cancelled"] = out["cancelled"].astype(bool)
    # เขียนทับทั้งชีตพร้อมหัวตารางตาม COLUMNS
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


def _contains(col: pd.Series, q: str) -> pd.Series:
    """helper: ค้นหาแบบไม่สนตัวพิมพ์ + กันค่า NA + บังคับเป็น string ก่อน"""
    return col.astype("string").str.contains(q, case=False, na=False)


def filter_by_query(df: pd.DataFrame, q: str) -> pd.DataFrame:
    if not q:
        return df
    q = q.strip()
    mask = (
        _contains(df["contract_no"], q)
        | _contains(df["shop_name"], q)
        | _contains(df["contact_name"], q)
        | _contains(df["phone"], q)
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

    # ถ้ามองหาใบที่จะหมดอายุ ให้ตัดสัญญาที่ถูกยกเลิกออก
    if within_days is not None:
        tmp = tmp[(~tmp["cancelled"]) & (tmp["days_left"].notna()) & (tmp["days_left"] >= 0) & (tmp["days_left"] <= within_days)]

    if start is not None:
        tmp = tmp[tmp["end_date"].apply(lambda d: pd.notna(d) and d >= start)]

    if end is not None:
        tmp = tmp[tmp["end_date"].apply(lambda d: pd.notna(d) and d <= end)]

    return tmp


def style_status(days_left: int | None, cancelled: bool) -> str:
    if cancelled:
        return "❌ ยกเลิกแล้ว"
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
        with pd.ExcelWriter(bio, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="filtered")
        bio.seek(0)
        return bio.read()
    return df.to_csv(index=False).encode("utf-8-sig")


def next_contract_no(df: pd.DataFrame, start: date) -> str:
    """สร้างหมายเลขสัญญาแบบ YYYYMM-XXX โดยนับรันภายในเดือนของ start_date"""
    yyyymm = f"{start:%Y%m}"
    # ดึงเลขลำดับที่มีอยู่ในเดือนนั้น ๆ
    seqs = []
    if not df.empty and "contract_no" in df.columns:
        pat = re.compile(rf"^{yyyymm}-(\d+)$")
        for v in df["contract_no"].fillna(""):
            m = pat.match(str(v))
            if m:
                try:
                    seqs.append(int(m.group(1)))
                except ValueError:
                    pass
    next_seq = (max(seqs) + 1) if seqs else 1
    return f"{yyyymm}-{next_seq:03d}"

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
            start_date = st.date_input("วันเริ่มสัญญา *", value=date.today())
            # หมายเลขสัญญาอัตโนมัติ (อ้างอิงเดือนของวันเริ่มสัญญา)
            contract_no_preview = next_contract_no(_df, start_date)
            st.text_input("หมายเลขสัญญา (อัตโนมัติ)", value=contract_no_preview, disabled=True)
            shop_name = st.text_input("ชื่อร้านค้า *")
            contact_name = st.text_input("ชื่อผู้ติดต่อ *")
        with c2:
            months = st.number_input("ระยะเวลาเช่า (เดือน) *", min_value=1, max_value=240, value=12, step=1)
            end_date = calc_end_date(start_date, months)
            st.info(f"วันสิ้นสุดสัญญา: **{end_date.strftime('%Y-%m-%d')}**")
            phone = st.text_input("เบอร์โทรศัพท์ *")

        if st.form_submit_button("บันทึกสัญญา"):
            if not all([shop_name.strip(), contact_name.strip(), phone.strip(), start_date, months]):
                st.error("กรุณากรอกข้อมูลให้ครบ")
            else:
                rec = {
                    "id": next_id(_df),
                    "contract_no": next_contract_no(_df, start_date),
                    "shop_name": shop_name.strip(),
                    "contact_name": contact_name.strip(),
                    "phone": phone.strip(),
                    "start_date": start_date,
                    "months": int(months),
                    "end_date": end_date,
                    "cancelled": False,
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
            q = st.text_input("ค้นหาจากเลขสัญญา/ชื่อร้าน/ผู้ติดต่อ/เบอร์")
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

    # ค้นหา (รวมสัญญาที่ยกเลิกด้วย)
    df_q = filter_by_query(_df, q)
    # กรองช่วงหมดอายุ (จะไม่นับสัญญาที่ยกเลิกในกลุ่มแจ้งเตือน)
    df_f = filter_by_expiry_window(
        df_q,
        within_days=within_days,
        start=start if start else None,
        end=end if end else None,
    ) if within_days or start or end else df_q.copy()

    # สถานะ
    df_f["days_left"] = df_f["end_date"].apply(lambda d: days_until(d) if pd.notna(d) else None)
    df_f["สถานะ"] = df_f.apply(lambda r: style_status(r["days_left"], r["cancelled"]), axis=1)

    # สรุปแจ้งเตือนจากข้อมูลทั้งหมด (ไม่นับใบที่ยกเลิก)
    df_30 = filter_by_expiry_window(_df, within_days=30)
    df_15 = filter_by_expiry_window(_df, within_days=15)
    if not df_15.empty:
        st.error(f"มี {len(df_15)} สัญญาใกล้หมดภายใน 15 วัน")
    elif not df_30.empty:
        st.warning(f"มี {len(df_30)} สัญญาจะหมดภายใน 30 วัน")
    else:
        st.success("ยังไม่มีสัญญาที่จะหมดภายใน 30 วัน")

    st.markdown("### ผลการค้นหา / ปรับสถานะ")
    if df_f.empty:
        st.info("ไม่พบข้อมูลตามเงื่อนไข")
    else:
        show_cols = ["id","contract_no","shop_name","contact_name","phone","start_date","months","end_date","cancelled","สถานะ"]
        edited = st.data_editor(
            df_f[show_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "cancelled": st.column_config.CheckboxColumn(
                    "ยกเลิกสัญญา",
                    help="ติ๊กเพื่อยกเลิก/คืนค่า แล้วกดปุ่มบันทึกการเปลี่ยนแปลงด้านล่าง",
                )
            },
            disabled=[c for c in show_cols if c != "cancelled" and c != "สถานะ"],
            key="editor_search",
        )
        if st.button("บันทึกการเปลี่ยนแปลงสถานะยกเลิก/คืนค่า"):
            # อัปเดตกลับเข้า _df ตาม id
            merged = _df.set_index("id").copy()
            for _id, is_cancel in zip(edited["id"].tolist(), edited["cancelled"].tolist()):
                merged.at[_id, "cancelled"] = bool(is_cancel)
            save_data(merged.reset_index())
            st.cache_data.clear()
            st.success("อัปเดตสถานะเรียบร้อย")
            st.rerun()

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
    st.subheader("ข้อมูลทั้งหมด (รวมใบที่ยกเลิก)")

    if _df.empty:
        st.info("ยังไม่มีข้อมูล")
    else:
        dfv = _df.copy()
        dfv["days_left"] = dfv["end_date"].apply(lambda d: days_until(d) if pd.notna(d) else None)
        dfv["สถานะ"] = dfv.apply(lambda r: style_status(r["days_left"], r["cancelled"]), axis=1)
        st.dataframe(
            dfv[["id","contract_no","shop_name","contact_name","phone","start_date","months","end_date","cancelled","สถานะ"]],
            use_container_width=True,
        )

