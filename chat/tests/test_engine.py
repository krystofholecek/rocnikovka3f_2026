"""Inference: KV cache dává stejné výsledky jako běžný průchod modelem, stop a limity fungují."""
import torch

from chat.engine import Engine, Finish, GenParams
from chat.prompt import build_prompt


def load(model_dir) -> Engine:
    return Engine.load(model_dir / "model.pt", model_dir / "tokenizer.json", device="cpu")


def test_kv_cache_matches_full_forward(model_dir):
    eng = load(model_dir)
    ids = eng.encode("The Internet is a global network")
    cache = [None] * len(eng.model.blocks)
    logits = eng._forward_cached(torch.tensor([ids]), cache, 0)
    full, _ = eng.model(torch.tensor([ids]))
    assert torch.allclose(logits, full[:, -1, :], atol=1e-5)
    # další token přes cache = poslední pozice celého průchodu
    nxt = 5
    step = eng._forward_cached(torch.tensor([[nxt]]), cache, len(ids))
    full2, _ = eng.model(torch.tensor([ids + [nxt]]))
    assert torch.allclose(step, full2[:, -1, :], atol=1e-5)


def test_greedy_is_deterministic_and_respects_max_tokens(model_dir):
    eng = load(model_dir)
    ids = eng.encode("Question: What is a computer?\nAnswer:")
    p = GenParams(max_new_tokens=12, temperature=0)
    f1, f2 = Finish(), Finish()
    a = "".join(eng.stream(ids, p, finish=f1))
    b = "".join(eng.stream(ids, p, finish=f2))
    assert a == b
    assert f1.tokens <= 12
    assert f1.reason in {"length", "stop"}


def test_stop_string_is_not_returned(model_dir):
    eng = load(model_dir)
    ids = eng.encode("Hello")
    f = Finish()
    out = "".join(eng.stream(ids, GenParams(max_new_tokens=60, temperature=1.0, top_k=0, top_p=1.0),
                             stop=("e",), seed=1, finish=f))
    assert "e" not in out


def test_timeout(model_dir):
    eng = load(model_dir)
    f = Finish()
    list(eng.stream(eng.encode("Hi"), GenParams(max_new_tokens=50), timeout=0, finish=f))
    assert f.reason == "timeout" and f.tokens == 0


def test_prompt_fits_budget_and_keeps_last_question(model_dir):
    eng = load(model_dir)
    msgs = [{"role": "user", "content": "old question " * 50}, {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "What is email?"}]
    ids = build_prompt(msgs, eng.encode, 40)
    assert len(ids) <= 40
    assert eng.tok.decode(ids).endswith("What is email?\nAnswer:")
