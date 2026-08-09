# JOINT_ORDER matches qpos ordering — i.e. mj_id2name(..., mjOBJ_JOINT, i) for i in 1..29
# (skips index 0, which is floating_base_joint)
# Use this list ONLY for publishing JointState (position/velocity read from qpos/qvel).
JOINT_ORDER = [
    "left_hip_pitch_joint", "left_hip_roll_joint", "left_hip_yaw_joint",
    "left_knee_joint", "left_ankle_pitch_joint", "left_ankle_roll_joint",
    "right_hip_pitch_joint", "right_hip_roll_joint", "right_hip_yaw_joint",
    "right_knee_joint", "right_ankle_pitch_joint", "right_ankle_roll_joint",
    "waist_yaw_joint",
    "left_shoulder_pitch_joint", "left_shoulder_roll_joint", "left_shoulder_yaw_joint",
    "left_elbow_joint", "left_wrist_roll_joint",
    "right_shoulder_pitch_joint", "right_shoulder_roll_joint", "right_shoulder_yaw_joint",
    "right_elbow_joint", "right_wrist_roll_joint",
    "waist_roll_joint", "waist_pitch_joint",
    "left_wrist_pitch_joint", "left_wrist_yaw_joint",
    "right_wrist_pitch_joint", "right_wrist_yaw_joint",
]

# ACTUATOR_ORDER matches ctrl ordering — i.e. mj_id2name(..., mjOBJ_ACTUATOR, i) for i in 0..28
# Use this list ONLY for sending commands (data.ctrl[:]).
ACTUATOR_ORDER = [
    "left_hip_pitch", "left_hip_roll", "left_hip_yaw",
    "left_knee", "left_ankle_pitch", "left_ankle_roll",
    "right_hip_pitch", "right_hip_roll", "right_hip_yaw",
    "right_knee", "right_ankle_pitch", "right_ankle_roll",
    "waist_yaw", "waist_roll", "waist_pitch",
    "left_shoulder_pitch", "left_shoulder_roll", "left_shoulder_yaw",
    "left_elbow", "left_wrist_roll", "left_wrist_pitch", "left_wrist_yaw",
    "right_shoulder_pitch", "right_shoulder_roll", "right_shoulder_yaw",
    "right_elbow", "right_wrist_roll", "right_wrist_pitch", "right_wrist_yaw",
]


# Standing pose targets, keyed by JOINT name (not actuator name) — used to set
# the initial qpos so the robot starts upright instead of free-falling first.
#
# Design rationale:
#   hip_pitch + knee + ankle_pitch ≈ -0.18 + 0.35 + (-0.20) = -0.03 rad
#   This net negative angle tilts the torso slightly FORWARD, keeping the
#   center of mass over the feet and preventing backward tipping.
#   Knee bend kept mild (0.35 rad ≈ 20°) to minimise the holding torque.
STANDING_POSE_BY_JOINT = {
    "left_hip_pitch_joint": -0.1, "left_hip_roll_joint": 0.0, "left_hip_yaw_joint": 0.0,
    "left_knee_joint": 0.3, "left_ankle_pitch_joint": -0.2, "left_ankle_roll_joint": 0.0,
    "right_hip_pitch_joint": -0.1, "right_hip_roll_joint": 0.0, "right_hip_yaw_joint": 0.0,
    "right_knee_joint": 0.3, "right_ankle_pitch_joint": -0.2, "right_ankle_roll_joint": 0.0,
    "waist_yaw_joint": 0.0, "waist_roll_joint": 0.0, "waist_pitch_joint": 0.0,
    "left_shoulder_pitch_joint": 0.35, "left_shoulder_roll_joint": 0.18, "left_shoulder_yaw_joint": 0.0,
    "left_elbow_joint": 0.87, "left_wrist_roll_joint": 0.0, "left_wrist_pitch_joint": 0.0, "left_wrist_yaw_joint": 0.0,
    "right_shoulder_pitch_joint": 0.35, "right_shoulder_roll_joint": -0.18, "right_shoulder_yaw_joint": 0.0,
    "right_elbow_joint": 0.87, "right_wrist_roll_joint": 0.0, "right_wrist_pitch_joint": 0.0, "right_wrist_yaw_joint": 0.0,
}

assert len(JOINT_ORDER) == 29, "JOINT_ORDER must have 29 entries"
assert len(ACTUATOR_ORDER) == 29, "ACTUATOR_ORDER must have 29 entries"
