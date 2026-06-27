import sys
import threading
import asyncio # <--- IMPORTANTE: Adicione isso!
import os
from dotenv import load_dotenv

# --- FIX PARA WINDOWS ---
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import ai_brain
from discord_bot import bot

load_dotenv()

def run_flask():
    print("--- [DEBUG] Iniciando Flask ---")
    ai_brain.app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

if __name__ == '__main__':
    print("--- [DEBUG] O main.py começou a rodar! ---")
    
    # Inicia o Flask
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Inicia o Discord
    print("--- [DEBUG] Iniciando Discord Bot ---")
    token = os.getenv("DISCORD_TOKEN")
    if token:
        try:
            bot.run(token) # Agora o bot roda APENAS aqui
        except Exception as e:
            print(f"❌ O bot parou com o erro: {e}")
    else:
        print("❌ ERRO: Token não encontrado no .env")