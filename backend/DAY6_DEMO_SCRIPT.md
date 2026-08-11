# Day 6 — Demo Recording Script

Aanya outbound medication reminder calls. Ye file recording ke waqt saamne rakhne ke
liye hai — commands, exact spoken lines, aur kis order mein kya karna hai.

Takes deliberately is order mein hain: **opt-out sabse last**, kyunki wo reminder row
ko permanently disable kar deta hai. Pehle kiya to Take 1 aur 2 dial hi nahi honge.

---

## 0. Pre-flight (recording start karne se pehle)

### Terminal 1 — worker

```powershell
cd backend
uv run python src/agent.py dev
```

**Ruko** jab tak ye line na dikhe:

```
registered worker {"agent_name": "Aanya", "id": "AW_...", "region": "India South"}
```

Cold start **~50 seconds** leta hai (Silero VAD + turn-detector model load). Is window
ke andar dial kiya to koi worker dispatch claim nahi karta — call connect ho jaayegi
par Aanya chup rahegi. Ye exact galti pehle ho chuki hai.

### Terminal 2 — reminder register karo

```powershell
cd backend
uv run python src/outbound.py --register `
  --to +919454535137 `
  --name Harshit `
  --medicine "दवा" `
  --dosage "1 tablet after dinner" `
  --at 20:00 `
  --language Hindi
```

Confirm:

```powershell
uv run python src/outbound.py --list
```

Expected: `1  Harshit  दवा  20:00  +919454535137  pending`

Agar `--list` par console `UnicodeEncodeError` de to pehle `$env:PYTHONIOENCODING="utf-8"`
chala lo — Windows console default cp1252 hai.

---

## Dialogue sheet

Bolne wali exact lines — Aanya ki har turn ke saamne aapka jawab, Devanagari + Roman —
alag file mein hain: [`script.md`](script.md). Recording ke waqt wahi saamne rakho.

Do rules: **phone uthate hi 1 second ruko** (Aanya pehle bolti hai), aur **sentence poora
karke ruko** — turn detection pause pe trigger hota hai.

---

## Take 1 — Happy path (dose li hui hai)

**Dial:**

```powershell
uv run python src/outbound.py --reminder-id 1
```

Phone bajega. **Utha lo aur 1 second ruko** — Aanya pehle bolegi.

**Aanya (code se, LLM se nahi):**

> नमस्ते Harshit जी, मैं आन्या बोल रही हूँ — आपकी हेल्थ रिमाइंडर सेवा से।
> आपने दवा के लिए रात आठ बजे का रिमाइंडर सेट किया था, इसलिए यह कॉल की है।
> अगर आप ये कॉल बंद करवाना चाहें, तो बस कहिए "रिमाइंडर बंद करें", मैं तुरंत बंद कर दूँगी।
> क्या आपने आज दवा ले ली है?

Ye pehle do sentence Day 6 ka graded requirement hai — **kaun, kyun, kaise band karein**.

**Aap bolo:**

> Haan, le li hai. Khaane ke baad li thi.

**Aanya:** confirm karegi, kal ka same time mention karegi, phir warm farewell.

**Terminal 1 mein dekhna:**

```
Reminder 1: medicine taken. note='...'
```

Wo `confirm_medicine_taken(taken=True)` tool fire hone ka proof hai — screen recording
mein isko frame karo.

---

## Take 2 — Dose missed + ek health sawaal

Ye take do cheezein dikhata hai: adherence dial-outcome se alag record hoti hai, aur
Day 1–5 ka health advisor persona outbound call mein bhi zinda hai.

```powershell
uv run python src/outbound.py --reminder-id 1
```

**Aap bolo (opening ke baad):**

> Nahi, aaj bhool gaya tha.

**Aanya:** gently ab lene ko kahegi, dosage repeat karegi ("1 tablet after dinner").

**Phir usi call mein aap poochho:**

> Ek baat poochhni thi — is dawa ke saath chakkar aate hain kya?

Ya PHC/helpline tool trigger karne ke liye:

> Mere paas sabse kareeb PHC kahan hai?

**Terminal 1:**

```
Reminder 1: medicine not_taken. note='...'
```

Note: dial outcome `answered` hi rahega. Adherence (`not_taken`) ek alag field hai —
call uthana aur dawa lena do alag cheezein hain, aur schema mein bhi alag hain.

---

## Take 3 — Opt-out (SABSE LAST)

Ye demo ka strongest moment hai. Ye take reminder ko permanently disable kar dega.

```powershell
uv run python src/outbound.py --reminder-id 1
```

**Aap Aanya ko opening ke beech mein interrupt karo** (barge-in bhi dikh jaayega):

> रिमाइंडर बंद करें।

Ya English mein: *"Stop the reminders."*

**Aanya:** turant confirm karegi, disturbance ke liye sorry bolegi, farewell — **na wajah
poochhegi, na convince karne ki koshish karegi.**

**Terminal 1:**

```
Opt-out honoured for '+919454535137': 1 reminder(s) disabled.
```

**Call ke baad — enforcement dikhao (ye asli punchline hai):**

```powershell
uv run python src/outbound.py --list
```

→ status column ab `OPTED OUT`.

```powershell
uv run python src/outbound.py --reminder-id 1
```

→ dial hone se pehle hi mana:

```
Reminder 1 is opted out - Harshit asked us to stop calling.
```

Opt-out ek polite jawab nahi hai — dialer khud refuse karta hai.

---

## Post-recording screens (30 seconds, video ke end mein)

Scheduler ka due-scan, bina dial kiye:

```powershell
uv run python src/scheduler.py --once --dry-run
```

Test suite (outcome/retry logic ka proof, koi trunk nahi chahiye):

```powershell
uv run pytest tests/test_day6_outbound.py -q
```

---

## Gotchas

| Problem | Kya karna hai |
|---|---|
| Call connect hui par Aanya chup | Worker `registered worker` print hone se pehle dial kiya tha. ~50s ruko, dubara dial karo. |
| Take 3 ke baad koi call nahi ja rahi | Wahi to point hai. Naya reminder register karo (Section 0) — `reminder_id` 2 milega. |
| Phone hi nahi bajta | Twilio trial sirf **Verified Caller IDs** ko dial karta hai. Number `Phone Numbers → Verified Caller IDs` mein hona chahiye. |
| Time galat boli ja rahi hai | `_spoken_time()` clock ko shabdon mein badalta hai — Murf "20:00" ko "twenty colon zero zero" padhta hai. |

---

## Recording ke baad (security)

Twilio SIP credential password rotate karo: **Elastic SIP Trunking → Credential lists →
Aanya → Change Password**, phir `outbound-trunk.json` update karke
`lk sip outbound create outbound-trunk.json` dubara chalao.

