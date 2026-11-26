"""Manual alignment and cell review against a fixed reference session."""
from paths import ROOT
import argparse
import copy
import json
import sys
from pathlib import Path

from PyQt5 import QtCore as C, QtGui as G, QtWidgets as W

from canvas import Canvas, Layers
from model import Dataset, Transform, confirmed_pairs_at, dataset_for_review, export_track2p, pair_score, propose, read_review, save_review
from session_picker import SessionPicker
from series import ReviewRound, check_next_dataset, read_series, save_series, visible_reference_ids
from traces import TracePanel


STYLE = """
QWidget { color: #20353b; font-family: 'Microsoft YaHei UI'; font-size: 10pt; }
QMainWindow, #page { background: #f3f6f5; }
#header, #card { background: white; border: 1px solid #d4dedc; border-radius: 7px; }
#title { font-size: 16pt; font-weight: bold; color: #19332e; }
#section { font-size: 12pt; font-weight: bold; }
#muted { color: #536b68; font-size: 9pt; }
QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
  border: 1px solid #b9cbc5; border-radius: 4px; padding: 4px 8px; background: white; }
QPushButton:hover { background: #e8f3ed; }
QPushButton:checked, QPushButton#primary { color: white; background: #226c53; border-color: #226c53; }
QPushButton:disabled { color: #7b8c87; background: #edf1ef; border-color: #d8e1de; }
QTabBar::tab { min-width: 8em; min-height: 1.5em; padding: 7px 18px;
  border-bottom: 2px solid transparent; color: #536b68; }
QTabBar::tab:selected { color: #14593f; border-bottom: 2px solid #267351; font-weight: bold; }
QTableWidget { border: 1px solid #d4dedc; gridline-color: #e6ece8; background: white;
  alternate-background-color: #f4f8f6; selection-background-color: #d0e8dd; selection-color: #173728; }
QHeaderView::section { background: #e9f1ed; padding: 4px; border: 0; }
QStatusBar { background: #f3f6f5; color: #33554a; }
"""


def label(text, style='', wrap=False):
    out = W.QLabel(text)
    out.setObjectName(style)
    out.setWordWrap(wrap)
    return out


def button(text, slot, primary=False):
    out = W.QPushButton(text)
    out.clicked.connect(lambda checked=False: slot())
    if primary:
        out.setObjectName('primary')
    return out


def row(*widgets):
    box = W.QHBoxLayout()
    for item in widgets:
        box.addWidget(item)
    return box


def card():
    frame = W.QFrame()
    frame.setObjectName('card')
    layout = W.QVBoxLayout(frame)
    layout.setContentsMargins(13, 12, 13, 12)
    layout.setSpacing(8)
    return frame, layout


class ReviewStatusDelegate(W.QStyledItemDelegate):
    backgrounds = {
        'Confirmed': G.QColor('#bce8ca'),
        'Pending': G.QColor('#e2e5e8'),
        'Unmatched': G.QColor('#f8c7c7'),
        'Suggested': G.QColor('#ffe693'),
    }

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        color = self.backgrounds.get(option.text)
        if color is not None:
            # The ROI columns retain the row highlight; keep the status color visible.
            option.state &= ~W.QStyle.State_Selected
            option.backgroundBrush = G.QBrush(color)


class ExportJob(C.QThread):
    done = C.pyqtSignal(object)
    failed = C.pyqtSignal(str)

    def __init__(self, data, matrix, results):
        super().__init__()
        self.data, self.matrix, self.results = data, matrix, results

    def run(self):
        try:
            self.done.emit(export_track2p(self.data, self.matrix, self.results))
        except Exception as exc:
            self.failed.emit(str(exc))


