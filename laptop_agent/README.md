# Laptop Agent

A tiny CLI tool that clicks the mouse at a given pixel coordinate.

## Install

```bash
cd laptop_agent
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Usage

```bash
./venv/bin/python click_pixel.py 500 300
./venv/bin/python click_pixel.py 500 300 --button right
./venv/bin/python click_pixel.py 500 300 --clicks 2 --duration 0.3
```

## Safety

PyAutoGUI's fail-safe is enabled: move the cursor to any corner of the screen to abort.
