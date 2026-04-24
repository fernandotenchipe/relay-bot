import os, logging, hashlib, re, asyncio
import time
import urllib.parse
import httpx
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')

BACKEND_URL = os.getenv("BACKEND_URL")


def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\.\-\+]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_compact(text: str) -> str:
    text = normalize(text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_words(text: str):
    return set(normalize_compact(text).split())


def parse_float_safe(val):
    try:
        return float(str(val).strip())
    except Exception:
        return None


def parse_market_title(title: str):
    raw = (title or "").strip()
    low = normalize(raw)

    result = {
        "raw": raw,
        "market_type": "unknown",
        "team1": None,
        "team2": None,
        "side_team": None,
        "line": None,
        "event_prefix": None,
    }

    # Total / OU
    m = re.match(r"^(.*?)\s+vs\.?\s+(.*?):\s*o/u\s*([0-9]+(?:\.[0-9]+)?)$", low, re.I)
    if m:
        result["market_type"] = "total"
        result["team1"] = m.group(1).strip()
        result["team2"] = m.group(2).strip()
        result["line"] = parse_float_safe(m.group(3))
        return result

    # Spread: Team (-8.5)
    m = re.match(r"^spread:\s*(.*?)\s*\(([+-]?[0-9]+(?:\.[0-9]+)?)\)$", low, re.I)
    if m:
        result["market_type"] = "spread"
        result["side_team"] = m.group(1).strip()
        result["line"] = parse_float_safe(m.group(2))
        return result

    # Tournament/Event: A vs B
    m = re.match(r"^(.*?):\s*(.*?)\s+vs\.?\s+(.*?)$", low, re.I)
    if m:
        result["market_type"] = "moneyline"
        result["event_prefix"] = m.group(1).strip()
        result["team1"] = m.group(2).strip()
        result["team2"] = m.group(3).strip()
        return result

    # A vs B
    m = re.match(r"^(.*?)\s+vs\.?\s+(.*?)$", low, re.I)
    if m:
        result["market_type"] = "moneyline"
        result["team1"] = m.group(1).strip()
        result["team2"] = m.group(2).strip()
        return result

    return result


def build_search_queries(title: str):
    p = parse_market_title(title)
    queries = []
    seen = set()

    def add(q):
        q = normalize_compact(q)
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    if p["market_type"] == "total":
        add(f"{p['team1']} {p['team2']}")
        add(f"{p['team2']} {p['team1']}")
        add(f"{p['team1']} vs {p['team2']}")
        add(f"{p['team1']} {p['team2']} total")
        add(f"{p['team1']} {p['team2']} over under")
        if p["line"] is not None:
            add(f"{p['team1']} {p['team2']} {p['line']}")
    elif p["market_type"] == "spread":
        add(f"spread {p['side_team']} {p['line']}")
        add(f"{p['side_team']} {p['line']}")
        add(f"{p['side_team']} spread")
    elif p["market_type"] == "moneyline":
        add(f"{p['team1']} {p['team2']}")
        add(f"{p['team2']} {p['team1']}")
        add(f"{p['team1']} vs {p['team2']}")
        if p["event_prefix"]:
            add(f"{p['event_prefix']} {p['team1']} {p['team2']}")
    else:
        add(title)

    add(title)
    return queries


def extract_line_from_text(text: str):
    if not text:
        return None

    m = re.search(r"([+-]?[0-9]+(?:\.[0-9]+)?)", normalize(text))
    if not m:
        return None
    return parse_float_safe(m.group(1))


def detect_candidate_type(text: str):
    t = normalize(text)

    if "o/u" in t or "over under" in t or "total" in t:
        return "total"

    if "spread" in t:
        return "spread"

    return "moneyline"


def spread_direction_from_line(line):
    if line is None:
        return None
    return "favorite" if line < 0 else "underdog"


def text_contains_any(text, values):
    norm = normalize_compact(text)
    return any(normalize_compact(v) in norm for v in values if v)


def team_pair_score(a1, a2, b_text):
    score = 0
    b = normalize_compact(b_text)

    if a1 and normalize_compact(a1) in b:
        score += 5
    if a2 and normalize_compact(a2) in b:
        score += 5

    return score


def generic_word_score(a, b):
    aw = get_words(a)
    bw = get_words(b)
    if not aw or not bw:
        return 0
    return len(aw & bw)


def score_market_candidate(parsed_query, candidate, original_title):
    texts = []

    for key in ["title", "question", "description", "slug"]:
        val = candidate.get(key)
        if isinstance(val, str) and val.strip():
            texts.append(val)

    blob = " | ".join(texts)
    blob_norm = normalize(blob)

    score = 0

    candidate_type = detect_candidate_type(blob_norm)
    if parsed_query["market_type"] == candidate_type:
        score += 6

    if parsed_query["market_type"] in {"moneyline", "total"}:
        score += team_pair_score(parsed_query["team1"], parsed_query["team2"], blob_norm)

    if parsed_query["market_type"] == "spread" and parsed_query["side_team"]:
        side_team = parsed_query["side_team"]

        if text_contains_any(blob_norm, [side_team]):
            score += 7

        q_line = parsed_query.get("line")
        c_line = extract_line_from_text(blob_norm)
        if q_line is not None and c_line is not None:
            diff = abs(q_line - c_line)
            if diff == 0:
                score += 8
            elif diff <= 0.5:
                score += 5
            elif diff <= 1:
                score += 2

            # Bonus when both lines imply the same side (favorite/underdog).
            if spread_direction_from_line(q_line) == spread_direction_from_line(c_line):
                score += 2

    if parsed_query["market_type"] != "spread":
        q_line = parsed_query.get("line")
        c_line = extract_line_from_text(blob_norm)
        if q_line is not None and c_line is not None:
            diff = abs(q_line - c_line)
            if diff == 0:
                score += 8
            elif diff <= 0.5:
                score += 5
            elif diff <= 1:
                score += 2

    score += generic_word_score(original_title, blob_norm)

    if normalize_compact(original_title) in normalize_compact(blob_norm):
        score += 4

    return score


def candidate_blob(candidate):
    texts = []
    for key in ["title", "question", "description", "slug"]:
        val = candidate.get(key)
        if isinstance(val, str) and val.strip():
            texts.append(val)
    return " | ".join(texts)


def pick_best_market(data, original_title):
    parsed_query = parse_market_title(original_title)

    scored = []

    for m in data:
        score = score_market_candidate(parsed_query, m, original_title)
        scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)

    for score, m in scored[:5]:
        log.info(
            f"[CANDIDATE] score={score} id={m.get('id')} text={candidate_blob(m)[:220]}"
        )

    threshold_map = {
        "moneyline": 8,
        "total": 10,
        "spread": 9,
        "unknown": 6,
    }
    min_score = threshold_map.get(parsed_query["market_type"], 6)

    if not scored:
        return None

    best_score, best = scored[0]

    if best_score < min_score:
        log.info(f"[REJECTED] {original_title} best_score={best_score} min_score={min_score}")
        return None

    return best.get("id")


