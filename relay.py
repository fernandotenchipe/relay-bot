import os, logging
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, events

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

print("=== INICIANDO SCRIPT ===")

client = TelegramClient('session', API_ID, API_HASH)

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

        await client.send_message(DEST, text)

        log.info(f"[{ts}] Reenviado OK")

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