import os
import discord
import re
from discord.ext import commands
from groq import Groq
from dotenv import load_dotenv
import ai_brain 

# Configurações
load_dotenv()
client_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

EMOCOES_NICK = {
    "N": "Normal 😐", "R": "Raiva 😡", "T": "Triste 😢",
    "A": "Alegre ✨", "C": "Confusa 🤔", "M": "Medo 😰",
    "X": "Ansiosa 😰", "E": "Empolgada 🚀", "S": "Sono 😴"
}

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    discord_id = str(message.author.id)
    # Garante que o usuário existe no Supabase
    perfil = ai_brain.obter_ou_criar_usuario(discord_id, message.author.display_name, plataforma="discord")

    if bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        pergunta = message.content.replace(f'<@{bot.user.id}>', '').strip()
        
        async with message.channel.typing():
            try:
                # Monta o prompt (que já contém a memória de vícios carregada)
                system_prompt = ai_brain.montar_system_prompt(perfil, discord_id)
                
                # Puxa histórico do Supabase
                historico = [{"role": "system", "content": system_prompt}] 
                historico += ai_brain.puxar_contexto_recente(discord_id)
                historico.append({"role": "user", "content": pergunta})

                response = client_groq.chat.completions.create(
                    model="llama-3.1-8b-instant", 
                    messages=historico, 
                    temperature=0.7
                )
                resposta_bruta = response.choices[0].message.content.strip()

                # 1. Lógica de Humor
                match_humor = re.search(r'\[HUMOR:([NARTCMXES])\]', resposta_bruta)
                humor_detectado = match_humor.group(1) if match_humor else "N"
                texto_limpo = re.sub(r'\[HUMOR:[NARTCMXES]\]', '', resposta_bruta).strip()

                # 2. Lógica de Memória de Vícios
                match_gosto = re.search(r'\[GOSTO:(.*?)\]', texto_limpo)
                if match_gosto:
                    item_viciado = match_gosto.group(1).strip()
                    ai_brain.adicionar_gosto(discord_id, item_viciado)
                    texto_limpo = re.sub(r'\[GOSTO:.*?\]', '', texto_limpo).strip()
                    print(f"🧠 Nova obsessão da Yatra registrada: {item_viciado}")

                # 3. Atualiza Humor no Supabase
                try:
                    ai_brain.supabase.table("estado_yatra") \
                        .update({"humor_atual": humor_detectado}) \
                        .eq("id", 1) \
                        .execute()
                except Exception as e:
                    print(f"Erro ao salvar humor: {e}")

                # 4. Salva histórico
                ai_brain.registrar_mensagem(discord_id, "discord", "user", pergunta)
                ai_brain.registrar_mensagem(discord_id, "discord", "assistant", texto_limpo)

                # 5. Atualiza Nick
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