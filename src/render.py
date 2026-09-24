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


# ------------------------------------------------------------------ flags ----
_flag_cache = {}
_flag_zip = None


def _flag_from_zip(code):
    """Flags (flag-icons, MIT) are packed in assets/flags.zip."""
    global _flag_zip
    import io
    import zipfile
    if _flag_zip is None:
        _flag_zip = zipfile.ZipFile(ROOT / "assets/flags.zip")
    try:
        return Image.open(io.BytesIO(_flag_zip.read(f"{code}.png")))
    except KeyError:
        return None


def flag_img(code, w):
    key = (code, w)
    if key not in _flag_cache:
        im = _flag_from_zip(code) if code else None
        if im is None:
            _flag_cache[key] = None
        else:
            im = im.convert("RGBA")
            h = int(w * 3 / 4)
            im = im.resize((w, h), Image.LANCZOS)
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                                   radius=max(4, w // 10),
                                                   fill=255)
            im.putalpha(mask)
            _flag_cache[key] = im
    return _flag_cache[key]


# ------------------------------------------------------------------ scene ----
class Renderer:
    def __init__(self, ranking, script, timeline, cfg):
        v = cfg["video"]
        self.W, self.H, self.fps = v["width"], v["height"], v["fps"]
        self.r, self.s, self.tl = ranking, script, timeline
        self.n = len(ranking.items)
        pillar = cfg["pillars"][ranking.pillar]
        self.accent = hex_rgb(pillar["accent"])
        self.pillar_label = pillar["label"]
        self.brand = cfg["channel"]["name"].upper()
        self.bg = make_background(self.W, self.H, self.accent)
        self.starts = timeline["seg_starts"]
        self.total = timeline["end"] + 1.2
        self.ref = ranking.reference or {}
        vals = [abs(i.value) for i in ranking.items]
        if self.ref:
            vals.append(abs(self.ref["value"]))
        self.max_val = max(vals) or 1
        self.parts = [split_display(i.display) for i in ranking.items]
        self.chunks = self._chunks(timeline["words"])

    @staticmethod
    def _chunks(words, size=3):
        """Group words into caption chunks (never across segments)."""
        out, cur = [], []
        for w in words:
            if cur and (len(cur) >= size or w[3] != cur[-1][3]
                        or cur[-1][0][-1:] in ".?!,"):
                out.append(cur)
                cur = []
            cur.append(w)
        if cur:
            out.append(cur)
        return out

    def reveal_time(self, rank):
        return self.starts[self.n - rank + 1]

    def segment_at(self, t):
        idx = 0
        for i, s in enumerate(self.starts):
            if t >= s:
                idx = i
        return idx

    def zoom_at(self, t):
        z = 1.0
        for rank in range(1, self.n + 1):
            p = t - self.reveal_time(rank)
            if 0 <= p < 0.6:
                amp = 0.05 if rank == 1 else 0.03
                z = max(z, 1 + amp * math.sin(math.pi * p / 0.6))
        return z

    def draw_captions(self, d, t):
        W = self.W
        chunk = None
        for i, c in enumerate(self.chunks):
            end = self.chunks[i + 1][0][1] if i + 1 < len(self.chunks) else c[-1][2] + 0.6
            if c[0][1] - 0.05 <= t < end:
                chunk = c
                break
        if not chunk:
            return
        f = font(FONT_DISPLAY, 92)
        text_words = [w[0].upper() for w in chunk]
        space = d.textlength(" ", font=f)
        widths = [d.textlength(w, font=f) for w in text_words]
        total_w = sum(widths) + space * (len(widths) - 1)
        scale_f = f
        if total_w > W - 140:
            size = int(92 * (W - 140) / total_w)
            scale_f = font(FONT_DISPLAY, size)
            widths = [d.textlength(w, font=scale_f) for w in text_words]
            space = d.textlength(" ", font=scale_f)
            total_w = sum(widths) + space * (len(widths) - 1)
        x = (W - total_w) / 2
        y = 1600
        for (word, ws, we, _), label, wdt in zip(chunk, text_words, widths):
            active = ws - 0.04 <= t
            col = self.accent if (ws - 0.04 <= t < we + 0.08) else WHITE
            alpha = 255 if active else 110
            for ox, oy in ((-4, 0), (4, 0), (0, -4), (0, 4), (3, 3), (-3, 3)):
                d.text((x + ox, y + oy), label, font=scale_f,
                       fill=(0, 0, 0, alpha), anchor="lm")
            d.text((x, y), label, font=scale_f, fill=(*col, alpha),
                   anchor="lm")
            x += wdt + space

    def frame(self, t):
        W, H = self.W, self.H
        img = self.bg.copy()
        d = ImageDraw.Draw(img, "RGBA")
        acc = self.accent

        # header
        f_brand = font(FONT_TEXT, 30, "Bold")
        label = f"{self.brand}  ·  {self.pillar_label}"
        tw = d.textlength(label, font=f_brand)
        d.rounded_rectangle([(W - tw) / 2 - 28, 96, (W + tw) / 2 + 28, 150],
                            radius=27, fill=(*acc, 40), outline=(*acc, 160),
                            width=2)
        d.text((W / 2, 123), label, font=f_brand, fill=WHITE, anchor="mm")
        k = ease_out_back(t / 0.45)
        title = self.r.title.upper()
        f_title = fit_font(d, title, FONT_DISPLAY, int(118 * max(k, 0.01)),
                           W - 120, min_size=10)
        d.text((W / 2, 250), title, font=f_title, fill=WHITE, anchor="mm")
        f_sub = font(FONT_TEXT, 36, "Medium")
        d.text((W / 2, 340), self.r.subtitle, font=f_sub, fill=GREY,
               anchor="mm")

        # board geometry
        top, row_h, gap = 470, 168, 16
        x0, x1 = 60, W - 60
        bx = x0 + 28
        tx = bx + 100 + 28          # text column
        bar_x0, bar_x1 = tx, x1 - 34

        # reference chip + dashed line
        if self.ref:
            f_ref = font(FONT_TEXT, 32, "SemiBold")
            ref_label = f"{self.ref['name']}: {self.ref['display']}"
            fl = flag_img(self.ref.get("code", ""), 44)
            rw = d.textlength(ref_label, font=f_ref) + (58 if fl else 0)
            rx = (W - rw) / 2
            if fl:
                img.paste(fl, (int(rx), 394), fl)
                rx += 58
            d.text((rx, 411), ref_label, font=f_ref, fill=WHITE, anchor="lm")

        for rank in range(1, self.n + 1):
            item = self.r.items[rank - 1]
            y = top + (rank - 1) * (row_h + gap)
            rt = self.reveal_time(rank)
            p = t - rt
            revealed = p >= 0
            active = revealed and self.segment_at(t) == self.n - rank + 1
            is_one = rank == 1 and revealed
            slide = ease_out(p / 0.3) if revealed else 0
            dx = int((1 - slide) * 90) if revealed else 0
            outline = (*acc, 255) if active else (255, 255, 255, 22)
            if is_one and not active:
                pulse = 0.5 + 0.5 * math.sin(p * 5)
                outline = (*acc, int(150 + 100 * pulse))
            d.rounded_rectangle([x0 + dx, y, x1 + dx, y + row_h], radius=26,
                                fill=(255, 255, 255, 16 if revealed else 5),
                                outline=outline,
                                width=4 if (active or is_one) else 2)
            cy = y + row_h / 2
            badge = acc if revealed else DIM
            d.ellipse([bx + dx, cy - 50, bx + 100 + dx, cy + 50],
                      fill=(*badge, 255))
            d.text((bx + 50 + dx, cy + 2), f"#{rank}",
                   font=font(FONT_DISPLAY, 58),
                   fill=(10, 12, 20) if revealed else GREY, anchor="mm")
            if not revealed:
                d.text((tx, cy), "?", font=font(FONT_DISPLAY, 60), fill=DIM,
                       anchor="lm")
                continue

            # flag + name + value
            name_y = y + 54
            nx = tx + dx
            fl = flag_img(item.code, 72)
            if fl:
                img.paste(fl, (int(nx), int(name_y - 27)), fl)
                nx += 90
            cnt = ease_out(p / 0.8)
            val_txt = (format_counter(self.parts[rank - 1], cnt)
                       if self.parts[rank - 1] else item.display)
            f_val = font(FONT_DISPLAY, 64)
            d.text((x1 - 34 + dx, name_y), val_txt, font=f_val, fill=WHITE,
                   anchor="rm")
            val_w = d.textlength(item.display, font=f_val)
            f_name = fit_font(d, item.name, FONT_TEXT, 52,
                              (x1 - 34 - val_w - 30) - (nx - dx), "Bold")
            d.text((nx, name_y), item.name, font=f_name, fill=WHITE,
                   anchor="lm")
            # bar
            by = y + 112
            d.rounded_rectangle([bar_x0 + dx, by, bar_x1 + dx, by + 24],
                                radius=12, fill=(255, 255, 255, 18))
            frac = abs(item.value) / self.max_val
            bw = max(24, (bar_x1 - bar_x0) * frac * ease_out(p / 0.7))
            d.rounded_rectangle([bar_x0 + dx, by, bar_x0 + dx + bw, by + 24],
                                radius=12, fill=(*acc, 255))

        # reference line drawn over the bars
        if self.ref:
            ref_x = bar_x0 + (bar_x1 - bar_x0) * abs(self.ref["value"]) / self.max_val
            yb0, yb1 = top + 96, top + self.n * (row_h + gap) - gap - 20
            y = yb0
            while y < yb1:
                d.line([(ref_x, y), (ref_x, min(y + 14, yb1))],
                       fill=(255, 255, 255, 170), width=3)
                y += 24

        # flash on #1
        t1 = self.reveal_time(1)
        if 0 <= t - t1 < 0.3:
            d.rectangle([0, 0, W, H], fill=(*acc, int(100 * (1 - (t - t1) / 0.3))))

        # push-in (board + header), captions and source stay sharp on top
        z = self.zoom_at(t)
        if z > 1.001:
            zw, zh = int(W * z), int(H * z)
            big = img.resize((zw, zh), Image.BILINEAR)
            ox, oy = (zw - W) // 2, int((zh - H) * 0.45)
            img = big.crop((ox, oy, ox + W, oy + H))
            d = ImageDraw.Draw(img, "RGBA")

        self.draw_captions(d, t)
        d.text((W / 2, 1840), self.r.source, font=font(FONT_TEXT, 28, "Medium"),
               fill=GREY, anchor="mm")
        return img

    # --------------------------------------------------------------- audio --
    def audio(self, voice):
        total = int(self.total * SR)
        mix = np.zeros(total, dtype=np.float32)
        mix[:min(total, len(voice))] += voice[:total]
        rng = np.random.default_rng(7)
        for rank in range(1, self.n + 1):
            i = int(max(0, self.reveal_time(rank) - 0.08) * SR)
            sfx = _whoosh(rng)
            if rank == 1:
                ding = _ding()
                ding[:len(sfx)] += sfx * 0.8
                sfx = ding
            j = min(total, i + len(sfx))
            mix[i:j] += sfx[:j - i]
        peak = np.max(np.abs(mix)) or 1
        return (mix / peak * 0.9).astype(np.float32)

    def render(self, voice, out_path):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        wav = out_path.with_suffix(".wav")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR),
             "-ac", "1", "-i", "pipe:0",
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", str(SR),
             str(wav)],
            input=self.audio(voice).tobytes(), check=True)
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


def _whoosh(rng, dur=0.4):
    n = int(dur * SR)
    noise = rng.standard_normal(n).astype(np.float32)
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    smooth = np.convolve(noise, np.ones(24) / 24, mode="same")
    return (smooth * env * 0.2).astype(np.float32)


def _ding(dur=1.2):
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.exp(-t * 3.2)
    tone = (np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1320 * t)
            + 0.25 * np.sin(2 * np.pi * 1760 * t))
    return (tone * env * 0.14).astype(np.float32)
