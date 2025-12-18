import numpy as np


def test_qt_stream_handler_capture_bridges_to_qt_signal(qtbot):
    from squid.backend.io.stream_handler import StreamHandler, StreamHandlerFunctions
    from squid.ui.qt_stream_handler import QtStreamHandler

    backend_capture_calls: list[object] = []

    def backend_capture(_image: np.ndarray, info: object) -> None:
        backend_capture_calls.append(info)

    handler = StreamHandler(
        handler_functions=StreamHandlerFunctions(
            image_to_display=lambda _image: None,
            packet_image_to_write=lambda _image, _frame_id, _ts: None,
            signal_new_frame_received=lambda: None,
            accept_new_frame=lambda: True,
            capture=backend_capture,
        )
    )

    qt_handler = QtStreamHandler(handler=handler)
    qt_capture_calls: list[object] = []
    qt_handler.capture.connect(lambda _image, info: qt_capture_calls.append(info))

    info = object()
    handler.on_new_image(np.zeros((8, 8), dtype=np.uint8), capture_info=info)

    qtbot.waitUntil(lambda: len(qt_capture_calls) == 1, timeout=1000)
    assert backend_capture_calls == [info]
    assert qt_capture_calls == [info]


def test_qt_stream_handler_merge_accept_new_frame_is_and(qtbot):
    from squid.backend.io.stream_handler import StreamHandler, StreamHandlerFunctions
    from squid.ui.qt_stream_handler import QtStreamHandler

    backend_accept = {"value": True}

    handler = StreamHandler(
        handler_functions=StreamHandlerFunctions(
            image_to_display=lambda _image: None,
            packet_image_to_write=lambda _image, _frame_id, _ts: None,
            signal_new_frame_received=lambda: None,
            accept_new_frame=lambda: backend_accept["value"],
        )
    )

    qt_accept = {"value": False}
    qt_handler = QtStreamHandler(handler=handler, accept_new_frame_fn=lambda: qt_accept["value"])

    capture_calls: list[object] = []
    qt_handler.capture.connect(lambda _image, info: capture_calls.append(info))

    info = object()
    handler.on_new_image(
        np.zeros((4, 4), dtype=np.uint8),
        respect_accept_new_frame=True,
        capture_info=info,
    )
    assert capture_calls == []

    qt_accept["value"] = True
    handler.on_new_image(
        np.zeros((4, 4), dtype=np.uint8),
        respect_accept_new_frame=True,
        capture_info=info,
    )
    qtbot.waitUntil(lambda: len(capture_calls) == 1, timeout=1000)

    backend_accept["value"] = False
    handler.on_new_image(
        np.zeros((4, 4), dtype=np.uint8),
        respect_accept_new_frame=True,
        capture_info=info,
    )
    assert len(capture_calls) == 1

