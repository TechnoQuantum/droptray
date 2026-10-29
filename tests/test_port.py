"""The receiver moves off a port another app holds, and keeps the one that worked."""
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core  # noqa: E402


def fake_app(port):
    saved = []
    store = SimpleNamespace(port=port, port_pinned=True, save_conf=lambda: saved.append(store.port))
    return SimpleNamespace(store=store, port_moved=None), saved


def hold(port, family=socket.AF_INET):
    s = socket.socket(family, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind(("::" if family == socket.AF_INET6 else "0.0.0.0", port))
    s.listen()
    return s


core.DEFAULT_PORT = base = 47900          # clear of a DropTray that may be running
blockers = [hold(base), hold(base + 1, socket.AF_INET6)]       # an IPv4 app, and one on IPv6 only
try:
    app, saved = fake_app(base)
    server = core.open_server(app)
    got = server.server_address[1]
    server.server_close()
    assert got == base + 2, got
    assert app.port_moved == (base, base + 2), app.port_moved
    assert app.store.port == base + 2 and saved == [base + 2] and not app.store.port_pinned

    app, saved = fake_app(base + 5)                                  # a free saved port is kept as is
    server = core.open_server(app)
    assert server.server_address[1] == base + 5 and app.port_moved is None and saved == []
    server.server_close()
finally:
    for b in blockers:
        b.close()
print("port fallback tests passed")
