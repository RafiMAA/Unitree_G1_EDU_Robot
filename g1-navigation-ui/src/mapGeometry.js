export function quaternionYaw(q = {}) {
  return Math.atan2(2 * ((q.w ?? 1) * (q.z ?? 0) + (q.x ?? 0) * (q.y ?? 0)),
    1 - 2 * ((q.y ?? 0) ** 2 + (q.z ?? 0) ** 2));
}

export function gridToWorld(info, x, y) {
  const yaw = quaternionYaw(info.origin.orientation);
  const c = Math.cos(yaw), s = Math.sin(yaw), r = info.resolution;
  return { x: info.origin.position.x + r * (c * x - s * y),
    y: info.origin.position.y + r * (s * x + c * y) };
}

export function worldToGrid(info, point) {
  const yaw = quaternionYaw(info.origin.orientation);
  const c = Math.cos(yaw), s = Math.sin(yaw);
  const x = point.x - info.origin.position.x, y = point.y - info.origin.position.y;
  return { x: (c * x + s * y) / info.resolution, y: (-s * x + c * y) / info.resolution };
}

export function worldToScreen(point, view, size) {
  return { x: size.width / 2 + (point.x - view.x) * view.scale,
    y: size.height / 2 - (point.y - view.y) * view.scale };
}

export function screenToWorld(point, view, size) {
  return { x: view.x + (point.x - size.width / 2) / view.scale,
    y: view.y - (point.y - size.height / 2) / view.scale };
}

export function fitMap(info, size) {
  const corners = [[0, 0], [info.width, 0], [0, info.height], [info.width, info.height]]
    .map(([x, y]) => gridToWorld(info, x, y));
  const xs = corners.map(p => p.x), ys = corners.map(p => p.y);
  const width = Math.max(...xs) - Math.min(...xs), height = Math.max(...ys) - Math.min(...ys);
  return { x: (Math.min(...xs) + Math.max(...xs)) / 2,
    y: (Math.min(...ys) + Math.max(...ys)) / 2,
    scale: Math.max(0.1, Math.min(Math.max(1, size.width - 40) / width, Math.max(1, size.height - 40) / height)) };
}

// Keep the world point under the cursor stationary while changing magnification.
export function zoomAt(view, point, factor, size) {
  const anchor = screenToWorld(point, view, size);
  const scale = Math.max(0.1, Math.min(5000, view.scale * factor));
  return { scale, x: anchor.x - (point.x - size.width / 2) / scale,
    y: anchor.y + (point.y - size.height / 2) / scale };
}

export function freeMapCell(map, world) {
  const point = worldToGrid(map.info, world);
  const x = Math.floor(point.x), y = Math.floor(point.y);
  if (x < 0 || y < 0 || x >= map.info.width || y >= map.info.height) return false;
  const value = map.data[y * map.info.width + x];
  return value != null && value >= 0 && value < 50;
}

export function insideMap(info, world) {
  const point = worldToGrid(info, world);
  return point.x >= 0 && point.y >= 0 && point.x < info.width && point.y < info.height;
}
