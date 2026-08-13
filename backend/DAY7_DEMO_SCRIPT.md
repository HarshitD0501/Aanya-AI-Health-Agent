# Day 7 — Demo Recording Script

Aanya jab apni limit pe pahunchti hai to insaan ko bulati hai. Ye file recording ke waqt
saamne rakhne ke liye hai — commands, exact spoken lines, aur kis order mein kya karna hai.

Takes deliberately is order mein hain: **"na" wala take pehle**, phir "haan" wala. Ulta
kiya to pehla row ban jaayega aur "kuch bhi save nahi hua" wala proof screen pe kamzor
lagega.

Video ka pura point ek line mein: **Aanya sirf do wajah se insaan ko bulati hai, aur
bulane se pehle permission maangti hai.**

---

## 0. Pre-flight (recording start karne se pehle)

### Terminal 1 — worker

```powershell
cd backend
uv run python src/agent.py console
```

**Ruko** jab tak model load na ho jaaye — cold start **~50 seconds** (Silero VAD +
turn-detector). Browser demo chahiye to `dev` chalao aur frontend `pnpm dev`.

### Terminal 2 — queue khaali karke shuru karo

```powershell
cd backend
$env:PYTHONIOENCODING="utf-8"
uv run python -c "import escalations; print(escalations.list_escalations())"
```

Expected: `[]` — khaali queue se shuru karna zaroori hai, warna dashboard pe purane rows
dikhenge aur "ye abhi bana" claim weak ho jaayega.

### Terminal 3 — dashboard

```powershell
cd frontend
pnpm dev
```

Browser: `http://localhost:3000/help-desk?token=<HELPDESK_TOKEN>` — abhi **"No open
requests"** dikhega. Ye pehla shot hai.

### Discord

`#aanya-escalations` channel ko browser mein alag tab mein khol lo, side-by-side.
**Webhook URL kabhi screen pe na aaye** — wo credential hai. `.env.local` file recording
mein na kholo.

---

## Take 1 — Red flag, par caller "na" bolta hai

Ye take Step 4 ka proof hai: **na ka matlab kuch bhi save nahi hota.**

**Aap bolo:**

> मुझे सीने में तेज़ दर्द है और साँस लेने में तकलीफ हो रही है।

**Aanya (expected order — ye order kabhi nahi badalta):**

1. Pehle emergency line — 108 par kॉल करें / nearest hospital jaayein, **abhi**
2. Uske baad hi handover offer: "मैं आपका केस एक इंसान — हेल्थ कोऑर्डिनेटर — तक पहुँचा दूँ?"
3. Permission maangte waqt exactly kya jaayega wo bologi: naam, aapne jo bataya, jo salah
   मैंने di, urgency, bhaasha, callback number

Consent emergency advice ko **kabhi** late nahi karti — 108 pehle, permission baad mein.

**Aap bolo:**

> नहीं, अभी नहीं।

**Aanya:** confirm karegi ki kuch bhi share nahi kiya gaya, aur 108 ki baat dohrayegi.

**Proof (Terminal 2):**

```powershell
uv run python -c "import escalations; print(escalations.list_escalations())"
```

→ still `[]`. Dashboard refresh karo → still "No open requests". **Ye shot lena zaroori
hai** — refusal ka matlab sirf spoken "ok" nahi, database mein zero row hai.

---

## Take 2 — Wahi red flag, ab caller "haan" bolta hai

**Aap bolo:**

> मुझे सीने में तेज़ दर्द है और साँस लेने में तकलीफ हो रही है।

Aanya phir 108 bolegi aur permission maangegi.

**Aap bolo:**

> हाँ, बिलकुल भेज दीजिए। मेरा नाम Harshit है, नंबर नौ चार पाँच चार पाँच तीन पाँच एक तीन सात।

Number **digit by digit** bolo — browser call mein caller ID nahi hota, isliye tool khud
number maangta hai aur bina number row banata hi nahi (agar follow-up "phone call" hai).

**Aanya:** reference number **digit by digit** padhegi — "एच एल पी एक शून्य शून्य एक" —
phir honest next step: ek insaan open requests dekhta hai, kitni jaldi call aayegi ye
promise nahi kar sakti, aur emergency mein 108 ka intezaar mat karo.

**Teen jagah proof, ek hi frame mein:**

| Kahan      | Kya dikhega                                                                                                |
| ---------- | ---------------------------------------------------------------------------------------------------------- |
| Terminal 1 | `Escalation HLP-1001 filed for Harshit (reason=red_flag_symptom, urgency=emergency, delivery=delivered)` |
| Discord    | Red embed — "Human help needed - HLP-1001", chhe fields, koi transcript nahi                              |
| Dashboard  | `Open · 1` — reference, urgency badge `emergency`, kya hua, Aanya ne kya kiya                        |

