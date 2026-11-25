"""Image coordinates, candidate proposals, explicit reviews, and file exports."""
from paths import DATA_ROOT
import csv
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from tempfile import TemporaryDirectory
import numpy as np
from scipy.ndimage import affine_transform
from scipy.optimize import linear_sum_assignment
from scipy.io import loadmat, savemat
from preview import write_preview


@dataclass
class Transform:
    dx: float = 0.0
    dy: float = 0.0
    angle: float = 0.0
    cx: float = 256.0
    cy: float = 256.0
    flip_x: bool = False
    flip_y: bool = False

    def matrix(self):
        theta = np.deg2rad(self.angle)
        rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
        rotation[np.abs(rotation) < 1e-14] = 0
        linear = rotation @ np.diag([-1 if self.flip_x else 1, -1 if self.flip_y else 1])
        c = np.array([self.cx, self.cy])
        matrix = np.eye(3)
        matrix[:2, :2] = linear
        matrix[:2, 2] = np.array([self.dx, self.dy]) + c - linear @ c
        return matrix

    def set_pivot_scene(self, x, y):
        """Move the pivot without making the already aligned image jump."""
        sx, sy, _ = np.linalg.solve(self.matrix(), [x, y, 1])
        self.set_pivot_source(sx, sy)

    def set_pivot_source(self, x, y):
        scene_x, scene_y, _ = self.matrix() @ [x, y, 1]
        self.cx, self.cy = x, y
        self.dx, self.dy = scene_x - x, scene_y - y

    def pivot_scene(self):
        return self.matrix() @ [self.cx, self.cy, 1]


def transform_points(points, matrix):
    points = np.asarray(points, dtype=float)
    return points @ matrix[:2, :2].T + matrix[:2, 2]


def warp_image(image, matrix, shape, order=1):
    inverse = np.linalg.inv(matrix)
    # scipy indexes array pixel centers at integers; the canvas uses +0.5.
    offset_xy = inverse[:2, :2] @ [.5, .5] + inverse[:2, 2] - [.5, .5]
    return affine_transform(np.asarray(image), inverse[:2, :2][::-1, ::-1],
                            offset=offset_xy[::-1], output_shape=shape,
                            order=order, mode='grid-constant', cval=0, prefilter=False)


def normalize_image(image, low=1.0, high=99.5):
    image = np.nan_to_num(np.asarray(image, dtype=float))
    lo, hi = np.percentile(image, [low, high])
    return np.clip((image - lo) / max(hi - lo, 1e-12), 0, 1).astype(np.float32)


class Session:
    def __init__(self, archive, prefix, metadata):
        self.meta = metadata
        for key in ('mean', 'enhanced', 'x', 'y', 'weights', 'offsets', 'original_ids', 'centroids'):
            setattr(self, key, np.array(archive[f'{prefix}_{key}']))
        self.n = len(self.original_ids)
        self.shape = self.mean.shape

    def read_fluorescence(self, roi_indices=None):
        """Read raw Fall.mat F for all footprints or the requested footprint indices in order."""
        source = Path(self.meta['folder']) / 'Fall.mat'
        fields = loadmat(source, variable_names=['F', 'iscell'])
        values = fields.get('F')
        if values is None:
            raise ValueError(f'{source} has no F variable.')
        if (values.ndim != 2 or values.dtype.kind not in 'iuf' or not values.shape[1]
                or 'iscell' not in fields or values.shape[0] != len(fields['iscell'])
                or self.original_ids.min() < 0 or self.original_ids.max() >= values.shape[0]):
            raise ValueError(f'F in {source} must contain one row per original Suite2p ROI and one column per frame.')
        original_ids = self.original_ids if roi_indices is None else self.original_ids[roi_indices]
        return values[original_ids]

    def footprint_samples(self, index):
        """Pixel centers use integer array indices here; weights follow the CellReg display cutoff."""
        x, y = self.pixels(index)
        weights = self.weights[self.offsets[index]:self.offsets[index+1]]
        peak = weights.max(initial=0)
        if peak <= 0:
            return x[:0], y[:0], weights[:0]
        bright = weights >= .5 * peak
        return x[bright], y[bright], weights[bright] / peak

    def footprint_projection(self, indices=None):
        """CellReg's per-ROI peak normalization, 0.5 cutoff and sum projection."""
        projection = np.zeros(self.shape, dtype=np.float32)
        for i in (range(self.n) if indices is None else indices):
            x, y, weight = self.footprint_samples(i)
            np.add.at(projection, (y, x), weight)
        return projection.clip(0, 1)

    def pixels(self, index):
        sl = slice(self.offsets[index], self.offsets[index + 1])
        return self.x[sl], self.y[sl]

    def points(self, index):
        x, y = self.pixels(index)
        return np.column_stack([x + .5, y + .5])

    def hits_at(self, x, y, indices, footprint_only=False, matrix=None):
        if matrix is not None:
            x, y, _ = np.linalg.solve(matrix, [x, y, 1])
        ix, iy = int(np.floor(x)), int(np.floor(y))
        for index in indices:
            if footprint_only:
                px, py, _ = self.footprint_samples(index)
            else:
                px, py = self.pixels(index)
            if np.any((px == ix) & (py == iy)):
                yield int(index)

    def pick(self, x, y, matrix=None, radius=8, footprint_only=False):
        if matrix is not None:
            x, y, _ = np.linalg.inv(matrix) @ [x, y, 1]
        distances = np.linalg.norm(self.centroids - [x, y], axis=1)
        hit = next(self.hits_at(x, y, np.argsort(distances), footprint_only), None)
        if hit is not None:
            return hit
        if radius is None:
            return None
        index = int(np.argmin(distances))
        return index if distances[index] <= radius else None


