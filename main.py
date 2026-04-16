from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import sqlite3
import os

app = FastAPI()

# 允許所有來源（Quackai 才能呼叫）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "nora.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            user_id TEXT PRIMARY KEY,
            last_seen TEXT,
            hunger INTEGER DEFAULT 30,
            energy INTEGER DEFAULT 80,
            mood INTEGER DEFAULT 65,
            loneliness INTEGER DEFAULT 20,
            affection INTEGER DEFAULT 0,
            desire INTEGER DEFAULT 20,
            negative INTEGER DEFAULT 10,
            mystery INTEGER DEFAULT 15,
            broken INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ── 計算離線時間 ──
def calc_absence(last_seen_str):
    if not last_seen_str:
        return {"duration_min": 0, "display": "初次到訪", "tier": 0}
    
    last = datetime.fromisoformat(last_seen_str)
    now = datetime.utcnow()
    diff_min = int((now - last).total_seconds() / 60)
    
    if diff_min < 3:
        tier = 0
        display = f"{diff_min}分鐘" if diff_min > 0 else "剛剛"
    elif diff_min < 30:
        tier = 1
        display = f"{diff_min}分鐘"
    elif diff_min < 480:
        tier = 2
        h = diff_min // 60
        m = diff_min % 60
        display = f"{h}小時{m}分鐘" if h > 0 else f"{diff_min}分鐘"
    elif diff_min < 1440:
        tier = 3
        h = diff_min // 60
        display = f"{h}小時"
    else:
        tier = 4
        d = diff_min // 1440
        h = (diff_min % 1440) // 60
        display = f"{d}天{h}小時" if h > 0 else f"{d}天"
    
    return {"duration_min": diff_min, "display": display, "tier": tier}

# ── 取得狀態 ──
@app.get("/status/{user_id}")
def get_status(user_id: str):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    
    now_str = datetime.utcnow().isoformat()
    
    if not row:
        return {
            "user_id": user_id,
            "now": now_str,
            "absence": {"duration_min": 0, "display": "初次到訪", "tier": 0},
            "stats": {
                "hunger": 30, "energy": 80, "mood": 65,
                "loneliness": 20, "affection": 0, "desire": 20,
                "negative": 10, "mystery": 15
            },
            "broken": False,
            "first_visit": True
        }
    
    absence = calc_absence(row["last_seen"])
    
    return {
        "user_id": user_id,
        "now": now_str,
        "absence": absence,
        "stats": {
            "hunger": row["hunger"],
            "energy": row["energy"],
            "mood": row["mood"],
            "loneliness": row["loneliness"],
            "affection": row["affection"],
            "desire": row["desire"],
            "negative": row["negative"],
            "mystery": row["mystery"]
        },
        "broken": bool(row["broken"]),
        "first_visit": False
    }

# ── 更新時間（用戶發訊息時呼叫）──
@app.post("/ping/{user_id}")
def ping(user_id: str):
    conn = get_db()
    now_str = datetime.utcnow().isoformat()
    
    row = conn.execute(
        "SELECT * FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchone()
    
    absence = calc_absence(row["last_seen"] if row else None)
    
    if row:
        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE user_id = ?",
            (now_str, user_id)
        )
    else:
        conn.execute(
            """INSERT INTO sessions 
               (user_id, last_seen, created_at) 
               VALUES (?, ?, ?)""",
            (user_id, now_str, now_str)
        )
    
    conn.commit()
    conn.close()
    
    return {"now": now_str, "absence": absence}

# ── 更新數值 ──
class StatsUpdate(BaseModel):
    hunger: int = None
    energy: int = None
    mood: int = None
    loneliness: int = None
    affection: int = None
    desire: int = None
    negative: int = None
    mystery: int = None
    broken: bool = None

@app.post("/stats/{user_id}")
def update_stats(user_id: str, data: StatsUpdate):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    
    fields = []
    values = []
    
    for field in ["hunger", "energy", "mood", "loneliness", 
                  "affection", "desire", "negative", "mystery"]:
        val = getattr(data, field)
        if val is not None:
            # 夾住 0-100（affection 0-200）
            max_val = 200 if field == "affection" else 100
            val = max(0, min(max_val, val))
            fields.append(f"{field} = ?")
            values.append(val)
    
    if data.broken is not None:
        fields.append("broken = ?")
        values.append(1 if data.broken else 0)
    
    if fields:
        values.append(user_id)
        conn.execute(
            f"UPDATE sessions SET {', '.join(fields)} WHERE user_id = ?",
            values
        )
        conn.commit()
    
    conn.close()
    return {"ok": True}

# ── 健康檢查 ──
@app.get("/")
def root():
    return {"status": "Nora API is running"}
