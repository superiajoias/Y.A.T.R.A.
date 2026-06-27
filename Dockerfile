# Usa uma imagem base do Python
FROM python:3.11-slim

# Instala as dependências do sistema que você precisa
RUN apt-get update && apt-get install -y \
    build-essential \
    libportaudio2 \
    portaudio19-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Define a pasta de trabalho
WORKDIR /app

# Copia seus arquivos para dentro do container
COPY . .

# Instala as dependências do seu Python (requirements.txt)
RUN pip install --no-cache-dir -r requirements.txt

# Comando para rodar o bot (substitua pelo nome do seu arquivo principal, ex: main.py ou discord_bot.py)
CMD ["python", "main.py"]