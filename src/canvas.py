"""Linked image views in reference pixel coordinates."""
import numpy as np
from PyQt5 import QtCore as C, QtGui as G, QtWidgets as W
from model import normalize_image


REF = '#f276be'
MOV = '#6de3ab'
SELECT = '#ffda74'


def image_layer(values, color):
    rgb = np.empty((*values.shape, 3), dtype=np.uint8)
    for channel, gain in enumerate(color):
        rgb[:, :, channel] = np.rint(gain * values)
    h, w = values.shape
    return G.QImage(rgb.data, w, h, rgb.strides[0], G.QImage.Format_RGB888).copy()


def roi_outline(x, y):
    """Make continuous contours along pixel edges, including holes and islands."""
    if not len(x):
        return G.QPainterPath()
    left, top = x.min(), y.min()
    mask = np.zeros((y.max() - top + 1, x.max() - left + 3), dtype=np.int8)
    mask[y - top, x - left + 1] = 1
    edges = np.diff(mask, axis=1)
    rows, starts = np.where(edges == 1)
    _, stops = np.where(edges == -1)
    path = G.QPainterPath()
    for row, start, stop in zip(rows, starts, stops):
        path.addRect(left + start, top + row, stop - start, 1)
    return path.simplified()


class Layers:
    def __init__(self, data):
        self.images = {}
        self.paths = {}
        self.outlines = {}
        self.roi_masks = {}
        self.confirmed_ids = {'ref': frozenset(), 'mov': frozenset()}
        self.confirmed_images = {'ref': None, 'mov': None}
        for key, session, tint in [('ref', data.ref, (255, 0, 255)), ('mov', data.mov, (0, 255, 0))]:
            for field in ('mean', 'enhanced'):
                norm = normalize_image(getattr(session, field))
                self.images[key, field, True] = image_layer(norm, tint)
                self.images[key, field, False] = image_layer(norm, (255, 255, 255))
            projection = session.footprint_projection()
            self.roi_masks[key, True] = projection > 0
            full_mask = np.zeros(session.shape, dtype=bool)
            full_mask[session.y, session.x] = True
            self.roi_masks[key, False] = full_mask
            color = (255, 0, 0) if key == 'ref' else (0, 255, 0)
            footprint = image_layer(projection, color)
            self.images[key, 'footprints', True] = footprint
            self.images[key, 'footprints', False] = footprint
            for footprint_only in (False, True):
                paths = []
                combined = G.QPainterPath()
                for i in range(session.n):
                    xy = session.footprint_samples(i)[:2] if footprint_only else session.pixels(i)
                    path = roi_outline(*xy)
                    paths.append(path)
                    combined.addPath(path)
                self.paths[key, footprint_only] = paths
                self.outlines[key, footprint_only] = combined

    def update_confirmed(self, data, reviews):
        accepted = [(int(ref), entry['moving']) for ref, entry in reviews.items()
                    if entry['status'] == 'accepted']
        for key, session, column in [('ref', data.ref, 0), ('mov', data.mov, 1)]:
            ids = frozenset(pair[column] for pair in accepted)
            if ids == self.confirmed_ids[key]:
                continue
            self.confirmed_ids[key] = ids
            self.confirmed_images[key] = None
            if ids:
                mask = session.footprint_projection(ids) > 0
                pixels = np.where(mask, np.uint32(0xffffffff), np.uint32(0))
                h, w = mask.shape
                self.confirmed_images[key] = G.QImage(
                    pixels.data, w, h, pixels.strides[0], G.QImage.Format_ARGB32_Premultiplied
                ).copy()


def qt_matrix(m):
    return G.QTransform(m[0, 0], m[1, 0], m[0, 1], m[1, 1], m[0, 2], m[1, 2])


