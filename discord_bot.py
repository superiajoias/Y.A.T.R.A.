import os
import discord
import re
from discord.ext import commands
from groq import Groq
from dotenv import load_dotenv
import ai_brain

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
load_dotenv()

client_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

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
# BOT CORE
# ─────────────────────────────────────────────
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    discord_id = str(message.author.id)

    perfil = ai_brain.obter_ou_criar_usuario(
        discord_id,
        message.author.display_name,
        plataforma="discord"
    )

    # só responde se mention ou DM
    if not (bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel)):
        return

    pergunta = message.content.replace(f'<@{bot.user.id}>', '').strip()

    async with message.channel.typing():
        try:
            system_prompt = ai_brain.montar_system_prompt(perfil, discord_id)

            historico = [{"role": "system", "content": system_prompt}]
            historico += ai_brain.puxar_contexto_recente(discord_id, limite=15)
            historico.append({"role": "user", "content": pergunta})

            response = client_groq.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=historico,
                temperature=0.7
            )

            resposta = response.choices[0].message.content.strip()

            # ─────────────────────────────────────────────
            # 1. EXTRAI HUMOR REAL
            # ─────────────────────────────────────────────
            match = re.search(r'\[HUMOR:([NARTCMXES])\]', resposta)

            if match:
                novo_humor = match.group(1)
            else:
                novo_humor = ai_brain.estado_yatra.get("humor_atual", "N")

            # atualiza estado REAL
            ai_brain.estado_yatra["humor_atual"] = novo_humor
            ai_brain.ajustar_estados_internos(novo_humor)

            # limpa output
            texto_limpo = re.sub(r'\[HUMOR:[NARTCMXES]\]', '', resposta).strip()

            # ─────────────────────────────────────────────
            # 2. DETECTA GOSTO / MEMÓRIA
            # ─────────────────────────────────────────────
            match_gosto = re.search(r'\[GOSTO:(.*?)\]', texto_limpo)

            if match_gosto:
                item = match_gosto.group(1).strip()
                ai_brain.adicionar_gosto(discord_id, item)
                texto_limpo = re.sub(r'\[GOSTO:.*?\]', '', texto_limpo).strip()
                print(f"🧠 Novo gosto registrado: {item}")

            # ─────────────────────────────────────────────
            # 3. SALVA HISTÓRICO
            # ─────────────────────────────────────────────
            ai_brain.registrar_mensagem(discord_id, "discord", "user", pergunta)
            ai_brain.registrar_mensagem(discord_id, "discord", "assistant", texto_limpo)

            # ─────────────────────────────────────────────
            # 4. SALVA HUMOR NO SUPABASE (ESP32 + UI)
            # ─────────────────────────────────────────────
            try:
                ai_brain.supabase.table("estado_yatra") \
                    .update({"humor_atual": novo_humor}) \
                    .eq("id", 1) \
                    .execute()
            except Exception as e:
                print("Erro Supabase humor:", e)

            # ─────────────────────────────────────────────
            # 5. ATUALIZA USUÁRIO
            # ─────────────────────────────────────────────
            msgs = perfil.get("mensagens", 0) + 1
            amizade = min(100, perfil.get("amizade", 0) + 1)

            ai_brain.atualizar_usuario(discord_id, {
                "mensagens": msgs,
                "amizade": amizade
            })

            # ─────────────────────────────────────────────
            # 6. NICK DO BOT (GUILD)
            # ─────────────────────────────────────────────
            if message.guild:
                try:
                    emoji = EMOCOES_NICK.get(novo_humor, "Normal 😐")
                    novo_nick = f"Y.A.T.R.A. - {emoji.split()[0]}"
                    await message.guild.me.edit(nick=novo_nick)
                except Exception as e:
                    print("Erro nick:", e)

            # ─────────────────────────────────────────────
            # 7. RESPOSTA FINAL
            # ─────────────────────────────────────────────
            await message.reply(texto_limpo)

        except Exception as e:
            print("Erro geral bot:", e)
            await message.reply("⚠️ Sistema da Y.A.T.R.A. falhou momentaneamente.")

    await bot.process_commands(message)


# ─────────────────────────────────────────────
# RUN BOT
# ─────────────────────────────────────────────
