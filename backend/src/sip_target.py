"""
Where an outbound call is actually sent - a PSTN number or a plain SIP account.

Day 6 dialled E.164 numbers through a Twilio Elastic SIP Trunk. On this project
that path is unusable twice over: the trunk never reports the pickup
(`sip.callStatus` stays 'dialing' for the whole life of a call that was genuinely
answered - proven with src/sip_probe.py, no agent in the room), and the trial
account will only dial numbers registered as Verified Caller IDs.

So the dialer now also speaks plain SIP, which is exactly what Linphone's free
sip.linphone.org account gives you: the "phone" is the Linphone app signed into
that account, running on the same handset you were trying to reach anyway. No
carrier, no verified-number list, and the pickup comes from a SIP endpoint that
does send a 200 OK.

Only the *destination* changes here. The identity the callee joins the room under
is still the reminder's own phone_number, because agent.py waits for that exact
identity (`ctx.wait_for_participant(identity=phone_number)`). Routing a call to a
SIP account must not rename the participant, or the agent would sit waiting for
somebody who is already in the room.

Two ways to point at Linphone, in order of preference:

  1. A stored LiveKit trunk - `lk sip outbound create linphone-trunk.json`, then
     put the printed ST_... id in SIP_LINPHONE_TRUNK_ID.
  2. No trunk at all: set SIP_LINPHONE_AUTH_USERNAME / _PASSWORD and every dial
     carries its own inline trunk config. Handy when re-running the CLI is not an
     option, and it keeps the credential in .env.local instead of a JSON file.

One thing a carrier trunk hides: Linphone requires encrypted media and rejects
LiveKit's default plain-RTP offer with '488 Not acceptable here'. The SIP leg
therefore offers SRTP - see DEFAULT_SIP_MEDIA_ENCRYPTION.
"""

import os
from dataclasses import dataclass
from typing import Any, Optional

# SIP_PROVIDER values. 'auto' decides per destination: anything that parses as a
# SIP address goes over SIP, anything that looks like a phone number goes to the
# PSTN trunk. Set it explicitly to route existing phone-number reminders to a SIP
# account without editing the database.
PROVIDER_AUTO = "auto"
PROVIDER_LINPHONE = "linphone"
PROVIDER_PSTN = "pstn"

# Other carriers are spelled differently but mean the same thing as 'pstn' here.
_PSTN_ALIASES = frozenset({"pstn", "twilio", "plivo", "telnyx", "number", "phone"})
_SIP_ALIASES = frozenset({"linphone", "sip", "softphone", "zoiper"})

DEFAULT_LINPHONE_DOMAIN = "sip.linphone.org"

# A Linphone account on sip.linphone.org requires encrypted media, and answers an
# unencrypted offer with '488 Not acceptable here' - which is what this project hit
# the first time the INVITE actually reached the app. LiveKit offers plain RTP
# unless told otherwise, so the SIP leg asks for SRTP by default. 'allow' rather
# than 'require' because it still falls back for an endpoint that cannot do it.
DEFAULT_SIP_MEDIA_ENCRYPTION = "allow"

# Spellings of SIP_MEDIA_ENCRYPTION, mapped onto the protobuf enum names.
_MEDIA_ENCRYPTION_ALIASES = {
    "allow": "SIP_MEDIA_ENCRYPT_ALLOW",
    "optional": "SIP_MEDIA_ENCRYPT_ALLOW",
    "srtp": "SIP_MEDIA_ENCRYPT_ALLOW",
    "require": "SIP_MEDIA_ENCRYPT_REQUIRE",
    "required": "SIP_MEDIA_ENCRYPT_REQUIRE",
    "disable": "SIP_MEDIA_ENCRYPT_DISABLE",
    "disabled": "SIP_MEDIA_ENCRYPT_DISABLE",
    "none": "SIP_MEDIA_ENCRYPT_DISABLE",
    "off": "SIP_MEDIA_ENCRYPT_DISABLE",
}


KIND_PSTN = "pstn"
KIND_SIP = "sip"

