"""Isolated actual Humble A* navigation regression; synthetic sensors, no robot process."""
import os
os.environ['ROS_DOMAIN_ID'] = '98'
os.environ['ROS_LOCALHOST_ONLY'] = '1'
import json, math, signal, subprocess, time
from pathlib import Path
import rclpy
from rclpy.action import ActionClient
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from geometry_msgs.msg import TransformStamped, Twist, PoseStamped
from nav_msgs.msg import OccupancyGrid, Odometry
from nav2_msgs.action import ComputePathToPose, NavigateToPose
from nav2_msgs.srv import ClearEntireCostmap
from lifecycle_msgs.srv import GetState, ChangeState
from std_srvs.srv import Trigger
from std_msgs.msg import Header, String
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py.point_cloud2 import create_cloud_xyz32
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster

ROOT=Path(__file__).resolve().parents[3]
PARAMS=ROOT/'src/g1_navigation/config/nav2_params.yaml'
procs=[];logs=[]
def launch(command,name):
    log=open('/tmp/g1-corridor-'+name+'.log','w');logs.append(log)
    p=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);procs.append(p)
    return p
rclpy.init();node=rclpy.create_node('recovery_smoke')
def spin(seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.03)
def wait(future,seconds=15):
    end=time.monotonic()+seconds
    while not future.done() and time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.03)
    assert future.done(),'ROS request timed out'
    return future.result()
def service(kind,name,request):
    client=node.create_client(kind,name)
    assert client.wait_for_service(timeout_sec=10),name+' unavailable'
    spin(.7)
    return wait(client.call_async(request))
def active(name):
    deadline=time.monotonic()+25
    client=node.create_client(GetState,'/'+name+'/get_state')
    while time.monotonic()<deadline:
        if client.wait_for_service(timeout_sec=.2):
            if wait(client.call_async(GetState.Request()),5).current_state.id==3:return
        spin(.2)
    raise AssertionError(name+' did not activate')
