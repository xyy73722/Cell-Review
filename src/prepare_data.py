"""Prepare saved image/ROI data independently of the interactive app."""
from paths import DATA_ROOT
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from scipy.io import loadmat, whosmat


def prepare(config_path, output):
    config_path, output = Path(config_path), Path(output)
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    return write_pair(config, output, config_path)


def prepare_selected_pair(reference, moving, data_root=DATA_ROOT):
    folders = (reference, moving)
    if reference == moving or reference.samefile(moving):
        raise ValueError('Choose two different session folders.')
    inputs = []
    for folder in folders:
        for name in ('Fall.mat', 'footprints.mat', 'ops.npy'):
            path = folder / name
            if not path.is_file():
                raise ValueError(f'Missing {name} in {folder}')
            info = path.stat()
            inputs.append((os.path.normcase(str(path)), info.st_size, info.st_mtime_ns))
    title = f'{reference.name} + {moving.name}'
    config = {'reference': str(reference), 'moving': str(moving), 'pair_name': title}
    if reference.parent == moving.parent and reference.parent.name.lower() == 'footprints':
        cellreg = reference.parent.parent
        config['pair_name'] = f'{cellreg.parent.name} · {title}'
        pair_dir = cellreg / f'{reference.name}+{moving.name}'
        candidates = sorted(pair_dir.glob('cellRegistered_[0-9]*.mat'))
        if candidates:
            result = candidates[-1]
            info = result.stat()
            inputs.append((str(result), info.st_size, info.st_mtime_ns))
            config['cellreg_result'] = str(result)
    digest = hashlib.sha256(json.dumps(inputs).encode()).hexdigest()[:16]
    destination = data_root / f'{reference.name[:32]}+{moving.name[:32]}_{digest}'
    if not destination.is_dir():
        data_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=data_root, prefix='.preparing_') as scratch:
            staged = write_pair(config, Path(scratch) / 'pair')
            try:
                staged.rename(destination)
            except FileExistsError:
                if not destination.is_dir():
                    raise
    return destination


def write_pair(config, output, config_path=None):
    output.mkdir(parents=True, exist_ok=True)
    metadata = {'pair_name': config['pair_name'], 'sessions': [],
                'coordinate_convention': 'scene xy: pixel centers at x+0.5,y+0.5',
                'config_path': str(config_path) if config_path else None}
    arrays = {}
    for prefix, key in [('ref', 'reference'), ('mov', 'moving')]:
        folder = Path(config[key])
        # Fall.mat is the same curated source used by step0.m. Do not infer ROI
        # order from another copy of iscell.npy that may have different labels.
        fall = loadmat(folder / 'Fall.mat', variable_names=['stat', 'iscell'], simplify_cells=True)
        stats = fall['stat']
        if isinstance(stats, dict):
            stats = [stats]
        stats = list(stats)
        labels = np.atleast_2d(fall['iscell'])
        merged = set()
        for stat in stats:
            merged.update(int(x) for x in np.asarray(stat.get('imerge', [])).reshape(-1))
        keep = np.array([i for i in range(len(stats)) if i not in merged and labels[i, 0] != 0], dtype=np.int32)
        if not len(keep):
            raise ValueError(f'No cell ROIs were found in {folder}.')
        footprint_info = next((v for v in whosmat(folder / 'footprints.mat') if v[0] == 'spatial_footprints'), None)
        if footprint_info is None:
            raise ValueError(f'Missing spatial_footprints in {folder / "footprints.mat"}.')
        if len(keep) != footprint_info[1][0]:
            raise ValueError(f'{folder}: Fall.mat selection and footprints.mat ROI count differ.')
        ops = np.load(folder / 'ops.npy', allow_pickle=True).item()
        mean = np.asarray(ops['meanImg'], dtype=np.float32)
        if mean.ndim != 2 or not mean.size:
            raise ValueError(f'Invalid mean image in {folder}.')
        enhanced = np.asarray(ops.get('meanImgE', mean), dtype=np.float32)
        if enhanced.shape != mean.shape:
            enhanced = mean.copy()
        offsets = [0]
        xs, ys, weights = [], [], []
        centroids = []
        for original_index in keep:
            stat = stats[int(original_index)]
            x = np.asarray(stat['xpix'], dtype=np.int32).reshape(-1)
            y = np.asarray(stat['ypix'], dtype=np.int32).reshape(-1)
            lam = np.asarray(stat['lam'], dtype=np.float32).reshape(-1)
            if not (len(x) == len(y) == len(lam)):
                raise ValueError('ROI coordinate and weight lengths differ.')
            if not x.size or x.min() < 0 or y.min() < 0 or x.max() >= mean.shape[1] or y.max() >= mean.shape[0]:
                raise ValueError(f'Invalid pixel coordinates for Suite2p ROI #{original_index} in {folder}.')
            xs.extend(x); ys.extend(y); weights.extend(lam)
            offsets.append(len(xs))
            w = np.maximum(lam, 0)
            if not w.sum():
                w = np.ones(len(x))
            centroids.append([np.average(x + .5, weights=w), np.average(y + .5, weights=w)])
        for name, value in {'mean': mean, 'enhanced': enhanced, 'x': np.array(xs, np.int32),
                            'y': np.array(ys, np.int32), 'weights': np.array(weights, np.float32),
                            'offsets': np.array(offsets, np.int32), 'original_ids': keep,
                            'centroids': np.array(centroids, dtype=np.float64)}.items():
            arrays[f'{prefix}_{name}'] = value
        metadata['sessions'].append({'name': folder.name, 'folder': str(folder),
                                     'n_rois': int(len(keep)), 'height': int(mean.shape[0]),
                                     'width': int(mean.shape[1]), 'f_path': str(folder / 'F.npy')})
        print(f'{folder.name}: {len(keep)} ROIs; image {mean.shape}', flush=True)
    existing = []
    result_path = Path(config.get('cellreg_result', ''))
    if result_path.is_file():
        import h5py
        try:
            if h5py.is_hdf5(result_path):
                with h5py.File(result_path, 'r') as f:
                    mapping = np.array(f['cell_registered_struct/cell_to_index_map']).T
            else:
                record = loadmat(result_path, simplify_cells=True, variable_names=['cell_registered_struct'])
                mapping = np.atleast_2d(record['cell_registered_struct']['cell_to_index_map'])
            if mapping.ndim != 2 or mapping.shape[1] != 2 or not np.all(
                    (mapping == np.floor(mapping)) & (mapping >= 0) &
                    (mapping <= [s['n_rois'] for s in metadata['sessions']])):
                raise ValueError('Invalid cell_to_index_map for this pair.')
            existing = [[int(a - 1), int(b - 1)] for a, b in mapping if a > 0 and b > 0]
        except (OSError, KeyError, TypeError, ValueError) as exc:
            if config_path:
                raise
            metadata['cellreg_error'] = str(exc)
    metadata['cellreg_pairs'] = existing
    metadata['cellreg_result'] = config.get('cellreg_result', '')
    np.savez_compressed(output / 'images_rois.npz', **arrays)
    (output / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Prepared data: {output}; CellReg candidate pairs: {len(existing)}', flush=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(sys.executable, sys.version, flush=True)
    prepare(args.config, args.output)
