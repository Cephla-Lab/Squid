# Statistics display widget
import locale
from typing import Dict, Any

from qtpy.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)


class StatsDisplayWidget(QFrame):
    _layout: QVBoxLayout
    table_widget: QTableWidget

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.initUI()
        self.setFrameStyle(QFrame.Panel | QFrame.Raised)

    def initUI(self) -> None:
        self._layout = QVBoxLayout()
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(2)
        header_v = self.table_widget.verticalHeader()
        header_h = self.table_widget.horizontalHeader()
        if header_v is not None:
            header_v.hide()
        if header_h is not None:
            header_h.hide()
            header_h.setSectionResizeMode(QHeaderView.ResizeToContents)
        self._layout.addWidget(self.table_widget)
        self.setLayout(self._layout)

    def display_stats(self, stats: Dict[str, Any]) -> None:
        print("displaying parasite stats")
        locale.setlocale(locale.LC_ALL, "")
        self.table_widget.setRowCount(len(stats))
        row = 0
        for key, value in stats.items():
            key_item = QTableWidgetItem(str(key))
            value_item = None
            try:
                value_item = QTableWidgetItem(f"{value:n}")
            except Exception:
                value_item = QTableWidgetItem(str(value))
            self.table_widget.setItem(row, 0, key_item)
            self.table_widget.setItem(row, 1, value_item)
            row += 1
