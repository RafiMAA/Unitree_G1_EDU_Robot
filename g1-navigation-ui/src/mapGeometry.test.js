import test from "node:test";
import assert from "node:assert/strict";
import { fitMap, freeMapCell, gridToWorld, insideMap, screenToWorld, worldToGrid, worldToScreen, zoomAt } from "./mapGeometry.js";

const info = { width: 100, height: 80, resolution: 0.1,
  origin: { position: { x: -5, y: -4 }, orientation: { z: 0, w: 1 } } };
const size = { width: 800, height: 600 };
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test("zoom preserves the world point under the cursor, including at limits", () => {
  let view = fitMap(info, size);
  const cursor = { x: 217, y: 490 };
  const world = screenToWorld(cursor, view, size);
  for (const factor of [2, 0.5, 1e9, 1e-12]) {
    view = zoomAt(view, cursor, factor, size);
    const after = screenToWorld(cursor, view, size);
    close(world.x, after.x); close(world.y, after.y);
    assert.ok(view.scale >= 0.1 && view.scale <= 5000);
  }
});

test("goal coordinates round-trip after zoom, pan, and viewport resize", () => {
  const view = { x: 2.3, y: -1.5, scale: 140 };
  const goal = { x: 3.25, y: -0.75 };
  for (const bounds of [size, { width: 380, height: 420 }]) {
    const recovered = screenToWorld(worldToScreen(goal, view, bounds), view, bounds);
    close(recovered.x, goal.x); close(recovered.y, goal.y);
  }
});

test("rotated map origins are respected for drawing and picking", () => {
  const rotated = { ...info, origin: { ...info.origin, orientation: { z: Math.sin(Math.PI / 4), w: Math.cos(Math.PI / 4) } } };
  const world = gridToWorld(rotated, 30, 20);
  close(world.x, -7); close(world.y, -1);
  const grid = worldToGrid(rotated, world);
  close(grid.x, 30); close(grid.y, 20);
  const fit = fitMap(rotated, size);
  for (const [x, y] of [[0, 0], [100, 0], [0, 80], [100, 80]]) {
    const point = worldToScreen(gridToWorld(rotated, x, y), fit, size);
    assert.ok(point.x >= 19 && point.x <= size.width - 19);
    assert.ok(point.y >= 19 && point.y <= size.height - 19);
  }
});

test("outside clicks cannot wrap into a free cell in an adjacent row", () => {
  const map = { info, data: new Array(8000).fill(0) };
  assert.equal(freeMapCell(map, gridToWorld(info, 100.5, 10.5)), false);
  assert.equal(freeMapCell(map, gridToWorld(info, -0.5, 10.5)), false);
  map.data[10 * 100 + 20] = 100;
  map.data[10 * 100 + 21] = -1;
  assert.equal(freeMapCell(map, gridToWorld(info, 20.5, 10.5)), false);
  assert.equal(freeMapCell(map, gridToWorld(info, 21.5, 10.5)), false);
  assert.equal(freeMapCell(map, gridToWorld(info, 22.5, 10.5)), true);
});

test("expanding SLAM bounds leaves world markers unchanged for a fixed camera", () => {
  const camera = fitMap(info, size), robot = { x: 1.2, y: 0.8 };
  const expanded = { ...info, width: 200, origin: { ...info.origin, position: { x: -10, y: -4 } } };
  const before = worldToScreen(gridToWorld(info, ...Object.values(worldToGrid(info, robot))), camera, size);
  const after = worldToScreen(gridToWorld(expanded, ...Object.values(worldToGrid(expanded, robot))), camera, size);
  close(before.x, after.x); close(before.y, after.y);
});

test("robot outside the last published grid becomes inside when SLAM expands it", () => {
  const robot = { x: 1, y: 6 };
  assert.equal(insideMap(info, robot), false);
  const expanded = { ...info, height: 120 };
  assert.equal(insideMap(expanded, robot), true);
  // Being inside the grid does not imply that a cell is observed or navigable.
  assert.equal(freeMapCell({ info: expanded, data: new Array(12000).fill(-1) }, robot), false);
});
