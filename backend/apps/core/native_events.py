"""Keep the native in-memory channel layer on the ASGI event loop."""
import asyncio

_loop = None


def bind_loop():
    global _loop
    _loop = asyncio.get_running_loop()


def send_group(layer, group, event):
    loop = _loop
    if loop is None or loop.is_closed():
        return False
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    if current is loop:
        loop.create_task(layer.group_send(group, event))
    else:
        asyncio.run_coroutine_threadsafe(layer.group_send(group, event), loop).result(timeout=5)
    return True
