"""
Call the DeepInfra OpenAI-compatible API for Qwen3-VL vision tasks.

Set your API key via the DEEPINFRA_API_KEY environment variable or pass it
as the `api_key` argument.
"""

import base64
import os
from pathlib import Path

import requests

API_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
MODEL = "Qwen/Qwen3-VL-235B-A22B-Instruct"


def encode_image(image_path: str | Path) -> str:
    """Encode an image file as a base64 data URI."""
    image_path = Path(image_path)
    with image_path.open("rb") as f:
        image_data = f.read()

    mime_type = "image/png"
    if image_path.suffix.lower() in (".jpg", ".jpeg"):
        mime_type = "image/jpeg"
    elif image_path.suffix.lower() == ".webp":
        mime_type = "image/webp"

    encoded = base64.b64encode(image_data).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def call_qwen_vl(
    image_path: str | Path,
    prompt: str | None = None,
    api_key: str | None = None,
    max_tokens: int = 4092,
    model: str = MODEL,
    api_url: str = API_URL,
) -> dict:
    """
    Send an image (and optional text prompt) to Qwen3-VL via DeepInfra.

    Args:
        image_path: Path to the image file.
        prompt: Optional text prompt. Defaults to a generic request.
        api_key: DeepInfra API key. Falls back to DEEPINFRA_API_KEY env var.
        max_tokens: Maximum tokens in the response.
        model: Model identifier to use.
        api_url: OpenAI-compatible chat completions endpoint.

    Returns:
        The parsed JSON response from the API.
    """
    api_key = api_key or os.environ.get("DEEPINFRA_API_KEY")
    if not api_key:
        raise ValueError(
            "API key is required. Set DEEPINFRA_API_KEY or pass api_key."
        )

    if prompt is None:
        prompt = (
            "Describe what you see in this image. If there are UI elements, "
            "provide their bounding boxes in [xmin, ymin, xmax, ymax] format "
            "on a 0-1000 scale."
        )

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": encode_image(image_path)}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }

    response = requests.post(
        api_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <image_path> [prompt]", file=sys.stderr)
        sys.exit(1)

    result = call_qwen_vl(sys.argv[1], prompt=sys.argv[2] if len(sys.argv) > 2 else None)
    print(result)
