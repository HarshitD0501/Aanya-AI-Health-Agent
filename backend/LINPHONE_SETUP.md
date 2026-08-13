# Ringing a Linphone account instead of a phone number

Twilio is not working on this project. Two separate reasons, both proven:

- **The pickup is never reported.** `src/sip_probe.py` dials with no agent in the
  room and prints every attribute LiveKit has. On this Elastic SIP Trunk
  `sip.callStatus` stays `dialing` for the whole life of a call that was answered
  and talked on. The agent gate that waits for `active` therefore never opens.
- **The trial account will only dial Verified Caller IDs**, so any number that is
  not on that list never rings at all.

Linphone's free SIP service sidesteps both. There is no carrier in the path: the
"phone" is the Linphone app signed into a `sip.linphone.org` account, running on
the same handset. It answers with a real SIP 200 OK, and it will accept a call
from anybody.

## 1. Create the account

Install Linphone (Android / iOS / desktop) → **Create account** → pick a
username. You end up with `username@sip.linphone.org` and a password. Sign in and
leave the app running; that is the device that will ring.

## 2. Point LiveKit at it

Two ways. The second needs no CLI.

**A stored trunk (preferred):**

```bash
cp linphone-trunk.example.json linphone-trunk.json   # then fill it in
lk sip outbound create linphone-trunk.json           # prints ST_...
```

```dotenv
SIP_PROVIDER=linphone
SIP_LINPHONE_TRUNK_ID=ST_xxxxxxxxxxxx
SIP_LINPHONE_USER=your_linphone_username
```

**An inline trunk (no CLI, credential stays in `.env.local`):**

```dotenv
SIP_PROVIDER=linphone
SIP_LINPHONE_DOMAIN=sip.linphone.org
SIP_LINPHONE_USER=your_linphone_username
SIP_LINPHONE_AUTH_USERNAME=your_linphone_username
SIP_LINPHONE_AUTH_PASSWORD=your_linphone_password
```

Every dial then carries its own trunk config, which is LiveKit's documented route
to an arbitrary SIP endpoint.

`linphone-trunk.json` holds a password — it is gitignored, like
`outbound-trunk.json`. Never commit either, and rotate the password after any
demo recording that showed the file.

## 3. Check the routing without dialling

```bash
uv run python src/outbound.py --routing --to +919454535137
```

That prints the resolved destination and exits. `SIP_PROVIDER=linphone` maps a
reminder still holding a phone number onto `SIP_LINPHONE_USER`, so existing rows
keep working untouched — the mapping is logged as a warning on every call so it is
never silent.

## 4. Place a call

```bash
# whatever the reminder row says, routed by SIP_PROVIDER
uv run python src/outbound.py --reminder-id 3

# an explicit SIP address ignores SIP_PROVIDER and always goes over SIP
uv run python src/outbound.py --to aanya-demo@sip.linphone.org --medicine Metformin --name Ramesh
```

Answer in the Linphone app. Aanya speaks once the answer lands.

## 5. If it does not connect

Run the probe first — it isolates the trunk from the agent:

```bash
uv run python src/sip_probe.py --to aanya-demo@sip.linphone.org
```

| Symptom | Cause | Fix |
| --- | --- | --- |
| `403 Forbidden` | The INVITE was unauthenticated, or its From user is not the account. | Set `SIP_LINPHONE_AUTH_USERNAME` / `_PASSWORD`. `SIP_FROM_NUMBER` is deliberately ignored on this path. |
| `404 Not Found` | Wrong username, or the domain came from the trunk rather than the address. | Check `SIP_LINPHONE_USER`; the trunk's `address` decides the domain, not the destination you type. |
| `488 Not acceptable here` | Media negotiation. LiveKit offers plain RTP; Linphone requires encryption. | Handled — the SIP leg offers SRTP by default. If the app is set to *mandatory* encryption, use `SIP_MEDIA_ENCRYPTION=require`; if to *none*, `disable`. |
| `480` / `408`, app never rings | The app is not registered. | Open Linphone, confirm it says *Connected*. Background-killed apps do not ring. |
| Nothing at all, no SIP status | The trunk refused the transport. | Try `SIP_LINPHONE_TRANSPORT=tls` with `SIP_LINPHONE_DOMAIN=sip.linphone.org:5061`, or `tcp` on 5060. |
| Rings, answered, still silent | No worker took the dispatch. | The dialer logs `NOBODY IN THE ROOM BUT THE CALLEE`. Start `uv run python src/agent.py dev`. |

Going back to a phone number is one line: `SIP_PROVIDER=pstn` (or unset it, and
store E.164 numbers in the reminders). Nothing else changes.
