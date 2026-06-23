import os
import sqlite3
import discord
import json
import re
from discord.ext import commands
from groq import Groq
from dotenv import load_dotenv
import ai_brain 

# Configurações
load_dotenv()
TOKEN_DISCORD = os.getenv("DISCORD_TOKEN")
client_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

EMOCOES_NICK = {
    "N": "Normal 😐", "R": "Com Raiva 😡", "T": "Triste 😢",
    "A": "Alegre ✨", "C": "Confusa 🤔", "M": "Com Medo 😰",
    "X": "Ansiosa 😰", "E": "Empolgada 🚀", "S": "Com Sono 😴"
}

def salvar_no_sqlite(user_id, plataforma, role, mensagem):
    conn = sqlite3.connect("memoria_yatra.db")
    cursor = conn.cursor()
    cursor.execute('INSERT INTO historico_conversas (user_id, plataforma, role, mensagem) VALUES (?, ?, ?, ?)', 
                   (str(user_id), plataforma, role, mensagem))
    conn.commit()
    conn.close()

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    discord_id = str(message.author.id)
    # Garante que o usuário existe no cérebro
    perfil = ai_brain.obter_ou_criar_usuario(discord_id, message.author.display_name, plataforma="discord")

    if bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        pergunta = message.content.replace(f'<@{bot.user.id}>', '').strip()
        
        async with message.channel.typing():
            try:
                # Monta o prompt com o reconhecimento de criador do ai_brain
                system_prompt = ai_brain.montar_system_prompt(perfil, discord_id)
                
                # Puxa o histórico (ajuste para sua função real)
                historico = [{"role": "system", "content": system_prompt}] 
                historico += ai_brain.puxar_contexto_recente(discord_id)
                historico.append({"role": "user", "content": pergunta})

                response = client_groq.chat.completions.create(
                    model="llama-3.1-8b-instant", 
                    messages=historico, 
                    temperature=0.7
                )
                resposta_bruta = response.choices[0].message.content.strip()

                # --- LÓGICA DE LIMPEZA E HUMOR ---
                match = re.search(r'\[HUMOR:([NARTCMXES])\]', resposta_bruta)
                humor_detectado = match.group(1) if match else "N"
                texto_limpo = re.sub(r'\[HUMOR:[NARTCMXES]\]', '', resposta_bruta).strip()

                # Atualiza JSON de estado (para web e bot)
                with open("estado_yatra.json", "r+") as f:
                    estado = json.load(f)
                    estado["humor_atual"] = humor_detectado
                    f.seek(0)
                    json.dump(estado, f, indent=2)
                    f.truncate()

                # Atualiza estado interno da Yatra para o console
                ai_brain.status_yatra["humor"] = EMOCOES_NICK.get(humor_detectado, "Normal 😐")
                ai_brain.status_yatra["ultima_acao"] = f"Falou com {message.author.display_name}"

                # Salva no banco
                salvar_no_sqlite(discord_id, "discord", "assistant", texto_limpo)

                # Muda o nick do bot no servidor
                if message.guild:
                    try:
                        novo_nick = f"Y.A.T.R.A. - {EMOCOES_NICK.get(humor_detectado, 'Normal 😐').split()[0]}"
                        await message.guild.me.edit(nick=novo_nick)
                    except Exception as e:
                        print(f"Erro ao mudar nick: {e}")

                await message.reply(texto_limpo)

            except Exception as e:
                print(f"Erro no processamento: {e}")
                await message.reply("Ops, meu sistema operacional teve uma falha momentânea.")

    await bot.process_commands(message)