# A phone number this short is a typo, not a destination; this long is not E.164.
_MIN_PHONE_DIGITS = 7
_MAX_PHONE_DIGITS = 15

# Punctuation people type into a phone number that carries no meaning.
_PHONE_NOISE = " -()./\u00a0\t"


class SipConfigError(RuntimeError):
    """Telephony is misconfigured for the destination being dialled.

    Raised before any dial is attempted, with the exact env var to set - a wrong
    trunk produces a 403 from the far end forty seconds later, which is a much
    worse way to learn the same thing.
    """


@dataclass(frozen=True)
class SipAddress:
    """A SIP address split into its parts. Empty user means 'not a SIP address'."""

    user: str
    domain: str
    had_scheme: bool


@dataclass(frozen=True)
class SipSettings:
    """Everything the resolver reads from the environment, in one place.

    Passed explicitly by the tests; built from os.environ at dial time in
    production. Read at call time rather than import time so a demo can flip
    SIP_PROVIDER without restarting the worker.
    """

    provider: str = PROVIDER_AUTO
    pstn_trunk_id: str = ""
    from_number: str = ""
    linphone_trunk_id: str = ""
    linphone_domain: str = DEFAULT_LINPHONE_DOMAIN
    linphone_user: str = ""
    auth_username: str = ""
    auth_password: str = ""
    transport: str = ""
    from_user: str = ""
    call_to_includes_domain: bool = False
    media_encryption: str = ""


@dataclass(frozen=True)
class DialTarget:
    """A resolved destination, ready to be copied onto a dial request.

    `identity` is deliberately separate from `sip_call_to`: the first is who the
    room calls them, the second is where the INVITE goes. On the PSTN path they
    are the same string; on the SIP path they are not.
    """

    kind: str
    sip_call_to: str
    identity: str
    caller_id: str
    trunk_id: str
    inline_trunk: Optional[Any]
    domain: str
    label: str
    note: str = ""
    # None leaves LiveKit's default (unencrypted RTP) in place; the SIP leg sets it.
    media_encryption: Optional[int] = None

    @property
    def is_sip(self) -> bool:
        return self.kind == KIND_SIP


