"""Renders the countdown Short: PIL frames piped into ffmpeg."""
import math
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_DISPLAY = str(ROOT / "assets/fonts/Anton-Regular.ttf")
FONT_TEXT = str(ROOT / "assets/fonts/Inter.ttf")
SR = 44100

BG_TOP = (9, 12, 22)
BG_BOT = (20, 26, 46)
WHITE = (245, 247, 252)
GREY = (140, 150, 172)
DIM = (46, 54, 78)


# ------------------------------------------------------------------ utils ----
def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ease_out(t):
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


def ease_out_back(t):
    t = min(max(t, 0.0), 1.0)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


_font_cache = {}


def font(path, size, weight=None):
    key = (path, size, weight)
    if key not in _font_cache:
        f = ImageFont.truetype(path, size)
        if weight:
            try:
                f.set_variation_by_name(weight)
            except Exception:  # noqa: BLE001
                pass
        _font_cache[key] = f
    return _font_cache[key]


def fit_font(draw, text, path, size, max_w, weight=None, min_size=28):
    while size > min_size:
        f = font(path, size, weight)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return font(path, min_size, weight)


def wrap(draw, text, f, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=f) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def split_display(display):
    """'$7.99' -> ('$', 7.99, '', 2). Used for the ticking counter."""
    m = re.search(r"\d[\d,]*(?:\.(\d+))?", display)
    if not m:
        return None
    num = float(m.group(0).replace(",", ""))
    decimals = len(m.group(1)) if m.group(1) else 0
    return display[:m.start()], num, display[m.end():], decimals, "," in m.group(0)


def format_counter(parts, frac):
    pre, num, post, dec, commas = parts
    v = num * frac
    s = f"{v:,.{dec}f}" if commas else f"{v:.{dec}f}"
    return f"{pre}{s}{post}"


# ------------------------------------------------------------- background ----
def make_background(w, h, accent):
    arr = np.zeros((h, w, 3), dtype=np.float32)
    t = np.linspace(0, 1, h)[:, None]
    for c in range(3):
        arr[:, :, c] = BG_TOP[c] * (1 - t) + BG_BOT[c] * t
    # accent glow top-centre
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.sqrt(((xx - w / 2) / (w * 0.8)) ** 2 + ((yy - 260) / (h * 0.28)) ** 2)
    glow = np.clip(1 - d, 0, 1) ** 2 * 0.22
    for c in range(3):
        arr[:, :, c] += glow * accent[c]
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    for x in range(0, w, 90):
        gd.line([(x, 0), (x, h)], fill=(255, 255, 255, 9))
    for y in range(0, h, 90):
        gd.line([(0, y), (w, y)], fill=(255, 255, 255, 9))
    return Image.alpha_composite(img.convert("RGBA"), grid).convert("RGB")


