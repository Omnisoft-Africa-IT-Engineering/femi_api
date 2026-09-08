import io

import pytesseract
from PIL import Image

with open("image.png", "rb") as f:
    image_bytes = f.read()

image = Image.open(io.BytesIO(image_bytes))

for psm in [4, 6, 11, 12]:
    print(f"--- PSM {psm} ---")
    text = pytesseract.image_to_string(image, lang="fra+eng", config=f"--psm {psm}")
    print(repr(text))
    print()