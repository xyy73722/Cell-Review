# Cell Review

A Windows desktop app for aligning two calcium-imaging sessions and manually matching their cell ROIs. It opens with an empty workspace. The interface is in English.

## Download and open

1. Open **Releases** on the GitHub repository and download the **Windows-x64.zip** asset.
2. Use **Extract All** to unpack the entire ZIP into a writable folder on a local disk.
3. Open the extracted **Cell Review** folder and double-click **Cell Review.exe**. Keep the executable and its `_internal` folder together. All required runtime libraries are included.

## Review a session pair

1. Click **Select Sessions…**. Choose the first session as the fixed red layer and the second as the moving green layer. Each session folder needs a Suite2p `Fall.mat` with `stat` and `iscell`, a `footprints.mat` with `spatial_footprints` in matching ROI order (ROI × height × width), and an `ops.npy` containing `meanImg`. Use session files from a trusted source. The optional Track2p export also reads the corresponding `F.npy`.
2. Click **Load Pair**. In **Alignment**, translate, rotate or flip the moving layer. **Set Rotation Center** picks a pivot and **Reset** restores its center.
3. In **Pair Review**, click a red ROI, click its green counterpart and press **Enter**. Confirmed pairs turn white. Right-click a white ROI to reopen its pair. **Ctrl+Z** undoes the latest action in the active tab.
4. Set **Output folder** and use **Ctrl+S** to save. **Open Progress** restores a saved `session.json`. Keep the prepared `data/` directory beside the executable, or reload the two original sessions from the same paths.
5. **Finish Review and Export MAT** writes `cellRegistered_manual.mat` in a new export subfolder. The two columns of `cell_registered_struct.cell_to_index_map` correspond to the selected sessions in order. ROI IDs start at 1 and 0 means absent. Unconfirmed cells appear as unmatched. The export includes the editable `session.json`, the finalized `session_final.json` and an alignment preview.

The footprint view uses the pixel coordinates and weights in `Fall.mat`. Cell Review checks the resulting ROI count against `spatial_footprints` in `footprints.mat`.

To share the app, send the original release ZIP. Once the app has been used, the extracted directory can contain session data and personal settings.

## 中文快速开始

从 GitHub 的 **Releases** 下载文件名以 `Windows-x64.zip` 结尾的压缩包，完整解压到可写目录，双击里面的 **Cell Review.exe**。首次打开显示 **No sessions loaded**。

点击 **Select Sessions…**，选择两个各含 `Fall.mat`、`footprints.mat`、`ops.npy` 的 session 文件夹。在 **Alignment** 中对齐图像，再到 **Pair Review** 依次点击红色、绿色 ROI，按回车确认。选好 **Output folder** 后，**Ctrl+S** 保存当前进度，**Finish Review and Export MAT** 导出两列 `cell_to_index_map` 和复核进度。原始输出与各次导出按时间保存在所选文件夹下。

## Run or build from source

Use 64-bit CPython 3.13 on Windows. The release was built with Python 3.13.5. In PowerShell, from the source folder:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src\app.py
```

To build the Windows ZIP and the matching source archive:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe build_release.py
```

The artifacts and SHA-256 checksums are written to `release/v0.1.1/`. `src/preview.py` redraws an exported alignment from its saved progress and prepared session data:

```powershell
.\.venv\Scripts\python.exe src\preview.py "path\to\session.json" --output "alignment.png"
```

## License

Cell Review is licensed under the GNU General Public License, version 3 (GPL-3.0-only). See `LICENSE`. The release includes corresponding application source under `source/` and third-party license notices under `licenses/`. Dependency source locations are listed in `THIRD_PARTY.md`.
