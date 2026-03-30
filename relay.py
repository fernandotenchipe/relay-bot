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
    auto_reconnect=True,
    request_retries=3
)

#  COLA GLOBAL (ORDEN)
queue = asyncio.Queue()
# Número de workers concurrentes para procesar la cola (evita cuellos de botella)
WORKERS = int(os.getenv('WORKERS', '3'))
# Semáforo para limitar llamadas simultáneas a OpenAI
SEMAPHORE = asyncio.Semaphore(WORKERS)


#  Traducción mejorada (textos largos)
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
                {"role": "user", "content": text[:4000]}  # límite seguro
            ],
            temperature=0.2,
            max_tokens=1000
        )

    try:
        # Permitir un tiempo razonable para la llamada a la API
        response = await asyncio.wait_for(
            loop.run_in_executor(None, call_openai),
            timeout=18
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return text


#  WORKER (ORDEN GARANTIZADO)
async def worker():
    worker_id = 1
    while True:
        sent, text = await queue.get()
        start = datetime.now()
        try:
            log.info(f"Worker picked message; queue size={queue.qsize()}")

            # Limitar concurrencia de llamadas a OpenAI
            async with SEMAPHORE:
                translated = await translate_text(text)

            if translated != text:
                try:
                    await sent.edit(translated, parse_mode='md')
                except Exception:
                    try:
                        await sent.edit(translated)
                    except Exception as e:
                        log.error(f"Failed to edit message: {e}")

            elapsed = (datetime.now() - start).total_seconds()
            log.info(f"Worker done in {elapsed:.2f}s")

        except Exception as e:
            log.error(f"Worker error: {e}")

        finally:
            queue.task_done()


#  KEEP ALIVE (estabilidad)
async def keep_alive():
    while True:
        try:
            # get_dialogs() fuerza sincronización agresiva de updates
            await client.get_dialogs(limit=1)
            log.debug(f"Keep-alive check OK")
        except Exception as e:
            log.warning(f"Keep-alive error: {e}")
        await asyncio.sleep(5)  # Reducido a 5s para forzar polling más frecuente


#  HANDLER
@client.on(events.NewMessage(chats=SOURCE))
async def handler(event):
    try:
        msg = event.message
        text = getattr(msg, "text", None) or getattr(msg, "message", None) or ""

        if not text:
            return

        ts = datetime.now().strftime("%H:%M:%S")
        
        # Calcular delay desde que fue enviado originalmente
        msg_date = getattr(msg, 'date', None)
        if msg_date:
            try:
                delay = (datetime.utcnow() - msg_date.replace(tzinfo=None)).total_seconds()
                log.info(f"[{ts}] Mensaje recibido (original: {msg_date.strftime('%H:%M:%S')}, delay={delay:.0f}s)")
            except:
                log.info(f"[{ts}] Mensaje recibido")
        else:
            log.info(f"[{ts}] Mensaje recibido")

        #  envío inmediato
        sent = await client.send_message(DEST, text)

        #  encola (ORDEN)
        await queue.put((sent, text))

        log.info(f"[{ts}] Reenviado inmediato (queue_size={queue.qsize()})")

    except Exception as e:
        log.error(f"Error reenviando: {e}")


#  MAIN
async def main():
    print("=== SESION INICIADA ===")

    await client.get_dialogs()

    #  tareas en background
    # Lanzar varios workers para procesar la cola en paralelo
    for i in range(WORKERS):
        asyncio.create_task(worker())
    asyncio.create_task(keep_alive())

    log.info("=== Relay iniciado ===")
    log.info(f"Escuchando canal: {SOURCE}")
    log.info(f"Destino: {DEST}")
    log.info(f"Workers concurrentes: {WORKERS}")

    await client.run_until_disconnected()


#  START
client.start(phone=PHONE)
client.loop.run_until_complete(main())