"""Grid A* and geometric tracking, inspired by RafiMAA's Qbot architecture.

Independent implementation: metre-based inflation, unknown-space rejection,
no diagonal corner cutting, and collision-checked shortcutting.
"""
import heapq
import math

import numpy as np
from scipy.ndimage import distance_transform_edt

PADDED_FOOTPRINT = ((.39,.33),(.39,-.33),(-.29,-.33),(-.29,.33))

def angle_error(target, current):
    return math.atan2(math.sin(target-current), math.cos(target-current))


class GridMap:
    def __init__(self, data, resolution, origin=(0., 0., 0.)):
        self.data = np.asarray(data, dtype=np.int16)
        self.resolution = float(resolution)
        self.origin = origin
        if (self.data.ndim != 2 or not self.data.size or not math.isfinite(self.resolution)
                or self.resolution <= 0 or not all(math.isfinite(v) for v in origin)):
            raise ValueError('Invalid occupancy grid')
        self.height, self.width = self.data.shape
        self.raw_blocked = (self.data < 0) | (self.data >= 50)

    def world_to_cell(self, x, y):
        ox, oy, yaw = self.origin
        dx, dy = x-ox, y-oy
        return (math.floor((math.cos(yaw)*dx+math.sin(yaw)*dy)/self.resolution),
                math.floor((-math.sin(yaw)*dx+math.cos(yaw)*dy)/self.resolution))

    def cell_to_world(self, x, y):
        ox, oy, yaw = self.origin
        dx, dy = (x+.5)*self.resolution, (y+.5)*self.resolution
        return ox+math.cos(yaw)*dx-math.sin(yaw)*dy, oy+math.sin(yaw)*dx+math.cos(yaw)*dy

    def contains(self, cell):
        return 0 <= cell[0] < self.width and 0 <= cell[1] < self.height

    def with_points(self, points):
        data = self.data.copy()
        points = np.asarray(points).reshape(-1,2)
        if len(points):
            ox,oy,yaw = self.origin
            dx,dy = points[:,0]-ox,points[:,1]-oy
            gx = np.floor((math.cos(yaw)*dx+math.sin(yaw)*dy)/self.resolution).astype(int)
            gy = np.floor((-math.sin(yaw)*dx+math.cos(yaw)*dy)/self.resolution).astype(int)
            valid = (gx>=0)&(gy>=0)&(gx<self.width)&(gy<self.height)
            data[gy[valid],gx[valid]] = 100
        return GridMap(data, self.resolution, self.origin)

    def planning_mask(self, radius):
        occupied = self.data >= 50
        if occupied.any():
            inflated = distance_transform_edt(~occupied)*self.resolution <= radius + 1e-9
            return self.raw_blocked | inflated
        return self.raw_blocked.copy()


def astar_cells(blocked, start, goal, max_expansions=250000, traversal_cost=None):
    height, width = blocked.shape
    def valid(p):
        return 0 <= p[0] < width and 0 <= p[1] < height and not blocked[p[1], p[0]]
    if not valid(start) or not valid(goal):
        return []
    def heuristic(p):
        dx, dy = abs(p[0]-goal[0]), abs(p[1]-goal[1])
        return max(dx, dy)+(math.sqrt(2)-1)*min(dx, dy)
    queue = [(heuristic(start), 0., start)]
    costs, parents = {start: 0.}, {}
    expanded = 0
    while queue and expanded < max_expansions:
        _, cost, cell = heapq.heappop(queue)
        if cost > costs[cell]:
            continue
        expanded += 1
        if cell == goal:
            result = [cell]
            while cell in parents:
                cell = parents[cell]
                result.append(cell)
            return result[::-1]
        for dx, dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            nxt = (cell[0]+dx, cell[1]+dy)
            if not valid(nxt):
                continue
            if dx and dy and (not valid((cell[0]+dx,cell[1])) or not valid((cell[0],cell[1]+dy))):
                continue
            multiplier = 1. if traversal_cost is None else traversal_cost[nxt[1],nxt[0]]
            new_cost = cost+(math.sqrt(2) if dx and dy else 1.)*multiplier
            if new_cost < costs.get(nxt, math.inf):
                costs[nxt], parents[nxt] = new_cost, cell
                heapq.heappush(queue, (new_cost+heuristic(nxt), new_cost, nxt))
    return []