# ------------------------------------------------------------------ scene ----
class Renderer:
    def __init__(self, ranking, script, durations, cfg):
        v = cfg["video"]
        self.W, self.H, self.fps = v["width"], v["height"], v["fps"]
        self.r = ranking
        self.s = script
        self.n = len(ranking.items)
        pillar = cfg["pillars"][ranking.pillar]
        self.accent = hex_rgb(pillar["accent"])
        self.pillar_label = pillar["label"]
        self.brand = cfg["channel"]["name"].upper()
        self.bg = make_background(self.W, self.H, self.accent)
        # segments: hook, lines (#n..#1), outro
        self.texts = [script["hook"], *script["lines"], script["outro"]]
        self.starts, t = [], 0.0
        for d in durations:
            self.starts.append(t)
            t += d
        self.total = t + 0.6  # small tail
        self.durations = durations
        self.max_val = max(abs(i.value) for i in ranking.items) or 1
        self.parts = [split_display(i.display) for i in ranking.items]

    # rank r (1 = best) is revealed in segment index (n - r + 1)
    def reveal_time(self, rank):
        return self.starts[self.n - rank + 1]

    def segment_at(self, t):
        idx = 0
        for i, s in enumerate(self.starts):
            if t >= s:
                idx = i
        return idx

    def frame(self, t):
        W, H = self.W, self.H
        img = self.bg.copy()
        d = ImageDraw.Draw(img, "RGBA")
        acc = self.accent

        # header: brand pill + pillar
        f_brand = font(FONT_TEXT, 30, "Bold")
        label = f"{self.brand}  ·  {self.pillar_label}"
        tw = d.textlength(label, font=f_brand)
        d.rounded_rectangle([(W - tw) / 2 - 28, 96, (W + tw) / 2 + 28, 150],
                            radius=27, fill=(*acc, 40), outline=(*acc, 160),
                            width=2)
        d.text((W / 2, 123), label, font=f_brand, fill=WHITE, anchor="mm")

        # title pops in during the first 0.5 s
        k = ease_out_back(t / 0.5)
        title = self.r.title.upper()
        f_title = fit_font(d, title, FONT_DISPLAY, int(118 * max(k, 0.01)),
                           W - 120, min_size=10)
        d.text((W / 2, 250), title, font=f_title, fill=WHITE, anchor="mm")
        f_sub = font(FONT_TEXT, 36, "Medium")
        d.text((W / 2, 345), self.r.subtitle, font=f_sub, fill=GREY,
               anchor="mm")

        # leaderboard
        top, row_h, gap = 440, 176, 18
        x0, x1 = 60, W - 60
        for rank in range(1, self.n + 1):
            item = self.r.items[rank - 1]
            y = top + (rank - 1) * (row_h + gap)
            rt = self.reveal_time(rank)
            p = (t - rt)
            revealed = p >= 0
            active = revealed and self.segment_at(t) == self.n - rank + 1
            is_one_final = rank == 1 and revealed

            slide = ease_out(p / 0.35) if revealed else 0
            dx = int((1 - slide) * 80) if revealed else 0
            box_fill = (255, 255, 255, 14) if revealed else (255, 255, 255, 6)
            outline = (*acc, 255) if active else (255, 255, 255, 22)
            if is_one_final:
                pulse = 0.5 + 0.5 * math.sin((t - rt) * 5)
                outline = (*acc, int(160 + 95 * pulse))
            d.rounded_rectangle([x0 + dx, y, x1 + dx, y + row_h], radius=26,
                                fill=box_fill, outline=outline,
                                width=4 if (active or is_one_final) else 2)

            # rank badge
            bx, by = x0 + 30 + dx, y + row_h / 2
            badge_col = acc if revealed else DIM
            d.ellipse([bx, by - 52, bx + 104, by + 52], fill=(*badge_col, 255))
            f_rank = font(FONT_DISPLAY, 60)
            d.text((bx + 52, by + 2), f"#{rank}", font=f_rank,
                   fill=(10, 12, 20) if revealed else GREY, anchor="mm")

            tx = bx + 136
            if not revealed:
                f_q = font(FONT_DISPLAY, 64)
                d.text((tx, by), "?", font=f_q, fill=DIM, anchor="lm")
                continue

            # value counter
            cnt = ease_out(p / 0.9)
            val_txt = (format_counter(self.parts[rank - 1], cnt)
                       if self.parts[rank - 1] else item.display)
            f_val = font(FONT_DISPLAY, 64)
            d.text((x1 - 34 + dx, y + 58), val_txt, font=f_val, fill=WHITE,
                   anchor="rm")
            val_w = d.textlength(item.display, font=f_val)

            # name
            name_max = (x1 - 34 - val_w - 30) - tx
            f_name = fit_font(d, item.name, FONT_TEXT, 54, name_max, "Bold")
            a = int(255 * min(1, p / 0.25))
            d.text((tx + dx, y + 58), item.name, font=f_name,
                   fill=(*WHITE, a), anchor="lm")

            # bar
            bar_x0, bar_x1 = tx + dx, x1 - 34 + dx
            bar_y = y + 118
            d.rounded_rectangle([bar_x0, bar_y, bar_x1, bar_y + 26],
                                radius=13, fill=(255, 255, 255, 18))
            frac = abs(item.value) / self.max_val
            grow = ease_out(p / 0.8)
            bw = max(26, (bar_x1 - bar_x0) * frac * grow)
            d.rounded_rectangle([bar_x0, bar_y, bar_x0 + bw, bar_y + 26],
                                radius=13, fill=(*acc, 255))

        # caption for the current line
        seg = self.segment_at(t)
        if seg < len(self.texts):
            seg_p = t - self.starts[seg]
            a = int(255 * min(1, seg_p / 0.18))
            f_cap = font(FONT_TEXT, 50, "Bold")
            lines = wrap(d, self.texts[seg], f_cap, W - 200)[:4]
            lh = 64
            cy0 = 1500
            box_h = lh * len(lines) + 56
            d.rounded_rectangle([70, cy0, W - 70, cy0 + box_h], radius=28,
                                fill=(0, 0, 0, int(150 * a / 255)))
            for i, ln in enumerate(lines):
                d.text((W / 2, cy0 + 28 + lh * i + lh / 2), ln, font=f_cap,
                       fill=(*WHITE, a), anchor="mm")

        # outro call to action
        if seg == len(self.texts) - 1:
            f_cta = font(FONT_TEXT, 38, "SemiBold")
            d.text((W / 2, 1450), "Agree? Tell me in the comments",
                   font=f_cta, fill=(*acc, 255), anchor="mm")

        # flash on #1 reveal
        t1 = self.reveal_time(1)
        if 0 <= t - t1 < 0.35:
            fa = int(110 * (1 - (t - t1) / 0.35))
            d.rectangle([0, 0, W, H], fill=(*acc, fa))

        # source
        f_src = font(FONT_TEXT, 28, "Medium")
        d.text((W / 2, 1830), self.r.source, font=f_src, fill=GREY,
               anchor="mm")
        return img

    # --------------------------------------------------------------- audio --
    def audio(self, voices):
        total = int(self.total * SR)
        mix = np.zeros(total, dtype=np.float32)
        for s, v in zip(self.starts, voices):
            i = int(s * SR)
            mix[i:i + len(v)] += v[:max(0, total - i)]
        rng = np.random.default_rng(7)
        for rank in range(1, self.n + 1):
            i = int(self.reveal_time(rank) * SR)
            sfx = _whoosh(rng)
            if rank == 1:
                ding = _ding()
                ding[:len(sfx)] += sfx * 0.8
                sfx = ding
            j = min(total, i + len(sfx))
            mix[i:j] += sfx[:j - i]
        peak = np.max(np.abs(mix)) or 1
        return (mix / peak * 0.92).astype(np.float32)

    def render(self, voices, out_path):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        wav = out_path.with_suffix(".wav")
        a = self.audio(voices)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR),
             "-ac", "1", "-i", "pipe:0", str(wav)],
            input=a.tobytes(), check=True)
        frames = int(self.total * self.fps)
        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-s", f"{self.W}x{self.H}",
             "-r", str(self.fps), "-i", "pipe:0", "-i", str(wav),
             "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", str(out_path)],
            stdin=subprocess.PIPE)
        for f in range(frames):
            proc.stdin.write(self.frame(f / self.fps).tobytes())
        proc.stdin.close()
        proc.wait()
        wav.unlink(missing_ok=True)
        if proc.returncode:
            raise RuntimeError("ffmpeg failed")
        return out_path


def _whoosh(rng, dur=0.45):
    n = int(dur * SR)
    noise = rng.standard_normal(n).astype(np.float32)
    # crude band sweep via moving-average of varying width
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    k = 24
    smooth = np.convolve(noise, np.ones(k) / k, mode="same")
    return (smooth * env * 0.22).astype(np.float32)


def _ding(dur=1.2):
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.exp(-t * 3.2)
    tone = (np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1320 * t)
            + 0.25 * np.sin(2 * np.pi * 1760 * t))
    return (tone * env * 0.16).astype(np.float32)
