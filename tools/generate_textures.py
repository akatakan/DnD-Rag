"""Generate the seamless material textures each table theme sits on.

The backdrop's geometry (lattice, flagstone courses, scales) is drawn in CSS,
where it stays crisp at any zoom. What CSS cannot give is *material*: the tooth
of vellum, the grain of cut stone, the bloom of lichen, the hammer marks in
brass. That is what this script produces.

Every texture is a greyscale luminance map centred on mid-grey and composited
with `mix-blend-mode: overlay`, so the theme's own palette still decides the
colour and the texture only decides how the surface catches light. Changing a
palette therefore never requires regenerating anything here.

Seamlessness is structural rather than fixed up afterwards: noise is built by
zeroing out-of-band frequencies of a periodic FFT, so every octave already
wraps at the tile edge. Output is deterministic: the same seed gives the same texture.

    uv run python tools/generate_textures.py

Writes web/public/textures/*.webp.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

SIZE = 512
OUTPUT = Path(__file__).resolve().parents[1] / "web" / "public" / "textures"


def band_noise(
    rng: np.random.Generator,
    size: int,
    frequency: float,
    anisotropy: float = 1.0,
) -> np.ndarray:
    """One octave of periodic band-limited noise.

    White noise is transformed, every frequency outside a soft-edged disc of
    radius `frequency` is discarded, and the result is transformed back. Because
    the DFT of a finite grid is periodic, the tile wraps exactly.

    `anisotropy` above 1 squashes the disc horizontally, which stretches the
    resulting features sideways — that is what makes a fibre rather than a blob.
    """
    spectrum = np.fft.fft2(rng.normal(size=(size, size)))
    axis = np.fft.fftfreq(size) * size
    fy, fx = np.meshgrid(axis, axis, indexing="ij")
    radius = np.hypot(fx / anisotropy, fy)
    # A hard cut-off rings; a Gaussian roll-off keeps the octave clean.
    spectrum *= np.exp(-((radius / max(frequency, 1e-6)) ** 2))
    field = np.real(np.fft.ifft2(spectrum))
    deviation = field.std()
    return field / deviation if deviation else field


def fbm(
    rng: np.random.Generator,
    size: int,
    octaves: int,
    base: float,
    gain: float = 0.55,
    anisotropy: float = 1.0,
) -> np.ndarray:
    """Fractal noise: octaves of band noise at halving amplitude."""
    total = np.zeros((size, size))
    amplitude = 1.0
    for octave in range(octaves):
        total += amplitude * band_noise(rng, size, base * 2**octave, anisotropy)
        amplitude *= gain
    return total / np.abs(total).max()


def emboss(height: np.ndarray) -> np.ndarray:
    """Light a height field from the upper left.

    np.roll wraps, so the lighting stays seamless along with everything else.
    """
    dx = np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)
    dy = np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)
    relief = dx + dy
    peak = np.abs(relief).max()
    return relief / peak if peak else relief


def scale_field(size: int) -> np.ndarray:
    """A height field of overlapping scales, tiling on both axes.

    Each scale is a dome of radius `radius`; rows are offset by half a scale.
    Distance is measured with wraparound so the pattern joins across the edge.
    """
    radius = size / 8
    ys, xs = np.mgrid[0:size, 0:size].astype(float)
    height = np.full((size, size), -1.0)
    row_height = radius * 1.15
    for row in range(int(size / row_height) + 2):
        centre_y = row * row_height
        offset = (row % 2) * radius
        for column in range(int(size / (2 * radius)) + 2):
            centre_x = column * 2 * radius + offset
            dx = np.abs(xs - centre_x)
            dy = np.abs(ys - centre_y)
            dx = np.minimum(dx, size - dx)
            dy = np.minimum(dy, size - dy)
            inside = 1.0 - ((dx / radius) ** 2 + (dy / (radius * 1.25)) ** 2)
            dome = np.sqrt(np.maximum(inside, 0.0))
            height = np.maximum(height, np.where(inside > 0, dome, -1.0))
    return height


# WebP is lossy here, so a committed texture never decodes back to the exact
# bytes it was written from. Drift is measured instead, at a threshold well
# under the codec's own error but far under any real change to the generator.
DRIFT_TOLERANCE = 2.0


def drift(path: Path, image: Image.Image) -> float:
    """Mean absolute difference, in levels, between a stored texture and a fresh one."""
    stored = np.asarray(Image.open(path).convert("L"), dtype=float)
    fresh = np.asarray(image, dtype=float)
    if stored.shape != fresh.shape:
        return float("inf")
    return float(np.abs(stored - fresh).mean())


def to_image(field: np.ndarray, amplitude: float) -> Image.Image:
    """Centre a signed field on mid-grey at the given amplitude."""
    scaled = 128.0 + np.clip(field, -1.0, 1.0) * amplitude * 127.0
    return Image.fromarray(np.clip(scaled, 0, 255).astype(np.uint8), mode="L")


def vellum(rng: np.random.Generator) -> np.ndarray:
    """Laid paper: long horizontal fibres over a slow cloudy mottle."""
    fibre = fbm(rng, SIZE, octaves=4, base=9, anisotropy=26)
    tooth = fbm(rng, SIZE, octaves=5, base=34)
    stain = fbm(rng, SIZE, octaves=2, base=2.2)
    return 0.52 * fibre + 0.33 * tooth + 0.5 * stain


def stone(rng: np.random.Generator) -> np.ndarray:
    """Cut stone: coarse grain, lit so the surface reads as chiselled."""
    grain = fbm(rng, SIZE, octaves=5, base=6)
    speckle = fbm(rng, SIZE, octaves=3, base=42)
    return 0.85 * emboss(grain) + 0.45 * speckle + 0.35 * grain


def lichen(rng: np.random.Generator) -> np.ndarray:
    """Moss on bark: soft blooms with a fine crust between them."""
    bloom = fbm(rng, SIZE, octaves=3, base=3.4)
    # Squaring pushes the field towards its extremes, which separates the
    # blooms into patches instead of leaving one continuous cloud.
    patches = np.sign(bloom) * bloom**2
    crust = fbm(rng, SIZE, octaves=4, base=28)
    return 0.9 * patches + 0.35 * crust


def brass(rng: np.random.Generator) -> np.ndarray:
    """Hammered scales: a scale relief, dented by noise and lit from above."""
    dents = fbm(rng, SIZE, octaves=4, base=14)
    scales = scale_field(SIZE) + 0.22 * dents
    return 0.75 * emboss(scales) + 0.4 * dents


TEXTURES = (
    # name, builder, amplitude, seed
    ("vellum-fibre", vellum, 0.60, 20260908),
    ("ebon-stone", stone, 0.62, 20260909),
    ("feywild-lichen", lichen, 0.55, 20260910),
    ("hoard-brass", brass, 0.58, 20260911),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail instead of writing if a texture is missing or out of date.",
    )
    arguments = parser.parse_args()

    OUTPUT.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for name, build, amplitude, seed in TEXTURES:
        image = to_image(build(np.random.default_rng(seed)), amplitude)
        path = OUTPUT / f"{name}.webp"
        if arguments.check:
            if not path.exists() or drift(path, image) > DRIFT_TOLERANCE:
                stale.append(name)
            continue
        image.save(path, format="WEBP", quality=88, method=6)
        print(f"{path.relative_to(OUTPUT.parents[2])}  {path.stat().st_size / 1024:.0f} KB")

    if stale:
        raise SystemExit(
            "Textures are out of date; run tools/generate_textures.py: "
            + ", ".join(stale)
        )


if __name__ == "__main__":
    main()
