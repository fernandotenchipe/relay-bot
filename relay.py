import os, logging, asyncio, random, hashlib, re
from collections import OrderedDict
from dotenv import load_dotenv
from telethon import TelegramClient, events
from openai import OpenAI

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')
DEST = int(os.getenv('DEST_CHANNEL'))
OPENAI_KEY = os.getenv('OPENAI_API_KEY')

BOT_USERNAME = "predictionradar_bot"

client = TelegramClient('session', API_ID, API_HASH, auto_reconnect=True)
client_ai = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

queue = asyncio.Queue()

sent_whales_this_cycle = False
sent_winning_this_cycle = False
cycle_running = False

# =========================
# HUMAN DELAY
# =========================
async def human_delay(min_s=1.5, max_s=4.5):
    await asyncio.sleep(random.uniform(min_s, max_s))

# =========================
# STATE
# =========================
class State:
    def __init__(self):
        self.pending = None
        self.last_msg_id = None

state = State()

# =========================
# DEDUP
# =========================
class Dedup:
    def __init__(self, max_size=1000):
        self.cache = OrderedDict()
        self.max = max_size

    def is_duplicate(self, text):
        h = hashlib.md5(text.encode()).hexdigest()
        if h in self.cache:
            return True
        self.cache[h] = True
        if len(self.cache) > self.max:
            self.cache.popitem(last=False)
        return False

dedup = Dedup()

# =========================
# NAVIGATOR
# =========================
class Navigator:

    async def get_msg(self):
        msgs = await client.get_messages(BOT_USERNAME, limit=5)
        for m in msgs:
            if m.buttons:
                return m
        return None

    async def click(self, text, wait_for=None):
        msg = await self.get_msg()
        if not msg or not msg.buttons:
            return False

        for row in msg.buttons:
            for btn in row:
                if text.lower() in (btn.text or "").lower():

                    await human_delay(2, 5)

                    msg = await self.get_msg()
                    if not msg:
                        return False

                    state.pending = wait_for
                    state.last_msg_id = msg.id

                    try:
                        await msg.click(text=btn.text)
                    except Exception as e:
                        log.warning(f"Click falló: {e}")
                        return False

                    return True

        return False

    async def go_home(self):
        return await self.click("home")

    async def go_whales(self):
        return await self.click("whales", "whales (")

    async def go_winning(self):
        return await self.click("winning", "winning")

navigator = Navigator()

# =========================
# REVIVE BOT
# =========================
async def revive_bot():
    await human_delay(3, 8)
    await client.send_message(BOT_USERNAME, "/start")

    for _ in range(10):
        await asyncio.sleep(random.uniform(1, 2))
        msg = await navigator.get_msg()
        if msg and msg.buttons:
            return True
    return False

# =========================
# ENSURE HOME
# =========================
async def ensure_home():
    msg = await navigator.get_msg()

    if not msg or not msg.buttons:
        await revive_bot()
        return

    for _ in range(3):
        msg = await navigator.get_msg()
        if not msg or not msg.buttons:
            return

        for row in msg.buttons:
            for btn in row:
                t = (btn.text or "").lower()

                if "whales" in t and "home" not in t:
                    return

                if "back" in t or "home" in t:
                    await human_delay(1.5, 3)
                    await msg.click(text=btn.text)
                    await human_delay(2, 4)
                    break

# =========================
# WAIT
# =========================
async def wait_for_content(keyword, timeout=10):
    end = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < end:
        msg = await navigator.get_msg()
        if msg and keyword.lower() in (msg.raw_text or "").lower():
            return True
        await asyncio.sleep(0.8)

    return False

# =========================
# NOMBRES (FIX REAL)
# =========================
def adapt_whale_names(text):
    replacements = {
        "Sports Grinder": "Analista Deportivo",
        "Soccer Esports Titan": "Titán Fútbol Esports",
        "NBA Volume Trader": "Operador NBA",
        "Esports NBA Dualist": "Dualista NBA Esports",
        "Everything Trader": "Operador Global",
        "Global Sports Arb": "Arbitraje Deportivo Global",
        "Sports Focused": "Especialista Deportivo",
        "Geopolitical Macro": "Macro Geopolítico"
    }

    for eng, esp in replacements.items():
        text = re.sub(rf"\b{re.escape(eng)}\b", esp, text)

    return text

# =========================
# TITULOS
# =========================
async def adapt_all_titles(text):
    if not client_ai:
        return text

    matches = re.findall(r'"(.*?)"', text)
    if not matches:
        return text

    loop = asyncio.get_running_loop()

    def call():
        return client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Traduce al español manteniendo formato."},
                {"role": "user", "content": "\n".join(matches)}
            ],
            temperature=0.2,
            max_tokens=300
        )

    try:
        res = await loop.run_in_executor(None, call)
        translated = res.choices[0].message.content.strip().split("\n")

        for orig, new in zip(matches, translated):
            text = text.replace(f'"{orig}"', f'"{new}"')

        return text

    except Exception:
        return text

