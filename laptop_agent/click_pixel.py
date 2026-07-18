#!/usr/bin/env python3
"""
Click the mouse at the screen coordinates derived from a Qwen3-VL bbox_2d.

The bbox is expected in [xmin, ymin, xmax, ymax] format on a 0-1000 scale.
It is converted to actual screen coordinates using the qwen_to_click_coordinates
helper before moving/clicking via ydotool (Wayland-compatible).

Usage:
    python click_pixel.py 200 450 350 510
    python click_pixel.py 200 450 350 510 --duration 0.5
    python click_pixel.py 200 450 350 510 --button right --clicks 2
    python click_pixel.py 200 450 350 510 --move-only
"""

import argparse
import shutil
import subprocess
import sys
import time

from qwen_to_click_coordinates import bbox_to_click_coords

# ydotool button codes: 1=left, 2=right, 3=middle
BUTTON_MAP = {
    "left": "1",
    "right": "2",
    "middle": "3",
}


def _run_ydotool(args: list[str]) -> None:
    """Run an ydotool command, raising on failure."""
    if not shutil.which("ydotool"):
        raise RuntimeError("ydotool is not installed or not in PATH")

    result = subprocess.run(
        ["ydotool", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"ydotool failed: {err}")


def move_to(x: int, y: int, duration: float = 0.0) -> None:
    """Move the cursor to (x, y) without clicking."""
    if duration > 0:
        # ydotool doesn't support move duration; do a simple stepped interpolation.
        start_x, start_y = _get_cursor_pos()
        steps = max(1, int(duration * 60))
        for i in range(1, steps + 1):
            t = i / steps
            ix = int(start_x + (x - start_x) * t)
            iy = int(start_y + (y - start_y) * t)
            _run_ydotool(["mousemove", str(ix), str(iy)])
            time.sleep(duration / steps)
    else:
        _run_ydotool(["mousemove", str(x), str(y)])


def click_at(
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    duration: float = 0.0,
) -> None:
    """Click the specified mouse button at (x, y)."""
    move_to(x, y, duration=duration)
    ydotool_button = BUTTON_MAP[button]
    for _ in range(clicks):
        _run_ydotool(["click", ydotool_button])


def _get_cursor_pos() -> tuple[int, int]:
    """Best-effort current cursor position (fallback to 0,0)."""
    try:
        # Try reading from the evdev-based position if available.
        result = subprocess.run(
            ["ydotool", "mousemove", "--", "0", "0"],
            capture_output=True,
            text=True,
            check=False,
        )
        # ydotool doesn't expose a query; fall back to a safe default.
    except Exception:
        pass
    return 0, 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Click the mouse at the coordinate derived from a Qwen3-VL bbox."
    )
    parser.add_argument("xmin", type=int, help="BBox xmin on 0-1000 scale")
    parser.add_argument("ymin", type=int, help="BBox ymin on 0-1000 scale")
    parser.add_argument("xmax", type=int, help="BBox xmax on 0-1000 scale")
    parser.add_argument("ymax", type=int, help="BBox ymax on 0-1000 scale")
    parser.add_argument(
        "--screen-width",
        type=int,
        default=2373,
        help="Monitor width in pixels (default: 2373)",
    )
    parser.add_argument(
        "--screen-height",
        type=int,
        default=1335,
        help="Monitor height in pixels (default: 1335)",
    )
    parser.add_argument(
        "--button",
        choices=["left", "right", "middle"],
        default="left",
        help="Mouse button to click (default: left)",
    )
    parser.add_argument(
        "--clicks",
        type=int,
        default=1,
        help="Number of clicks (default: 1)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Seconds to spend moving the cursor (default: 0, instant)",
    )
    parser.add_argument(
        "--move-only",
        action="store_true",
        help="Move the cursor to the target without clicking",
    )

    args = parser.parse_args()

    bbox_2d = [args.xmin, args.ymin, args.xmax, args.ymax]
    click_x, click_y = bbox_to_click_coords(
        bbox_2d,
        screen_width=args.screen_width,
        screen_height=args.screen_height,
    )

    try:
        if args.move_only:
            move_to(click_x, click_y, args.duration)
            print(f"Moved cursor to ({click_x}, {click_y}).")
        else:
            click_at(click_x, click_y, args.button, args.clicks, args.duration)
            print(
                f"Clicked {args.clicks} time(s) at ({click_x}, {click_y}) "
                f"with {args.button} button."
            )
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
