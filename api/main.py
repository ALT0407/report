from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import httpx, sqlite3, json, os, secrets
from datetime import datetime
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Report System API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config ───────────────────────────────────────────────
DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI  = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8000/auth/callback")
DISCORD_BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID      = os.getenv("DISCORD_GUILD_ID", "")
BOT_CALLBACK_URL      = os.getenv("BOT_CALLBACK_URL", "http://localhost:8001/bot-action")
# 관리자: 환경변수 (호스팅에서 설정)
ADMIN_USER_IDS = [x.strip() for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip()]

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

        CREATE TABLE IF NOT EXISTS rank_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role_id TEXT,
            order_num INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS position_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role_id TEXT,
            order_num INTEGER DEFAULT 0
        );

        -- 보직장 테이블: discord_id <-> position_id (1:1)
        CREATE TABLE IF NOT EXISTS commanders (
            discord_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            position_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT NOT NULL,
            discord_id TEXT NOT NULL,
            username TEXT NOT NULL,
            avatar TEXT,
            writer TEXT NOT NULL,
            target TEXT NOT NULL,
            reason TEXT NOT NULL,
            before_value TEXT NOT NULL,
            after_value TEXT NOT NULL,
            position_id INTEGER,
            position_name TEXT,
            after_role_id TEXT,
            after_role_name TEXT,
            status TEXT DEFAULT 'pending',
            reject_reason TEXT,
            approved_by TEXT,
            approved_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    defaults = [
        ("dm_approve_rank",     "계급 변동이 인가되었습니다."),
        ("dm_reject_rank",      "계급 변동이 기각되었습니다."),
        ("dm_approve_position", "보직 변동이 인가되었습니다."),
        ("dm_reject_position",  "보직 변동이 기각되었습니다."),
    ]
    for k, v in defaults:
        c.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (k, v))
    conn.commit()
    conn.close()

init_db()

# ─── 권한 헬퍼 ────────────────────────────────────────────
def get_session(token: str):
    if not token:
        raise HTTPException(status_code=401, detail="인증이 필요합니다")
    conn = get_db()
    row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="인증이 필요합니다")
    return dict(row)

def is_admin(discord_id: str) -> bool:
    return discord_id in ADMIN_USER_IDS

def get_commander_info(discord_id: str):
    """보직장이면 {position_id, position_name} 반환, 아니면 None"""
    conn = get_db()
    row = conn.execute(
        "SELECT c.discord_id, c.position_id, p.name as position_name "
        "FROM commanders c LEFT JOIN position_list p ON c.position_id=p.id "
        "WHERE c.discord_id=?", (discord_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def require_admin(token: str):
    session = get_session(token)
    if not is_admin(session["discord_id"]):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return session

def require_can_review(token: str):
    """관리자 또는 보직장이어야 함"""
    session = get_session(token)
    did = session["discord_id"]
    if is_admin(did):
        return session
    if get_commander_info(did):
        return session
    raise HTTPException(status_code=403, detail="검토 권한이 없습니다")

# ─── 봇 액션 ──────────────────────────────────────────────
async def call_bot(action: str, discord_id: str, role_id: str = None, message: str = None):
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
        print("봇 액션 실패: " + str(e))

# ─── Auth ─────────────────────────────────────────────────
@app.get("/auth/login")
def discord_login():
    url = (
        "https://discord.com/api/oauth2/authorize"
        "?client_id=" + DISCORD_CLIENT_ID +
        "&redirect_uri=" + DISCORD_REDIRECT_URI +
        "&response_type=code"
        "&scope=identify"
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
            raise HTTPException(status_code=400,
                detail="OAuth failed: status=" + str(token_res.status_code) + " body=" + token_res.text[:200])
        token_data = token_res.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="No access_token: " + str(token_data))
        user_res = await client.get("https://discord.com/api/users/@me",
            headers={"Authorization": "Bearer " + access_token})
        user = user_res.json()

    session_token = secrets.token_urlsafe(32)
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,datetime('now'))",
        (session_token, user["id"], user["username"], user.get("avatar", "")))
    conn.commit()
    conn.close()

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return RedirectResponse(frontend_url + "?token=" + session_token)

@app.get("/auth/me")
def get_me(token: str):
    session = get_session(token)
    did = session["discord_id"]
    admin = is_admin(did)
    cmd = get_commander_info(did)
    return {
        **session,
        "is_admin": admin,
        "is_commander": cmd is not None,
        "commander_position_id": cmd["position_id"] if cmd else None,
        "commander_position_name": cmd["position_name"] if cmd else None,
    }