async def get_market_id(title: str):
    if not title:
        return None

    queries = build_search_queries(title)

    async with httpx.AsyncClient(timeout=10) as client_http:
        all_candidates = []
        seen_ids = set()

        for q in queries:
            try:
                url = "https://gamma-api.polymarket.com/markets?search=" + urllib.parse.quote(q) + "&limit=20"
                res = await client_http.get(url)
                data = res.json()

                if not isinstance(data, list):
                    continue

                for item in data:
                    item_id = item.get("id")
                    if item_id and item_id not in seen_ids:
                        seen_ids.add(item_id)
                        all_candidates.append(item)

            except Exception as e:
                log.error(f"search error for '{q}': {e}")

        best = pick_best_market(all_candidates, title)

        if best:
            log.info(f"[MATCH] {title} -> {best}")
            return best

    log.warning(f"[NO MATCH] {title}")
    return None


async def get_market_status(market_id: str):
    url = f"https://gamma-api.polymarket.com/markets/{market_id}"

    try:
        async with httpx.AsyncClient(timeout=10) as client_http:
            res = await client_http.get(url)
            data = res.json()

            return {
                "closed": data.get("closed"),
                "outcomes": data.get("outcomes"),
            }

    except Exception as e:
        log.error(f"market status error: {e}")
        return None


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
    "sports_esports_titan": {
    "chat_id": get_int_env("TG_SPORTS_ESPORTS_TITAN"),
    "invite_link": os.getenv("TG_SPORTS_ESPORTS_TITAN_INVITE")
    },
}

