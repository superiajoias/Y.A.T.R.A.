import threading
import os
import discord
from dotenv import load_dotenv
from ai_brain import app 
# Importamos o bot diretamente do arquivo onde ele foi criado
from discord_bot import bot 

load_dotenv()

# 1. Flask rodando como background thread
def run_flask():
    print("🌐 Iniciando servidor Flask...")
    # desligamos o reloader para evitar conflitos de thread
    app.run(host='0.0.0.0', port=5000, use_reloader=False)

if __name__ == '__main__':
    # Iniciamos o Flask primeiro em background
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 2. Discord rodando no thread PRINCIPAL (obrigatório pelo discord.py)
    print("🚀 Iniciando bot do Discord no thread principal...")
    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)
    else:
        print("❌ ERRO: Token não encontrado no .env")