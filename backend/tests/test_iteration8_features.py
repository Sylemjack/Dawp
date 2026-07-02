"""Iteration 8 backend tests:
1) Chats list: VIP + active frame/badge + in_voice_room on partner
2) Users partners filter: min_age/max_age/location combinable
3) Retest: POST /chats/{id}/mute toggles; DELETE /chats/{id}/messages clears
"""
import os
import uuid

import requests

def _from_env_file():
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
                return line.split("=", 1)[1].strip()
    return ""


BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or _from_env_file()
).rstrip("/")
API = f"{BASE_URL}/api"

DEMO_PW = "Demo1234!"


def auth(t):
    return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}


def login(email, pw=DEMO_PW):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=10)
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return r.json()["token"]


def signup(email, name, **extra):
    r = requests.post(
        f"{API}/auth/register",
        json={"email": email, "password": DEMO_PW, "name": name, **extra},
        timeout=10,
    )
    assert r.status_code in (200, 201), f"signup {email}: {r.status_code} {r.text}"
    d = r.json()
    return d["token"], d["user"]["id"]


# ---------------- Chats list decoration ----------------
def test_chats_list_returns_partner_flags_and_in_voice_room():
    """demo (VIP) has conversation with mei. Create a live room hosted by mei.
    demo GET /chats should show mei with in_voice_room populated."""
    demo_tok = login("demo@demo.com")
    mei_tok = login("mei@demo.com")
    mei_me = requests.get(f"{API}/auth/me", headers=auth(mei_tok), timeout=10).json()

    # Ensure demo↔mei conversation exists
    conv = requests.post(
        f"{API}/chats", headers=auth(demo_tok), json={"partner_id": mei_me["id"]}, timeout=10
    )
    assert conv.status_code == 200, conv.text

    # Try to create a room hosted by mei. Free tier = 1/day; if already used, try another user.
    hoster_tok = mei_tok
    hoster_id = mei_me["id"]
    room_r = requests.post(
        f"{API}/rooms",
        headers=auth(hoster_tok),
        json={"title": "TEST i8 voice", "language": "en"},
        timeout=10,
    )
    if room_r.status_code != 201:
        # fallback: create a fresh user, have them befriend demo? We just need any partner in a room.
        # For the in_voice_room check, we specifically need `demo` to have a conversation with the hoster.
        # Use fresh user, conv with demo, then host a room.
        fresh_email = f"i8_host_{uuid.uuid4().hex[:6]}@demo.com"
        fresh_tok, fresh_id = signup(fresh_email, "TEST i8 Host")
        # demo creates conversation with fresh
        requests.post(
            f"{API}/chats", headers=auth(demo_tok), json={"partner_id": fresh_id}, timeout=10
        )
        room_r = requests.post(
            f"{API}/rooms",
            headers=auth(fresh_tok),
            json={"title": "TEST i8 voice", "language": "en"},
            timeout=10,
        )
        assert room_r.status_code == 201, f"room create: {room_r.status_code} {room_r.text}"
        hoster_id = fresh_id
        hoster_tok = fresh_tok

    room_id = room_r.json()["id"]
    # Ensure it's live
    assert room_r.json().get("is_live") is True, room_r.json()

    # demo fetches chats
    chats = requests.get(f"{API}/chats", headers=auth(demo_tok), timeout=10).json()
    assert isinstance(chats, list) and len(chats) > 0

    target = next((c for c in chats if c.get("partner", {}).get("id") == hoster_id), None)
    assert target, f"No conv with hoster {hoster_id} in demo's chats"
    partner = target["partner"]
    # Partner should have vip flags (mei may or may not be VIP — just check keys)
    for k in ("id", "name"):
        assert k in partner
    # in_voice_room must be present
    assert partner.get("in_voice_room"), f"in_voice_room missing on partner: {partner}"
    assert partner["in_voice_room"].get("room_id") == room_id
    assert "name" in partner["in_voice_room"]

    # cleanup: close room
    requests.post(f"{API}/rooms/{room_id}/close", headers=auth(hoster_tok), timeout=10)


def test_chats_list_partner_has_vip_and_frame_fields():
    """demo is VIP — mei's chat with demo should show demo's is_vip flag."""
    mei_tok = login("mei@demo.com")
    demo_tok = login("demo@demo.com")
    demo_me = requests.get(f"{API}/auth/me", headers=auth(demo_tok), timeout=10).json()
    # ensure conv exists (from previous test or create)
    requests.post(
        f"{API}/chats", headers=auth(mei_tok), json={"partner_id": demo_me["id"]}, timeout=10
    )
    chats = requests.get(f"{API}/chats", headers=auth(mei_tok), timeout=10).json()
    demo_conv = next((c for c in chats if c.get("partner", {}).get("id") == demo_me["id"]), None)
    assert demo_conv, "mei has no conv with demo"
    p = demo_conv["partner"]
    # These fields may be present (from user_card). Ensure at least the keys we care about are supported.
    # is_vip should be true for demo
    assert p.get("is_vip") is True, f"demo should show is_vip=True: {p}"
    # active_frame/active_badge keys should exist (values may be None)
    assert "active_frame" in p, f"active_frame missing: {p.keys()}"
    assert "active_badge" in p, f"active_badge missing: {p.keys()}"


