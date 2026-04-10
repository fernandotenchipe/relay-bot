import os, logging, hashlib, re, asyncio
import httpx
from telethon import TelegramClient, events
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')

BACKEND_URL = os.getenv("BACKEND_URL")

BOT_USERNAME = "predictionradar_bot"

client = TelegramClient('session', API_ID, API_HASH)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# =========================
# DEDUP
# =========================
class Dedup:
    def __init__(self):
        self.cache = set()

    def is_duplicate(self, text):
        h = hashlib.md5(text.encode()).hexdigest()
        if h in self.cache:
            return True
        self.cache.add(h)
        return False

dedup = Dedup()

# =========================
# CLEAN ALERT TEXT
# =========================
def clean_alert(text: str) -> str:
    lines = text.split("\n")
    clean = []

    for line in lines:
        l = line.lower()

        # Stop when non-alert promo/noise content starts.
        if "view on polymarket" in l:
            break
        if "new to polymarket" in l:
            break
        if "predictionradar" in l:
            break
        if "http" in l:
            break

        clean.append(line)

    return "\n".join(clean).strip()

# =========================
# NORMALIZACION WHALES
# =========================
WHALE_MAP = {
    "geopolitical macro": "geo_macro",
    "sports grinder": "sports_grinder",
    "nba volume trader": "nba_volume",
    "esports nba dualist": "nba_dualist",
    "everything trader": "global_trader",
    "global sports arb": "sports_arb",
    "sports focused": "sports_focus",
}

def normalize_whale(name):
    n = name.lower()
    for k, v in WHALE_MAP.items():
        if k in n:
            return v
    return None

# =========================
# PARSER
# =========================
def parse_alert(text):
    try:
        whale = re.search(r"🐋\s*(.*?)\n", text)
        action = re.search(r"(BUY|SELL)", text)
        answer = re.search(r"(Yes|No)", text)
        market = re.search(r'\"(.*?)\"', text)
        size = re.search(r"\$(.*?)\n", text)
        price = re.search(r"(\d+)¢", text)
        shares = re.search(r"(\d+)\s*shares", text)

        whale_name = whale.group(1) if whale else None
        whale_id = normalize_whale(whale_name or "")

        return {
            "whale_name": whale_name,
            "whale_id": whale_id,
            "action": action.group(1) if action else None,
            "answer": answer.group(1) if answer else None,
            "market_title": market.group(1) if market else None,
            "size_usd": size.group(1) if size else None,
            "price_cents": price.group(1) if price else None,
            "shares": shares.group(1) if shares else None,
            "raw_text": text
        }
    except Exception as e:
        log.error(f"parse error: {e}")
        return None

# =========================
# SEND TO BACKEND
# =========================
async def send_to_backend(data):
    try:
        async with httpx.AsyncClient() as client_http:
            await client_http.post(BACKEND_URL + "/alerts", json=data)
    except Exception as e:
        log.error(f"backend error: {e}")

# =========================
# HANDLER
# =========================
@client.on(events.NewMessage(from_users=BOT_USERNAME))
async def handler(event):
    text = event.message.raw_text or ""
    text = clean_alert(text)

    if "whale alert" not in text.lower():
        return

    if dedup.is_duplicate(text):
        return

    parsed = parse_alert(text)
    if not parsed:
        return

    await send_to_backend(parsed)

# =========================
# MAIN
# =========================
async def main():
    await client.start(phone=PHONE)
    log.info("Relay activo")
    await client.run_until_disconnected()

asyncio.run(main())