import os, logging, asyncio, random, hashlib
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
                    await human_delay(2, 5)  # 🧠 pensar antes de click
                    state.pending = wait_for
                    state.last_msg_id = msg.id
                    await msg.click(text=btn.text)
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
# ENSURE HOME
# =========================
async def ensure_home():
    for _ in range(3):
        msg = await navigator.get_msg()
        if not msg or not msg.buttons:
            return

        for row in msg.buttons:
            for btn in row:
                text = (btn.text or "").lower()

                if "whales" in text and "home" not in text:
                    return

                if "back" in text or "home" in text:
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
# TRANSLATE
# =========================
async def translate_text(text):
    t = text.lower()

    if "whales (" in t and "recent trades" not in t:
        return text

    if "recent trades" in t:
        return await detailed_translate(text)

    return text

async def detailed_translate(text):
    if not client_ai:
        return text

    loop = asyncio.get_running_loop()

    def call():
        return client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Traduce al español manteniendo formato exacto."},
                {"role": "user", "content": text}
            ],
            temperature=0,
            max_tokens=700
        )

    try:
        res = await loop.run_in_executor(None, call)
        return res.choices[0].message.content.strip()
    except:
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
        except:
            await client.send_message(DEST, text)
        queue.task_done()

# =========================
# HANDLER
# =========================
@client.on(events.NewMessage(from_users=BOT_USERNAME))
@client.on(events.MessageEdited(from_users=BOT_USERNAME))
async def handler(event):
    msg = event.message
    text = msg.raw_text or ""

    if "cargando" in text.lower():
        return

    if state.last_msg_id and msg.id != state.last_msg_id:
        return

    if dedup.is_duplicate(text):
        return

    if state.pending:
        if state.pending.lower() in text.lower():
            state.pending = None
            await queue.put(text)
            return

    if "pnl" in text.lower() or "recent trades" in text.lower():
        await queue.put(text)

# =========================
# EXPLORE
# =========================
async def explore_whales(limit=3):
    msg = await navigator.get_msg()
    if not msg or not msg.buttons:
        return

    whale_buttons = [
        btn.text for row in msg.buttons
        for btn in row
        if btn.text and "home" not in btn.text.lower() and "back" not in btn.text.lower()
    ]

    for label in whale_buttons[:limit]:

        await human_delay(2, 6)

        ok = await navigator.click(label, "pnl")
        if not ok:
            continue

        await wait_for_content("pnl")
        await human_delay(5, 12)  # 🧠 leer

        if random.random() < 0.2:
            await human_delay(5, 10)  # distracción

        await navigator.click("back", "whales (")
        await wait_for_content("whales (")
        await human_delay(2, 5)

# =========================
# LOOP
# =========================
async def crawler_loop():
    while True:
        try:
            log.info("CRAWLER LOOP")

            await ensure_home()
            await human_delay(2, 6)

            ok = await navigator.go_whales()
            if not ok:
                await asyncio.sleep(30)
                continue

            await wait_for_content("whales (")
            await human_delay(5, 12)

            await explore_whales(limit=3)

            await navigator.go_home()
            await human_delay(2, 5)

            ok = await navigator.go_winning()
            if ok:
                await human_delay(3, 8)
                await navigator.go_home()

            await asyncio.sleep(random.uniform(600, 10800))

        except Exception as e:
            log.error(e)
            await asyncio.sleep(60)

# =========================
# MAIN
# =========================
async def main():
    await client.get_dialogs()
    asyncio.create_task(worker())
    asyncio.create_task(crawler_loop())
    log.info("BOT CORRIENDO")
    await client.run_until_disconnected()

client.start(phone=PHONE)
client.loop.run_until_complete(main())