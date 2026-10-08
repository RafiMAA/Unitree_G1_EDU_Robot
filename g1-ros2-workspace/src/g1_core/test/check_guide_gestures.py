"""Headless MuJoCo standing gesture check; no ROS node, viewer or real robot."""
import math
import numpy as np
import mujoco
from g1_core.guide_behavior import CONFIG, GesturePlayer
from g1_mujoco.mujoco_bridge_node import DEFAULT_POS, RIGID_KP, RIGID_KD, resolve_model_path

for name in CONFIG['gestures']:
    model = mujoco.MjModel.from_xml_path(str(resolve_model_path()))
    model.actuator_gaintype[:] = 0
    model.actuator_biastype[:] = 1
    model.actuator_gainprm[:,0] = RIGID_KP
    model.actuator_biasprm[:,0] = 0.
    model.actuator_biasprm[:,1] = -np.array(RIGID_KP)
    model.actuator_biasprm[:,2] = -np.array(RIGID_KD)
    model.actuator_ctrllimited[:] = 0
    data = mujoco.MjData(model)
    data.qpos[:3] = [0.,0.,.783];data.qpos[3:7] = [1.,0.,0.,0.]
    joints = model.actuator_trnid[:,0]
    data.qpos[model.jnt_qposadr[joints]] = DEFAULT_POS
    data.ctrl[:] = DEFAULT_POS
    mujoco.mj_forward(model, data)
    gesture = GesturePlayer(); gesture.play_gesture(name, now=0.)
    max_tilt, min_height, max_arm = 0., 1., 0.
    for step in range(int(6./model.opt.timestep)):
        now = step*model.opt.timestep
        data.ctrl[:] = gesture.apply(np.array(DEFAULT_POS, dtype=float), now=now)
        mujoco.mj_step(model, data)
        w,x,y,z = data.qpos[3:7]
        tilt = math.acos(min(1., max(-1., 1.-2.*(x*x+y*y))))
        max_tilt=max(max_tilt, tilt); min_height=min(min_height, data.qpos[2])
        max_arm=max(max_arm, abs(data.qpos[model.jnt_qposadr[joints[22]]]-DEFAULT_POS[22]))
    assert min_height > .65 and max_tilt < .20, (name, min_height, max_tilt)
    assert np.allclose(data.ctrl, DEFAULT_POS), 'Gesture did not return to stance'
    print(f'PASS {name}: minimum base height {min_height:.3f} m, peak tilt {math.degrees(max_tilt):.2f} deg, arm motion {max_arm:.3f} rad')
