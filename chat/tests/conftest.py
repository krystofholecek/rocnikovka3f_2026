"""Společné přípravy pro testy chatu: Django bez databáze a malý náhodný model (žádný skutečný checkpoint)."""
import os

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chat.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "test-not-secret")
os.environ["CHAT_DEVICE"] = "cpu"  # testy nikdy nesahají na GPU
os.environ["DJANGO_ALLOWED_HOSTS"] = "testserver,localhost"
django.setup()

TEXT = (
    "The Internet is a global network of computers. People use email and the web every day. "
    "In 2005 many people read blogs and listened to music on the iPod. "
    "Question: What is a computer?\nAnswer: A machine that follows instructions.\n\n"
) * 20


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory):
    """Složka s malým náhodně inicializovaným modelem ve formátu z llm2005.export."""
    import torch
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

    from llm2005.export import export
    from llm2005.model import GPT, GPTConfig

    d = tmp_path_factory.mktemp("model")
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(vocab_size=400, special_tokens=["<|endoftext|>"],
                                  initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), show_progress=False)
    tok.train_from_iterator([TEXT], trainer=trainer)
    tok.save(str(d / "train-tokenizer.json"))

    torch.manual_seed(0)
    cfg = {"name": "test-tiny", "model": {"vocab_size": 400, "block_size": 128, "n_layer": 2, "n_head": 2,
                                          "n_embd": 32}}
    model = GPT(GPTConfig(**cfg["model"]))
    torch.save({"model": model.state_dict(), "optimizer": {}, "step": 7, "config": cfg}, d / "ckpt.pt")
    export(d / "ckpt.pt", d / "train-tokenizer.json", d / "chat")
    return d / "chat"


@pytest.fixture
def api(settings_override, model_dir):
    """Django test klient s cestami k testovacímu modelu; před a po testu se zapomene načtený model i limity."""
    from django.test import Client

    with settings_override(CHAT_CHECKPOINT=str(model_dir / "model.pt"), CHAT_TOKENIZER=str(model_dir / "tokenizer.json")):
        yield Client()


@pytest.fixture
def settings_override():
    from django.test import override_settings

    from chat import engine, views

    def apply(**kwargs):
        engine.reset()
        views._runtime.cache_clear()
        return override_settings(**kwargs)

    yield apply
    engine.reset()
    views._runtime.cache_clear()
