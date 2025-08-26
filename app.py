# app.py
# ------------------------------------------------------------
# ระบบจัดการสัญญาร้านเช่า (บันทึกลงไฟล์ Excel)
# เทคโนโลยี: Streamlit + pandas + openpyxl + python-dateutil
# คุณสมบัติ:
#  - บันทึก: ชื่อร้านค้า, ผู้ติดต่อ, เบอร์โทร, วันเริ่มสัญญา, ระยะเวลา (เดือน)
#    -> คำนวณวันสิ้นสุดสัญญาอัตโนมัติ
#  - แจ้งเตือนล่วงหน้า 30 และ 15 วัน (หน้า "ค้นหา/แจ้งเตือน")
#  - ค้นหา/กรองตามชื่อ และช่วงวันหมดสัญญา
#  - ดู/แก้ไขข้อมูลแบบตาราง แล้วบันทึกกลับ Excel
#  - ดาวน์โหลดผลการค้นหาเป็น Excel/CSV
# ------------------------------------------------------------

import os
from datetime import date
from io import BytesIO

import pandas as pd
from dateutil.relativedelta import relativedelta
import streamlit as st

# --------------------- การตั้งค่าไฟล์ ------------------------
DATA_DIR = "data"
EXCEL_PATH = os.path.join(DATA_DIR, "leases.xlsx")
SHEET_NAME = "leases"

COLUMNS = [
    "id",           # running id
    "shop_name",
    "contact_name",
    "phone",
    "start_date",
    "months",
    "end_date",
]

# -------------------- ฟังก์ชันช่วยเหลือ ---------------------
def ensure_storage():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(EXCEL_PATH):
        df = pd.DataFrame(columns=COLUMNS)
        with pd.ExcelWriter(EXCEL_PATH, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=SHEET_NAME)

