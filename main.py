import os
import mysql.connector
from mysql.connector import pooling
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from datetime import date
from dotenv import load_dotenv

# Memuat konfigurasi dari file .env jika ada
load_dotenv()

app = FastAPI(
    title="Sistem Informasi Kas RW - Keuangan Transparan",
    description="Backend API untuk pengelolaan kas warga yang aman dan akuntabel.",
    version="1.0.0"
)

# Mengaktifkan CORS agar Frontend dapat berkomunikasi dengan Backend lintas origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://3.233.82.68",       
        "http://3.233.82.68:80",
        "http://kasrw3.org",
        "https://kasrw3.org",
        "http://localhost",
        "http://localhost:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Konfigurasi Database Pooling (Membaca dari file .env)
db_config = {
    "pool_name": "kas_rw_pool",
    "pool_size": 5,
    "host": os.getenv("DB_HOST", "localhost"),       # Di dalam Docker, arahkan ke IP Host / IP MySQL Anda
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "@password123"),  
    "database": os.getenv("DB_NAME", "kas_rw_db")
}

try:
    connection_pool = pooling.MySQLConnectionPool(**db_config)
    print("Database Connection Pool berhasil diinisialisasi.")
except mysql.connector.Error as err:
    print(f"Error Database Connection Pool: {err}")
    connection_pool = None

def get_db():
    """Dependency untuk mendapatkan koneksi database dari pool."""
    if connection_pool is None:
        raise HTTPException(status_code=500, detail="Koneksi database tidak tersedia.")
    connection = connection_pool.get_connection()
    try:
        yield connection
    finally:
        connection.close()

# --- MODEL DATA (Pydantic) ---

class LoginRequest(BaseModel):
    username: str
    password: str
    role: str

class LoginResponse(BaseModel):
    success: bool
    username: str
    role: str
    message: str

class TransaksiCreate(BaseModel):
    tanggal: date
    jenis: str = Field(..., description="Harus 'pemasukan' atau 'pengeluaran'")
    jumlah: float = Field(..., gt=0, description="Jumlah nominal harus lebih dari 0")
    keterangan: str = Field(..., min_length=15, description="Keterangan wajib detail minimal 15 karakter untuk mencegah markup")
    username_admin: str

class TransaksiUpdate(BaseModel):
    tanggal: date
    jumlah: float = Field(..., gt=0)
    keterangan: str = Field(..., min_length=15)

# --- ENDPOINTS API ---

@app.post("/api/login", response_model=LoginResponse)
def login(payload: LoginRequest, db=Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    query = "SELECT username, role FROM users WHERE username = %s AND password = %s AND role = %s"
    cursor.execute(query, (payload.username, payload.password, payload.role))
    user = cursor.fetchone()
    cursor.close()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kredensial salah atau Role tidak sesuai. Akses ditolak."
        )
    
    return {
        "success": True,
        "username": user["username"],
        "role": user["role"],
        "message": f"Selamat datang {user['username']} sebagai {user['role']}"
    }

@app.get("/api/transaksi")
def get_all_transaksi(db=Depends(get_db)):
    cursor = db.cursor(dictionary=True)
    
    # Ambil riwayat urut dari yang terbaru
    query_history = "SELECT id, DATE_FORMAT(tanggal, '%Y-%m-%d') as tanggal, jenis, jumlah, keterangan, username_admin FROM transaksi ORDER BY tanggal DESC, id DESC"
    cursor.execute(query_history)
    riwayat = cursor.fetchall()
    
    # Hitung Rekapitulasi Ringkasan Keuangan
    query_summary = """
        SELECT 
            COALESCE(SUM(CASE WHEN jenis = 'pemasukan' THEN jumlah ELSE 0 END), 0) as total_pemasukan,
            COALESCE(SUM(CASE WHEN jenis = 'pengeluaran' THEN jumlah ELSE 0 END), 0) as total_pengeluaran
        FROM transaksi
    """
    cursor.execute(query_summary)
    summary = cursor.fetchone()
    cursor.close()
    
    total_pemasukan = float(summary["total_pemasukan"])
    total_pengeluaran = float(summary["total_pengeluaran"])
    saldo_berjalan = total_pemasukan - total_pengeluaran
    
    return {
        "saldo_berjalan": saldo_berjalan,
        "total_pemasukan": total_pemasukan,
        "total_pengeluaran": total_pengeluaran,
        "data": riwayat
    }

@app.post("/api/transaksi", status_code=status.HTTP_201_CREATED)
def create_transaksi(payload: TransaksiCreate, db=Depends(get_db)):
    if payload.jenis not in ['pemasukan', 'pengeluaran']:
        raise HTTPException(status_code=400, detail="Jenis transaksi tidak valid.")
        
    cursor = db.cursor()
    query = """
        INSERT INTO transaksi (tanggal, jenis, jumlah, keterangan, username_admin)
        VALUES (%s, %s, %s, %s, %s)
    """
    try:
        cursor.execute(query, (payload.tanggal, payload.jenis, payload.jumlah, payload.keterangan, payload.username_admin))
        db.commit()
        cursor.close()
        return {"success": True, "message": "Data transaksi berhasil disimpan secara transparan."}
    except mysql.connector.Error as err:
        db.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan data: {err}")

@app.put("/api/transaksi/{transaksi_id}")
def update_transaksi(transaksi_id: int, payload: TransaksiUpdate, db=Depends(get_db)):
    cursor = db.cursor()
    # Pastikan data yang akan diupdate ada di database
    check_query = "SELECT id FROM transaksi WHERE id = %s"
    cursor.execute(check_query, (transaksi_id,))
    if not cursor.fetchone():
        cursor.close()
        raise HTTPException(status_code=404, detail="Data transaksi tidak ditemukan.")
        
    update_query = """
        UPDATE transaksi 
        SET tanggal = %s, jumlah = %s, keterangan = %s 
        WHERE id = %s
    """
    try:
        cursor.execute(update_query, (payload.tanggal, payload.jumlah, payload.keterangan, transaksi_id))
        db.commit()
        cursor.close()
        return {"success": True, "message": "Data transaksi berhasil diperbarui."}
    except mysql.connector.Error as err:
        db.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Gagal memperbarui data: {err}")

@app.delete("/api/transaksi/{transaksi_id}")
def delete_transaksi(transaksi_id: int, db=Depends(get_db)):
    cursor = db.cursor()
    check_query = "SELECT id FROM transaksi WHERE id = %s"
    cursor.execute(check_query, (transaksi_id,))
    if not cursor.fetchone():
        cursor.close()
        raise HTTPException(status_code=404, detail="Data transaksi tidak ditemukan.")
        
    delete_query = "DELETE FROM transaksi WHERE id = %s"
    try:
        cursor.execute(delete_query, (transaksi_id,))
        db.commit()
        cursor.close()
        return {"success": True, "message": "Data transaksi berhasil dihapus dari sistem."}
    except mysql.connector.Error as err:
        db.rollback()
        cursor.close()
        raise HTTPException(status_code=500, detail=f"Gagal menghapus data: {err}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)