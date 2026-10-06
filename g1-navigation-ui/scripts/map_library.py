"""Validate and store Nav2 YAML/image pairs without overwriting existing maps."""
import base64
import io
import math
import re
from pathlib import Path
import shutil
import tempfile

from PIL import Image
import yaml


def map_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', value):
        raise ValueError('Map name must start with a letter/number; use letters, numbers, _, - or .')
    return value


def validate_map(document):
    if not isinstance(document, dict) or not isinstance(document.get('image'), str):
        raise ValueError('Select a ROS map YAML containing image, resolution and origin')
    for field in ('resolution', 'occupied_thresh', 'free_thresh'):
        value = document.get(field)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError(f'Invalid map {field}')
    if document['resolution'] <= 0 or not 0 <= document['free_thresh'] < document['occupied_thresh'] <= 1:
        raise ValueError('Invalid resolution or occupancy thresholds')
    origin = document.get('origin')
    if not isinstance(origin, list) or len(origin) != 3 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in origin):
        raise ValueError('Map origin must contain three finite numbers')
    if document.get('negate') not in (0, 1) or document.get('mode', 'trinary') not in ('trinary', 'scale', 'raw'):
        raise ValueError('Unsupported map negate/mode')
    return document


class MapLibrary:
    def __init__(self, directory, workspace):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.workspace = Path(workspace).resolve()

    def maps(self):
        results = []
        paths = set(self.directory.glob('*.yaml')) | set(self.directory.glob('*.yml'))
        paths |= set(self.workspace.glob('*.yaml')) | set(self.workspace.glob('*.yml'))
        for path in sorted(paths):
            try:
                document = validate_map(yaml.safe_load(path.read_text()))
                image = Path(document['image'])
                if not image.is_absolute():
                    image = path.parent / image
                if image.is_file():
                    results.append({'id': str(path.resolve()), 'name': path.stem})
            except (OSError, ValueError, yaml.YAMLError):
                continue
        return results

    def resolve(self, identifier):
        for item in self.maps():
            if item['id'] == identifier:
                return Path(identifier)
        raise ValueError('Choose a map from the saved map library')

    def import_map(self, payload):
        name = map_name(payload.get('name'))
        destination = self.directory / f'{name}.yaml'
        if destination.exists() or any(item['name'] == name for item in self.maps()):
            raise ValueError('That map name already exists. Choose a new name; existing maps are preserved.')
        text = payload.get('yaml', '')
        if not isinstance(text, str) or len(text) > 100000:
            raise ValueError('Map YAML is too large')
        document = validate_map(yaml.safe_load(text))
        image_name = payload.get('image_name', '')
        if Path(document['image']).name != image_name:
            raise ValueError(f"YAML references {Path(document['image']).name}; select that image file")
        if not isinstance(image_name, str) or Path(image_name).name != image_name or '/' in image_name or '\\' in image_name:
            raise ValueError('Use a plain image filename')
        suffix = Path(image_name).suffix.lower()
        if suffix not in ('.pgm', '.png', '.bmp', '.jpg', '.jpeg'):
            raise ValueError('Supported map images: PGM, PNG, BMP, JPG')
        try:
            data = base64.b64decode(payload.get('image', ''), validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError('Invalid map image encoding') from exc
        if not data or len(data) > 20 * 1024 * 1024:
            raise ValueError('Map image must be between 1 byte and 20 MB')
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 25_000_000:
                raise ValueError('Map image exceeds 25 million cells')
            image.verify()
        # A unique asset directory lets the final YAML rename commit the pair.
        asset_dir = Path(tempfile.mkdtemp(prefix=f'{name}_assets_', dir=self.directory))
        temporary = None
        try:
            (asset_dir / image_name).write_bytes(data)
            document['image'] = f'{asset_dir.name}/{image_name}'
            with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                yaml.safe_dump(document, file)
            temporary.replace(destination)
        except Exception:
            shutil.rmtree(asset_dir)
            if temporary:
                temporary.unlink(missing_ok=True)
            raise
        return {'id': str(destination), 'name': name}