@st.cache_data(ttl=5)
def load_data():
    ensure_storage()
    try:
        df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME, dtype={"phone": str})
    except Exception:
        df = pd.DataFrame(columns=COLUMNS)

    # แปลงวันที่ให้เป็น datetime.date
    for col in ["start_date", "end_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date

    # เติมคอลัมน์ที่ขาด
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA

    if not df.empty:
        df = df.sort_values(by=["end_date", "id"], ascending=[True, True])

    return df.reset_index(drop=True)

def save_data(df: pd.DataFrame):
    ensure_storage()
    out = df.copy()
    with pd.ExcelWriter(EXCEL_PATH, engine="openpyxl") as writer:
        out.to_excel(writer, index=False, sheet_name=SHEET_NAME)

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
    temp = df.copy()
    temp["days_left"] = temp["end_date"].apply(lambda d: days_until(d) if pd.notna(d) else None)

    if within_days is not None:
        temp = temp[
            (temp["days_left"].notna())
            & (temp["days_left"] >= 0)
            & (temp["days_left"] <= within_days)
        ]

    if start is not None:
        temp = temp[temp["end_date"].apply(lambda d: pd.notna(d) and d >= start)]

    if end is not None:
        temp = temp[temp["end_date"].apply(lambda d: pd.notna(d) and d <= end)]

    return temp

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
        with pd.ExcelWriter(bio, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="filtered")
        bio.seek(0)
        return bio.read()
    else:
        return df.to_csv(index=False).encode("utf-8-sig")

# ------------------------ UI หลัก ---------------------------
st.set_page_config(page_title="สัญญาร้านเช่า - Excel", page_icon="📑", layout="wide")
st.title("📑 ระบบจัดการสัญญาร้านเช่า (บันทึกลง Excel)")

with st.sidebar:
    st.header("เมนู")
    page = st.radio("ไปที่หน้า:", ["➕ เพิ่มสัญญา", "🔎 ค้นหา/แจ้งเตือน", "📋 ข้อมูลทั้งหมด"], index=0)
    st.markdown("—")
    st.caption("บันทึกข้อมูลอยู่ใน: `data/leases.xlsx`")

# โหลดข้อมูล
_df = load_data()

# ---------------------- หน้า: เพิ่มสัญญา --------------------
if page.startswith("➕"):
    st.subheader("เพิ่ม/บันทึกสัญญาใหม่")
    with st.form("add_form", clear_on_submit=True):
        col1, col2 = st.columns([2, 2])
        with col1:
            shop_name = st.text_input("ชื่อร้านค้า *")
            contact_name = st.text_input("ชื่อผู้ติดต่อ *")
            phone = st.text_input("เบอร์โทรศัพท์ *", help="ตัวอย่าง: 0812345678")
        with col2:
            start_date = st.date_input("วันเริ่มสัญญา *", value=date.today())
            months = st.number_input("ระยะเวลาเช่า (เดือน) *", min_value=1, max_value=240, value=12, step=1)
            end_date = calc_end_date(start_date, months)
            st.info(f"วันสิ้นสุดสัญญาโดยอัตโนมัติ: **{end_date.strftime('%Y-%m-%d')}**")

        submitted = st.form_submit_button("บันทึกสัญญา")

        if submitted:
            if not all([shop_name.strip(), contact_name.strip(), phone.strip(), start_date, months]):
                st.error("กรุณากรอกข้อมูลที่มีเครื่องหมาย * ให้ครบถ้วนครับ")
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
                st.success("บันทึกสำเร็จ! เพิ่มสัญญาใหม่เรียบร้อย")
                st.rerun()

# ------------------- หน้า: ค้นหา/แจ้งเตือน ------------------
elif page.startswith("🔎"):
    st.subheader("ค้นหา & แจ้งเตือนวันหมดสัญญา")

    # แถบกรอง
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

    # ✅ สร้างคอลัมน์สถานะเสมอ แม้ผลลัพธ์ว่าง (แก้ KeyError)
    df_f = df_f.copy()
    df_f["days_left"] = df_f["end_date"].apply(lambda d: days_until(d) if pd.notna(d) else None)
    df_f["สถานะ"] = df_f["days_left"].apply(style_status)

    # แถบแจ้งเตือนรวม (จากข้อมูลทั้งหมด)
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
        st.info("ไม่พบข้อมูลตามเงื่อนไขที่กำหนด ลองเปลี่ยนตัวกรองหรือเพิ่มสัญญาใหม่ก่อนครับ")
    else:
        st.dataframe(
            df_f[["id", "shop_name", "contact_name", "phone", "start_date", "months", "end_date", "สถานะ"]],
            use_container_width=True,
        )

    # ปุ่มดาวน์โหลด
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button(
            "ดาวน์โหลดเป็น Excel (.xlsx)",
            data=to_download_bytes(df_f, as_excel=True),
            file_name="leases_filtered.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col_dl2:
        st.download_button(
            "ดาวน์โหลดเป็น CSV",
            data=to_download_bytes(df_f, as_excel=False),
            file_name="leases_filtered.csv",
            mime="text/csv",
        )

# -------------------- หน้า: ข้อมูลทั้งหมด -------------------
else:
    st.subheader("ข้อมูลทั้งหมด")

    if _df.empty:
        st.info("ยังไม่มีข้อมูล ลองไปที่หน้า 'เพิ่มสัญญา' เพื่อบันทึกข้อมูลแรกครับ")
    else:
        df_view = _df.copy()
        df_view["days_left"] = df_view["end_date"].apply(lambda d: days_until(d) if pd.notna(d) else None)
        df_view["สถานะ"] = df_view["days_left"].apply(style_status)

        st.dataframe(
            df_view[["id", "shop_name", "contact_name", "phone", "start_date", "months", "end_date", "สถานะ"]],
            use_container_width=True,
        )

        st.markdown("#### แก้ไขแบบตาราง")
        st.caption("แก้ค่าตามต้องการ แล้วกดปุ่ม 'บันทึกการแก้ไข' ด้านล่าง")

        edit_cols = ["id", "shop_name", "contact_name", "phone", "start_date", "months", "end_date"]
        editable = st.data_editor(
            _df[edit_cols],
            use_container_width=True,
            num_rows="dynamic",
            key="editor",
        )
        if st.button("บันทึกการแก้ไข"):
            try:
                editable["id"] = pd.to_numeric(editable["id"], errors="coerce").astype("Int64")
                editable["months"] = pd.to_numeric(editable["months"], errors="coerce").astype("Int64")
                for c in ["start_date", "end_date"]:
                    editable[c] = pd.to_datetime(editable[c], errors="coerce").dt.date

                # คำนวณ end_date อัตโนมัติหาก start+months มีค่าและ end_date ว่าง
                mask_need_end = (
                    editable["end_date"].isna()
                    & editable["start_date"].notna()
                    & editable["months"].notna()
                )
                editable.loc[mask_need_end, "end_date"] = editable.loc[mask_need_end].apply(
                    lambda r: calc_end_date(r["start_date"], int(r["months"]))
                    if pd.notna(r["start_date"]) and pd.notna(r["months"])
                    else pd.NaT,
                    axis=1,
                )

                # ให้แน่ใจว่ามีทุกคอลัมน์และเรียงตาม COLUMNS
                for c in COLUMNS:
                    if c not in editable.columns:
                        editable[c] = pd.NA
                editable = editable[COLUMNS]

                save_data(editable)
                st.success("บันทึกการแก้ไขเรียบร้อย")
                st.rerun()
            except Exception as e:
                st.exception(e)
# -------------------------- จบไฟล์ ---------------------------
