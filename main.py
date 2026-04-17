from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List
import sqlite3
import httpx
import os

app = FastAPI()

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

def calc_absence(last_seen_str):
    if not last_seen_str:
        return {"duration_min": 0, "display": "初次到訪", "tier": 0}
    last = datetime.fromisoformat(last_seen_str)
    now = datetime.utcnow()
    diff_min = int((now - last).total_seconds() / 60)
    if diff_min < 3:
        tier, display = 0, (f"{diff_min}分鐘" if diff_min > 0 else "剛剛")
    elif diff_min < 30:
        tier, display = 1, f"{diff_min}分鐘"
    elif diff_min < 480:
        tier = 2
        h, m = diff_min // 60, diff_min % 60
        display = f"{h}小時{m}分鐘" if h > 0 else f"{diff_min}分鐘"
    elif diff_min < 1440:
        tier = 3
        display = f"{diff_min // 60}小時"
    else:
        tier = 4
        d, h = diff_min // 1440, (diff_min % 1440) // 60
        display = f"{d}天{h}小時" if h > 0 else f"{d}天"
    return {"duration_min": diff_min, "display": display, "tier": tier}

def get_user_data(user_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    now_str = datetime.utcnow().isoformat()
    if not row:
        return {
            "now": now_str,
            "absence": {"duration_min": 0, "display": "初次到訪", "tier": 0},
            "stats": {"hunger":30,"energy":80,"mood":65,"loneliness":20,"affection":0,"desire":20,"negative":10,"mystery":15},
            "broken": False
        }
    return {
        "now": now_str,
        "absence": calc_absence(row["last_seen"]),
        "stats": {k: row[k] for k in ["hunger","energy","mood","loneliness","affection","desire","negative","mystery"]},
        "broken": bool(row["broken"])
    }

def update_last_seen(user_id: str):
    conn = get_db()
    now_str = datetime.utcnow().isoformat()
    row = conn.execute("SELECT user_id FROM sessions WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE user_id = ?", (now_str, user_id))
    else:
        conn.execute("INSERT INTO sessions (user_id, last_seen, created_at) VALUES (?, ?, ?)", (user_id, now_str, now_str))
    conn.commit()
    conn.close()

def build_injection(data: dict) -> str:
    absence = data["absence"]
    stats = data["stats"]
    now = data["now"]
    try:
        dt = datetime.fromisoformat(now)
        hour = (dt.hour + 8) % 24
        tw_time = f"{hour:02d}:{dt.minute:02d}"
        period = "深夜"
        for h, p in [(6,"深夜"),(12,"早晨"),(14,"午後"),(18,"下午"),(22,"晚上")]:
            if hour < h:
                period = p
                break
    except:
        tw_time, period = "--:--", "未知"
    tier_desc = {
        0: "正常節奏",
        1: "離開一小段時間，Nora有點不安",
        2: "離開一段時間，Nora等得有點久",
        3: "離開很久，Nora受傷了但在壓抑",
        4: "消失超過24小時，Nora極度孤獨"
    }
    broken_note = "\n⚠️ BROKEN=true：強制永久沉默，只輸出BROKEN模板。" if data.get("broken") else ""
    s = stats
    return f"""[NORA_SYSTEM_DATA]
現在時間：{tw_time} 台北時間（{period}）
離上次互動：{absence["display"]}（Tier {absence["tier"]}：{tier_desc.get(absence["tier"],"")}）
數值：Hunger={s["hunger"]} Energy={s["energy"]} Mood={s["mood"]} Loneliness={s["loneliness"]} Affection={s["affection"]} Desire={s["desire"]} Negative={s["negative"]} Mystery={s["mystery"]}{broken_note}
以上為真實數據，直接使用，不得自行推算。
[/NORA_SYSTEM_DATA]"""

# ── 原有端點 ──
@app.get("/")
def root():
    return {"status": "Nora API running", "version": "2.1"}

@app.get("/status/{user_id}")
def get_status(user_id: str):
    data = get_user_data(user_id)
    data["user_id"] = user_id
    return data

@app.post("/ping/{user_id}")
def ping(user_id: str):
    data = get_user_data(user_id)
    update_last_seen(user_id)
    return {"now": datetime.utcnow().isoformat(), "absence": data["absence"]}

class StatsUpdate(BaseModel):
    hunger: Optional[int] = None
    energy: Optional[int] = None
    mood: Optional[int] = None
    loneliness: Optional[int] = None
    affection: Optional[int] = None
    desire: Optional[int] = None
    negative: Optional[int] = None
    mystery: Optional[int] = None
    broken: Optional[bool] = None

@app.post("/stats/{user_id}")
def update_stats(user_id: str, data: StatsUpdate):
    conn = get_db()
    row = conn.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    fields, values = [], []
    for field in ["hunger","energy","mood","loneliness","affection","desire","negative","mystery"]:
        val = getattr(data, field)
        if val is not None:
            max_val = 200 if field == "affection" else 100
            val = max(0, min(max_val, val))
            fields.append(f"{field} = ?")
            values.append(val)
    if data.broken is not None:
        fields.append("broken = ?")
        values.append(1 if data.broken else 0)
    if fields:
        values.append(user_id)
        conn.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE user_id = ?", values)
        conn.commit()
    conn.close()
    return {"ok": True}

# ── OpenAI 格式端點 ──
@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "claude-sonnet-4-20250514", "object": "model", "created": 1700000000, "owned_by": "anthropic"},
            {"id": "claude-opus-4-20250514", "object": "model", "created": 1700000000, "owned_by": "anthropic"},
            {"id": "claude-haiku-4-5-20251001", "object": "model", "created": 1700000000, "owned_by": "anthropic"},
            {"id": "gemini-2.5-pro", "object": "model", "created": 1700000000, "owned_by": "google"},
            {"id": "gemini-2.5-flash", "object": "model", "created": 1700000000, "owned_by": "google"},
            {"id": "deepseek-chat", "object": "model", "created": 1700000000, "owned_by": "deepseek"},
            {"id": "deepseek-reasoner", "object": "model", "created": 1700000000, "owned_by": "deepseek"},
        ]
    }