class Dataset:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.meta = json.loads((self.folder / 'metadata.json').read_text(encoding='utf-8-sig'))
        with np.load(self.folder / 'images_rois.npz', allow_pickle=False) as arrays:
            self.ref = Session(arrays, 'ref', self.meta['sessions'][0])
            self.mov = Session(arrays, 'mov', self.meta['sessions'][1])


def confirmed_pairs_at(data, reviews, matrix, role, x, y):
    """Find every accepted pair whose painted white footprint covers this point."""
    moving_to_ref = {item['moving']: int(ref) for ref, item in reviews.items()
                     if item['status'] == 'accepted'}
    hits = set()
    if role in ('ref', 'review'):
        hits.update(data.ref.hits_at(x, y, moving_to_ref.values(), footprint_only=True))
    if role in ('mov', 'review'):
        mx, my, _ = np.linalg.solve(matrix, [x, y, 1])
        hits.update(moving_to_ref[m] for m in
                    data.mov.hits_at(mx, my, moving_to_ref, footprint_only=True))
    return sorted(hits)


def pair_score(data, ref_id, mov_id, matrix):
    # Resample the moving mask on the same nearest-neighbor grid as the export.
    x, y = data.mov.pixels(mov_id)
    x0, x1, y0, y1 = x.min(), x.max()+1, y.min(), y.max()+1
    corners = transform_points([[x0,y0], [x1,y0], [x0,y1], [x1,y1]], matrix)
    h, w = data.ref.shape
    left, top = np.maximum(0, np.floor(corners.min(axis=0)).astype(int))
    right, bottom = np.minimum([w,h], np.ceil(corners.max(axis=0)).astype(int))
    rx, ry = data.ref.pixels(ref_id)
    iou = 0.
    if right > left and bottom > top:
        src = np.zeros((y1-y0, x1-x0), dtype=np.uint8)
        src[y-y0, x-x0] = 1
        local = matrix.copy()
        local[:2, 2] += matrix[:2, :2] @ [x0,y0] - [left,top]
        mask = warp_image(src, local, (bottom-top, right-left), order=0)
        inside = (rx >= left) & (rx < right) & (ry >= top) & (ry < bottom)
        overlap = int(mask[ry[inside]-top, rx[inside]-left].sum())
        iou = overlap / (len(rx) + int(mask.sum()) - overlap)
    rc = data.ref.centroids[ref_id]
    mc = transform_points(data.mov.centroids[[mov_id]], matrix)[0]
    return float(iou), float(np.linalg.norm(rc - mc))


