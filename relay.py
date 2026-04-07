import os, logging, asyncio
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

client = TelegramClient(
    'session',
    API_ID,
    API_HASH,
    auto_reconnect=True
)

queue = asyncio.Queue()

# 🔴 estado simple
waiting_whales = False


def is_whales_loading_message(text: str) -> bool:
    t = (text or "").lower()
    return "cargando" in t or ("loading" in t and "whale" in t)


def is_whales_report_message(text: str) -> bool:
    t = (text or "").lower()
    return "whales (" in t and "pnl:" in t and "vol:" in t


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


# 🔹 REFRESH
async def refresh():
    global waiting_whales

    while True:
        try:
            waiting_whales = False
            log.info("Enviando /start al bot...")
            await client.send_message(BOT_USERNAME, "/start")
        except Exception as e:
            log.error(f"Refresh error: {e}")

        await asyncio.sleep(7200)


# 🔹 HANDLER (ARREGLADO)
@client.on(events.NewMessage(from_users=BOT_USERNAME))
@client.on(events.MessageEdited(from_users=BOT_USERNAME))
async def bot_handler(event):
    global waiting_whales

    msg = event.message
    text = (msg.raw_text or msg.text or "").strip()

    log.info(f"Mensaje del bot recibido ({event.__class__.__name__})")

    # 🔥 Detectar menú y hacer click en Whales
    if msg.buttons and "Prediction Radar" in text:
        for row in msg.buttons:
            for btn in row:
                btn_text = (btn.text or "").strip()

                if btn_text.replace("🐋", "").strip().lower() == "whales":
                    await asyncio.sleep(2)

                    try:
                        waiting_whales = True
                        await msg.click(text=btn_text)
                        log.info("Click en Whales realizado")
                    except Exception as e:
                        waiting_whales = False
                        log.warning(f"Click falló: {e}")

                    return
        return

    # 🔥 Esperando respuesta real
    if waiting_whales and text:

        # ❌ ignorar loading
        if is_whales_loading_message(text):
            log.info("Ignorando mensaje de carga")
            return

        # ✅ solo aceptar reporte
        if is_whales_report_message(text):
            await queue.put(text)
            waiting_whales = False
            log.info("Reporte de Whales encolado")
            return

        log.info("Mensaje ignorado (no es reporte)")


# 🔹 MAIN
async def main():
    await client.get_dialogs()

    asyncio.create_task(worker())
    asyncio.create_task(refresh())

    log.info("=== BOT CORRIENDO ===")

    await client.run_until_disconnected()


# 🔥 START
client.start(phone=PHONE)
client.loop.run_until_complete(main())