import math

from g1_mapping.voxel_mapper_node import _rotation_matrix
import numpy as np


def test_rotation_matrix_rotates_x_to_y_for_positive_yaw():
    half_angle = math.pi / 4.0
    rotation = _rotation_matrix(0.0, 0.0, math.sin(half_angle), math.cos(half_angle))

    transformed = rotation @ np.array([1.0, 0.0, 0.0])

    np.testing.assert_allclose(transformed, [0.0, 1.0, 0.0], atol=1e-12)