class Canvas(W.QWidget):
    picked = C.pyqtSignal(str, float, float)
    confirmRequested = C.pyqtSignal()
    referencePickRequested = C.pyqtSignal(float, float)
    reopenRequested = C.pyqtSignal(str, float, float)
    shifted = C.pyqtSignal(float, float)
    shiftStarted = C.pyqtSignal()
    panStarted = C.pyqtSignal()
    navigated = C.pyqtSignal(object, float)

    def __init__(self, role, shape, layers, transform):
        super().__init__()
        self.role, self.shape, self.layers = role, shape, layers
        self.transform = transform
        self.center = C.QPointF(shape[1] / 2, shape[0] / 2)
        self.zoom = 1.
        self.field = 'footprints'
        self.outlines = False
        self.balance = 50
        self.pivot_mode = False
        self.selected = {'ref': None, 'mov': None}
        self.dash_offset = 0
        self.outline_rect = C.QRect()
        self.outline_timer = C.QTimer(self)
        self.outline_timer.setInterval(75)
        self.outline_timer.timeout.connect(self.advance_outline)
        self.drag = None
        self.last = None
        self.setMinimumSize(120, 150)
        self.setMouseTracking(True)
        self.setFocusPolicy(C.Qt.ClickFocus)

    @property
    def factor(self):
        return min(max(self.width()-16, 1) / self.shape[1],
                   max(self.height()-16, 1) / self.shape[0]) * self.zoom

    def to_scene(self, point):
        return self.center + (C.QPointF(point) - C.QPointF(self.width()/2, self.height()/2)) / self.factor

    def pose(self, center, zoom, broadcast=False):
        self.center, self.zoom = C.QPointF(center), float(zoom)
        self.update()
        if broadcast:
            self.navigated.emit(self.center, self.zoom)

    def fit(self):
        self.pose(C.QPointF(self.shape[1]/2, self.shape[0]/2), 1., True)

    def advance_outline(self):
        self.dash_offset = (self.dash_offset + 1) % 12
        self.update(self.outline_rect)

    def hideEvent(self, event):
        self.outline_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = G.QPainter(self)
        try:
            p.fillRect(self.rect(), C.Qt.black if self.field == 'footprints' else G.QColor('#11181d'))
            p.translate(self.width()/2, self.height()/2)
            p.scale(self.factor, self.factor)
            p.translate(-self.center)
            h, w = self.shape
            p.fillRect(C.QRectF(0, 0, w, h), C.Qt.black)
            overlay = self.role in ('overlay', 'review')
            footprint_only = self.field == 'footprints'
            p.setRenderHint(G.QPainter.SmoothPixmapTransform, not footprint_only)
            keys = ('ref', 'mov') if overlay else (self.role,)
            for key in keys:
                p.save()
                if key == 'mov':
                    p.setWorldTransform(qt_matrix(self.transform.matrix()), True)
                if overlay:
                    p.setOpacity(min(1., (100-self.balance if key == 'ref' else self.balance) / 50))
                    p.setCompositionMode(G.QPainter.CompositionMode_Plus)
                p.drawImage(C.QPointF(), self.layers.images[key, self.field, overlay])
                p.restore()
            for key in keys:
                p.save()
                if key == 'mov':
                    p.setWorldTransform(qt_matrix(self.transform.matrix()), True)
                if self.outlines:
                    color = G.QColor('#ff5757' if self.field == 'footprints' and key == 'ref' else (REF if key == 'ref' else MOV))
                    color.setAlpha(155)
                    p.setPen(G.QPen(color, 0))
                    p.drawPath(self.layers.outlines[key, footprint_only])
                if self.role != 'overlay' and self.layers.confirmed_images[key] is not None:
                    p.drawImage(C.QPointF(), self.layers.confirmed_images[key])
                p.restore()
            outline_rect = C.QRect()
            for key in keys:
                selected = self.selected[key]
                if selected is None or (self.role != 'overlay' and selected in self.layers.confirmed_ids[key]):
                    continue
                p.save()
                if key == 'mov':
                    p.setWorldTransform(qt_matrix(self.transform.matrix()), True)
                path = self.layers.paths[key, footprint_only][selected]
                if path.isEmpty():
                    p.restore()
                    continue
                bounds = p.worldTransform().mapRect(path.boundingRect()).toAlignedRect()
                outline_rect |= bounds.adjusted(-4, -4, 4, 4)
                color, width = ('#ff3333', 4) if key == 'ref' else ('#39ff54', 2.5)
                p.setRenderHint(G.QPainter.Antialiasing)
                pen = G.QPen()
                pen.setCosmetic(True)
                pen.setJoinStyle(C.Qt.RoundJoin)
                pen.setCapStyle(C.Qt.FlatCap)
                # Keep the dark backing and the color in phase, with opposite phases for red and green.
                offset = self.dash_offset + (6 if key == 'mov' else 0)
                for ink, stroke in ((G.QColor(15, 19, 22, 210), width + 1.5), (G.QColor(color), width)):
                    pen.setColor(ink)
                    pen.setWidthF(stroke)
                    pen.setDashPattern([6 / stroke, 6 / stroke])
                    pen.setDashOffset(offset / stroke)
                    p.setPen(pen)
                    p.drawPath(path)
                p.restore()
            self.outline_rect = outline_rect.intersected(self.rect())
            if self.outline_rect.isEmpty() or not self.isVisible():
                self.outline_timer.stop()
            elif not self.outline_timer.isActive():
                self.outline_timer.start()
            if self.role == 'overlay' and (self.field != 'footprints' or self.pivot_mode):
                x, y, _ = self.transform.pivot_scene()
                delta = 7 / self.factor
                pen = G.QPen(G.QColor(SELECT), 1)
                pen.setCosmetic(True)
                p.setPen(pen)
                p.drawLine(C.QPointF(x-delta,y), C.QPointF(x+delta,y))
                p.drawLine(C.QPointF(x,y-delta), C.QPointF(x,y+delta))
        finally:
            p.end()

    def keyPressEvent(self, event):
        enter = event.key() in (C.Qt.Key_Return, C.Qt.Key_Enter)
        if self.role != 'overlay' and enter and event.modifiers() in (C.Qt.NoModifier, C.Qt.KeypadModifier):
            if not event.isAutoRepeat():
                self.confirmRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def roi_layers_at(self, point):
        overlay = self.role in ('overlay', 'review')
        hits = []
        for key in (('mov', 'ref') if overlay else (self.role,)):
            local = point if key == 'ref' else qt_matrix(self.transform.matrix()).inverted()[0].map(point)
            x, y = int(np.floor(local.x())), int(np.floor(local.y()))
            mask = self.layers.roi_masks[key, self.field == 'footprints']
            if not (0 <= y < mask.shape[0] and 0 <= x < mask.shape[1] and mask[y, x]):
                continue
            hidden = overlay and self.balance == (0 if key == 'mov' else 100)
            if hidden:
                white = self.layers.confirmed_images[key] if self.role == 'review' else None
                if white is None or not white.pixelColor(x, y).alpha():
                    continue
            hits.append(key)
        return hits

    def mousePressEvent(self, event):
        if self.drag is not None:
            return
        self.press_button = event.button()
        self.last = event.pos()
        if event.button() == C.Qt.RightButton and self.role != 'overlay':
            self.drag = 'reopen'
        elif event.button() == C.Qt.LeftButton:
            hits = self.roi_layers_at(self.to_scene(self.last))
            if not hits:
                self.drag = 'background'
            elif self.role == 'overlay' and 'mov' in hits and not self.pivot_mode:
                self.drag = 'moving-roi'
            else:
                self.drag = 'roi'

    def mouseMoveEvent(self, event):
        self.move_drag(event.pos())

    def move_drag(self, pos):
        if self.drag in (None, 'ignore'):
            return
        if self.drag not in ('view', 'image'):
            if (pos - self.last).manhattanLength() < W.QApplication.startDragDistance():
                return
            if self.drag == 'background':
                self.drag = 'view'
                self.panStarted.emit()
            elif self.drag == 'moving-roi':
                self.drag = 'image'
                self.shiftStarted.emit()
            else:
                self.drag = 'ignore'
                return
        delta = C.QPointF(pos - self.last) / self.factor
        self.last = pos
        if self.drag == 'view':
            self.pose(self.center - delta, self.zoom, True)
        else:
            self.shifted.emit(delta.x(), delta.y())

    def mouseReleaseEvent(self, event):
        if self.drag is None or event.button() != self.press_button:
            return
        self.move_drag(event.pos())
        gesture = self.drag
        self.drag = None
        point = self.to_scene(self.last)
        if gesture == 'reopen' and event.button() == C.Qt.RightButton:
            self.reopenRequested.emit(self.role, point.x(), point.y())
        elif event.button() == C.Qt.LeftButton and (gesture in ('roi', 'moving-roi')
                or (gesture == 'background' and self.pivot_mode)):
            if self.role == 'review' and event.modifiers() & C.Qt.ShiftModifier:
                self.referencePickRequested.emit(point.x(), point.y())
            else:
                self.picked.emit(self.role, point.x(), point.y())

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        if not steps:
            return
        point = self.to_scene(event.position() if hasattr(event, 'position') else event.pos())
        zoom = float(np.clip(self.zoom * 1.22 ** steps, .25, 24))
        self.pose(point + (self.center - point) * (self.zoom / zoom), zoom, True)
        event.accept()
