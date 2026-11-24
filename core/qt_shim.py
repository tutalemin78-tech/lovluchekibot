"""Заглушка PyQt6 для запуска БЕЗ графики (сервер, Render, VPS): сигналы работают как обычные колбэки."""
from __future__ import annotations


class _Bound:
    def __init__(self) -> None:
        self._handlers: list = []

    def connect(self, fn) -> None:
        self._handlers.append(fn)

    def emit(self, *args) -> None:
        for fn in list(self._handlers):
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001
                print("signal error:", repr(e), flush=True)


class pyqtSignal:  # noqa: N801
    def __init__(self, *types) -> None:
        self._name = ""

    def __set_name__(self, owner, name) -> None:
        self._name = "_sig_" + name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        bound = obj.__dict__.get(self._name)
        if bound is None:
            bound = obj.__dict__[self._name] = _Bound()
        return bound


class QObject:
    def __init__(self, *args, **kwargs) -> None:
        pass
