"""Two full-session fluorescence traces for the selected reference and moving ROIs."""
import numpy as np
from PyQt5 import QtCore as C, QtGui as G, QtWidgets as W


class TraceSignals(C.QObject):
    loaded = C.pyqtSignal(str, object, str)


class TraceLoad(C.QRunnable):
    def __init__(self, key, session):
        super().__init__()
        self.key, self.session = key, session
        self.signals = TraceSignals()

    def run(self):
        try:
            values = self.session.read_fluorescence()
        except Exception as exc:
            self.signals.loaded.emit(self.key, None, str(exc) or type(exc).__name__)
        else:
            self.signals.loaded.emit(self.key, values, '')


def trace_vertices(values, bin_size):
    """Keep each bin's extremes in frame order, including the first and last samples."""
    frames = len(values)
    if bin_size <= 2 or frames <= 2:
        return np.arange(frames)
    bins, tail = divmod(frames, bin_size)
    blocks = values[:bins * bin_size].reshape(bins, bin_size)
    starts = np.arange(bins) * bin_size
    indices = [starts + blocks.argmin(axis=1), starts + blocks.argmax(axis=1), [0, frames - 1]]
    if tail:
        rest = values[bins * bin_size:]
        indices.append(bins * bin_size + np.array([rest.argmin(), rest.argmax()]))
    return np.unique(np.concatenate(indices))


