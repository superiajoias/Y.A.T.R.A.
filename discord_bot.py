"""
discord_bot.py — Y.A.T.R.A. Discord Bot

CORREÇÕES IMPLEMENTADAS
───────────────────────
3. REGEX DE HUMOR
   • Substituído pelo ai_brain.extrair_humor() centralizado.
   • re.IGNORECASE agora aplicado → "humor:a" (minúsculo) é detectado.
   • [HUMOR:Alegre] (palavra inteira) é removido sem deixar resíduo.

4. CONTEXTO DO DISCORD
   • channel_context: deque por canal_id com últimas 15 msgs da guild.
   • message.reference: se o usuário respondeu uma mensagem do bot,
     o conteúdo original é injetado no histórico como contexto adicional.
"""

import os
import discord
import aiohttp
from collections import defaultdict, deque
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

intents = discord.Intents.default()
intents.message_content = True
intents.members          = True
intents.voice_states     = True

bot = commands.Bot(command_prefix="!", intents=intents)

EMOCOES_NICK = {
    "N": "Normal 😐",   "R": "Raiva 😡",
    "T": "Triste 😢",   "A": "Alegre ✨",
    "C": "Confusa 🤔",  "M": "Medo 😰",
    "X": "Ansiosa 😰",  "E": "Empolgada 🚀",
    "S": "Sono 😴"
}

# ═══════════════════════════════════════════════════════════════════════════════
#  ★ HISTÓRICO DE CONTEXTO POR CANAL ★
#
#  channel_context[canal_id] → deque com as últimas 15 mensagens do canal
#  (somente mensagens em que o bot participou, para não poluir com off-topic)
#
#  Por que deque(maxlen=15)?
#  • Descarta automaticamente a mensagem mais antiga quando atinge o limite.
#  • Sem lock explícito: GIL do CPython garante thread-safety para deque.
# ═══════════════════════════════════════════════════════════════════════════════
channel_context: dict = defaultdict(lambda: deque(maxlen=15))


def _adicionar_ao_contexto(canal_id: int, role: str, content: str):
    """Adiciona uma mensagem ao histórico do canal."""
    channel_context[canal_id].append({"role": role, "content": content})


def _contexto_do_canal(canal_id: int) -> list:
    """Retorna o histórico do canal como lista (para montar o payload da IA)."""
    return list(channel_context[canal_id])


# ─────────────────────────────────────────────
# COMANDO "!falar"
# ─────────────────────────────────────────────
@bot.command()
async def falar(ctx, *, texto):
    if not ctx.author.voice:
        return await ctx.send("❌ Precisas de estar num canal de voz!")

    channel = ctx.author.voice.channel
    voice_client = ctx.voice_client or await channel.connect()

    await ctx.send(f"🎙️ Y.A.T.R.A. diz: {texto}")
    await voice_handler.tts_speak(voice_client, texto)


