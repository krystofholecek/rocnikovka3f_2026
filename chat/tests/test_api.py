"""HTTP API: odpovědi, stream, limity, CORS a režim „model se trénuje“ (503)."""
import json

import pytest

ORIGIN = "https://chronosllm.com"


def post(client, body, **headers):
    return client.post("/api/chat", data=json.dumps(body), content_type="application/json", **headers)


def events(response) -> list[dict]:
    raw = b"".join(response.streaming_content).decode()
    return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]


def test_health_and_info(api):
    h = api.get("/api/health").json()
    assert h["ready"] is True and h["status"] == "ready"
    assert h["model"]["name"] == "test-tiny" and h["model"]["step"] == 7
    assert "commit" in h["version"]
    i = api.get("/api/info").json()
    assert i["cutoff"] == "2005-12-31"
    assert i["model"]["config"]["n_layer"] == 2
    assert i["limits"]["max_new_tokens"] >= 1


def test_chat_json(api):
    r = post(api, {"messages": [{"role": "user", "content": "What is the Internet?"}], "stream": False,
                   "max_new_tokens": 10})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["reply"], str)
    assert body["tokens"] <= 10
    assert body["finish_reason"] in {"length", "stop"}


def test_chat_stream(api):
    r = post(api, {"message": "Hello there", "max_new_tokens": 8})
    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/event-stream")
    ev = events(r)
    assert ev[0]["type"] == "start"
    assert ev[-1]["type"] == "done" and ev[-1]["tokens"] <= 8
    assert all(e["type"] == "token" for e in ev[1:-1])


def test_max_new_tokens_is_capped(api, settings_override):
    with settings_override(CHAT_MAX_NEW_TOKENS=5):
        r = post(api, {"message": "Hi", "stream": False, "max_new_tokens": 10_000, "temperature": 1.0})
        assert r.status_code == 200 and r.json()["tokens"] <= 5


@pytest.mark.parametrize("body, code", [
    ({"messages": []}, "bad_messages"),
    ({"messages": [{"role": "system", "content": "x"}]}, "bad_messages"),
    ({"messages": [{"role": "user", "content": "   "}]}, "empty"),
    ({"message": "x" * 1001}, "too_long"),
    ({"message": "hi", "temperature": "hot"}, "bad_param"),
])
def test_invalid_input(api, body, code):
    r = post(api, body)
    assert r.status_code == 400 and r.json()["error"] == code


def test_rate_limit(api, settings_override):
    with settings_override(CHAT_RATE_LIMIT_PER_MINUTE=2):
        assert post(api, {"message": "a", "stream": False, "max_new_tokens": 1}).status_code == 200
        assert post(api, {"message": "b", "stream": False, "max_new_tokens": 1}).status_code == 200
        r = post(api, {"message": "c", "stream": False, "max_new_tokens": 1})
        assert r.status_code == 429 and int(r["Retry-After"]) >= 1


def test_busy_when_all_slots_taken(api, settings_override):
    from chat import views

    with settings_override(CHAT_MAX_CONCURRENT=1):
        _, slots = views.runtime()
        assert slots.try_acquire()
        try:
            r = post(api, {"message": "hi", "stream": False})
            assert r.status_code == 503 and r.json()["error"] == "busy"
        finally:
            slots.release()


def test_slot_released_after_stream(api, settings_override):
    from chat import views

    with settings_override(CHAT_MAX_CONCURRENT=1):
        for _ in range(2):  # druhý požadavek projde jen tehdy, když první místo uvolnil
            r = post(api, {"message": "hi", "max_new_tokens": 2})
            events(r)
            r.close()
        _, slots = views.runtime()
        assert slots.try_acquire()
        slots.release()


def test_no_model_returns_503(settings_override, tmp_path):
    from django.test import Client

    with settings_override(CHAT_CHECKPOINT=str(tmp_path / "missing.pt"), CHAT_TOKENIZER=str(tmp_path / "t.json")):
        c = Client()
        h = c.get("/api/health").json()
        assert h["ready"] is False and h["status"] == "no_model"
        assert "trénuje" in h["message"]
        r = post(c, {"message": "hi"})
        assert r.status_code == 503
        assert r.json()["error"] == "model_unavailable" and "trénuje" in r.json()["message"]
        assert c.get("/api/info").json()["ready"] is False


def test_cors(api):
    r = api.options("/api/chat", HTTP_ORIGIN=ORIGIN)
    assert r.status_code == 204 and r["Access-Control-Allow-Origin"] == ORIGIN
    r = api.get("/api/health", HTTP_ORIGIN=ORIGIN)
    assert r["Access-Control-Allow-Origin"] == ORIGIN
    bad = post(api, {"message": "hi"}, HTTP_ORIGIN="https://evil.example")
    assert bad.status_code == 403 and "Access-Control-Allow-Origin" not in bad
    assert api.options("/api/chat", HTTP_ORIGIN="https://evil.example").status_code == 403


def test_get_on_chat_not_allowed(api):
    assert api.get("/api/chat").status_code == 405
