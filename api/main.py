from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import httpx, sqlite3, json, os, secrets
from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI(title="Report System API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config (환경변수로 설정) ───────────────────────────────
DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "YOUR_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
DISCORD_REDIRECT_URI  = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8000/auth/callback")
DISCORD_BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN", "YOUR_BOT_TOKEN")
DISCORD_GUILD_ID      = os.getenv("DISCORD_GUILD_ID", "YOUR_GUILD_ID")
ADMIN_USER_IDS        = os.getenv("ADMIN_USER_IDS", "").split(",")  # 관리자 Discord ID 목록
SECRET_KEY            = os.getenv("SECRET_KEY", secrets.token_hex(32))
BOT_CALLBACK_URL      = os.getenv("BOT_CALLBACK_URL", "http://localhost:8001/bot-action")

DB_PATH = "reports.db"

# ─── DB 초기화 ────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            discord_id TEXT NOT NULL,
            username TEXT NOT NULL,
            avatar TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL,
            username TEXT NOT NULL,
            avatar TEXT,
            title TEXT NOT NULL,
            fields TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            admin_note TEXT,
            approved_by TEXT,
            approved_at TEXT,
            role_to_grant TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS form_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            type TEXT NOT NULL,
            required INTEGER DEFAULT 1,
            options TEXT,
            order_num INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    # 기본 설정
    c.execute("INSERT OR IGNORE INTO settings VALUES ('role_on_approve', '')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('dm_approve_msg', '✅ 보고서가 승인되었습니다!')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('dm_reject_msg', '❌ 보고서가 반려되었습니다.')")
    conn.commit()
    conn.close()

init_db()

# ─── 세션 헬퍼 ────────────────────────────────────────────
def get_session(token: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="인증이 필요합니다")
    return dict(row)

def require_admin(token: str):
    session = get_session(token)
    if session["discord_id"] not in ADMIN_USER_IDS:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return session

# ─── Discord 봇 액션 호출 ─────────────────────────────────
async def call_bot(action: str, discord_id: str, role_id: str = None, message: str = None):
    """봇 서버에 액션 요청 (봇이 별도 프로세스로 실행 중)"""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(BOT_CALLBACK_URL, json={
                "action": action,
                "discord_id": discord_id,
                "guild_id": DISCORD_GUILD_ID,
                "role_id": role_id,
                "message": message
            }, timeout=10)
    except Exception as e:
        print(f"봇 액션 실패: {e}")

# ─── Auth Routes ──────────────────────────────────────────
@app.get("/auth/login")
def discord_login():
    url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify"
    )
    return RedirectResponse(url)

@app.get("/auth/callback")
async def discord_callback(code: str):
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "DiscordBot (https://github.com, 1.0)",
            }
        )
        if token_res.status_code != 200 or not token_res.text.strip():
            raise HTTPException(
                status_code=400,
                detail="OAuth failed: status=" + str(token_res.status_code) + " body=" + token_res.text[:200]
            )

        token_data = token_res.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="No access_token: " + str(token_data))

    session_token = secrets.token_urlsafe(32)
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,datetime('now'))",
        (session_token, user["id"], user["username"],
         user.get("avatar", "")))
    conn.commit()
    conn.close()

    # 프론트로 리다이렉트 (토큰 전달)
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return RedirectResponse(f"{frontend_url}?token={session_token}")

@app.get("/auth/me")
def get_me(token: str):
    session = get_session(token)
    is_admin = session["discord_id"] in ADMIN_USER_IDS
    return {**session, "is_admin": is_admin}

@app.post("/auth/logout")
def logout(token: str):
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ─── Form Fields (관리자 커스텀) ──────────────────────────
@app.get("/fields")
def get_fields():
    conn = get_db()
    rows = conn.execute("SELECT * FROM form_fields ORDER BY order_num").fetchall()
    conn.close()
    return [dict(r) for r in rows]

class FieldCreate(BaseModel):
    label: str
    type: str  # text, textarea, select, number, date
    required: bool = True
    options: Optional[str] = None  # select일 때 쉼표 구분
    order_num: int = 0

@app.post("/fields")
def add_field(field: FieldCreate, token: str):
    require_admin(token)
    conn = get_db()
    conn.execute("INSERT INTO form_fields (label,type,required,options,order_num) VALUES (?,?,?,?,?)",
        (field.label, field.type, int(field.required), field.options, field.order_num))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/fields/{field_id}")
