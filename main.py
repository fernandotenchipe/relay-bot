import os, logging, hashlib, re, asyncio
import json
import time
import urllib.parse
from urllib.parse import urlparse
import httpx
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl
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

    # Both teams to score
    m = re.match(r"^(.*?)\s+vs\.?\s+(.*?):\s*both teams to score$", low, re.I)
    if m:
        result["market_type"] = "both_teams_score"
        result["team1"] = m.group(1).strip()
        result["team2"] = m.group(2).strip()
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

    if "both teams to score" in t or "both teams score" in t:
        return "both_teams_score"

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

    for term in expand_team_terms(a1):
        if term and term in b:
            score += 5
            break

    for term in expand_team_terms(a2):
        if term and term in b:
            score += 5
            break

    return score


def generic_word_score(a, b):
    aw = get_words(a)
    bw = get_words(b)
    if not aw or not bw:
        return 0
    return len(aw & bw)


def required_terms_present(parsed_query, blob_text):
    blob = normalize_compact(blob_text)

    market_type = parsed_query.get("market_type")

    if market_type in {"moneyline", "total"}:
        t1 = normalize_compact(parsed_query.get("team1") or "")
        t2 = normalize_compact(parsed_query.get("team2") or "")

        has_t1 = bool(t1 and t1 in blob)
        has_t2 = bool(t2 and t2 in blob)

        # Menos estricto: acepta si aparece alguno de los equipos.
        return has_t1 or has_t2

    if market_type == "spread":
        side_team = normalize_compact(parsed_query.get("side_team") or "")
        if side_team and side_team in blob:
            return True
        # Si es spread y no hay team2 definido, avisar ambigüedad
        if parsed_query.get("market_type") == "spread" and not parsed_query.get("team2"):
            log.warning(f"[SPREAD AMBIGUOUS] {parsed_query.get('raw')}")
        return False

    return True


def score_market_candidate(parsed_query, candidate, original_title):
    rejected, reason = reject_wrong_market_type(parsed_query, candidate)

    if rejected:
        return -999

    texts = []

    for key in [
        "title",
        "question",
        "description",
        "slug",
        "subtitle",
        "_eventTitle",
        "_eventSlug",
        "outcomes",
        "groupItemTitle",
    ]:
        val = candidate.get(key)
        if isinstance(val, str) and val.strip():
            texts.append(val)
        elif isinstance(val, list):
            texts.append(" ".join(str(x) for x in val))

    blob = " | ".join(texts)
    blob_norm = normalize(blob)

    if not required_terms_present(parsed_query, blob_norm):
        return -999

    score = 0

    candidate_type = detect_candidate_type(blob_norm)
    if parsed_query["market_type"] == candidate_type:
        score += 6

    if parsed_query["market_type"] in {"moneyline", "total", "both_teams_score"}:
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

    for key in [
        "title",
        "question",
        "description",
        "slug",
        "subtitle",
        "_eventTitle",
        "_eventSlug",
        "outcomes",
        "groupItemTitle",
        "gameStatus",
    ]:
        val = candidate.get(key)

        if isinstance(val, str) and val.strip():
            texts.append(val)

        elif isinstance(val, list):
            texts.append(" ".join(str(x) for x in val))

    return " | ".join(texts)


async def pick_best_market(client_http, data, original_title):
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
        "both_teams_score": 8,
    }
    min_score = threshold_map.get(parsed_query["market_type"], 6)

    for score, candidate in scored[:15]:
        market_id = candidate.get("id")

        if not market_id:
            continue

        if score < min_score:
            continue

        valid, full_market = await is_valid_market_id(client_http, str(market_id))

        if not valid:
            log.warning(f"[SKIP_INVALID_MARKET_ID] {market_id} for {original_title}")
            continue

        ok, reason = validate_market_against_alert(original_title, full_market)

        if not ok:
            log.warning(
                f"[SKIP_BAD_MATCH] {original_title} -> {market_id} reason={reason}"
            )
            continue

        log.info(f"[MATCH] {original_title} -> {market_id}")
        return str(market_id)

    log.info(f"[REJECTED] {original_title} no valid candidate min_score={min_score}")
    return None


