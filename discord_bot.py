import os
import sqlite3
import discord
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

def puxar_contexto_recente(user_id, limite=10):
    conn = sqlite3.connect("memoria_yatra.db")
    cursor = conn.cursor()
    cursor.execute('SELECT role, mensagem FROM historico_conversas WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?', (str(user_id), limite))
    linhas = cursor.fetchall()
    conn.close()
    return [{"role": role, "content": msg} for role, msg in reversed(linhas)]

@bot.event
async def on_ready():
    print(f"🤖 Y.A.T.R.A. online no Discord!")
    await bot.change_presence(activity=discord.Game(name="Conectada à ESP32 🧠"))

@bot.event
async def on_message(message):
    if message.author.bot: return

    # Registrar no cérebro
    discord_id = str(message.author.id)
    perfil = ai_brain.obter_ou_criar_usuario(discord_id, message.author.display_name, plataforma="discord")
    ai_brain.atualizar_usuario(discord_id, {"mensagens": perfil["mensagens"] + 1})

    if bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        pergunta = message.content.replace(f'<@{bot.user.id}>', '').strip()
        if not pergunta: return

        async with message.channel.typing():
            try:
                salvar_no_sqlite(discord_id, "discord", "user", pergunta)
                historico = [{"role": "system", "content": "Você é a Y.A.T.R.A., IA sagaz e zoeira."}] 
                historico += puxar_contexto_recente(discord_id, limite=10)

                response = client_groq.chat.completions.create(model="llama-3.1-8b-instant", messages=historico, temperature=0.7)
                resposta_ia = response.choices[0].message.content.strip()

                # Detectar humor
                with open("estado_yatra.json", "r+") as f:
                    estado = json.load(f)
                    estado["humor_atual"] = humor_detectado
                    f.seek(0)
                    json.dump(estado, f, indent=2)
                    f.truncate()

                # Atualizar Estado para o Flask/ESP32
                ai_brain.status_yatra["humor"] = EMOCOES_NICK.get(humor_detectado, "Normal 😐")
                ai_brain.status_yatra["ultima_acao"] = f"Falou com {message.author.display_name}"

                salvar_no_sqlite(discord_id, "discord", "assistant", resposta_ia)

                # Mudar Nick
                if message.guild:
                    try:
                        novo_nick = f"Y.A.T.R.A. - {EMOCOES_NICK.get(humor_detectado, 'Normal 😐')}"
                        await message.guild.me.edit(nick=novo_nick)
                    except Exception as e:
                        print(f"Erro ao mudar nick: {e}")

                await message.reply(resposta_ia)

            except Exception as e:
                print(f"Erro no processamento: {e}")
                await message.reply("Deu um estalo no meu córtex. Pode repetir?")

    await bot.process_commands(message)

bot.run(TOKEN_DISCORD)