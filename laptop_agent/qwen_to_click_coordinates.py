def bbox_to_click_coords(bbox_2d, screen_width=2373, screen_height=1335):
    """
    Convert a Qwen3-VL bbox_2d (0-1000 scale) into monitor click coordinates.

    Args:
        bbox_2d: List/tuple in the format [xmin, ymin, xmax, ymax].
        screen_width: Monitor width in pixels (default: 1920).
        screen_height: Monitor height in pixels (default: 1080).

    Returns:
        Tuple of (click_x, click_y) screen coordinates.
    """
    xmin, ymin, xmax, ymax = bbox_2d

    # 1. Calculate the relative center of the box (0-1000 scale)
    center_x_relative = (xmin + xmax) / 2
    center_y_relative = (ymin + ymax) / 2

    # 2. Scale it back to the original monitor dimensions
    click_x = int((center_x_relative / 1000) * screen_width)
    click_y = int((center_y_relative / 1000) * screen_height)

    return click_x, click_y


# Example usage
if __name__ == "__main__":
    example_bbox = [200, 450, 350, 510]  # [xmin, ymin, xmax, ymax]
    x, y = bbox_to_click_coords(example_bbox)
    print(f"Target Click Coordinates: X={x}, Y={y}")
    # Output: X=653, Y=641 -> You can pass this straight to ydotool/PyAutoGUI
