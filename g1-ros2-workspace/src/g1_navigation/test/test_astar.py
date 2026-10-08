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


def test_inflation_is_twenty_five_centimetres_at_multiple_resolutions():
    for resolution in (.05,.025):
        data = np.zeros((80,80),dtype=int);data[40,40]=100
        grid = GridMap(data,resolution)
        mask = grid.planning_mask(.25)
        assert mask[40,40+round(.25/resolution)]
        assert not mask[40,41+round(.25/resolution)]
        assert not mask[40+round(.25/resolution),40+round(.25/resolution)]


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
    path = plan_path(grid,(1.,2.),(3.,2.))
    assert path and path[0] == (1.,2.) and path[-1] == (3.,2.)
    blocked = grid.planning_mask(.25)
    assert all(segment_clear(blocked,grid.world_to_cell(*a),grid.world_to_cell(*b)) for a,b in zip(path,path[1:]))


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
