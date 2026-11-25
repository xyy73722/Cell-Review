# Third-party software

Cell Review uses unmodified published Python wheels. The Windows package keeps the Qt DLLs and plugins as separate, replaceable files under `_internal/PyQt5/Qt5/`. License and attribution files supplied with the distributions are collected under `licenses/`.

| Component | Upstream source |
|---|---|
| CPython 3.13.5 | [Python release and source](https://www.python.org/downloads/release/python-3135/) |
| PyQt5 5.15.11 (GPLv3) | [Riverbank source distribution](https://pypi.org/project/PyQt5/5.15.11/#files) |
| Qt 5.15.2 (LGPLv3) | [Qt source archive](https://download.qt.io/archive/qt/5.15/5.15.2/single/qt-everywhere-src-5.15.2.tar.xz), [Windows runtime distribution](https://pypi.org/project/PyQt5-Qt5/5.15.2/) |
| PyQt5-sip 12.20.1 | [Source distribution](https://pypi.org/project/PyQt5-sip/12.20.1/#files) |
| NumPy 2.1.3 | [Source distribution](https://pypi.org/project/numpy/2.1.3/#files) |
| SciPy 1.15.3 | [Source distribution](https://pypi.org/project/scipy/1.15.3/#files) |
| h5py 3.12.1 | [Source distribution](https://pypi.org/project/h5py/3.12.1/#files) |
| Pillow 11.1.0 | [Source distribution](https://pypi.org/project/Pillow/11.1.0/#files) |
| PyInstaller 6.16.0 bootloader | [Source and bootloader exception](https://github.com/pyinstaller/pyinstaller/tree/v6.16.0) |
