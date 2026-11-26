"""Render an alignment snapshot from the prepared pair and a saved session."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image
from scipy.ndimage import binary_erosion


def write_preview(data, transform, reviews, path, image_field='enhanced', reference_ids=None):
    from model import normalize_image, warp_image
    shape = data.ref.shape
    matrix = transform.matrix()
    if image_field == 'footprints':
        red = data.ref.footprint_projection(reference_ids)
        green = warp_image(data.mov.footprint_projection(), matrix, shape)
        rgb = np.stack((red, green, np.zeros_like(red)), axis=-1)
        Image.fromarray(np.rint(rgb * 255).astype(np.uint8)).save(path)
        return
    ref = normalize_image(getattr(data.ref, image_field))
    mov = warp_image(normalize_image(getattr(data.mov, image_field)), matrix, shape)
    rgb = np.rint(np.dstack([ref, mov, ref]) * 255).astype(np.uint8)
    for key, session, color in [('ref', data.ref, (255,121,206)), ('mov', data.mov, (104,255,160))]:
        mask = np.zeros(session.shape, bool)
        for r, item in reviews.items():
            if item['status'] == 'accepted' and (key == 'mov' or reference_ids is None or int(r) in reference_ids):
                x, y = session.pixels(int(r) if key=='ref' else item['moving'])
                mask[y,x] = True
        if key == 'mov':
            mask = warp_image(mask, matrix, shape, order=0)
        edges = mask & ~binary_erosion(mask)
        rgb[edges] = color
    Image.fromarray(rgb).save(path)


if __name__ == '__main__':
    from model import Dataset, dataset_for_state, review_values
    from series import visible_reference_ids
    parser = argparse.ArgumentParser()
    parser.add_argument('session',type=Path)
    parser.add_argument('--data',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    state = json.loads(args.session.read_text(encoding='utf-8-sig'))
    active = state.get('active_round', 0)
    records = state['rounds'] if state.get('format_version') == 2 else [state]
    record = records[active]
    data = Dataset(args.data) if args.data else dataset_for_state(record)
    transform, reviews, _, _, field = review_values(record, data, with_image_field=True)
    visible = visible_reference_ids(data.ref.n, (r['reviews'] for r in records[:active]),
                                    record.get('position', {}).get('show_hidden', False))
    if args.output.exists():
        parser.error('输出已存在，请指定一个新的文件名。')
    write_preview(data, transform, reviews, args.output, field or 'enhanced', visible)