async def get_market_id(title: str, created_at=None):
    if not title:
        return None

    parsed_query = parse_market_title(title)
    if parsed_query["market_type"] == "spread":
        log.warning(f"[SPREAD NEEDS CONTEXT] {title}")
        return None

    queries = build_search_queries(title)

    async with httpx.AsyncClient(timeout=10) as client_http:
        all_candidates = []
        seen_ids = set()

        for q in queries:
            # 1) public-search
            public_candidates = await search_public(client_http, q)

            # 2) markets search abierto/cerrado
            market_candidates = []
            for closed in ["false", "true"]:
                try:
                    res = await client_http.get(
                        "https://gamma-api.polymarket.com/markets",
                        params={
                            "search": q,
                            "limit": 50,
                            "closed": closed,
                        },
                    )
                    data = res.json()
                    if isinstance(data, list):
                        market_candidates.extend(data)
                except Exception as e:
                    log.error(f"market search error for '{q}': {e}")

            for item in public_candidates + market_candidates:
                item_id = item.get("id")
                if item_id and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    all_candidates.append(item)

        best = await pick_best_market(client_http, all_candidates, title)

        if best:
            return best

    log.warning(f"[NO MATCH] {title}")
    return None


async def search_public(client_http, q: str):
    url = "https://gamma-api.polymarket.com/public-search"
    params = {
        "q": q,
        "limit_per_type": 10,
        "keep_closed_markets": 1,
        "search_profiles": "false",
        "search_tags": "false",
    }

    try:
        res = await client_http.get(url, params=params)
        data = res.json()

        candidates = []

        for event in data.get("events") or []:
            for m in event.get("markets") or []:
                candidates.append({
                    **m,
                    "_eventTitle": event.get("title"),
                    "_eventSlug": event.get("slug"),
                    "_eventId": event.get("id"),
                    "_source": "public_search_market",
                })

        for m in data.get("markets") or []:
            candidates.append({
                **m,
                "_source": "public_search_direct_market",
            })

        return candidates

    except Exception as e:
        log.error(f"public-search error for '{q}': {e}")
        return []


def parse_json_field(value, default=None):
    if default is None:
        default = []

    if value is None:
        return default

    if isinstance(value, list):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return default

    return default


def parse_prices(value):
    raw = parse_json_field(value, [])

    prices = []
    for p in raw:
        try:
            prices.append(float(p))
        except Exception:
            prices.append(None)

    return prices


def extract_numbers(text: str):
    nums = re.findall(r"[+-]?[0-9]+(?:\.[0-9]+)?", normalize(text or ""))
    out = []

    for n in nums:
        try:
            out.append(float(n))
        except Exception:
            pass

    return out


def has_exact_line(text: str, line):
    if line is None:
        return True

    nums = extract_numbers(text)

    return any(abs(n - float(line)) <= 0.01 for n in nums)


def get_candidate_title(candidate):
    return normalize(
        candidate.get("title")
        or candidate.get("question")
        or candidate.get("name")
        or ""
    )


def is_player_prop(blob: str):
    bad_terms = [
        "points over",
        "points o/u",
        "assists over",
        "assists o/u",
        "rebounds over",
        "rebounds o/u",
        "steals over",
        "blocks over",
        "threes over",
        "player",
    ]

    return any(term in blob for term in bad_terms)


