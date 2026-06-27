#!/usr/bin/env bash
set -e

echo "🚀 Iniciando o setup da Yatra no servidor..."

# Atualiza e instala dependências de sistema
echo "📦 Instalando dependências de sistema (PortAudio/FFmpeg)..."
apt-get update
apt-get install -y portaudio19-dev ffmpeg

# Instala as dependências do Python
echo "🐍 Instalando bibliotecas Python via requirements.txt..."
pip install -r requirements.txt

echo "✅ Tudo pronto! Iniciando o bot..."