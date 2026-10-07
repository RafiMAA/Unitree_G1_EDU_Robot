"""Validate saved spoken destinations against the current occupancy grid."""
import math


def goal_is_free(grid, label):
    info = grid.info
    if info.resolution <= 0:
        return False
    orientation = info.origin.orientation
    yaw = math.atan2(2 * (orientation.w * orientation.z + orientation.x * orientation.y), 1 - 2 * (orientation.y ** 2 + orientation.z ** 2))
    dx, dy = label['x'] - info.origin.position.x, label['y'] - info.origin.position.y
    x = math.floor((math.cos(yaw) * dx + math.sin(yaw) * dy) / info.resolution)
    y = math.floor((-math.sin(yaw) * dx + math.cos(yaw) * dy) / info.resolution)
    return 0 <= x < info.width and 0 <= y < info.height and 0 <= grid.data[y * info.width + x] < 50
