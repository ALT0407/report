# 📋 보고서 관리 시스템 — 설치 가이드

## 전체 구조

```
웹 사용자 (Discord OAuth 로그인)
  └─ 보고서 제출
        └─ FastAPI 백엔드 (SQLite 저장)
              └─ 관리자 웹에서 승인/반려 클릭
                    └─ 봇 내부 서버 호출
                          ├─ Discord 역할 자동 부여
                          └─ DM 자동 발송
```

---

## 1단계 — Discord Developer Portal 설정

### 앱 생성
1. https://discord.com/developers/applications 접속
2. **New Application** 클릭 → 이름 입력
3. **OAuth2** 탭 → `CLIENT ID`, `CLIENT SECRET` 복사
4. **Redirects** 에 추가:
   - 로컬 테스트: `http://localhost:8000/auth/callback`
   - 배포 후: `https://your-api.onrender.com/auth/callback`
5. **Bot** 탭 → **Add Bot** → 토큰 복사
6. Bot 탭에서 **Server Members Intent** 활성화 ✅

### 봇 초대 (서버에)
OAuth2 > URL Generator에서 스코프 `bot` 선택, 권한:
- `Manage Roles` (역할 부여)
- `Send Messages` (DM은 별도 권한 불필요)

---

## 2단계 — 로컬 실행

```bash
# 프로젝트 클론/이동
cd report-system

# .env 파일 생성
cp .env.example .env
# .env 파일을 열어서 값 채우기

# ── API 서버 ──
cd api
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# ── 봇 (새 터미널) ──
cd bot
pip install -r requirements.txt
python bot.py

# ── 프론트 (새 터미널) ──
# web/index.html 을 브라우저에서 직접 열거나
# 간단한 서버로 실행:
cd web
python -m http.server 3000
```

### 로컬 .env 설정 (api/.env)
```
DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
DISCORD_BOT_TOKEN=...
DISCORD_GUILD_ID=...
DISCORD_REDIRECT_URI=http://localhost:8000/auth/callback
FRONTEND_URL=http://localhost:3000
ADMIN_USER_IDS=내Discord아이디
BOT_CALLBACK_URL=http://localhost:8001/bot-action
SECRET_KEY=아무_랜덤_문자열_32자
```

---

## 3단계 — Render 배포

### API + Bot 배포
1. GitHub에 이 폴더 push
2. https://render.com → **New** → **Blueprint**
3. `render.yaml` 파일 선택
4. 환경변수 채우기 (Dashboard > Environment)

### 프론트엔드 배포 (Cloudflare Pages)
1. https://pages.cloudflare.com
2. `web/` 폴더 업로드 or GitHub 연결
3. 빌드 설정: 없음 (정적 파일)

### 배포 후 수정 필요한 곳
- `web/index.html` 상단: `const API = "https://your-api.onrender.com";`
- Discord Developer Portal Redirects에 실제 URL 추가
- Render 환경변수 `FRONTEND_URL`, `DISCORD_REDIRECT_URI`, `BOT_CALLBACK_URL` 업데이트

---

## 4단계 — 관리자 설정

로그인 후 **⚙️ 설정** 탭에서:

1. **보고서 양식 설정**: 보고서에 들어갈 항목 추가
   - 텍스트, 텍스트(여러줄), 선택지, 숫자, 날짜 유형 지원
2. **승인/반려 DM 메시지** 커스텀
3. **기본 역할 ID** 설정 (승인 시 자동 부여)

---

## 사용 흐름

| 역할 | 동작 |
|------|------|
| 일반 유저 | Discord 로그인 → 보고서 작성 → 제출 |
| 관리자 | 관리자 탭에서 보고서 확인 → 승인/반려 클릭 |
| 디코봇 | 승인 시: 역할 부여 + DM 발송 / 반려 시: DM 발송 |

---

## 내 Discord ID 확인 방법
Discord 설정 → 고급 → 개발자 모드 활성화  
→ 프로필 우클릭 → **ID 복사**

---

## 구조 요약

```
report-system/
├── api/
│   ├── main.py          # FastAPI 백엔드
│   └── requirements.txt
├── bot/
│   ├── bot.py           # discord.py 봇 + 내부 서버
│   └── requirements.txt
├── web/
│   └── index.html       # 프론트엔드 (단일 파일)
├── render.yaml          # Render 배포 설정
└── .env.example         # 환경변수 예시
```
