"""
Automated Unit Tests for the SIP destination resolver (Linphone shift).

Twilio's trunk on this project never reports a pickup and its trial will only
dial Verified Caller IDs, so outbound calls now go to a Linphone SIP account
instead. Everything about that decision lives in src/sip_target.py, and this file
pins the parts that are easy to break and expensive to debug over a live trunk:

  * which leg a destination takes, and how SIP_PROVIDER overrides it,
  * that the callee still joins under the reminder's phone_number - agent.py does
    `ctx.wait_for_participant(identity=phone_number)`, so renaming the
    participant would hang every routed call,
  * that a missing setting fails locally with the env var named, not as a 403
    from the far end forty seconds later,
  * that the password never reaches a log line.

No trunk, no network and no LiveKit connection: SipSettings is passed in
directly, and apply_to_request is checked against a real (never sent) request.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import sip_target
from livekit import api

LINPHONE_ADDRESS = "aanya-demo@sip.linphone.org"
PHONE = "+919454535137"


def _settings(**overrides) -> sip_target.SipSettings:
    """SipSettings with both legs configured, so a test can break one on purpose."""
    kwargs = {
        "provider": sip_target.PROVIDER_AUTO,
        "pstn_trunk_id": "ST_pstn_test",
        "from_number": "+17432245805",
        "linphone_user": "aanya-demo",
        "auth_username": "aanya-demo",
        "auth_password": "s3cret-do-not-log",
    }
    kwargs.update(overrides)
    return sip_target.SipSettings(**kwargs)


# ---------------------------------------------------------------------------
# Address parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "aanya-demo@sip.linphone.org",
        "sip:aanya-demo@sip.linphone.org",
        "SIPS:aanya-demo@sip.linphone.org",
        "<sip:aanya-demo@sip.linphone.org>",
        "sip:aanya-demo@sip.linphone.org:5061",
        "sip:aanya-demo@sip.linphone.org;transport=tls",
        "sip:aanya-demo@sip.linphone.org?X-Header=1",
        "  aanya-demo@SIP.Linphone.ORG  ",
    ],
)
def test_parse_sip_address_accepts_every_spelling(raw):
    """A human types a SIP address six different ways; all mean one account."""
    address = sip_target.parse_sip_address(raw)
    assert address.user == "aanya-demo"
    assert address.domain == "sip.linphone.org"


def test_parse_sip_address_bare_username_has_no_domain():
    address = sip_target.parse_sip_address("aanya-demo")
    assert address == sip_target.SipAddress(
        user="aanya-demo", domain="", had_scheme=False
    )


def test_parse_sip_address_scheme_is_remembered_without_a_domain():
    """'sip:alice' is still a SIP address even though the domain is missing."""
    assert sip_target.parse_sip_address("sip:alice").had_scheme is True


# ---------------------------------------------------------------------------
# Telling a phone number apart from a SIP account
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["+919454535137", "9454535137", "+91 94545-35137", "(0731) 250 4900", "+17432245805"],
)
def test_looks_like_phone_number_accepts_dialable_digits(raw):
    assert sip_target.looks_like_phone_number(raw) is True


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "12345",  # too short to be anybody's number
        "1234567890123456",  # longer than E.164 allows
        "aanya-demo",
        LINPHONE_ADDRESS,
        "+91945abc5137",
    ],
)
def test_looks_like_phone_number_rejects_everything_else(raw):
    assert sip_target.looks_like_phone_number(raw) is False


@pytest.mark.parametrize(
    "raw, expected",
    [
        (LINPHONE_ADDRESS, True),
        ("sip:aanya-demo@sip.linphone.org", True),
        ("sip:alice", True),
        ("aanya-demo", True),  # a bare username is never stored by accident
        (PHONE, False),
        ("9454535137", False),
        ("", False),
    ],
)
def test_is_sip_destination(raw, expected):
    assert sip_target.is_sip_destination(raw) is expected


# ---------------------------------------------------------------------------
# Routing: auto
# ---------------------------------------------------------------------------


def test_auto_sends_a_phone_number_to_the_pstn_trunk():
    target = sip_target.resolve_dial_target(PHONE, settings=_settings())
    assert target.kind == sip_target.KIND_PSTN
    assert target.is_sip is False
    assert target.sip_call_to == PHONE
    assert target.trunk_id == "ST_pstn_test"
    assert target.inline_trunk is None
    assert target.caller_id == "+17432245805"
    assert target.note == ""
    assert target.label == f"phone number {PHONE}"


def test_auto_sends_a_sip_address_over_sip_without_being_told():
    target = sip_target.resolve_dial_target(f"sip:{LINPHONE_ADDRESS}", settings=_settings())
    assert target.kind == sip_target.KIND_SIP
    assert target.is_sip is True
    assert target.sip_call_to == "aanya-demo"  # the trunk supplies the domain
    assert target.domain == "sip.linphone.org"
    assert target.label == f"SIP account {LINPHONE_ADDRESS}"
    assert target.note == ""


# ---------------------------------------------------------------------------
# Routing: forced by SIP_PROVIDER
# ---------------------------------------------------------------------------


def test_linphone_provider_remaps_a_phone_number_row_and_says_so_loudly():
    """The Twilio-is-down case: the database still says +91..., the app rings."""
    target = sip_target.resolve_dial_target(
        PHONE, settings=_settings(provider=sip_target.PROVIDER_LINPHONE)
    )
    assert target.kind == sip_target.KIND_SIP
    assert target.sip_call_to == "aanya-demo"
    # The remap must never be silent - an unexplained call to the wrong device is
    # indistinguishable from a bug.
    assert "is a phone number" in target.note
    assert LINPHONE_ADDRESS in target.note


def test_the_callee_keeps_the_reminders_phone_number_as_its_identity():
    """agent.py waits for this exact identity; routing must not rename anybody."""
    target = sip_target.resolve_dial_target(
        PHONE, settings=_settings(provider=sip_target.PROVIDER_LINPHONE)
    )
    assert target.identity == PHONE
    assert target.identity != target.sip_call_to


def test_an_explicit_identity_survives_resolution():
    target = sip_target.resolve_dial_target(
        LINPHONE_ADDRESS, identity=PHONE, settings=_settings()
    )
    assert target.identity == PHONE


@pytest.mark.parametrize("alias", ["sip", "softphone", "LINPHONE"])
def test_sip_aliases_all_force_the_sip_leg(alias):
    settings = _settings(provider=alias.lower())
    assert sip_target.resolve_dial_target(PHONE, settings=settings).is_sip is True


@pytest.mark.parametrize("alias", ["pstn", "twilio", "telnyx"])
def test_pstn_aliases_all_force_the_pstn_leg(alias):
    settings = _settings(provider=alias)
    assert sip_target.resolve_dial_target(PHONE, settings=settings).kind == "pstn"


# ---------------------------------------------------------------------------
# Misconfiguration fails here, not at the far end
# ---------------------------------------------------------------------------


def test_empty_destination_is_refused_before_dialling():
    with pytest.raises(sip_target.SipConfigError, match="empty phone_number"):
        sip_target.resolve_dial_target("   ", settings=_settings())


def test_unknown_provider_names_the_valid_values():
    with pytest.raises(sip_target.SipConfigError) as excinfo:
        sip_target.resolve_dial_target(PHONE, settings=_settings(provider="asterisk"))
    message = str(excinfo.value)
    assert "asterisk" in message
    assert "linphone" in message


def test_a_sip_address_under_a_pstn_provider_is_refused():
    with pytest.raises(sip_target.SipConfigError) as excinfo:
        sip_target.resolve_dial_target(
            LINPHONE_ADDRESS, settings=_settings(provider=sip_target.PROVIDER_PSTN)
        )
    assert "SIP_PROVIDER=linphone" in str(excinfo.value)


def test_a_phone_number_with_no_pstn_trunk_names_the_env_var():
    with pytest.raises(sip_target.SipConfigError) as excinfo:
        sip_target.resolve_dial_target(PHONE, settings=_settings(pstn_trunk_id=""))
    assert "SIP_OUTBOUND_TRUNK_ID" in str(excinfo.value)


def test_routing_a_number_to_sip_without_a_username_names_the_env_var():
    with pytest.raises(sip_target.SipConfigError) as excinfo:
        sip_target.resolve_dial_target(
            PHONE,
            settings=_settings(provider=sip_target.PROVIDER_LINPHONE, linphone_user=""),
        )
    assert "SIP_LINPHONE_USER" in str(excinfo.value)


def test_a_sip_call_with_neither_trunk_nor_credential_offers_both_fixes():
    with pytest.raises(sip_target.SipConfigError) as excinfo:
        sip_target.resolve_dial_target(
            LINPHONE_ADDRESS,
            settings=_settings(linphone_trunk_id="", auth_username="", auth_password=""),
        )
    message = str(excinfo.value)
    assert "SIP_LINPHONE_TRUNK_ID" in message
    assert "SIP_LINPHONE_AUTH_PASSWORD" in message


def test_an_unusable_transport_is_refused_by_name():
    with pytest.raises(sip_target.SipConfigError, match="not a SIP transport"):
        sip_target.resolve_dial_target(
            LINPHONE_ADDRESS, settings=_settings(transport="carrier-pigeon")
        )


# ---------------------------------------------------------------------------
# The trunk that carries the SIP leg
# ---------------------------------------------------------------------------


def test_a_stored_trunk_wins_over_the_inline_credential():
    """One ST_ id is cheaper than authenticating on every dial."""
    target = sip_target.resolve_dial_target(
        LINPHONE_ADDRESS, settings=_settings(linphone_trunk_id="ST_linphone")
    )
    assert target.trunk_id == "ST_linphone"
    assert target.inline_trunk is None


def test_without_a_stored_trunk_the_dial_carries_its_own():
    """sip.linphone.org 403s an unauthenticated INVITE, so the credential goes along."""
    target = sip_target.resolve_dial_target(LINPHONE_ADDRESS, settings=_settings())
    assert target.trunk_id == ""
    assert target.inline_trunk is not None
    assert target.inline_trunk.hostname == "sip.linphone.org"
    assert target.inline_trunk.auth_username == "aanya-demo"
    assert target.inline_trunk.auth_password == "s3cret-do-not-log"


def test_a_named_transport_reaches_the_inline_trunk():
    from livekit.protocol import sip as sip_proto

    trunk = sip_target.build_inline_trunk(
        _settings(transport="tls", linphone_domain="sip.linphone.org:5061")
    )
    assert trunk.transport == sip_proto.SIPTransport.SIP_TRANSPORT_TLS
    assert trunk.hostname == "sip.linphone.org:5061"


def test_the_from_user_is_the_account_never_the_twilio_caller_id():
    """A Twilio E.164 From is an address Linphone does not own - it answers 403."""
    target = sip_target.resolve_dial_target(LINPHONE_ADDRESS, settings=_settings())
    assert target.caller_id == "aanya-demo"
    assert target.caller_id != "+17432245805"


def test_from_user_can_be_overridden():
    target = sip_target.resolve_dial_target(
        LINPHONE_ADDRESS, settings=_settings(from_user="aanya-clinic")
    )
    assert target.caller_id == "aanya-clinic"


def test_a_domain_the_trunk_cannot_reach_is_called_out():
    """sip_call_to carries only the user, so a mismatch rings the wrong account."""
    target = sip_target.resolve_dial_target(
        "aanya-demo@sip.example.net", settings=_settings()
    )
    assert "does not match the trunk hostname" in target.note


def test_the_domain_can_be_pushed_into_the_destination_when_a_trunk_needs_it():
    target = sip_target.resolve_dial_target(
        LINPHONE_ADDRESS, settings=_settings(call_to_includes_domain=True)
    )
    assert target.sip_call_to == LINPHONE_ADDRESS


# ---------------------------------------------------------------------------
# What actually lands on the dial request
# ---------------------------------------------------------------------------


def _request() -> api.CreateSIPParticipantRequest:
    return api.CreateSIPParticipantRequest(room_name="test-room")


def test_apply_to_request_pstn_leg():
    request = _request()
    sip_target.apply_to_request(
        request, sip_target.resolve_dial_target(PHONE, settings=_settings())
    )
    assert request.sip_trunk_id == "ST_pstn_test"
    assert request.sip_call_to == PHONE
    assert request.participant_identity == PHONE
    assert request.sip_number == "+17432245805"
    assert request.HasField("trunk") is False


def test_apply_to_request_sip_leg_with_an_inline_trunk():
    request = _request()
    target = sip_target.resolve_dial_target(
        PHONE, settings=_settings(provider=sip_target.PROVIDER_LINPHONE)
    )
    sip_target.apply_to_request(request, target)

    assert request.sip_trunk_id == ""
    assert request.HasField("trunk") is True
    assert request.trunk.hostname == "sip.linphone.org"
    assert request.trunk.auth_username == "aanya-demo"
    assert request.sip_call_to == "aanya-demo"
    assert request.sip_number == "aanya-demo"
    # The one invariant the whole shift rests on.
    assert request.participant_identity == PHONE


def test_apply_to_request_sip_leg_with_a_stored_trunk_sets_no_inline_config():
    request = _request()
    sip_target.apply_to_request(
        request,
        sip_target.resolve_dial_target(
            LINPHONE_ADDRESS, settings=_settings(linphone_trunk_id="ST_linphone")
        ),
    )
    assert request.sip_trunk_id == "ST_linphone"
    assert request.HasField("trunk") is False


def test_the_sip_leg_offers_encrypted_media_by_default():
    """Linphone rejects LiveKit's default plain-RTP offer with 488."""
    from livekit.protocol import sip as sip_proto

    request = _request()
    sip_target.apply_to_request(
        request, sip_target.resolve_dial_target(LINPHONE_ADDRESS, settings=_settings())
    )
    assert request.media_encryption == sip_proto.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_ALLOW


