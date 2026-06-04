import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_KEY"]

NSE_BASE_URL   = "https://www.nseindia.com"
NSE_TIMEZONE   = "Asia/Kolkata"

MAX_RETRIES           = 5
REQUEST_DELAY_SECONDS = 1.5

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate",
    "Referer":          "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Connection":       "keep-alive",
    "sec-ch-ua":          '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest":   "empty",
    "Sec-Fetch-Mode":   "cors",
    "Sec-Fetch-Site":   "same-origin",
}
