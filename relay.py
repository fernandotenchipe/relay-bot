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


#  Identificar si es TRADE IDEA con IA
async def is_trade_idea(text: str) -> bool:
    if not client_ai:
        return "TRADE IDEA" in text.upper()

    loop = asyncio.get_running_loop()

    def call_openai():
        return client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """Eres un analizador de mensajes de trading. Tu tarea es determinar si el mensaje es una TRADE IDEA VÁLIDA y LEGÍTIMA.

DESCARTA si:
- Contiene links externos (http, https, t.me, click here, claim, unlock, etc)
- Es promoción o spam (VIP, SPOT, LOCK, PREMIUM, JOIN, mentiones a canales/grupos)
- Es una pregunta (ej: "Which direction? Buy or Sell?")
- Contiene referencias a marcas externas o canales
- Pide que hagas click o se unan a algo

ACEPTA solo si:
- Tiene instrumento claro (XAUUSD, BTC, EURUSD, etc)
- Tiene dirección de entrada (BUY o SELL)
- Tiene al menos 1 TP (Take Profit) o SL (Stop Loss)
- Tiene precio de entrada
- Es contenido educativo/información de tradeo PURO

Responde SOLO con: "yes" si es trade idea legítima, o "no" si no lo es."""
                },
                {"role": "user", "content": text[:2000]}
            ],
            temperature=0,
            max_tokens=10
        )

    try:
        response = await asyncio.wait_for(
            loop.run_in_executor(None, call_openai),
            timeout=10
        )
        result = response.choices[0].message.content.strip().lower()
        is_valid = "yes" in result
        log.info(f"Trade idea check: {is_valid} - Response: {result}")
        return is_valid
    except Exception as e:
        log.error(f"OpenAI trade check error: {e}")
        return "TRADE IDEA" in text.upper()  # fallback a búsqueda de texto


#  WORKER (ORDEN GARANTIZADO)
async def worker():
    worker_id = 1
    while True:
        text = await queue.get()
        start = datetime.now()
        try:
            log.info(f"Worker picked message; queue size={queue.qsize()}")

            # Limitar concurrencia de llamadas a OpenAI
            async with SEMAPHORE:
                translated = await translate_text(text)

            # Enviar el mensaje traducido al canal destino
            try:
                await client.send_message(DEST, translated, parse_mode='md')
                log.info(f"Message sent translated (parse_mode='md')")
            except Exception:
                try:
                    await client.send_message(DEST, translated)
                    log.info(f"Message sent translated (no parse_mode)")
                except Exception as e:
                    log.error(f"Failed to send message: {e}")

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
            # Múltiples acciones para forzar sincronización agresiva
            await client.get_me()
            await client.get_dialogs(limit=1)
            # Forzar sincronización de estados
            await asyncio.sleep(0.5)
            await client.catch_up()
            log.debug(f"Keep-alive check OK")
        except Exception as e:
            log.warning(f"Keep-alive error: {e}")
        await asyncio.sleep(2)  # Reducido a 2s para forzar polling MUCHO más agresivo


#  HANDLER
@client.on(events.NewMessage(chats=SOURCE))
async def handler(event):
    try:
        msg = event.message
        text = getattr(msg, "text", None) or getattr(msg, "message", None) or ""

        if not text:
            return

        ts = datetime.now().strftime("%H:%M:%S")
        
        # Verificar si es TRADE IDEA con IA
        if not await is_trade_idea(text):
            log.info(f"[{ts}] Mensaje descartado (no es trade idea)")
            return
        
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

        #  encola para traducir y enviar (ORDEN GARANTIZADO)
        await queue.put(text)

        log.info(f"[{ts}] Mensaje encolado para traducción y envío (queue_size={queue.qsize()})")

    except Exception as e:
        log.error(f"Error procesando mensaje: {e}")


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