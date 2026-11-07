"""HTTP API chatu.

GET  /api/health  stav serveru: je načtený model? jaký checkpoint? verze kódu, anonymní počty
GET  /api/info    parametry modelu, datum „cutoff“, limity
POST /api/chat    {"messages": [{"role": "user", "content": "..."}], "stream": true, ...}
                  -> stream (Server-Sent Events) nebo JSON s odpovědí

Když model ještě není (checkpoint chybí, trénink běží), /api/chat vrací 503 se srozumitelnou zprávou.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import subprocess

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_GET, require_POST

from . import engine as engine_mod
from .engine import Finish, GenParams
from .limits import RateLimiter, Slots, Stats, anonymize
from .prompt import STOP, build_prompt, clean

log = logging.getLogger(__name__)

CUTOFF = "2005-12-31"
TRAINING_MESSAGE = "Model se zrovna trénuje, zkus to později."

stats = Stats()


@functools.cache
def _runtime(per_minute: int, per_day: int, concurrent: int) -> tuple[RateLimiter, Slots]:
    return RateLimiter([(per_minute, 60.0), (per_day, 86400.0)]), Slots(concurrent)


def runtime() -> tuple[RateLimiter, Slots]:
    return _runtime(settings.CHAT_RATE_LIMIT_PER_MINUTE, settings.CHAT_RATE_LIMIT_PER_DAY, settings.CHAT_MAX_CONCURRENT)


@functools.cache
def commit() -> str | None:
    """Verze kódu: proměnná GIT_COMMIT (Docker) nebo git v pracovní složce."""
    if os.environ.get("GIT_COMMIT"):
        return os.environ["GIT_COMMIT"]
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=settings.BASE_DIR, capture_output=True,
                             text=True, timeout=2, check=True)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def error(code: str, message: str, status: int, **headers) -> JsonResponse:
    response = JsonResponse({"error": code, "message": message}, status=status)
    for key, value in headers.items():
        response[key.replace("_", "-")] = value
    return response


def client_ip(request) -> str:
    header = settings.CHAT_CLIENT_IP_HEADER
    if header and request.headers.get(header):
        return request.headers[header].split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def model_info(eng) -> dict | None:
    if eng is None:
        return None
    return {k: eng.info[k] for k in ("name", "step", "params_m", "checkpoint", "loaded_at", "device", "vocab_size")}


def limits() -> dict:
    return {
        "max_input_chars": settings.CHAT_MAX_INPUT_CHARS,
        "max_history_chars": settings.CHAT_MAX_HISTORY_CHARS,
        "max_messages": settings.CHAT_MAX_MESSAGES,
        "max_new_tokens": settings.CHAT_MAX_NEW_TOKENS,
        "timeout_seconds": settings.CHAT_TIMEOUT_SECONDS,
        "rate_limit_per_minute": settings.CHAT_RATE_LIMIT_PER_MINUTE,
        "rate_limit_per_day": settings.CHAT_RATE_LIMIT_PER_DAY,
    }


# ---------------------------------------------------------------------- GET
@require_GET
def health(request):
    eng = engine_mod.get_engine(settings)
    st = engine_mod.state()
    body = {
        "status": st["status"],  # ready | no_model | error | not_loaded
        "ready": eng is not None,
        "model": model_info(eng),
        "version": {"commit": commit()},
        "stats": stats.snapshot(),
    }
    if eng is None:
        body["message"] = TRAINING_MESSAGE if st["status"] != "error" else "Model se nepodařilo načíst."
    return JsonResponse(body)


@require_GET
def info(request):
    eng = engine_mod.get_engine(settings)
    return JsonResponse({
        "name": "ChronosLLM",
        "cutoff": CUTOFF,
        "description": "Jazykový model natrénovaný od nuly jen na textech napsaných do 31. 12. 2005.",
        "language": "en",
        "language_note": "Model se učil hlavně z anglických textů, nejlépe rozumí angličtině.",
        "ready": eng is not None,
        "model": None if eng is None else {**model_info(eng), "config": eng.info["config"]},
        "defaults": {"max_new_tokens": settings.CHAT_DEFAULT_NEW_TOKENS, "temperature": 0.8, "top_k": 50,
                     "top_p": 0.95},
        "limits": limits(),
    })


# ---------------------------------------------------------------------- POST /api/chat
class BadRequest(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _num(body: dict, key: str, default, lo, hi, kind):
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadRequest("bad_param", f"Parametr {key} musí být číslo.")
    return kind(min(max(value, lo), hi))


def parse_params(body: dict) -> GenParams:
    return GenParams(
        max_new_tokens=_num(body, "max_new_tokens", settings.CHAT_DEFAULT_NEW_TOKENS, 1,
                            settings.CHAT_MAX_NEW_TOKENS, int),
        temperature=_num(body, "temperature", 0.8, 0.0, 1.5, float),
        top_k=_num(body, "top_k", 50, 0, 200, int),
        top_p=_num(body, "top_p", 0.95, 0.05, 1.0, float),
    )


def parse_messages(body: dict) -> list[dict]:
    messages = body.get("messages")
    if messages is None and isinstance(body.get("message"), str):
        messages = [{"role": "user", "content": body["message"]}]
    if not isinstance(messages, list) or not messages:
        raise BadRequest("bad_messages", "Chybí zprávy (messages).")
    out = []
    for m in messages:
        if (not isinstance(m, dict) or m.get("role") not in {"user", "assistant"}
                or not isinstance(m.get("content"), str)):
            raise BadRequest("bad_messages", "Každá zpráva musí mít role (user/assistant) a content (text).")
        out.append({"role": m["role"], "content": m["content"]})
    if out[-1]["role"] != "user":
        raise BadRequest("bad_messages", "Poslední zpráva musí být od uživatele.")
    last = clean(out[-1]["content"])
    if not last:
        raise BadRequest("empty", "Napiš nějakou otázku.")
    if len(last) > settings.CHAT_MAX_INPUT_CHARS:
        raise BadRequest("too_long", f"Zpráva je moc dlouhá (max. {settings.CHAT_MAX_INPUT_CHARS} znaků).")
    # starší historii tiše zkrátíme: počet zpráv i celkový počet znaků
    out = out[-settings.CHAT_MAX_MESSAGES:]
    while len(out) > 1 and sum(len(m["content"]) for m in out) > settings.CHAT_MAX_HISTORY_CHARS:
        out.pop(0)
    return out


def sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


class _Stream:
    """Iterátor odpovědi, který vždy uvolní místo pro generování – i když klient spojení přeruší."""

    def __init__(self, gen, release):
        self._gen, self._release, self._done = gen, release, False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._gen)
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self._done:
            self._done = True
            try:
                self._gen.close()
            finally:
                self._release()


def _generate(eng, prompt_ids: list[int], params: GenParams, finish: Finish):
    first = True
    for piece in eng.stream(prompt_ids, params, stop=STOP, timeout=settings.CHAT_TIMEOUT_SECONDS, finish=finish):
        if first:  # odpověď začíná za „Answer:“ mezerou
            piece = piece.lstrip()
            if not piece:
                continue
            first = False
        yield piece


@require_POST
def chat(request):
    stats.add("requests")
    limiter, slots = runtime()
    wait = limiter.hit(anonymize(client_ip(request)))
    if wait:
        stats.add("rate_limited")
        return error("rate_limited", "Moc dotazů najednou, chvilku počkej.", 429, Retry_After=str(int(wait)))

    try:
        body = json.loads(request.body or b"{}")
        if not isinstance(body, dict):
            raise BadRequest("bad_json", "Tělo požadavku musí být JSON objekt.")
        messages = parse_messages(body)
        params = parse_params(body)
    except json.JSONDecodeError:
        stats.add("rejected")
        return error("bad_json", "Tělo požadavku není platný JSON.", 400)
    except BadRequest as exc:
        stats.add("rejected")
        return error(exc.code, exc.message, 400)

    eng = engine_mod.get_engine(settings)
    if eng is None:
        stats.add("no_model")
        status = engine_mod.state()["status"]
        message = TRAINING_MESSAGE if status != "error" else "Model se nepodařilo načíst, zkus to později."
        return error("model_unavailable", message, 503, Retry_After="600")

    if not slots.try_acquire():
        stats.add("busy")
        return error("busy", "Server je zrovna vytížený, zkus to za chvilku.", 503, Retry_After="5")

    released = False

    def release():
        nonlocal released
        if not released:
            released = True
            slots.release()

    try:
        prompt_ids = build_prompt(messages, eng.encode, eng.block_size - params.max_new_tokens)
    except Exception:
        release()
        stats.add("errors")
        log.exception("Chyba při přípravě promptu")
        return error("internal", "Něco se pokazilo.", 500)

    finish = Finish()
    if body.get("stream", True):
        def events():
            try:
                yield sse({"type": "start", "model": eng.info["name"]})
                for piece in _generate(eng, prompt_ids, params, finish):
                    yield sse({"type": "token", "text": piece})
                _count(finish)
                yield sse({"type": "done", "finish_reason": finish.reason, "tokens": finish.tokens,
                           "seconds": finish.seconds})
            except Exception:
                stats.add("errors")
                log.exception("Chyba při generování")
                yield sse({"type": "error", "message": "Při generování se něco pokazilo."})
            finally:
                release()

        response = StreamingHttpResponse(_Stream(events(), release), content_type="text/event-stream; charset=utf-8")
        response["Cache-Control"] = "no-cache, no-store"
        response["X-Accel-Buffering"] = "no"  # proxy (nginx) nemá odpověď držet, ale posílat hned
        return response

    try:
        reply = "".join(_generate(eng, prompt_ids, params, finish))
    except Exception:
        stats.add("errors")
        log.exception("Chyba při generování")
        return error("internal", "Při generování se něco pokazilo.", 500)
    finally:
        release()
    _count(finish)
    return JsonResponse({"reply": reply, "finish_reason": finish.reason, "tokens": finish.tokens,
                         "seconds": finish.seconds, "model": eng.info["name"]})


def _count(finish: Finish) -> None:
    stats.add("completed")
    stats.add("tokens", finish.tokens)
    if finish.reason == "timeout":
        stats.add("timeouts")
