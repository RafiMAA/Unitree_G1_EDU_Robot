import json
from pathlib import Path

import pytest

from g1_navigation.label_store import LabelStore


def location(text='Entrance', **values):
    return dict(text=text, x=1.25, y=-2.5, **values)


def test_labels_roundtrip_after_restart_and_maps_are_separate(tmp_path):
    store = LabelStore(tmp_path)
    labels = store.apply(dict(operation='upsert', map_id='g1_map', label=location()))
    document = json.loads((tmp_path / 'g1_map_labels.json').read_text())
    assert document['frame_id'] == 'map'
    assert document['labels'][0]['text'] == 'Entrance'
    assert LabelStore(tmp_path).load('g1_map') == labels
    assert store.load('other_map') == []
    moved = dict(labels[0], text='Main entrance', x=3.5)
    store.apply(dict(operation='upsert', map_id='g1_map', label=moved))
    assert store.load('g1_map') == [moved]
    store.apply(dict(operation='delete', map_id='g1_map', id=moved['id']))
    assert store.load('g1_map') == []


def test_import_legacy_json_merges_without_losing_other_locations(tmp_path):
    store = LabelStore(tmp_path)
    entrance = store.apply(dict(operation='upsert', map_id='g1_map', label=location()))[0]
    store.apply(dict(operation='upsert', map_id='g1_map', label=location('Office')))
    labels = store.apply(dict(operation='import', map_id='g1_map', document=[
        dict(name='Entrance', x=4.0, y=5.0), dict(text='Washroom', x=6.0, y=7.0, z=1.0)
    ]))
    by_name = {label['text']: label for label in labels}
    assert set(by_name) == {'Entrance', 'Office', 'Washroom'}
    assert by_name['Entrance']['id'] == entrance['id']
    assert by_name['Entrance']['x'] == 4.0
    assert by_name['Washroom']['z'] == 1.0


@pytest.mark.parametrize('bad', [
    dict(text='', x=1, y=2), dict(text='bad', x=float('nan'), y=2),
    dict(text='bad', x=True, y=2), dict(text='bad', x='1', y=2),
    dict(text='Office', x=3, y=4),
])
def test_invalid_save_leaves_previous_json_untouched(tmp_path, bad):
    store = LabelStore(tmp_path)
    store.apply(dict(operation='upsert', map_id='g1_map', label=location('Office')))
    before = store.path('g1_map').read_bytes()
    with pytest.raises(ValueError):
        store.apply(dict(operation='upsert', map_id='g1_map', label=bad))
    assert store.path('g1_map').read_bytes() == before


def test_wrong_map_frame_and_path_traversal_are_rejected(tmp_path):
    store = LabelStore(tmp_path)
    for document in [dict(frame_id='odom', labels=[]), dict(map_id='another', labels=[])]:
        with pytest.raises(ValueError):
            store.apply(dict(operation='import', map_id='g1_map', document=document))
    for name in ['../outside', '/tmp/file', '', '..', 'x/../y']:
        with pytest.raises(ValueError):
            store.load(name)
    assert not list(tmp_path.iterdir())


def test_failed_atomic_replace_preserves_original(tmp_path, monkeypatch):
    store = LabelStore(tmp_path)
    labels = store.save('g1_map', [location()])
    before = store.path('g1_map').read_bytes()

    def fail_replace(*args):
        raise OSError('Disk write failed')

    monkeypatch.setattr('g1_navigation.label_store.os.replace', fail_replace)
    with pytest.raises(OSError):
        store.save('g1_map', [dict(labels[0], x=8.0)])
    assert store.path('g1_map').read_bytes() == before
    assert list(tmp_path.iterdir()) == [store.path('g1_map')]