def segment_clear(blocked, start, end, clearance=None, min_clearance=0.):
    # Supercover sampling checks both side cells at diagonal transitions.
    count = max(1, math.ceil(math.hypot(end[0]-start[0], end[1]-start[1])*4))
    previous = start
    for i in range(count+1):
        t = i/count
        cell = (math.floor(start[0]+.5+(end[0]-start[0])*t),
                math.floor(start[1]+.5+(end[1]-start[1])*t))
        if blocked[cell[1],cell[0]]:
            return False
        if clearance is not None and clearance[cell[1],cell[0]]+1e-9 < min_clearance:
            return False
        if cell[0] != previous[0] and cell[1] != previous[1]:
            if blocked[cell[1],previous[0]] or blocked[previous[1],cell[0]]:
                return False
        previous = cell
    return True


def plan_path(grid, start, goal, radius=.15):
    blocked = grid.planning_mask(radius)
    # Soft clearance preference centres narrow corridors without enlarging the
    # hard 15 cm buffer. The octile heuristic stays admissible (costs >= 1).
    distance = distance_transform_edt(~grid.raw_blocked)*grid.resolution
    traversal = 1.+.5*np.clip((.5-distance)/.25,0.,1.)
    cells = astar_cells(blocked, grid.world_to_cell(*start), grid.world_to_cell(*goal), traversal_cost=traversal)
    if not cells:
        return []
    # Shortcut only when the complete segment is free, unlike unconstrained averaging.
    shortened, index = [cells[0]], 0
    while index < len(cells)-1:
        end = min(index+40, len(cells)-1)
        while end > index+1:
            minimum = min(distance[y,x] for x,y in cells[index:end+1])
            if segment_clear(blocked, cells[index], cells[end], distance, minimum):
                break
            end -= 1
        shortened.append(cells[end])
        index = end
    world = [grid.cell_to_world(*cell) for cell in shortened]
    world[0], world[-1] = start, goal
    # Densify for lookahead and monotonic nearest-point tracking.
    dense = [world[0]]
    for a, b in zip(world, world[1:]):
        n = max(1, math.ceil(math.dist(a,b)/grid.resolution))
        dense.extend((a[0]+(b[0]-a[0])*i/n, a[1]+(b[1]-a[1])*i/n) for i in range(1,n+1))
    return dense


def tracking_command(pose, target, remaining, align=False, max_speed=.65, max_turn=1., lookahead=.6):
    x, y, yaw = pose
    error = angle_error(math.atan2(target[1]-y,target[0]-x), yaw)
    align = abs(error) > (.10 if align else .55)
    if align:
        return 0., math.copysign(min(max_turn,max(.12,2.*abs(error))),error), True
    speed = min(max_speed,max(.12,max_speed*remaining/.7))*max(.2,math.cos(error))
    turn = max(-max_turn,min(max_turn,2*speed*math.sin(error)/max(.15,math.dist((x,y),target))))
    return speed, turn, False


