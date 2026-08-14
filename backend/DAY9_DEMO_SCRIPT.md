# Day 9 — Demo Recording Script: Clinic & Appointment Specialist Handoff

Aanya ab monolithic nahi rahi — complex healthcare tasks ke liye specialized agents mein **handoff** karti hai.
Is video mein Aanya se Clinic & Appointment Specialist tak seamless handoff, live PHC lookup, appointment booking (`APT-2001`), aur wapas Aanya tak return-transfer dikhana hai.

Video ka main point: **General advisor (Aanya) se clinic specialist tak clean context transfer — har turn survive karta hai, pura 100-line prompt nahi, aur specialist introduce hoke appointment book karta hai.**

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

### Terminal 2 — frontend

```powershell
cd frontend
pnpm dev
```

### Browser

Open `http://localhost:3000`

---

## Take 1 — PHC Lookup & Specialist Handoff (Primary Demo)

**Home → "Get started" click karo.**

1. Aanya greet karegi:
   > *"नमस्ते! मैं आन्या हूँ, आपकी हेल्थ एडवाइजर। आज आपकी क्या सहायता कर सकती हूँ?"*

2. Ab bolo:
   > **"मुझे लखनऊ में अपने पास का सबसे करीबी PHC (प्राथमिक स्वास्थ्य केंद्र) जानना है।"**
   > *(Mujhe Lucknow mein apne paas ka sabse kareebi PHC jaanna hai.)*

3. **Expected Behavior (Handoff trigger):**
   - Aanya turant recognize karegi ki ye clinic lookup request hai.
   - Aanya bolega: *"मैं आपको हमारे क्लिनिक और अपॉइंटमेंट विशेषज्ञ से कनेक्ट कर रही हूँ..."*
   - `transfer_to_clinic_specialist` tool call hoga.
   - Specialist ka `on_enter` introduce karega:
     > *"नमस्ते! मैं क्लिनिक और अपॉइंटमेंट विशेषज्ञ हूँ। मैं आपको नजदीकी प्राथमिक स्वास्थ्य केंद्र खोजने और अपॉइंटमेंट बुक करने में मदद कर सकता हूँ..."*
   - Specialist live OpenStreetMap / Health Registry se Lucknow ke nearest PHC/Civil Hospital ka naam aur address batayega.

---

## Take 2 — Appointment Booking Flow (`APT-2001`)

1. PHC sunne ke baad bolo:
   > **"क्या आप सिविल हॉस्पिटल में कल सुबह 10:30 बजे डॉक्टर परामर्श के लिए मेरी अपॉइंटमेंट बुक कर सकते हैं? मेरा नाम हर्षित है और नंबर 9454535137 है।"**

2. **Expected Behavior (Booking Tool):**
   - Specialist `book_clinic_appointment` call karega.
   - Reason automatically scrub hoga (secrets/OTPs redacted).
   - Database mein `clinic_appointments` table mein row insert hogi.
   - Reference ID generate hoga: `APT-2001`.
   - Specialist reference number **digit-by-digit** Hindi mein bolega:
     > *"आपकी अपॉइंटमेंट सिविल हॉस्पिटल लखनऊ में 16 अगस्त को सुबह 10:30 बजे कन्फर्म हो गई है। आपका रेफरेंस नंबर है — ए पी टी दो शून्य शून्य एक। कृपया 10 मिनट पहले पहुँचें।"*

---

## Take 3 — Return to Health Advisor (Two-way Transfer)

1. Appointment book hone ke baad caller symptom question pooche:
   > **"धन्यवाद! मुझे सिरदर्द और थकान भी लग रही है, उसके लिए क्या घरेलू उपाय करूँ?"**

2. **Expected Behavior (Boundary & Return Handoff):**
   - Specialist boundary maintain karega — wo medical advice nahi deta.
   - Specialist bolega: *"स्वास्थ्य और घरेलू उपचार के लिए मैं आपको वापस आन्या के पास ट्रांसफर कर रहा हूँ..."*
   - `return_to_health_advisor` tool call hoga.
   - Aanya conversation context ke sath resume karegi, hydration aur rest ki wellness advice degi.
   - Caller bolega: *"बहुत बहुत धन्यवाद आन्या!"*
   - Aanya farewell degi aur `end_call` se call smoothly disconnect ho jayegi.

---

## Key Architecture Points (Camera pe bolne ke liye)

1. **Clean Context Handoff (`chat_ctx.copy(exclude_instructions=True)`):**
   - Handoff ke time conversation history survive karti hai taaki specialist ko context dobara na mangna pade, lekin Aanya ka 100-line general prompt specialist ke domain prompt se replace ho jaata hai.
2. **Specialized Scopes:**
   - Aanya = General health triage, wellness, reminders, escalations.
   - Clinic Specialist = Facility lookup (`lookup_nearest_phc`), appointments (`book_clinic_appointment`), listings.
3. **Spoken Reference Digit-by-Digit:**
   - Reference ID `APT-2001` Latin abbreviations ke bajaye Hindi phonetics (*"ए पी टी दो शून्य शून्य एक"*) mein bola jaata hai taaki caller aasaani se note kar sake.
4. **Single Analytics Lifecycle:**
   - Mid-call agent handoff ke baad bhi `call_analytics` table mein call session level pe track hoti hai — exactly ek clean analytics row banti hai.

---

## Post-recording Test Verification (Terminal proof)

```powershell
cd backend
uv run pytest tests/test_day9_handoff.py -v
```

Aur database mein booked appointments verify karo:

```powershell
uv run python -c "import appointments; print(appointments.list_appointments())"
```

---

## Gotchas & Tips

| Problem | Solution |
|---------|----------|
| Handoff ke baad specialist silent | Check karo `on_enter` mein `await self.session.say(intro)` execute ho raha hai |
| Reference number "APT" spell na ho | `_spoken_reference` letter map mein 'A' and 'T' Devanagari mapped hone chahiye (`ए`, `टी`) |
| Symptoms poochne par specialist fas jaaye | `return_to_health_advisor` tool ensure karta hai ki user medical advice ke liye Aanya ke paas wapas aa sake |
| Cold start silence on transfer | Single session reuse hoti hai, naya process spawn nahi hota to latency sub-second rehti hai |
