"""Draw the app's logo (a red wheel) as a Windows .ico for KN5Garage.exe."""

import sys
from pathlib import Path

from PIL import Image, ImageDraw


def draw(size):
    s = size * 4  # draw large, then scale down for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    red = (255, 77, 46, 255)
    d.ellipse([s * 0.06, s * 0.06, s * 0.94, s * 0.94], fill=(16, 19, 23, 255))
    d.ellipse([s * 0.14, s * 0.14, s * 0.86, s * 0.86], outline=red, width=int(s * 0.1))
    d.ellipse([s * 0.38, s * 0.38, s * 0.62, s * 0.62], fill=red)
    w = int(s * 0.07)
    for x0, y0, x1, y1 in ((0.5, 0.1, 0.5, 0.32), (0.5, 0.68, 0.5, 0.9), (0.1, 0.5, 0.32, 0.5), (0.68, 0.5, 0.9, 0.5)):
        d.line([s * x0, s * y0, s * x1, s * y1], fill=red, width=w)
    return img.resize((size, size), Image.LANCZOS)


def main(out):
    images = [draw(n) for n in (256, 64, 48, 32, 16)]
    images[0].save(out, format="ICO", sizes=[(n, n) for n in (256, 64, 48, 32, 16)], append_images=images[1:])
    print("wrote", out)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("kn5garage.ico")))