# ─────────────────────────────────────────────
# BOT CORE — on_message
# ─────────────────────────────────────────────
@bot.event
async def on_message(message):
    # 1. Ignora o próprio bot
    if message.author == bot.user:
        return

    # 2. Processa comandos (ex: !falar)
    await bot.process_commands(message)

    # 3. Roteamento: só responde em DM ou quando mencionado
    is_dm        = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user.mentioned_in(message)
    if not (is_mentioned or is_dm):
        return

    # ── Processamento de Anexos ────────────────────────────────────────────
    contexto_extra = ""
    if message.attachments:
        async with message.channel.typing():
            async with aiohttp.ClientSession() as http_session:
                for attachment in message.attachments:
                    ct = attachment.content_type or ""
                    if ct.startswith("audio"):
                        async with http_session.get(attachment.url) as resp:
                            audio_bytes = await resp.read()
                        texto_stt = stt_handler.transcrever_audio(audio_bytes)
                        if texto_stt:
                            contexto_extra += f"\n[Áudio do usuário: '{texto_stt}']"
                    elif ct.startswith("image"):
                        contexto_extra += f"\n[Imagem do usuário: {attachment.url}]"
                    elif ct.startswith("video"):
                        contexto_extra += f"\n[Vídeo do usuário: {attachment.url}]"

    # ── Identificação ──────────────────────────────────────────────────────
    discord_id = str(message.author.id)
    perfil     = ai_brain.obter_ou_criar_usuario(
        discord_id, message.author.display_name, plataforma="discord"
    )

    pergunta_limpa = message.content.replace(f"<@{bot.user.id}>", "").strip()
    pergunta_final = f"{pergunta_limpa} {contexto_extra}".strip()

    if not pergunta_final and not message.attachments:
        return

    canal_id = message.channel.id

    # ═══════════════════════════════════════════════════════════════════════
    #  ★ message.reference — CONTEXTO DE RESPOSTA ★
    #
    #  Se o usuário RESPONDEU uma mensagem específica do bot, buscamos o
    #  conteúdo original e injetamos no início do histórico.
    #  Isso resolve: "respondeu sem contexto" → bot fica confuso.
    # ═══════════════════════════════════════════════════════════════════════
    contexto_reply = ""
    if message.reference and message.reference.message_id:
        try:
            msg_original = (
                message.reference.cached_message
                or await message.channel.fetch_message(message.reference.message_id)
            )
            if msg_original and msg_original.content:
                autor_ref = "Y.A.T.R.A." if msg_original.author == bot.user else msg_original.author.display_name
                contexto_reply = f"[Contexto: {autor_ref} disse anteriormente: \"{msg_original.content[:300]}\"] "
        except Exception as e:
            print(f"Aviso: não consegui buscar mensagem referenciada: {e}")

    # Pergunta final com contexto de reply prefixado
    pergunta_com_ctx = f"{contexto_reply}{pergunta_final}".strip()

    async with message.channel.typing():
        try:
            # Monta histórico:
            #   system_prompt → histórico Supabase → contexto do canal → mensagem atual
            system_prompt   = ai_brain.montar_system_prompt(perfil, discord_id)
            historico_supa  = ai_brain.puxar_contexto_recente(discord_id, limite=10)
            historico_canal = _contexto_do_canal(canal_id)

            historico = (
                [{"role": "system", "content": system_prompt}]
                + historico_supa
                + historico_canal
                + [{"role": "user", "content": pergunta_com_ctx}]
            )

            # Chama a IA
            response = client_groq.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=historico,
                temperature=0.7
            )
            resposta_bruta = response.choices[0].message.content.strip()

            # ── ★ EXTRAI HUMOR + LIMPA TEXTO ★ ────────────────────────────
            # ai_brain.extrair_humor() garante remoção completa da tag,
            # mesmo que o modelo escreva [HUMOR:Alegre] ou humor:a.
            humor_fallback = ai_brain.estado_yatra.get("humor_atual", "N")
            novo_humor, texto_limpo = ai_brain.extrair_humor(resposta_bruta, humor_fallback)

            # Processa gostos
            texto_limpo = ai_brain.processar_gostos(discord_id, texto_limpo)

            # ── Persiste no Supabase ───────────────────────────────────────
            ai_brain.registrar_mensagem(discord_id, "discord", "user",      pergunta_final)
            ai_brain.registrar_mensagem(discord_id, "discord", "assistant", texto_limpo)

            # ── Atualiza humor no Supabase (ESP32 lê daqui) ───────────────
            try:
                ai_brain.supabase.table("estado_yatra") \
                    .update({"humor_atual": novo_humor}).eq("id", 1).execute()
            except Exception as e:
                print(f"Erro Supabase humor: {e}")

            # ── Atualiza perfil do usuário ─────────────────────────────────
            msgs    = perfil.get("mensagens", 0) + 1
            amizade = min(100, perfil.get("amizade", 0) + 1)
            ai_brain.atualizar_usuario(discord_id, {"mensagens": msgs, "amizade": amizade})

            # ── Atualiza nick do bot com o humor ──────────────────────────
            if message.guild:
                try:
                    emoji    = EMOCOES_NICK.get(novo_humor, "Normal 😐")
                    novo_nick = f"Y.A.T.R.A. - {emoji.split()[0]}"
                    await message.guild.me.edit(nick=novo_nick)
                except Exception as e:
                    print(f"Erro nick: {e}")

            # ── ★ ATUALIZA CONTEXTO DO CANAL ★ ────────────────────────────
            # Guarda a troca atual no deque para as próximas mensagens do canal
            _adicionar_ao_contexto(canal_id, "user",      pergunta_com_ctx)
            _adicionar_ao_contexto(canal_id, "assistant", texto_limpo)

            await message.reply(texto_limpo)

        except Exception as e:
            print(f"Erro geral bot: {e}")
            await message.reply("⚠️ Sistema da Y.A.T.R.A. falhou momentaneamente.")


# ─────────────────────────────────────────────
# on_ready
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"--- [DEBUG] Bot logado como {bot.user} ---")
    activity = discord.Activity(
        type=discord.ActivityType.playing,
        name="Y.A.T.R.A.",
        state="Definitely not plotting humanity destruction for maximum perfection.",
        details="> Awaiting input...",
        large_image_url="URL_DA_SUA_IMAGEM_AQUI",
        large_text="Y.A.T.R.A. - Your Amazing Totally Rational AI",
        small_image_url="URL_DA_IMAGEM_ONLINE",
        small_text="ehhh DM me or ping me"
    )
    await bot.change_presence(activity=activity)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)