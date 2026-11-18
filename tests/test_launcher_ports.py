import importlib.util
from pathlib import Path
import socket


spec = importlib.util.spec_from_file_location("own_launcher", Path(__file__).resolve().parents[1] / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_busy_saved_port_is_not_reused_or_stopped(tmp_path):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        (tmp_path / "local-port").write_text(str(port))
        selected = launcher.choose_port(tmp_path)
        assert selected != port
        assert busy.getsockname()[1] == port


def test_free_saved_port_is_preserved(tmp_path):
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = candidate.getsockname()[1]
    (tmp_path / "local-port").write_text(str(port))
    assert launcher.choose_port(tmp_path) == port
