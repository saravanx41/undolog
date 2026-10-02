"""Registry directory loading: per-tool YAML files merged over the seed.

Backward-compatible extension of the loader: load_registry() keeps loading
the bundled seed (exactly 10 tools) unless registry_dir= is given, in which
case every *.yaml file in that directory is merged OVER the seed (dir wins
on conflicts). Core understands only class/compensation/snapshot_capable;
anything else (e.g. an adapter spec) is exposed verbatim on ToolSpec.adapter.
"""
from undolog_core import registry as reg


def _write(tmp_path, name, body):
    (tmp_path / name).write_text(body, encoding="utf-8")


def test_dir_merges_over_seed(tmp_path):
    _write(
        tmp_path,
        "acme.one.yaml",
        "class: reversible\n"
        "adapter:\n"
        "  type: rest\n"
        "  base_url: https://one.example\n",
    )
    _write(tmp_path, "acme.two.yaml", "class: compensatable\n")
    r = reg.load_registry(registry_dir=tmp_path)
    assert len(r) == 12
    one = r.lookup("acme.one")
    assert one is not None
    assert one.entry_class == "reversible"
    assert one.adapter == {"type": "rest", "base_url": "https://one.example"}
    assert r.lookup("acme.two").entry_class == "compensatable"


def test_seed_untouched_by_dir_load(tmp_path):
    _write(tmp_path, "acme.one.yaml", "class: irreversible\n")
    r = reg.load_registry(registry_dir=tmp_path)
    assert r.lookup("gmail.send").entry_class == "irreversible"
    assert r.lookup("stripe.create_charge").entry_class == "compensatable"


def test_dir_wins_on_conflict(tmp_path):
    _write(tmp_path, "gmail.send.yaml", "class: compensatable\nadapter: null\n")
    r = reg.load_registry(registry_dir=tmp_path)
    assert len(r) == 10
    assert r.lookup("gmail.send").entry_class == "compensatable"


def test_dir_without_adapter_key_exposes_extras(tmp_path):
    _write(
        tmp_path,
        "acme.three.yaml",
        "class: reversible\nsnapshot_capable: true\nflavor: custom\n",
    )
    r = reg.load_registry(registry_dir=tmp_path)
    spec = r.lookup("acme.three")
    assert spec.snapshot_capable is True
    assert spec.adapter == {"flavor": "custom"}


def test_default_load_still_yields_exactly_ten():
    assert len(reg.load_registry()) == 10
