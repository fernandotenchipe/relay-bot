import os
from dotenv import load_dotenv
from telethon.sync import TelegramClient

load_dotenv()
client = TelegramClient('session', int(os.getenv('API_ID')), os.getenv('API_HASH'))

with client:
    client.start(phone=os.getenv('PHONE'))
    for dialog in client.iter_dialogs():
        if dialog.is_channel or dialog.is_group:
            print(f"{dialog.name:40s} | ID: {dialog.id}")
