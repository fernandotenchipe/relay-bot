import os, logging, asyncio
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, events
from openai import OpenAI

load_dotenv()

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
SOURCE = int(os.getenv('SOURCE_CHANNEL'))
DEST = int(os.getenv('DEST_CHANNEL'))
OPENAI_KEY = os.getenv('OPENAI_API_KEY')

client_ai = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

if not OPENAI_KEY:
    log.warning('OPENAI_API_KEY no configurada. Traducción desactivada.')

print("=== INICIANDO SCRIPT ===")

# 🔥 CLIENT CONFIG MÁS ESTABLE
client = TelegramClient(
    'session',
    API_ID,
    API_HASH,
    connection_retries=None,
    retry_delay=2,
    auto_reconnect=True,
    request_retries=5,
    flood_sleep_threshold=60
)


# 🔹 KEEP ALIVE (CLAVE)
async def keep_alive():
    while True:
        try:
            await client.get_me()
            log.info("ping")
        except Exception as e:
            log.warning(f"keepalive error: {e}")
        await asyncio.sleep(25)


# 🔹 Traducción NO bloqueante
async def translate_text(text: str) -> str:
    if not client_ai:
        return text

    loop = asyncio.get_running_loop()

    def call_openai():
        return client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Traduce al español manteniendo emojis y formato Markdown."},
                {"role": "user", "content": text}
            ],
            temperature=0.2,
            max_tokens=300
        )

    try:
        response = await loop.run_in_executor(None, call_openai)
        return response.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return text


# 🔹 Handler ultra rápido
@client.on(events.NewMessage(chats=SOURCE))
async def handler(event):
    try:
        msg = event.message

        # 🔥 FIX caption error
        text = getattr(msg, "text", None) or getattr(msg, "message", None) or ""

        if not text:
            return

        ts = datetime.now().strftime("%H:%M:%S")
        log.info(f"[{ts}] Mensaje recibido")

        # 🚀 ENVÍA INMEDIATO
        sent = await client.send_message(DEST, text)

        # 🚀 TRADUCE EN BACKGROUND
        async def process_translation():
            translated = await translate_text(text)
            if translated != text:
                try:
                    await sent.edit(translated, parse_mode='md')
                except Exception as e:
                    log.error(f"Error editando mensaje: {e}")

        asyncio.create_task(process_translation())

        log.info(f"[{ts}] Reenviado inmediato")

    except Exception as e:
        log.error(f"Error reenviando: {e}")


# 🔹 MAIN
async def main():
    print("=== SESION INICIADA ===")

    await client.get_dialogs()

    # 🔥 MANTENER CONEXIÓN VIVA
    asyncio.create_task(keep_alive())

    log.info("=== Relay iniciado ===")
    log.info(f"Escuchando canal: {SOURCE}")
    log.info(f"Destino: {DEST}")

    await client.run_until_disconnected()


# 🔥 START
client.start(phone=PHONE)
client.loop.run_until_complete(main())