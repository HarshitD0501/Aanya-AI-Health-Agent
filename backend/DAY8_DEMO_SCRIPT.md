# Day 8 — Demo Recording Script: Call Analytics Dashboard

Aanya ab apni performance track kar sakti hai. Is video mein ek successful call dikhao
aur dashboard pe counts update hote dekhao — real data, koi hardcoded value nahi.

Video ka pura point ek line mein: **dashboard sirf real call data dikhata hai — ek call
karo, ek row banta hai, count badhta hai.**

---

## 0. Pre-flight (recording start karne se pehle)

### Terminal 1 — backend worker

```powershell
cd backend
uv run python src/agent.py dev
```

**Ruko** jab tak ye do lines na dikh jaayein:

```
INFO  livekit.agents  job runner initialized     <- warm process ready
INFO  livekit.agents  registered worker
```

`job runner initialized` sabse important hai — iska matlab ek process pehle se
taiyaar baitha hai, to call aane pe caller ko silence nahi milega. Agar tum call
`registered worker` se pehle karte ho, ya agar log mein
`no warmed process available for job` dikhe, to caller ko 20 second tak kuch
sunai nahi dega aur wo cut kar dega — us take ko phenk do.

Pehli baar models download hote hain (~90s). Dusri baar se ~10s.

### Terminal 2 — frontend

```powershell
cd frontend
pnpm dev
```

### Browser: do tabs kholo

| Tab | URL |
|-----|-----|
| Home | `http://localhost:3000` |
| Analytics | `http://localhost:3000/analytics` |

Analytics tab abhi dikhayega:
- **Total Calls: 0** (ya jo pehle se hain)
- **Successful: 0**
- **Failed: 0**
- Call log: "No calls recorded yet."

Ye pehla shot hai — dashboard khali ya baseline count ke saath.

---

## Take 1 — Successful call (primary demo)

**Analytics tab ko ek side mein rakh lo (split-screen).**

**Home tab → "Get started" click karo.**

Aanya greet karegi. Ab bolo:

> नमस्ते आन्या! मुझे पिछले दो दिनों से सिरदर्द हो रहा है, क्या करूँ?

Aanya jawab degi — symptoms poochegi, sleep/hydration/screen time pe guidance degi.

Jab guidance complete ho jaaye, bolo:

> धन्यवाद, बहुत मददगार रहीं आप।

Aanya name/consent ke baare mein poochegi. Bolo apna naam, consent do.
Aanya farewell degi aur call end karti hai.

**Ab Analytics tab refresh karo (ya 3 seconds ruko — auto-refresh on hai):**

Expected:
- **Total Calls: +1**
- **Successful Calls: +1** (caller_spoke=True, duration ≥ 5s)
- Call log mein naya row: channel "Web Browser", outcome "Successful", duration > 5s

**Ye shot lena zaroori hai** — screen pe real data update hote dikhao.

---

## Take 2 — Failed call proof (optional but strong)

Ye dikhata hai ki success aur failure ka fark real logic se aata hai, hardcode se nahi.

**Home → "Get started" click karo.**

Jab Aanya greet kare, **turant browser tab close kar do** ya call 2-3 seconds mein hi
disconnect kar do — kuch bhi mat bolo.

**Analytics tab:**

Expected:
- **Total Calls: +1** (ab +2 total)
- **Failed Calls: +1**
- Call log: outcome "Failed", reason "Early disconnect / no response", duration < 5s

> Greeting sunne ke **baad** cut karo. Agar Aanya bolne se pehle hi cut kar diya, to
> reason "Caller left before agent was ready" aayega — wo bhi failed hai, par wo
> Aanya ki slow start ki galti hai, caller ki nahi. Take 2 ke liye pehla wala chahiye.

---

## Take 3 — Outbound reminder call (agar SIP setup hai)

Agar outbound kaam kar raha hai, is command se ek test reminder call trigger karo:

```powershell
cd backend
uv run python src/outbound.py
```

Outbound call answer karo, "haan, le li" bolo, Aanya farewell degi.

**Analytics tab:**
- Ek aur successful row: channel "Outbound SIP", outcome "Successful"

---

## Dashboard ke features jo camera pe dikhao

1. **KPI cards** — Total / Successful / Failed / Success Rate % with progress bar
2. **"Live (3s)" toggle** — green pulsing dot, auto-refresh on
3. **Privacy banner** — "Caller Privacy Guaranteed: phone numbers masked"
4. **Murf Falcon badge** — "Powered by Murf Falcon TTS"
5. **Call log table** — Channel badge (Web Browser / Inbound SIP / Outbound SIP),
   Outcome badge (green/red), Duration, masked Caller ID, Timestamp

---

## Success condition (bolo on camera)

> "Aanya ek call ko 'successful' tab maanti hai jab caller ne kuch bola ho aur call
> 5 seconds se zyada chali ho. Isse short disconnects aur voicemail drops filter ho
> jaate hain. Ye logic backend mein hai — koi hardcoded number nahi hai."

---

## Post-recording screens (30 seconds, video end mein)

```powershell
cd backend
uv run pytest tests/ -q
```

Aur analytics CLI se verify karo:

```powershell
uv run python -c "import db; print(db.get_call_analytics_summary())"
```

---

## Privacy compliance (camera pe zaroori)

Dashboard mein koi bhi:
- Full phone number **nahi** dikhta — sirf masked version (`+91 945****137`)
- PIN, OTP, password **nahi** dikhta
- Full transcript **nahi** dikhta
- Medical records **nahi** dikhte

Ye Day 8 ka Step 6 hai — explicitly bolo on camera.

---

## Gotchas

| Problem | Kya karna hai |
|---------|---------------|
| Call connect hui par Aanya kuch nahi boli, 20s silence | Worker warm nahi tha. Log mein `no warmed process available for job` dhoondo. Worker ko `registered worker` tak ruk kar phir call karo |
| Duration dashboard pe expected se kam | Sahi hai — clock greeting se shuru hota hai, session setup se nahi. Setup time call duration mein nahi ginti |
| Greeting ke baad Aanya jawab hi nahi deti, log mein `404 ... no longer available` | Google ne woh Gemini model retire kar diya. `backend/.env.local` mein `GEMINI_MODEL=<naya flash model>` set karo — code change ki zaroorat nahi. Default `gemini-3.5-flash-lite` hai |
| Dashboard "No calls recorded yet" ke baad bhi | `ANALYTICS_DB_PATH` ya `ESCALATION_DB_PATH` frontend `.env.local` mein set hai? Path `../backend/health_memory.db` hona chahiye |
| Success count nahi badha | Call 5 seconds se kam thi ya kuch bola nahi — `Failed` column check karo |
| DB path error | `frontend/.env.local` mein `ANALYTICS_DB_PATH=../backend/health_memory.db` add karo |
| Node:sqlite error | Node.js version 22.5+ chahiye — `node --version` check karo |

---

## Recording ke baad (security)

1. `backend/health_memory.db` commit **na karo** — isme real call data hai.
2. `.env.local` files screen pe kabhi na dikhao.