def footprint_cells(grid, pose):
    """Return exact padded-body cell overlaps, plus whether it fits inside the map."""
    x,y,yaw = pose
    ox,oy,oyaw = grid.origin
    relative = yaw-oyaw
    c,s = math.cos(relative),math.sin(relative)
    cx = ((x-ox)*math.cos(oyaw)+(y-oy)*math.sin(oyaw))+.05*c
    cy = (-(x-ox)*math.sin(oyaw)+(y-oy)*math.cos(oyaw))+.05*s
    ex,ey = .34*abs(c)+.33*abs(s),.34*abs(s)+.33*abs(c)
    inside = cx-ex >= 0 and cy-ey >= 0 and cx+ex < grid.width*grid.resolution and cy+ey < grid.height*grid.resolution
    x0,x1 = max(0,math.floor((cx-ex)/grid.resolution)),min(grid.width-1,math.floor((cx+ex)/grid.resolution))
    y0,y1 = max(0,math.floor((cy-ey)/grid.resolution)),min(grid.height-1,math.floor((cy+ey)/grid.resolution))
    ys,xs = np.mgrid[y0:y1+1,x0:x1+1]
    dx,dy = (xs+.5)*grid.resolution-cx,(ys+.5)*grid.resolution-cy
    half = grid.resolution/2
    overlap = ((np.abs(dx*c+dy*s) <= .34+half*(abs(c)+abs(s)))
               & (np.abs(-dx*s+dy*c) <= .33+half*(abs(c)+abs(s)))
               & (np.abs(dx) <= ex+half) & (np.abs(dy) <= ey+half))
    return inside, ys[overlap], xs[overlap]


def starting_footprint_holes(grid, pose):
    """Snapshot self-occlusion gaps in the body's initial turning envelope.

    SLAM can leave unknown cells where the robot started mapping. Include the
    padded footprint's rotation sweep so the first alignment turn can leave
    that gap. This fixed patch never follows the robot, never clears occupied
    cells, and never edits the saved map. Live obstacles are overlaid afterwards.
    """
    inside, _, _ = footprint_cells(grid, pose)
    centre=grid.world_to_cell(*pose[:2])
    if not inside or not grid.contains(centre) or grid.data[centre[1],centre[0]] != 0:
        return ()
    radius=max(math.hypot(x,y) for x,y in PADDED_FOOTPRINT)+grid.resolution/math.sqrt(2)
    x,y=centre;cells=math.ceil(radius/grid.resolution)+1
    result=[]
    for row in range(max(0,y-cells),min(grid.height,y+cells+1)):
        for col in range(max(0,x-cells),min(grid.width,x+cells+1)):
            point=grid.cell_to_world(col,row)
            if grid.data[row,col]<0 and math.dist(pose[:2],point)<=radius:
                result.append(point)
    return tuple(result)


def clear_starting_holes(grid, holes):
    if not holes:
        return grid
    data = grid.data.copy()
    for point in holes:
        x,y = grid.world_to_cell(*point)
        if grid.contains((x,y)) and data[y,x] < 0:
            data[y,x] = 0
    return GridMap(data,grid.resolution,grid.origin)


def body_clear(grid, pose):
    """Exact rectangle/grid-cell overlap via the separating-axis theorem."""
    inside,ys,xs = footprint_cells(grid,pose)
    return inside and not np.any(grid.raw_blocked[ys,xs])


def motion_clear(grid, pose, vx, wz, horizon=1., obstacle_points=None):
    """Check map cell areas and live obstacle points at their exact positions.

    Rasterizing a live point outside the body into a whole occupied cell can
    falsely overlap the body at t=0 and prevent every escape motion.
    """
    points = np.asarray(obstacle_points if obstacle_points is not None else []).reshape(-1,2)
    x,y,yaw = pose
    for dt in np.linspace(0.,horizon,max(2,math.ceil(horizon/.05)+1)):
        theta = yaw+wz*dt
        if abs(wz) < 1e-6:
            px,py = x+vx*math.cos(yaw)*dt,y+vx*math.sin(yaw)*dt
        else:
            px,py = x+vx/wz*(math.sin(theta)-math.sin(yaw)),y-vx/wz*(math.cos(theta)-math.cos(yaw))
        if not body_clear(grid,(px,py,theta)):
            return False
        if len(points):
            dx,dy = points[:,0]-px,points[:,1]-py
            bx = dx*math.cos(theta)+dy*math.sin(theta)
            by = -dx*math.sin(theta)+dy*math.cos(theta)
            if np.any((bx >= -.29) & (bx <= .39) & (np.abs(by) <= .33)):
                return False
    return True
