"""Převod konverzace na text pro model.

Model není „chatbot“ – umí jen pokračovat v textu. Proto konverzaci zapíšeme jako otázky a odpovědi
a necháme ho dopsat další odpověď. Odpověď končí tam, kde by model začal psát další otázku.
"""
from __future__ import annotations

from collections.abc import Callable

from .engine import EOT

HEADER = "The following questions were answered in December 2005.\n\n"
USER, BOT = "Question: ", "Answer:"
STOP = ("\nQuestion:", "\nAnswer:")


def clean(text: str) -> str:
    """Odstraní speciální token konce dokumentu a sjednotí konce řádků."""
    return text.replace(EOT, "").replace("\r\n", "\n").strip()


def build_prompt(messages: list[dict], encode: Callable[[str], list[int]], budget: int) -> list[int]:
    """Složí tokeny promptu z co nejvíce posledních zpráv tak, aby se vešly do `budget` tokenů.

    messages: [{"role": "user"|"assistant", "content": str}, ...], poslední je vždy od uživatele.
    Když se nevejde ani poslední otázka, ořízne se její začátek.
    """
    header = encode(HEADER)
    turns: list[str] = []
    pending_answer = None
    for m in reversed(messages):
        if m["role"] == "assistant":
            pending_answer = clean(m["content"])
            continue
        q = f"{USER}{clean(m['content'])}\n{BOT}"
        if pending_answer is None:
            turns.append(q)  # poslední otázka: model dopíše odpověď
        else:
            turns.append(f"{q} {pending_answer}\n\n")
        pending_answer = None

    ids: list[int] = []
    for i, turn in enumerate(turns):  # od nejnovější
        t = encode(turn)
        if len(header) + len(t) + len(ids) > budget:
            if i == 0:
                room = max(1, budget - len(header))
                ids = t[-room:]
            break
        ids = t + ids
    return (header + ids)[-budget:] if budget > 0 else ids
