from statistics import median


def interpolate_short_gaps(
    values: list[float | None],
    max_gap: int = 2,
) -> list[float | None]:
    if max_gap < 0:
        raise ValueError("max_gap cannot be negative")

    result = list(values)
    index = 0

    while index < len(result):
        if result[index] is not None:
            index += 1
            continue

        gap_start = index

        while index < len(result) and result[index] is None:
            index += 1

        gap_end = index
        gap_length = gap_end - gap_start

        if (
            gap_length <= max_gap
            and gap_start > 0
            and gap_end < len(result)
            and result[gap_start - 1] is not None
            and result[gap_end] is not None
        ):
            before = result[gap_start - 1]
            after = result[gap_end]
            step = (after - before) / (gap_length + 1)

            for offset in range(gap_length):
                result[gap_start + offset] = before + step * (offset + 1)

    return result


def smooth_signal(
    values: list[float | None],
    window_size: int = 5,
) -> list[float | None]:
    if window_size <= 0 or window_size % 2 == 0:
        raise ValueError("window_size must be a positive odd number")

    radius = window_size // 2
    smoothed = []

    for index, value in enumerate(values):
        if value is None:
            smoothed.append(None)
            continue

        window = [
            item
            for item in values[
                max(0, index - radius): index + radius + 1
            ]
            if item is not None
        ]
        smoothed.append(median(window))

    return smoothed
