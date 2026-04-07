import os, logging, asyncio, random
from dotenv import load_dotenv
from telethon import TelegramClient, events
from openai import OpenAI

load_dotenv()

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

# 🔴 estado
waiting_whales = False


# 🔹 Traducción
async def translate_text(text: str) -> str:
    if not client_ai:
        return text

    loop = asyncio.get_running_loop()

    def call_openai():
        return client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Traduce al español manteniendo emojis y formato."},
                {"role": "user", "content": text[:4000]}
            ],
            temperature=0.2
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


# 🔹 CLICK EN WHALES (sin /start)
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


# 🔁 LOOP NATURAL
async def loop_whales():
    while True:
        await trigger_whales()
        await asyncio.sleep(random.uniform(600,1200))  # 10–20 min


# 🔹 HANDLER (LO IMPORTANTE)
@client.on(events.NewMessage(from_users=BOT_USERNAME))
@client.on(events.MessageEdited(from_users=BOT_USERNAME))
async def bot_handler(event):
    global waiting_whales

    msg = event.message
    text = (msg.raw_text or msg.text or "").strip().lower()

    log.info(f"Mensaje del bot ({event.__class__.__name__})")

    if waiting_whales and text:

        # ❌ ignorar loading
        if "cargando" in text or "loading" in text:
            return

        # ✅ SOLO esto importa
        if "whales (" in text and "pnl" in text:
            await queue.put(msg.raw_text or msg.text)
            waiting_whales = False
            log.info("Reporte real enviado")
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