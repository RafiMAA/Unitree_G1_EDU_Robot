export function initialPoseMessage(world, yaw) {
  const covariance = Array(36).fill(0);
  covariance[0] = 0.25;
  covariance[7] = 0.25;
  covariance[35] = (Math.PI / 12) ** 2;
  return {
    // Zero stamp asks AMCL to use the latest transform (works with simulation too).
    header: { stamp: { sec: 0, nanosec: 0 }, frame_id: "map" },
    pose: {
      pose: { position: { x: world.x, y: world.y, z: 0 },
        orientation: { x: 0, y: 0, z: Math.sin(yaw / 2), w: Math.cos(yaw / 2) } },
      covariance,
    },
  };
}

export function arrowPose(start, end, view, size) {
  const x = view.x + (start.x - size.width / 2) / view.scale;
  const y = view.y - (start.y - size.height / 2) / view.scale;
  if (Math.hypot(end.x - start.x, end.y - start.y) < 8) return null;
  return { world: { x, y }, yaw: Math.atan2(start.y - end.y, end.x - start.x) };
}
