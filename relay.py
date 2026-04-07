import os, logging, asyncio, random, re
from dotenv import load_dotenv
from telethon import TelegramClient, events
from openai import OpenAI

load_dotenv()

# 🔥 CONFIG
LISTEN_ALL = True

# 🔥 LOGS
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("relay.log"),
        logging.StreamHandler()
    ]
)

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


# 🔹 OPTIMIZACIONES CLAVE
def trim_text(text):
    return text[:1200]

def should_translate(text):
    t = text.lower()
    if "pnl" in t and "$" in t and "recent trades" not in t:
        return False
    return True

def clean_text(text):
    return re.sub(r'[^\x00-\x7F]+', '', text)


# 🔹 Traducción optimizada
async def translate_text(text: str) -> str:
    if not client_ai:
        return text

    if not should_translate(text):
        return text

    loop = asyncio.get_running_loop()

    def call_openai():
        return client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Traduce al español."},
                {"role": "user", "content": trim_text(text)}
            ],
            temperature=0,
            max_tokens=500
        )

    try:
        response = await loop.run_in_executor(None, call_openai)
        return response.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return text


# 🔹 WORKER
async def worker():
    while True:
        text = await queue.get()

        try:
            translated = await translate_text(text)

            try:
                await client.send_message(DEST, translated, parse_mode='md')
            except:
                await client.send_message(DEST, translated)

            log.info("Mensaje reenviado")

        except Exception as e:
            log.error(f"Worker error: {e}")

        finally:
            queue.task_done()


# 🔹 CLICK EN WHALES
async def trigger_whales():
    global waiting_whales

    try:
        messages = await client.get_messages(BOT_USERNAME, limit=1)
        msg = messages[0]

        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    btn_text = (btn.text or "").strip()

                    if btn_text.replace("🐋", "").strip().lower() == "whales":
                        await asyncio.sleep(random.uniform(2,5))

                        waiting_whales = True
                        await msg.click(text=btn_text)

                        log.info("Click en Whales")
                        return

    except Exception as e:
        log.error(f"Trigger error: {e}")


# 🔹 VOLVER A HOME
async def go_home():
    try:
        messages = await client.get_messages(BOT_USERNAME, limit=1)
        msg = messages[0]

        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    btn_text = (btn.text or "").lower()

                    if "home" in btn_text:
                        await asyncio.sleep(random.uniform(2,4))
                        await msg.click(text=btn.text)
                        log.info("Click en Home")
                        return

    except Exception as e:
        log.error(f"Home error: {e}")


# 🔁 LOOP
async def loop_whales():
    while True:
        await trigger_whales()
        await asyncio.sleep(random.uniform(600,1200))


# 🔹 HANDLER
@client.on(events.NewMessage(from_users=BOT_USERNAME))
@client.on(events.MessageEdited(from_users=BOT_USERNAME))
async def bot_handler(event):
    global waiting_whales

    msg = event.message
    raw = msg.raw_text or msg.text or ""
    text = raw.lower().strip()

    log.info(f"Mensaje del bot ({event.__class__.__name__})")

    # 🔥 ESCUCHA MANUAL
    if LISTEN_ALL and text:

        if "cargando" in text:
            return

        if "pnl" in text or "recent trades" in text or "whales (" in text:
            await queue.put(raw)
            log.info("Actividad manual detectada")
            return

    # 🔽 AUTOMÁTICO
    if waiting_whales and text:

        if "cargando" in text:
            return

        if "whales (" in text and "pnl" in text:
            await queue.put(raw)
            waiting_whales = False
            log.info("Reporte automático enviado")

            asyncio.create_task(go_home())
            return


# 🔹 MAIN
async def main():
    await client.get_dialogs()

    asyncio.create_task(worker())
    asyncio.create_task(loop_whales())

    log.info("=== BOT CORRIENDO ===")

    await client.run_until_disconnected()


# 🔥 START
client.start(phone=PHONE)
client.loop.run_until_complete(main())