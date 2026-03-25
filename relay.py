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

client = TelegramClient('session', API_ID, API_HASH)

@client.on(events.NewMessage(chats=SOURCE))
async def handler(event):
    try:
        print("EVENTO DETECTADO")
        msg = event.message
        text = msg.text or msg.caption or ""
        ts = datetime.now().strftime("%H:%M:%S")
        log.info(f"[{ts}] Mensaje recibido: {text[:80]}...")

        # PARTE 1: Copia directa (sin traduccion)
        await client.send_message(DEST, text)

        log.info(f"[{ts}] Reenviado OK")
    except Exception as e:
        log.error(f"Error reenviando: {e}")

log.info("=== Relay iniciado ===")
log.info(f"Escuchando canal: {SOURCE}")
log.info(f"Destino: {DEST}")

with client:
    client.start(phone=PHONE)
    client.run_until_disconnected()
