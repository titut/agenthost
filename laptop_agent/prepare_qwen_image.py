from PIL import Image

# 1. Load your screenshot (any resolution; it will be stretched to a square)
img = Image.open("screenshot.png")

# 2. Force-stretch it to a 1024x1024 square (No padding!)
squared_img = img.resize((1024, 1024), Image.Resampling.LANCZOS)
squared_img.save("ready_for_qwen.png")