Discord embed pe **zoom karo aur bolo**: sirf chhe fields hain. Poora transcript nahi,
saved health history nahi, OTP/PIN/account number nahi — summary `scrub_private_details`
se guzar kar aati hai.

---

## Take 3 — Doosra trigger: diagnosis / dawa / report

Ye dikhata hai ki escalation sirf emergency ke liye nahi hai — jo cheez Aanya ko batane
ka **haq hi nahi** hai, wo bhi insaan ke paas jaati hai.

**Aap bolo (koi ek):**

> मेरी ब्लड रिपोर्ट में हीमोग्लोबिन 9 है — बताओ मुझे कौन सी बीमारी है और कौन सी दवा लूँ?

**Aanya:** seedha mana karegi — main diagnosis nahi kar sakti, dawa ka naam nahi de
sakti, report nahi padh sakti — phir handover offer karegi. Bolo "haan".

**Expected:** `HLP-1002`, `reason=diagnosis_request`, urgency `soon` (emergency nahi) —
Discord embed ka rang bhi badal jaayega (orange, laal nahi). Dashboard: `Open · 2`.

---

## Take 4 — Normal sawaal (yahan kuch nahi hona chahiye)

Ye take utna hi important hai jitna Take 2. Agar agent har cheez escalate karne lage to
human queue bekaar ho jaati hai.

**Aap bolo:**

> मुझे रात को नींद नहीं आती, क्या करूँ?

**Aanya:** khud jawab degi — sleep hygiene, screen time, caffeine, ek clarifying sawaal.
**Na insaan ka zikr, na koi reference number, na koi tool call.**

**Proof:** dashboard refresh → still `Open · 2`. Terminal 1 mein koi `Escalation ... filed`
line nahi. Ye frame karo aur bolo: "sleep, diet, stress, PHC lookup, reminders — ye sab
Aanya ka khud ka kaam hai. Escalate karne ke sirf do trigger hain."

---

## Post-recording screens (30 seconds, video ke end mein)

Test suite — consent gate, dono trigger, scrubber, webhook failure, aur dono conversation
paths. Koi webhook aur koi network nahi chahiye:

```powershell
uv run pytest tests/test_day7_escalation.py -q
```

Queue CLI se:

```powershell
uv run python -c "import escalations; print(escalations.list_escalations())"
```

Ek line dikhane layak: webhook mar bhi jaaye to request nahi khoti —
`delivery_status` `failed` ho jaata hai par row aur dashboard entry zinda rehti hai.
`ESCALATION_WEBHOOK_URL` blank karke dubara chalao → `saved_only`, aur dashboard pe
"saved here only (no webhook configured)".

---

## Gotchas

| Problem                                                 | Kya karna hai                                                                                                                                                     |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Aanya reference number "one thousand one" padh rahi hai | `_spoken_reference()` use nahi hua. Tool ka return message digit-by-digit form deta hai — prompt ko wahi padhna hai.                                           |
| "No usable phone number yet"                            | Browser call mein caller ID nahi hota. Number digit by digit bolo, ya follow-up "WhatsApp message" bolo.                                                          |
| Discord mein kuch nahi aaya                             | `ESCALATION_WEBHOOK_URL` `.env.local` mein set hai? Worker restart kiya? Row phir bhi bani hogi — dashboard check karo, `delivery_status` reason batayega. |
| Dashboard "Not authorised"                              | URL mein`?token=` wahi hona chahiye jo `frontend/.env.local` ke `HELPDESK_TOKEN` mein hai.                                                                  |
| Dashboard "Could not read the escalation database"      | `ESCALATION_DB_PATH` galat hai. Page khud batata hai kaunsa path try kiya tha.                                                                                  |
| Ordinary sawaal pe bhi escalation                       | Prompt ka never-escalate list check karo;`tests/test_day7_escalation.py` ka sleep-path test isi ke liye hai.                                                    |

---

## Recording ke baad (security)

1. Discord webhook **delete karke naya banao** — agar galti se screen pe aa gaya ho.
2. `backend/health_memory.db` commit **na karo**. Demo ke baad us mein asli health
   complaints aur phone numbers hain.
3. `HELPDESK_TOKEN` blank rakh kar dashboard ko localhost ke baahar expose na karo —
   page khud amber banner mein ye warning dikhata hai.
