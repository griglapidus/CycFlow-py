"""
Живой просмотр TCP-буферов CycFlow — Python-аналог CycBufReceiver (MVP).

Подключается к серверу CycFlow, читает схему буфера и рисует по одной
дорожке на каждое поле:

    * числовые скаляры             → обычный график,
    * массивы (count > 1)          → один график на каждый индекс "Name[i]",
    * целые с именованными битами  → график самого слова + цифровой график
                                     на каждый именованный бит.

Кнопка «Discover…» опрашивает сервер через TcpServiceClient и показывает
список опубликованных буферов; двойной клик заполняет поля host/port/buffer
и сразу подключается. История точек вида host:port хранится в памяти
приложения (последние 10).

Зависимости (не входят в обычный cycflow):
    pip install PyQt6 pyqtgraph        # или PySide6

Пример запуска:
    python examples/tcp_publish.py      # терминал 1 — поднимает SensorStream
    python examples/tcp_viewer.py       # терминал 2 — открывает окно
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

import cycflow


_NUMERIC_TYPES = {
    cycflow.DataType.Int8, cycflow.DataType.UInt8,
    cycflow.DataType.Int16, cycflow.DataType.UInt16,
    cycflow.DataType.Int32, cycflow.DataType.UInt32,
    cycflow.DataType.Int64, cycflow.DataType.UInt64,
    cycflow.DataType.Float, cycflow.DataType.Double,
}

_INTEGER_TYPES = {
    cycflow.DataType.Int8, cycflow.DataType.UInt8,
    cycflow.DataType.Int16, cycflow.DataType.UInt16,
    cycflow.DataType.Int32, cycflow.DataType.UInt32,
    cycflow.DataType.Int64, cycflow.DataType.UInt64,
}

_DTYPE_MAP = {
    cycflow.DataType.Int8: np.int8,
    cycflow.DataType.UInt8: np.uint8,
    cycflow.DataType.Int16: np.int16,
    cycflow.DataType.UInt16: np.uint16,
    cycflow.DataType.Int32: np.int32,
    cycflow.DataType.UInt32: np.uint32,
    cycflow.DataType.Int64: np.int64,
    cycflow.DataType.UInt64: np.uint64,
    cycflow.DataType.Float: np.float32,
    cycflow.DataType.Double: np.float64,
}

_PEN_COLORS = [
    (31, 119, 180), (255, 127, 14), (44, 160, 44), (214, 39, 40),
    (148, 103, 189), (140, 86, 75), (227, 119, 194), (188, 189, 34),
    (23, 190, 207),
]

_DIGITAL_PEN = (46, 125, 50)


class ChartViewBox(pg.ViewBox):
    """ViewBox whose wheel zooms X by default, Y only with Shift modifier."""

    def wheelEvent(self, ev, axis=None):
        if axis is None:
            mods = ev.modifiers()
            if mods & QtCore.Qt.KeyboardModifier.ShiftModifier:
                axis = 1
            else:
                axis = 0
        super().wheelEvent(ev, axis=axis)


@dataclass
class ChannelSpec:
    display_name: str
    field_name: str
    dtype: np.dtype
    index: Optional[int] = None      # element index inside an array field
    bit_pos: Optional[int] = None    # bit position inside an integer word
    is_digital: bool = False


def _build_channel_specs(rule) -> list[ChannelSpec]:
    """Expand a RecRule into per-track specs (arrays + named bits exploded)."""
    specs: list[ChannelSpec] = []
    for attr in rule:
        if attr.type not in _NUMERIC_TYPES:
            continue
        dtype = np.dtype(_DTYPE_MAP[attr.type])

        if attr.count > 1:
            for i in range(attr.count):
                specs.append(ChannelSpec(
                    display_name=f"{attr.name}[{i}]",
                    field_name=attr.name,
                    dtype=dtype,
                    index=i,
                ))
            continue

        if attr.has_bit_fields() and attr.type in _INTEGER_TYPES:
            specs.append(ChannelSpec(
                display_name=attr.name,
                field_name=attr.name,
                dtype=dtype,
            ))
            for pos, bid in enumerate(attr.bit_ids):
                if bid == 0:
                    continue
                try:
                    bit_name = cycflow.PReg.get_name(bid)
                except Exception:
                    bit_name = f"{attr.name}.bit{pos}"
                specs.append(ChannelSpec(
                    display_name=bit_name,
                    field_name=attr.name,
                    dtype=np.dtype(np.uint8),
                    bit_pos=pos,
                    is_digital=True,
                ))
            continue

        specs.append(ChannelSpec(
            display_name=attr.name,
            field_name=attr.name,
            dtype=dtype,
        ))
    return specs


def _extract(batch: np.ndarray, spec: ChannelSpec) -> np.ndarray:
    col = batch[spec.field_name]
    if spec.index is not None:
        col = col[:, spec.index]
    if spec.bit_pos is not None:
        col = ((col >> spec.bit_pos) & 1).astype(np.uint8)
    return col


@dataclass
class Channel:
    spec: ChannelSpec
    buf: np.ndarray
    curve: object
    plot: object
    head: int = 0       # next write slot (= stored while stored < cap)
    stored: int = 0     # valid records currently in buf (≤ cap)
    written: int = 0    # cumulative records ever received (absolute X origin)

    def reset(self) -> None:
        self.head = 0
        self.stored = 0
        self.written = 0

    def resize(self, size: int) -> None:
        if size == len(self.buf):
            return
        if self.stored < len(self.buf):
            existing = self.buf[:self.stored]
        else:
            existing = np.concatenate((self.buf[self.head:], self.buf[:self.head]))
        new_buf = np.zeros(size, dtype=self.buf.dtype)
        keep = min(len(existing), size)
        if keep > 0:
            new_buf[:keep] = existing[-keep:]
        self.buf = new_buf
        self.stored = keep
        self.head = keep % size if size > 0 else 0

    def append(self, data: np.ndarray) -> None:
        n = len(data)
        if n == 0:
            return
        cap = len(self.buf)
        if n >= cap:
            self.buf[:] = data[-cap:]
            self.head = 0
            self.stored = cap
        else:
            end = self.head + n
            if end <= cap:
                self.buf[self.head:end] = data
            else:
                first = cap - self.head
                self.buf[self.head:] = data[:first]
                self.buf[:n - first] = data[first:]
            self.head = end % cap
            self.stored = min(self.stored + n, cap)
        self.written += n

    def view(self) -> tuple[np.ndarray, np.ndarray]:
        cap = len(self.buf)
        if self.stored < cap:
            y = self.buf[:self.stored]
        else:
            y = np.concatenate((self.buf[self.head:], self.buf[:self.head]))
        x0 = self.written - self.stored
        x = np.arange(x0, x0 + len(y))
        return x, y


class DiscoveryDialog(QtWidgets.QDialog):
    """Query a CycFlow server for its buffer list and pick one."""

    HISTORY_CAP = 10

    def __init__(self, parent=None, history: Optional[list[str]] = None,
                 last_endpoint: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Discover buffers")
        self.resize(440, 360)

        self._selected_host = ""
        self._selected_port = 0
        self._selected_name = ""

        root = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QHBoxLayout()
        form.addWidget(QtWidgets.QLabel("host:port"))
        self._combo = QtWidgets.QComboBox()
        self._combo.setEditable(True)
        if history:
            self._combo.addItems(history)
        if last_endpoint:
            self._combo.setCurrentText(last_endpoint)
        form.addWidget(self._combo, stretch=1)
        self._btn_query = QtWidgets.QPushButton("Query")
        self._btn_query.clicked.connect(self._on_query)
        form.addWidget(self._btn_query)
        root.addLayout(form)

        self._list = QtWidgets.QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self._accept())
        root.addWidget(self._list, stretch=1)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("QLabel { color: palette(mid); }")
        root.addWidget(self._status)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _parse_endpoint(self) -> Optional[tuple[str, int]]:
        text = self._combo.currentText().strip()
        if ":" not in text:
            return None
        host, _, port_s = text.rpartition(":")
        host = host.strip()
        if not host:
            return None
        try:
            port = int(port_s.strip())
        except ValueError:
            return None
        return host, port

    def _on_query(self) -> None:
        self._list.clear()
        endpoint = self._parse_endpoint()
        if endpoint is None:
            self._status.setText("format: host:port")
            return
        host, port = endpoint
        self._status.setText(f"querying {host}:{port}…")
        QtWidgets.QApplication.processEvents()
        try:
            names = cycflow.TcpServiceClient.request_buffer_list(host, port)
        except Exception as e:
            self._status.setText(f"failed: {e}")
            return
        names = list(names or [])
        if not names:
            self._status.setText(f"{host}:{port}: no buffers")
            return
        self._list.addItems(names)
        self._list.setCurrentRow(0)
        self._status.setText(f"{host}:{port}: {len(names)} buffer(s)")

    def _accept(self) -> None:
        endpoint = self._parse_endpoint()
        if endpoint is None:
            self._status.setText("format: host:port")
            return
        item = self._list.currentItem()
        if item is None:
            self._status.setText("pick a buffer")
            return
        self._selected_host, self._selected_port = endpoint
        self._selected_name = item.text()
        self.accept()

    def selection(self) -> tuple[str, int, str]:
        return self._selected_host, self._selected_port, self._selected_name

    def endpoint_text(self) -> str:
        return self._combo.currentText().strip()


class ViewerWindow(QtWidgets.QMainWindow):
    TIMER_MS = 25
    BUFFER_CAPACITY = 65536
    BATCH_CAPACITY = 1000
    ENDPOINT_HISTORY_CAP = 10
    MIN_PLOT_HEIGHT = 110
    PLOT_SPACING = 2

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CycFlow buffer viewer")
        self.resize(960, 720)

        self._rx: cycflow.TcpDataReceiver | None = None
        self._reader: cycflow.RecordReader | None = None
        self._channels: list[Channel] = []
        self._paused = False
        self._auto_fit_y = False
        self._history_size = 10_000
        self._rate_t0 = 0.0
        self._rate_count = 0
        self._endpoint_history: list[str] = []

        self._build_toolbar()

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        top = QtWidgets.QHBoxLayout()
        self._host = QtWidgets.QLineEdit("127.0.0.1")
        self._port = QtWidgets.QLineEdit("5000")
        self._port.setFixedWidth(70)
        self._buffer = QtWidgets.QLineEdit("SensorStream")
        self._btn_discover = QtWidgets.QPushButton("Discover…")
        self._btn_discover.clicked.connect(self._on_discover)
        self._btn_conn = QtWidgets.QPushButton("Connect")
        self._btn_conn.clicked.connect(self._toggle_connection)
        self._status = QtWidgets.QLabel("disconnected")
        top.addWidget(QtWidgets.QLabel("host:"))
        top.addWidget(self._host)
        top.addWidget(QtWidgets.QLabel("port:"))
        top.addWidget(self._port)
        top.addWidget(QtWidgets.QLabel("buffer:"))
        top.addWidget(self._buffer, stretch=1)
        top.addWidget(self._btn_discover)
        top.addWidget(self._btn_conn)
        top.addWidget(self._status, stretch=1)
        root.addLayout(top)

        ctrl = QtWidgets.QHBoxLayout()
        ctrl.addWidget(QtWidgets.QLabel("history (records):"))
        self._history_spin = QtWidgets.QSpinBox()
        self._history_spin.setRange(100, 1_000_000)
        self._history_spin.setSingleStep(1000)
        self._history_spin.setGroupSeparatorShown(True)
        self._history_spin.setValue(self._history_size)
        self._history_spin.setToolTip(
            "How many most-recent records to keep in memory and plot.\n"
            "Increasing preserves existing data; decreasing drops the oldest."
        )
        self._history_spin.editingFinished.connect(self._on_history_changed)
        ctrl.addWidget(self._history_spin)
        self._btn_pause = QtWidgets.QPushButton("Pause")
        self._btn_pause.setCheckable(True)
        self._btn_pause.toggled.connect(self._on_pause_toggled)
        ctrl.addWidget(self._btn_pause)
        self._btn_clear = QtWidgets.QPushButton("Clear")
        self._btn_clear.clicked.connect(self._on_clear)
        ctrl.addWidget(self._btn_clear)
        ctrl.addStretch(1)
        root.addLayout(ctrl)

        self._plot_layout = pg.GraphicsLayoutWidget()
        self._plot_layout.setBackground("w")
        self._plot_layout.ci.layout.setVerticalSpacing(self.PLOT_SPACING)
        self._plot_layout.ci.layout.setContentsMargins(2, 2, 2, 2)

        self._plot_scroll = QtWidgets.QScrollArea()
        self._plot_scroll.setWidgetResizable(True)
        self._plot_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._plot_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._plot_scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._plot_scroll.setWidget(self._plot_layout)
        root.addWidget(self._plot_scroll, stretch=1)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self.TIMER_MS)
        self._timer.timeout.connect(self._on_tick)

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("chart")
        tb.setMovable(False)
        tb.setFloatable(False)

        act_x_in = tb.addAction("X +")
        act_x_in.setToolTip("Zoom X in")
        act_x_in.triggered.connect(lambda: self._zoom_x(0.8))
        act_x_out = tb.addAction("X \u2212")
        act_x_out.setToolTip("Zoom X out")
        act_x_out.triggered.connect(lambda: self._zoom_x(1.25))
        tb.addSeparator()

        act_y_in = tb.addAction("Y +")
        act_y_in.setToolTip("Zoom Y in (all plots)")
        act_y_in.triggered.connect(lambda: self._zoom_y(0.8))
        act_y_out = tb.addAction("Y \u2212")
        act_y_out.setToolTip("Zoom Y out (all plots)")
        act_y_out.triggered.connect(lambda: self._zoom_y(1.25))
        tb.addSeparator()

        self._act_auto_y = tb.addAction("Auto Y")
        self._act_auto_y.setCheckable(True)
        self._act_auto_y.setToolTip("Auto-fit Y to the visible X range")
        self._act_auto_y.toggled.connect(self._on_auto_y_toggled)
        tb.addSeparator()

        act_reset = tb.addAction("Reset")
        act_reset.setToolTip("Reset X/Y ranges to auto")
        act_reset.triggered.connect(self._reset_view)

        spacer = QtWidgets.QWidget(tb)
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                             QtWidgets.QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        hint = QtWidgets.QLabel("Wheel: X  |  Shift+Wheel: Y  |  LMB: pan", tb)
        hint.setStyleSheet("QLabel { color: palette(mid); padding: 0 8px; }")
        tb.addWidget(hint)

    def _on_discover(self) -> None:
        if self._rx is not None:
            self._disconnect()
        last = f"{self._host.text().strip()}:{self._port.text().strip()}"
        dlg = DiscoveryDialog(self,
                              history=list(self._endpoint_history),
                              last_endpoint=last)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self._push_endpoint_history(dlg.endpoint_text())
        host, port, name = dlg.selection()
        self._host.setText(host)
        self._port.setText(str(port))
        self._buffer.setText(name)
        self._connect()

    def _push_endpoint_history(self, ep: str) -> None:
        if not ep:
            return
        if ep in self._endpoint_history:
            self._endpoint_history.remove(ep)
        self._endpoint_history.insert(0, ep)
        del self._endpoint_history[self.ENDPOINT_HISTORY_CAP:]

    def _toggle_connection(self) -> None:
        if self._rx is None:
            self._connect()
        else:
            self._disconnect()

    def _connect(self) -> None:
        host = self._host.text().strip()
        buffer_name = self._buffer.text().strip()
        try:
            port = int(self._port.text().strip())
        except ValueError:
            self._status.setText("invalid port")
            return
        if not host or not buffer_name:
            self._status.setText("fill host and buffer")
            return

        self._status.setText(f"connecting to {host}:{port}/{buffer_name}…")
        QtWidgets.QApplication.processEvents()

        rx = cycflow.TcpDataReceiver(buffer_capacity=self.BUFFER_CAPACITY)
        if not rx.connect(host, port, buffer_name):
            self._status.setText(f"failed: {host}:{port}/{buffer_name}")
            return

        rule = rx.get_buffer().get_rule()
        specs = _build_channel_specs(rule)
        if not specs:
            rx.stop()
            self._status.setText("no plottable fields in buffer")
            return

        self._push_endpoint_history(f"{host}:{port}")
        self._build_plots(specs)
        self._reader = cycflow.RecordReader(rx.get_buffer(),
                                            batch_capacity=self.BATCH_CAPACITY)
        self._rx = rx
        self._rate_t0 = time.monotonic()
        self._rate_count = 0
        self._btn_conn.setText("Disconnect")
        self._status.setText(f"connected • {len(specs)} track(s)")
        self._timer.start()

    def _disconnect(self) -> None:
        self._timer.stop()
        if self._reader is not None:
            self._reader.stop()
            self._reader = None
        if self._rx is not None:
            self._rx.stop()
            self._rx = None
        self._btn_conn.setText("Connect")
        self._status.setText("disconnected")

    def _build_plots(self, specs: list[ChannelSpec]) -> None:
        self._plot_layout.clear()
        self._channels.clear()
        self._plot_layout.setMinimumHeight(0)

        tick_font = QtGui.QFont()
        tick_font.setPointSize(8)

        first = None
        color_idx = 0
        last_row = len(specs) - 1
        for row, spec in enumerate(specs):
            vb = ChartViewBox()
            p = self._plot_layout.addPlot(row=row, col=0, viewBox=vb)
            p.setTitle(spec.display_name, size="9pt")
            p.showGrid(x=True, y=True, alpha=0.3)
            p.hideButtons()
            p.setMinimumHeight(self.MIN_PLOT_HEIGHT)

            for ax_name in ("left", "bottom"):
                ax = p.getAxis(ax_name)
                ax.setStyle(tickFont=tick_font, tickLength=-4)
            p.getAxis("left").setWidth(48)
            if row != last_row:
                p.getAxis("bottom").setStyle(showValues=False, tickLength=0)
                p.getAxis("bottom").setHeight(6)
            else:
                p.getAxis("bottom").setHeight(22)

            if spec.is_digital:
                pen = pg.mkPen(color=_DIGITAL_PEN, width=1)
                curve = p.plot([], [], pen=pen, stepMode="right")
                p.setYRange(-0.2, 1.2, padding=0)
                vb.setMouseEnabled(x=True, y=False)
                vb.setLimits(yMin=-0.2, yMax=1.2)
            else:
                pen = pg.mkPen(color=_PEN_COLORS[color_idx % len(_PEN_COLORS)],
                               width=1)
                color_idx += 1
                curve = p.plot([], [], pen=pen)

            if first is None:
                first = p
            else:
                p.setXLink(first)

            buf = np.zeros(self._history_size, dtype=spec.dtype)
            self._channels.append(Channel(spec=spec, buf=buf,
                                          curve=curve, plot=p))

        total = max(len(specs), 1) * (self.MIN_PLOT_HEIGHT + self.PLOT_SPACING)
        self._plot_layout.setMinimumHeight(total)

    def _on_tick(self) -> None:
        if self._reader is None:
            return
        new_records = 0
        while True:
            batch = self._reader.next_batch_copy(self.BATCH_CAPACITY, False)
            if batch is None:
                break
            for ch in self._channels:
                ch.append(_extract(batch, ch.spec))
            new_records += len(batch)

        self._rate_count += new_records
        now = time.monotonic()
        dt = now - self._rate_t0
        if dt >= 0.5:
            rate = self._rate_count / dt
            self._status.setText(f"connected • {rate:,.0f} rec/s")
            self._rate_t0 = now
            self._rate_count = 0

        if self._paused:
            return
        for ch in self._channels:
            x, y = ch.view()
            ch.curve.setData(x, y)
            if self._auto_fit_y and not ch.spec.is_digital:
                self._autofit_y_to_visible(ch, x, y)

    def _zoom_x(self, factor: float) -> None:
        if not self._channels:
            return
        vb = self._channels[0].plot.getViewBox()
        x_min, x_max = vb.viewRange()[0]
        cx = 0.5 * (x_min + x_max)
        half = 0.5 * (x_max - x_min) * factor
        vb.setXRange(cx - half, cx + half, padding=0)

    def _zoom_y(self, factor: float) -> None:
        for ch in self._channels:
            if ch.spec.is_digital:
                continue
            vb = ch.plot.getViewBox()
            y_min, y_max = vb.viewRange()[1]
            cy = 0.5 * (y_min + y_max)
            half = 0.5 * (y_max - y_min) * factor
            vb.setYRange(cy - half, cy + half, padding=0)

    def _reset_view(self) -> None:
        for ch in self._channels:
            if ch.spec.is_digital:
                ch.plot.enableAutoRange(axis='x')
                ch.plot.getViewBox().setYRange(-0.2, 1.2, padding=0)
            else:
                ch.plot.enableAutoRange(axis='xy')

    def _on_auto_y_toggled(self, on: bool) -> None:
        self._auto_fit_y = on
        if on and self._channels:
            for ch in self._channels:
                if ch.spec.is_digital:
                    continue
                x, y = ch.view()
                self._autofit_y_to_visible(ch, x, y)

    def _autofit_y_to_visible(self, ch: Channel,
                              x: np.ndarray, y: np.ndarray) -> None:
        if len(y) == 0:
            return
        vb = ch.plot.getViewBox()
        x_min, x_max = vb.viewRange()[0]
        mask = (x >= x_min) & (x <= x_max)
        if not mask.any():
            return
        ys = y[mask]
        y_lo = float(ys.min())
        y_hi = float(ys.max())
        if y_lo == y_hi:
            pad = 1.0 if y_lo == 0.0 else abs(y_lo) * 0.05
        else:
            pad = (y_hi - y_lo) * 0.05
        vb.setYRange(y_lo - pad, y_hi + pad, padding=0)

    def _on_pause_toggled(self, on: bool) -> None:
        self._paused = on
        self._btn_pause.setText("Resume" if on else "Pause")

    def _on_clear(self) -> None:
        for ch in self._channels:
            ch.reset()
            ch.curve.setData([], [])

    def _on_history_changed(self) -> None:
        val = int(self._history_spin.value())
        if val == self._history_size:
            return
        self._history_size = val
        for ch in self._channels:
            ch.resize(self._history_size)

    def closeEvent(self, ev) -> None:
        self._disconnect()
        super().closeEvent(ev)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    w = ViewerWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
