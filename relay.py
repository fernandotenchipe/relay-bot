import os, logging, asyncio, random
from dotenv import load_dotenv
from telethon import TelegramClient, events
from openai import OpenAI

load_dotenv()

LISTEN_ALL = True

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')
DEST = int(os.getenv('DEST_CHANNEL'))
OPENAI_KEY = os.getenv('OPENAI_API_KEY')

BOT_USERNAME = "predictionradar_bot"

client_ai = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
client = TelegramClient('session', API_ID, API_HASH, auto_reconnect=True)

queue = asyncio.Queue()
waiting_whales = False


# =========================
# 🔥 TRADUCCIÓN INTELIGENTE
# =========================

async def translate_text(text: str) -> str:
    t = text.lower()

    # ⚡ LISTA WHALES → SIN OPENAI (instantáneo)
    if "whales (" in t and "recent trades" not in t:
        return fast_translate(text)

    # 🧠 DETALLE → OPENAI CONTROLADO
    if "recent trades" in t or "open positions" in t:
        return await detailed_translate(text)

    return text


# ⚡ TRADUCCIÓN LIMPIA (NO rompe formato)
def fast_translate(text):
    replacements = {
        "Whales": "Ballenas",
        "Volume": "Vol",
        "Last active": "Hace",
        "ago": "",
    }

    result = text
    for en, es in replacements.items():
        result = result.replace(en, es)

    return result


# 🧠 TRADUCCIÓN CONTROLADA (sin cortar texto)
async def detailed_translate(text):
    if not client_ai:
        return text

    loop = asyncio.get_running_loop()

    def call():
        return client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Traduce al español manteniendo EXACTAMENTE el formato, saltos de línea y emojis. "
                        "NO cambies números, NO cortes texto, NO resumas."
                    )
                },
                {
                    "role": "user",
                    "content": text  # 🔥 SIN RECORTE
                }
            ],
            temperature=0,
            max_tokens=800
        )

    try:
        res = await loop.run_in_executor(None, call)
        return res.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return text


# =========================
# 🔹 WORKER
# =========================

async def worker():
    while True:
        text = await queue.get()

        try:
            translated = await translate_text(text)
            await client.send_message(DEST, translated)
            log.info("Mensaje enviado")

        except Exception as e:
            log.error(f"Worker error: {e}")

        finally:
            queue.task_done()


# =========================
# 🔹 CLICK WHALES
# =========================

async def trigger_whales():
    global waiting_whales

    try:
        messages = await client.get_messages(BOT_USERNAME, limit=1)
        msg = messages[0]

        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    if "whales" in (btn.text or "").lower():
                        await asyncio.sleep(random.uniform(2,5))
                        waiting_whales = True
                        await msg.click(text=btn.text)
                        log.info("Click Whales")
                        return

    except Exception as e:
        log.error(f"Trigger error: {e}")


# =========================
# 🔹 HOME
# =========================

async def go_home():
    try:
        messages = await client.get_messages(BOT_USERNAME, limit=1)
        msg = messages[0]

        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    if "home" in (btn.text or "").lower():
                        await asyncio.sleep(random.uniform(2,4))
                        await msg.click(text=btn.text)
                        log.info("Click Home")
                        return

    except Exception as e:
        log.error(f"Home error: {e}")


# =========================
# 🔁 LOOP
# =========================

async def loop_whales():
    while True:
        await trigger_whales()
        await asyncio.sleep(random.uniform(600,1200))


# =========================
# 🔹 HANDLER
# =========================

@client.on(events.NewMessage(from_users=BOT_USERNAME))
@client.on(events.MessageEdited(from_users=BOT_USERNAME))
async def bot_handler(event):
    global waiting_whales

    raw = event.message.raw_text or ""
    text = raw.lower()

    if "cargando" in text:
        return

    # 🔥 MANUAL
    if LISTEN_ALL:
        if "pnl" in text or "whales (" in text or "recent trades" in text:
            await queue.put(raw)
            return

    # 🔽 AUTOMÁTICO
    if waiting_whales:
        if "whales (" in text:
            await queue.put(raw)
            waiting_whales = False
            asyncio.create_task(go_home())


# =========================
# 🔹 MAIN
# =========================

async def main():
    await client.get_dialogs()

    asyncio.create_task(worker())
    asyncio.create_task(loop_whales())

    log.info("BOT CORRIENDO")
    await client.run_until_disconnected()


client.start(phone=PHONE)
client.loop.run_until_complete(main())