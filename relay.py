import os, logging
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, events

import openai

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

if OPENAI_KEY:
    openai.api_key = OPENAI_KEY
else:
    log.warning('OPENAI_API_KEY no está configurada. Traducciones no estarán disponibles.')

print("=== INICIANDO SCRIPT ===")

client = TelegramClient('session', API_ID, API_HASH)


async def translate_text(text: str) -> str:
    """Translate English->Spanish preserving emojis and Markdown-like formatting.

    We instruct the model to keep emojis and Markdown (bold, italic, inline code, code blocks)
    and to NOT translate contents inside code blocks or inline code.
    The model should return only the translated text (keep same markup and emojis).
    """
    if not OPENAI_KEY:
        return text

    system = (
        "Eres un traductor de inglés a español. Traduce solo el texto y conserva los emojis "
        "y el formato Markdown (**, *, `, ```). No traduzcas el contenido dentro de bloques de código "
        "o código inline. Devuelve únicamente el texto traducido manteniendo los mismos emojis y "
        "marcadores de formato Markdown."
    )

    try:
        resp = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text}
            ],
            temperature=0.2,
            max_tokens=1500,
        )
        translated = resp['choices'][0]['message']['content'].strip()
        return translated
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return text


@client.on(events.NewMessage())
async def handler(event):
    try:
        chat_id = event.chat_id

        print(f"EVENTO DETECTADO EN: {chat_id}")

        if chat_id != SOURCE:
            return

        msg = event.message
        text = msg.text or msg.caption or ""

        ts = datetime.now().strftime("%H:%M:%S")
        log.info(f"[{ts}] Mensaje recibido: {text[:80]}...")

        # Call translator (if configured)
        translated = await translate_text(text)

        # Send translated text using Markdown parsing so formatting is preserved
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