class Window(W.QMainWindow):
    def __init__(self, data=None, results=None):
        super().__init__()
        self.results_override = results
        self.settings = C.QSettings(str(ROOT / 'work' / 'gui.ini'), C.QSettings.IniFormat) if results is None else None
        screen = W.QApplication.primaryScreen().availableGeometry()
        self.resize(min(1490, screen.width()-30), min(910, screen.height()-55))
        self.data = None
        self.results = Path(results or ROOT / 'results').absolute()
        self.dirty = False
        self.job = None
        if data is None:
            outer, _ = self.build_header()
            outer.addWidget(label('No sessions loaded', 'muted'), 1, C.Qt.AlignCenter)
        else:
            self.set_pair(data)

    def build_header(self, pair_name=None):
        self.setWindowTitle(f'Cell Review  |  {pair_name}' if pair_name else 'Cell Review')
        page = W.QWidget()
        page.setObjectName('page')
        self.setCentralWidget(page)
        outer = W.QVBoxLayout(page)
        outer.setContentsMargins(14, 12, 14, 4)
        header = W.QFrame()
        header.setObjectName('header')
        header_layout = W.QHBoxLayout(header)
        header_layout.addWidget(label('Cell Review', 'title'))
        if pair_name:
            header_layout.addSpacing(12)
            header_layout.addWidget(label(pair_name, 'muted'))
        header_layout.addStretch()
        self.session_button = button('Select Sessions…', self.select_sessions)
        header_layout.addWidget(self.session_button)
        self.open_button = button('Open Progress', self.open_dialog)
        header_layout.addWidget(self.open_button)
        outer.addWidget(header)
        return outer, header_layout

    def set_pair(self, data, *, rounds=None, active=0, output=None):
        self.rounds = rounds if rounds is not None else [ReviewRound.start(data)]
        self.active_round = active
        item = self.rounds[active]
        pos = item.position
        self.layers = Layers(data)
        self.data = data
        series_name = self.rounds[0].data.folder.name
        self.output_key = 'outputs/' + series_name
        results = self.results_override or output
        if results is None:
            results = self.settings.value(self.output_key, str(ROOT / 'results' / series_name))
        self.results = Path(results).absolute()
        self.t = copy.copy(item.transform)
        self.reviews, self.proposals = dict(item.reviews), dict(item.proposals)
        self.undo_stack = []
        self.review_history = []
        self.candidate_generation = 0
        self.dirty = False
        self.ref_id = pos.get('ref_id', 0)
        self.pick_step = 'red'
        self.last_saved = None
        outer, header_layout = self.build_header(data.meta['pair_name'])
        page = self.centralWidget()
        W.QShortcut(G.QKeySequence.Save, page, activated=self.save)
        W.QShortcut(G.QKeySequence('Ctrl+Z'), page, activated=self.undo_current)
        save_button = button('Save Progress', self.save)
        save_button.setToolTip('Ctrl+S')
        header_layout.addWidget(save_button)
        self.finish_button = button('Finish Review and Export MAT', self.finish_review, True)
        self.finish_button.setToolTip('Save the CellReg MAT and current progress together. Unpaired ROIs are exported as unmatched.')
        header_layout.addWidget(self.finish_button)
        self.output_path = W.QLineEdit(str(self.results))
        self.output_path.setAccessibleName('Current output folder')
        self.output_path.editingFinished.connect(self.apply_output_folder)
        self.paste_path_button = button('Paste Path', self.paste_output_folder)
        self.output_button = button('Choose Folder…', self.choose_output_folder)
        outer.addLayout(row(label('Output folder'), self.output_path, self.paste_path_button, self.output_button))
        self.tabs = W.QTabBar()
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setElideMode(C.Qt.ElideNone)
        self.tabs.setSizePolicy(W.QSizePolicy.Fixed, W.QSizePolicy.Fixed)
        self.tabs.addTab('Alignment')
        self.tabs.addTab('Pair Review')
        self.round_combo = W.QComboBox(page)
        for i, review_round in enumerate(self.rounds, 2):
            self.round_combo.addItem(f'Session {i} · {review_round.data.mov.meta["name"]}')
        self.round_combo.setCurrentIndex(active)
        self.round_combo.currentIndexChanged.connect(self.switch_round)
        self.next_session_button = button('Next Session…' if active == len(self.rounds)-1 else 'Next Session →',
                                          self.next_session)
        self.show_hidden = W.QCheckBox('Show Hidden Reference ROIs', page)
        self.show_hidden.setChecked(bool(pos.get('show_hidden', False)) and active > 0)
        self.show_hidden.toggled.connect(self.filter_changed)
        if len(self.rounds) > 1:
            navigation = row(self.round_combo, self.next_session_button, self.show_hidden)
            navigation.addStretch()
            outer.addLayout(navigation)
        self.round_combo.setVisible(len(self.rounds) > 1)
        self.show_hidden.setVisible(active > 0)
        bar = W.QHBoxLayout()
        bar.addWidget(self.tabs)
        if len(self.rounds) == 1:
            bar.addWidget(self.next_session_button)
        bar.addStretch()
        self.channel = W.QComboBox()
        for title, field in [('ROI footprints only', 'footprints'), ('Enhanced mean image', 'enhanced'), ('Mean image', 'mean')]:
            self.channel.addItem(title, field)
        self.edges = W.QCheckBox('ROI outlines')
        bar.addWidget(self.channel)
        bar.addWidget(self.edges)
        bar.addWidget(button('Fit View', self.fit_view))
        outer.addLayout(bar)
        self.pages = W.QStackedWidget()
        outer.addWidget(self.pages, 1)
        self.overlay = Canvas('overlay', data.ref.shape, self.layers, self.t)
        self.views = [Canvas(key, data.ref.shape, self.layers, self.t) for key in ('ref','mov')]
        self.review_overlay = Canvas('review', data.ref.shape, self.layers, self.t)
        self.review_overlay.setCursor(C.Qt.CrossCursor)
        self.review_overlay.setToolTip('Click another red ROI to switch. Shift+click selects red in an overlap.')
        self.canvases = [self.overlay, self.review_overlay, *self.views]
        self.pages.addWidget(self.alignment_page())
        self.pages.addWidget(self.review_page())
        self.overlay.picked.connect(self.pick)
        self.review_overlay.picked.connect(self.pick)
        self.review_overlay.referencePickRequested.connect(lambda x, y: self.pick_on_overlay(x, y, force_red=True))
        for view in (self.review_overlay, *self.views):
            view.confirmRequested.connect(self.confirm_current_pair)
            view.reopenRequested.connect(self.reopen_at)
        self.overlay.shiftStarted.connect(self.remember)
        self.overlay.panStarted.connect(self.remember_pan)
        self.overlay.shifted.connect(self.shift)
        for view, peer in zip(self.views, reversed(self.views)):
            view.picked.connect(self.pick)
            view.navigated.connect(peer.pose)
        self.tabs.currentChanged.connect(self.change_tab)
        self.edges.toggled.connect(self.update_display)
        self.channel.currentIndexChanged.connect(self.update_display)
        self.log = self.statusBar()
        self.log.setSizeGripEnabled(False)
        for edit in page.findChildren(W.QLineEdit):
            edit.installEventFilter(self)
        self.note.setText(item.note)
        self.channel.setCurrentIndex(max(0, self.channel.findData(item.image_field)))
        self.update_reference_filter()
        self.review_mode.setCurrentIndex(pos.get('mode', 0))
        self.tabs.setCurrentIndex(pos.get('tab', 0))
        self.follow.setChecked(bool(pos.get('follow', False)))
        for view, pose in zip(self.canvases, pos.get('views', [])):
            view.pose(C.QPointF(pose[0], pose[1]), pose[2])
        step = pos.get('pick_step', 'red')
        if self.ref_id == pos.get('ref_id') and self.ref_id is not None:
            if step == 'green' or (step == 'ready' and self.entry(self.ref_id)[1] is not None):
                self.pick_step = step
        self.update_display()
        self.sync_transform()
        self.refresh_list()

    def snapshot_rounds(self):
        rounds = self.rounds.copy()
        position = {'ref_id': self.ref_id, 'show_hidden': self.show_hidden.isChecked(),
                    'tab': self.tabs.currentIndex(), 'mode': self.review_mode.currentIndex(),
                    'pick_step': self.pick_step, 'follow': self.follow.isChecked(),
                    'views': [[v.center.x(), v.center.y(), v.zoom] for v in self.canvases]}
        rounds[self.active_round] = ReviewRound(self.data, copy.copy(self.t), self.reviews.copy(),
                                                self.proposals.copy(), self.note.text(),
                                                self.channel.currentData(), position)
        return rounds

    def update_reference_filter(self):
        ids = visible_reference_ids(self.data.ref.n, (r.reviews for r in self.rounds[:self.active_round]),
                                    self.show_hidden.isChecked())
        self.layers.set_visible('ref', self.data.ref, ids)
        if self.ref_id not in ids:
            self.ref_id = next((i for i in sorted(ids) if str(i) not in self.reviews), min(ids, default=None))
            self.pick_step = 'red'
        if self.overlay.selected['ref'] not in ids:
            self.overlay.selected['ref'] = None

    def filter_changed(self):
        self.update_reference_filter()
        self.refresh_list()
        self.overlay.update()
        self.mark_dirty()

    def switch_round(self, index):
        if index == self.active_round or self.job is not None:
            return
        if not self.apply_output_folder():
            with C.QSignalBlocker(self.round_combo):
                self.round_combo.setCurrentIndex(self.active_round)
            return
        rounds = self.snapshot_rounds()
        self.set_pair(rounds[index].data, rounds=rounds, active=index, output=self.results)
        self.mark_dirty()

    def next_session(self):
        if self.job is not None:
            return
        if self.active_round + 1 < len(self.rounds):
            self.round_combo.setCurrentIndex(self.active_round + 1)
            return
        dialog = SessionPicker(self, self.data, append=True)
        try:
            if dialog.exec_() != W.QDialog.Accepted or not self.apply_output_folder():
                return
            data = dialog.data
            rounds = self.snapshot_rounds()
            check_next_dataset(rounds, data)
            rounds[-1] = rounds[-1].completed()
            rounds.append(ReviewRound.start(data))
            index = len(rounds) - 1
            folder = save_series(rounds, index, self.results)
            self.set_pair(data, rounds=rounds, active=index, output=self.results)
            self.last_saved = folder
            self.status('Loaded the next session. Series progress saved to ' + str(folder))
        except Exception as exc:
            W.QMessageBox.warning(self, 'Could Not Add Session', str(exc))
        finally:
            dialog.deleteLater()

    def identity(self):
        h, w = self.data.mov.shape
        return Transform(cx=w/2, cy=h/2)

    @staticmethod
    def page_layout():
        page = W.QWidget()
        layout = W.QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        return page, layout

    def alignment_page(self):
        page, layout = self.page_layout()
        image, box = card()
        legend = W.QHBoxLayout()
        self.ref_legend = label('')
        legend.addWidget(self.ref_legend)
        legend.addWidget(label(f'<b style="color:#268355">■</b> Moving · {self.data.mov.meta["name"]}'))
        legend.addStretch()
        box.addLayout(legend)
        box.addWidget(self.overlay, 1)
        layout.addWidget(image, 1)
        side, controls = card()
        side.setMinimumWidth(318)
        controls.addWidget(label('Image transform', 'section'))
        form = W.QFormLayout()
        self.params = {}
        for name, title, unit, step in [('angle','Clockwise rotation',' °',.5),('dx','Horizontal shift',' px',1.),('dy','Vertical shift',' px',1.)]:
            spin = W.QDoubleSpinBox()
            limit = 360 if name == 'angle' else 20000
            spin.setRange(-limit, limit)
            spin.setKeyboardTracking(False)
            spin.setDecimals(2)
            spin.setSingleStep(step)
            spin.setSuffix(unit)
            spin.valueChanged.connect(lambda value, key=name: self.parameter(key, value))
            self.params[name] = spin
            form.addRow(title, spin)
        controls.addLayout(form)
        self.pivot = button('Set Rotation Center', self.toggle_pivot)
        self.pivot.setCheckable(True)
        self.reset_pivot_btn = button('Reset', self.reset_pivot)
        self.reset_pivot_btn.setToolTip('Restore the center of the moving image')
        self.reset_pivot_btn.setSizePolicy(W.QSizePolicy.Maximum, W.QSizePolicy.Fixed)
        controls.addLayout(row(self.pivot, self.reset_pivot_btn))
        self.pivot_label = label('', 'muted')
        controls.addWidget(self.pivot_label)
        self.flip_x, self.flip_y = W.QPushButton('Flip Horizontally'), W.QPushButton('Flip Vertically')
        for widget, key in [(self.flip_x,'flip_x'), (self.flip_y,'flip_y')]:
            widget.setCheckable(True)
            widget.toggled.connect(lambda value, k=key: self.parameter(k, value))
        controls.addLayout(row(self.flip_x, self.flip_y))
        self.undo_btn = button('Undo (Ctrl+Z)', self.undo_transform)
        controls.addLayout(row(self.undo_btn, button('Reset Transform', self.reset_transform)))
        controls.addSpacing(10)
        controls.addWidget(label('Overlay', 'section'))
        self.blend = W.QSlider(C.Qt.Horizontal)
        self.blend.setRange(0, 100)
        self.blend.setValue(50)
        self.blend.valueChanged.connect(self.update_display)
        controls.addWidget(self.blend)
        self.blend_hint = label('', 'muted', True)
        controls.addWidget(self.blend_hint)
        controls.addStretch()
        controls.addWidget(button('Review Pairs →', lambda: self.tabs.setCurrentIndex(1), True))
        self.export_btn = button('Export Track2p Inputs', self.export)
        controls.addWidget(self.export_btn)
        layout.addWidget(side)
        return page

    def review_page(self):
        page, layout = self.page_layout()
        images, box = card()
        self.review_mode = W.QComboBox()
        self.review_mode.addItems(['Overlay', 'Side by side'])
        self.pick_hint = label('', wrap=True)
        self.restart_pick = button('Reselect Red', self.restart_pair)
        box.addLayout(row(self.review_mode, self.pick_hint, self.restart_pick))
        self.review_images = W.QStackedWidget()
        self.review_images.addWidget(self.review_overlay)
        split = W.QWidget()
        pair = W.QHBoxLayout(split)
        pair.setContentsMargins(0, 0, 0, 0)
        for view, title, session in zip(self.views, ('Reference','Aligned'), (self.data.ref, self.data.mov)):
            col = W.QVBoxLayout()
            col.addWidget(label(f'{title} · {session.meta["name"]} · {session.n} ROIs'))
            col.addWidget(view, 1)
            pair.addLayout(col, 1)
        self.review_images.addWidget(split)
        self.traces = TracePanel(self.data)
        review_split = W.QSplitter(C.Qt.Vertical)
        review_split.setChildrenCollapsible(False)
        review_split.addWidget(self.review_images)
        review_split.addWidget(self.traces)
        review_split.setSizes([500, 160])
        box.addWidget(review_split, 1)
        self.review_mode.currentIndexChanged.connect(self.review_mode_changed)
        layout.addWidget(images, 1)
        side, controls = card()
        side.setMinimumWidth(332)
        controls.addWidget(label('Cell matches', 'section'))
        self.counts = label('', 'muted')
        controls.addWidget(self.counts)
        cellreg_btn = button('Load CellReg Matches', self.cellreg)
        cellreg_btn.setEnabled(bool(self.data.meta.get('cellreg_pairs')))
        if not cellreg_btn.isEnabled():
            cellreg_btn.setToolTip(self.data.meta.get('cellreg_error') or 'No CellReg matches found for this pair.')
        controls.addLayout(row(button('Suggest Matches', self.generate), cellreg_btn))
        self.table = W.QTableWidget(self.data.ref.n, 3)
        self.table.setHorizontalHeaderLabels(['Reference ROI','Moving ROI','Status'])
        self.table.setItemDelegateForColumn(2, ReviewStatusDelegate(self.table))
        self.table.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(W.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.horizontalHeader().setSectionResizeMode(W.QHeaderView.Stretch)
        self.table.currentCellChanged.connect(self.row_changed)
        self.table.cellClicked.connect(self.row_changed)
        self.table.installEventFilter(self)
        controls.addWidget(self.table, 1)
        self.follow = W.QCheckBox('Zoom to ROI')
        self.follow.toggled.connect(lambda on: self.zoom_selected() if on else self.fit_review())
        self.mov_spin = W.QSpinBox()
        self.mov_spin.setRange(0, self.data.mov.n)
        self.mov_spin.setKeyboardTracking(False)
        self.mov_spin.setSpecialValueText('None')
        self.mov_spin.valueChanged.connect(self.choose_moving)
        controls.addLayout(row(label('Moving ROI'), self.mov_spin, self.follow))
        self.details = label('', 'muted', True)
        controls.addWidget(self.details)
        self.accept_btn = button('Confirm Pair ↵', self.confirm_current_pair, True)
        self.absent_btn = button('Unmatched', lambda: self.decide('unmatched'))
        controls.addLayout(row(self.accept_btn, self.absent_btn))
        self.reopen_btn = button('Reopen ROI', self.reopen)
        controls.addLayout(row(self.reopen_btn, button('Next Pending', self.next_pending)))
        self.note = W.QLineEdit()
        self.note.setPlaceholderText('Review note (optional)')
        self.note.textEdited.connect(lambda: self.mark_dirty())
        controls.addWidget(self.note)
        layout.addWidget(side)
        return page

    def status(self, message):
        self.log.showMessage(message)

    def mark_dirty(self):
        self.dirty = True

    def update_display(self, *args):
        field = self.channel.currentData()
        color = '#be2222' if field == 'footprints' else '#9b3976'
        self.ref_legend.setText(f'<b style="color:{color}">■</b> Reference · {self.data.ref.meta["name"]}')
        overlap = 'yellow' if field == 'footprints' else 'gray or white'
        self.blend_hint.setText(f'At the midpoint, equally bright overlap appears {overlap}. Move to either end to isolate a layer.')
        for view in self.canvases:
            view.field = field
            view.outlines = self.edges.isChecked()
            view.balance = self.blend.value()
            view.update()

    def change_tab(self, index):
        self.pages.setCurrentIndex(index)
        if index == 1:
            self.restart_pair()
            if self.review_mode.currentIndex() == 0:
                self.review_overlay.pose(self.overlay.center, self.overlay.zoom)
            elif self.follow.isChecked():
                self.zoom_selected()

    def fit_view(self):
        if self.tabs.currentIndex() == 0:
            self.overlay.fit()
        else:
            self.follow.setChecked(False)
            self.fit_review()

    def fit_review(self):
        self.review_overlay.fit()
        self.views[0].fit()

    def review_mode_changed(self, index):
        self.review_images.setCurrentIndex(index)
        self.restart_pair()
        if index == 0:
            self.review_overlay.pose(self.views[0].center, self.views[0].zoom)
        else:
            self.views[0].pose(self.review_overlay.center, self.review_overlay.zoom, True)

    def restart_pair(self):
        self.pick_step = 'red'
        self.refresh_selected()

    def remember(self):
        state = ('transform', copy.copy(self.t), dict(self.proposals))
        if not self.undo_stack or self.undo_stack[-1] != state:
            self.undo_stack.append(state)
            self.undo_stack = self.undo_stack[-80:]
        self.undo_btn.setEnabled(True)

    def remember_pan(self):
        self.undo_stack.append(('view', C.QPointF(self.overlay.center)))
        self.undo_stack = self.undo_stack[-80:]
        self.undo_btn.setEnabled(True)

    def parameter(self, key, value):
        if getattr(self.t, key) != value:
            self.remember()
            setattr(self.t, key, value)
            self.transform_changed()

    def shift(self, x, y):
        if x or y:
            self.t.dx += x
            self.t.dy += y
            self.transform_changed()

    def toggle_pivot(self):
        self.overlay.pivot_mode = self.pivot.isChecked()
        self.overlay.update()
        self.overlay.setCursor(C.Qt.CrossCursor if self.overlay.pivot_mode else C.Qt.ArrowCursor)
        if self.overlay.pivot_mode:
            self.status('Click the overlay to set the center for rotation and flipping.')

    def reset_pivot(self):
        h, w = self.data.mov.shape
        center = (w / 2, h / 2)
        if (self.t.cx, self.t.cy) != center:
            self.remember()
            self.t.set_pivot_source(*center)
            self.mark_dirty()
            self.sync_transform()
        self.pivot.setChecked(False)
        self.toggle_pivot()
        self.status('Rotation center reset to the center of the moving image.')

    def pick(self, role, x, y):
        if role == 'overlay':
            if not self.overlay.pivot_mode:
                hits = self.overlay.roi_layers_at(C.QPointF(x, y))
                if hits:
                    key = hits[0]
                    roi = self.pick_roi(key, x, y)
                    if roi is not None:
                        self.overlay.selected = {'ref': None, 'mov': None}
                        self.overlay.selected[key] = roi
                        self.overlay.update()
                        self.status(f'Selected {getattr(self.data, key).meta["name"]} ROI #{roi+1}.')
                return
            self.remember()
            self.t.set_pivot_scene(x, y)
            self.pivot.setChecked(False)
            self.toggle_pivot()
            self.mark_dirty()
            self.sync_transform()
            self.status('Rotation center set.')
            return
        if role == 'review':
            self.pick_on_overlay(x, y)
            return
        if role == 'ref':
            found = self.pick_roi('ref', x, y)
            if found is not None:
                self.table.selectRow(found)
                self.pick_step = 'red'
        else:
            found = self.pick_roi('mov', x, y)
            if found is not None and str(self.ref_id) not in self.reviews:
                self.choose_moving(found+1)

    def pick_roi(self, key, x, y):
        session = self.data.ref if key == 'ref' else self.data.mov
        matrix = self.t.matrix() if key == 'mov' else None
        ids = self.layers.reference_ids if key == 'ref' else range(session.n)
        if self.channel.currentData() != 'footprints':
            return session.pick(x, y, matrix, radius=None, indices=ids)
        hits = list(session.hits_at(x, y, ids, footprint_only=True, matrix=matrix))
        if len(hits) < 2:
            return hits[0] if hits else None
        menu = W.QMenu(self)
        menu.addSection(f'Choose an overlapping ROI in {session.meta["name"]}')
        for index in hits:
            action = menu.addAction(f'ROI #{index+1} (Suite2p #{session.original_ids[index]})')
            action.setData(index)
        choice = menu.exec_(G.QCursor.pos())
        return choice.data() if choice is not None else None

    def pick_on_overlay(self, x, y, *, force_red=False):
        hits = self.review_overlay.roi_layers_at(C.QPointF(x, y))
        select_red = force_red or self.pick_step == 'red' or 'mov' not in hits
        if ('ref' if select_red else 'mov') not in hits:
            return
        if select_red:
            ref = self.pick_roi('ref', x, y)
            if ref is None:
                self.status('Select a reference ROI in a red or yellow area.')
                return
            # Keep the clicked scene point in place for the second click.
            with C.QSignalBlocker(self.table):
                self.table.selectRow(ref)
            self.ref_id = ref
            if str(ref) in self.reviews:
                self.pick_step = 'red'
                self.status('This reference ROI has been reviewed. Reopen it to change the match.')
            else:
                self.pick_step = 'green'
                self.status(f'Reference ROI #{ref+1} selected. Click its matching moving ROI.')
            self.refresh_selected()
            return
        mov = self.pick_roi('mov', x, y)
        if mov is None:
            self.status('Select a moving ROI in a green or yellow area.')
            return
        self.choose_moving(mov+1)
        self.status(f'Reference #{self.ref_id+1} and moving #{mov+1} selected. Press Enter to confirm.')

    def transform_changed(self):
        self.candidate_generation += 1
        dropped = bool(self.proposals)
        self.pick_step = 'red'
        self.proposals = {}
        self.mark_dirty()
        self.sync_transform()
        if dropped:
            self.refresh_list()
        if dropped or self.reviews:
            self.status('Transform updated. You can generate new match suggestions.')

    def sync_transform(self):
        for name, widget in self.params.items():
            with C.QSignalBlocker(widget):
                widget.setValue(getattr(self.t, name))
        for widget, name in [(self.flip_x,'flip_x'),(self.flip_y,'flip_y')]:
            with C.QSignalBlocker(widget):
                widget.setChecked(getattr(self.t, name))
        x,y,_ = self.t.pivot_scene()
        self.pivot_label.setText(f'Center in reference image (px)   x {x:.1f}, y {y:.1f}')
        self.undo_btn.setEnabled(bool(self.undo_stack))
        for view in self.canvases:
            view.transform = self.t
            view.update()
        self.refresh_selected()

    def undo_transform(self):
        if self.undo_stack:
            step = self.undo_stack.pop()
            if step[0] == 'view':
                self.overlay.pose(step[1], self.overlay.zoom)
                self.undo_btn.setEnabled(bool(self.undo_stack))
                return
            self.pick_step = 'red'
            _, self.t, previous = step
            self.candidate_generation += 1
            self.proposals = self.available(previous)
            self.mark_dirty()
            self.sync_transform()
            self.refresh_list()

    def reset_transform(self):
        self.remember()
        self.t = self.identity()
        self.transform_changed()

    def available(self, candidates, reviews=None):
        if reviews is None:
            reviews = self.reviews
        used = {v['moving'] for v in reviews.values() if v['status']=='accepted'}
        return {str(r):int(m) for r,m in candidates.items() if str(r) not in reviews and int(m) not in used}

    def use_candidates(self, candidates):
        self.candidate_generation += 1
        self.pick_step = 'red'
        ids = set(self.layers.reference_ids)
        keep_hidden = {k: m for k, m in self.proposals.items() if int(k) not in ids}
        shown = {str(r): m for r, m in candidates.items() if int(r) in ids}
        self.proposals = self.available({**keep_hidden, **shown})
        self.mark_dirty()
        self.refresh_list()
        self.status(f'Loaded {sum(int(k) in ids for k in self.proposals)} visible match suggestions.')

    def generate(self):
        self.use_candidates(propose(self.data, self.t.matrix(), self.reviews, reference_ids=self.layers.reference_ids))

    def cellreg(self):
        self.use_candidates(dict(self.data.meta.get('cellreg_pairs', [])))

    def entry(self, r):
        key = str(r)
        review = self.reviews.get(key)
        if review is not None:
            return review['status'], review.get('moving')
        return 'pending', self.proposals.get(key)

    def refresh_row(self, r):
        status, m = self.entry(r)
        status_text = {'accepted':'Confirmed','unmatched':'Unmatched','pending':'Pending' if m is None else 'Suggested'}[status]
        for c, value in enumerate((str(r+1), '—' if m is None else str(m+1), status_text)):
            item = self.table.item(r,c)
            if item is None:
                item = W.QTableWidgetItem()
                self.table.setItem(r,c,item)
            item.setText(value)

    def refresh_list(self):
        self.layers.update_confirmed(self.data, self.reviews)
        ids = set(self.layers.reference_ids)
        with C.QSignalBlocker(self.table):
            for r in range(self.data.ref.n):
                self.refresh_row(r)
                self.table.setRowHidden(r, r not in ids)
            if self.ref_id in ids:
                self.table.selectRow(self.ref_id)
            else:
                self.table.setCurrentCell(-1, -1)
        visible = [v for k, v in self.reviews.items() if int(k) in ids]
        n = sum(x['status']=='accepted' for x in visible)
        counts = f'Confirmed {n} · Unmatched {len(visible)-n} · Pending {len(ids)-len(visible)}'
        if len(ids) < self.data.ref.n:
            counts += f' · Hidden {self.data.ref.n-len(ids)}'
        self.counts.setText(counts)
        self.refresh_selected()

    def row_changed(self, r, *args):
        if r in self.layers.reference_ids and (r != self.ref_id or self.pick_step != 'red'):
            self.pick_step = 'red'
            self.ref_id = r
            self.refresh_selected()
            if self.follow.isChecked():
                self.zoom_selected()

    def zoom_selected(self):
        if self.ref_id is None:
            return
        x,y = self.data.ref.centroids[self.ref_id]
        center = C.QPointF(x,y)
        self.review_overlay.pose(center, 5.)
        self.views[0].pose(center, 5., True)

    def choose_moving(self, value):
        key = str(self.ref_id)
        if self.ref_id is None or key in self.reviews:
            return
        self.pick_step = 'ready' if value else 'red'
        if not value and self.review_mode.currentIndex() == 0:
            self.pick_step = 'green'
        if value:
            self.proposals[key] = value-1
        else:
            self.proposals.pop(key,None)
        self.mark_dirty()
        self.refresh_row(self.ref_id)
        self.refresh_selected()

    def refresh_selected(self):
        if self.ref_id is None:
            self.pick_hint.setText('No continuous reference ROIs. Select Show Hidden Reference ROIs to review others.')
            for widget in (self.accept_btn, self.absent_btn, self.reopen_btn, self.mov_spin, self.restart_pick):
                widget.setEnabled(False)
            with C.QSignalBlocker(self.mov_spin):
                self.mov_spin.setValue(0)
            self.details.clear()
            self.traces.select(None, None)
            for view in (self.review_overlay, *self.views):
                view.selected = {'ref': None, 'mov': None}
                view.update()
            return
        status, m = self.entry(self.ref_id)
        in_overlay = self.review_mode.currentIndex() == 0
        waiting_green = in_overlay and self.pick_step == 'green'
        if waiting_green:
            m = None
        self.restart_pick.setVisible(in_overlay)
        self.restart_pick.setEnabled(self.pick_step != 'red')
        if status == 'pending' and m is not None and self.pick_step == 'red':
            hint = f'Suggested: red #{self.ref_id+1} ↔ green #{m+1}. Press Enter to confirm.'
        elif not in_overlay:
            hint = 'Select the reference ROI on the left and its match on the right.'
        elif self.pick_step == 'red':
            hint = f'Select a red ROI in {self.data.ref.meta["name"]}.'
        elif waiting_green:
            hint = f'Red #{self.ref_id+1} selected. Click green to pair or another red to switch.'
        else:
            hint = f'Red #{self.ref_id+1} ↔ green #{m+1}. Press Enter to confirm.'
        self.pick_hint.setText(hint)
        with C.QSignalBlocker(self.mov_spin):
            self.mov_spin.setValue(0 if m is None else m+1)
        pending = status=='pending'
        self.mov_spin.setEnabled(pending)
        self.accept_btn.setEnabled(pending and m is not None)
        self.absent_btn.setEnabled(pending)
        self.reopen_btn.setEnabled(not pending)
        detail = f'Reference ROI #{self.ref_id+1} (Suite2p #{self.data.ref.original_ids[self.ref_id]})'
        if m is not None:
            score, distance = pair_score(self.data,self.ref_id,m,self.t.matrix())
            detail += f' ↔ moving #{m+1} (Suite2p #{self.data.mov.original_ids[m]})\nIoU {score:.2f} · Centroid distance {distance:.1f} px'
        self.details.setText(detail)
        self.traces.select(self.ref_id, m)
        show_ref = self.pick_step != 'red' or m is not None or not pending
        self.review_overlay.selected = {'ref': self.ref_id if show_ref else None, 'mov': m}
        self.review_overlay.update()
        for view in self.views:
            view.selected = {'ref':self.ref_id, 'mov':m}
            view.update()

    def confirm_current_pair(self):
        if self.accept_btn.isEnabled():
            self.decide('accepted')

    def eventFilter(self, watched, event):
        if (event.type() == C.QEvent.KeyPress and watched is self.table
                and event.key() in (C.Qt.Key_Return, C.Qt.Key_Enter)
                and event.modifiers() in (C.Qt.NoModifier, C.Qt.KeypadModifier)):
            if not event.isAutoRepeat():
                self.confirm_current_pair()
            event.accept()
            return True
        # Spin boxes and the note editor normally intercept Undo before window shortcuts.
        if (event.type() == C.QEvent.ShortcutOverride and event.key() == C.Qt.Key_Z
                and event.modifiers() == C.Qt.ControlModifier):
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    def undo_current(self):
        if self.tabs.currentIndex() == 0:
            self.undo_transform()
            return
        if not self.review_history:
            self.status('No review action to undo.')
            return
        self.ref_id, old_reviews, candidate_changes, generation = self.review_history.pop()
        if self.ref_id is not None and self.ref_id not in self.layers.reference_ids:
            self.show_hidden.setChecked(True)
        for key, old in old_reviews.items():
            if old is None:
                self.reviews.pop(key, None)
            else:
                self.reviews[key] = old
        if generation == self.candidate_generation:
            for key, (old, after) in candidate_changes.items():
                if self.proposals.get(key) == after:
                    if old is None:
                        self.proposals.pop(key, None)
                    else:
                        self.proposals[key] = old
        self.proposals = self.available(self.proposals)
        self.pick_step = 'red'
        self.mark_dirty()
        self.refresh_list()
        self.status('Review action undone.' if self.ref_id is None else f'Review action undone for reference ROI #{self.ref_id+1}.')

    def apply_review(self, reviews, proposals):
        old_reviews = {k: self.reviews.get(k) for k in self.reviews.keys() | reviews.keys()
                       if self.reviews.get(k) != reviews.get(k)}
        changes = {k: (self.proposals.get(k), proposals.get(k))
                   for k in self.proposals.keys() | proposals.keys()
                   if self.proposals.get(k) != proposals.get(k)}
        if old_reviews or changes:
            self.review_history.append((self.ref_id, old_reviews, changes, self.candidate_generation))
        self.reviews, self.proposals = reviews, proposals
        self.pick_step = 'red'
        self.mark_dirty()
        self.refresh_list()

    def decide(self, status):
        key = str(self.ref_id)
        if self.ref_id is None or key in self.reviews:
            return
        if status=='accepted':
            if self.pick_step == 'green':
                return
            m = self.proposals.get(key)
            if m is None:
                return
            owner = next((k for k,v in self.reviews.items() if v.get('moving')==m), None)
            if owner is not None:
                self.status(f'Moving ROI #{m+1} is paired with reference ROI #{int(owner)+1}. Reopen that pair to reassign it.')
                return
            entry = {'status':status,'moving':m}
        else:
            entry = {'status':'unmatched'}
        reviews = {**self.reviews, key: entry}
        reviewing_suggestion = status == 'accepted' and self.pick_step == 'red'
        self.apply_review(reviews, self.available(self.proposals, reviews))
        self.next_pending(prefer_suggested=reviewing_suggestion)

    def next_pending(self, *, prefer_suggested=False):
        self.restart_pair()
        pending = [r for r in self.layers.reference_ids if str(r) not in self.reviews]
        if prefer_suggested:
            pending = [r for r in pending if str(r) in self.proposals] or pending
        if pending:
            index = next((r for r in pending if r > self.ref_id), pending[0])
            self.table.selectRow(index)
        else:
            self.status('All reference ROIs have been reviewed.')

    def reopen_at(self, role, x, y):
        refs = set()
        if role in ('review', 'ref'):
            visible = {k: v for k, v in self.reviews.items() if int(k) in self.layers.reference_ids}
            refs.update(confirmed_pairs_at(self.data, visible, self.t.matrix(), 'ref', x, y))
        if role in ('review', 'mov'):
            refs.update(confirmed_pairs_at(self.data, self.reviews, self.t.matrix(), 'mov', x, y))
        refs = sorted(refs)
        if not refs:
            return
        ref = refs[0]
        if len(refs) > 1:
            menu = W.QMenu(self)
            menu.addSection('Choose the pair to reopen')
            for candidate in refs:
                moving = self.reviews[str(candidate)]['moving']
                action = menu.addAction(f'Reference #{candidate+1} ↔ moving #{moving+1}')
                action.setData(candidate)
            selected = menu.exec_(G.QCursor.pos())
            if selected is None:
                return
            ref = selected.data()
        moving = self.reviews[str(ref)]['moving']
        if ref not in self.layers.reference_ids:
            self.show_hidden.setChecked(True)
        self.ref_id = ref
        self.reopen()
        self.status(f'Reopened the pair for reference #{ref+1} and moving #{moving+1}.')

    def reopen(self):
        key = str(self.ref_id)
        old = self.reviews.get(key)
        if old:
            reviews, proposals = self.reviews.copy(), self.proposals.copy()
            del reviews[key]
            if 'moving' in old:
                proposals[key] = old['moving']
            self.apply_review(reviews, proposals)

    def choose_output_folder(self):
        folder = W.QFileDialog.getExistingDirectory(self, 'Choose Output Folder', str(self.results))
        if folder:
            self.output_path.setText(folder)
            self.apply_output_folder()

    def paste_output_folder(self):
        clipboard = W.QApplication.clipboard().mimeData()
        if clipboard is not None and clipboard.hasUrls():
            urls = clipboard.urls()
            text = urls[0].toLocalFile() if len(urls) == 1 else ''
        else:
            text = clipboard.text() if clipboard is not None else ''
        if not text.strip() or len(text.strip().splitlines()) != 1:
            self.status('Copy one folder or its full path, then click Paste Path.')
            return
        self.output_path.setText(text)
        self.apply_output_folder()

    def apply_output_folder(self):
        value = self.output_path.text().strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        folder = Path(value)
        if not folder.is_absolute() or '"' in value or len(value.splitlines()) != 1:
            self.status('Enter the full path to an output folder.')
            return False
        try:
            if folder.exists() and not folder.is_dir():
                self.status('The output path points to a file. Choose a folder.')
                return False
        except OSError as exc:
            self.status(f'Could not access the output folder. {exc}')
            return False
        self.output_path.setText(str(folder))
        if folder != self.results:
            self.results = folder
            if self.settings is not None:
                self.settings.setValue(self.output_key, str(self.results))
                self.settings.sync()
            self.status('Output folder set to ' + str(self.results))
        return True

    def save(self):
        if not self.apply_output_folder():
            return False
        try:
            if len(self.rounds) > 1:
                self.last_saved = save_series(self.snapshot_rounds(), self.active_round, self.results)
            else:
                self.last_saved = save_review(self.data,self.t,self.reviews,self.proposals,self.results,self.note.text(), self.channel.currentData())
        except Exception as exc:
            W.QMessageBox.warning(self,'Could Not Save Progress',str(exc))
            return False
        self.dirty = False
        self.status('Progress saved to ' + str(self.last_saved))
        return True

    def finish_review(self):
        if not self.apply_output_folder():
            return False
        if len(self.rounds) > 1:
            rounds = self.snapshot_rounds()
            try:
                folder = save_series(rounds, self.active_round, self.results, final=True)
            except Exception as exc:
                W.QMessageBox.warning(self, 'Could Not Export MAT', str(exc))
                return False
            self.rounds = [r.completed() for r in rounds]
            self.apply_review(self.rounds[self.active_round].reviews, {})
            self.last_saved = folder
            self.dirty = False
            self.status('Multi-session MAT and progress saved to ' + str(folder))
            return True
        completed = dict(self.reviews)
        for i in range(self.data.ref.n):
            completed.setdefault(str(i), {'status': 'unmatched'})
        try:
            folder = save_review(self.data, self.t, completed, {}, self.results, self.note.text(),
                                 self.channel.currentData(), cellreg=True, progress=(self.reviews, self.proposals))
        except Exception as exc:
            W.QMessageBox.warning(self, 'Could Not Export MAT', str(exc))
            return False
        self.last_saved = folder
        self.apply_review(completed, {})
        self.dirty = False
        self.status('MAT and progress saved to ' + str(folder))
        return True

    def allow_leave(self):
        if not self.dirty:
            return True
        answer = W.QMessageBox.question(self, 'Save Progress', 'Save your changes before continuing?',
                W.QMessageBox.Save | W.QMessageBox.Discard | W.QMessageBox.Cancel, W.QMessageBox.Save)
        return self.save() if answer==W.QMessageBox.Save else answer==W.QMessageBox.Discard

    def select_sessions(self):
        dialog = SessionPicker(self, self.data)
        if dialog.exec_() == W.QDialog.Accepted and dialog.data is not self.data and self.allow_leave():
            try:
                self.set_pair(dialog.data)
                self.status('Loaded ' + self.data.meta['pair_name'])
            except Exception as exc:
                W.QMessageBox.warning(self, 'Could Not Load Sessions', str(exc))
        dialog.deleteLater()

    def restore(self, path):
        state = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if state.get('format_version') == 2:
            rounds, index = read_series(state, self.data)
            self.set_pair(rounds[index].data, rounds=rounds, active=index, output=state.get('output_folder'))
            self.last_saved = Path(path).parent
            self.status('Opened ' + str(path))
            return
        data = dataset_for_review(path, self.data)
        t, reviews, proposals, note, field = read_review(path, data, with_image_field=True)
        if data is not self.data or len(self.rounds) > 1:
            self.set_pair(data)
        channel = self.channel.findData(field or 'footprints')
        self.channel.setCurrentIndex(max(0, channel))
        self.pick_step = 'red'
        self.t, self.reviews, self.proposals = t, reviews, proposals
        self.ref_id = next((i for i in range(self.data.ref.n) if str(i) not in reviews), 0)
        self.undo_stack.clear()
        self.review_history.clear()
        self.overlay.selected = {'ref': None, 'mov': None}
        self.note.setText(note)
        self.dirty = False
        self.sync_transform()
        self.refresh_list()
        if self.tabs.currentIndex() and self.follow.isChecked():
            self.zoom_selected()
        self.status('Opened '+str(path))

    def open_dialog(self):
        path, _ = W.QFileDialog.getOpenFileName(
            self, 'Open Review Progress', str(self.results), 'Review progress (session.json session_final.json)'
        )
        if path and self.allow_leave():
            try:
                self.restore(path)
            except Exception as exc:
                W.QMessageBox.warning(self,'Could Not Open Progress',str(exc))

    def export(self):
        if self.job is not None or not self.apply_output_folder():
            return
        self.export_btn.setEnabled(False)
        self.session_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.next_session_button.setEnabled(False)
        self.round_combo.setEnabled(False)
        self.status('Exporting the transform, ROI masks and fluorescence traces…')
        self.job = ExportJob(self.data,self.t.matrix(),self.results)
        self.job.done.connect(self.export_done)
        self.job.failed.connect(lambda msg: W.QMessageBox.warning(self,'Export Failed',msg))
        self.job.finished.connect(self.export_finished)
        self.job.start()

    def export_done(self, result):
        path, counts = result
        self.status(f'Exported {counts[0]["exported"]} reference ROIs and {counts[1]["exported"]} moving ROIs to {path}')

    def export_finished(self):
        self.job.deleteLater()
        self.job = None
        self.export_btn.setEnabled(True)
        self.session_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.next_session_button.setEnabled(True)
        self.round_combo.setEnabled(True)

    def closeEvent(self, event):
        if self.job is not None:
            self.status('Track2p export in progress. You can close the app when it finishes.')
            event.ignore()
        elif not self.allow_leave():
            event.ignore()
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data',type=Path)
    parser.add_argument('--results',type=Path)
    parser.add_argument('--session',type=Path)
    args = parser.parse_args()
    C.QLocale.setDefault(C.QLocale(C.QLocale.English, C.QLocale.UnitedStates))
    W.QApplication.setAttribute(C.Qt.AA_EnableHighDpiScaling)
    app = W.QApplication(sys.argv[:1])
    app.setWindowIcon(G.QIcon(str(Path(__file__).with_name('cell-review.ico'))))
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    try:
        data = Dataset(args.data) if args.data else None
        window = Window(data, args.results)
        if args.session:
            window.restore(args.session)
    except Exception as exc:
        W.QMessageBox.critical(None,'Could Not Open Cell Review',str(exc))
        return 1
    window.show()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
