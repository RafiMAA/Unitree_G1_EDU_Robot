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
        c.guide_status, c.guide_status_time = None, 0.
        c.guide_profile = Mock()
        c.guide_profile.call_async.return_value.done.return_value = True
        c.guide_profile.call_async.return_value.result.return_value.success = True
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
        self.assertNotIn('stack_nav', c.processes)  # Saved map is loaded in Navigate first.
        self.assertIs(c.processes['stack'], stack)
        self.switch({'tab': 'maps'})
        self.assertEqual(set(c.processes), {'web', 'stack', 'labels'})
        self.assertIs(c.map, grid)
        self.assertIs(c.processes['stack'], stack)
        self.switch({'tab': 'mapping'})
        self.assertIs(c.processes['stack'], stack)
        self.assertNotIn('labels', c.processes)

    def test_delete_rejects_loaded_map_and_switch_in_progress(self):
        c=self.console;c.selected={'id':'loaded.yaml','name':'loaded'}
        with patch('console_server.LIBRARY.delete') as delete:
            with self.assertRaisesRegex(ValueError,'currently loaded'):
                c.delete_map({'map_id':'loaded.yaml'})
            delete.assert_not_called()
            c.transitioning=True
            with self.assertRaisesRegex(ValueError,'switch'):
                c.delete_map({'map_id':'other.yaml'})
            delete.assert_not_called()
            c.transitioning=False
            c.delete_map({'map_id':'other.yaml'})
            delete.assert_called_once_with('other.yaml')

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
        self.console.map = object()
        self.switch({'tab': 'navigate'})
        self.assertFalse(self.console.localized)
        self.assertIn('stack_nav', self.console.processes)
        self.assertNotIn('labels', self.console.processes)

    def test_navigation_without_a_map_opens_load_controls_without_planner(self):
        self.switch({'tab':'navigate'})
        self.assertEqual(self.console.tab,'navigate')
        self.assertEqual(set(self.console.processes), {'web'})
        self.assertFalse(self.console.localized)

    def test_navigate_load_keeps_tab_and_requires_explicit_pose(self):
        from pathlib import Path
        c=self.console
        with patch('console_server.LIBRARY.resolve',return_value=Path('/tmp/airport.yaml')):
            self.switch({'mode':'localization','map_id':'airport','tab':'navigate'})
            self.assertEqual(c.tab,'navigate')
            self.assertIn('stack_nav',c.processes)
            c.on_pose(Mock())
            self.assertFalse(c.localized)  # AMCL's default pose cannot reveal the robot.
            c.on_initial_pose(Mock())
            self.assertFalse(c.localized)
            c.on_pose(Mock())
            self.assertTrue(c.localized)
            self.switch({'mode':'localization','map_id':'airport','tab':'navigate'})
            self.assertFalse(c.localized)  # Reloading the same map needs a new pose too.
            c.on_pose(Mock())
            self.assertFalse(c.localized)

    def test_conversation_can_load_saved_map_without_leaving_rag_or_starting_nav(self):
        from pathlib import Path
        c = self.console
        c.tab, c.mode = 'rag', 'mapping'
        c.map = object()
        worker = c.processes['rag'] = object()
        with patch('console_server.LIBRARY.resolve', return_value=Path('/maps/g1_map.yaml')), patch.object(c, 'ensure_rag'):
            self.switch({'mode': 'localization', 'map_id': 'g1_map', 'tab': 'rag'})
        self.assertEqual(c.tab, 'rag')
        self.assertEqual(c.mode, 'localization')
        self.assertEqual(c.selected['name'], 'g1_map')
        self.assertIs(c.processes['rag'], worker)
        self.assertFalse(c.localized)
        self.assertNotIn('stack_nav', c.processes)
        self.assertNotIn('safety', c.processes)
        self.assertIn('start_sim:=false', self.commands['stack'])

    def test_rag_context_reads_only_selected_map_labels(self):
        import tempfile
        from pathlib import Path
        from g1_navigation.label_store import LabelStore
        c = self.console
        c.navigation_status = {'state': 'idle'}
        with tempfile.TemporaryDirectory() as directory, patch('console_server.LIBRARY.directory', Path(directory)):
            LabelStore(directory).save('g1_map', [{'id': 'office', 'text': 'office', 'x': 1., 'y': 2.}])
            c.mode, c.selected = 'localization', {'id': '/maps/g1_map.yaml', 'name': 'g1_map'}
            self.assertEqual(c.rag_context()['locations'][0]['text'], 'office')
            self.assertEqual(c.rag_context()['map_name'], 'g1_map')
            c.selected = {'id': '/maps/other.yaml', 'name': 'other'}
            self.assertEqual(c.rag_context()['locations'], [])
            c.mode, c.selected = 'mapping', None
            self.assertIsNone(c.rag_context()['map_name'])
            self.assertEqual(c.rag_context()['locations'], [])

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
        self.assertNotIn('stack_nav', c.processes)  # Load a saved map to navigate.

    def spoken_setup(self):
        from types import SimpleNamespace as NS
        c = self.console
        c.tab, c.mode = 'rag', 'localization'
        c.selected = {'id': '/maps/airport.yaml', 'name': 'airport'}
        c.localized = c.amcl_ready = c.navigation_ready = True
        c.estop = False
        c.readiness = {'collision_monitor': True, 'velocity_smoother': True}
        label = {'id': 'office', 'text': 'Office', 'x': .5, 'y': .5, 'yaw': 1.0}
        c.rag_context = Mock(return_value={'map_id': '/maps/airport.yaml', 'locations': [label]})
        c.map = NS(info=NS(resolution=1., width=2, height=2, origin=NS(position=NS(x=0.,y=0.), orientation=NS(x=0.,y=0.,z=0.,w=1.))), data=[0,100,-1,0])
        return c, {'map_id': '/maps/airport.yaml', 'location_id': 'office'}

    def test_gateway_rejection_is_bound_to_voice_goal_for_narration(self):
        import json
        from types import SimpleNamespace as NS
        c=self.console
        c.spoken_goal={'x':1.,'y':2.}
        c.on_navigation_status(NS(data=json.dumps({'state':'rejected','message':'No feasible goal'})))
        self.assertEqual(c.guide_status['state'],'rejected')
        self.assertEqual(c.guide_status['goal'],c.spoken_goal)

    def test_spoken_goal_uses_saved_coordinates_and_keeps_rag_running(self):
        import rclpy.time
        c, payload = self.spoken_setup()
        c.processes['rag'] = object()
        c.node, c.goal_pub = Mock(), Mock()
        c.node.get_clock.return_value.now.return_value.to_msg.return_value = rclpy.time.Time().to_msg()
        c.gateway_mode = 'navigate'
        c.prepare_spoken_navigation(payload)
        self.assertIn('rag', c.processes)
        self.assertIn('stack_nav', c.processes)
        self.assertIn('start_sim:=false', self.commands['stack_nav'])
        result = c.send_spoken_goal(payload)
        self.assertEqual(result['state'], 'submitted')
        pose = c.goal_pub.publish.call_args.args[0]
        self.assertEqual(pose.header.frame_id, 'map')
        self.assertEqual(pose.pose.position.x, .5)
        self.assertAlmostEqual(pose.pose.orientation.z, __import__('math').sin(.5))

    def test_spoken_navigation_rejects_unlocalized_stale_or_blocked_goals(self):
        c, payload = self.spoken_setup()
        c.localized = False
        with self.assertRaisesRegex(ValueError, 'initial pose'):
            c.spoken_destination(payload)
        c.localized = True
        with self.assertRaisesRegex(ValueError, 'map changed'):
            c.spoken_destination({**payload, 'map_id': 'other'})
        with self.assertRaisesRegex(ValueError, 'not a saved'):
            c.spoken_destination({**payload, 'location_id': 'hallucinated'})
        c.map.data[0] = 100
        with self.assertRaisesRegex(ValueError, 'free map'):
            c.spoken_destination(payload)
        c.estop = True
        with self.assertRaisesRegex(ValueError, 'Emergency stop'):
            c.spoken_destination(payload)

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

    def test_astar_readiness_uses_trigger_success_and_stops_on_failure(self):
        from types import SimpleNamespace as NS
        from std_srvs.srv import Trigger
        c = self.console
        future, client = Mock(), Mock()
        future.done.return_value = True
        future.result.return_value = NS(success=True)
        client.service_is_ready.return_value = True
        client.call_async.return_value = future
        c.clients, c.readiness = {'g1_astar': client}, {}
        c.futures, c.future_started = {'g1_astar': future}, {}
        c.poll_ready()
        self.assertTrue(c.navigation_ready)
        self.assertIsInstance(client.call_async.call_args.args[0], Trigger.Request)
        future.result.return_value = NS(success=False)
        c.poll_ready()
        self.assertFalse(c.navigation_ready)


if __name__ == '__main__':
    unittest.main()
