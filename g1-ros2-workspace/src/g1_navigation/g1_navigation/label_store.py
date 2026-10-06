"""Validated, atomic JSON persistence for named map positions (no ROS dependency)."""

import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid


def default_labels_dir():
    for parent in Path(__file__).resolve().parents:
        candidate = parent / 'src' / 'g1_navigation' / 'maps'
        if candidate.is_dir():
            return candidate
    return Path.home() / '.ros' / 'g1_navigation' / 'maps'


def validate_map_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', value):
        raise ValueError('Map name must contain 1–80 letters, digits, dots, underscores or hyphens')
    return value


def normalize_label(value):
    if not isinstance(value, dict):
        raise ValueError('Each location must be an object')
    text = value.get('text', value.get('name', ''))
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > 80:
        raise ValueError('Location name must contain 1–80 characters')
    if any(ord(c) < 32 for c in text):
        raise ValueError('Location names cannot contain control characters')
    label_id = value.get('id') or str(uuid.uuid4())
    if not isinstance(label_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', label_id):
        raise ValueError('Invalid location ID')
    result = {'id': label_id, 'text': text.strip()}
    for field, default in [('x', None), ('y', None), ('z', 0.0), ('yaw', 0.0)]:
        number = value.get(field, default)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError(f'{field} must be a finite number')
        result[field] = float(number)
    return result


def normalize_labels(values):
    if not isinstance(values, list) or len(values) > 1000:
        raise ValueError('Expected a list of up to 1000 locations')
    labels = [normalize_label(value) for value in values]
    if len({label['id'] for label in labels}) != len(labels):
        raise ValueError('Location IDs must be unique')
    if len({label['text'].casefold() for label in labels}) != len(labels):
        raise ValueError('Location names must be unique')
    return labels


class LabelStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser()

    def path(self, map_id):
        return self.directory / f'{validate_map_id(map_id)}_labels.json'

    def load(self, map_id):
        path = self.path(map_id)
        if not path.exists():
            return []
        document = json.loads(path.read_text(encoding='utf-8'))
        return self.read_document(document, map_id)

    @staticmethod
    def read_document(document, map_id):
        if isinstance(document, dict):
            if document.get('frame_id', 'map') != 'map':
                raise ValueError('Location coordinates must use the map frame')
            if document.get('map_id', map_id) != map_id:
                raise ValueError('This JSON file belongs to a different map')
            document = document.get('labels', document.get('locations'))
        return normalize_labels(document)

    def save(self, map_id, labels):
        labels = normalize_labels(labels)
        path = self.path(map_id)
        document = {'schema_version': 1, 'map_id': map_id, 'frame_id': 'map', 'labels': labels}
        self.directory.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.directory,
                                             prefix='.labels-', suffix='.tmp', delete=False) as stream:
                temp_path = Path(stream.name)
                json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
        return labels

    def apply(self, command):
        map_id = validate_map_id(command.get('map_id'))
        labels = self.load(map_id)
        operation = command.get('operation')
        if operation == 'select':
            return labels
        if operation == 'upsert':
            label = normalize_label(command.get('label'))
            labels = [old for old in labels if old['id'] != label['id']] + [label]
        elif operation == 'delete':
            label_id = command.get('id')
            if not any(label['id'] == label_id for label in labels):
                raise ValueError('Location no longer exists')
            labels = [label for label in labels if label['id'] != label_id]
        elif operation == 'import':
            imported = self.read_document(command.get('document'), map_id)
            # Merge imports by name, updating matching names and keeping other locations.
            by_name = {label['text'].casefold(): label for label in labels}
            for label in imported:
                key = label['text'].casefold()
                if key in by_name:
                    label['id'] = by_name[key]['id']
                else:
                    label['id'] = str(uuid.uuid4())
                by_name[key] = label
            labels = list(by_name.values())
        else:
            raise ValueError('Unknown location operation')
        return self.save(map_id, labels)
