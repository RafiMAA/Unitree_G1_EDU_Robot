import test from "node:test";
import assert from "node:assert/strict";
import { arrowPose, initialPoseMessage } from "./initialPose.js";

test("pose arrow uses world position after zoom/pan and north-positive heading", () => {
  const pose = arrowPose({ x: 150, y: 50 }, { x: 150, y: 10 }, { x: 4, y: -2, scale: 10 }, { width: 200, height: 100 });
  assert.deepEqual(pose.world, { x: 9, y: -2 });
  assert.equal(pose.yaw, Math.PI / 2);
  assert.equal(arrowPose({ x: 10, y: 10 }, { x: 12, y: 10 }, { x: 0, y: 0, scale: 1 }, { width: 100, height: 100 }), null);
});
test("AMCL message contains normalized quaternion, map frame and planar uncertainty", () => {
  const msg = initialPoseMessage({ x: 3, y: -4 }, Math.PI / 2);
  assert.equal(msg.header.frame_id, "map");
  assert.deepEqual(msg.header.stamp, { sec: 0, nanosec: 0 });
  assert.deepEqual(msg.pose.pose.position, { x: 3, y: -4, z: 0 });
  const q = msg.pose.pose.orientation;
  assert.ok(Math.abs(q.z ** 2 + q.w ** 2 - 1) < 1e-12);
  assert.equal(msg.pose.covariance.length, 36);
  assert.equal(msg.pose.covariance[0], 0.25);
  assert.equal(msg.pose.covariance[7], 0.25);
  assert.ok(msg.pose.covariance[35] > 0);
});