def _flag(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> SipSettings:
    """Read the SIP routing configuration out of the environment.

    Note that .env.local is loaded by the entrypoint (outbound.py / sip_probe.py),
    not here, so importing this module has no side effects.
    """
    return SipSettings(
        provider=(os.getenv("SIP_PROVIDER", "") or PROVIDER_AUTO).strip().lower(),
        pstn_trunk_id=os.getenv("SIP_OUTBOUND_TRUNK_ID", "").strip(),
        from_number=os.getenv("SIP_FROM_NUMBER", "").strip(),
        linphone_trunk_id=os.getenv("SIP_LINPHONE_TRUNK_ID", "").strip(),
        linphone_domain=(
            os.getenv("SIP_LINPHONE_DOMAIN", "").strip() or DEFAULT_LINPHONE_DOMAIN
        ),
        linphone_user=os.getenv("SIP_LINPHONE_USER", "").strip(),
        auth_username=os.getenv("SIP_LINPHONE_AUTH_USERNAME", "").strip(),
        auth_password=os.getenv("SIP_LINPHONE_AUTH_PASSWORD", ""),
        transport=os.getenv("SIP_LINPHONE_TRANSPORT", "").strip().lower(),
        from_user=os.getenv("SIP_LINPHONE_FROM_USER", "").strip(),
        call_to_includes_domain=_flag(os.getenv("SIP_CALL_TO_INCLUDES_DOMAIN", "")),
        media_encryption=os.getenv("SIP_MEDIA_ENCRYPTION", "").strip().lower(),
    )


def parse_sip_address(raw: str) -> SipAddress:
    """Split 'sip:alice@sip.linphone.org;transport=tls' into its parts.

    Tolerant on purpose: the destination can arrive from a database row, a CLI
    flag or an env var, so every spelling a human might type is accepted -
    angle brackets, a sips: scheme, a port, URI parameters, or just 'alice'.
    """
    text = raw.strip()
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()

    had_scheme = False
    lowered = text.lower()
    for scheme in ("sip:", "sips:"):
        if lowered.startswith(scheme):
            text = text[len(scheme) :]
            had_scheme = True
            break

    # Everything after ';' or '?' is transport/header decoration, not the address.
    text = text.split(";", 1)[0].split("?", 1)[0].strip()

    user, separator, domain = text.partition("@")
    if separator:
        # A port belongs to the trunk's hostname, never to the address we dial.
        domain = domain.split(":", 1)[0]
    return SipAddress(
        user=user.strip(), domain=domain.strip().lower(), had_scheme=had_scheme
    )


def looks_like_phone_number(raw: str) -> bool:
    """True for something dialable on the PSTN: E.164, or bare national digits."""
    text = raw.strip()
    if not text:
        return False
    for char in _PHONE_NOISE:
        text = text.replace(char, "")
    if text.startswith("+"):
        text = text[1:]
    return text.isdigit() and _MIN_PHONE_DIGITS <= len(text) <= _MAX_PHONE_DIGITS


def is_sip_destination(raw: str) -> bool:
    """True when this destination is a SIP account rather than a phone number.

    A bare 'alice' counts as SIP: nobody stores a username in a reminder row by
    accident, whereas a phone number always survives looks_like_phone_number.
    """
    address = parse_sip_address(raw)
    if not address.user:
        return False
    if address.had_scheme or address.domain:
        return True
    return not looks_like_phone_number(raw)


def _host_only(hostname: str) -> str:
    """The host part of a trunk hostname, with any :port removed."""
    return hostname.strip().lower().split(":", 1)[0]


def _transport_enum(name: str) -> Optional[int]:
    """Map SIP_LINPHONE_TRANSPORT onto the protobuf enum, or None to leave it auto."""
    if name in ("", "auto"):
        return None

    from livekit.protocol import sip as sip_proto

    try:
        return sip_proto.SIPTransport.Value(f"SIP_TRANSPORT_{name.upper()}")
    except ValueError as exc:
        raise SipConfigError(
            f"SIP_LINPHONE_TRANSPORT='{name}' is not a SIP transport. "
            "Use udp, tcp, tls, or leave it blank for auto."
        ) from exc


def _media_encryption_enum(name: str) -> Optional[int]:
    """Map SIP_MEDIA_ENCRYPTION onto the protobuf enum, or None to leave it alone.

    LiveKit defaults to offering unencrypted RTP. Linphone accounts require SRTP
    and reject a plain offer with 488, so the SIP leg overrides the default -
    see DEFAULT_SIP_MEDIA_ENCRYPTION.
    """
    if name in ("", "default"):
        return None

    enum_name = _MEDIA_ENCRYPTION_ALIASES.get(name)
    if enum_name is None:
        raise SipConfigError(
            f"SIP_MEDIA_ENCRYPTION='{name}' is not a media encryption mode. Use "
            "allow (offer SRTP, accept plain), require (SRTP or nothing), or "
            "disable (never encrypt)."
        )

    from livekit.protocol import sip as sip_proto

    return sip_proto.SIPMediaEncryption.Value(enum_name)


def build_inline_trunk(settings: SipSettings) -> Optional[Any]:
    """Build the per-request trunk config, or None if a stored trunk should be used.

    LiveKit's documented route to "an arbitrary SIP endpoint" is to hand the dial
    request its own trunk instead of a stored ST_ id. That is what makes the
    Linphone switch a .env.local edit rather than another CLI round trip - but it
    needs the account credential, because sip.linphone.org answers an
    unauthenticated INVITE with 403 Forbidden.
    """
    if not (settings.auth_username and settings.auth_password):
        return None

    from livekit import api

    config = api.SIPOutboundConfig(
        hostname=settings.linphone_domain,
        auth_username=settings.auth_username,
        auth_password=settings.auth_password,
    )
    transport = _transport_enum(settings.transport)
    if transport is not None:
        config.transport = transport
    return config


def _wants_sip(destination: str, settings: SipSettings) -> bool:
    """Decide which leg this destination takes. SIP_PROVIDER overrides the shape."""
    if settings.provider in _SIP_ALIASES:
        return True
    if settings.provider in _PSTN_ALIASES:
        return False
    return is_sip_destination(destination)


def _resolve_pstn(destination: str, identity: str, settings: SipSettings) -> DialTarget:
    if is_sip_destination(destination):
        raise SipConfigError(
            f"'{destination}' is a SIP address, but SIP_PROVIDER='{settings.provider}' "
            "sends every call to the PSTN trunk. Set SIP_PROVIDER=linphone (or leave "
            "it unset for auto) to dial SIP accounts."
        )
    if not settings.pstn_trunk_id:
        raise SipConfigError(
            "SIP_OUTBOUND_TRUNK_ID is not set in .env.local, so there is no PSTN "
            "trunk to dial through. Either create one - "
            "`lk sip outbound create outbound-trunk.json` - or switch to a SIP "
            "account with SIP_PROVIDER=linphone (see .env.example)."
        )

    number = destination.strip()
    return DialTarget(
        kind=KIND_PSTN,
        sip_call_to=number,
        identity=identity,
        caller_id=settings.from_number,
        trunk_id=settings.pstn_trunk_id,
        inline_trunk=None,
        domain="",
        label=f"phone number {number}",
        # A carrier trunk is happy with plain RTP, so this stays at LiveKit's
        # default unless SIP_MEDIA_ENCRYPTION says otherwise.
        media_encryption=_media_encryption_enum(settings.media_encryption),
    )


def _sip_account(raw: str, settings: SipSettings) -> tuple[str, str]:
    """Split a configured/stored SIP destination into (user, domain)."""
    address = parse_sip_address(raw)
    return address.user, (address.domain or _host_only(settings.linphone_domain))


def _resolve_sip(destination: str, identity: str, settings: SipSettings) -> DialTarget:
    note = ""
    if is_sip_destination(destination):
        user, domain = _sip_account(destination, settings)
    else:
        # A reminder row holding a phone number, deliberately routed to SIP. This is
        # the Twilio-is-down case: the database still says +91..., but the call has
        # to land in the Linphone app instead.
        if not settings.linphone_user:
            raise SipConfigError(
                f"SIP_PROVIDER='{settings.provider}' routes calls to a SIP account, "
                f"but '{destination}' is a phone number and SIP_LINPHONE_USER is not "
                "set, so there is nothing to dial. Put your Linphone username in "
                "SIP_LINPHONE_USER, or store the SIP address in the reminder itself."
            )
        user, domain = _sip_account(settings.linphone_user, settings)
        note = (
            f"'{destination}' is a phone number; SIP_PROVIDER={settings.provider} "
            f"rings the SIP account '{user}@{domain}' instead"
        )

    if not user:
        raise SipConfigError(f"'{destination}' has no SIP user part to dial.")

    trunk_id = settings.linphone_trunk_id
    inline_trunk = None if trunk_id else build_inline_trunk(settings)
    if not trunk_id and inline_trunk is None:
        raise SipConfigError(
            f"Nothing is configured to route SIP calls to {domain}. Either register a "
            "trunk once - `cp linphone-trunk.example.json linphone-trunk.json`, fill "
            "it in, `lk sip outbound create linphone-trunk.json`, then set "
            "SIP_LINPHONE_TRUNK_ID=ST_... - or set SIP_LINPHONE_AUTH_USERNAME and "
            "SIP_LINPHONE_AUTH_PASSWORD and the dial will carry its own trunk."
        )

    if inline_trunk is not None and domain != _host_only(settings.linphone_domain):
        # sip_call_to carries only the user part, so the trunk's hostname is what
        # actually decides where the INVITE goes. A mismatch here means the call
        # would quietly ring the wrong domain's account of the same name.
        note = (f"{note}; " if note else "") + (
            f"destination domain '{domain}' does not match the trunk hostname "
            f"'{settings.linphone_domain}' - the trunk wins"
        )

    call_to = f"{user}@{domain}" if settings.call_to_includes_domain else user
    encryption = _media_encryption_enum(
        settings.media_encryption or DEFAULT_SIP_MEDIA_ENCRYPTION
    )
    return DialTarget(
        kind=KIND_SIP,
        sip_call_to=call_to,
        identity=identity,
        # The From user must be the account we authenticate as; sip.linphone.org
        # answers 403 to an INVITE claiming to be from someone else's address, and
        # a Twilio E.164 caller ID is exactly that. So SIP_FROM_NUMBER is ignored.
        caller_id=settings.from_user or settings.auth_username,
        trunk_id=trunk_id,
        inline_trunk=inline_trunk,
        domain=domain,
        label=f"SIP account {user}@{domain}",
        note=note,
        media_encryption=encryption,
    )


def resolve_dial_target(
    destination: str,
    identity: str = "",
    settings: Optional[SipSettings] = None,
) -> DialTarget:
    """Work out where to send this call, and how to identify the callee in the room.

    `destination` is whatever the reminder holds - an E.164 number, a bare SIP
    username, or a full sip: URI. `identity` defaults to the destination, and
    should be left alone for reminder calls: agent.py waits for the reminder's
    phone_number as the participant identity.

    Raises SipConfigError, before any network call, when the configuration cannot
    deliver this destination.
    """
    settings = settings or load_settings()
    destination = (destination or "").strip()
    if not destination:
        raise SipConfigError(
            "No destination to dial - this reminder has an empty phone_number."
        )

    known_providers = {PROVIDER_AUTO} | _SIP_ALIASES | _PSTN_ALIASES
    if settings.provider not in known_providers:
        raise SipConfigError(
            f"SIP_PROVIDER='{settings.provider}' is not recognised. Use 'auto' "
            "(decide per destination), 'linphone' (always SIP) or 'pstn'."
        )

    identity = (identity or destination).strip()
    if _wants_sip(destination, settings):
        return _resolve_sip(destination, identity, settings)
    return _resolve_pstn(destination, identity, settings)


def apply_to_request(request: Any, target: DialTarget) -> Any:
    """Copy a resolved target onto a CreateSIPParticipantRequest.

    Kept here so the dialer and the probe cannot drift apart on which fields a
    SIP-account call sets - the difference between the two legs is three fields,
    and getting one wrong fails at the far end rather than locally.
    """
    request.sip_call_to = target.sip_call_to
    request.participant_identity = target.identity

    if target.trunk_id:
        request.sip_trunk_id = target.trunk_id
    elif target.inline_trunk is not None:
        request.trunk.CopyFrom(target.inline_trunk)

    if target.caller_id:
        request.sip_number = target.caller_id

    # Left unset, LiveKit offers plain RTP and a Linphone account answers 488.
    if target.media_encryption is not None:
        request.media_encryption = target.media_encryption
    return request


def describe_routing(settings: Optional[SipSettings] = None) -> str:
    """One line for the logs saying where calls are going. Never prints a password."""
    settings = settings or load_settings()
    if settings.provider in _PSTN_ALIASES:
        route = "PSTN only"
    elif settings.provider in _SIP_ALIASES:
        route = f"SIP only ({settings.linphone_domain})"
    else:
        route = f"auto (PSTN numbers, or SIP for {settings.linphone_domain} addresses)"

    if settings.linphone_trunk_id:
        trunk = f"SIP trunk {settings.linphone_trunk_id}"
    elif settings.auth_username and settings.auth_password:
        trunk = f"inline SIP trunk, authenticating as '{settings.auth_username}'"
    elif settings.pstn_trunk_id:
        trunk = f"PSTN trunk {settings.pstn_trunk_id}"
    else:
        trunk = "no trunk configured"

    # Worth a few characters in every log line: a 488 from the far end is otherwise
    # indistinguishable from a codec problem.
    if settings.provider in _PSTN_ALIASES:
        encryption = settings.media_encryption or "default"
    else:
        encryption = settings.media_encryption or DEFAULT_SIP_MEDIA_ENCRYPTION
    return f"SIP routing: {route}; {trunk}; media encryption {encryption}."
