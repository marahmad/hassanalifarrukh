import sys
from datetime import datetime, timezone, timedelta

ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
IG_EPOCH_MS = 1314220021721
PKT = timezone(timedelta(hours=5))

def shortcode_to_dt(sc: str) -> datetime:
    n = 0
    for ch in sc:
        n = n * 64 + ALPHA.index(ch)
    ms = (n >> 23) + IG_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, tz=PKT)

if __name__ == "__main__":
    for sc in sys.argv[1:]:
        print(f"{sc}: {shortcode_to_dt(sc).strftime('%Y-%m-%d %H:%M:%S PKT')}")
