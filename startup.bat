@echo off
title 🧠 YATRA — Iniciando Córtex Virtual
cls
cd /d "C:\Users\Denis\Downloads\yatra\principal"

echo 📦 Verificando dependencias...
python -m pip install groq pyserial Flask python-dotenv discord.py duckduckgo_search --quiet

echo.
echo 🧠 Ligando o Cortex Virtual da Yatra (Web)...
start /b python ai_brain.py

echo 🤖 Inicializando a ponte com o Discord...
start /b python discord_bot.py

echo.
echo ✅ Sistemas rodando em background!
echo 🌐 Interface Web: http://localhost:5000
echo 💬 Discord: Pronto no seu servidor!
echo.
pause