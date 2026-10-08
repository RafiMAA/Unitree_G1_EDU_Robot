import base64
import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image
import yaml
from map_library import MapLibrary


class MapLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = MapLibrary(self.root / 'maps', self.root)
        image = io.BytesIO()
        Image.new('L', (20, 20), 254).save(image, 'PNG')
        self.payload = {'name': 'office', 'yaml': yaml.safe_dump({'image': 'office.png', 'resolution': 0.05, 'origin': [-1.5, 2, 0.3], 'negate': 0, 'occupied_thresh': 0.65, 'free_thresh': 0.25}), 'image_name': 'office.png', 'image': base64.b64encode(image.getvalue()).decode()}

    def test_import_pair_preserves_geometry_and_survives_restart(self):
        result = self.library.import_map(self.payload)
        document = yaml.safe_load(Path(result['id']).read_text())
        self.assertEqual(document['origin'], [-1.5, 2, 0.3])
        self.assertEqual(document['resolution'], 0.05)
        self.assertTrue((Path(result['id']).parent / document['image']).is_file())
        self.assertEqual(MapLibrary(self.root / 'maps', self.root).maps(), [result])

    def test_delete_archives_pair_and_labels_without_destroying_files(self):
        result=self.library.import_map(self.payload)
        path=Path(result['id'])
        image=path.parent/yaml.safe_load(path.read_text())['image']
        labels=self.library.directory/'office_labels.json';labels.write_text('[]')
        deleted=self.library.delete(result['id'])
        archive=Path(deleted['archive'])
        self.assertEqual(self.library.maps(),[])
        self.assertFalse(path.exists());self.assertFalse(image.exists());self.assertFalse(labels.exists())
        self.assertTrue((archive/'office.yaml').is_file())
        self.assertTrue((archive/image.relative_to(path.parent)).is_file())
        self.assertEqual((archive/'labels/office_labels.json').read_text(),'[]')

    def test_delete_preserves_image_shared_by_another_map(self):
        result=self.library.import_map(self.payload)
        path=Path(result['id']);image=path.parent/yaml.safe_load(path.read_text())['image']
        other=path.parent/'other.yaml';other.write_text(path.read_text())
        self.library.delete(result['id'])
        self.assertTrue(image.is_file())
        self.assertEqual(self.library.maps(),[{'id':str(other),'name':'other'}])

    def test_delete_rejects_arbitrary_files_outside_library(self):
        external=self.root/'external.txt';external.write_text('preserve')
        with self.assertRaises(ValueError):self.library.delete(str(external))
        self.assertEqual(external.read_text(),'preserve')

    def test_existing_map_is_never_overwritten(self):
        result = self.library.import_map(self.payload)
        original = Path(result['id']).read_bytes()
        with self.assertRaises(ValueError):
            self.library.import_map(self.payload)
        self.assertEqual(Path(result['id']).read_bytes(), original)

    def test_browser_upload_requires_matching_yaml_and_image_names(self):
        for filename in ('other.yaml', 'office.txt', '../office.yaml', None):
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, 'names must match'):
                self.library.import_map({**self.payload, 'yaml_name': filename})
        self.assertEqual(list(self.library.directory.iterdir()), [])
        result = self.library.import_map({**self.payload, 'yaml_name': 'office.yaml'})
        self.assertEqual(result['name'], 'office')

    def test_missing_wrong_image_and_traversal_fail_without_files(self):
        for change in ({'image_name': 'wrong.png'}, {'image': 'not base64'}, {'name': '../outside'}, {'image_name': '../office.png'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.library.import_map({**self.payload, **change})
        self.assertEqual(list(self.library.directory.iterdir()), [])
        with self.assertRaises(ValueError):
            self.library.resolve('/etc/passwd')

    def test_bad_map_schema_rejected(self):
        document = yaml.safe_load(self.payload['yaml'])
        for change in ({'resolution': -1}, {'origin': [0, float('nan'), 0]}, {'free_thresh': 0.9}, {'mode': 'unsupported'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.library.import_map({**self.payload, 'yaml': yaml.safe_dump({**document, **change})})
        self.assertEqual(self.library.maps(), [])

    def test_preexisting_workspace_saved_map_is_discovered(self):
        result = self.library.import_map(self.payload)
        document = yaml.safe_load(Path(result['id']).read_text())
        document['image'] = str((self.library.directory / document['image']).resolve())
        (self.root / 'earlier.yaml').write_text(yaml.safe_dump(document))
        self.assertEqual({m['name'] for m in self.library.maps()}, {'office', 'earlier'})

    def test_save_live_grid_preserves_orientation_unknown_cells_and_row_order(self):
        from types import SimpleNamespace as NS
        import math
        grid = NS(info=NS(width=3, height=2, resolution=.05,
                          origin=NS(position=NS(x=-2., y=1.),
                                    orientation=NS(x=0., y=0., z=math.sin(.15), w=math.cos(.15)))),
                  data=[0, 100, -1, 100, -1, 0])
        result = self.library.save_grid('live', grid)
        document = yaml.safe_load(Path(result['id']).read_text())
        self.assertAlmostEqual(document['origin'][2], .3)
        self.assertEqual(document['origin'][:2], [-2., 1.])
        with Image.open(self.library.directory / document['image']) as image:
            self.assertEqual(image.size, (3, 2))
            self.assertEqual(list(image.getdata()), [0, 205, 254, 254, 0, 205])
        occupancy = (255 - 205) / 255
        self.assertLess(document['free_thresh'], occupancy)
        self.assertLess(occupancy, document['occupied_thresh'])
        with self.assertRaises(ValueError):
            self.library.save_grid('live', grid)

    def test_incomplete_live_grid_is_not_saved(self):
        from types import SimpleNamespace as NS
        with self.assertRaisesRegex(ValueError, 'complete map'):
            self.library.save_grid('bad', NS(info=NS(width=2, height=2), data=[0]))


if __name__ == '__main__':
    unittest.main()