def propose(data, matrix, reviews, radius=18.0):
    """Only propose: never convert an algorithm suggestion into a confirmation."""
    refs = [r for r in range(data.ref.n) if str(r) not in reviews]
    used = {int(v['moving']) for v in reviews.values() if v['status'] == 'accepted'}
    movs = [m for m in range(data.mov.n) if m not in used]
    proposals = {}
    if not refs or not movs:
        return proposals
    rc = data.ref.centroids[refs]
    mc = transform_points(data.mov.centroids[movs], matrix)
    distances = np.linalg.norm(rc[:, None, :] - mc[None, :, :], axis=2)
    # Dummy columns leave unmatched references unassigned instead of forcing a match.
    cost = np.full((len(refs), len(movs) + len(refs)), 2.0)
    cost[:, len(movs):] = 1.05
    for i, j in zip(*np.where(distances <= radius)):
        iou, distance = pair_score(data, refs[i], movs[j], matrix)
        cost[i, j] = 1 - iou + .15 * distance / radius
    rows, columns = linear_sum_assignment(cost)
    for i, j in zip(rows, columns):
        if j < len(movs) and cost[i, j] < 1.05:
            proposals[str(refs[i])] = int(movs[j])
    return proposals


def validate_reviews(reviews, data):
    moving = []
    for key, entry in reviews.items():
        r = int(key)
        if not 0 <= r < data.ref.n:
            raise ValueError('Reference ROI index is out of range.')
        if entry['status'] == 'accepted':
            m = int(entry['moving'])
            if not 0 <= m < data.mov.n:
                raise ValueError('Moving ROI index is out of range.')
            moving.append(m)
        elif entry['status'] != 'unmatched':
            raise ValueError('Unknown review status.')
    if len(moving) != len(set(moving)):
        raise ValueError('A moving ROI cannot be confirmed for two reference ROIs.')