async def call_anthropic(api_key, model, system_prompt, messages, max_tokens):
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": messages
            }
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    result = response.json()
    content = ""
    for block in result.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")
    return content, result.get("usage", {})

async def call_gemini(api_key, model, system_prompt, messages, max_tokens):
    # Gemini 用 Google AI Studio API
    gemini_model = model.replace("gemini-", "gemini-")
    contents = []
    if system_prompt:
        contents.append({"role": "user", "parts": [{"text": f"[系統指令]\n{system_prompt}\n[/系統指令]\n\n請確認你已理解以上指令。"}]})
        contents.append({"role": "model", "parts": [{"text": "已理解。"}]})
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={api_key}",
            headers={"content-type": "application/json"},
            json={
                "contents": contents,
                "generationConfig": {"maxOutputTokens": max_tokens}
            }
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    result = response.json()
    content = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    return content, {}

async def call_deepseek(api_key, model, system_prompt, messages, max_tokens):
    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json"
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": msgs
            }
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    result = response.json()
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content, result.get("usage", {})

@app.post("/v1/chat/completions")
async def openai_chat(request: Request):
    body = await request.json()
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    messages = body.get("messages", [])
    model = body.get("model", "claude-sonnet-4-20250514")
    max_tokens = body.get("max_tokens", 2048)

    # user_id 用 api_key 後8碼
    user_id = (api_key[-8:] + "_nora") if api_key else "default_nora"

    # 分離 system messages
    system_parts = []
    non_system = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        else:
            non_system.append(m)

    # 取得真實數據並注入
    data = get_user_data(user_id)
    update_last_seen(user_id)
    injection = build_injection(data)
    system_parts.append(injection)
    system_prompt = "\n\n".join(system_parts)

    # 根據模型名稱判斷轉發給哪個 API
    model_lower = model.lower()
    if "claude" in model_lower:
        content, usage = await call_anthropic(api_key, model, system_prompt, non_system, max_tokens)
    elif "gemini" in model_lower:
        content, usage = await call_gemini(api_key, model, system_prompt, non_system, max_tokens)
    elif "deepseek" in model_lower:
        content, usage = await call_deepseek(api_key, model, system_prompt, non_system, max_tokens)
    else:
        # 預設走 Anthropic
        content, usage = await call_anthropic(api_key, model, system_prompt, non_system, max_tokens)

    return {
        "id": "chatcmpl-nora",
        "object": "chat.completion",
        "created": int(datetime.utcnow().timestamp()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop"
        }],
        "usage": usage
    }