BOT_USERNAME = "predictionradar_bot"

client = TelegramClient('/app/data/session', API_ID, API_HASH)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# =========================
# DEDUP
# =========================
class Dedup:
    def __init__(self, ttl_seconds=3600):
        self.cache = {}
        self.ttl = ttl_seconds

    def cleanup(self):
        now = time.time()
        expired = [k for k, ts in self.cache.items() if now - ts > self.ttl]
        for k in expired:
            del self.cache[k]

    def get_hash(self, parsed):
        size_bucket = round((parsed.get("size_usd") or 0) / 100) * 100
        shares_bucket = round((parsed.get("shares") or 0) / 100) * 100

        key = "|".join([
            str(parsed.get("whale_id")),
            str(parsed.get("market_title", "")).strip().lower(),
            str(parsed.get("action")),
            str(parsed.get("answer", "")).strip().lower(),
            str(parsed.get("price_cents")),
            str(size_bucket),
            str(shares_bucket),
        ])
        return hashlib.md5(key.encode()).hexdigest()

    def is_duplicate(self, parsed):
        self.cleanup()
        h = self.get_hash(parsed)

        if h in self.cache:
            return True

        self.cache[h] = time.time()
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
    "soccer esports titan": "sports_esports_titan",
}


def normalize_whale(name):
    if not name:
        log.info("Whale raw: None -> None")
        return None

    n = name.lower().strip()

    # Remove emojis and punctuation to match noisy names.
    n = re.sub(r"[^\w\s]", "", n)

    for key in sorted(WHALE_MAP.keys(), key=len, reverse=True):
        if key in n:
            whale_id = WHALE_MAP[key]
            log.info(f"Whale raw: {name} -> {whale_id}")
            return whale_id

    log.info(f"Whale raw: {name} -> None")
    return None

# =========================
# PARSER (FIX COMPLETO)
# =========================
def parse_money(val):
    if not val:
        return None

    val = val.lower().replace("$", "").replace(",", "").strip()

    try:
        if "k" in val:
            return round(float(val.replace("k", "").strip()) * 1000, 2)
        if "m" in val:
            return float(val.replace("m", "").strip()) * 1_000_000
        return float(val)
    except Exception:
        return None


def extract_whale(text):
    match = re.search(r"👤\s*(.+)", text)
    if match:
        return match.group(1).strip()
    return None


def extract_action_answer(text):
    match = re.search(r"📈\s*(BUY|SELL)\s*(.+)", text)
    if match:
        action = match.group(1)
        answer = match.group(2).strip()
        return action, answer
    return None, None


def parse_alert(message_text):
    try:
        whale_name = extract_whale(message_text)

        if not whale_name:
            log.warning("No whale found")
            return None

        action, answer = extract_action_answer(message_text)

        market = re.search(r'\"(.*?)\"', message_text)
        size = re.search(r"Size:\s*\$(.*?)\n", message_text)
        price = re.search(r"(\d+)¢", message_text)
        shares = re.search(r"\((\d+)\s*shares\)", message_text)

        whale_id = normalize_whale(whale_name)

        log.info(f"RAW whale: {whale_name}")
        log.info(f"Normalized: {whale_id}")

        if not whale_id:
            log.warning(f"Unknown whale: {whale_name}")
            return None

        parsed = {
            "whale_name": whale_name,
            "whale_id": whale_id,
            "action": action,
            "answer": answer,  #  string
            "market_title": market.group(1) if market else None,
            "size_usd": parse_money(size.group(1)) if size else None,
            "price_cents": int(price.group(1)) if price else None,
            "shares": int(shares.group(1)) if shares else None,
            "raw_text": message_text
        }

        return parsed

    except Exception as e:
        log.error(f"parse error: {e}")
        return None


