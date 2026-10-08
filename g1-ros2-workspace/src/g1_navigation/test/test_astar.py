import math

import numpy as np

from g1_navigation.astar import GridMap, astar_cells, motion_clear, plan_path, segment_clear, tracking_command


def test_astar_detours_and_does_not_cut_diagonal_corners():
    blocked = np.zeros((10,10),dtype=bool)
    blocked[2:9,5] = True
    cells = astar_cells(blocked,(2,5),(8,5))
    assert cells[0] == (2,5) and cells[-1] == (8,5)
    assert any(y < 2 or y == 9 for _,y in cells)
    assert all(segment_clear(blocked,a,b) for a,b in zip(cells,cells[1:]))
    assert not astar_cells(np.array([[False,True],[True,False]]),(0,0),(1,1))


def test_inflation_is_fifteen_centimetres_at_multiple_resolutions():
    for resolution in (.05,.025):
        data = np.zeros((80,80),dtype=int);data[40,40]=100
        grid = GridMap(data,resolution)
        mask = grid.planning_mask(.15)
        assert mask[40,40+round(.15/resolution)]
        assert not mask[40,41+round(.15/resolution)]
        assert not mask[40+round(.15/resolution),40+round(.15/resolution)]


def test_unknown_blocked_goal_and_unreachable_goal_have_no_path():
    data = np.zeros((20,20),dtype=int);data[:,10]=-1
    grid = GridMap(data,.1)
    assert not plan_path(grid,(.5,.5),(1.5,.5))
    assert not plan_path(grid,(.5,.5),(1.05,.5))
    assert not plan_path(grid,(.5,.5),(-.01,.5))


def test_rotated_map_origin_and_negative_coordinates():
    grid = GridMap(np.zeros((10,10)),.1,(3.,-2.,math.pi/2))
    for cell in ((0,0),(3,7),(9,9)):
        assert grid.world_to_cell(*grid.cell_to_world(*cell)) == cell
    assert GridMap(np.zeros((10,10)),.1).world_to_cell(-.01,0.) == (-1,0)


def test_shortcuts_keep_the_inflated_path_clear():
    data = np.zeros((80,80));data[20:60,40]=100
    grid = GridMap(data,.05)
    for radius in (.15,.25):
        path = plan_path(grid,(1.,2.),(3.,2.),radius)
        assert path and path[0] == (1.,2.) and path[-1] == (3.,2.)
        blocked = grid.planning_mask(radius)
        for a,b in zip(path,path[1:]):
            # Check the actual metre-based trajectory. Snapping each dense
            # endpoint to its cell centre can invent a different diagonal.
            count=max(1,math.ceil(math.dist(a,b)/grid.resolution*16))
            for fraction in np.linspace(0.,1.,count+1):
                x,y=grid.world_to_cell(a[0]+fraction*(b[0]-a[0]),a[1]+fraction*(b[1]-a[1]))
                assert not blocked[y,x]


def test_full_body_allows_narrow_corridor_but_stops_front_wall():
    data = np.zeros((160,160),dtype=int)
    data[72,:] = 100;data[88,:] = 100
    grid = GridMap(data,.05)
    assert motion_clear(grid,(2.,4.,0.),.65,0.)
    assert not motion_clear(grid,(2.,3.7,0.),.65,0.)
    wall = grid.with_points([(2.6,4.)])
    assert not motion_clear(wall,(2.,4.,0.),.65,0.)
    assert motion_clear(wall,(2.,4.,0.),-.15,0.,.5)


def test_turn_before_walking_and_no_reverse_normal_tracking():
    vx,wz,align = tracking_command((0.,0.,0.),(0.,1.),2.,True)
    assert vx == 0. and wz > 0. and align
    vx,wz,align = tracking_command((0.,0.,math.pi/2),(0.,1.),2.,True)
    assert vx == .65 and abs(wz) < 1e-6 and not align
    vx,wz,align = tracking_command((0.,0.,0.),(-1.,0.),2.,False)
    assert vx == 0. and align


def test_live_point_outside_body_does_not_block_departure_by_cell_rounding():
    grid = GridMap(np.zeros((100,100)), .05)
    pose = (2.025, 2.025, 0.)
    points = [(2.025-.295, 2.025-.26), (2.025+.15, 2.025-.335)]
    assert not motion_clear(grid.with_points(points), pose, .15, 0., .5)
    assert motion_clear(grid, pose, .15, 0., .5, obstacle_points=points)
    # The same near-body points still prevent entering them or turning into them.
    assert not motion_clear(grid, pose, -.15, 0., .5, obstacle_points=points)
    assert not motion_clear(grid, pose, 0., -.8, .5, obstacle_points=points)
    assert not motion_clear(grid, pose, .15, 0., obstacle_points=[(2.1, 2.)])
    assert not motion_clear(grid, pose, .65, 0., obstacle_points=[(2.7, 2.025)])


def test_exact_live_points_keep_saved_walls_and_unknown_space_blocked():
    data = np.zeros((100,100));data[40,40] = 100
    grid = GridMap(data,.05)
    assert not motion_clear(grid,(2.025,2.025,0.),.15,0.,obstacle_points=[])
    data[40,40] = -1
    assert not motion_clear(GridMap(data,.05),(2.025,2.025,0.),.15,0.,obstacle_points=[])


def test_departure_from_saved_map_self_occlusion_keeps_unknown_ahead_blocked():
    from g1_navigation.astar import body_clear, starting_footprint_holes, clear_starting_holes
    data=np.zeros((100,100),dtype=int)
    data[37:44,35:38]=-1  # A hole under the starting body's rear-left corner.
    data[40,70]=-1       # Unknown away from the starting footprint stays blocked.
    grid=GridMap(data,.05)
    pose=(2.,2.,0.)
    assert not body_clear(grid,pose)
    holes=starting_footprint_holes(grid,pose)
    assert holes
    cleared=clear_starting_holes(grid,holes)
    assert motion_clear(cleared,pose,.5,0.)
    assert cleared.data[40,70]==-1
    assert grid.data[40,36]==-1  # Saved map is never modified.
    assert not body_clear(cleared,(3.5,2.,0.))
    assert not body_clear(cleared.with_points([(1.85,2.)]),pose)
    occupied=grid.data.copy();occupied[40,36]=100
    assert not body_clear(clear_starting_holes(GridMap(occupied,.05),holes),pose)


def test_rotated_starting_patch_cannot_clear_unknown_under_a_later_pose():
    from g1_navigation.astar import starting_footprint_holes, clear_starting_holes, body_clear
    data=np.full((100,100),-1);data[40,40]=0
    grid=GridMap(data,.05,(5.,-2.,math.pi/2))
    point=grid.cell_to_world(40,40);pose=(*point,math.pi/2)
    holes=starting_footprint_holes(grid,pose)
    clear=clear_starting_holes(grid,holes)
    assert body_clear(clear,pose)
    assert motion_clear(clear,pose,0.,.8)
    assert not body_clear(clear,(point[0],point[1]+.5,pose[2]))
    assert not starting_footprint_holes(grid,(*grid.cell_to_world(0,0),0.))
