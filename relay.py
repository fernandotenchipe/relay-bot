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

client = TelegramClient(
    'session',
    API_ID,
    API_HASH,
    connection_retries=None,
    retry_delay=2,
    auto_reconnect=True
)


# 🔹 Traducción NO bloqueante
async def translate_text(text: str) -> str:
    if not client_ai:
        return text

    loop = asyncio.get_running_loop()

    def call_openai():
        return client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Traduce al español manteniendo emojis y formato Markdown."
                },
                {"role": "user", "content": text}
            ],
            temperature=0.2,
            max_tokens=300
        )

    try:
        response = await asyncio.wait_for(
            loop.run_in_executor(None, call_openai),
            timeout=6
        )
        return response.choices[0].message.content.strip()
    except asyncio.TimeoutError:
        log.warning("OpenAI timeout > 6s, se mantiene texto original")
        return text
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return text


# 🔹 Handler ultra rápido
@client.on(events.NewMessage(chats=SOURCE))
async def handler(event):
    try:
        msg = event.message
        text = msg.text or msg.caption or ""

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
                    log.warning(f"Error editando con markdown, reintentando sin formato: {e}")
                    try:
                        await sent.edit(translated)
                    except Exception as e2:
                        log.error(f"Error editando mensaje: {e2}")

        asyncio.create_task(process_translation())

        log.info(f"[{ts}] Reenviado inmediato")

    except Exception as e:
        log.error(f"Error reenviando: {e}")


# 🔹 MAIN
async def main():
    print("=== SESION INICIADA ===")

    await client.get_dialogs()

    log.info("=== Relay iniciado ===")
    log.info(f"Escuchando canal: {SOURCE}")
    log.info(f"Destino: {DEST}")

    await client.run_until_disconnected()


# 🔥 START
client.start(phone=PHONE)
client.loop.run_until_complete(main())