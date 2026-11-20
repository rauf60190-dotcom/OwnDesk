import importlib.util
from pathlib import Path
from types import SimpleNamespace

module_spec = importlib.util.spec_from_file_location('resource_requirements',
    Path(__file__).resolve().parents[1] / 'infra/resource_requirements.py')
resources = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(resources)


def test_local_metrics_are_reported_without_output(monkeypatch, capsys):
    monkeypatch.setattr(resources.os, 'cpu_count', lambda: 2)
    monkeypatch.setattr(resources, 'physical_memory', lambda: 4 * resources.GIB)
    monkeypatch.setattr(resources.shutil, 'disk_usage', lambda _: SimpleNamespace(free=20 * resources.GIB))
    values = resources.collect()
    assert values == {'logical_cpus': 2, 'ram_bytes': 4 * resources.GIB, 'free_disk_bytes': 20 * resources.GIB}
    summary, advice = resources.describe(values)
    assert 'RAM 4,0 ГБ' in summary and 'свободно 20,0 ГБ' in summary
    assert 'соответствуют' in advice
    assert not capsys.readouterr().out


def test_unavailable_metrics_do_not_prevent_installer_opening(monkeypatch):
    def unavailable(*args):
        raise OSError('synthetic read failure')
    monkeypatch.setattr(resources.os, 'cpu_count', unavailable)
    monkeypatch.setattr(resources, 'physical_memory', unavailable)
    monkeypatch.setattr(resources.shutil, 'disk_usage', unavailable)
    values = resources.collect()
    assert all(value is None for value in values.values())
    summary, advice = resources.describe(values)
    assert 'не определено' in summary and 'вручную' in advice and 'соответствуют' not in advice


def test_low_resources_produce_advice_for_each_shortage():
    _, advice = resources.describe({'logical_cpus': 1, 'ram_bytes': resources.GIB, 'free_disk_bytes': resources.GIB})
    assert '2 потоков' in advice and 'RAM ниже' in advice and '20 ГБ' in advice


def test_partial_reading_does_not_claim_configuration_is_sufficient():
    _, advice = resources.describe({'logical_cpus': 8, 'ram_bytes': None, 'free_disk_bytes': 100 * resources.GIB})
    assert 'вручную' in advice and 'соответствуют' not in advice
