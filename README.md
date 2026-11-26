# Cell Review

A Windows desktop app for aligning two or more calcium-imaging sessions and manually matching their cell ROIs. It opens with an empty workspace. The interface is in English.

See the [changelog](CHANGELOG.md) for the features added in each version.

## Download and open

1. Open **Releases** on the GitHub repository and download the **Windows-x64.zip** asset.
2. Use **Extract All** to unpack the entire ZIP into a writable folder on a local disk.
3. Open the extracted **Cell Review** folder and double-click **Cell Review.exe**. Keep the executable and its `_internal` folder together. All required runtime libraries are included.

## Prepare the input folders

1. Run Suite2p for each imaging session with MATLAB output enabled (`save_mat = True`). If you curate the cell labels in the Suite2p GUI, save the updated results to `Fall.mat` before copying the file. [Suite2p output documentation](https://suite2p.readthedocs.io/en/latest/outputs/#matlab-output)
2. Create one input folder per session. Copy that session’s `Fall.mat` from the corresponding Suite2p plane (for example, `suite2p/plane0/Fall.mat`) directly into its folder. You can also select the original plane folder if it already contains `Fall.mat`.
3. In **Select Sessions…**, choose each folder that directly contains `Fall.mat`, or paste the two full file paths. For later sessions, use **Next Session…**. Use **Output folder** to choose where Cell Review saves progress and exports.

```text
input/
  day1/Fall.mat
  day4/Fall.mat
  day8/Fall.mat
```

For this example, choose `input/day1` as the fixed reference and `input/day4` as the moving session. Then add `input/day8/Fall.mat` with **Next Session…**. Each file supplies `ops.meanImg`, `stat` and `iscell` for alignment and `F` for fluorescence traces and Track2p export.

## Review a session pair

1. Click **Select Sessions…**. Choose the first session as the fixed red layer and the second as the moving green layer. Each session folder needs only its Suite2p `Fall.mat`. Browse to the folders or paste the full paths to the two MAT files. The app reads `ops.meanImg` for the mean image, `ops.meanImgE` for the enhanced image when available, and `stat` and `iscell` for the ROIs. **Export Track2p Inputs** reads raw fluorescence from the same MAT file’s `F` variable and writes a new `F.npy` for the exported ROIs.
2. Click **Load Pair**. In **Alignment**, translate, rotate or flip the moving layer. **Set Rotation Center** picks a pivot and **Reset** restores its center.
3. In **Pair Review**, click a red ROI, click its green counterpart and press **Enter**. The selected reference and moving ROIs have animated red and green dashed outlines. Click another red ROI to switch reference cells. Shift+click selects red where the layers overlap. Below the images, the red and green traces show raw fluorescence from each session’s `Fall.mat` variable `F`, indexed by the original Suite2p ROI. Both traces use session-local frame numbers and each has its own labeled F scale. Scroll over either trace to zoom both around the pointer. Drag horizontally with the left mouse button to pan, or double-click to show the full session. Selecting another ROI keeps the current frame range. Drag the divider to resize the traces. To review suggestions, click a yellow **Suggested** row and press **Enter** to confirm it and move to the next suggestion. Confirmed pairs turn white on the overlay. Right-click a white ROI to reopen its pair. **Ctrl+Z** undoes the latest action in the active tab.
4. Set **Output folder** and use **Ctrl+S** to save. **Open Progress** restores a saved `session.json`. Keep the prepared `data/` directory beside the executable, or reload the two original sessions from the same paths.
5. **Finish Review and Export MAT** writes `cellRegistered_manual.mat` in a new export subfolder. The two columns of `cell_registered_struct.cell_to_index_map` correspond to the selected sessions in order. ROI IDs start at 1 and 0 means absent. Unconfirmed cells appear as unmatched. The export includes the editable `session.json`, the finalized `session_final.json` and an alignment preview.

The footprint view uses each selected ROI’s `stat.xpix`, `stat.ypix` and `stat.lam` from `Fall.mat`. ROI order follows the original Suite2p indices after cell filtering and exclusion of merged child ROIs.

To share the app, send the original release ZIP. Once the app has been used, the extracted directory can contain session data and personal settings.

## Review three or more sessions

After reviewing the first pair, click **Next Session…** and select the next session's `Fall.mat`. You can also start with an existing two-session progress file. Each new session has its own alignment to the original red template. Adding a session saves the series in your output folder and records unconfirmed cells in the preceding session as unmatched.

Red footprints initially show only template cells confirmed in **every** earlier session. **Show Hidden Reference ROIs** reveals cells with gaps for manual recovery. A recovered cell with an earlier gap stays hidden by default in later sessions. Green footprints include all ROIs of the current session. Use the session list to revisit earlier decisions.

The multi-session MAT contains `cell_registered_struct.cell_to_index_map` with one row per Session 1 ROI and one column per session, plus `cell_to_index_map_complete` with only fully matched rows. Zero means no confirmed match. `session_names` gives the column order. Reopen the exported `session.json` to resume at the saved round.

## 中文快速开始

从 GitHub 的 **Releases** 下载文件名以 `Windows-x64.zip` 结尾的压缩包，完整解压到可写目录，双击里面的 **Cell Review.exe**。首次打开显示 **No sessions loaded**。

点击 **Select Sessions…**，选择各自含有 `Fall.mat` 的两个 session 文件夹，也可粘贴两个 `Fall.mat` 的完整路径。在 **Alignment** 中对齐图像，再到 **Pair Review** 依次点击红色、绿色 ROI。当前选择以红、绿流动虚线显示，按回车确认。点另一处红色可换选，红绿重叠处用 Shift+左键指定红色。画布下方按各自的帧号绘制两个细胞在整个 session 的原始 F，上红下绿。滚轮以指针所在帧为中心同步缩放，按住左键左右拖动可平移，双击恢复全程。也可点击黄色 **Suggested** 行查看候选，按回车确认并跳到下一条候选。选好 **Output folder** 后，**Ctrl+S** 保存当前进度，**Finish Review and Export MAT** 导出两列 `cell_to_index_map` 和复核进度。原始输出与各次导出按时间保存在所选文件夹下。

处理三个或更多 session 时，点 **Next Session…** 接着选择下一份 `Fall.mat`。默认只显示此前每轮都配对成功的红色 ROI。勾选 **Show Hidden Reference ROIs** 可以找回曾经缺失的细胞。导出的配对矩阵每个 session 占一列。

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

The artifacts and SHA-256 checksums are written to `release/v0.2.0/`. `src/preview.py` redraws an exported alignment from its saved progress and prepared session data:

```powershell
.\.venv\Scripts\python.exe src\preview.py "path\to\session.json" --output "alignment.png"
```

## License

Cell Review is licensed under the GNU General Public License, version 3 (GPL-3.0-only). See `LICENSE`. The release includes corresponding application source under `source/` and third-party license notices under `licenses/`. Dependency source locations are listed in `THIRD_PARTY.md`.