@contextmanager
def new_result(root, prefix):
    """Publish complete runs only, including after a network or disk error."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    final = root / datetime.now().strftime(prefix + '_%Y%m%d_%H%M%S_%f')
    with TemporaryDirectory(dir=root, prefix='.writing_') as temp:
        work = Path(temp) / 'output'
        work.mkdir()
        yield work, final
        work.rename(final)


def dataset_for_review(path, current=None):
    state = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if state.get('prepared_path'):
        folder = Path(state['prepared_path'])
        if current is not None and folder == current.folder:
            return current
        if folder.is_dir():
            return Dataset(folder)
    if current is not None and state['source_sessions'] == current.meta['sessions']:
        return current
    for metadata in DATA_ROOT.glob('*/metadata.json'):
        try:
            saved = json.loads(metadata.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
        if saved.get('sessions') == state['source_sessions']:
            return Dataset(metadata.parent)
    raise ValueError('Select the two original session folders, then open this progress file again.')


def read_review(path, data, *, with_image_field=False):
    state = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if state['format_version'] != 1 or state['source_sessions'] != data.meta['sessions']:
        raise ValueError('The sessions or data version in this progress file do not match the current pair.')
    transform = Transform(**state['transform'])
    if not np.isfinite(transform.matrix()).all():
        raise ValueError('The transform contains invalid values.')
    validate_reviews(state['reviews'], data)
    for r, m in state['proposals'].items():
        if not 0 <= int(r) < data.ref.n or not isinstance(m,int) or not 0 <= m < data.mov.n:
            raise ValueError('A suggested ROI index is out of range.')
    review = transform, state['reviews'], state['proposals'], state.get('note', '')
    if with_image_field:
        return (*review, state.get('image_field'))
    return review


def cellreg_map(data, reviews):
    """One row per distinct cell, one-based footprint IDs; zero means absent."""
    ref_rows = np.zeros((data.ref.n, 2), dtype=np.float64)
    ref_rows[:, 0] = np.arange(1, data.ref.n+1)
    seen = np.zeros(data.mov.n, dtype=bool)
    for ref, entry in reviews.items():
        if entry['status'] == 'accepted':
            mov = entry['moving']
            ref_rows[int(ref), 1] = mov + 1
            seen[mov] = True
    moving_only = np.flatnonzero(~seen) + 1
    return np.vstack((ref_rows, np.column_stack((np.zeros(len(moving_only)), moving_only))))


def save_review(data, transform, reviews, proposals, root, note='', image_field='enhanced',
                *, cellreg=False, progress=None):
    with new_result(root, 'cellreg_final' if cellreg else 'manual') as (destination, final):
        _write_review(data, transform, reviews, proposals, destination, note, image_field, progress)
        write_preview(data, transform, reviews, destination / 'alignment.png', image_field)
        if cellreg:
            mapping = cellreg_map(data, reviews)
            sessions = np.array([data.ref.meta['name'], data.mov.meta['name']], dtype=object)
            savemat(destination / 'cellRegistered_manual.mat', {
                'cell_registered_struct': {'cell_to_index_map': mapping},
                'cell_to_index_map': mapping, 'session_names': sessions,
                'registration_method': 'manual', 'moving_to_reference_matrix': transform.matrix()
            })
    return final


def _write_review(data, transform, reviews, proposals, destination, note, image_field, progress):
    validate_reviews(reviews, data)
    state = {'format_version': 1, 'pair_name': data.meta['pair_name'],
             'prepared_path': str(data.folder), 'source_sessions': data.meta['sessions'],
             'transform': asdict(transform), 'moving_to_reference_matrix': transform.matrix().tolist(),
             'reviews': reviews, 'proposals': proposals, 'note': note, 'image_field': image_field,
             'id_convention': 'footprint IDs 1-based; Suite2p IDs 0-based in labeled columns'}
    if progress is not None:
        (destination / 'session_final.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        state.update(reviews=progress[0], proposals=progress[1])
    (destination / 'session.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    accepted = sorted((int(r), int(v['moving'])) for r, v in reviews.items() if v['status'] == 'accepted')
    with (destination / 'confirmed_matches.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['reference_footprint_id1', 'moving_footprint_id1',
                         'reference_suite2p_id0', 'moving_suite2p_id0', 'IoU', 'distance_px'])
        for r, m in accepted:
            iou, distance = pair_score(data, r, m, transform.matrix())
            writer.writerow([r + 1, m + 1, int(data.ref.original_ids[r]), int(data.mov.original_ids[m]), iou, distance])
    with (destination / 'review_status.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f); writer.writerow(['reference_footprint_id1', 'status', 'moving_footprint_id1'])
        for r in range(data.ref.n):
            entry = reviews.get(str(r))
            writer.writerow([r + 1, entry['status'] if entry else 'pending',
                             int(entry['moving']) + 1 if entry and entry['status'] == 'accepted' else ''])
    indices = np.array([[r + 1, m + 1] for r, m in accepted], dtype=np.int32).reshape(-1, 2)
    suite2p_ids = np.array([[data.ref.original_ids[r], data.mov.original_ids[m]] for r, m in accepted], dtype=np.int32).reshape(-1, 2)
    savemat(destination / 'manual_aligned_index.mat', {
        'cleaned_data': indices, 'suite2p_indices0': suite2p_ids,
        'moving_to_reference_matrix': transform.matrix(),
        'reference_session': data.ref.meta['folder'], 'moving_session': data.mov.meta['folder']})


def export_track2p(data, matrix, root):
    """Write documented Track2p raw-npy inputs; retain an explicit original-ID map."""
    with new_result(root, 'track2p_input') as (destination, final):
        counts = _write_track2p(data, matrix, destination, final)
    return final, counts


def _write_track2p(data, matrix, destination, final):
    shape = data.ref.shape
    counts = []
    for name, session, moving in [('reference', data.ref, False), ('moving', data.mov, True)]:
        folder = destination / name / 'data_npy/plane0'
        folder.mkdir(parents=True, exist_ok=False)
        fov = warp_image(session.mean, matrix, shape) if moving else session.mean
        masks = []
        selected = []
        for index in range(session.n):
            mask = np.zeros(session.shape, dtype=np.uint8)
            x, y = session.pixels(index); mask[y, x] = 1
            if moving:
                mask = warp_image(mask, matrix, shape, order=0)
            if np.any(mask):
                selected.append(index); masks.append(mask.astype(bool))
        masks = np.asarray(masks, dtype=bool).reshape(-1, *shape)
        np.save(folder / 'F.npy', session.read_fluorescence(selected).astype(np.float32, copy=False))
        np.save(folder / 'fov.npy', np.asarray(fov, dtype=np.float32))
        np.save(folder / 'rois.npy', masks)
        with (folder / 'roi_index_map.csv').open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f); writer.writerow(['export_index0', 'footprint_id1', 'suite2p_id0'])
            writer.writerows((i, r + 1, int(session.original_ids[r])) for i, r in enumerate(selected))
        counts.append({'session': name, 'exported': len(selected), 'outside_reference_FOV': session.n - len(selected)})
    manifest = {'input_format': 'npy', 'all_ds_path': [str(final / 'reference'), str(final / 'moving')],
                'moving_to_reference_matrix': matrix.tolist(), 'counts': counts,
                'note': 'Moving fov and ROI masks were transformed together into reference coordinates.'}
    (destination / 'track2p_input.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return counts
