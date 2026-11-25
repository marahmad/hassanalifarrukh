"""
Renders v3 — a super-personal, 2-voice, 100% FYP-safe 1080x1920 (9:16) 30fps
TikTok story video:
  - Voice 1 (`Puck`): Youthful, relatable late-night Pakistani narrator
  - Voice 2 (`Algenib`): Deep, gravelly older-brother mentor voice replying
  - Natural wording: "Hassan Ali Farrukh ke bot ko message kar diya..." (zero "AI" buzzword!)
  - Organic comment trigger: `Comment "ME" below` (no URLs, prices, payment apps, or ad spam!)
  - Exact stream parity with working reels: H.264 High Level 4.0, full bt709 TV range,
    ~5 Mbps bitrate with natural per-frame film grain, 48kHz stereo AAC -14 LUFS.
"""
import json
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy.io import wavfile
from scipy.signal import butter, filtfilt

BASE_DIR = Path(__file__).resolve().parent
VO_DIR = BASE_DIR / "vo_v3"
PREVIEW_DIR = BASE_DIR / "preview_frames_v3"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

OUT_MP4 = BASE_DIR / "hassan_bot_personal_tiktok_v3.mp4"
MASTER_WAV = BASE_DIR / "master_audio_v3.wav"

W, H = 1080, 1920
FPS = 30
SR = 48000
VO_TEMPO = 1.10  # Natural, personal, brisk TikTok pacing (pitch-preserved)

# Warm late-night obsidian palette
BG_TOP = (8, 9, 13)
BG_BOT = (14, 15, 20)
TEXT_PRI = (250, 250, 252)
TEXT_SEC = (182, 184, 196)
TEXT_MUT = (128, 130, 144)
GREEN = (34, 197, 94)
WARM_GOLD = (245, 208, 128)

FONT_BLACK = r"C:\Windows\Fonts\seguibl.ttf"
FONT_BOLD = r"C:\Windows\Fonts\segoeuib.ttf"
FONT_SEMI = r"C:\Windows\Fonts\seguisb.ttf"
FONT_REG = r"C:\Windows\Fonts\segoeui.ttf"

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
RNG = np.random.default_rng(2027)


def get_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, int(size))
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(path, int(size))
    return _FONT_CACHE[key]


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def ease_out_cubic(t: float) -> float:
    t = clamp(t)
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    t = clamp(t)
    return 4 * t * t * t if t < 0.5 else 1.0 - ((-2 * t + 2) ** 3) / 2.0


def ease_out_back(t: float, s: float = 1.35) -> float:
    t = clamp(t)
    t1 = t - 1.0
    return 1.0 + (s + 1.0) * (t1 ** 3) + s * (t1 ** 2)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = f"{cur} {w}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_double_check(d: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int, int]):
    d.line([(x, y + 6), (x + 4, y + 10), (x + 11, y + 1)], fill=color, width=2)
    d.line([(x + 6, y + 6), (x + 10, y + 10), (x + 17, y + 1)], fill=color, width=2)


def draw_down_arrow(d: ImageDraw.ImageDraw, cx: int, y_top: int, color: tuple[int, int, int, int]):
    d.line([(cx, y_top), (cx, y_top + 44)], fill=color, width=7)
    d.polygon([(cx - 18, y_top + 32), (cx + 18, y_top + 32), (cx, y_top + 58)], fill=color)


# ── Atmospheric Backdrop & Organic Film Grain Bank ──────────────────────────

def build_night_bg() -> Image.Image:
    y = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None]
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    for c in range(3):
        arr[:, :, c] = (BG_TOP[c] * (1.0 - y) + BG_BOT[c] * y).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def make_radial_glow(radius: int, color: tuple[int, int, int], max_alpha: int) -> Image.Image:
    size = radius * 2
    yy, xx = np.ogrid[-radius:radius, -radius:radius]
    dist = np.sqrt(xx * xx + yy * yy) / float(radius)
    falloff = np.clip(1.0 - dist, 0.0, 1.0) ** 2.4
    alpha = (falloff * max_alpha).astype(np.uint8)
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[:, :, 0] = color[0]
    rgba[:, :, 1] = color[1]
    rgba[:, :, 2] = color[2]
    rgba[:, :, 3] = alpha
    return Image.fromarray(rgba, mode="RGBA")


BASE_BG = build_night_bg()
GLOW_WARM = make_radial_glow(560, (235, 165, 85), 46)
GLOW_INDIGO = make_radial_glow(580, (75, 110, 195), 48)
GLOW_EMERALD = make_radial_glow(560, GREEN, 58)

GRAIN_BANK = [RNG.normal(0.0, 4.2, (H, W, 1)).astype(np.int16) for _ in range(8)]


def add_organic_grain(img_rgb: Image.Image, frame_idx: int) -> bytes:
    arr = np.asarray(img_rgb, dtype=np.int16)
    out = np.clip(arr + GRAIN_BANK[frame_idx % len(GRAIN_BANK)], 0, 255).astype(np.uint8)
    return out.tobytes()


