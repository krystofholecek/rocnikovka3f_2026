import torch

from llm2005.model import GPT, GPTConfig


def test_forward_and_generate():
    c = GPTConfig(vocab_size=100, block_size=16, n_layer=2, n_head=2, n_embd=32)
    m = GPT(c)
    x = torch.randint(100, (2, 16))
    logits, loss = m(x, x)
    assert logits.shape == (2, 16, 100)
    assert loss.item() > 0
    out = m.generate(x[:, :4], max_new_tokens=5)
    assert out.shape == (2, 9)
