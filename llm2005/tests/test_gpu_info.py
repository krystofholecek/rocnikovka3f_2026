"""Test rozboru výstupu nvidia-smi a varování před plnou pamětí GPU."""
from llm2005.gpu_info import parse_smi
from llm2005.train import vram_warning


def test_parse_smi():
    s = parse_smi("97, 6144, 12288, 64, 151.20")
    assert s == {"util_pct": 97.0, "mem_used_gb": 6.0, "mem_total_gb": 12.0, "temp_c": 64.0, "power_w": 151.2}


def test_parse_smi_missing_values():
    s = parse_smi("[N/A], 1000, 12288, 50, [N/A]")
    assert s["util_pct"] is None and s["power_w"] is None and s["temp_c"] == 50.0
    assert parse_smi("nesmysl") is None


def test_vram_warning():
    assert vram_warning(None) is None
    assert vram_warning({"mem_used_gb": 6.0, "mem_total_gb": 12.0}) is None
    assert "skoro plné" in vram_warning({"mem_used_gb": 11.8, "mem_total_gb": 12.0})