def paste_glow(canvas_rgb: Image.Image, glow: Image.Image, cx: int, cy: int, scale: float = 1.0):
    g = glow.resize((max(32, int(glow.width * scale)), max(32, int(glow.height * scale))), Image.BILINEAR) if scale != 1.0 else glow
    canvas_rgb.paste(g, (cx - g.width // 2, cy - g.height // 2), g)


def draw_tiktok_story_caption(
    draw: ImageDraw.ImageDraw,
    text: str,
    y_center: int,
    accent_word: str = "",
    accent_color: tuple[int, int, int] = WARM_GOLD,
    font_size: int = 42,
    alpha: float = 1.0,
):
    a = int(clamp(alpha) * 255)
    if a <= 4:
        return
    f = get_font(FONT_BOLD, font_size)
    lines = wrap_text(draw, text, f, 820)
    line_metrics = [text_size(draw, ln, f) for ln in lines]
    max_w = max((w for w, _ in line_metrics), default=100)
    line_h = max((h for _, h in line_metrics), default=42)
    gap = 12
    total_h = line_h * len(lines) + gap * max(0, len(lines) - 1)
    pad_x, pad_y = 32, 18

    cx = 520
    bx0 = cx - max_w // 2 - pad_x
    by0 = y_center - total_h // 2 - pad_y
    bx1 = cx + max_w // 2 + pad_x
    by1 = y_center + total_h // 2 + pad_y

    draw.rounded_rectangle(
        (bx0, by0, bx1, by1),
        radius=20,
        fill=(12, 13, 18, int(200 * clamp(alpha))),
        outline=(255, 255, 255, int(40 * clamp(alpha))),
        width=2,
    )

    first_cy = y_center - total_h // 2 + line_h // 2
    for idx, ln in enumerate(lines):
        ly = first_cy + idx * (line_h + gap)
        col = accent_color if (accent_word and accent_word.lower() in ln.lower()) else TEXT_PRI
        draw.text((cx + 2, ly + 2), ln, font=f, fill=(0, 0, 0, int(180 * clamp(alpha))), anchor="mm")
        draw.text((cx, ly), ln, font=f, fill=(*col, a), anchor="mm")


# ── Timeline & Audio Mixing (2 Distinct Voices + Warm Late-Night Lo-Fi Bed) ─

def prepare_paced_vo(manifest: list[dict]) -> list[dict]:
    paced = []
    for b in manifest:
        src = Path(b["file"])
        dst = VO_DIR / f"{b['id']}_paced.wav"
        # Trim any internal dead-air pauses > 0.25s (pro TikTok editor cut) + smooth 1.10x pacing
        af = "silenceremove=stop_periods=-1:stop_duration=0.25:stop_threshold=-36dB,atempo=" + str(VO_TEMPO)
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(src),
                "-af", af,
                "-ar", str(SR), "-ac", "1",
                str(dst),
            ],
            check=True,
        )
        sr_in, data = wavfile.read(dst)
        dur = len(data) / float(sr_in)
        paced.append({**b, "paced_file": str(dst), "duration": round(dur, 3)})
    return paced


def build_timeline(paced_manifest: list[dict]) -> tuple[dict[str, dict], float]:
    gaps_after = {
        "01_confession": 0.18,
        "02_typing": 0.90,        # Crisp Send-Pop + animated typing dots!
        "03_mentor_reply": 0.48,  # Emotional breath after punchline
        "04_personal_close": 1.25,
    }
    cursor = 0.18
    tl: dict[str, dict] = {}
    for b in paced_manifest:
        bid = b["id"]
        dur = float(b["duration"])
        gap = gaps_after.get(bid, 0.25)
        tl[bid] = {
            **b,
            "vo_start": cursor,
            "vo_end": cursor + dur,
            "scene_start": 0.0 if bid == "01_confession" else cursor,
            "scene_end": cursor + dur + gap,
        }
        cursor = cursor + dur + gap
    return tl, cursor


def resample_to_48k(sr: int, data: np.ndarray) -> np.ndarray:
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.dtype == np.int16:
        x = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        x = data.astype(np.float32) / 2147483648.0
    else:
        x = data.astype(np.float32)
    if sr == SR:
        return x
    new_len = int(round(len(x) * SR / sr))
    return np.interp(np.linspace(0, len(x) - 1, new_len), np.arange(len(x)), x).astype(np.float32)


def synth_lofi_chord(freqs: list[float], dur: float) -> tuple[np.ndarray, np.ndarray]:
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float32) / SR
    L = np.zeros(n, dtype=np.float32)
    R = np.zeros(n, dtype=np.float32)
    env = np.minimum(1.0, t / 0.045) * np.exp(-t * 0.58)
    for i, f in enumerate(freqs):
        w1 = np.sin(2.0 * np.pi * f * t) + 0.22 * np.sin(2.0 * np.pi * (f * 2.0) * t) * np.exp(-t * 2.2)
        w2 = np.sin(2.0 * np.pi * (f * 1.0025) * t + 0.5) + 0.22 * np.sin(2.0 * np.pi * (f * 2.004) * t) * np.exp(-t * 2.2)
        gain = 0.24 if i == 0 else 0.14
        L += w1 * env * gain
        R += w2 * env * gain
    return L, R


