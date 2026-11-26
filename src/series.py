"""Review several independently aligned sessions against one fixed ROI template."""
from dataclasses import dataclass, field, replace
import json
from pathlib import Path

import numpy as np
from scipy.io import savemat

from model import (Dataset, Transform, dataset_for_state, new_result, review_record,
                   review_values, validate_reviews)
from prepare_data import prepare_selected_pair
from preview import write_preview


@dataclass
class ReviewRound:
    data: Dataset
    transform: Transform
    reviews: dict = field(default_factory=dict)
    proposals: dict = field(default_factory=dict)
    note: str = ''
    image_field: str = 'footprints'
    position: dict = field(default_factory=dict)

    @classmethod
    def start(cls, data):
        height, width = data.mov.shape
        return cls(data, Transform(cx=width / 2, cy=height / 2))

    def completed(self):
        reviews = {str(i): self.reviews.get(str(i), {'status': 'unmatched'})
                   for i in range(self.data.ref.n)}
        return replace(self, reviews=reviews, proposals={})

    def record(self):
        validate_reviews(self.reviews, self.data)
        state = review_record(self.data, self.transform, self.reviews, self.proposals,
                              self.note, self.image_field)
        state['position'] = self.position
        return state


def check_next_dataset(rounds, data):
    anchor = rounds[0].data.ref
    if (data.ref.meta != anchor.meta or
            any(not np.array_equal(getattr(data.ref, k), getattr(anchor, k))
                for k in ('original_ids', 'offsets', 'x', 'y', 'weights'))):
        raise ValueError('Session 1 ROI data changed. Open a new series for this template.')
    folder = Path(data.mov.meta['folder']).resolve()
    previous = [anchor, *(r.data.mov for r in rounds)]
    if any(folder == Path(s.meta['folder']).resolve() for s in previous):
        raise ValueError('This session is already in the series. Choose it in the session list.')
    data.ref = anchor


def visible_reference_ids(n, earlier_reviews, show_hidden):
    eligible = set(range(n))
    if show_hidden:
        return eligible
    for reviews in earlier_reviews:
        eligible.intersection_update(int(k) for k, v in reviews.items()
                                     if v['status'] == 'accepted')
    return eligible


def series_map(rounds):
    n = rounds[0].data.ref.n
    mapping = np.zeros((n, len(rounds) + 1), dtype=np.float64)
    mapping[:, 0] = np.arange(1, n + 1)
    for column, item in enumerate(rounds, 1):
        for ref, decision in item.reviews.items():
            if decision['status'] == 'accepted':
                mapping[int(ref), column] = decision['moving'] + 1
    return mapping


def save_series(rounds, active, root, *, final=False):
    with new_result(root, 'cellreg_final' if final else 'manual') as (folder, published):
        def save_state(items, name):
            state = {'format_version': 2, 'active_round': active, 'output_folder': str(root),
                     'rounds': [item.record() for item in items]}
            (folder / name).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        save_state(rounds, 'session.json')
        if final:
            save_state([item.completed() for item in rounds], 'session_final.json')
            mapping = series_map(rounds)
            tables = {'cell_to_index_map': mapping,
                      'cell_to_index_map_complete': mapping[np.all(mapping > 0, axis=1)]}
            names = [rounds[0].data.ref.meta['name'], *(r.data.mov.meta['name'] for r in rounds)]
            savemat(folder / 'cellRegistered_manual.mat', {
                'cell_registered_struct': tables, **tables, 'session_names': np.array(names, dtype=object),
                'registration_method': 'manual', 'moving_to_reference_matrices':
                    np.stack([r.transform.matrix() for r in rounds], axis=2)})
            for i, item in enumerate(rounds):
                visible = visible_reference_ids(item.data.ref.n, (r.reviews for r in rounds[:i]),
                                                item.position.get('show_hidden', False))
                write_preview(item.data, item.transform, item.reviews,
                              folder / f'alignment_session_{i+2}.png', item.image_field, visible)
    return published


def read_series(state, current=None):
    records = state['rounds']
    active = state['active_round']
    if len(records) < 2 or not isinstance(active, int) or not 0 <= active < len(records):
        raise ValueError('Invalid multi-session review index.')
    rounds = []
    for record in records:
        try:
            data = dataset_for_state(record, current)
        except ValueError:
            folders = [Path(s['folder']) for s in record['source_sessions']]
            data = Dataset(prepare_selected_pair(*folders))
        t, reviews, proposals, note, image_field = review_values(record, data, with_image_field=True)
        if rounds:
            check_next_dataset(rounds, data)
        rounds.append(ReviewRound(data, t, reviews, proposals, note, image_field or 'footprints',
                                  record.get('position', {})))
    return rounds, active
