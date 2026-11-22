"""Generate PolyAI logo as .ico file — clean, bold, pinnable."""
from PIL import Image, ImageDraw, ImageFont
import os, math

SIZES = [16, 24, 32, 48, 64, 128, 256]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polyai.ico")

def draw_logo(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = size / 2, size / 2
    pad = size * 0.06
    r = size / 2 - pad

    # Rounded-square background
    bg_r = size * 0.18
    d.rounded_rectangle([pad, pad, size - pad, size - pad],
                        radius=bg_r, fill=(7, 14, 22, 255))

    # Subtle border glow
    d.rounded_rectangle([pad, pad, size - pad, size - pad],
                        radius=bg_r, outline=(0, 229, 160, 60), width=max(1, size // 64))

    # Main hexagon
    hex_r = r * 0.52
    hex_pts = []
    for i in range(6):
        angle = math.radians(60 * i - 90)
        hex_pts.append((cx + hex_r * math.cos(angle), cy + hex_r * math.sin(angle)))

    # Filled hexagon with gradient-like effect
    d.polygon(hex_pts, fill=(0, 229, 160, 40))

    # Hexagon outline — bold
    hex_w = max(1, int(size / 18))
    for i in range(6):
        d.line([hex_pts[i], hex_pts[(i + 1) % 6]], fill=(0, 229, 160, 255), width=hex_w)

    # Inner triangle pointing up (the "A" in AI)
    tri_r = hex_r * 0.45
    tri_pts = []
    for i in range(3):
        angle = math.radians(120 * i - 90)
        tri_pts.append((cx + tri_r * math.cos(angle), cy + tri_r * math.sin(angle)))
    tri_w = max(1, int(size / 22))
    d.polygon(tri_pts, outline=(0, 229, 160, 255), fill=(0, 229, 160, 20))
    for i in range(3):
        d.line([tri_pts[i], tri_pts[(i + 1) % 3]], fill=(0, 229, 160, 255), width=tri_w)

    # Center dot — the "eye"
    dot_r = max(1, int(size / 16))
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=(0, 229, 160, 255))

    # Connection lines from hex vertices to triangle vertices (neural net look)
    line_w = max(1, size // 40)
    for hp in hex_pts[::2]:  # every other hex vertex
        closest = min(tri_pts, key=lambda tp: (tp[0]-hp[0])**2 + (tp[1]-hp[1])**2)
        d.line([hp, closest], fill=(0, 229, 160, 80), width=line_w)

    # Vertex dots on hexagon
    vdot_r = max(1, int(size / 28))
    for pt in hex_pts:
        d.ellipse([pt[0]-vdot_r, pt[1]-vdot_r, pt[0]+vdot_r, pt[1]+vdot_r],
                  fill=(0, 229, 160, 255))

    return img

imgs = [draw_logo(s) for s in SIZES]
imgs[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES],
              append_images=imgs[:-1])
print(f"Icon saved: {OUT}")
