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
    def __init__(self, parent=None, current=None):
        super().__init__(parent)
        self.setWindowTitle('Select Sessions')
        self.current = current
        self.data = None
        self.job = None
        layout = W.QVBoxLayout(self)
        self.fields = []
        for index, title in enumerate(('Session 1 · Reference (red)', 'Session 2 · Moving (green)')):
            layout.addWidget(W.QLabel(title))
            row = W.QHBoxLayout()
            field = W.QLineEdit(current.meta['sessions'][index]['folder'] if current else '')
            field.setAccessibleName(title)
            field.setPlaceholderText('Session folder containing footprints.mat')
            self.fields.append(field)
            row.addWidget(field)
            browse = W.QPushButton('Browse…')
            browse.setAutoDefault(False)
            browse.clicked.connect(lambda checked=False, i=index: self.browse(i))
            row.addWidget(browse)
            layout.addLayout(row)
        self.message = W.QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.Open | W.QDialogButtonBox.Cancel)
        buttons.button(W.QDialogButtonBox.Open).setText('Load Pair')
        buttons.accepted.connect(self.load)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(780, self.sizeHint().height())

    def browse(self, index):
        value = self.fields[index].text().strip().strip('"')
        initial = str(Path(value).parent) if value else self.fields[0].text()
        folder = W.QFileDialog.getExistingDirectory(self, f'Select Session {index+1}', initial)
        if folder:
            self.fields[index].setText(folder)

    def load(self):
        folders = tuple(Path(field.text().strip().strip('"')) for field in self.fields)
        if not all(folder.is_absolute() for folder in folders):
            self.message.setText('Enter or browse to both session folders.')
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