def test_the_pstn_leg_leaves_media_encryption_at_the_livekit_default():
    """A carrier trunk is happy with plain RTP; do not change what already worked."""
    target = sip_target.resolve_dial_target(PHONE, settings=_settings())
    assert target.media_encryption is None


@pytest.mark.parametrize(
    "configured, expected_name",
    [
        ("require", "SIP_MEDIA_ENCRYPT_REQUIRE"),
        ("disable", "SIP_MEDIA_ENCRYPT_DISABLE"),
        ("none", "SIP_MEDIA_ENCRYPT_DISABLE"),
        ("allow", "SIP_MEDIA_ENCRYPT_ALLOW"),
    ],
)
def test_media_encryption_can_be_overridden(configured, expected_name):
    from livekit.protocol import sip as sip_proto

    target = sip_target.resolve_dial_target(
        LINPHONE_ADDRESS, settings=_settings(media_encryption=configured)
    )
    assert target.media_encryption == sip_proto.SIPMediaEncryption.Value(expected_name)


def test_an_override_also_reaches_the_pstn_leg():
    from livekit.protocol import sip as sip_proto

    target = sip_target.resolve_dial_target(
        PHONE, settings=_settings(provider="pstn", media_encryption="require")
    )
    assert target.media_encryption == sip_proto.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE


