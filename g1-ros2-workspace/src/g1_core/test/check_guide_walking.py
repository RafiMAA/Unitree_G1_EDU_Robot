"""Isolated headless ONNX + MuJoCo walking/gesture check; no live robot/viewer."""
import os
os.environ['ROS_DOMAIN_ID']='97'
os.environ['ROS_LOCALHOST_ONLY']='1'
import math
import time
import numpy as np
import mujoco
import rclpy
from geometry_msgs.msg import Twist
from sensor_msgs.msg import JointState, Imu
from std_msgs.msg import String
from g1_core.guide_behavior import CONFIG
from g1_core.state_machine_node import G1RLController
from g1_mujoco.mujoco_bridge_node import DEFAULT_POS, RIGID_KP, RIGID_KD, RL_STIFFNESS, RL_DAMPING, resolve_model_path

model=mujoco.MjModel.from_xml_path(str(resolve_model_path()))
def gains(kp,kd):
    model.actuator_gaintype[:]=0;model.actuator_biastype[:]=1
    model.actuator_gainprm[:,0]=kp;model.actuator_biasprm[:,0]=0.
    model.actuator_biasprm[:,1]=-np.array(kp);model.actuator_biasprm[:,2]=-np.array(kd)
    model.actuator_ctrllimited[:]=0

gains(RIGID_KP,RIGID_KD)
data=mujoco.MjData(model)
data.qpos[:3]=[0.,0.,.783];data.qpos[3:7]=[1.,0.,0.,0.]
joints=model.actuator_trnid[:,0];qpos=model.jnt_qposadr[joints];dof=model.jnt_dofadr[joints]
data.qpos[qpos]=DEFAULT_POS;data.ctrl[:]=DEFAULT_POS
mujoco.mj_forward(model,data)
for _ in range(int(2./model.opt.timestep)):mujoco.mj_step(model,data)
start=data.qpos[:2].copy()
rclpy.init();controller=G1RLController()
class Sink:
    active=False
    def publish(self,msg):
        if not self.active:gains(RL_STIFFNESS,RL_DAMPING);self.active=True
        data.ctrl[:]=msg.data
controller.cmd_pub=Sink()
quat=model.sensor_adr[mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_SENSOR,'imu_quat')]
gyro=model.sensor_adr[mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_SENSOR,'imu_gyro')]
max_tilt,min_height=0.,1.
names=['greet','follow_me','point','idle-look-around'];next_gesture=0
try:
    for tick in range(750):
        started=time.monotonic()
        state=JointState();state.position=data.qpos[qpos].tolist();state.velocity=data.qvel[dof].tolist();controller.on_joint_state(state)
        imu=Imu();w,x,y,z=data.sensordata[quat:quat+4];imu.orientation.w=w;imu.orientation.x=x;imu.orientation.y=y;imu.orientation.z=z
        imu.angular_velocity.x,imu.angular_velocity.y,imu.angular_velocity.z=data.sensordata[gyro:gyro+3]
        controller.on_imu(imu)
        velocity=Twist();velocity.linear.x=CONFIG['walking']['linear'];velocity.angular.z=.15;controller.on_cmd_vel(velocity)
        if tick in (0,200,400,600):
            controller.on_gesture_command(String(data=names[next_gesture]));next_gesture+=1
        controller.control_tick()
        for _ in range(round(.02/model.opt.timestep)):mujoco.mj_step(model,data)
        w,x,y,z=data.qpos[3:7]
        max_tilt=max(max_tilt,math.acos(min(1.,max(-1.,1.-2*(x*x+y*y)))))
        min_height=min(min_height,data.qpos[2])
        assert data.qpos[2]>.55 and max_tilt<.5, ('Unstable walking',tick,data.qpos[:3],max_tilt)
        time.sleep(max(0.,.02-(time.monotonic()-started)))
    distance=float(np.linalg.norm(data.qpos[:2]-start))
    assert distance>.3, ('Robot did not walk',distance)
    print(f'PASS ONNX walking with all gestures: displacement {distance:.2f} m, minimum base height {min_height:.3f} m, peak tilt {math.degrees(max_tilt):.2f} deg')
finally:
    controller.destroy_node();rclpy.shutdown()
