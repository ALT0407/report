"""
디코봇 서버 - FastAPI 백엔드의 요청을 받아 디스코드 액션 수행
별도 프로세스로 실행: python bot.py
"""
import discord
from discord.ext import commands
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import asyncio, os, uvicorn, threading

BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN", "YOUR_BOT_TOKEN")
BOT_PORT   = int(os.getenv("BOT_PORT", "8001"))

# ─── Discord 봇 설정 ──────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ 봇 로그인 완료: {bot.user}")

# ─── FastAPI 내부 서버 (봇 액션 수신) ────────────────────
app_inner = FastAPI()

class BotActionRequest(BaseModel):
    action: str          # "approve" | "reject"
    discord_id: str
    guild_id: str
    role_id: Optional[str] = None
    message: Optional[str] = None

@app_inner.post("/bot-action")
async def bot_action(req: BotActionRequest):
    guild = bot.get_guild(int(req.guild_id))
    if not guild:
        return {"error": "서버를 찾을 수 없습니다"}

    member = guild.get_member(int(req.discord_id))
    if not member:
        # 캐시에 없으면 fetch
        try:
            member = await guild.fetch_member(int(req.discord_id))
        except discord.NotFound:
            return {"error": "멤버를 찾을 수 없습니다"}

    results = []

    # 역할 부여 (승인 시)
    if req.action == "approve" and req.role_id:
        try:
            role = guild.get_role(int(req.role_id))
            if role:
                await member.add_roles(role, reason="보고서 시스템 자동 승인")
                results.append(f"✅ 역할 부여: {role.name}")
            else:
                results.append("⚠️ 역할을 찾을 수 없음")
        except discord.Forbidden:
            results.append("❌ 역할 부여 권한 없음")

    # DM 발송
    if req.message:
        try:
            embed = discord.Embed(
                title="📋 보고서 처리 결과",
                description=req.message,
                color=0x57F287 if req.action == "approve" else 0xED4245
            )
            embed.set_footer(text="보고서 관리 시스템")
            await member.send(embed=embed)
            results.append("✅ DM 발송 완료")
        except discord.Forbidden:
            results.append("⚠️ DM 차단됨 (유저 설정)")

    return {"ok": True, "results": results}

@app_inner.get("/health")
def health():
    return {"status": "ok", "bot_ready": bot.is_ready()}

# ─── 봇 + FastAPI 동시 실행 ───────────────────────────────
def run_fastapi():
    uvicorn.run(app_inner, host="0.0.0.0", port=BOT_PORT, log_level="warning")

async def main():
    # FastAPI를 별도 스레드에서 실행
    thread = threading.Thread(target=run_fastapi, daemon=True)
    thread.start()
    print(f"🌐 봇 내부 서버 시작 (포트 {BOT_PORT})")

    # 디스코드 봇 시작
    await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
