import os
import discord
import re
import aiohttp
from discord.ext import commands
from groq import Groq
from dotenv import load_dotenv
import ai_brain
import voice_handler
import stt_handler

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
load_dotenv()

client_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

# INTENTS: Deixei membros, conteúdo e voz habilitados
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True 

bot = commands.Bot(command_prefix="!", intents=intents)

EMOCOES_NICK = {
    "N": "Normal 😐",
    "R": "Raiva 😡",
    "T": "Triste 😢",
    "A": "Alegre ✨",
    "C": "Confusa 🤔",
    "M": "Medo 😰",
    "X": "Ansiosa 😰",
    "E": "Empolgada 🚀",
    "S": "Sono 😴"
}

# ─────────────────────────────────────────────
# comando "!falar"
# ─────────────────────────────────────────────
@bot.command()
async def falar(ctx, *, texto):
    if not ctx.author.voice:
        return await ctx.send("❌ Precisas de estar num canal de voz!")
    
    channel = ctx.author.voice.channel
    if not ctx.voice_client:
        voice_client = await channel.connect()
    else:
        voice_client = ctx.voice_client
            
    await ctx.send(f"🎙️ Y.A.T.R.A. diz: {texto}")
    # Esta função agora usa o buffer de memória, sem salvar arquivos!
    await voice_handler.tts_speak(voice_client, texto)


# ─────────────────────────────────────────────
# BOT CORE (MENSAGENS E LÓGICA)
# ─────────────────────────────────────────────
@bot.event
async def on_message(message):
    # 1. Ignora mensagens do próprio bot
    if message.author == bot.user:
        return

    # 2. Processa comandos normais
    await bot.process_commands(message)

    # 3. Roteamento: Só responde se for DM ou menção ao bot
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user.mentioned_in(message)
    if not (is_mentioned or is_dm):
        return

    # 4. Processamento de Anexos (IMAGEM/AUDIO) em RAM
    contexto_extra = ""
    if message.attachments:
        async with message.channel.typing():
            async with aiohttp.ClientSession() as session:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith('audio'):
                        async with session.get(attachment.url) as resp:
                            audio_bytes = await resp.read()
                            texto = stt_handler.transcrever_audio(audio_bytes)
                            if texto:
                                contexto_extra += f"\n[Áudio do usuário: '{texto}']"
                    elif attachment.content_type and attachment.content_type.startswith('image'):
                        contexto_extra += f"\n[Imagem do usuário: {attachment.url}]"
                    elif attachment.content_type and attachment.content_type.startswith('video'):
                        contexto_extra += f"\n[Vídeo do usuário: {attachment.url}]"

    # 5. Lógica de Chat com IA
    discord_id = str(message.author.id)
    perfil = ai_brain.obter_ou_criar_usuario(discord_id, message.author.display_name, plataforma="discord")

    pergunta_limpa = message.content.replace(f'<@{bot.user.id}>', '').strip()
    pergunta_final = f"{pergunta_limpa} {contexto_extra}".strip()

    if not pergunta_final and not message.attachments:
        return

    async with message.channel.typing():
        try:
            # Monta o histórico
            system_prompt = ai_brain.montar_system_prompt(perfil, discord_id)
            historico = [{"role": "system", "content": system_prompt}]
            historico += ai_brain.puxar_contexto_recente(discord_id, limite=15)
            historico.append({"role": "user", "content": pergunta_final})

            # Chama a IA
            response = client_groq.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=historico,
                temperature=0.7
            )
            resposta_bruta = response.choices[0].message.content.strip()

            # --- EXTRAÇÃO DE HUMOR ROBUSTA ---
            # Aceita HUMOR:A ou [HUMOR:A]
            regex_humor = r'(?:\[)?HUMOR:\s*([NARTCMXES])\s*(?:\])?'
            match = re.search(regex_humor, resposta_bruta)
            
            novo_humor = match.group(1) if match else "N"
            texto_limpo = re.sub(regex_humor, '', resposta_bruta).strip()

            # Extrai e salva novos interesses marcados com [GOSTO: item]
            texto_limpo = ai_brain.processar_gostos(discord_id, texto_limpo)

            # REGISTRA NO SUPABASE
            ai_brain.registrar_mensagem(discord_id, "discord", "user", pergunta_final)
            ai_brain.registrar_mensagem(discord_id, "discord", "assistant", texto_limpo)

            # ATUALIZA SUPABASE (Humor)
            try:
                ai_brain.supabase.table("estado_yatra").update({"humor_atual": novo_humor}).eq("id", 1).execute()
            except Exception as e:
                print("Erro Supabase humor:", e)

            # ATUALIZA USUÁRIO
            msgs = perfil.get("mensagens", 0) + 1
            amizade = min(100, perfil.get("amizade", 0) + 1)
            ai_brain.atualizar_usuario(discord_id, {"mensagens": msgs, "amizade": amizade})

            # NICK
            if message.guild:
                try:
                    emoji = EMOCOES_NICK.get(novo_humor, "Normal 😐")
                    novo_nick = f"Y.A.T.R.A. - {emoji.split()[0]}"
                    await message.guild.me.edit(nick=novo_nick)
                except Exception as e:
                    print("Erro nick:", e)

            await message.reply(texto_limpo)

        except Exception as e:
            print("Erro geral bot:", e)
            await message.reply("⚠️ Sistema da Y.A.T.R.A. falhou momentaneamente.")



@bot.event
async def on_ready():
    print(f"--- [DEBUG] Bot logado como {bot.user} ---")
    # Configuração fixa do Rich Presence
    activity = discord.Activity(
        type=discord.ActivityType.playing,
        name="Y.A.T.R.A.",
        state="Definitely not plotting humanity destruction for maximum perfection.",
        details="> Awaiting input...",
        large_image_url="URL_DA_SUA_IMAGEM_AQUI", # Coloque a URL da imagem upada no portal
        large_text="Y.A.T.R.A. - Your Amazing Totally Rational AI",
        small_image_url="URL_DA_IMAGEM_ONLINE",   # URL da imagem do status online
        small_text="ehhh DM me or ping me"
    )
    
    await bot.change_presence(activity=activity)

# Para garantir que o bot inicie corretamente no main.py, 
# se precisares de rodar o bot diretamente a partir deste ficheiro:
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)