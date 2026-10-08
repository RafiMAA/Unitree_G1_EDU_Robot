"""Standalone A* + lookahead navigation; no Nav2 navigation servers.

NavigateToPose is retained only as the ROS message/API contract for the existing
browser and voice gateway. Planning and driving are implemented here.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import math
import signal
import threading
import time

import numpy as np
import rclpy
from geometry_msgs.msg import Point32, PolygonStamped, PoseStamped, Twist
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import OccupancyGrid, Path
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.duration import Duration
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py.point_cloud2 import read_points_numpy
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger, SetBool
from g1_core.guide_behavior import CONFIG
from tf2_ros import Buffer, TransformException, TransformListener

from .astar import PADDED_FOOTPRINT, GridMap, angle_error, motion_clear, plan_path, tracking_command, starting_footprint_holes, clear_starting_holes


def yaw_of(q):
    return math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))


@dataclass
class Job:
    handle: object
    goal: tuple
    yaw: float
    start_time: float = field(default_factory=time.monotonic)
    done: str = ''
    path: list = field(default_factory=list)
    index: int = 0
    align: bool = True
    future: object = None
    last_plan: float = 0.
    progress_pose: tuple = None
    progress_time: float = field(default_factory=time.monotonic)
    recovery_pose: tuple = None
    recovery_time: float = 0.
    attempts: int = 0
    guide: bool = False
    face_yaw: float = None
    face_done: bool = False
    starting_holes: tuple = ()


class AStarNavigator(Node):
    def __init__(self):
        super().__init__('g1_astar')
        self.radius = float(self.declare_parameter('inflation_radius', .15).value)
        self.max_speed = float(self.declare_parameter('max_speed', .65).value)
        self.max_turn = float(self.declare_parameter('max_turn', 1.).value)
        self.lookahead = float(self.declare_parameter('lookahead_distance', .6).value)
        self.source_timeout = float(self.declare_parameter('source_timeout', 1.).value)
        self.cmd_topic = self.declare_parameter('cmd_vel_topic', '/cmd_vel_controller').value
        if not 0 <= self.radius or not 0 < self.max_speed <= 1. or not 0 < self.max_turn <= 1.:
            raise ValueError('Invalid inflation or speed outside the locomotion policy range')
        if not math.isfinite(self.radius) or not .1 <= self.lookahead <= 2. or not 0 < self.source_timeout <= 2.:
            raise ValueError('Invalid inflation, lookahead or sensor timeout')
        self.normal_speed, self.normal_turn = self.max_speed, self.max_turn
        self.guide_profile = False
        self.user_pose = None
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.callbacks = ReentrantCallbackGroup()
        self.grid = None
        self.cloud_world = np.empty((0,2))
        self.cloud_stamp = None
        self.grid_cache_key = None
        self.collision_grid = None
        self.motion_grid = None
        self.mode = 'idle'
        self.estop = False
        self.job = None
        self.last_status = None
        self.last_feedback = 0.
        self.tf = Buffer(cache_time=Duration(seconds=10.))
        self.listener = TransformListener(self.tf,self)
        self.cmd_pub = self.create_publisher(Twist,self.cmd_topic,10)
        qos = QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL,reliability=ReliabilityPolicy.RELIABLE)
        self.path_pub = self.create_publisher(Path,'/plan',qos)
        self.footprint_pub = self.create_publisher(PolygonStamped,'/local_costmap/published_footprint',10)
        self.status_pub = self.create_publisher(String,'/ui/navigation_status',10)
        self.guide_pub = self.create_publisher(String, '/g1/guide_status', 10)
        self.create_subscription(PoseStamped, '/g1/user_pose', self.on_user_pose, 10, callback_group=self.callbacks)
        self.create_service(SetBool, '/g1_astar/guide_profile', self.on_guide_profile, callback_group=self.callbacks)
        self.create_subscription(OccupancyGrid,'/map',self.on_map,qos,callback_group=self.callbacks)
        self.create_subscription(PointCloud2,'/g1/mid360/points_filtered',self.on_cloud,qos_profile_sensor_data,callback_group=self.callbacks)
        self.create_subscription(String,'/ui/mode',self.on_mode,10,callback_group=self.callbacks)
        mode_qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
        self.create_subscription(String, '/ui/mode', self.on_mode, mode_qos, callback_group=self.callbacks)
        self.create_subscription(Bool,'/ui/emergency_stop',self.on_estop,10,callback_group=self.callbacks)
        self.create_service(Trigger,'/g1_astar/ready',self.on_ready,callback_group=self.callbacks)
        self.server = ActionServer(self,NavigateToPose,'navigate_to_pose',
            execute_callback=self.execute,goal_callback=self.accept_goal,
            cancel_callback=lambda _: CancelResponse.ACCEPT,
            handle_accepted_callback=self.on_accepted,callback_group=self.callbacks)
        self.create_timer(.05,self.tick)
        self.get_logger().info(f'A* + lookahead navigation ready; inflation {self.radius:.2f} m')

    def on_user_pose(self, msg):
        if msg.header.frame_id == 'map' and all(math.isfinite(v) for v in (msg.pose.position.x, msg.pose.position.y)):
            self.user_pose = msg

    def on_guide_profile(self, request, response):
        with self.lock:
            self.guide_profile = request.data
            self.max_speed = min(self.normal_speed, CONFIG['walking']['linear']) if request.data else self.normal_speed
            self.max_turn = min(self.normal_turn, CONFIG['walking']['angular']) if request.data else self.normal_turn
        response.success, response.message = True, 'Guide profile updated'
        return response

    def pose(self):
        t = self.tf.lookup_transform('map','base_footprint',rclpy.time.Time())
        stamp = t.header.stamp.sec+t.header.stamp.nanosec/1e9
        now = self.get_clock().now().nanoseconds/1e9
        if now-stamp > self.source_timeout or now-stamp < -.2:
            raise TransformException('Robot transform is stale')
        return t.transform.translation.x,t.transform.translation.y,yaw_of(t.transform.rotation)

    def ready(self):
        if self.grid is None:
            return False,'Waiting for a map'
        now = self.get_clock().now().nanoseconds/1e9
        if self.cloud_stamp is None or not 0 <= now-self.cloud_stamp <= self.source_timeout:
            return False,'Waiting for fresh obstacle data'
        try:
            self.pose()
        except TransformException:
            return False,'Waiting for robot localization'
        return True,'A* navigator ready'

    def on_ready(self, _, response):
        with self.lock:
            response.success,response.message = self.ready()
        return response

    def on_map(self, msg):
        if msg.header.frame_id != 'map':
            return
        try:
            grid = GridMap(np.asarray(msg.data).reshape(msg.info.height,msg.info.width),msg.info.resolution,
                (msg.info.origin.position.x,msg.info.origin.position.y,yaw_of(msg.info.origin.orientation)))
        except (ValueError,TypeError):
            return
        with self.lock:
            self.grid = grid

    def on_cloud(self, msg):
        if msg.header.frame_id != 'base_footprint':
            return
        try:
            points = read_points_numpy(msg,field_names=('x','y','z'),skip_nans=True).reshape(-1,3)
            points = points[np.isfinite(points).all(axis=1)]
            points = points[(points[:,2]>=.10)&(points[:,2]<=2.)]
            x,y,yaw = self.pose()
            world = np.column_stack((x+points[:,0]*math.cos(yaw)-points[:,1]*math.sin(yaw),
                                     y+points[:,0]*math.sin(yaw)+points[:,1]*math.cos(yaw)))
        except (ValueError,TypeError,TransformException):
            return
        with self.lock:
            self.cloud_world = world
            self.cloud_stamp = msg.header.stamp.sec+msg.header.stamp.nanosec/1e9

    def on_mode(self, msg):
        with self.lock:
            self.mode = msg.data
            if self.mode != 'navigate':
                self.stop_job('canceled','Navigation canceled by mode change')

    def on_estop(self, msg):
        with self.lock:
            self.estop = msg.data
            if self.estop:
                self.stop_job('canceled','Emergency stop engaged')

    def accept_goal(self, request):
        p,q = request.pose.pose.position,request.pose.pose.orientation
        values = (p.x,p.y,q.x,q.y,q.z,q.w)
        with self.lock:
            valid = (self.mode == 'navigate' and not self.estop and self.ready()[0]
                     and request.pose.header.frame_id == 'map'
                     and all(math.isfinite(v) for v in values)
                     and abs(sum(v*v for v in (q.x,q.y,q.z,q.w))-1.) < .01)
            if valid:
                grid = self.get_grid()
                cell = grid.world_to_cell(p.x,p.y)
                valid = grid.contains(cell) and not grid.planning_mask(self.radius)[cell[1],cell[0]]
        return GoalResponse.ACCEPT if valid else GoalResponse.REJECT

    def on_accepted(self, handle):
        p = handle.request.pose.pose
        with self.lock:
            self.stop_job('canceled','Previous goal replaced')
            job = Job(handle,(p.position.x,p.position.y),yaw_of(p.orientation), guide=self.guide_profile)
            job.starting_holes = starting_footprint_holes(self.grid, self.pose())
            if job.starting_holes:
                self.get_logger().info(f'Allowing departure from {len(job.starting_holes)} unmapped cells in the starting turn envelope')
            if job.guide and self.user_pose is not None:
                user = self.user_pose
                age = self.get_clock().now().nanoseconds/1e9 - (user.header.stamp.sec+user.header.stamp.nanosec/1e9)
                if 0 <= age <= CONFIG['walking']['user_pose_max_age_seconds']:
                    pose = self.pose()
                    if math.dist(pose[:2], (user.pose.position.x, user.pose.position.y)) > .3:
                        job.face_yaw = math.atan2(user.pose.position.y-pose[1], user.pose.position.x-pose[0])
            handle._g1_job = job
            self.job = job
            self.report('planning','Planning an A* path')
        handle.execute()

    def execute(self, handle):
        job = handle._g1_job
        while rclpy.ok() and not job.done:
            time.sleep(.02)
        if job.done == 'succeeded':
            handle.succeed()
        elif job.done == 'canceled':
            # Preemption need not have received an action cancel request.
            if handle.is_cancel_requested:
                handle.canceled()
            else:
                handle.abort()
        else:
            if rclpy.ok():
                handle.abort()
        return NavigateToPose.Result()

    def report(self, state, message, **extra):
        status = (state,message)
        if status != self.last_status or extra:
            if status != self.last_status:
                self.get_logger().info(f'{state}: {message}')
            value = {'state': state, 'message': message, **extra}
            if self.job:
                value['goal'] = {'x': self.job.goal[0], 'y': self.job.goal[1]}
            msg = String(data=json.dumps(value))
            self.status_pub.publish(msg)
            self.guide_pub.publish(msg)
            self.last_status = status

    def publish_path(self, path):
        msg = Path()
        msg.header.frame_id = 'map'
        msg.header.stamp = self.get_clock().now().to_msg()
        for x,y in path:
            p = PoseStamped();p.header=msg.header
            p.pose.position.x=float(x);p.pose.position.y=float(y);p.pose.orientation.w=1.
            msg.poses.append(p)
        self.path_pub.publish(msg)

    def stop_job(self, state, message):
        if self.job:
            self.job.done = state
            self.publish_path([])
            self.report(state,message)
            self.job = None
        self.cmd_pub.publish(Twist())

    def get_grid(self):
        key = (id(self.grid),self.cloud_stamp,id(self.job))
        if key != self.grid_cache_key:
            grid = clear_starting_holes(self.grid, self.job.starting_holes if self.job else ())
            self.motion_grid = grid
            # Overlay current obstacles last: even an occupied starting cell blocks motion.
            self.collision_grid = grid.with_points(self.cloud_world)
            self.grid_cache_key = key
        return self.collision_grid

    def movement_clear(self, pose, vx, wz, horizon=1.):
        return motion_clear(self.motion_grid, pose, vx, wz, horizon,
                            obstacle_points=self.cloud_world)

    def plan(self, job, pose, grid):
        job.last_plan = time.monotonic()
        job.future = self.pool.submit(plan_path,grid,pose[:2],job.goal,self.radius)

    def recover(self, job, pose):
        if job.attempts >= 4:
            self.stop_job('failed','No feasible route after four recovery attempts')
            return
        job.attempts += 1
        job.recovery_pose = pose
        job.recovery_time = time.monotonic()
        job.path = []
        # An in-flight plan is ignored; its snapshot predates the retreat.
        job.future = None
        self.publish_path([])
        self.report('recovering','Backing out before replanning the original goal')

    def tick(self):
        with self.lock:
            footprint = PolygonStamped();footprint.header.frame_id='base_footprint'
            footprint.header.stamp=self.get_clock().now().to_msg()
            footprint.polygon.points=[Point32(x=x,y=y,z=0.) for x,y in PADDED_FOOTPRINT]
            self.footprint_pub.publish(footprint)
            cmd = Twist()
            job = self.job
            if job is None:
                self.cmd_pub.publish(cmd)
                return
            if job.handle.is_cancel_requested or self.mode != 'navigate' or self.estop:
                self.stop_job('canceled','Navigation canceled')
                return
            ready,message = self.ready()
            if not ready:
                self.cmd_pub.publish(cmd)
                self.report('paused',message)
                job.progress_time = time.monotonic()
                return
            pose = self.pose();grid = self.get_grid();now = time.monotonic()
            remaining = math.dist(pose[:2],job.goal)
            if job.face_yaw is not None and not job.face_done:
                error = angle_error(job.face_yaw, pose[2])
                if abs(error) <= .15 or now-job.start_time >= CONFIG['walking']['face_user_timeout_seconds']:
                    job.face_done = True
                    job.progress_time = now
                else:
                    cmd.angular.z = math.copysign(min(self.max_turn, max(.12, abs(error))), error)
                    if not self.movement_clear(pose, 0., cmd.angular.z, .5):
                        cmd.angular.z = 0.
                    self.cmd_pub.publish(cmd)
                    self.report('facing_user', 'Facing the passenger before escorting')
                    return

            if now-job.start_time > (CONFIG['walking']['goal_timeout_seconds'] if job.guide else 180):
                self.stop_job('failed','Goal timed out')
                return
            if job.progress_pose is None or math.dist(pose[:2],job.progress_pose[:2]) >= .08 or abs(angle_error(pose[2],job.progress_pose[2])) >= .15:
                job.progress_pose,job.progress_time = pose,now
            if job.recovery_pose is not None:
                if (math.dist(pose[:2],job.recovery_pose[:2]) >= .30 or now-job.recovery_time >= 4.
                        or not self.movement_clear(pose,-.15,0.,.5)):
                    job.recovery_pose = None
                    job.progress_pose,job.progress_time = pose,now
                    job.align = True
                    self.plan(job,pose,grid)
                else:
                    cmd.linear.x = -.15
                self.cmd_pub.publish(cmd)
                return
            if now-job.progress_time > 3.:
                self.recover(job,pose)
                self.cmd_pub.publish(cmd)
                return
            if remaining <= .20:
                error = angle_error(job.yaw,pose[2])
                if abs(error) <= .15:
                    self.stop_job('succeeded','Goal reached')
                    return
                cmd.angular.z = math.copysign(min(self.max_turn,max(.12,2*abs(error))),error)
            else:
                if job.future is not None and job.future.done():
                    try:
                        path = job.future.result()
                    except Exception as exc:
                        self.get_logger().error(f'A* planning failed: {exc}')
                        path = []
                    job.future = None
                    if path:
                        job.path,job.index = path,0
                        self.publish_path(path)
                    elif not job.path:
                        self.stop_job('failed','No A* route through known free space to this goal')
                        return
                if job.future is None and (not job.path or now-job.last_plan >= 1.):
                    self.plan(job,pose,grid)
                if not job.path:
                    self.cmd_pub.publish(cmd)
                    return
                # Bound nearest search to avoid skipping a hairpin across a wall.
                end = min(len(job.path),job.index+max(15,int(1.5/grid.resolution)))
                job.index = min(range(job.index,end),key=lambda i: math.dist(pose[:2],job.path[i]))
                target = job.path[-1]
                for point in job.path[job.index:]:
                    if math.dist(pose[:2],point) >= self.lookahead:
                        target = point
                        break
                vx,wz,job.align = tracking_command(pose,target,remaining,job.align,self.max_speed,self.max_turn,self.lookahead)
                cmd.linear.x,cmd.angular.z = vx,wz
            if not self.movement_clear(pose,cmd.linear.x,cmd.angular.z):
                # Slow to a collision-checked short horizon before declaring a stall.
                cmd.linear.x *= .25
                cmd.angular.z *= .25
                if not self.movement_clear(pose,cmd.linear.x,cmd.angular.z,.5):
                    cmd = Twist()
                self.report('blocked','Checking clearance and replanning around obstacles')
            else:
                self.report('navigating','Following the A* path',distance_remaining=round(remaining,3),
                            heading_error=round(angle_error(math.atan2(target[1]-pose[1], target[0]-pose[0]), pose[2]), 3) if remaining > .20 else 0.)
            self.cmd_pub.publish(cmd)
            if now-self.last_feedback >= .5:
                feedback = NavigateToPose.Feedback()
                feedback.current_pose.header.frame_id='map'
                feedback.current_pose.header.stamp=self.get_clock().now().to_msg()
                feedback.current_pose.pose.position.x=pose[0];feedback.current_pose.pose.position.y=pose[1]
                feedback.current_pose.pose.orientation.z=math.sin(pose[2]/2);feedback.current_pose.pose.orientation.w=math.cos(pose[2]/2)
                feedback.distance_remaining=remaining
                elapsed = now-job.start_time
                feedback.navigation_time.sec=int(elapsed);feedback.navigation_time.nanosec=int((elapsed-int(elapsed))*1e9)
                feedback.number_of_recoveries=job.attempts
                job.handle.publish_feedback(feedback)
                self.last_feedback=now

    def destroy_node(self):
        with self.lock:
            if rclpy.ok():
                self.stop_job('canceled','Navigator stopping')
            elif self.job:
                self.job.done='canceled'
                self.job=None
        self.pool.shutdown(wait=True,cancel_futures=True)
        self.server.destroy()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args,signal_handler_options=SignalHandlerOptions.NO)
    node=AStarNavigator();executor=MultiThreadedExecutor(num_threads=4);executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT,signal.SIG_IGN)
        with node.lock:
            node.stop_job('canceled','Navigator stopping')
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
