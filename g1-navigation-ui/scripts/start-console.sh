#!/usr/bin/env bash
set -e
ui_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ros_workspace="$(cd "$ui_root/../g1-ros2-workspace" && pwd)"
export PYTHONNOUSERSITE=1
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-1}"
unset PYTHONPATH
source /opt/ros/humble/setup.bash
source "$ros_workspace/install/setup.bash"
export PYTHONPATH="$ros_workspace/.venv/lib/python3.10/site-packages:${PYTHONPATH:-}"
exec /usr/bin/python3 "$ui_root/scripts/console_server.py"