def reject_wrong_market_type(parsed_query, candidate):
    title = get_candidate_title(candidate)
    blob = normalize(candidate_blob(candidate))

    market_type = parsed_query.get("market_type")

    if market_type == "total":
        line = parsed_query.get("line")

        if is_player_prop(blob):
            return True, "PLAYER_PROP"

        if line is not None and not has_exact_line(blob, line):
            return True, "LINE_MISMATCH"

        total_terms = [
            "o/u",
            "over under",
            "total",
            "combine to score",
            "combined score",
        ]

        if not any(term in blob for term in total_terms):
            return True, "TYPE_MISMATCH_TOTAL"

    if market_type == "moneyline":
        bad_terms = [
            "series",
            "spread",
            "o/u",
            "over under",
            "points",
            "assists",
            "rebounds",
            "1h",
            "first half",
            "set 1",
            "handicap",
        ]

        if any(term in title for term in bad_terms):
            return True, "TYPE_MISMATCH_MONEYLINE"

    if market_type == "spread":
        line = parsed_query.get("line")

        if "spread" not in title and "spread" not in blob:
            return True, "TYPE_MISMATCH_SPREAD"

        if line is not None and not has_exact_line(blob, line):
            return True, "LINE_MISMATCH"

        if not parsed_query.get("team2") and not parsed_query.get("event_prefix"):
            return True, "AMBIGUOUS_SPREAD"

    if market_type == "both_teams_score":
        if "both teams to score" not in blob and "both teams score" not in blob:
            return True, "TYPE_MISMATCH_BTTS"

    return False, None


async def is_valid_market_id(client_http, market_id: str):
    try:
        res = await client_http.get(
            f"https://gamma-api.polymarket.com/markets/{market_id}"
        )

        if res.status_code != 200:
            return False, None

        data = res.json()

        if not data.get("id"):
            return False, None

        if data.get("outcomes") is None:
            return False, None

        return True, data

    except Exception:
        return False, None


def validate_market_against_alert(market_title: str, market_data: dict):
    parsed_query = parse_market_title(market_title)

    rejected, reason = reject_wrong_market_type(parsed_query, market_data)

    if rejected:
        return False, reason

    score = score_market_candidate(parsed_query, market_data, market_title)

    threshold_map = {
        "moneyline": 8,
        "total": 10,
        "spread": 9,
        "both_teams_score": 8,
        "unknown": 6,
    }

    min_score = threshold_map.get(parsed_query["market_type"], 6)

    if score < min_score:
        return False, f"LOW_SCORE_{score}"

    return True, "OK"


async def get_market_status(market_id: str):
    url = f"https://gamma-api.polymarket.com/markets/{market_id}"

    try:
        async with httpx.AsyncClient(timeout=10) as client_http:
            res = await client_http.get(url)
            data = res.json()

            return {
                "id": data.get("id"),
                "question": data.get("question"),
                "slug": data.get("slug"),
                "closed": data.get("closed"),
                "active": data.get("active"),
                "umaResolutionStatus": data.get("umaResolutionStatus"),
                "umaResolutionStatuses": data.get("umaResolutionStatuses"),
                "outcomes": parse_json_field(data.get("outcomes"), []),
                "outcomePrices": parse_prices(data.get("outcomePrices")),
            }

    except Exception as e:
        log.error(f"market status error: {e}")
        return None


async def get_market_id_by_slug(slug):
    if not slug:
        return None

    url = f"https://gamma-api.polymarket.com/markets/slug/{urllib.parse.quote(slug)}"

    try:
        async with httpx.AsyncClient(timeout=10) as client_http:
            res = await client_http.get(url)

            if res.status_code != 200:
                return None

            data = res.json()

            if isinstance(data, dict) and data.get("id"):
                log.info(f"[SLUG MATCH] {slug} -> {data.get('id')}")
                return data.get("id")

    except Exception as e:
        log.error(f"slug search error: {e}")

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


# Team aliases for fuzzy matching
TEAM_ALIASES = {
    "knicks": ["knicks", "new york knicks"],
    "hawks": ["hawks", "atlanta hawks"],
    "cavaliers": ["cavaliers", "cleveland cavaliers", "cavs"],
    "raptors": ["raptors", "toronto raptors"],
    "nuggets": ["nuggets", "denver nuggets"],
    "timberwolves": ["timberwolves", "minnesota timberwolves", "wolves"],
    "yankees": ["yankees", "new york yankees"],
    "red sox": ["red sox", "boston red sox"],
    "rockies": ["rockies", "colorado rockies"],
    "mets": ["mets", "new york mets"],
    "brewers": ["brewers", "milwaukee brewers"],
    "tigers": ["tigers", "detroit tigers"],
}