# ---------------- Partners filter (age + location) ----------------
def test_partners_filter_by_age_range():
    tok = login("demo@demo.com")
    r = requests.get(
        f"{API}/users/partners?min_age=18&max_age=25", headers=auth(tok), timeout=10
    )
    assert r.status_code == 200, r.text
    cards = r.json()
    for c in cards:
        # age may be hidden by privacy; ignore None
        age = c.get("age")
        if age is not None:
            assert 18 <= age <= 25, f"card age {age} out of 18-25: {c.get('id')}"


def test_partners_filter_by_location_case_insensitive():
    tok = login("demo@demo.com")
    r = requests.get(f"{API}/users/partners?location=china", headers=auth(tok), timeout=10)
    assert r.status_code == 200, r.text
    cards = r.json()
    # At least mei should be there (mei is China)
    for c in cards:
        country = (c.get("country") or "").lower()
        city = (c.get("city") or "").lower()
        assert "china" in country or "china" in city or (country == "" and city == ""), (
            f"result does not match china: {c}"
        )


def test_partners_filter_combines_age_location_gender():
    tok = login("demo@demo.com")
    r = requests.get(
        f"{API}/users/partners?min_age=18&max_age=99&location=china&gender=female",
        headers=auth(tok),
        timeout=10,
    )
    assert r.status_code == 200, r.text
    cards = r.json()
    for c in cards:
        if c.get("gender") is not None:
            assert c["gender"] == "female", c


def test_partners_no_filter_still_works():
    tok = login("demo@demo.com")
    r = requests.get(f"{API}/users/partners", headers=auth(tok), timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------------- RETEST: mute toggle + suppresses unread ----------------
def test_mute_toggle_and_partner_unread_not_incremented():
    """A mutes conv with B, then B sends message. A's unread must stay 0."""
    a_email = f"i8_mute_a_{uuid.uuid4().hex[:6]}@demo.com"
    b_email = f"i8_mute_b_{uuid.uuid4().hex[:6]}@demo.com"
    a_tok, a_id = signup(a_email, "TEST i8 Mute A")
    b_tok, b_id = signup(b_email, "TEST i8 Mute B")
    conv = requests.post(
        f"{API}/chats", headers=auth(a_tok), json={"partner_id": b_id}, timeout=10
    ).json()
    conv_id = conv["id"]

    # A toggles mute (mutes)
    r = requests.post(f"{API}/chats/{conv_id}/mute", headers=auth(a_tok), timeout=10)
    assert r.status_code in (200, 201), r.text
    assert r.json().get("muted") is True

    # B sends text message
    m = requests.post(
        f"{API}/chats/{conv_id}/messages",
        headers=auth(b_tok),
        json={"text": "hi muted"},
        timeout=10,
    )
    assert m.status_code == 201

    # A's chat list: unread for this conv must remain 0 because A muted
    chats = requests.get(f"{API}/chats", headers=auth(a_tok), timeout=10).json()
    this = next((c for c in chats if c["id"] == conv_id), None)
    assert this is not None, "conv missing"
    assert this.get("muted") is True
    assert this.get("unread", 0) == 0, f"muted conv unread should be 0, got {this.get('unread')}"

    # Toggle again -> unmuted
    r2 = requests.post(f"{API}/chats/{conv_id}/mute", headers=auth(a_tok), timeout=10)
    assert r2.json().get("muted") is False


def test_clear_history_wipes_messages_and_last_message():
    a_email = f"i8_clr_a_{uuid.uuid4().hex[:6]}@demo.com"
    b_email = f"i8_clr_b_{uuid.uuid4().hex[:6]}@demo.com"
    a_tok, _ = signup(a_email, "TEST i8 Clear A")
    _, b_id = signup(b_email, "TEST i8 Clear B")
    conv = requests.post(
        f"{API}/chats", headers=auth(a_tok), json={"partner_id": b_id}, timeout=10
    ).json()
    conv_id = conv["id"]
    # Send a couple of messages
    for i in range(3):
        requests.post(
            f"{API}/chats/{conv_id}/messages",
            headers=auth(a_tok),
            json={"text": f"msg {i}"},
            timeout=10,
        )
    msgs = requests.get(
        f"{API}/chats/{conv_id}/messages", headers=auth(a_tok), timeout=10
    ).json()
    assert len(msgs) == 3
    # Clear
    d = requests.delete(f"{API}/chats/{conv_id}/messages", headers=auth(a_tok), timeout=10)
    assert d.status_code in (200, 204), d.text
    msgs2 = requests.get(
        f"{API}/chats/{conv_id}/messages", headers=auth(a_tok), timeout=10
    ).json()
    assert msgs2 == []
    # Conv last_message should be None
    chats = requests.get(f"{API}/chats", headers=auth(a_tok), timeout=10).json()
    this = next(c for c in chats if c["id"] == conv_id)
    assert this.get("last_message") in (None, {}), this


# ---------------- Regression ----------------
def test_regression_core_endpoints():
    tok = login("demo@demo.com")
    for path in ("/chats", "/moments", "/rooms", "/users/partners"):
        r = requests.get(f"{API}{path}", headers=auth(tok), timeout=10)
        assert r.status_code == 200, f"{path} => {r.status_code}"
