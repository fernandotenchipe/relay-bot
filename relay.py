import os, logging, hashlib, re, asyncio
import httpx
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')

BACKEND_URL = os.getenv("BACKEND_URL")


def get_int_env(name):
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return None
    try:
        return int(val)
    except ValueError:
        return None

# =========================
# CHANNEL CONFIG (9 whales)
# =========================
CHANNELS = {
    "geo_macro": {
        "chat_id": get_int_env("TG_GEO_MACRO"),
        "invite_link": os.getenv("TG_GEO_MACRO_INVITE")
    },
    "sports_grinder": {
        "chat_id": get_int_env("TG_SPORTS_GRINDER"),
        "invite_link": os.getenv("TG_SPORTS_GRINDER_INVITE")
    },
    "nba_volume": {
        "chat_id": get_int_env("TG_NBA_VOLUME"),
        "invite_link": os.getenv("TG_NBA_VOLUME_INVITE")
    },
    "nba_dualist": {
        "chat_id": get_int_env("TG_NBA_DUALIST"),
        "invite_link": os.getenv("TG_NBA_DUALIST_INVITE")
    },
    "global_trader": {
        "chat_id": get_int_env("TG_GLOBAL_TRADER"),
        "invite_link": os.getenv("TG_GLOBAL_TRADER_INVITE")
    },
    "sports_arb": {
        "chat_id": get_int_env("TG_SPORTS_ARB"),
        "invite_link": os.getenv("TG_SPORTS_ARB_INVITE")
    },
    "sports_focus": {
        "chat_id": get_int_env("TG_SPORTS_FOCUS"),
        "invite_link": os.getenv("TG_SPORTS_FOCUS_INVITE")
    },
}

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

    def get_hash(self, text):
        return hashlib.md5(text.encode()).hexdigest()

    def is_duplicate(self, text):
        h = self.get_hash(text)
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
# NORMALIZACION WHALES (FIX)
# =========================
WHALE_MAP = {
    "geopolitical macro": "geo_macro",
    "sports grinder": "sports_grinder",
    "nba volume": "nba_volume",
    "esports nba": "nba_dualist",
    "everything trader": "global_trader",
    "global sports": "sports_arb",
    "sports focused": "sports_focus",
}


def normalize_whale(name):
    if not name:
        log.info("Whale raw: None -> None")
        return None

    n = name.lower().strip()

    # Remove emojis and punctuation to match noisy names.
    n = re.sub(r"[^\w\s]", "", n)

    for key, whale_id in WHALE_MAP.items():
        if key in n:
            log.info(f"Whale raw: {name} -> {whale_id}")
            return whale_id

    log.info(f"Whale raw: {name} -> None")
    return None

# =========================
# PARSER
# =========================
def parse_money(val):
    if not val:
        return None

    val = val.lower().replace("$", "").replace(",", "").strip()

    try:
        if "k" in val:
            return float(val.replace("k", "").strip()) * 1000
        if "m" in val:
            return float(val.replace("m", "").strip()) * 1_000_000
        return float(val)
    except Exception:
        return None


def parse_alert(text):
    try:
        whale = re.search(r"👤\s*(.*?)\n", text)
        action = re.search(r"(BUY|SELL)", text)
        answer = re.search(r"(Yes|No)", text)
        market = re.search(r'\"(.*?)\"', text)
        size = re.search(r"Size:\s*\$(.*?)\n", text)
        price = re.search(r"(\d+)¢", text)
        shares = re.search(r"\((\d+)\s*shares\)", text)

        whale_name = whale.group(1).strip() if whale else None
        whale_id = normalize_whale(whale_name)
        log.info(f"RAW whale: {whale_name}")
        log.info(f"Normalized: {whale_id}")

        if not whale_id:
            log.warning(f"Unknown whale: {whale_name}")
            return None

        return {
            "whale_name": whale_name,
            "whale_id": whale_id,
            "action": action.group(1) if action else None,
            "answer": answer.group(1) if answer else None,
            "market_title": market.group(1) if market else None,
            "size_usd": parse_money(size.group(1)) if size else None,
            "price_cents": int(price.group(1)) if price else None,
            "shares": int(shares.group(1)) if shares else None,
            "raw_text": text
        }
    except Exception as e:
        log.error(f"parse error: {e}")
        return None


def format_alert(alert):
    return f"""🐋 {alert['whale_name']}

📈 {alert['action']} {alert['answer']}
📊 \"{alert['market_title']}\"

💰 ${alert['size_usd']}
💲 {alert['price_cents']}¢ ({alert['shares']} shares)
"""

# =========================
# SEND TO BACKEND
# =========================
async def send_to_backend(data):
    if not BACKEND_URL:
        log.warning("BACKEND_URL not set, skip backend send")
        return

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10) as client_http:
                await client_http.post(BACKEND_URL + "/alerts", json=data)
                return
        except Exception as e:
            log.error(f"backend error attempt {attempt}: {e}")
            await asyncio.sleep(2)


async def send_to_channel(alert):
    whale_id = alert.get("whale_id")

    if not whale_id:
        log.warning("No whale_id, skip telegram")
        return

    if whale_id not in CHANNELS:
        log.warning(f"No channel for {whale_id}")
        return

    chat_id = CHANNELS[whale_id]["chat_id"]
    if not chat_id:
        log.warning(f"Missing chat_id for {whale_id}")
        return

    message = format_alert(alert)

    try:
        await client.send_message(chat_id, message)
    except FloodWaitError as e:
        log.warning(f"Flood wait: {e.seconds}s")
        await asyncio.sleep(e.seconds)
    except Exception as e:
        log.error(f"Telegram error: {e}")

# =========================
# HANDLER
# =========================
@client.on(events.NewMessage(from_users=BOT_USERNAME))
async def handler(event):
    text = event.message.raw_text or ""
    text = clean_alert(text)
    log.info(f"Incoming alert:\n{text}")

    if "whale alert" not in text.lower():
        return

    if "price" not in text.lower():
        return

    if dedup.is_duplicate(text):
        return

    parsed = parse_alert(text)
    log.info(f"Parsed alert: {parsed}")
    if not parsed:
        return

    await send_to_backend(parsed)
    await send_to_channel(parsed)

# =========================
# MAIN
# =========================
async def main():
    await client.start(phone=PHONE)
    log.info("Relay activo")
    await client.run_until_disconnected()

asyncio.run(main())