def expand_team_terms(team):
    t = normalize_compact(team)
    return TEAM_ALIASES.get(t, [t])

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


def extract_polymarket_urls(message):
    urls = []

    text = message.raw_text or ""

    for ent in message.entities or []:
        if isinstance(ent, MessageEntityTextUrl):
            if "polymarket.com" in ent.url:
                urls.append(ent.url)

        elif isinstance(ent, MessageEntityUrl):
            raw_url = text[ent.offset: ent.offset + ent.length]
            if "polymarket.com" in raw_url:
                urls.append(raw_url)

    return urls


def extract_slug_from_polymarket_url(url):
    try:
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]

        # Ejemplo: /event/slug
        if "event" in parts:
            i = parts.index("event")
            if i + 1 < len(parts):
                return parts[i + 1]

        # Ejemplo: /market/slug si viniera así
        if "market" in parts:
            i = parts.index("market")
            if i + 1 < len(parts):
                return parts[i + 1]

    except Exception:
        return None

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


async def clear_bad_market_id(client_http, alert, reason: str):
    alert_id = alert.get("id")

    if not alert_id:
        return

    log.warning(f"[CLEAR_MARKET_ID] alert={alert_id} reason={reason}")

    await client_http.post(
        BACKEND_URL + "/api/alerts/update",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Bot-Api-Key": os.getenv("BOT_API_KEY"),
        },
        json={
            "id": alert_id,
            "marketId": None,
            "resolved": False,
            "result": None,
            "isWin": None,
        },
    )


def get_alert_market_title(alert):
    return (
        alert.get("marketTitle")
        or alert.get("market_title")
        or alert.get("question")
        or ""
    )


async def audit_existing_market_ids(limit=1000):
    if not BACKEND_URL:
        log.warning("[AUDIT] BACKEND_URL not set")
        return

    log.info("[AUDIT] Starting marketId audit...")

    try:
        async with httpx.AsyncClient(timeout=15) as client_http:
            res = await client_http.get(
                BACKEND_URL + f"/api/alerts?limit={limit}",
                headers={
                    "X-Bot-Api-Key": os.getenv("BOT_API_KEY"),
                },
            )

            payload = res.json()
            alerts = payload.get("data", [])

            checked = 0
            kept = 0
            cleared = 0

            for alert in alerts:
                market_id = alert.get("marketId") or alert.get("market_id")

                if not market_id:
                    continue

                market_title = get_alert_market_title(alert)

                if not market_title:
                    continue

                checked += 1

                valid, full_market = await is_valid_market_id(
                    client_http,
                    str(market_id),
                )

                if not valid:
                    await clear_bad_market_id(client_http, alert, "CLEAR_404_OR_INVALID")
                    cleared += 1
                    continue

                ok, reason = validate_market_against_alert(market_title, full_market)

                if not ok:
                    await clear_bad_market_id(client_http, alert, reason)
                    cleared += 1
                    continue

                kept += 1
                log.info(f"[KEEP] alert={alert.get('id')} marketId={market_id}")

            log.info(
                f"[AUDIT_DONE] checked={checked} kept={kept} cleared={cleared}"
            )

    except Exception as e:
        log.error(f"[AUDIT_ERROR] {e}")


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


def same_outcome(a, b):
    a = normalize_outcome_name(a)
    b = normalize_outcome_name(b)

    if not a or not b:
        return False

    if a == b:
        return True

    # Evita que "no" haga match parcial con palabras largas.
    if len(a) > 3 and len(b) > 3:
        if a in b or b in a:
            return True

    aw = get_words(a)
    bw = get_words(b)

    if not aw or not bw:
        return False

    overlap = len(aw & bw)
    return overlap / min(len(aw), len(bw)) >= 0.75


