import argparse
import json
import os
import re
import time
import subprocess
import shutil
from pathlib import Path
import requests
import pyfiglet


CACHE_DIR = Path.home() / ".cache" / "termalyr"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TRACK_RE = re.compile(r"\[(\d+):(\d+\.\d+)\](.*)")

RESET_COLOR = "\033[0m"
CLEAR_SCREEN = "\033[2J"
CURSOR_HOME = "\033[H"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"

NAMED_COLORS = {
    "white":  "\033[0m",
    "red":    "\033[31m",
    "green":  "\033[32m",
    "yellow": "\033[33m",
    "blue":   "\033[34m",
    "purple": "\033[35m",
    "cyan":   "\033[36m",
}


def parse_args():
    parser = argparse.ArgumentParser(
        prog="termalyr",
        description="Synchronized song lyrics in your terminal"
    )
    parser.add_argument(
        "-n", "--no-ascii",
        action="store_true",
        help="Show plain text instead of ASCII art"
    )
    parser.add_argument(
        "-c", "--color",
        default="white",
        metavar="COLOR|#RRGGBB",
        help=(
            "Text color. Named: white, red, green, yellow, blue, purple, cyan. "
            "Or hex: #FF8800. Default: white"
        )
    )
    parser.add_argument(
        "-o", "--offset",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="Lyrics time offset in seconds (default: 0.5)"
    )
    parser.add_argument(
        "-f", "--font",
        default="smmono12",
        metavar="FONT",
        help="Figlet font name (default: smmono12)"
    )
    return parser.parse_args()


def resolve_color(value):
    if value.startswith("#") and len(value) == 7:
        try:
            r = int(value[1:3], 16)
            g = int(value[3:5], 16)
            b = int(value[5:7], 16)
            return f"\033[38;2;{r};{g};{b}m"
        except ValueError:
            print(f"Invalid hex color: {value}, falling back to white")
            return NAMED_COLORS["cyan"]
    name = value.lower()
    if name in NAMED_COLORS:
        return NAMED_COLORS[name]
    print(f"Unknown color: {value}, falling back to cyan")
    return NAMED_COLORS["cyan"]


def move_cursor(row, col):
    return f"\033[{row};{col}H"


def render_centered(text, color, no_ascii, font):
    term_w, term_h = shutil.get_terminal_size()

    if no_ascii or not text:
        lines = [text] if text else [""]
    else:
        try:
            rendered = pyfiglet.figlet_format(text, font=font)
        except Exception:
            rendered = text
        lines = rendered.splitlines()

    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)

    if not lines:
        lines = [""]

    art_h = len(lines)
    art_w = max((len(l) for l in lines), default=0)

    start_row = max(1, (term_h - art_h) // 2)
    start_col = max(1, (term_w - art_w) // 2)

    buf = [CLEAR_SCREEN, CURSOR_HOME]

    for i, line in enumerate(lines):
        buf.append(move_cursor(start_row + i, start_col))
        buf.append(f"{color}{line}{RESET_COLOR}")

    os.write(1, "".join(buf).encode())


def sanitize(name):
    return "".join(c for c in name if c not in r'\/:*?"<>|')


def run(cmd):
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()


def get_track():
    try:
        return run(["playerctl", "metadata", "--format", "{{ artist }}|||{{ title }}"])
    except Exception:
        return ""


def get_status():
    try:
        return run(["playerctl", "status"])
    except Exception:
        return ""


def get_position():
    try:
        return float(run(["playerctl", "position"]))
    except Exception:
        return 0.0


def parse_lrc(text):
    lyrics = []
    for line in text.splitlines():
        m = TRACK_RE.match(line)
        if not m:
            continue
        minutes = int(m.group(1))
        seconds = float(m.group(2))
        word = m.group(3).strip()
        lyrics.append({"time": minutes * 60 + seconds, "text": word})
    return lyrics


def load_cache(artist, title):
    path = CACHE_DIR / f"{sanitize(artist)} - {sanitize(title)}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(artist, title, data):
    path = CACHE_DIR / f"{sanitize(artist)} - {sanitize(title)}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_lyrics(artist, title):
    try:
        r = requests.get(
            "https://lrclib.net/api/get",
            params={"artist_name": artist, "track_name": title},
            timeout=10
        )
        if r.status_code != 200:
            return None
        data = r.json()
        if not data.get("syncedLyrics"):
            return None
        return data
    except Exception:
        return None


def main():
    args = parse_args()
    color = resolve_color(args.color)

    def draw(text):
        render_centered(text, color=color, no_ascii=args.no_ascii, font=args.font)

    os.system("stty -echo -icanon time 0 min 0")
    os.write(1, HIDE_CURSOR.encode())

    current_track = None
    lyrics = []
    idx = -1
    last_status_check = 0
    status = "Stopped"

    try:
        while True:
            try:
                now = time.time()

                if now - last_status_check > 0.5:
                    status = get_status()
                    last_status_check = now

                if status != "Playing":
                    time.sleep(0.2)
                    continue

                raw = get_track()

                if not raw or "|||" not in raw:
                    time.sleep(0.5)
                    continue

                artist, title = raw.split("|||", 1)
                track = f"{artist} - {title}"

                if track != current_track:
                    current_track = track
                    idx = -1

                    draw("Loading...")

                    data = load_cache(artist, title)

                    if data is None:
                        data = fetch_lyrics(artist, title)
                        if data:
                            save_cache(artist, title, data)

                    if not data:
                        draw("No Lyrics")
                        lyrics = []
                        continue

                    lyrics = parse_lrc(data["syncedLyrics"])
                    draw("Ready")

                if not lyrics:
                    time.sleep(0.1)
                    continue

                pos = get_position() + args.offset

                new_idx = -1
                for i, line in enumerate(lyrics):
                    if pos >= line["time"]:
                        new_idx = i
                    else:
                        break

                if new_idx != idx:
                    idx = new_idx
                    text = lyrics[idx]["text"] if idx != -1 else ""
                    draw(text)

                time.sleep(0.01)

            except Exception as e:
                draw(f"Error: {e}")
                time.sleep(1)

    except KeyboardInterrupt:
        pass

    finally:
        os.system("stty echo icanon")
        os.write(1, SHOW_CURSOR.encode())
        os.write(1, (CLEAR_SCREEN + CURSOR_HOME + RESET_COLOR).encode())


if __name__ == "__main__":
    main()
