"""Isolated actual Humble Nav2 regression; synthetic sensors, no robot process."""
import os
os.environ['ROS_DOMAIN_ID'] = '97'
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
from std_msgs.msg import Header, String
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py.point_cloud2 import create_cloud_xyz32
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster

ROOT=Path(__file__).resolve().parents[3]
PARAMS=ROOT/'src/g1_navigation/config/nav2_params.yaml'
procs=[];logs=[]
def launch(command,name):
    log=open('/tmp/g1-recovery-'+name+'.log','w');logs.append(log)
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
    qos=QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL,reliability=ReliabilityPolicy.RELIABLE)
    map_pub=node.create_publisher(OccupancyGrid,'/map',qos)
    cloud_pub=node.create_publisher(PointCloud2,'/g1/mid360/points_filtered',10)
    odom_pub=node.create_publisher(Odometry,'/g1/odom',10)
    command_pub=node.create_publisher(Twist,'/cmd_vel_smoothed',10)
    mode_pub=node.create_publisher(String,'/ui/mode',10)
    outputs=[];nav_commands=[]
    node.create_subscription(Twist,'/cmd_vel_safe',lambda m:outputs.append(m.linear.x),10)
    node.create_subscription(Twist,'/cmd_vel_controller',lambda m:nav_commands.append(m.linear.x),10)
    grid=OccupancyGrid();grid.header.frame_id='map';grid.info.width=grid.info.height=160;grid.info.resolution=.05;grid.info.origin.orientation.w=1.
    grid.data=[100 if x in (0,159) or y in (0,159) else 0 for y in range(160) for x in range(160)]
    tf=StaticTransformBroadcaster(node);transforms=[]
    for parent,child,x,y in [('map','odom',0.,0.),('odom','base_footprint',2.,4.)]:
        msg=TransformStamped();msg.header.frame_id=parent;msg.child_frame_id=child;msg.header.stamp=node.get_clock().now().to_msg()
        msg.transform.translation.x=x;msg.transform.translation.y=y;msg.transform.rotation.w=1.;transforms.append(msg)
    tf.sendTransform(transforms)
    points=[[4.,3.,.8]];send_cloud=True;requested=None
    def publish():
        stamp=node.get_clock().now().to_msg()
        grid.header.stamp=stamp;map_pub.publish(grid)
        if send_cloud:cloud_pub.publish(create_cloud_xyz32(Header(stamp=stamp,frame_id='base_footprint'),points))
        odom=Odometry();odom.header.frame_id='odom';odom.child_frame_id='base_footprint';odom.header.stamp=stamp
        odom.pose.pose.position.x=2.;odom.pose.pose.position.y=4.;odom.pose.pose.orientation.w=1.;odom_pub.publish(odom)
        if requested is not None:
            cmd=Twist();cmd.linear.x=requested;command_pub.publish(cmd)
    timer=node.create_timer(.1,publish)
    launch(['ros2','launch','g1_navigation','mapping.launch.py','start_sim:=false','start_slam:=false','start_web:=false','start_nav:=true','cmd_vel_topic:=/cmd_vel_controller'],'nav')
    for name in ('planner_server','controller_server','behavior_server','bt_navigator'):active(name)
    print('PASS: actual Nav2 servers activated with new config',flush=True)
    planner=ActionClient(node,ComputePathToPose,'/compute_path_to_pose');assert planner.wait_for_server(timeout_sec=5)
    def pose(x,y):
        p=PoseStamped();p.header.frame_id='map';p.header.stamp=node.get_clock().now().to_msg();p.pose.position.x=x;p.pose.position.y=y;p.pose.orientation.w=1.;return p
    def plan():
        goal=ComputePathToPose.Goal();goal.start=pose(2.,4.);goal.goal=pose(6.,4.);goal.use_start=True;goal.planner_id='GridBased'
        handle=wait(planner.send_goal_async(goal));assert handle.accepted
        result=wait(handle.get_result_async());assert result.status==4,result.status
        return result.result.path.poses
    spin(1.5);before=plan();assert max(abs(p.pose.position.y-4.) for p in before)<.15
    points=[[1.,i*.05,.8] for i in range(-6,7)]
    spin(2.);after=plan();detour=max(abs(p.pose.position.y-4.) for p in after)
    assert detour>.40,detour
    print(f'PASS: live obstacle changes straight path into detour ({detour:.2f} m lateral)',flush=True)
    launch(['/opt/ros/humble/lib/nav2_collision_monitor/collision_monitor','--ros-args','--params-file',str(PARAMS)],'monitor')
    for transition in (1,3):
        req=ChangeState.Request();req.transition.id=transition
        assert service(ChangeState,'/collision_monitor/change_state',req).success
    launch(['ros2','run','g1_navigation','retreat_guard'],'guard')
    deadline=time.monotonic()+10
    while mode_pub.get_subscription_count()<1 and time.monotonic()<deadline:spin(.1)
    assert mode_pub.get_subscription_count()>=1
    mode_pub.publish(String(data='navigate'));spin(1.)
    points=[[.41,i*.025,.8] for i in range(-10,11)]
    requested=-.15;spin(2.);assert outputs and min(outputs[-5:])<-.14,outputs[-5:]
    print('PASS: front-wall padding allows collision-checked reverse',flush=True)
    outputs.clear();requested=.15;spin(1.);assert outputs and max(outputs[-5:])<.04,outputs[-5:]
    print('PASS: forward motion toward front wall is stopped/slowed',flush=True)
    points=[[-.31,i*.025,.8] for i in range(-10,11)];requested=-.15;spin(1.)
    assert outputs and min(outputs[-5:])>-.04,outputs[-5:]
    print('PASS: reverse toward rear wall is stopped/slowed',flush=True)
    send_cloud=False;spin(1.6);assert outputs and all(abs(x)<1e-6 for x in outputs[-5:]),outputs[-5:]
    print('PASS: stale obstacle data stops output',flush=True)
    send_cloud=True;requested=None;points=[[4.,3.,.8]];spin(.3)
    for frame in ('local','global'):
        service(ClearEntireCostmap,f'/{frame}_costmap/clear_entirely_{frame}_costmap',ClearEntireCostmap.Request())
    spin(1.5)
    nav=ActionClient(node,NavigateToPose,'/navigate_to_pose');assert nav.wait_for_server(timeout_sec=5)
    goal=NavigateToPose.Goal();goal.pose=pose(6.,4.)
    handle=wait(nav.send_goal_async(goal));assert handle.accepted
    deadline=time.monotonic()+18
    while time.monotonic()<deadline and not any(x<-.10 for x in nav_commands):spin(.1)
    assert any(x<-.10 for x in nav_commands),'No backup recovery for stationary robot'
    wait(handle.cancel_goal_async());spin(.5)
    print('PASS: stalled navigation automatically requests backward recovery using custom BT',flush=True)
finally:
    for p in procs:
        if p.poll() is None:os.killpg(p.pid,signal.SIGINT)
    for p in procs:
        try:p.wait(timeout=7)
        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
    for log in logs:log.close()
    node.destroy_node();rclpy.shutdown()
