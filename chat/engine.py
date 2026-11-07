"""Inference: načtení natrénovaného modelu a generování textu po tokenech.

Model je GPT z llm2005/model.py. Pro rychlé generování na CPU tu je vlastní průchod s KV cache
(klíče a hodnoty už zpracovaných tokenů se pamatují, takže každý nový token stojí jen jeden krok,
ne přepočet celého textu). Váhy i výsledek jsou stejné jako u GPT.forward – ověřuje to test.

Checkpoint může být:
- přímo výstup tréninku checkpoints/<name>/ckpt.pt ({"model", "optimizer", "step", "config", ...}),
- nebo menší export z `python -m llm2005.export` (model.pt: jen váhy + konfigurace).
Načítá se s weights_only=True: soubor nemůže spustit žádný kód (bezpečné i pro cizí soubor).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn.functional as F

from llm2005.model import GPT, GPTConfig

log = logging.getLogger(__name__)

EOT = "<|endoftext|>"


@dataclass(frozen=True)
class GenParams:
    """Parametry generování (už oříznuté na povolené hodnoty, viz views.parse_params)."""

    max_new_tokens: int = 150
    temperature: float = 0.8
    top_k: int = 50
    top_p: float = 0.95


@dataclass
class Finish:
    """Proč generování skončilo: stop (konec odpovědi), length (limit tokenů), timeout."""

    reason: str = "length"
    tokens: int = 0
    seconds: float = 0.0


def _model_config(ck: dict, config_path: Path | None) -> dict:
    """Konfigurace modelu z checkpointu, případně z configs/*.json."""
    cfg = ck.get("config") or {}
    model_cfg = cfg.get("model") if isinstance(cfg, dict) else None
    if model_cfg is None and config_path is not None:
        model_cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))["model"]
    if model_cfg is None:
        raise ValueError("Checkpoint neobsahuje konfiguraci modelu a nebyl zadán CHAT_CONFIG (configs/*.json).")
    allowed = {f.name for f in dataclasses.fields(GPTConfig)}
    return {k: v for k, v in model_cfg.items() if k in allowed}


def pick_device(name: str) -> str:
    if name == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return name


class Engine:
    """Načtený model + tokenizér. Jedna instance na proces (viz get_engine)."""

    def __init__(self, model: GPT, tokenizer, device: str, info: dict):
        self.model = model.eval()
        self.tok = tokenizer
        self.device = device
        self.info = info
        self.block_size = model.config.block_size
        self.eot_id = tokenizer.token_to_id(EOT)

    # ------------------------------------------------------------------ načtení
    @classmethod
    def load(cls, checkpoint: Path, tokenizer_path: Path, config_path: Path | None = None,
             device: str = "auto") -> Engine:
        from tokenizers import Tokenizer

        device = pick_device(device)
        ck = torch.load(checkpoint, map_location="cpu", weights_only=True)
        model_cfg = _model_config(ck, config_path)
        model = GPT(GPTConfig(**model_cfg))
        state = {k.removeprefix("_orig_mod."): v for k, v in ck["model"].items()}
        model.load_state_dict(state)
        model = model.float().to(device)
        tok = Tokenizer.from_file(str(tokenizer_path))
        if tok.get_vocab_size() > model_cfg["vocab_size"]:
            raise ValueError("Tokenizér má větší slovník než model – patří k jinému modelu?")
        name = (ck.get("config") or {}).get("name") or Path(checkpoint).parent.name
        info = {
            "name": name,
            "step": ck.get("step"),
            "params_m": round(model.num_params() / 1e6, 1),
            "checkpoint": Path(checkpoint).name,
            "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "device": device,
            "config": model_cfg,
            "vocab_size": tok.get_vocab_size(),
        }
        log.info("Model %s načten (%s M parametrů, krok %s, %s)", name, info["params_m"], info["step"], device)
        return cls(model, tok, device, info)

    # ------------------------------------------------------------------ model s KV cache
    @torch.no_grad()
    def _forward_cached(self, idx: torch.Tensor, cache: list, pos: int) -> torch.Tensor:
        """Průchod modelem pro nové tokeny idx (1 × T) od pozice pos; doplní cache. Vrací logity posledního tokenu."""
        m = self.model
        T = idx.size(1)
        x = m.tok_emb(idx) + m.pos_emb(torch.arange(pos, pos + T, device=idx.device))
        for i, block in enumerate(m.blocks):
            attn = block.attn
            h = block.ln1(x)
            B, _, C = h.shape
            q, k, v = attn.qkv(h).split(C, dim=2)
            q, k, v = (t.view(B, T, attn.n_head, C // attn.n_head).transpose(1, 2) for t in (q, k, v))
            if cache[i] is not None:
                k = torch.cat([cache[i][0], k], dim=2)
                v = torch.cat([cache[i][1], v], dim=2)
            cache[i] = (k, v)
            # Při prvním průchodu (celý prompt) maska „nedívej se dopředu“; nový token vidí vše před sebou.
            y = F.scaled_dot_product_attention(q, k, v, is_causal=pos == 0 and T > 1)
            x = x + attn.proj(y.transpose(1, 2).contiguous().view(B, T, C))
            x = x + block.mlp(block.ln2(x))
        return m.head(m.ln_f(x[:, -1, :]))

    @staticmethod
    def _sample(logits: torch.Tensor, p: GenParams, generator: torch.Generator | None) -> int:
        logits = logits.float().squeeze(0)
        if p.temperature <= 0:
            return int(torch.argmax(logits))
        logits = logits / p.temperature
        if p.top_k and p.top_k < logits.size(-1):
            kth = torch.topk(logits, p.top_k).values[-1]
            logits = logits.masked_fill(logits < kth, float("-inf"))
        probs = F.softmax(logits, dim=-1)
        if p.top_p < 1.0:
            sorted_probs, order = torch.sort(probs, descending=True)
            cum = torch.cumsum(sorted_probs, dim=-1)
            drop = cum - sorted_probs > p.top_p  # ponech nejmenší skupinu nejpravděpodobnějších tokenů se součtem ≥ top_p
            sorted_probs = sorted_probs.masked_fill(drop, 0.0)
            probs = torch.zeros_like(probs).scatter(0, order, sorted_probs)
            probs = probs / probs.sum()
        return int(torch.multinomial(probs, 1, generator=generator))

    # ------------------------------------------------------------------ generování
    def encode(self, text: str) -> list[int]:
        return self.tok.encode(text).ids

    def stream(self, prompt_ids: list[int], params: GenParams, stop: tuple[str, ...] = (),
               timeout: float | None = None, seed: int | None = None, finish: Finish | None = None) -> Iterator[str]:
        """Generuje odpověď a průběžně vrací nové kousky textu.

        Konec: token <|endoftext|>, některý ze stop řetězců, max_new_tokens, nebo vypršení timeout (sekundy).
        Výsledek (důvod, počet tokenů, čas) se zapíše do `finish`.
        """
        finish = finish if finish is not None else Finish()
        start = time.monotonic()
        max_new = max(0, min(params.max_new_tokens, self.block_size - 1))
        prompt_ids = prompt_ids[-(self.block_size - max_new):] or [self.eot_id or 0]
        generator = torch.Generator(device="cpu").manual_seed(seed) if seed is not None else None
        hold = max((len(s) for s in stop), default=1) - 1  # tolik znaků podržíme: může to být začátek stop řetězce

        cache: list = [None] * len(self.model.blocks)
        idx = torch.tensor([prompt_ids], dtype=torch.long, device=self.device)
        logits = self._forward_cached(idx, cache, 0)
        pos = len(prompt_ids)
        out_ids: list[int] = []
        sent = 0
        stop_hit = False
        finish.reason = "length"
        for _ in range(max_new):
            if timeout is not None and time.monotonic() - start > timeout:
                finish.reason = "timeout"
                break
            next_id = self._sample(logits.cpu(), params, generator)
            if next_id == self.eot_id:
                finish.reason = "stop"
                break
            out_ids.append(next_id)
            text = self.tok.decode(out_ids)
            cut = min((text.find(s) for s in stop if s in text), default=-1)
            if cut >= 0:
                if cut > sent:
                    yield text[sent:cut]
                sent = cut
                finish.reason = "stop"
                stop_hit = True
                break
            # neúplný znak UTF-8 (rozdělený mezi tokeny) a možný začátek stop řetězce zatím neposíláme
            safe = len(text.rstrip("�")) - hold
            if safe > sent:
                yield text[sent:safe]
                sent = safe
            logits = self._forward_cached(torch.tensor([[next_id]], device=self.device), cache, pos)
            pos += 1
        if not stop_hit:
            text = self.tok.decode(out_ids).rstrip("�")
            if len(text) > sent:
                yield text[sent:]
        finish.tokens = len(out_ids)
        finish.seconds = round(time.monotonic() - start, 3)


# ---------------------------------------------------------------------- jedna instance na proces
_lock = threading.Lock()
_engine: Engine | None = None
_state = {"status": "not_loaded", "error": None, "checked_at": 0.0}
RECHECK_SECONDS = 30.0


def reset() -> None:
    """Zapomene načtený model (použití v testech a po výměně checkpointu)."""
    global _engine
    with _lock:
        _engine = None
        _state.update(status="not_loaded", error=None, checked_at=0.0)


def get_engine(settings) -> Engine | None:
    """Vrátí načtený model, nebo None, když checkpoint zatím neexistuje (model se trénuje).

    Chybějící checkpoint se zkouší znovu nejvýš jednou za RECHECK_SECONDS, takže po nahrání vah
    stačí chvíli počkat (nebo restartovat server).
    """
    global _engine
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine
        now = time.monotonic()
        if _state["checked_at"] and now - _state["checked_at"] < RECHECK_SECONDS:
            return None
        _state["checked_at"] = now
        ckpt = Path(settings.CHAT_CHECKPOINT)
        tok = Path(settings.CHAT_TOKENIZER)
        if not ckpt.is_file() or not tok.is_file():
            _state.update(status="no_model", error=None)
            return None
        try:
            cfg = Path(settings.CHAT_CONFIG) if settings.CHAT_CONFIG else None
            _engine = Engine.load(ckpt, tok, cfg, settings.CHAT_DEVICE)
        except Exception as exc:  # poškozený nebo nekompatibilní soubor: API běží dál a hlásí chybu
            log.exception("Model se nepodařilo načíst")
            _state.update(status="error", error=f"{type(exc).__name__}: {exc}")
            return None
        _state.update(status="ready", error=None)
        return _engine


def state() -> dict:
    return dict(_state)
