"""Select the ordered reference and moving session folders."""
from pathlib import Path

from PyQt5 import QtCore as C, QtWidgets as W

from model import Dataset
from prepare_data import prepare_selected_pair


class PairLoader(C.QThread):
    def __init__(self, folders, parent):
        super().__init__(parent)
        self.folders = folders
        self.data = None
        self.error = ''

    def run(self):
        try:
            self.data = Dataset(prepare_selected_pair(*self.folders))
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__


class SessionPicker(W.QDialog):
    def __init__(self, parent=None, current=None, *, append=False):
        super().__init__(parent)
        self.append = append
        self.setWindowTitle('Next Session' if append else 'Select Sessions')
        self.current = current
        self.data = None
        self.job = None
        layout = W.QVBoxLayout(self)
        self.fields = []
        moving_title = 'Next session · Moving (green)' if append else 'Session 2 · Moving (green)'
        for index, title in enumerate(('Session 1 · Reference (red)', moving_title)):
            layout.addWidget(W.QLabel(title))
            row = W.QHBoxLayout()
            field = W.QLineEdit(current.meta['sessions'][index]['folder']
                                if current and not (append and index == 1) else '')
            field.setReadOnly(append and index == 0)
            field.setAccessibleName(title)
            field.setPlaceholderText('Session folder or full path to Fall.mat')
            self.fields.append(field)
            row.addWidget(field)
            browse = W.QPushButton('Browse…')
            browse.setAutoDefault(False)
            if field.isReadOnly():
                browse.hide()
            browse.clicked.connect(lambda checked=False, i=index: self.browse(i))
            row.addWidget(browse)
            layout.addLayout(row)
        self.message = W.QLabel()
        if append:
            self.message.setText('Loading the next session saves this round and marks its unconfirmed ROIs as unmatched.')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.Open | W.QDialogButtonBox.Cancel)
        buttons.button(W.QDialogButtonBox.Open).setText('Load Session' if append else 'Load Pair')
        buttons.accepted.connect(self.load)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(780, self.sizeHint().height())

    def browse(self, index):
        value = self.fields[index].text().strip().strip('"')
        initial = str(Path(value).parent) if value else self.fields[0].text()
        if self.append:
            initial = value or str(Path(self.current.mov.meta['folder']).parent)
            folder, _ = W.QFileDialog.getOpenFileName(self, 'Select the next Fall.mat', initial,
                                                       'Suite2p session (Fall.mat)')
        else:
            folder = W.QFileDialog.getExistingDirectory(self, f'Select Session {index+1}', initial)
        if folder:
            self.fields[index].setText(folder)

    def load(self):
        paths = (Path(field.text().strip().strip('"')) for field in self.fields)
        folders = tuple(path.parent if path.name.lower() == 'fall.mat' else path for path in paths)
        if not all(folder.is_absolute() for folder in folders):
            self.message.setText('Choose both session folders or paste their full Fall.mat paths.')
            return
        if self.current and folders == tuple(Path(s['folder']) for s in self.current.meta['sessions']):
            self.data = self.current
            self.accept()
            return
        self.message.setText('Loading session images and ROIs…')
        self.setEnabled(False)
        self.job = PairLoader(folders, self)
        self.job.finished.connect(self.loaded)
        self.job.start()

    def loaded(self):
        self.setEnabled(True)
        self.data = self.job.data
        self.message.setText(self.job.error)
        self.job.deleteLater()
        self.job = None
        if self.data is not None:
            self.accept()

    def reject(self):
        if self.job is None:
            super().reject()
