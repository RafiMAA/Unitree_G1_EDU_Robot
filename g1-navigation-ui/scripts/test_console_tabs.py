"""Tab ownership regression checks without launching ROS subprocesses."""
import threading
import time
import unittest
from unittest.mock import Mock, patch

from console_server import Supervisor


class TabTests(unittest.TestCase):
    def setUp(self):
        self.console = Supervisor.__new__(Supervisor)
        c = self.console
        c.lock = threading.RLock()
        c.mode, c.tab, c.selected = 'idle', None, None
        c.transitioning = c.localized = c.amcl_ready = c.navigation_ready = False
        c.error, c.map = '', None
        c.processes, c.futures = {}, {}
        c.mode_pub, c.cancel_pub = Mock(), Mock()
        self.commands = {}
        def spawn(name, command, *args):
            token = object()
            c.processes[name] = token
            self.commands[name] = command
        c.spawn = spawn
        c.stop = lambda name: c.processes.pop(name, None)

    def switch(self, payload):
        self.console.switch(payload)
        deadline = time.monotonic() + 3
        while self.console.transitioning and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.console.transitioning)
        self.assertEqual(self.console.error, '')

    def test_startup_never_launches_simulation_or_mapping(self):
        with patch.dict('os.environ', {'G1_START_SIM': 'true'}):
            self.console.start()
        self.assertEqual(set(self.console.processes), {'bridge', 'ui'})

    def test_tabs_preserve_slam_and_start_only_requested_services(self):
        self.switch({'tab': 'mapping'})
        c = self.console
        self.assertEqual(set(c.processes), {'web', 'safety', 'stack'})
        stack = c.processes['stack']
        grid = c.map = object()
        self.switch({'tab': 'navigate'})
        self.assertIn('stack_nav', c.processes)
        self.assertIn('start_slam:=false', self.commands['stack_nav'])
        self.assertIs(c.processes['stack'], stack)
        self.switch({'tab': 'maps'})
        self.assertEqual(set(c.processes), {'web', 'stack', 'labels'})
        self.assertIs(c.map, grid)
        self.assertIs(c.processes['stack'], stack)
        self.switch({'tab': 'mapping'})
        self.assertIs(c.processes['stack'], stack)
        self.assertNotIn('labels', c.processes)

    def test_maps_first_starts_only_label_saver(self):
        self.switch({'tab': 'maps'})
        self.assertEqual(set(self.console.processes), {'labels'})
        self.assertEqual(self.console.mode, 'idle')

    def test_saved_map_load_defers_navigation_until_pose_and_tab(self):
        with patch('console_server.LIBRARY.resolve') as resolve:
            from pathlib import Path
            resolve.return_value = Path('/tmp/room.yaml')
            self.switch({'mode': 'localization', 'map_id': 'room'})
        self.assertEqual(set(self.console.processes), {'labels', 'web', 'stack'})
        self.assertIn('start_nav:=false', self.commands['stack'])
        self.assertIn('start_sim:=false', self.commands['stack'])
        with self.assertRaisesRegex(ValueError, 'initial pose'):
            self.console.map = object()
            self.console.switch({'tab': 'navigate'})
        self.console.localized = True
        self.switch({'tab': 'navigate'})
        self.assertIn('stack_nav', self.console.processes)
        self.assertNotIn('labels', self.console.processes)

    def test_navigation_without_a_map_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'load a saved map'):
            self.console.switch({'tab': 'navigate'})
        self.assertEqual(self.console.processes, {})

    def test_rag_starts_only_conversation_and_stops_on_other_tab(self):
        c = self.console
        with patch.object(c, 'ensure_rag', side_effect=lambda: c.spawn('rag', ['conversation'])):
            self.switch({'tab': 'rag'})
        self.assertEqual(set(c.processes), {'rag'})
        self.assertEqual(c.mode, 'idle')
        self.switch({'tab': 'maps'})
        self.assertEqual(set(c.processes), {'labels'})

    def test_rag_keeps_existing_map_but_stops_navigation_and_drive(self):
        self.switch({'tab': 'mapping'})
        c = self.console
        grid = c.map = object()
        stack = c.processes['stack']
        self.switch({'tab': 'navigate'})
        with patch.object(c, 'ensure_rag', side_effect=lambda: c.spawn('rag', ['conversation'])):
            self.switch({'tab': 'rag'})
        self.assertEqual(set(c.processes), {'web', 'stack', 'rag'})
        self.assertIs(c.map, grid)
        self.assertIs(c.processes['stack'], stack)
        self.switch({'tab': 'navigate'})
        self.assertNotIn('rag', c.processes)
        self.assertIn('stack_nav', c.processes)

    def test_readiness_recovers_from_request_to_stopped_node(self):
        from types import SimpleNamespace as NS
        c = self.console
        pending = Mock()
        pending.done.return_value = False
        client = Mock()
        client.service_is_ready.return_value = True
        replacement = Mock()
        replacement.done.return_value = True
        replacement.result.return_value = NS(current_state=NS(id=3))
        client.call_async.return_value = replacement
        c.clients = {'collision_monitor': client}
        c.readiness = {}
        c.futures = {'collision_monitor': pending}
        c.future_started = {'collision_monitor': time.monotonic() - 10}
        c.poll_ready()
        pending.cancel.assert_called_once()
        self.assertIs(c.futures['collision_monitor'], replacement)
        c.poll_ready()
        self.assertTrue(c.readiness['collision_monitor'])


if __name__ == '__main__':
    unittest.main()