def is_valid_alert(parsed):
    required = ["whale_id", "action", "market_title", "price_cents"]

    missing = [f for f in required if not parsed.get(f)]

    if missing:
        log.warning(f"Alert incompleto: {missing}")
        return False

    return True


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

    payload = dict(data)

    if payload.get("market_id") is None:
        payload.pop("market_id", None)

    payload["marketId"] = data.get("market_id")

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10) as client_http:
                await client_http.post(
                    BACKEND_URL + "/api/alerts",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "X-Bot-Api-Key": os.getenv("BOT_API_KEY"),
                    },
                    json=payload,
                )
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
        log.warning(f"FloodWait {e.seconds}s -> skip")
        return
    except Exception as e:
        log.error(f"Telegram error: {e}")


def normalize_outcome_name(text):
    return normalize_compact(text or "")


def evaluate_result(alert, market_data):
    if not market_data or not market_data.get("closed"):
        return None

    outcomes = market_data.get("outcomes") or []

    winner = None
    if isinstance(outcomes, list):
        for o in outcomes:
            if isinstance(o, dict) and o.get("winner"):
                winner = o.get("name")
                break

    if not winner:
        return None

    answer = normalize_outcome_name(alert.get("answer"))
    winner_norm = normalize_outcome_name(winner)

    is_win = answer == winner_norm

    return {
        "resolved": True,
        "result": winner,
        "isWin": is_win
    }


async def worker_loop():
    while True:
        log.info("Worker tick...")

        if not BACKEND_URL:
            log.warning("BACKEND_URL not set, worker idle")
            await asyncio.sleep(600)
            continue

        try:
            async with httpx.AsyncClient(timeout=10) as client_http:
                # 1. traer alerts no resueltas
                res = await client_http.get(BACKEND_URL + "/api/alerts?unresolved=true")
                alerts = res.json().get("data", [])
                seen = set()

                for alert in alerts:
                    market_id = alert.get("marketId")

                    if not market_id:
                        market_title = alert.get("market_title") or alert.get("marketTitle")
                        if market_title:
                            resolved_market_id = await get_market_id(market_title)
                            if resolved_market_id:
                                await client_http.post(
                                    BACKEND_URL + "/api/alerts/update",
                                    json={
                                        "id": alert["id"],
                                        "marketId": resolved_market_id
                                    }
                                )
                                market_id = resolved_market_id

                    if not market_id or market_id in seen:
                        continue

                    seen.add(market_id)

                    market_data = await get_market_status(market_id)

                    result = evaluate_result(alert, market_data)

                    if not result:
                        continue

                    # 2. actualizar backend
                    await client_http.post(
                        BACKEND_URL + "/api/alerts/update",
                        json={
                            "id": alert["id"],
                            **result
                        }
                    )

                    log.info(f"Updated alert {alert['id']} -> {result}")

        except Exception as e:
            log.error(f"worker error: {e}")

        await asyncio.sleep(600)  # cada 10 min

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

    parsed = parse_alert(text)

    if not parsed:
        return

    if not is_valid_alert(parsed):
        return

    if dedup.is_duplicate(parsed):
        return

    market_id = await get_market_id(parsed["market_title"])

    if not market_id:
        log.warning(f"No marketId for: {parsed['market_title']}")
        parsed["market_id"] = None
    else:
        parsed["market_id"] = market_id
        log.info(f"Market search: {parsed['market_title']} -> {market_id}")

    log.info(f"Parsed alert: {parsed}")

    await asyncio.gather(
        send_to_backend(parsed),
        # send_to_channel(parsed),
        return_exceptions=True
    )

# =========================
# MAIN
# =========================
async def main():
    await client.start(phone=PHONE)
    log.info("Relay activo")

    await asyncio.gather(
        client.run_until_disconnected(),
        worker_loop()
    )

asyncio.run(main())