try:
    from tf2_ros import TransformBroadcaster
    from std_msgs.msg import String
    qos=QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL,reliability=ReliabilityPolicy.RELIABLE)
    map_pub=node.create_publisher(OccupancyGrid,'/map',qos)
    cloud_pub=node.create_publisher(PointCloud2,'/g1/mid360/points_filtered',10)
    odom_pub=node.create_publisher(Odometry,'/g1/odom',10)
    command_pub=node.create_publisher(Twist,'/cmd_vel_smoothed',10)
    mode_pub=node.create_publisher(String,'/ui/mode',qos)
    robot=[2.,4.,0.]; commanded=Twist(); safe=Twist(); moving=False; blocked=True; recovered=False; violation=[]; last=time.monotonic()
    def on_command(msg):
        global commanded
        commanded=msg
    def on_safe(msg):
        global safe
        safe=msg
    node.create_subscription(Twist,'/cmd_vel_controller',on_command,10)
    node.create_subscription(Twist,'/cmd_vel_safe',on_safe,10)
    grid=OccupancyGrid();grid.header.frame_id='map';grid.info.width=grid.info.height=160;grid.info.resolution=.05;grid.info.origin.orientation.w=1.
    grid.data=[100 if x in (0,159) or y in (0,159) or (30<=x<=110 and y in (72,88)) else 0 for y in range(160) for x in range(160)]
    static=StaticTransformBroadcaster(node);dynamic=TransformBroadcaster(node)
    tf=TransformStamped();tf.header.frame_id='map';tf.child_frame_id='odom';tf.transform.rotation.w=1.;tf.header.stamp=node.get_clock().now().to_msg();static.sendTransform(tf)
    wall_points=[(x*.05,y,.8) for x in range(30,111) for y in (3.6,4.4)]
    def publish():
        global last, blocked, recovered
        if moving and blocked and safe.linear.x < -.10 and 'Backing out' in Path('/tmp/g1-corridor-nav.log').read_text():
            blocked=False; recovered=True
        now=time.monotonic();dt=min(.15,now-last);last=now
        x,y,theta=robot;c,s=math.cos(theta),math.sin(theta)
        if moving and not blocked:
            robot[0]+=(safe.linear.x*c-safe.linear.y*s)*dt
            robot[1]+=(safe.linear.x*s+safe.linear.y*c)*dt
            robot[2]+=safe.angular.z*dt
        x,y,theta=robot;c,s=math.cos(theta),math.sin(theta)
        for fx,fy in ((-.29,-.33),(-.29,.33),(.39,-.33),(.39,.33)):
            side=y+fx*s+fy*c
            if not 3.595<=side<=4.405:violation.append((x,y,theta,side))
        stamp=node.get_clock().now().to_msg()
        grid.header.stamp=stamp;map_pub.publish(grid)
        points=[((px-x)*c+(py-y)*s,-(px-x)*s+(py-y)*c,pz) for px,py,pz in wall_points]
        cloud_pub.publish(create_cloud_xyz32(Header(stamp=stamp,frame_id='base_footprint'),points))
        tf=TransformStamped();tf.header.frame_id='odom';tf.child_frame_id='base_footprint';tf.header.stamp=stamp
        tf.transform.translation.x=x;tf.transform.translation.y=y;tf.transform.rotation.z=math.sin(theta/2);tf.transform.rotation.w=math.cos(theta/2);dynamic.sendTransform(tf)
        odom=Odometry();odom.header.frame_id='odom';odom.child_frame_id='base_footprint';odom.header.stamp=stamp
        odom.pose.pose.position.x=x;odom.pose.pose.position.y=y;odom.pose.pose.orientation.z=tf.transform.rotation.z;odom.pose.pose.orientation.w=tf.transform.rotation.w
        odom.twist.twist=safe if not blocked else Twist();odom_pub.publish(odom)
        if moving:command_pub.publish(commanded)
    node.create_timer(.05,publish)
    launch(['ros2','launch','g1_navigation','mapping.launch.py','start_sim:=false','start_slam:=false','start_web:=false','start_nav:=true','cmd_vel_topic:=/cmd_vel_controller'],'nav')
    ready=node.create_client(Trigger,'/g1_astar/ready')
    assert ready.wait_for_service(timeout_sec=15), 'A* ready service unavailable'
    spin(.7)
    deadline=time.monotonic()+15
    while not wait(ready.call_async(Trigger.Request())).success:
        assert time.monotonic()<deadline,'A* did not become ready'
        spin(.1)
    launch(['/opt/ros/humble/lib/nav2_collision_monitor/collision_monitor','--ros-args','--params-file',str(PARAMS)],'monitor')
    for transition in (1,3):
        req=ChangeState.Request();req.transition.id=transition
        assert service(ChangeState,'/collision_monitor/change_state',req).success
    launch(['ros2','run','g1_navigation','retreat_guard'],'guard')
    deadline=time.monotonic()+10
    while mode_pub.get_subscription_count()<4 and time.monotonic()<deadline:spin(.1)
    mode_pub.publish(String(data='navigate'));spin(1.)
    nav=ActionClient(node,NavigateToPose,'/navigate_to_pose');assert nav.wait_for_server(timeout_sec=5)
    goal=NavigateToPose.Goal();goal.pose.header.frame_id='map';goal.pose.header.stamp=node.get_clock().now().to_msg()
    goal.pose.pose.position.x=4.;goal.pose.pose.position.y=4.;goal.pose.pose.orientation.w=1.
    handle=wait(nav.send_goal_async(goal));assert handle.accepted
    result=handle.get_result_async();moving=True
    deadline=time.monotonic()+45
    while not result.done() and time.monotonic()<deadline:spin(.1)
    assert result.done(),('Narrow corridor stalled',robot)
    assert result.result().status==4,('Goal failed',result.result().status,robot)
    assert not violation,violation[:3]
    assert recovered,'Expected stalled goal to back out before resuming'
    print('PASS: real A* navigation + Collision Monitor + final guard recovered from a stall and reached the original goal through 0.80 m corridor with padded 0.66 m body; pose:',robot,flush=True)
finally:
    for p in procs:
        if p.poll() is None:os.killpg(p.pid,signal.SIGINT)
    for p in procs:
        try:p.wait(timeout=7)
        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
    for log in logs:log.close()
    node.destroy_node();rclpy.shutdown()
