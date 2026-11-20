"""Local, read-only guidance for a first organization server; no telemetry or hard gate."""
import ctypes
import os
from pathlib import Path
import shutil

GIB = 1024 ** 3
RECOMMENDATION = 'Для первых тестов: 2 потока CPU · 4 ГБ RAM · 20 ГБ свободного места.'


def physical_memory():
    if os.name != 'nt':
        return None
    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32)] + [
            (name, ctypes.c_uint64) for name in ('total_physical', 'available_physical',
                'total_pagefile', 'available_pagefile', 'total_virtual', 'available_virtual', 'available_extended')]
    state = MemoryStatus()
    state.length = ctypes.sizeof(state)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    kernel.GlobalMemoryStatusEx.restype = ctypes.c_int
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(state)):
        raise OSError('Physical memory could not be read')
    return int(state.total_physical)


def collect():
    result = {'logical_cpus': None, 'ram_bytes': None, 'free_disk_bytes': None}
    try:
        result['logical_cpus'] = os.cpu_count()
    except OSError:
        pass
    try:
        result['ram_bytes'] = physical_memory()
    except (OSError, AttributeError):
        pass
    try:
        root = Path(os.environ.get('ProgramFiles', str(Path.home()))).anchor
        result['free_disk_bytes'] = shutil.disk_usage(root or '.').free
    except OSError:
        pass
    return result


def describe(values):
    def gib(value):
        return f'{value / GIB:.1f}'.replace('.', ',') + ' ГБ' if value is not None else 'не определено'
    cpu, ram, disk = (values[key] for key in ('logical_cpus', 'ram_bytes', 'free_disk_bytes'))
    actual = f"Этот компьютер: CPU {cpu if cpu is not None else 'не определено'} · RAM {gib(ram)} · свободно {gib(disk)}."
    warnings = []
    if cpu is not None and cpu < 2:
        warnings.append('Меньше 2 потоков CPU.')
    if ram is not None and ram < 4 * GIB:
        warnings.append('RAM ниже стартовой рекомендации.')
    if disk is not None and disk < 20 * GIB:
        warnings.append('Свободного места меньше 20 ГБ.')
    if warnings:
        advice = ' '.join(warnings) + ' Проверьте запас ресурсов перед установкой.'
    elif any(value is None for value in (cpu, ram, disk)):
        advice = 'Часть характеристик не определена — проверьте их вручную.'
    else:
        advice = 'Характеристики соответствуют стартовой рекомендации; производительность зависит от нагрузки.'
    return actual, advice