@app.post("/auth/logout")
def logout(token: str):
    conn = get_db()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ─── 계급 목록 ────────────────────────────────────────────
class ListItem(BaseModel):
    name: str
    role_id: Optional[str] = None
    order_num: int = 0

@app.get("/ranks")
def get_ranks():
    conn = get_db()
    rows = conn.execute("SELECT * FROM rank_list ORDER BY order_num").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/ranks")
def add_rank(item: ListItem, token: str):
    require_admin(token)
    conn = get_db()
    conn.execute("INSERT INTO rank_list (name,role_id,order_num) VALUES (?,?,?)",
        (item.name, item.role_id, item.order_num))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/ranks/{rid}")
def delete_rank(rid: int, token: str):
    require_admin(token)
    conn = get_db()
    conn.execute("DELETE FROM rank_list WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ─── 보직 목록 ────────────────────────────────────────────
@app.get("/positions")
def get_positions():
    conn = get_db()
    rows = conn.execute("SELECT * FROM position_list ORDER BY order_num").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/positions")
def add_position(item: ListItem, token: str):
    require_admin(token)
    conn = get_db()
    conn.execute("INSERT INTO position_list (name,role_id,order_num) VALUES (?,?,?)",
        (item.name, item.role_id, item.order_num))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/positions/{pid}")
def delete_position(pid: int, token: str):
    require_admin(token)
    conn = get_db()
    conn.execute("DELETE FROM position_list WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ─── 보직장 관리 (관리자만) ───────────────────────────────
@app.get("/commanders")
def get_commanders(token: str):
    require_admin(token)
    conn = get_db()
    rows = conn.execute(
        "SELECT c.discord_id, c.username, c.position_id, p.name as position_name, c.created_at "
        "FROM commanders c LEFT JOIN position_list p ON c.position_id=p.id "
        "ORDER BY c.created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

class CommanderCreate(BaseModel):
    discord_id: str
    username: str
    position_id: int

@app.post("/commanders")
def add_commander(body: CommanderCreate, token: str):
    require_admin(token)
    conn = get_db()
    # 해당 보직에 이미 보직장이 있는지 확인
    existing = conn.execute(
        "SELECT discord_id FROM commanders WHERE position_id=?", (body.position_id,)
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="해당 보직에 이미 보직장이 등록되어 있습니다")
    conn.execute(
        "INSERT OR REPLACE INTO commanders (discord_id, username, position_id) VALUES (?,?,?)",
        (body.discord_id, body.username, body.position_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/commanders/{discord_id}")
def delete_commander(discord_id: str, token: str):
    require_admin(token)
    conn = get_db()
    conn.execute("DELETE FROM commanders WHERE discord_id=?", (discord_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ─── 보고서 ───────────────────────────────────────────────
class ReportCreate(BaseModel):
    report_type: str
    writer: str
    target: str
    reason: str
    before_value: str
    after_value: str
    position_id: Optional[int] = None
    position_name: Optional[str] = None
    after_role_id: Optional[str] = None
    after_role_name: Optional[str] = None

@app.post("/reports")
def submit_report(report: ReportCreate, token: str):
    session = get_session(token)
    conn = get_db()
    conn.execute("""INSERT INTO reports
        (report_type,discord_id,username,avatar,writer,target,reason,
         before_value,after_value,position_id,position_name,after_role_id,after_role_name)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (report.report_type, session["discord_id"], session["username"], session["avatar"],
         report.writer, report.target, report.reason,
         report.before_value, report.after_value,
         report.position_id, report.position_name,
         report.after_role_id, report.after_role_name))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/reports")
def get_reports(token: str, status: str = None, report_type: str = None):
    session = get_session(token)
    did = session["discord_id"]
    admin = is_admin(did)
    cmd = get_commander_info(did)

    if not admin and not cmd:
        # 일반 유저: 본인 보고서만
        conn = get_db()
        query = "SELECT * FROM reports WHERE discord_id=?"
        params = [did]
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    conn = get_db()
    if admin:
        # 관리자: 전체
        query = "SELECT * FROM reports WHERE 1=1"
        params = []
    else:
        # 보직장: 자기 담당 보직 보고서만
        query = "SELECT * FROM reports WHERE position_id=?"
        params = [cmd["position_id"]]

    if status:
        query += " AND status=?"
        params.append(status)
    if report_type:
        query += " AND report_type=?"
        params.append(report_type)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/reports/{report_id}")
def get_report(report_id: int, token: str):
    session = get_session(token)
    did = session["discord_id"]
    conn = get_db()
    row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404)
    d = dict(row)
    cmd = get_commander_info(did)
    # 접근 권한: 관리자 or 담당 보직장 or 본인
    if not is_admin(did):
        if cmd:
            if d["position_id"] != cmd["position_id"]:
                raise HTTPException(status_code=403, detail="담당 보직 보고서가 아닙니다")
        elif d["discord_id"] != did:
            raise HTTPException(status_code=403)
    return d

class RejectBody(BaseModel):
    reject_reason: Optional[str] = ""

@app.post("/reports/{report_id}/approve")
async def approve_report(report_id: int, token: str):
    session = require_can_review(token)
    did = session["discord_id"]
    cmd = get_commander_info(did)

    conn = get_db()
    row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404)
    r = dict(row)

    # 보직장이면 담당 보직 보고서만 처리 가능
    if cmd and r["position_id"] != cmd["position_id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="담당 보직 보고서가 아닙니다")
    if r["status"] != "pending":
        conn.close()
        raise HTTPException(status_code=400, detail="이미 처리된 보고서입니다")

    conn.execute(
        "UPDATE reports SET status='approved', approved_by=?, approved_at=datetime('now') WHERE id=?",
        (session["username"], report_id))
    conn.commit()

    type_name = "계급 변동" if r["report_type"] == "rank" else "보직 변동"
    dm_key = "dm_approve_rank" if r["report_type"] == "rank" else "dm_approve_position"
    s = conn.execute("SELECT value FROM settings WHERE key=?", (dm_key,)).fetchone()
    dm_base = s["value"] if s else "인가되었습니다."
    conn.close()

    role_line = ("\n부여 역할: " + r["after_role_name"]) if r["after_role_name"] else ""
    pos_line = ("\n소속 보직: " + r["position_name"]) if r["position_name"] else ""

    dm_msg = (
        dm_base + "\n\n"
        "[ " + type_name + " 보고서 처리 결과 ]\n"
        "작성자: " + r["writer"] + "\n"
        "대상자: " + r["target"] + "\n"
        "변동: " + r["before_value"] + " → " + r["after_value"] +
        pos_line + role_line + "\n"
        "결과: 인가\n"
        "처리자: " + session["username"]
    )
    await call_bot("approve", r["discord_id"], role_id=r["after_role_id"], message=dm_msg)
    return {"ok": True}

@app.post("/reports/{report_id}/reject")
async def reject_report(report_id: int, body: RejectBody, token: str):
    session = require_can_review(token)
    did = session["discord_id"]
    cmd = get_commander_info(did)

    conn = get_db()
    row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404)
    r = dict(row)

    if cmd and r["position_id"] != cmd["position_id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="담당 보직 보고서가 아닙니다")
    if r["status"] != "pending":
        conn.close()
        raise HTTPException(status_code=400, detail="이미 처리된 보고서입니다")

    conn.execute(
        "UPDATE reports SET status='rejected', reject_reason=?, approved_by=?, approved_at=datetime('now') WHERE id=?",
        (body.reject_reason, session["username"], report_id))
    conn.commit()

    type_name = "계급 변동" if r["report_type"] == "rank" else "보직 변동"
    dm_key = "dm_reject_rank" if r["report_type"] == "rank" else "dm_reject_position"
    s = conn.execute("SELECT value FROM settings WHERE key=?", (dm_key,)).fetchone()
    dm_base = s["value"] if s else "기각되었습니다."
    pos_line = ("\n소속 보직: " + r["position_name"]) if r["position_name"] else ""
    conn.close()

    dm_msg = (
        dm_base + "\n\n"
        "[ " + type_name + " 보고서 처리 결과 ]\n"
        "작성자: " + r["writer"] + "\n"
        "대상자: " + r["target"] + "\n"
        "변동: " + r["before_value"] + " → " + r["after_value"] +
        pos_line + "\n"
        "결과: 기각\n"
        "기각 사유: " + (body.reject_reason or "없음") + "\n"
        "처리자: " + session["username"]
    )
    await call_bot("reject", r["discord_id"], message=dm_msg)
    return {"ok": True}

# ─── 설정 ─────────────────────────────────────────────────
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
