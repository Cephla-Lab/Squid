from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Type
import time

from qtpy.QtWidgets import QApplication


def process_events() -> None:
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def click_widget(widget: Any) -> None:
    widget.click()
    process_events()


def set_spinbox_value(spinbox: Any, value: float) -> None:
    spinbox.setValue(value)
    process_events()


def set_slider_value(slider: Any, value: int) -> None:
    slider.setValue(value)
    process_events()


def set_checkbox_value(checkbox: Any, value: bool) -> None:
    checkbox.setChecked(value)
    process_events()


def set_combobox_text(combo: Any, text: str) -> None:
    combo.setCurrentText(text)
    process_events()


def set_line_edit_text(line_edit: Any, text: str) -> None:
    line_edit.setText(text)
    process_events()


def apply_gui_flags(
    monkeypatch: Any,
    *,
    extra_modules: Iterable[Any] = (),
    **flags: Any,
) -> None:
    import _def
    import squid.ui.main_window as main_window
    import squid.ui.gui.widget_factory as widget_factory
    import squid.ui.gui.layout_builder as layout_builder

    modules = [main_window, widget_factory, layout_builder, *list(extra_modules)]
    for name, value in flags.items():
        monkeypatch.setattr(_def, name, value, raising=False)
        for module in modules:
            monkeypatch.setattr(module, name, value, raising=False)


class EventCollector:
    def __init__(self, event_bus: Any):
        self._bus = event_bus
        self._events: Dict[Type[Any], List[Any]] = defaultdict(list)
        self._subscriptions: List[Tuple[Type[Any], Callable[[Any], None]]] = []

    def subscribe(self, *event_types: Type[Any]) -> "EventCollector":
        for event_type in event_types:
            def _handler(event: Any, _event_type: Type[Any] = event_type) -> None:
                self._events[_event_type].append(event)

            self._bus.subscribe(event_type, _handler)
            self._subscriptions.append((event_type, _handler))
        return self

    def unsubscribe_all(self) -> None:
        for event_type, handler in self._subscriptions:
            try:
                self._bus.unsubscribe(event_type, handler)
            except Exception:
                pass
        self._subscriptions.clear()

    def events(self, event_type: Type[Any]) -> List[Any]:
        return list(self._events[event_type])

    def last(self, event_type: Type[Any]) -> Optional[Any]:
        events = self._events[event_type]
        return events[-1] if events else None

    def wait_for(
        self,
        event_type: Type[Any],
        *,
        timeout_s: float = 1.0,
        predicate: Optional[Callable[[Any], bool]] = None,
    ) -> Any:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            process_events()
            event = self.last(event_type)
            if event is not None:
                if predicate is None or predicate(event):
                    return event
            time.sleep(0.01)
        raise AssertionError(f"Timed out waiting for {event_type.__name__}")
