"""Render an alignment snapshot from the prepared pair and a saved session."""
import argparse
from pathlib import Path
import numpy as np
from PIL import Image
from scipy.ndimage import binary_erosion


def write_preview(data, transform, reviews, path, image_field='enhanced'):
    from model import normalize_image, warp_image
    shape = data.ref.shape
    matrix = transform.matrix()
    if image_field == 'footprints':
        red = data.ref.footprint_projection()
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
            if item['status'] == 'accepted':
                x, y = session.pixels(int(r) if key=='ref' else item['moving'])
                mask[y,x] = True
        if key == 'mov':
            mask = warp_image(mask, matrix, shape, order=0)
        edges = mask & ~binary_erosion(mask)
        rgb[edges] = color
    Image.fromarray(rgb).save(path)


if __name__ == '__main__':
    from model import Dataset, dataset_for_review, read_review
    parser = argparse.ArgumentParser()
    parser.add_argument('session',type=Path)
    parser.add_argument('--data',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    data = Dataset(args.data) if args.data else dataset_for_review(args.session)
    transform, reviews, _, _, field = read_review(args.session, data, with_image_field=True)
    if args.output.exists():
        parser.error('输出已存在，请指定一个新的文件名。')
    write_preview(data,transform,reviews,args.output,field or 'enhanced')
