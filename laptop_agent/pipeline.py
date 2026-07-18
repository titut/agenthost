#!/usr/bin/env python3
"""
End-to-end pipeline:

1. Load screenshot.png
2. Resize it to 1024x1024 for Qwen3-VL
3. Send it to Qwen3-VL with a prompt asking for the YouTube tab bbox
4. Parse the JSON bbox_2d response
5. Convert bbox to actual screen coordinates
6. Click (or move to) the target

Usage:
    python pipeline.py
    python pipeline.py --move-only
    python pipeline.py --screenshot my_screen.png
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

from qwen_to_click_coordinates import bbox_to_click_coords
from qwen_vl_api import call_qwen_vl

SCREENSHOT_PATH = "screenshot.png"
QWEN_READY_PATH = "ready_for_qwen.png"


def prepare_image(input_path: str | Path, output_path: str | Path) -> None:
    """Force-stretch the screenshot to a 1024x1024 square (no padding)."""
    img = Image.open(input_path)
    squared_img = img.resize((1024, 1024), Image.Resampling.LANCZOS)
    squared_img.save(output_path)


def extract_bbox_2d(response: dict) -> list[int, int, int, int]:
    """
    Extract the bbox_2d list from the Qwen API response.

    Expects the assistant message to contain a JSON object like:
        {"bbox_2d": [xmin, ymin, xmax, ymax]}
    """
    content = (
        response.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )

    # Try to find JSON in markdown code blocks first
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Fall back to the first {...} block in the response
        json_match = re.search(r"(\{.*?\})", content, re.DOTALL)
        if not json_match:
            raise ValueError(f"No JSON object found in response:\n{content}")
        json_str = json_match.group(1)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON from response:\n{json_str}") from exc

    bbox = data.get("bbox_2d")
    if not bbox or len(bbox) != 4:
        raise ValueError(f"Invalid or missing bbox_2d in parsed JSON:\n{data}")

    return [int(v) for v in bbox]


def build_prompt() -> str:
    """Return the prompt that asks for the YouTube tab bounding box."""
    return (
        "You are looking at the user's current screen screenshot. "
        "Locate the YouTube browser tab (or YouTube window/tab bar item). "
        "Return ONLY a single JSON object in this exact format, with no extra text:\n"
        '{"bbox_2d": [xmin, ymin, xmax, ymax]}\n'
        "The coordinates must be normalized to a 0-1000 scale relative to the image."
    )


def run_pipeline(
    screenshot_path: str | Path = SCREENSHOT_PATH,
    qwen_ready_path: str | Path = QWEN_READY_PATH,
    move_only: bool = False,
    screen_width: int = 2373,
    screen_height: int = 1335,
    api_key: str | None = None,
) -> None:
    """Run the full screenshot → Qwen → click pipeline."""
    screenshot_path = Path(screenshot_path)
    if not screenshot_path.exists():
        raise FileNotFoundError(f"Screenshot not found: {screenshot_path}")

    print(f"[1/5] Preparing {screenshot_path} for Qwen3-VL...")
    prepare_image(screenshot_path, qwen_ready_path)

    print(f"[2/5] Sending image to Qwen3-VL...")
    response = call_qwen_vl(
        image_path=qwen_ready_path,
        prompt=build_prompt(),
        api_key=api_key,
    )

    print(f"[3/5] Parsing bbox_2d from response...")
    bbox_2d = extract_bbox_2d(response)
    print(f"       bbox_2d = {bbox_2d}")

    print(f"[4/5] Converting to screen coordinates...")
    click_x, click_y = bbox_to_click_coords(
        bbox_2d,
        screen_width=screen_width,
        screen_height=screen_height,
    )
    print(f"       screen coords = ({click_x}, {click_y})")

    print(f"[5/5] Executing action...")
    action_flag = "--move-only" if move_only else ""
    script_dir = Path(__file__).parent
    cmd = [
        sys.executable,
        str(script_dir / "click_pixel.py"),
        str(bbox_2d[0]),
        str(bbox_2d[1]),
        str(bbox_2d[2]),
        str(bbox_2d[3]),
        "--screen-width",
        str(screen_width),
        "--screen-height",
        str(screen_height),
    ]
    if move_only:
        cmd.append("--move-only")

    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Screenshot -> Qwen3-VL -> click pipeline."
    )
    parser.add_argument(
        "--screenshot",
        default=SCREENSHOT_PATH,
        help=f"Path to the screenshot file (default: {SCREENSHOT_PATH})",
    )
    parser.add_argument(
        "--move-only",
        action="store_true",
        help="Move the cursor without clicking",
    )
    parser.add_argument(
        "--screen-width",
        type=int,
        default=2373,
        help="Effective screen width in pixels (default: 2373)",
    )
    parser.add_argument(
        "--screen-height",
        type=int,
        default=1335,
        help="Effective screen height in pixels (default: 1335)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DEEPINFRA_API_KEY"),
        help="DeepInfra API key (default: DEEPINFRA_API_KEY env var)",
    )

    args = parser.parse_args()

    if not args.api_key:
        print(
            "Error: DeepInfra API key is required. Set DEEPINFRA_API_KEY or use --api-key.",
            file=sys.stderr,
        )
        return 1

    try:
        run_pipeline(
            screenshot_path=args.screenshot,
            move_only=args.move_only,
            screen_width=args.screen_width,
            screen_height=args.screen_height,
            api_key=args.api_key,
        )
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