def synth_soft_heart_pulse(dur: float = 0.35) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float32) / SR
    freq = 76.0 * np.exp(-t * 14.0) + 42.0
    phase = 2.0 * np.pi * np.cumsum(freq) / SR
    return (np.sin(phase) * np.exp(-t * 9.0) * 0.32).astype(np.float32)


def synth_soft_key() -> np.ndarray:
    n = int(SR * 0.018)
    t = np.arange(n, dtype=np.float32) / SR
    noise = RNG.standard_normal(n).astype(np.float32)
    tone = np.sin(2.0 * np.pi * 1600.0 * t)
    return ((0.55 * noise + 0.45 * tone) * np.exp(-t * 240.0) * 0.09).astype(np.float32)


def synth_msg_pop(f0: float = 480.0, f1: float = 890.0, dur: float = 0.085) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float32) / SR
    freq = f0 + (f1 - f0) * (t / dur)
    phase = 2.0 * np.pi * np.cumsum(freq) / SR
    env = np.minimum(1.0, t / 0.006) * np.exp(-t * 36.0)
    return (np.sin(phase) * env * 0.34).astype(np.float32)


def add_at(buf: np.ndarray, clip: np.ndarray, time_sec: float, gain: float = 1.0):
    idx = int(round(time_sec * SR))
    if idx >= len(buf) or idx + len(clip) <= 0:
        return
    src_start = max(0, -idx)
    dst_start = max(0, idx)
    length = min(len(clip) - src_start, len(buf) - dst_start)
    if length > 0:
        buf[dst_start:dst_start + length] += clip[src_start:src_start + length] * gain


def build_master_audio(tl: dict[str, dict], total_dur: float) -> Path:
    n_samples = int(math.ceil((total_dur + 0.15) * SR))
    vo_track = np.zeros(n_samples, dtype=np.float32)
    sfx_track = np.zeros(n_samples, dtype=np.float32)
    music_L = np.zeros(n_samples, dtype=np.float32)
    music_R = np.zeros(n_samples, dtype=np.float32)

    for bid, info in tl.items():
        sr_in, raw = wavfile.read(info["paced_file"])
        sig = resample_to_48k(sr_in, raw)
        peak = np.max(np.abs(sig)) + 1e-9
        target_peak = 0.85 if bid == "03_mentor_reply" else 0.78
        sig = sig / peak * target_peak
        add_at(vo_track, sig, info["vo_start"], gain=1.0)

    chords = [
        [146.83, 220.00, 261.63, 329.63],  # Dm9
        [116.54, 174.61, 220.00, 293.66],  # Bbmaj7
        [130.81, 196.00, 220.00, 261.63],  # C6
        [110.00, 164.81, 196.00, 261.63],  # Am7
    ]
    bar_len = 3.2
    for bi in range(int(math.ceil(total_dur / bar_len))):
        cL, cR = synth_lofi_chord(chords[bi % len(chords)], bar_len + 0.4)
        add_at(music_L, cL, bi * bar_len, gain=0.42)
        add_at(music_R, cR, bi * bar_len, gain=0.42)

    pulse = synth_soft_heart_pulse()
    pt = 0.25
    while pt < total_dur - 0.6:
        add_at(music_L, pulse, pt, gain=0.35)
        add_at(music_R, pulse, pt, gain=0.35)
        pt += 1.6

    b_lp, a_lp = butter(2, 1350 / (SR / 2), btype="low")
    music_L = filtfilt(b_lp, a_lp, music_L).astype(np.float32)
    music_R = filtfilt(b_lp, a_lp, music_R).astype(np.float32)

    fi_n = int(SR * 0.5)
    fo_n = int(SR * 1.1)
    music_L[:fi_n] *= np.linspace(0.0, 1.0, fi_n, dtype=np.float32)
    music_R[:fi_n] *= np.linspace(0.0, 1.0, fi_n, dtype=np.float32)
    music_L[-fo_n:] *= np.linspace(1.0, 0.0, fo_n, dtype=np.float32)
    music_R[-fo_n:] *= np.linspace(1.0, 0.0, fo_n, dtype=np.float32)

    s2_start = tl["02_typing"]["scene_start"]
    send_t = tl["02_typing"]["vo_end"] - 0.06
    kclick = synth_soft_key()
    t_click = s2_start + 0.55
    while t_click < send_t - 0.18:
        add_at(sfx_track, kclick, t_click, gain=0.95)
        t_click += float(RNG.uniform(0.085, 0.155))
    add_at(sfx_track, synth_msg_pop(480, 960, 0.085), send_t, gain=0.85)
    add_at(sfx_track, synth_msg_pop(580, 860, 0.10), tl["03_mentor_reply"]["vo_start"], gain=0.65)

    env_vo = np.abs(vo_track)
    b_env, a_env = butter(2, 5.0 / (SR / 2), btype="low")
    vo_smooth = np.clip(filtfilt(b_env, a_env, env_vo) * 6.0, 0.0, 1.0)
    duck = 1.0 - 0.50 * vo_smooth

    mix_L = vo_track * 1.08 + music_L * duck + sfx_track * 0.78
    mix_R = vo_track * 1.08 + music_R * duck + sfx_track * 0.78

    stereo = np.stack([mix_L, mix_R], axis=1)
    peak = np.max(np.abs(stereo)) + 1e-9
    if peak > 0.94:
        stereo = np.tanh(stereo / peak * 1.12) * 0.94

    raw_wav = BASE_DIR / "master_audio_v3_raw.wav"
    wavfile.write(raw_wav, SR, (stereo * 32767.0).astype(np.int16))

    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(raw_wav),
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=9",
            "-ar", str(SR), "-ac", "2",
            str(MASTER_WAV),
        ],
        check=True,
    )
    raw_wav.unlink(missing_ok=True)
    return MASTER_WAV


