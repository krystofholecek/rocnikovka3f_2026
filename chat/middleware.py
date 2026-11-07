"""CORS jen pro web chronosllm.com: prohlížeč dovolí volat API jen stránkám z povolených adres.

Požadavek POST z jiné stránky (hlavička Origin mimo seznam) se rovnou odmítne.
Při DEBUG=True se navíc povolí http://localhost a http://127.0.0.1 (lokální vývoj).
"""
from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, JsonResponse


def origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    if origin in settings.CHAT_ALLOWED_ORIGINS:
        return True
    if settings.DEBUG:
        parts = urlsplit(origin)
        return parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"}
    return False


class ApiCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith("/api/"):
            return self.get_response(request)
        origin = request.headers.get("Origin")
        allowed = origin_allowed(origin)
        if request.method == "OPTIONS":
            response = HttpResponse(status=204 if allowed else 403)
        elif origin and not allowed and request.method not in {"GET", "HEAD"}:
            response = JsonResponse(
                {"error": "origin_not_allowed", "message": "Tahle stránka nesmí volat API chatu."}, status=403
            )
        else:
            response = self.get_response(request)
        if allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type"
            response["Access-Control-Max-Age"] = "600"
        response["Vary"] = "Origin"
        response.setdefault("Cache-Control", "no-store")
        return response
