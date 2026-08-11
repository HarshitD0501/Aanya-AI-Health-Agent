# script.md — Aanya se kya baat karni hai (Day 6)

Ye recording ke waqt saamne rakhne wali dialogue sheet hai. Har row mein: Aanya kya
bolegi, aur uske jawab mein aap kya bologe. **Devanagari line bolni hai**; italic Roman
version sirf pronunciation ke liye hai.

Setup, dial commands aur troubleshooting ke liye → [`DAY6_DEMO_SCRIPT.md`](DAY6_DEMO_SCRIPT.md)

---

## Do rules

1. **Phone uthate hi 1 second ruko.** Aanya pehle bolti hai — opening line code se aati
   hai, LLM se nahi.
2. **Sentence poora karke ruko.** Turn detection pause pe trigger hota hai; beech mein
   ruk gaye to wo aapki baat khatam samajh legi.

Takes ka order badalna mat — **opt-out sabse last**, kyunki wo reminder ko permanently
disable kar deta hai.

---

## Take 1 — Dose li hui hai (happy path)

Dial: `uv run python src/outbound.py --reminder-id 1`

| Aanya | Aap bolo |
|---|---|
| नमस्ते Harshit जी, मैं आन्या बोल रही हूँ — आपकी हेल्थ रिमाइंडर सेवा से… अगर आप ये कॉल बंद करवाना चाहें, तो बस कहिए "रिमाइंडर बंद करें"… क्या आपने आज दवा ले ली है? | **"हाँ, ले ली है। खाने के बाद ली थी।"**<br>*"Haan, le li hai. Khaane ke baad li thi."* |
| अच्छा लगा सुनकर… कल भी इसी समय याद दिला दूँगी। | **"ठीक है, धन्यवाद आन्या।"**<br>*"Theek hai, dhanyavaad Aanya."* |

Terminal 1 mein dikhega: `Reminder 1: medicine taken.` — ye `confirm_medicine_taken` tool
fire hone ka proof hai.

---

## Take 2 — Dose bhool gaye + health sawaal

Dial: `uv run python src/outbound.py --reminder-id 1`

| Aanya | Aap bolo |
|---|---|
| …क्या आपने आज दवा ले ली है? | **"नहीं, आज भूल गया था।"**<br>*"Nahi, aaj bhool gaya tha."* |
| कोई बात नहीं, अभी ले लीजिए — खाने के बाद एक टैबलेट। | **"अभी ले लेता हूँ। एक बात पूछनी थी — इस दवा के साथ चक्कर आते हैं क्या?"**<br>*"Abhi le leta hoon. Ek baat poochhni thi — is dawa ke saath chakkar aate hain kya?"* |
| Health guidance degi (Day 1–5 advisor persona) | **"मेरे पास सबसे करीब PHC कहाँ है?"**<br>*"Mere paas sabse kareeb PHC kahan hai?"* |
| PHC ka naam + doori batayegi (`lookup_nearest_phc`) | **"ठीक है, धन्यवाद।"**<br>*"Theek hai, dhanyavaad."* |

Terminal 1: `Reminder 1: medicine not_taken.` — dial outcome phir bhi `answered` rahega.
Call uthana aur dawa lena do alag cheezein hain, aur schema mein bhi alag fields hain.

---

## Take 3 — Opt-out (SABSE LAST)

Dial: `uv run python src/outbound.py --reminder-id 1`

| Aanya | Aap bolo |
|---|---|
| Opening bol rahi hai — **beech mein hi interrupt karo** | **"रिमाइंडर बंद करें।"**<br>*"Reminder band karein."* |
| तुरंत confirm karegi, disturbance ke liye sorry bolegi, farewell — **na wajah poochhegi, na convince karegi** | **"धन्यवाद।"**<br>*"Dhanyavaad."* |

Terminal 1: `Opt-out honoured for '+91…': 1 reminder(s) disabled.`

Call ke baad ye do commands recording mein zaroor dikhao — asli punchline yahi hai:

```powershell
uv run python src/outbound.py --list          # status: OPTED OUT
uv run python src/outbound.py --reminder-id 1 # dial hone se pehle hi mana kar degi
```

---

## Optional lines (extra footage ke liye)

| Kya dikhana hai | Bolo |
|---|---|
| Memory save (Day 4) | **"मेरा नाम हर्षित है, मुझे शुगर की दिक्कत है — याद रख लीजिए।"**<br>*"Mera naam Harshit hai, mujhe sugar ki dikkat hai — yaad rakh lijiye."* |
| Helpline tool | **"कोई मेडिकल हेल्पलाइन नंबर बता दीजिए।"**<br>*"Koi medical helpline number bata dijiye."* |
| Barge-in / interruption | Aanya ke bolte waqt: **"एक मिनट रुकिए।"**<br>*"Ek minute rukiye."* |
| Call end tool | **"बस इतना ही, धन्यवाद। फ़ोन रख दीजिए।"**<br>*"Bas itna hi, dhanyavaad. Phone rakh dijiye."* |

---

## Bonus take — reminder khud voice se set karvao (inbound / browser)

Ye Day 6 ka doosra half hai: reminder banane ke liye CLI ki zaroorat nahi, Aanya se
baat karke bhi ban jaata hai. Browser call ya inbound call pe kaam karta hai.

Aanya ek-ek sawaal poochhegi (teen cheezein: dawa, time, number). Number ke liye
**digit by digit boliye**, warna STT galat sun sakta hai.

| Aanya | Aap bolo |
|---|---|
| (aap shuru karo) | **"मुझे रोज़ दवा लेने की याद दिला दीजिए।"**<br>*"Mujhe roz dawa lene ki yaad dila dijiye."* |
| "किस दवा के लिए याद दिलाना है?" | **"शुगर की दवा।"**<br>*"Sugar ki dawa."* |
| "रोज़ किस समय कॉल करूँ?" | **"रात आठ बजे।"**<br>*"Raat aath baje."* |
| "किस नंबर पर कॉल करूँ?" | **"नौ चार पाँच चार पाँच तीन पाँच एक तीन सात।"**<br>*"Nau chaar paanch chaar paanch teen paanch ek teen saat."* |
| "आपका शुभ नाम?" (agar pehle se pata nahi) | **"हर्षित।"**<br>*"Harshit."* |
| Confirm karegi — dawa, time, number digit by digit, aur opt-out phrase | **"ठीक है, धन्यवाद।"**<br>*"Theek hai, dhanyavaad."* |

Terminal 1: `Reminder N scheduled in-conversation: ... language=Hindi.`

Proof screen ke liye:

```powershell
uv run python src/outbound.py --list
```

Isi tarah **"मेरे कौन-कौन से रिमाइंडर लगे हैं?"** poochhne par wo `list_my_reminders` se
padh kar sunaayegi.

---

## English mein karna ho

Reminder ko `--language English` se register karo. Tab opening "Hello Harshit, this is
Aanya calling from your health reminder service…" hogi, sawaal "Have you taken your
medicine today?" aur opt-out phrase **"stop the reminders"**.

| Take | Aap bolo |
|---|---|
| 1 | *"Yes, I took it after dinner."* |
| 2 | *"No, I forgot today. Does this medicine cause dizziness?"* |
| 3 | *"Stop the reminders."* |