def test_an_unusable_media_encryption_mode_is_refused_by_name():
    with pytest.raises(sip_target.SipConfigError, match="not a media encryption mode"):
        sip_target.resolve_dial_target(
            LINPHONE_ADDRESS, settings=_settings(media_encryption="maybe")
        )


def test_apply_to_request_leaves_the_caller_id_alone_when_there_is_none():
    request = _request()
    target = sip_target.resolve_dial_target(
        PHONE, settings=_settings(from_number="", provider=sip_target.PROVIDER_PSTN)
    )
    sip_target.apply_to_request(request, target)
    assert request.sip_number == ""  # the trunk's own default number is used


# ---------------------------------------------------------------------------
# Configuration and logging
# ---------------------------------------------------------------------------


def test_load_settings_reads_the_environment(monkeypatch):
    monkeypatch.setenv("SIP_PROVIDER", "  LinPhone ")
    monkeypatch.setenv("SIP_LINPHONE_USER", " aanya-demo ")
    monkeypatch.setenv("SIP_LINPHONE_AUTH_PASSWORD", "s3cret")
    monkeypatch.setenv("SIP_CALL_TO_INCLUDES_DOMAIN", "YES")
    monkeypatch.delenv("SIP_LINPHONE_DOMAIN", raising=False)

    settings = sip_target.load_settings()
    assert settings.provider == sip_target.PROVIDER_LINPHONE
    assert settings.linphone_user == "aanya-demo"
    assert settings.linphone_domain == sip_target.DEFAULT_LINPHONE_DOMAIN
    assert settings.call_to_includes_domain is True


