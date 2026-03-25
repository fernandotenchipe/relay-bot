import os, logging
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

# Cliente OpenAI nuevo
client_ai = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

if not OPENAI_KEY:
    log.warning('OPENAI_API_KEY no configurada. Traducción desactivada.')

print("=== INICIANDO SCRIPT ===")

client = TelegramClient('session', API_ID, API_HASH)


async def translate_text(text: str) -> str:
    if not client_ai:
        return text

    try:
        response = client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Traduce al español manteniendo emojis y formato Markdown. "
                        "No traduzcas código ni bloques ```."
                    )
                },
                {"role": "user", "content": text}
            ],
            temperature=0.2
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return text


@client.on(events.NewMessage())
async def handler(event):
    try:
        chat_id = event.chat_id

        log.info(f"Evento detectado en: {chat_id}")

        if chat_id != SOURCE:
            return

        msg = event.message
        text = msg.text or msg.caption or ""

        ts = datetime.now().strftime("%H:%M:%S")
        log.info(f"[{ts}] Mensaje recibido: {text[:80]}...")

        translated = await translate_text(text)

        await client.send_message(DEST, translated, parse_mode='md')

        log.info(f"[{ts}] Reenviado OK (traducido)")

    except Exception as e:
        log.error(f"Error reenviando: {e}")


async def main():
    print("=== SESION INICIADA ===")

    print("📡 Listando canales disponibles:")
    async for dialog in client.iter_dialogs():
        print(f"👉 {dialog.name} | ID: {dialog.id}")

    log.info("=== Relay iniciado ===")
    log.info(f"Escuchando canal: {SOURCE}")
    log.info(f"Destino: {DEST}")

    await client.run_until_disconnected()


with client:
    client.loop.run_until_complete(main())