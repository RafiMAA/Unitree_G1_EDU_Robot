#!/usr/bin/env python3
"""Local console supervisor. Owns and cleans up only processes it starts."""
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rclpy
from lifecycle_msgs.srv import GetState
from nav_msgs.msg import OccupancyGrid
from geometry_msgs.msg import PoseWithCovarianceStamped, PoseStamped
from rclpy.signals import SignalHandlerOptions
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from std_msgs.msg import String, Bool
from map_library import MapLibrary
from spoken_navigation import goal_is_free

UI = Path(__file__).resolve().parents[1]
WORKSPACE = UI.parent / 'g1-ros2-workspace'
API_PORT = int(os.environ.get('G1_CONSOLE_PORT', '8765'))
UI_PORT = int(os.environ.get('G1_UI_PORT', '5173'))
RAG_PORT = int(os.environ.get('G1_RAG_PORT', '8767'))
LIVE_PORT = int(os.environ.get('G1_RAG_LIVE_PORT', RAG_PORT + 1))
BRIDGE_PORT = int(os.environ.get('G1_ROSBRIDGE_PORT', '9090'))
LIBRARY = MapLibrary(os.environ.get('G1_MAPS_DIR', WORKSPACE / 'src/g1_navigation/maps'), WORKSPACE)
LOGS = WORKSPACE / 'log/console'