def test_load_settings_defaults_to_auto(monkeypatch):
    monkeypatch.delenv("SIP_PROVIDER", raising=False)
    assert sip_target.load_settings().provider == sip_target.PROVIDER_AUTO


@pytest.mark.parametrize(
    "provider, expected",
    [
        (sip_target.PROVIDER_PSTN, "PSTN only"),
        (sip_target.PROVIDER_LINPHONE, "SIP only"),
        (sip_target.PROVIDER_AUTO, "auto"),
    ],
)
def test_describe_routing_states_the_route(provider, expected):
    line = sip_target.describe_routing(_settings(provider=provider))
    assert expected in line


def test_describe_routing_states_the_media_encryption():
    """A 488 is otherwise indistinguishable from a codec problem in the logs."""
    line = sip_target.describe_routing(_settings(provider=sip_target.PROVIDER_LINPHONE))
    assert "media encryption allow" in line


def test_describe_routing_never_prints_the_password():
    """This line goes into every call's logs, and logs get pasted into chats."""
    line = sip_target.describe_routing(_settings(auth_password="s3cret-do-not-log"))
    assert "s3cret-do-not-log" not in line
    assert "authenticating as 'aanya-demo'" in line


def test_describe_routing_admits_when_nothing_is_configured():
    line = sip_target.describe_routing(
        _settings(pstn_trunk_id="", auth_username="", auth_password="")
    )
    assert "no trunk configured" in line
