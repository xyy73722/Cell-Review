# Changelog

## v0.2.0 — Multi-session review

- Keep Session 1 fixed and choose **Next Session…** to align each additional session independently. The app saves the series when you add a session and marks unconfirmed ROIs in the preceding round as unmatched.
- By default, the red layer shows reference ROIs confirmed in every preceding round. Choose **Show Hidden Reference ROIs** to look for a cell that was missing earlier. The session selector lets you revisit earlier matches.
- Resume a series with **Open Progress**, including one started from a two-session progress file. The multi-session MAT export includes one column per session in `cell_to_index_map` and a `cell_to_index_map_complete` table for cells confirmed throughout.

## v0.1.7 — Track2p export from Fall.mat

- **Export Track2p Inputs** reads raw fluorescence from each session’s `Fall.mat` and writes `F.npy` in the same ROI order as the exported masks. This completes the single-file input workflow for Track2p export.

## v0.1.6 — Single-file session loading

- Load mean images and ROI footprints directly from the Suite2p `Fall.mat` in each session folder. A separate `ops.npy` or `footprints.mat` is no longer required for alignment or review.

## v0.1.5 — Synchronized trace navigation

- Use the mouse wheel to zoom both fluorescence traces to the same frame range. Drag horizontally to pan and double-click to return to the full session.

## v0.1.4 — Raw fluorescence traces

- Inspect raw `F` below the review canvas for the selected reference ROI (red) and moving ROI (green). Click another red ROI to switch the reference selection, or Shift-click where red and green overlap.

## v0.1.3 — Selected outlines and keyboard confirmation

- Animated dashed outlines identify the selected red and green ROIs. Select a suggested pair in the table and press **Enter** to confirm it and advance to the next suggestion.

## v0.1.2 — Color-coded review status

- The review table displays **Confirmed** in green, **Pending** in gray, **Unmatched** in red and **Suggested** in yellow.

## v0.1.1 — Windows application icon

- The Windows executable and app window share the red-line S icon. This first GitHub release includes the portable Windows app and its corresponding GPLv3 source.

## v0.1.0 — Initial portable build

- Align two sessions, review ROI pairs manually, save and reopen progress, and export a CellReg-style MAT file. The portable Windows build includes the runtime, application source and licenses.