class Supervisor:
    def __init__(self):
        self.lock = threading.RLock()
        self.processes = {}
        self.mode = 'idle'
        self.tab = None
        self.selected = None
        self.error = ''
        self.rag_error = ''
        self.estop = False
        self.navigation_status = {'state': 'idle', 'message': 'No navigation request'}
        self.gateway_mode = 'idle'
        self.transitioning = False
        self.amcl_ready = False
        self.navigation_ready = False
        self.localized = False
        self.node = rclpy.create_node('g1_console_supervisor')
        self.mode_pub = self.node.create_publisher(String, '/ui/mode', 10)
        self.cancel_pub = self.node.create_publisher(Bool, '/ui/cancel_navigation', 10)
        self.speech_pub = self.node.create_publisher(String, '/g1/speech_text', 10)
        self.goal_pub = self.node.create_publisher(PoseStamped, '/ui/goal', 10)
        self.answer_pub = self.node.create_publisher(String, '/g1/agent_response', 10)
        self.clients = {name: self.node.create_client(GetState, f'/{name}/get_state') for name in ('amcl', 'bt_navigator', 'collision_monitor', 'velocity_smoother')}
        self.futures = {}
        self.future_started = {}
        self.readiness = {}
        self.node.create_subscription(Bool, '/ui/emergency_stop', lambda msg: setattr(self, 'estop', bool(msg.data)), 10)
        self.node.create_subscription(String, '/ui/navigation_status', self.on_navigation_status, 10)
        self.node.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self.on_pose, 10)
        self.node.create_subscription(PoseWithCovarianceStamped, '/initialpose', self.on_initial_pose, 10)
        self.node.create_timer(1.0, self.poll_ready)
        # Replay a static map for browsers opening after map_server activation.
        self.map = None
        self.map_pub = self.node.create_publisher(OccupancyGrid, '/ui/map', 1)
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.node.create_subscription(OccupancyGrid, '/map', self.on_map, qos)
        self.node.create_timer(1.0, self.replay_map)

    def on_navigation_status(self, msg):
        try:
            value = json.loads(msg.data)
            self.navigation_status = value
            if value.get('message', '').startswith('Mode: '):
                self.gateway_mode = value['message'].split(': ', 1)[1]
        except (ValueError, TypeError):
            pass

    def rag_context(self):
        from g1_navigation.label_store import LabelStore
        with self.lock:
            locations = []
            if self.mode == 'localization' and self.selected:
                locations = LabelStore(LIBRARY.directory).load(self.selected['name'])
            return {'map_id': self.selected['id'] if self.selected else None,
                    'map_name': self.selected['name'] if self.selected else None,
                    'mode': self.mode, 'transitioning': self.transitioning,
                    'locations': locations, 'localized': self.localized,
                    'navigation': self.navigation_status}

    def spoken_destination(self, payload):
        if self.tab != 'rag' or self.transitioning:
            raise ValueError('Keep the RAG conversation tab open')
        if self.estop:
            raise ValueError('Emergency stop is engaged')
        if self.mode != 'localization' or not self.selected or self.map is None:
            raise ValueError('Load a saved map in Maps & Localization first')
        if not self.localized or not self.amcl_ready:
            raise ValueError('Set the robot initial pose and wait for AMCL localization')
        context = self.rag_context()
        if payload.get('map_id') != context['map_id']:
            raise ValueError('The loaded map changed; repeat the destination request')
        label = next((item for item in context['locations'] if item['id'] == payload.get('location_id')), None)
        if label is None:
            raise ValueError('Destination is not a saved location on this map')
        if not goal_is_free(self.map, label):
            raise ValueError('The saved location must be in known free map space')
        return label

    def prepare_spoken_navigation(self, payload):
        with self.lock:
            self.spoken_destination(payload)
            self.ensure_web()
            self.ensure('safety', ['ros2', 'launch', 'g1_navigation', 'safety.launch.py'])
            self.ensure('stack_nav', ['ros2', 'launch', 'g1_navigation', 'mapping.launch.py',
                                      'start_sim:=false', 'start_web:=false', 'start_slam:=false',
                                      'start_nav:=true', 'cmd_vel_topic:=/cmd_vel_controller'])
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            with self.lock:
                self.spoken_destination(payload)
                if self.navigation_ready and all(self.readiness.get(name, False) for name in ('collision_monitor', 'velocity_smoother')):
                    return {'ready': True}
            time.sleep(.1)
        raise ValueError('Nav2 or its collision/velocity controllers are not ready; check navigation logs')

    def send_spoken_goal(self, payload):
        with self.lock:
            label = self.spoken_destination(payload)
            if not self.navigation_ready or not all(self.readiness.get(name, False) for name in ('collision_monitor', 'velocity_smoother')):
                raise ValueError('Nav2 and collision controls must be ready')
            self.mode_pub.publish(String(data='navigate'))
        deadline = time.monotonic() + 2
        while self.gateway_mode != 'navigate' and time.monotonic() < deadline:
            if self.tab != 'rag' or self.estop:
                raise ValueError('Navigation was canceled')
            time.sleep(.02)
        with self.lock:
            label = self.spoken_destination(payload)
            if self.gateway_mode != 'navigate':
                raise ValueError('Navigation gateway did not acknowledge navigation mode')
            goal = PoseStamped()
            goal.header.frame_id = 'map'
            goal.header.stamp = self.node.get_clock().now().to_msg()
            goal.pose.position.x, goal.pose.position.y = label['x'], label['y']
            goal.pose.orientation.z, goal.pose.orientation.w = math.sin(label['yaw'] / 2), math.cos(label['yaw'] / 2)
            self.goal_pub.publish(goal)
            return {'state': 'submitted', 'destination': label['text'], 'message': f"Goal submitted for {label['text']}"}

    def cancel_spoken_navigation(self):
        if self.tab == 'rag':
            self.mode_pub.publish(String(data='idle'))
            self.cancel_pub.publish(Bool(data=True))
        return {'state': 'canceled', 'message': 'Navigation canceled'}

    def on_map(self, msg):
        self.map = msg

    def replay_map(self):
        if self.map is not None and not self.transitioning:
            self.map_pub.publish(self.map)

    def on_initial_pose(self, msg):
        self.localized = False

    def on_pose(self, msg):
        if self.mode == 'localization' and not self.transitioning:
            self.localized = True

    def poll_ready(self):
        for name, client in self.clients.items():
            future = self.futures.get(name)
            if future is not None and not future.done():
                # A tab switch can destroy a service while its request is in
                # flight. Retire that request so the replacement node is polled.
                if time.monotonic() - self.future_started.get(name, 0) < 2.0:
                    continue
                future.cancel()
                self.futures.pop(name, None)
                future = None
            ready = False
            if future is not None:
                try:
                    ready = future.result().current_state.id == 3
                except Exception:
                    pass
            if name == 'amcl':
                self.amcl_ready = ready and self.mode == 'localization' and not self.transitioning
            elif name == 'bt_navigator':
                self.navigation_ready = ready and not self.transitioning
            self.readiness[name] = ready
            if client.service_is_ready():
                self.futures[name] = client.call_async(GetState.Request())
                self.future_started[name] = time.monotonic()
            else:
                self.futures.pop(name, None)

    def spawn(self, name, command, cwd=WORKSPACE):
        LOGS.mkdir(parents=True, exist_ok=True)
        with (LOGS / f'{name}.log').open('ab') as log:
            self.processes[name] = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        print(f'Started {name}; log: {LOGS / (name + ".log")}', flush=True)

    def stop(self, name):
        process = self.processes.pop(name, None)
        if not process:
            return
        for sig, seconds in ((signal.SIGINT, 8), (signal.SIGTERM, 3), (signal.SIGKILL, 1)):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                process.poll()  # Reap the launch parent if it has exited.
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    return
                time.sleep(0.05)
        process.wait(timeout=2)

    def launch_stack(self):
        if self.mode == 'mapping':
            command = ['ros2', 'launch', 'g1_navigation', 'mapping.launch.py', 'start_sim:=false', 'start_web:=false', 'start_nav:=false', 'cmd_vel_topic:=/cmd_vel_controller']
        else:
            command = ['ros2', 'launch', 'g1_navigation', 'navigation.launch.py', 'start_sim:=false', 'start_web:=false', 'start_safety:=false', 'start_nav:=false', f'map:={self.selected["id"]}']
        self.spawn('stack', command)

    def ensure(self, name, command):
        if name not in self.processes:
            self.spawn(name, command)

    def ensure_web(self):
        self.ensure('web', ['ros2', 'launch', 'g1_navigation', 'perception_web.launch.py',
                            'start_rosbridge:=false', 'start_labels:=false'])

    def ensure_rag(self):
        old = self.processes.get('rag')
        if old and old.poll() is not None:
            self.stop('rag')
        if 'rag' not in self.processes:
            for port in (RAG_PORT, LIVE_PORT):
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        probe.bind(('127.0.0.1', port))
                    except OSError as exc:
                        raise ValueError(f'Conversation port {port} is already occupied') from exc
            self.rag_error = ''
            self.spawn('rag', ['/usr/bin/python3', str(UI / 'scripts/rag-launcher.py')])

    def rag_request(self, route, payload=None):
        if self.tab != 'rag' or self.transitioning:
            raise ValueError('Open the RAG Conversation tab first')
        process = self.processes.get('rag')
        if not process or process.poll() is not None:
            raise ValueError(self.rag_error or 'Conversation backend is stopped. Reopen the RAG tab to retry')
        request = Request(f'http://127.0.0.1:{RAG_PORT}/{route}',
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=180 if route in ('turn', 'end') else 2) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise ValueError(json.load(exc).get('error', 'Conversation request failed')) from exc
        except (URLError, TimeoutError) as exc:
            raise ValueError('Preparing conversation. Please wait, or check log/console/rag.log if it does not finish') from exc
        if route == 'turn':
            self.speech_pub.publish(String(data=result['transcript']))
            self.answer_pub.publish(String(data=result['answer']))
        return result

    def start(self):
        # Simulation is always supplied externally; opening the UI is inert.
        self.spawn('bridge', ['ros2', 'run', 'rosbridge_server', 'rosbridge_websocket',
                             '--ros-args', '-p', f'port:={BRIDGE_PORT}',
                             '-p', 'default_call_service_timeout:=8.0',
                             '-p', 'call_services_in_new_thread:=true'])
        self.spawn('ui', ['npm', 'run', 'ui'], UI)

    def switch(self, payload):
        with self.lock:
            if self.transitioning:
                raise ValueError('A tab or map switch is already in progress')
            mode = payload.get('mode')
            tab = payload.get('tab')
            selected = self.selected
            if mode is not None:
                if mode not in ('mapping', 'localization'):
                    raise ValueError('Choose mapping or localization')
                tab = 'mapping' if mode == 'mapping' else 'maps'
                selected = None
                if mode == 'localization':
                    if payload.get('tab') == 'rag':
                        tab = 'rag'
                    path = LIBRARY.resolve(payload.get('map_id'))
                    selected = {'id': str(path), 'name': path.stem}
            elif tab not in ('mapping', 'navigate', 'maps', 'rag'):
                raise ValueError('Choose Mapping, Navigate, Maps & Localization or RAG Conversation')
            if tab == 'mapping':
                mode, selected = 'mapping', None
            if tab == 'navigate' and (self.mode == 'idle' or self.map is None):
                raise ValueError('Start Mapping or load a saved map before navigating')
            if tab == 'navigate' and self.mode == 'localization' and not self.localized:
                raise ValueError('Set the initial pose in Maps & Localization first')
            mode = mode or self.mode
            self.transitioning = True
            self.error = ''
            self.mode_pub.publish(String(data='idle'))
            self.cancel_pub.publish(Bool(data=True))

            def transition():
                try:
                    if tab != 'navigate':
                        self.stop('stack_nav')
                        self.navigation_ready = False
                    if tab != 'rag':
                        self.stop('rag')
                    if tab in ('maps', 'rag'):
                        self.stop('safety')
                        if tab == 'maps':
                            self.ensure('labels', ['ros2', 'run', 'g1_navigation', 'map_labels',
                                                   '--ros-args', '-p', f'labels_dir:={LIBRARY.directory}'])
                        else:
                            self.stop('labels')
                            self.ensure_rag()
                    else:
                        self.stop('labels')
                        self.ensure_web()
                        self.ensure('safety', ['ros2', 'launch', 'g1_navigation', 'safety.launch.py'])
                    if self.mode != mode or self.selected != selected:
                        self.stop('stack_nav')
                        self.stop('stack')
                        self.map = None
                        self.mode, self.selected = mode, selected
                        self.localized = self.amcl_ready = self.navigation_ready = False
                        self.futures.clear()
                        self.ensure_web()
                        self.launch_stack()
                    if tab == 'navigate':
                        self.ensure('stack_nav', ['ros2', 'launch', 'g1_navigation', 'mapping.launch.py',
                                                 'start_sim:=false', 'start_web:=false', 'start_slam:=false',
                                                 'start_nav:=true', 'cmd_vel_topic:=/cmd_vel_controller'])
                    self.tab = tab
                    self.mode_pub.publish(String(data=tab if tab in ('mapping', 'navigate') else 'idle'))
                except Exception as exc:
                    self.error = str(exc)
                finally:
                    self.transitioning = False
            threading.Thread(target=transition, daemon=True).start()

    def status(self):
        return {'managed': True, 'tab': self.tab, 'mode': self.mode, 'selected_map': self.selected, 'transitioning': self.transitioning,
                'safety_ready': 'safety' in self.processes and all(self.readiness.get(name, False) for name in (('velocity_smoother',) if self.tab == 'mapping' else ('collision_monitor', 'velocity_smoother'))),
                'labels_ready': 'labels' in self.processes and self.node.count_subscribers('/ui/map_label_command') > 0,
                'amcl_ready': self.amcl_ready, 'navigation_ready': self.navigation_ready, 'localized': self.localized,
                'maps_dir': str(LIBRARY.directory), 'maps': LIBRARY.maps(), 'error': self.error,
                'processes': {name: {'running': p.poll() is None, 'exit_code': p.poll()} for name, p in list(self.processes.items())}}

    def close(self):
        # Wait for a switch to finish before cleaning up owned groups.
        while self.transitioning:
            time.sleep(0.1)
        if rclpy.ok():
            self.mode_pub.publish(String(data='idle'))
            self.cancel_pub.publish(Bool(data=True))
        for name in ('rag', 'stack_nav', 'stack', 'safety', 'labels', 'web', 'bridge', 'ui'):
            self.stop(name)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, code, value):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/api/session':
            self.respond(200, self.server.supervisor.status())
        elif self.path == '/api/rag/context':
            try:
                self.respond(200, self.server.supervisor.rag_context())
            except Exception:
                self.respond(400, {'error': 'Saved map labels are unavailable'})
        elif self.path == '/api/rag/status':
            try:
                self.respond(200, self.server.supervisor.rag_request('status'))
            except ValueError as exc:
                self.respond(200, {'ready': False, 'configured': False, 'stage': 'Preparing conversation', 'error': str(exc)})
        else:
            self.respond(404, {'error': 'Unknown API route'})

    def do_POST(self):
        try:
            origin = self.headers.get('Origin')
            allowed_hosts = {'localhost', '127.0.0.1', socket.gethostname()}
            hosts_file = UI / '.phone-tls/hosts.json'
            if hosts_file.exists():
                allowed_hosts.update(json.loads(hosts_file.read_text()))
            extra_origins = os.environ.get('G1_UI_ALLOWED_ORIGINS', '').split(',')
            parsed = urlparse(origin or '')
            if origin and origin not in extra_origins and not (
                    parsed.scheme in ('http', 'https') and parsed.hostname in allowed_hosts
                    and parsed.port == UI_PORT):
                raise ValueError('Use the configured navigation console address')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Expected application/json')
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 30 * 1024 * 1024:
                raise ValueError('Invalid upload size (maximum 30 MB)')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Expected a JSON object')
            if self.path == '/api/rag/prepare-navigation':
                result = self.server.supervisor.prepare_spoken_navigation(payload)
            elif self.path == '/api/rag/goal':
                result = self.server.supervisor.send_spoken_goal(payload)
            elif self.path == '/api/rag/cancel':
                result = self.server.supervisor.cancel_spoken_navigation()
            elif self.path == '/api/rag/transcript':
                if self.server.supervisor.tab != 'rag':
                    raise ValueError('Open the RAG tab first')
                self.server.supervisor.speech_pub.publish(String(data=str(payload.get('text', ''))[:2000]))
                self.server.supervisor.answer_pub.publish(String(data=str(payload.get('answer', ''))[:4000]))
                result = {'published': True}
            elif self.path in ('/api/rag/turn', '/api/rag/end'):
                result = self.server.supervisor.rag_request(self.path.rsplit('/', 1)[1], payload)
            elif self.path == '/api/maps/save':
                with self.server.supervisor.lock:
                    supervisor = self.server.supervisor
                    if supervisor.transitioning or supervisor.mode != 'mapping' or supervisor.map is None:
                        raise ValueError('Start Mapping and wait for a live map before saving')
                    result = LIBRARY.save_grid(payload.get('name'), supervisor.map)
            elif self.path == '/api/maps/import':
                with self.server.supervisor.lock:
                    result = LIBRARY.import_map(payload)
            elif self.path == '/api/session':
                self.server.supervisor.switch(payload)
                result = {'accepted': True}
            else:
                self.respond(404, {'error': 'Unknown API route'})
                return
            self.respond(200, result)
        except Exception as exc:
            self.respond(400, {'error': str(exc)})


