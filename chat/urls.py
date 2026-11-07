"""Adresy API chatu (popis v chat/views.py)."""

from django.urls import path

from . import views

urlpatterns = [
    path("api/health", views.health, name="health"),
    path("api/info", views.info, name="info"),
    path("api/chat", views.chat, name="chat"),
]