# =========================
# CLEAN ALERT
# =========================
def clean_whale_alert(text: str) -> str:
    lines = text.split("\n")
    clean = []

    for line in lines:
        if "View on Polymarket" in line:
            break
        if "http" in line:
            continue
        if "New to Polymarket" in line:
            continue
        clean.append(line)

    return "\n".join(clean).strip()

# =========================
# TRANSLATE (ORDEN CORRECTO)
# =========================
async def translate_text(text):
    t = text.lower()

    if "whales (" in t:
        text = await adapt_all_titles(text)
        text = adapt_whale_names(text)
        return text

    if "recent trades" in t or "open positions" in t or "latest winning plays" in t:
        text = await detailed_translate(text)
        text = await adapt_all_titles(text)
        text = adapt_whale_names(text)
        return text

    if "whale alert" in t:
        text = await adapt_all_titles(text)
        text = adapt_whale_names(text)
        return text

    return text

async def detailed_translate(text):
    if not client_ai:
        return text

    loop = asyncio.get_running_loop()

    def call():
        return client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Traduce todo al español manteniendo formato."},
                {"role": "user", "content": text}
            ],
            temperature=0,
            max_tokens=700
        )

    try:
        res = await loop.run_in_executor(None, call)
        return res.choices[0].message.content.strip()
    except Exception:
        return text

# =========================
# WORKER
# =========================
async def worker():
    while True:
        text = await queue.get()
        try:
            translated = await translate_text(text)
            await client.send_message(DEST, translated)
        except Exception:
            await client.send_message(DEST, text)
        queue.task_done()

# =========================
# FORCE CYCLE
# =========================
async def force_cycle():
    global cycle_running

    if cycle_running:
        return

    cycle_running = True

    await asyncio.sleep(random.uniform(5, 10))

    try:
        await client.send_message(BOT_USERNAME, "/start")
        await asyncio.sleep(random.uniform(3, 5))

        await ensure_home()
        await human_delay(3, 6)

        if await navigator.go_whales():
            await human_delay(5, 10)
            await explore_whales()

        await navigator.go_home()

        if await navigator.go_winning():
            await human_delay(6, 12)
            await navigator.go_home()

    except Exception as e:
        log.error(f"force_cycle error: {e}")

    finally:
        cycle_running = False

# =========================
# HANDLER
# =========================
@client.on(events.NewMessage(from_users=BOT_USERNAME))
@client.on(events.MessageEdited(from_users=BOT_USERNAME))
async def handler(event):
    global sent_whales_this_cycle, sent_winning_this_cycle

    text = event.message.raw_text or ""
    t = text.lower()

    if "cargando" in t:
        return

    if dedup.is_duplicate(text):
        return

    if "whale alert" in t:
        await asyncio.sleep(1.0)
        msg2 = await navigator.get_msg()
        final_text = msg2.raw_text if msg2 else text

        final_text = clean_whale_alert(final_text)
        final_text = await adapt_all_titles(final_text)
        final_text = adapt_whale_names(final_text)

        await queue.put(final_text)

        await force_cycle()
        return

    if "whales (" in t and not sent_whales_this_cycle:
        sent_whales_this_cycle = True
        await asyncio.sleep(1.5)
        msg2 = await navigator.get_msg()
        await queue.put(msg2.raw_text if msg2 else text)
        return

    if "recent trades" in t or "open positions" in t:
        await asyncio.sleep(1.5)
        msg2 = await navigator.get_msg()
        await queue.put(msg2.raw_text if msg2 else text)
        return

    if "latest winning plays" in t and not sent_winning_this_cycle:
        sent_winning_this_cycle = True
        await asyncio.sleep(1.5)
        msg2 = await navigator.get_msg()
        await queue.put(msg2.raw_text if msg2 else text)
        return

# =========================
# EXPLORE
# =========================
async def explore_whales(limit=9):
    msg = await navigator.get_msg()
    if not msg or not msg.buttons:
        return

    whale_buttons = [
        btn.text for row in msg.buttons
        for btn in row
        if btn.text and "home" not in btn.text.lower() and "back" not in btn.text.lower()
    ]

    random.shuffle(whale_buttons)

    for label in whale_buttons[:limit]:
        await human_delay(3, 7)

        ok = await navigator.click(label, "pnl")
        if not ok:
            continue

        await wait_for_content("pnl")
        await human_delay(5, 12)

        await navigator.click("back", "whales (")

# =========================
# LOOP
# =========================
async def crawler_loop():
    global sent_whales_this_cycle, sent_winning_this_cycle

    while True:
        sent_whales_this_cycle = False
        sent_winning_this_cycle = False

        msg = await navigator.get_msg()
        if not msg:
            await revive_bot()
            continue

        await ensure_home()
        await human_delay(2, 6)

        if await navigator.go_whales():
            await human_delay(5, 10)
            await explore_whales()

        await navigator.go_home()

        if await navigator.go_winning():
            await human_delay(6, 12)
            await navigator.go_home()

        await asyncio.sleep(random.uniform(600, 10800))

# =========================
# MAIN
# =========================
async def main():
    await client.get_dialogs()
    asyncio.create_task(worker())
    asyncio.create_task(crawler_loop())
    await client.run_until_disconnected()

client.start(phone=PHONE)
client.loop.run_until_complete(main())