def main():
    for port in (API_PORT, BRIDGE_PORT, UI_PORT):
        with socket.socket() as probe:
            # Match HTTPServer reuse behavior so a recent Ctrl+C can restart.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                raise SystemExit(f'Port {port} is already in use. Stop your earlier UI/ROS launch terminals before running npm run dev. No existing process was stopped.')
    os.environ['G1_ROSBRIDGE_PORT'] = str(BRIDGE_PORT)
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    supervisor = Supervisor()
    server = ThreadingHTTPServer(('127.0.0.1', API_PORT), Handler)
    server.supervisor = supervisor
    def spin_ros():
        from rclpy.executors import ExternalShutdownException
        try:
            rclpy.spin(supervisor.node)
        except ExternalShutdownException:
            pass
    ros_thread = threading.Thread(target=spin_ros, daemon=True)
    ros_thread.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        supervisor.start()
        tls_cert = Path(os.environ.get('G1_TLS_CERT', UI / '.phone-tls/server.crt'))
        tls_key = Path(os.environ.get('G1_TLS_KEY', UI / '.phone-tls/server.key'))
        scheme = 'https' if tls_cert.exists() and tls_key.exists() else 'http'
        print(f'Open {scheme}://localhost:{UI_PORT} — Ctrl+C stops this console and its ROS processes.', flush=True)
        while True:
            time.sleep(1)
            for name, process in list(supervisor.processes.items()):
                if supervisor.transitioning:
                    continue
                if process.poll() is not None:
                    if name == 'rag':
                        supervisor.rag_error = 'Conversation setup stopped. Check log/console/rag.log, then reopen the RAG tab'
                        continue
                    raise RuntimeError(f'{name} exited ({process.returncode}). See {LOGS / (name + ".log")}')
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        supervisor.close()
        rclpy.shutdown()
        ros_thread.join(timeout=2)
        supervisor.node.destroy_node()


if __name__ == '__main__':
    main()
