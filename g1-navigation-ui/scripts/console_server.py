#!/usr/bin/env python3
"""Local console supervisor. Owns and cleans up only processes it starts."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import rclpy
from lifecycle_msgs.srv import GetState
from nav_msgs.msg import OccupancyGrid
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.signals import SignalHandlerOptions
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from std_msgs.msg import String, Bool
from map_library import MapLibrary

UI = Path(__file__).resolve().parents[1]
WORKSPACE = UI.parent / 'g1-ros2-workspace'
API_PORT = int(os.environ.get('G1_CONSOLE_PORT', '8765'))
UI_PORT = int(os.environ.get('G1_UI_PORT', '5173'))
BRIDGE_PORT = int(os.environ.get('G1_ROSBRIDGE_PORT', '9090'))
LIBRARY = MapLibrary(os.environ.get('G1_MAPS_DIR', WORKSPACE / 'src/g1_navigation/maps'), WORKSPACE)
LOGS = WORKSPACE / 'log/console'


class Supervisor:
    def __init__(self):
        self.lock = threading.RLock()
        self.processes = {}
        self.mode = 'mapping'
        self.selected = None
        self.error = ''
        self.transitioning = False
        self.amcl_ready = False
        self.navigation_ready = False
        self.localized = False
        self.node = rclpy.create_node('g1_console_supervisor')
        self.mode_pub = self.node.create_publisher(String, '/ui/mode', 10)
        self.cancel_pub = self.node.create_publisher(Bool, '/ui/cancel_navigation', 10)
        self.clients = {name: self.node.create_client(GetState, f'/{name}/get_state') for name in ('amcl', 'bt_navigator', 'collision_monitor', 'velocity_smoother')}
        self.futures = {}
        self.readiness = {}
        self.node.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self.on_pose, 10)
        self.node.create_subscription(PoseWithCovarianceStamped, '/initialpose', self.on_initial_pose, 10)
        self.node.create_timer(1.0, self.poll_ready)
        # Replay a static map for browsers opening after map_server activation.
        self.map = None
        self.map_pub = self.node.create_publisher(OccupancyGrid, '/ui/map', 1)
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.node.create_subscription(OccupancyGrid, '/map', self.on_map, qos)
        self.node.create_timer(1.0, self.replay_map)

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
                continue
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
            command = ['ros2', 'launch', 'g1_navigation', 'navigation.launch.py', 'start_sim:=false', 'start_web:=false', 'start_safety:=false', f'map:={self.selected["id"]}']
        self.spawn('stack', command)

    def start(self):
        if os.environ.get('G1_START_SIM', 'true').lower() != 'false':
            self.spawn('simulation', ['ros2', 'launch', 'g1_mujoco', 'sim.launch.py', 'start_rosbridge:=false', 'cmd_vel_topic:=/cmd_vel_safe'])
        self.spawn('web', ['ros2', 'launch', 'g1_navigation', 'perception_web.launch.py', f'rosbridge_port:={BRIDGE_PORT}', f'labels_dir:={LIBRARY.directory}'])
        self.spawn('safety', ['ros2', 'launch', 'g1_navigation', 'safety.launch.py'])
        self.launch_stack()
        self.spawn('ui', ['npm', 'run', 'ui'], UI)

    def switch(self, payload):
        with self.lock:
            if self.transitioning:
                raise ValueError('A map switch is already in progress')
            mode = payload.get('mode')
            if mode not in ('mapping', 'localization'):
                raise ValueError('Choose mapping or localization')
            selected = None
            if mode == 'localization':
                path = LIBRARY.resolve(payload.get('map_id'))
                selected = {'id': str(path), 'name': path.stem}
            if self.mode == mode and self.selected == selected:
                return
            self.transitioning = True
            self.error = ''
            self.mode_pub.publish(String(data='idle'))
            self.cancel_pub.publish(Bool(data=True))
            def transition():
                try:
                    self.stop('stack_nav')
                    self.stop('stack')
                    self.map = None
                    self.mode, self.selected = mode, selected
                    self.localized = self.amcl_ready = self.navigation_ready = False
                    self.futures.clear()
                    self.launch_stack()
                    self.mode_pub.publish(String(data='mapping' if mode == 'mapping' else 'idle'))
                except Exception as exc:
                    self.error = str(exc)
                finally:
                    self.transitioning = False
            threading.Thread(target=transition, daemon=True).start()

    def status(self):
        return {'managed': True, 'mode': self.mode, 'selected_map': self.selected, 'transitioning': self.transitioning,
                'safety_ready': all(self.readiness.get(name, False) for name in ('collision_monitor', 'velocity_smoother')),
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
        for name in ('stack_nav', 'stack', 'safety', 'web', 'simulation', 'ui'):
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
        else:
            self.respond(404, {'error': 'Unknown API route'})

    def do_POST(self):
        try:
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://localhost:{UI_PORT}', f'http://127.0.0.1:{UI_PORT}'):
                raise ValueError('Use the local navigation console')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Expected application/json')
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 30 * 1024 * 1024:
                raise ValueError('Invalid upload size (maximum 30 MB)')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Expected a JSON object')
            if self.path == '/api/maps/import':
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
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                raise SystemExit(f'Port {port} is already in use. Stop your earlier UI/ROS launch terminals before running npm run dev. No existing process was stopped.')
    os.environ['VITE_ROSBRIDGE_URL'] = f'ws://localhost:{BRIDGE_PORT}'
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
        print(f'Open http://localhost:{UI_PORT} — Ctrl+C stops this console and its ROS processes.', flush=True)
        while True:
            time.sleep(1)
            if supervisor.mode == 'mapping' and not supervisor.transitioning and supervisor.map is not None and 'stack_nav' not in supervisor.processes:
                supervisor.spawn('stack_nav', ['ros2', 'launch', 'g1_navigation', 'mapping.launch.py', 'start_sim:=false', 'start_web:=false', 'start_slam:=false', 'start_nav:=true', 'cmd_vel_topic:=/cmd_vel_controller'])
            for name, process in list(supervisor.processes.items()):
                if name in ('stack', 'stack_nav') and supervisor.transitioning:
                    continue
                if process.poll() is not None:
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
