"""從 static/origin.png 產出登入頁的三張底圖：原圖抹掉假卡片，再換成三種基底色。

兩件事：

1. 原圖上那張登入卡是「畫上去的」—— Email / Remember Me / Register 全是圖案的一部分。
   直接當底圖，我們真正的卡片後面會露出另一張卡片的邊框和文字。
   這張圖的景物是水平分層的（天空、山、雪地），所以每一橫排拿卡片左右兩側的顏色
   線性內插填回去，再把接縫糊掉。

2. 換色不能用 CSS 的 hue-rotate —— 那是矩陣近似，不是真的旋轉色相，
   會把藍色的樹轉成粉紅、把橘色的窗戶轉成螢光綠。這裡在 HSV 上真的轉色相，
   而且把色相的散佈壓窄（整張圖收斂到同一個色系），並且**跳過窗戶的燈光**：
   窗戶是暖光，不管基底色是什麼都該是暖的。
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).parent.parent
OUT = ROOT / "static"
SOURCE = OUT / "origin.png"

# 畫上去的那張卡片的範圍，量出來的，不是猜的
CARD = (396, 19, 786, 562)
FEATHER = 14

# 原圖的主色相（紫，HSV 的 0–255 刻度上約 178）。色相以它為軸心旋轉
BASE_HUE = 178
# 色相散佈壓縮：1.0 是原樣。開太大，藍色的樹會被甩成紅色、天空會冒出綠色。
# 0.32 的意思是整張圖收斂在目標色相正負 20 度以內，只留層次、不留別的顏色。
SPREAD = 0.32

# 目標色相與飽和度。紫色就是原圖，不動
PALETTES = {
    "purple": (BASE_HUE, 1.0),
    "amber": (14, 1.0),
    "sage": (72, 0.42),
}


def _patch(im: Image.Image) -> Image.Image:
    """把畫上去的登入卡抹掉。"""
    left, top, right, bottom = CARD
    px = im.load()
    out = im.copy()
    draw = ImageDraw.Draw(out)
    span = right - left
    for y in range(top, bottom):
        a, b = px[left - 6, y], px[right + 6, y]
        for x in range(left, right):
            t = (x - left) / span
            draw.point((x, y), tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3)))

    soft = out.filter(ImageFilter.GaussianBlur(18))
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rectangle(
        (left - FEATHER, top - FEATHER, right + FEATHER, bottom + FEATHER), fill=255
    )
    out.paste(soft, (0, 0), mask.filter(ImageFilter.GaussianBlur(FEATHER)))
    return out


def _recolour(im: Image.Image, hue: int, sat: float) -> Image.Image:
    a = np.array(im.convert("HSV")).astype(np.int16)
    h, s, v = a[..., 0], a[..., 1], a[..., 2]

    # 窗戶的燈光不跟著換色 —— 基底色是什麼，燈都該是暖的。
    # 但不能用「框一塊起來不動」：那塊牆壁會保持原色，窗戶外面就描出一個紫色方框。
    # 改成逐像素算「有多暖」（0～1），越暖越不動，邊緣自然過渡。
    warm = (
        np.clip(1 - np.minimum(np.abs(h - 28), 256 - np.abs(h - 28)) / 34, 0, 1)
        * np.clip((s - 55) / 80, 0, 1)
        * np.clip((v - 85) / 80, 0, 1)
    )

    turned = (hue + (h - BASE_HUE) * SPREAD) % 256
    # 色相是個圓。差值要繞近路，不然從 250 轉到 10 會沿著整圈走過去
    delta = (turned - h + 128) % 256 - 128
    a[..., 0] = (h + (1 - warm) * delta) % 256
    a[..., 1] = np.clip(s * (warm + (1 - warm) * sat), 0, 255)
    return Image.fromarray(a.astype(np.uint8), "HSV").convert("RGB")


def main() -> None:
    clean = _patch(Image.open(SOURCE).convert("RGB"))
    for name, (hue, sat) in PALETTES.items():
        target = OUT / f"login-bg-{name}.png"
        _recolour(clean, hue, sat).save(target, optimize=True)
        print(f"{target.name} {target.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