def delete_field(field_id: int, token: str):
    require_admin(token)
    conn = get_db()
    conn.execute("DELETE FROM form_fields WHERE id=?", (field_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.put("/fields/reorder")
def reorder_fields(orders: List[dict], token: str):
    require_admin(token)
    conn = get_db()
    for item in orders:
        conn.execute("UPDATE form_fields SET order_num=? WHERE id=?",
            (item["order_num"], item["id"]))
    conn.commit()
    conn.close()
    return {"ok": True}

# ─── Reports ─────────────────────────────────────────────
class ReportCreate(BaseModel):
    title: str
    fields: dict  # { label: value }

@app.post("/reports")
def submit_report(report: ReportCreate, token: str):
    session = get_session(token)
    conn = get_db()
    conn.execute(
        "INSERT INTO reports (discord_id,username,avatar,title,fields) VALUES (?,?,?,?,?)",
        (session["discord_id"], session["username"], session["avatar"],
         report.title, json.dumps(report.fields, ensure_ascii=False))
    )
    conn.commit()
    conn.close()
    return {"ok": True, "message": "보고서가 제출되었습니다"}

@app.get("/reports")
def get_reports(token: str, status: str = None):
    session = get_session(token)
    is_admin = session["discord_id"] in ADMIN_USER_IDS
    conn = get_db()

    if is_admin:
        if status:
            rows = conn.execute("SELECT * FROM reports WHERE status=? ORDER BY created_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM reports ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM reports WHERE discord_id=? ORDER BY created_at DESC",
            (session["discord_id"],)).fetchall()

    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["fields"] = json.loads(d["fields"])
        result.append(d)
    return result

@app.get("/reports/{report_id}")
def get_report(report_id: int, token: str):
    session = get_session(token)
    is_admin = session["discord_id"] in ADMIN_USER_IDS
    conn = get_db()
    row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다")
    d = dict(row)
    if not is_admin and d["discord_id"] != session["discord_id"]:
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다")
    d["fields"] = json.loads(d["fields"])
    return d

class ApproveRequest(BaseModel):
    admin_note: Optional[str] = ""
    role_id: Optional[str] = ""

@app.post("/reports/{report_id}/approve")
async def approve_report(report_id: int, body: ApproveRequest, token: str):
    admin = require_admin(token)
    conn = get_db()
    row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404)

    conn.execute("""UPDATE reports SET status='approved', admin_note=?,
        approved_by=?, approved_at=datetime('now'), role_to_grant=? WHERE id=?""",
        (body.admin_note, admin["username"], body.role_id, report_id))
    conn.commit()

    # 설정에서 DM 메시지 가져오기
    dm_msg = conn.execute("SELECT value FROM settings WHERE key='dm_approve_msg'").fetchone()
    dm_msg = dm_msg["value"] if dm_msg else "✅ 보고서가 승인되었습니다!"
    conn.close()

    # 봇에게 액션 요청
    discord_id = dict(row)["discord_id"]
    await call_bot("approve", discord_id, role_id=body.role_id,
        message=f"{dm_msg}\n\n📋 보고서: **{dict(row)['title']}**\n💬 관리자 메모: {body.admin_note or '없음'}")

    return {"ok": True}

@app.post("/reports/{report_id}/reject")
async def reject_report(report_id: int, body: ApproveRequest, token: str):
    admin = require_admin(token)
    conn = get_db()
    row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404)

    conn.execute("""UPDATE reports SET status='rejected', admin_note=?,
        approved_by=?, approved_at=datetime('now') WHERE id=?""",
        (body.admin_note, admin["username"], report_id))
    conn.commit()

    dm_msg = conn.execute("SELECT value FROM settings WHERE key='dm_reject_msg'").fetchone()
    dm_msg = dm_msg["value"] if dm_msg else "❌ 보고서가 반려되었습니다."
    conn.close()

    discord_id = dict(row)["discord_id"]
    await call_bot("reject", discord_id,
        message=f"{dm_msg}\n\n📋 보고서: **{dict(row)['title']}**\n💬 반려 사유: {body.admin_note or '없음'}")

    return {"ok": True}

# ─── Settings ─────────────────────────────────────────────
@app.get("/settings")
def get_settings(token: str):
    require_admin(token)
    conn = get_db()
    rows = conn.execute("SELECT * FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

@app.put("/settings")
def update_settings(settings: dict, token: str):
    require_admin(token)
    conn = get_db()
    for k, v in settings.items():
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, v))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}