def is_market_final(market_data):
    if market_data.get("closed") is True:
        return True

    status = str(market_data.get("umaResolutionStatus") or "").lower()
    statuses = str(market_data.get("umaResolutionStatuses") or "").lower()

    # "proposed" todavía NO es final.
    if "resolved" in status or "resolved" in statuses:
        return True

    return False


def get_winning_outcome(market_data):
    outcomes = market_data.get("outcomes") or []
    prices = market_data.get("outcomePrices") or []

    if not outcomes or not prices:
        return None

    if len(outcomes) != len(prices):
        return None

    valid_prices = [p for p in prices if p is not None]

    if not valid_prices:
        return None

    # Caso 50-50 / void / empate.
    if len(valid_prices) == 2 and abs(valid_prices[0] - 0.5) <= 0.02 and abs(valid_prices[1] - 0.5) <= 0.02:
        return {
            "winner": "50-50",
            "is_push": True,
        }

    max_price = max(valid_prices)

    # No marcar ganador si todavía no hay precio claro.
    if max_price < 0.98:
        return None

    winner_index = prices.index(max_price)

    return {
        "winner": outcomes[winner_index],
        "is_push": False,
    }


def evaluate_result(alert, market_data):
    if not market_data:
        return None

    # No marques win/loss hasta que Polymarket esté cerrado/resuelto.
    if not is_market_final(market_data):
        return None

    winning = get_winning_outcome(market_data)

    if not winning:
        return None

    winner = winning["winner"]

    if winning.get("is_push"):
        return {
            "resolved": True,
            "result": winner,
            "isWin": None,
        }

    answer = alert.get("answer")
    action = str(alert.get("action") or "").upper()

    answer_won = same_outcome(answer, winner)

    # BUY outcome = gana si ese outcome ganó.
    # SELL outcome = gana si ese outcome perdió.
    if action == "SELL":
        is_win = not answer_won
    else:
        is_win = answer_won

    return {
        "resolved": True,
        "result": winner,
        "isWin": is_win,
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
                res = await client_http.get(
                    BACKEND_URL + "/api/alerts?unresolved=true",
                    headers={
                        "X-Bot-Api-Key": os.getenv("BOT_API_KEY"),
                    },
                )
                alerts = res.json().get("data", [])
                market_cache = {}

                for alert in alerts:
                    market_id = alert.get("marketId")

                    if not market_id:
                        market_title = alert.get("market_title") or alert.get("marketTitle")
                        if market_title:
                            resolved_market_id = await get_market_id(market_title)
                            if resolved_market_id:
                                await client_http.post(
                                    BACKEND_URL + "/api/alerts/update",
                                    headers={
                                        "Content-Type": "application/json",
                                        "Accept": "application/json",
                                        "X-Bot-Api-Key": os.getenv("BOT_API_KEY"),
                                    },
                                    json={
                                        "id": alert["id"],
                                        "marketId": resolved_market_id
                                    }
                                )
                                market_id = resolved_market_id

                    if not market_id:
                        continue

                    if market_id in market_cache:
                        market_data = market_cache[market_id]
                    else:
                        market_data = await get_market_status(market_id)
                        market_cache[market_id] = market_data

                    result = evaluate_result(alert, market_data)

                    if not result:
                        continue

                    # 2. actualizar backend
                    await client_http.post(
                        BACKEND_URL + "/api/alerts/update",
                        headers={
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                            "X-Bot-Api-Key": os.getenv("BOT_API_KEY"),
                        },
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

    market_id = None

    urls = extract_polymarket_urls(event.message)
    for url in urls:
        slug = extract_slug_from_polymarket_url(url)
        market_id = await get_market_id_by_slug(slug)
        if market_id:
            break

    if not market_id:
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

    if os.getenv("AUDIT_MARKET_IDS_ON_START") == "true":
        await audit_existing_market_ids(limit=1000)

    await asyncio.gather(
        client.run_until_disconnected(),
        worker_loop()
    )

asyncio.run(main())