# ── Authentic iPhone Chat Screen (Zero "AI" Buzzword, Natural "Mentor Bot") ─

def render_phone_surface(
    t: float,
    typing_text: str,
    show_user_msg: bool,
    user_msg_pop: float,
    show_typing_dots: bool,
    ai_reply_progress: float,
    highlight_sweep: float,
) -> Image.Image:
    pw, ph = 860, 1180
    phone_rgb = Image.new("RGB", (pw, ph), (12, 13, 16))
    d = ImageDraw.Draw(phone_rgb, "RGBA")

    d.rounded_rectangle((0, 0, pw - 1, ph - 1), radius=58, fill=(22, 23, 28, 255), outline=(68, 70, 82, 255), width=4)
    d.rounded_rectangle((12, 12, pw - 13, ph - 13), radius=48, fill=(12, 13, 16, 255))

    d.rounded_rectangle((pw // 2 - 86, 25, pw // 2 + 86, 55), radius=15, fill=(4, 4, 6, 255))
    f_stat = get_font(FONT_SEMI, 22)
    d.text((52, 40), "2:14", font=f_stat, fill=(*TEXT_PRI, 225), anchor="lm")
    d.text((pw - 46, 40), "5G  24%", font=f_stat, fill=(*TEXT_SEC, 210), anchor="rm")

    # Header: Hassan Ali Farrukh | Active now • Mentor Bot (ZERO "AI"!)
    hy0, hy1 = 72, 166
    d.rectangle((12, hy0, pw - 13, hy1), fill=(19, 20, 24, 255))
    d.line([(12, hy1), (pw - 13, hy1)], fill=(45, 47, 56, 255), width=2)

    d.line([(46, hy0 + 36), (34, hy0 + 48), (46, hy0 + 60)], fill=(*TEXT_SEC, 230), width=3)
    d.rounded_rectangle((70, hy0 + 18, 126, hy0 + 74), radius=28, fill=(250, 250, 252, 255))
    f_logo = get_font(FONT_BLACK, 24)
    d.text((98, hy0 + 46), "HF", font=f_logo, fill=(12, 13, 16, 255), anchor="mm")

    f_title = get_font(FONT_BOLD, 28)
    f_sub = get_font(FONT_SEMI, 19)
    d.text((144, hy0 + 32), "Hassan Ali Farrukh", font=f_title, fill=(*TEXT_PRI, 255), anchor="lm")
    d.ellipse((144, hy0 + 57, 156, hy0 + 69), fill=(*GREEN, 255))
    d.text((164, hy0 + 63), "Active now • Mentor Bot", font=f_sub, fill=(*TEXT_SEC, 230), anchor="lm")

    cy = hy1 + 26
    f_msg = get_font(FONT_REG, 27)
    f_msg_b = get_font(FONT_BOLD, 27)
    f_meta = get_font(FONT_REG, 17)
    f_av = get_font(FONT_BLACK, 18)

    d.text((pw // 2, cy), "Today • 2:14 AM", font=f_meta, fill=(*TEXT_MUT, 190), anchor="mm")
    cy += 34

    # 1. Warm opening bubble
    g_lines = [
        "Assalamu Alaikum! Seedhi baat karte hain —",
        "aaj kal kya chal raha hai dimagh mein?",
    ]
    d.ellipse((32, cy, 76, cy + 44), fill=(250, 250, 252, 255))
    d.text((54, cy + 22), "HF", font=f_av, fill=(12, 13, 16, 255), anchor="mm")
    gb_x0, gb_y0 = 90, cy
    gb_x1, gb_y1 = pw - 104, cy + 106
    d.rounded_rectangle((gb_x0, gb_y0, gb_x1, gb_y1), radius=24, fill=(25, 26, 32, 255), outline=(44, 46, 56, 255), width=2)
    gy = gb_y0 + 18
    for ln in g_lines:
        d.text((gb_x0 + 22, gy), ln, font=f_msg, fill=(*TEXT_PRI, 240))
        gy += 36
    cy = gb_y1 + 22

    # 2. User raw personal confession bubble
    if show_user_msg:
        u_text = "Yar samajh nahi aa raha life kidhar ja rahi hai... har baar 3 din baad meri discipline toot jati hai. Sab try kar liya."
        u_lines = wrap_text(d, u_text, f_msg, 560)
        uh = len(u_lines) * 36 + 50
        ub_w = 616
        pop_dy = int((1.0 - ease_out_back(user_msg_pop)) * 34)
        ub_x0 = pw - 34 - ub_w
        ub_y0 = cy + pop_dy
        ub_x1 = pw - 34
        ub_y1 = ub_y0 + uh
        d.rounded_rectangle(
            (ub_x0, ub_y0, ub_x1, ub_y1),
            radius=24,
            fill=(20, 78, 48, 255),
            outline=(34, 197, 94, 145),
            width=2,
        )
        uy = ub_y0 + 16
        for ln in u_lines:
            d.text((ub_x0 + 22, uy), ln, font=f_msg, fill=(246, 255, 249, 255))
            uy += 36
        d.text((ub_x1 - 92, ub_y1 - 24), "2:14", font=f_meta, fill=(144, 238, 178, 225))
        draw_double_check(d, ub_x1 - 44, ub_y1 - 20, (144, 238, 178, 245))
        cy = ub_y1 + 22

    # 3. Animated typing dots
    if show_typing_dots:
        d.ellipse((32, cy, 76, cy + 44), fill=(250, 250, 252, 255))
        d.text((54, cy + 22), "HF", font=f_av, fill=(12, 13, 16, 255), anchor="mm")
        d.rounded_rectangle((90, cy, 226, cy + 56), radius=24, fill=(25, 26, 32, 255), outline=(50, 52, 64, 255), width=2)
        for di in range(3):
            bounce = math.sin(t * 10.0 - di * 1.1)
            dy = int(-6 * max(0.0, bounce))
            col = GREEN if bounce > 0.25 else TEXT_SEC
            cx_d = 126 + di * 32
            cy_d = cy + 28 + dy
            d.ellipse((cx_d - 7, cy_d - 7, cx_d + 7, cy_d + 7), fill=(*col, 255))

    # 4. Deep mentor bot reply streaming in
    if ai_reply_progress > 0.0:
        paragraphs = [
            "Tu weak nahi hai yar. Bas perfect ban'ne ke chakkar mein khud ko 3 din mein thaka deta hai.",
            "Kal subah sirf 20 minute ka ek chhota wada khud se kar — aur kisi haal mein mat tod.",
            "Pehle apni zubaan ka pakka ban... zindagi khud line pe aa jayegi.",
        ]
        all_words = [(pi, w) for pi, p in enumerate(paragraphs) for w in p.split()]
        visible_n = max(1, int(math.ceil(len(all_words) * clamp(ai_reply_progress))))
        shown_by_para: list[list[str]] = [[] for _ in paragraphs]
        for pi, w in all_words[:visible_n]:
            shown_by_para[pi].append(w)

        rendered_blocks: list[tuple[int, list[str]]] = []
        total_text_h = 0
        for pi, words in enumerate(shown_by_para):
            if not words:
                continue
            f_use = f_msg_b if pi == 2 else f_msg
            p_lines = wrap_text(d, " ".join(words), f_use, 650)
            rendered_blocks.append((pi, p_lines))
            total_text_h += len(p_lines) * 36 + 14

        d.ellipse((32, cy, 76, cy + 44), fill=(250, 250, 252, 255))
        d.text((54, cy + 22), "HF", font=f_av, fill=(12, 13, 16, 255), anchor="mm")

        ab_x0, ab_y0 = 90, cy
        ab_x1 = pw - 34
        ab_y1 = min(ph - 128, ab_y0 + total_text_h + 58)
        d.rounded_rectangle(
            (ab_x0, ab_y0, ab_x1, ab_y1),
            radius=24,
            fill=(25, 27, 33, 255),
            outline=(*WARM_GOLD, 135) if highlight_sweep > 0.05 else (50, 52, 64, 255),
            width=2,
        )

        ay = ab_y0 + 18
        for pi, p_lines in rendered_blocks:
            is_punch = (pi == 2)
            f_use = f_msg_b if is_punch else f_msg
            if is_punch and highlight_sweep > 0.02:
                bh = len(p_lines) * 36 + 14
                hw = int((ab_x1 - ab_x0 - 28) * ease_out_cubic(highlight_sweep))
                d.rounded_rectangle(
                    (ab_x0 + 14, ay - 6, ab_x0 + 14 + max(18, hw), ay - 6 + bh),
                    radius=14,
                    fill=(245, 208, 128, 32),
                    outline=(245, 208, 128, 155),
                    width=2,
                )
            for ln in p_lines:
                if ay + 30 > ab_y1 - 24:
                    break
                col = WARM_GOLD if (is_punch and highlight_sweep > 0.15) else TEXT_PRI
                d.text((ab_x0 + 22, ay), ln, font=f_use, fill=(*col, 255))
                ay += 36
            ay += 14

        d.text((ab_x0 + 22, ab_y1 - 26), "Hassan Mentor Bot", font=f_meta, fill=(*TEXT_MUT, 210))
        d.text((ab_x1 - 66, ab_y1 - 26), "2:15", font=f_meta, fill=(*TEXT_MUT, 210))

    # Bottom input dock
    iy0, iy1 = ph - 110, ph - 30
    d.rounded_rectangle((30, iy0, pw - 30, iy1), radius=26, fill=(24, 25, 31, 255), outline=(58, 60, 72, 255), width=2)
    f_inp = get_font(FONT_REG, 24)
    if typing_text:
        disp = typing_text
        while text_size(d, disp + "|", f_inp)[0] > pw - 178 and len(disp) > 5:
            disp = disp[1:]
        cursor_ch = "|" if int(t * 4) % 2 == 0 else ""
        d.text((52, (iy0 + iy1) // 2), disp + cursor_ch, font=f_inp, fill=(*TEXT_PRI, 255), anchor="lm")
    else:
        d.text((52, (iy0 + iy1) // 2), "Write whatever's on your mind...", font=f_inp, fill=(*TEXT_MUT, 205), anchor="lm")

    send_col = GREEN if typing_text else (46, 48, 58)
    d.rounded_rectangle((pw - 100, iy0 + 12, pw - 44, iy1 - 12), radius=16, fill=(*send_col, 255))
    d.polygon(
        [(pw - 80, iy0 + 26), (pw - 80, iy1 - 26), (pw - 56, (iy0 + iy1) // 2)],
        fill=(12, 13, 16, 255) if typing_text else (*TEXT_SEC, 255),
    )

    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw - 1, ph - 1), radius=58, fill=255)
    phone_rgba = phone_rgb.convert("RGBA")
    phone_rgba.putalpha(mask)
    return phone_rgba


# ── Per-Frame Organic Story Renderer ────────────────────────────────────────

def render_frame(t: float, tl: dict[str, dict], total_dur: float) -> Image.Image:
    canvas = BASE_BG.copy()

    active_id = "04_personal_close"
    for bid in ["01_confession", "02_typing", "03_mentor_reply", "04_personal_close"]:
        if t < tl[bid]["scene_end"]:
            active_id = bid
            break

    s = tl[active_id]
    local_t = max(0.0, t - s["scene_start"])
    scene_len = max(0.01, s["scene_end"] - s["scene_start"])
    vo_prog = clamp((t - s["vo_start"]) / max(0.01, s["duration"]))

    pulse = 1.0 + 0.05 * math.sin(t * 2.2)
    paste_glow(canvas, GLOW_INDIGO, 300, 520, scale=pulse)
    if active_id in ("03_mentor_reply", "04_personal_close"):
        paste_glow(canvas, GLOW_WARM, 640, 1080, scale=1.0 + 0.04 * math.cos(t * 2.0))
        paste_glow(canvas, GLOW_EMERALD, 480, 1420, scale=0.72)
    else:
        paste_glow(canvas, GLOW_WARM, 660, 1180, scale=0.85)

    # ── SCENE 1: 2 AM PERSONAL CONFESSION ───────────────────────
    if active_id == "01_confession":
        phone_bg = render_phone_surface(t, "", False, 0.0, False, 0.0, 0.0)
        phone_blur = phone_bg.filter(ImageFilter.GaussianBlur(radius=14))
        canvas.paste(phone_blur, (515 - phone_blur.width // 2, 360), phone_blur)

        draw = ImageDraw.Draw(canvas, "RGBA")
        draw.rectangle((0, 0, W, H), fill=(8, 9, 13, 155))

        f_time = get_font(FONT_SEMI, 32)
        fade_in = ease_out_cubic(local_t / 0.42)
        a_main = int(255 * fade_in)
        draw.rounded_rectangle(
            (420, 620, 620, 678),
            radius=29,
            fill=(255, 255, 255, int(18 * fade_in)),
            outline=(*WARM_GOLD, int(130 * fade_in)),
            width=2,
        )
        draw.text((520, 649), "2:14 AM", font=f_time, fill=(*WARM_GOLD, a_main), anchor="mm")

        f_line1 = get_font(FONT_BOLD, 62)
        f_line2 = get_font(FONT_BLACK, 66)
        dy1 = int((1.0 - ease_out_cubic(local_t / 0.52)) * 28)
        draw.text((520, 790 + dy1), "Jab sab so rahe hote hain...", font=f_line1, fill=(*TEXT_PRI, a_main), anchor="mm")

        p2 = clamp((vo_prog - 0.45) / 0.30)
        if p2 > 0:
            a2 = int(255 * ease_out_cubic(p2))
            dy2 = int((1.0 - ease_out_cubic(p2)) * 28)
            draw.text((520, 895 + dy2), "tab asal tension", font=f_line2, fill=(*WARM_GOLD, a2), anchor="mm")
            draw.text((520, 978 + dy2), "shuru hoti hai.", font=f_line2, fill=(*TEXT_PRI, a2), anchor="mm")

    # ── SCENE 2: MESSAGING HASSAN ALI FARRUKH KE BOT ────────────
    elif active_id == "02_typing":
        full_q = "Yar samajh nahi aa raha life kidhar ja rahi hai... har baar 3 din baad meri discipline toot jati hai. Sab try kar liya."
        send_t = s["vo_end"] - 0.06
        if t < send_t:
            raw_p = clamp((local_t - 0.32) / max(0.5, (send_t - s["scene_start"] - 0.42)))
            typing_str = full_q[: int(len(full_q) * raw_p)]
            show_user = False
            user_pop = 0.0
            show_dots = False
        else:
            typing_str = ""
            show_user = True
            user_pop = clamp((t - send_t) / 0.28)
            show_dots = (t >= send_t + 0.22)

        phone = render_phone_surface(
            t=t,
            typing_text=typing_str,
            show_user_msg=show_user,
            user_msg_pop=user_pop,
            show_typing_dots=show_dots,
            ai_reply_progress=0.0,
            highlight_sweep=0.0,
        )
        enter_p = ease_out_cubic(local_t / 0.45)
        float_y = int(math.sin(t * 2.0) * 5)
        sc = 0.96 + 0.02 * enter_p
        phone_sc = phone.resize((int(phone.width * sc), int(phone.height * sc)), Image.LANCZOS)
        canvas.paste(phone_sc, (515 - phone_sc.width // 2, 365 + int((1.0 - enter_p) * 90) + float_y), phone_sc)

        draw = ImageDraw.Draw(canvas, "RGBA")
        if vo_prog < 0.48:
            cap, acc = "Hassan Ali Farrukh ke bot ko message kar diya...", "Hassan Ali Farrukh"
        else:
            cap, acc = "3 din baad har routine kyun toot jati hai?", "routine"
        draw_tiktok_story_caption(draw, cap, y_center=250, accent_word=acc, accent_color=WARM_GOLD, font_size=38)

    # ── SCENE 3: DEEP GRAVELLY MENTOR VOICE REPLY ───────────────
    elif active_id == "03_mentor_reply":
        reply_p = clamp((t - s["vo_start"]) / max(0.5, s["duration"] * 0.80))
        hl_p = clamp((vo_prog - 0.66) / 0.22)
        phone = render_phone_surface(
            t=t,
            typing_text="",
            show_user_msg=True,
            user_msg_pop=1.0,
            show_typing_dots=False,
            ai_reply_progress=reply_p,
            highlight_sweep=hl_p,
        )
        zoom = 0.98 + 0.055 * ease_in_out_cubic(local_t / scene_len)
        phone_sc = phone.resize((int(phone.width * zoom), int(phone.height * zoom)), Image.LANCZOS)
        px = 515 - phone_sc.width // 2
        py = 365 - int(24 * ease_in_out_cubic(local_t / scene_len)) + int(math.sin(t * 1.8) * 4)
        canvas.paste(phone_sc, (px, py), phone_sc)

        draw = ImageDraw.Draw(canvas, "RGBA")
        if vo_prog < 0.32:
            cap, acc = "Tu weak nahi hai yar...", "weak"
        elif vo_prog < 0.68:
            cap, acc = "Kal subah sirf 20 minute ka wada khud se kar.", "20 minute"
        else:
            cap, acc = "Pehle apni zubaan ka pakka ban.", "zubaan"
        draw_tiktok_story_caption(draw, cap, y_center=250, accent_word=acc, accent_color=WARM_GOLD, font_size=42)

    # ── SCENE 4: PERSONAL REFLECTION + NATURAL "ME" COMMENT CTA ─
    else:
        phone_done = render_phone_surface(t, "", True, 1.0, False, 1.0, 1.0)
        blur_r = int(12 * ease_out_cubic(local_t / 0.45))
        if blur_r > 0:
            phone_done = phone_done.filter(ImageFilter.GaussianBlur(radius=blur_r))
        canvas.paste(phone_done, (515 - phone_done.width // 2, 350), phone_done)

        draw = ImageDraw.Draw(canvas, "RGBA")
        dim_a = int(185 * ease_out_cubic(local_t / 0.45))
        draw.rectangle((0, 0, W, H), fill=(8, 9, 13, dim_a))

        fade1 = ease_out_cubic(local_t / 0.42)
        f_r1 = get_font(FONT_BOLD, 56)
        f_r2 = get_font(FONT_BLACK, 64)
        dy1 = int((1.0 - fade1) * 28)
        draw.text((520, 620 + dy1), "Pehli baar kisi ne", font=f_r1, fill=(*TEXT_PRI, int(255 * fade1)), anchor="mm")
        draw.text((520, 696 + dy1), "jhooti tasalli nahi di...", font=f_r1, fill=(*TEXT_SEC, int(255 * fade1)), anchor="mm")

        p2 = clamp((vo_prog - 0.30) / 0.25)
        if p2 > 0:
            f2 = ease_out_cubic(p2)
            draw.text((520, 800), "seedha aaina dikha diya.", font=f_r2, fill=(*WARM_GOLD, int(255 * f2)), anchor="mm")

        p3 = clamp((vo_prog - 0.52) / 0.28)
        if p3 > 0:
            ep = ease_out_back(p3)
            card_y = int(960 + (1.0 - ep) * 45)
            draw.rounded_rectangle(
                (110, card_y, 930, card_y + 188),
                radius=34,
                fill=(18, 22, 24, 240),
                outline=(*GREEN, 215),
                width=3,
            )
            f_cta_sm = get_font(FONT_SEMI, 31)
            f_cta_big = get_font(FONT_BLACK, 52)
            draw.text((520, card_y + 56), "Agar tumhare saath bhi aisa hota hai", font=f_cta_sm, fill=(*TEXT_SEC, 245), anchor="mm")
            draw.text((520, card_y + 124), 'Comment "ME" below', font=f_cta_big, fill=(*GREEN, 255), anchor="mm")

            bounce = int(math.sin(t * 6.5) * 10)
            draw_down_arrow(draw, 520, card_y + 218 + bounce, (*TEXT_PRI, 225))

            f_sub = get_font(FONT_SEMI, 25)
            draw.text((520, card_y + 322), "Hassan Mentor Bot • Check profile", font=f_sub, fill=(*TEXT_MUT, 205), anchor="mm")

    return canvas


def main():
    manifest_path = VO_DIR / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}. Run generate_vo_v3.py first.")

    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paced_manifest = prepare_paced_vo(raw_manifest)
    tl, total_dur = build_timeline(paced_manifest)
    total_frames = int(math.ceil(total_dur * FPS))

    print(f"v3 2-Voice Story Timeline: {total_dur:.2f}s ({total_frames} frames @ {FPS}fps)")
    for bid, info in tl.items():
        print(f"  {bid:18s} [{info['scene_start']:.2f}s .. {info['scene_end']:.2f}s]  vo={info['duration']:.2f}s ({info['voice']})")

    print("\n1/3 Mixing warm lo-fi piano + 2-voice stereo audio (Puck + Algenib, -14 LUFS)...")
    audio_path = build_master_audio(tl, total_dur)
    print(f"  -> Master audio ready: {audio_path}")

    print("\n2/3 Saving scene preview frames for visual QA...")
    for bid, info in tl.items():
        snap_t = info["vo_start"] + info["duration"] * 0.78
        if bid == "02_typing":
            snap_t = info["vo_start"] + info["duration"] * 0.35  # Capture "Hassan Ali Farrukh ke bot" caption + typing!
        elif bid == "03_mentor_reply":
            snap_t = info["vo_start"] + info["duration"] * 0.90
        elif bid == "04_personal_close":
            snap_t = info["vo_end"] + 0.30
        render_frame(snap_t, tl, total_dur).save(PREVIEW_DIR / f"{bid}.png", optimize=True)
    print(f"  -> Saved 4 previews in {PREVIEW_DIR}")

    print(f"\n3/3 Encoding {total_frames} frames @ 1080x1920 H.264 High L4.0 full bt709 ~5Mbps...")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}",
        "-r", str(FPS),
        "-i", "-",
        "-i", str(audio_path),
        "-map_metadata", "-1",
        "-c:v", "libx264",
        "-preset", "medium",
        "-profile:v", "high",
        "-level:v", "4.0",
        "-b:v", "5000k",
        "-maxrate", "6500k",
        "-bufsize", "10000k",
        "-pix_fmt", "yuv420p",
        "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=tv",
        "-color_range", "tv",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", str(SR),
        "-movflags", "+faststart",
        "-shortest",
        str(OUT_MP4),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for fi in range(total_frames):
            t = fi / float(FPS)
            frame = render_frame(t, tl, total_dur)
            proc.stdin.write(add_organic_grain(frame, fi))
            if fi % 150 == 0 or fi == total_frames - 1:
                pct = int((fi + 1) / total_frames * 100)
                print(f"  Frame {fi + 1}/{total_frames} ({pct}%)", flush=True)
    finally:
        proc.stdin.close()
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"FFmpeg exited with code {ret}")

    size_mb = OUT_MP4.stat().st_size / (1024 * 1024)
    print(f"\nDONE! v3 2-Voice Personal Video: {OUT_MP4} ({size_mb:.2f} MB, {total_dur:.2f}s)")


if __name__ == "__main__":
    main()
