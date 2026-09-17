from app.services.heatmap_service import build_heatmap_payload, build_occupancy_grid


def test_occupancy_grid_counts_points_in_correct_cell():
    grid = build_occupancy_grid([[0.05, 0.05], [0.05, 0.05], [0.95, 0.95]], grid_size=10)
    assert grid[0][0] == 2
    assert grid[9][9] == 1
    assert sum(sum(row) for row in grid) == 3


def test_occupancy_grid_clamps_out_of_range_points():
    grid = build_occupancy_grid([[-0.5, 1.5]], grid_size=10)
    assert sum(sum(row) for row in grid) == 1


def test_build_heatmap_payload_includes_both_trajectories_and_grids():
    samples = [
        {"t_seconds": 0.0, "player_center": [0.1, 0.1], "ball_center": [0.2, 0.2]},
        {"t_seconds": 1.0, "player_center": [0.3, 0.3], "ball_center": None},
    ]
    payload = build_heatmap_payload(samples, grid_size=5)
    assert payload["sample_count"] == 2
    assert len(payload["player_trajectory"]) == 2
    assert len(payload["ball_trajectory"]) == 1
    assert payload["player_heat_grid"] is not None
    assert payload["ball_heat_grid"] is not None


def test_build_heatmap_payload_ball_grid_none_when_no_ball_ever_seen():
    samples = [{"t_seconds": 0.0, "player_center": [0.1, 0.1], "ball_center": None}]
    payload = build_heatmap_payload(samples)
    assert payload["ball_heat_grid"] is None
