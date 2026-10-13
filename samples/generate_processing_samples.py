"""Generate geometric JPEG fixtures locally; these are not real headshot examples.

Usage: python samples/generate_processing_samples.py D:\\SyntheticPhotos
"""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def make_sample(kind="even-green", size=(480, 640)):
    width, height = size
    image = Image.new("RGB", size, (0, 190, 0) if kind != "no-green" else (170, 170, 170))
    if kind == "empty-green":
        return image
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((int(width * .25), int(height * .4), int(width * .75), int(height * .9)),
                           radius=max(1, width // 12), fill=(45, 60, 130))
    draw.ellipse((int(width * .35), int(height * .15), int(width * .65), int(height * .45)), fill=(185, 130, 100))
    if kind == "green-clothing":
        draw.rectangle((int(width * .4), int(height * .55), int(width * .6), int(height * .75)), fill=(0, 190, 0))
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    for kind in ("even-green", "no-green", "empty-green", "green-clothing"):
        destination = args.directory / f"synthetic-{kind}.jpg"
        with destination.open("xb") as file:
            make_sample(kind).save(file, "JPEG", quality=98, subsampling=0)
        print(destination)


if __name__ == "__main__":
    main()