class TracePanel(W.QWidget):
    lanes = (('ref', 'Reference', '#ff6262'), ('mov', 'Moving', '#55e578'))

    def __init__(self, data):
        super().__init__()
        self.data = data
        self.selection = {'ref': None, 'mov': None}
        self.values = {}
        self.errors = {}
        self.loading = set()
        self.limits = {}
        self.window_frames = None  # None follows the full length as each session loads.
        self.drag_origin = None
        self.setMinimumHeight(118)
        self.setAccessibleName('Raw fluorescence F by session frame')

    def select(self, reference, moving):
        self.selection = {'ref': reference, 'mov': moving}
        if self.isVisible():
            self.load_selected()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.load_selected()

    def load_selected(self):
        for key, roi in self.selection.items():
            if roi is None or key in self.values or key in self.errors or key in self.loading:
                continue
            self.loading.add(key)
            job = TraceLoad(key, getattr(self.data, key))
            job.signals.loaded.connect(self.loaded, C.Qt.QueuedConnection)
            C.QThreadPool.globalInstance().start(job)

    @C.pyqtSlot(str, object, str)
    def loaded(self, key, values, error):
        self.loading.discard(key)
        if error:
            self.errors[key] = error
            self.setToolTip('\n'.join(f'{getattr(self.data, side).meta["name"]}: {reason}'
                                      for side, reason in self.errors.items()))
        else:
            self.values[key] = values
        self.update()

    @property
    def total_frames(self):
        return max((v.shape[1] for v in self.values.values()), default=0)

    @property
    def visible_frames(self):
        """Shared zero-based [start, stop) interval in both sessions."""
        return self.window_frames or (0, self.total_frames)

    def plot_geometry(self):
        font = G.QFont(self.font())
        font.setPixelSize(12)
        left = G.QFontMetrics(font, self).horizontalAdvance('Reference #0000') + 18
        return font, left, max(left + 1, self.width() - 26)

    def set_frame_window(self, start, count):
        total = self.total_frames
        start = max(0, min(round(start), total - count))
        self.window_frames = (start, start + count) if count < total else None
        self.update()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120 or event.pixelDelta().y() / 80
        if not steps or self.total_frames < 2:
            event.ignore()
            return
        first, stop = self.visible_frames
        _, left, right = self.plot_geometry()
        fraction = max(0., min(1., (event.position().x() - left) / (right - left)))
        factor = 1.4 ** max(-32., min(32., -steps))
        count = max(2, min(self.total_frames, round((stop - first) * factor)))
        self.set_frame_window(first + fraction * (stop - first - count), count)
        self.drag_origin = None
        self.setCursor(C.Qt.OpenHandCursor if self.window_frames else C.Qt.ArrowCursor)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == C.Qt.LeftButton and self.window_frames:
            self.drag_origin = (event.x(), self.window_frames)
            self.setCursor(C.Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def pan_to(self, x):
        anchor, (first, stop) = self.drag_origin
        _, left, right = self.plot_geometry()
        count = stop - first
        self.set_frame_window(first + (anchor - x) * (count - 1) / (right - left), count)

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None and event.buttons() & C.Qt.LeftButton:
            self.pan_to(event.x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == C.Qt.LeftButton and self.drag_origin is not None:
            self.pan_to(event.x())
            self.drag_origin = None
            self.setCursor(C.Qt.OpenHandCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == C.Qt.LeftButton:
            self.drag_origin = None
            self.set_frame_window(0, self.total_frames)
            self.unsetCursor()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def fluorescence_limits(self, key, roi):
        """Keep each ROI's raw F scale stable during horizontal navigation."""
        if (key, roi) not in self.limits:
            trace = self.values[key][roi]
            finite = trace[np.isfinite(trace)]
            low, high = float(finite.min()), float(finite.max())
            if high == low:
                margin = max(.5, abs(low) * .01)
                low, high = low - margin, high + margin
            self.limits[key, roi] = low, high
        return self.limits[key, roi]

    def paintEvent(self, event):
        p = G.QPainter(self)
        p.fillRect(self.rect(), G.QColor('#101618'))
        font, left, right = self.plot_geometry()
        p.setFont(font)
        metrics = p.fontMetrics()
        lane_height = (self.height() - 24) // 2
        first, stop = self.visible_frames
        frames = stop - first
        for i, (key, name, color) in enumerate(self.lanes):
            top = 4 + i * lane_height
            bottom = top + lane_height - 8
            roi = self.selection[key]
            p.setPen(G.QColor(color))
            title = name if roi is None else f'{name} #{roi+1}'
            p.drawText(7, (top + bottom + metrics.ascent()) // 2, title)
            p.setPen(G.QColor('#364044'))
            p.drawLine(left, bottom + 2, right, bottom + 2)
            if roi is None or key not in self.values:
                message = ('No ROI selected' if roi is None else
                           'F unavailable' if key in self.errors else 'Loading F…')
                p.setPen(G.QColor('#a5afb1'))
                p.drawText(left + 5, (top + bottom + metrics.ascent()) // 2, message)
                continue
            trace = self.values[key][roi, first:stop]
            finite = np.isfinite(trace)
            if not finite.any():
                p.setPen(G.QColor('#a5afb1'))
                message = 'No frames in this range' if not trace.size else 'No finite F values'
                p.drawText(left + 5, (top + bottom + metrics.ascent()) // 2, message)
                continue
            low, high = self.fluorescence_limits(key, roi)
            p.setPen(G.QColor('#a5afb1'))
            p.drawText(7, top + metrics.ascent(), 'Raw F')
            p.drawText(C.QRect(0, top, left-5, metrics.height()), C.Qt.AlignRight, f'{high:.5g}')
            p.drawText(C.QRect(0, bottom-metrics.height()+1, left-5, metrics.height()), C.Qt.AlignRight, f'{low:.5g}')
            xscale = (right-left) / max(1, frames-1)
            yscale = (bottom-top) / (high-low)
            bin_size = max(1, int(np.ceil(frames / (right-left))))
            edges = np.flatnonzero(np.diff(finite, prepend=False, append=False))
            path = G.QPainterPath()
            p.setPen(G.QColor(color))
            for start, end in edges.reshape(-1, 2):
                indices = start + trace_vertices(trace[start:end], bin_size)
                xs = left + indices * xscale
                ys = bottom - (trace[indices].astype(float) - low) * yscale
                path.moveTo(xs[0], ys[0])
                if len(indices) == 1:
                    p.drawPoint(C.QPointF(xs[0], ys[0]))
                for x, y in zip(xs[1:], ys[1:]):
                    path.lineTo(x, y)
            p.drawPath(path)
        p.setPen(G.QColor('#b2bdbe'))
        axis = self.height() - metrics.descent() - 3
        p.drawText(7, axis, 'Frame')
        if frames:
            count = min(frames, max(2, min(5, (right-left)//90)))
            for frame in np.linspace(first + 1, stop, count, dtype=int):
                x = left + (frame-1-first) * (right-left) / max(1, frames-1)
                text = str(frame)
                p.drawText(round(x - metrics.horizontalAdvance(text)/2), axis, text)
        p.end()
