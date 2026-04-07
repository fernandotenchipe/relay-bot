import os, logging, asyncio
from dotenv import load_dotenv
from telethon import TelegramClient, events
from openai import OpenAI

load_dotenv()

# 🔥 LOGS (archivo + consola)
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

# 🔴 control de flujo: click en Whales y esperar solo su siguiente respuesta
waiting_whales_message = False
waiting_msg_id = None
waiting_text_snapshot = ""


def is_whales_loading_message(text: str) -> bool:
    t = (text or "").strip().lower()
    return (
        "cargando estadisticas de ballenas" in t
        or "cargando estadísticas de ballenas" in t
        or ("loading" in t and "whale" in t)
    )


def is_whales_report_message(text: str) -> bool:
    t = (text or "").strip().lower()
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
                {"role": "system", "content": "Traduce al español manteniendo emojis y formato Markdown."},
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


# 🔹 REFRESH CADA 2 HORAS
async def refresh():
    global waiting_whales_message, waiting_msg_id, waiting_text_snapshot

    while True:
        try:
            # Reinicia el estado en cada ciclo para evitar arrastrar respuestas viejas.
            waiting_whales_message = False
            waiting_msg_id = None
            waiting_text_snapshot = ""
            log.info("Enviando /start al bot...")
            await client.send_message(BOT_USERNAME, "/start")
        except Exception as e:
            log.error(f"Refresh error: {e}")

        await asyncio.sleep(7200)


# 🔹 HANDLER DEL BOT (LO IMPORTANTE)
@client.on(events.NewMessage(from_users=BOT_USERNAME))
@client.on(events.MessageEdited(from_users=BOT_USERNAME))
async def bot_handler(event):
    global waiting_whales_message, waiting_msg_id, waiting_text_snapshot

    msg = event.message
    text = (msg.raw_text or msg.text or "").strip()

    log.info(f"Mensaje del bot recibido ({event.__class__.__name__})")

    # 🔥 Solo click en el menú inicial, nunca se reenvía ese mensaje.
    if msg.buttons and "Prediction Radar" in text:
        whales_button_found = False

        for row in msg.buttons:
            for btn in row:
                btn_text = (btn.text or "").strip()

                # Evita coincidir con "My Whales" o "Archived Whales".
                if btn_text.replace("🐋", "").strip().lower() == "whales":
                    whales_button_found = True
                    await asyncio.sleep(2)
                    try:
                        waiting_whales_message = True
                        waiting_msg_id = msg.id
                        waiting_text_snapshot = text

                        callback = await msg.click(text=btn_text)
                        waiting_whales_message = True
                        log.info("Click en Whales realizado")

                        # Algunos bots responden por callback sin enviar mensaje nuevo.
                        callback_text = (getattr(callback, "message", "") or "").strip()
                        if callback_text:
                            if is_whales_loading_message(callback_text):
                                log.info("Respuesta intermedia de Whales detectada; esperando reporte final")
                            elif is_whales_report_message(callback_text):
                                await queue.put(callback_text)
                                waiting_whales_message = False
                                waiting_msg_id = None
                                waiting_text_snapshot = ""
                                log.info("Respuesta callback de Whales encolada")
                            else:
                                log.info("Callback sin reporte de Whales; esperando siguiente mensaje")
                    except Exception as e:
                        waiting_whales_message = False
                        waiting_msg_id = None
                        waiting_text_snapshot = ""
                        log.warning(f"Click falló: {e}")

                    return

        if not whales_button_found:
            log.warning("No se encontró botón Whales en el menú")

        return

    # 🔥 Solo reenvía el primer mensaje posterior al click en Whales.
    if waiting_whales_message and text:
        # Ignora la misma tarjeta del menú sin cambios.
        if msg.id == waiting_msg_id and text == waiting_text_snapshot:
            return

        if is_whales_loading_message(text):
            log.info("Mensaje de carga detectado; esperando reporte final")
            return

        if not is_whales_report_message(text):
            log.info("Mensaje ignorado mientras se espera reporte de Whales")
            return

        await queue.put(text)
        waiting_whales_message = False
        waiting_msg_id = None
        waiting_text_snapshot = ""
        log.info("Respuesta de Whales encolada")


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