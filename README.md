# ระบบจัดการสัญญาร้านเช่า (Streamlit + Excel)

แอปสำหรับบันทึก/ค้นหา/แจ้งเตือนสัญญาเช่าร้านค้า เก็บลงไฟล์ Excel

## วิธีรันบนเครื่อง
```bash
# แนะนำให้ใช้ Python 3.10+
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
python -m streamlit run app.py
