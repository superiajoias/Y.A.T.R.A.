import sys
import threading
import asyncio
import os
from dotenv import load_dotenv
from flask import Flask

# --- FIX PARA WINDOWS ---
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import ai_brain
from discord_bot import bot

load_dotenv()

# --- SETUP DO SERVIDOR WEB (FANTASMA) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "O bot Y.A.T.R.A está vivo e operando!"

def run_flask():
    print("--- [DEBUG] Iniciando Flask na porta 10000 ---")
    # IMPORTANTE: host='0.0.0.0' e port=10000 são OBRIGATÓRIOS para o Render
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)

if __name__ == '__main__':
    print("--- [DEBUG] O main.py começou a rodar! ---")
    
    # Inicia o Flask em uma thread separada
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Inicia o Discord
    print("--- [DEBUG] Iniciando Discord Bot ---")
    token = os.getenv("DISCORD_TOKEN")
    if token:
        try:
            bot.run(token)
        except Exception as e:
            print(f"❌ O bot parou com o erro: {e}")
    else:
        print("❌ ERRO: Token não encontrado no .env")