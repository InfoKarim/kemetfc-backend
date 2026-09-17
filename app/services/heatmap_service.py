"""Grid-based occupancy heat map computation from a session's player/ball
center samples. Deliberately a plain 2D histogram over normalized
image-space coordinates, computed on demand from TrackingSampleDB rows —
not stored as a rendered image, so it costs nothing until actually
viewed and the frontend can render it at any resolution/color scale."""

from __future__ import annotations

DEFAULT_GRID_SIZE = 20


def _grid_index(value: float, grid_size: int) -> int:
    index = int(value * grid_size)
    return max(0, min(grid_size - 1, index))


def build_occupancy_grid(points: list[list[float]], grid_size: int = DEFAULT_GRID_SIZE) -> list[list[int]]:
    """A grid_size x grid_size occupancy count grid from a list of
    [x, y] normalized (0-1) points. Points outside [0, 1] are clamped
    rather than dropped, since a brief tracking overshoot shouldn't
    silently disappear from the map."""
    grid = [[0 for _ in range(grid_size)] for _ in range(grid_size)]
    for point in points:
        if not point or len(point) < 2:
            continue
        x, y = point[0], point[1]
        col = _grid_index(x, grid_size)
        row = _grid_index(y, grid_size)
        grid[row][col] += 1
    return grid


def build_heatmap_payload(samples: list[dict], grid_size: int = DEFAULT_GRID_SIZE) -> dict:
    """The full heat-map response: player heat grid, ball heat grid, and
    both trajectories as ordered point lists (for drawing a path line,
    not just a density grid) plus key movement events (direction changes,
    touches) so the frontend can annotate the map, not just color it."""
    ordered = sorted(samples, key=lambda s: s["t_seconds"])

    player_points = [s["player_center"] for s in ordered if s.get("player_center")]
    ball_points = [s["ball_center"] for s in ordered if s.get("ball_center")]

    return {
        "grid_size": grid_size,
        "player_heat_grid": build_occupancy_grid(player_points, grid_size),
        "ball_heat_grid": build_occupancy_grid(ball_points, grid_size) if ball_points else None,
        "player_trajectory": [
            {"t_seconds": s["t_seconds"], "x": s["player_center"][0], "y": s["player_center"][1]}
            for s in ordered
            if s.get("player_center")
        ],
        "ball_trajectory": [
            {"t_seconds": s["t_seconds"], "x": s["ball_center"][0], "y": s["ball_center"][1]}
            for s in ordered
            if s.get("ball_center")
        ],
        "sample_count": len(ordered),
    }
