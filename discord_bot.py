import discord
from discord import app_commands
from discord.ext import commands
import os
import camera_handler
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()  # Carrega variáveis de ambiente do arquivo .env

# ── CONFIGURAÇÕES ─────────────────────────────────────────────────────────────
# Substitua pelo seu ID real (clique com botão direito no seu nome no Discord -> Copiar ID)
MEU_ID = os.getenv("ID_DISCORD_CHAUSSE")  # Coloque seu ID do Discord aqui

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── LOGICA DE PERMISSÃO ───────────────────────────────────────────────────────
async def tem_permissao(user_id):
    if str(user_id) == MEU_ID:
        return True
    
    try:
        res = supabase.table("permissoes").select("permissao").eq("discord_id", str(user_id)).execute()
        return len(res.data) > 0 and res.data[0].get("permissao") == True
    except:
        return False

# ── SLASH COMMANDS ───────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await bot.tree.sync() # Sincroniza os comandos com o Discord
    print(f"--- Y.A.T.R.A. Online como {bot.user} ---")

@bot.tree.command(name="ver", description="Yatra captura uma imagem e a descreve")
async def ver(interaction: discord.Interaction):
    # 1. Avisa o Discord que você recebeu a ordem e está trabalhando
    await interaction.response.defer() 
    
    # 2. Agora chama a função de câmera
    resultado = await camera_handler.capturar_e_descrever()
    
    # 3. Manda a resposta final
    if resultado:
        await interaction.followup.send(f"📷 **Câmera:** {resultado}")
    else:
        await interaction.followup.send("❌ Erro ao capturar imagem.")

@bot.tree.command(name="setpermissao", description="Dá/tira permissão de alguém (Apenas Chausse)")
async def setpermissao(interaction: discord.Interaction, usuario: discord.Member, permitir: bool):
    if str(interaction.user.id) != MEU_ID:
        return await interaction.response.send_message("Apenas o Chausse pode gerenciar acessos!", ephemeral=True)

    supabase.table("permissoes").upsert({
        "discord_id": str(usuario.id), 
        "permissao": permitir
    }).execute()
    
    await interaction.response.send_message(f"✅ Permissão de {usuario.name} definida como: **{permitir}**")

@bot.tree.command(name="entrar", description="Entra no canal de voz")
async def entrar(interaction: discord.Interaction):
    if not await tem_permissao(interaction.user.id):
        return await interaction.response.send_message("❌ Acesso negado.", ephemeral=True)
    
    if interaction.user.voice:
        channel = interaction.user.voice.channel
        await channel.connect()
        await interaction.response.send_message(f"✅ Conectada ao {channel.name}")
    else:
        await interaction.response.send_message("❌ Você precisa estar em um canal de voz!")

@bot.tree.command(name="sair", description="Sai do canal de voz")
async def sair(interaction: discord.Interaction):
    if not await tem_permissao(interaction.user.id):
        return await interaction.response.send_message("❌ Acesso negado.", ephemeral=True)
    
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("👋 Até logo!")
    else:
        await interaction.response.send_message("❌ Eu não estou em nenhum canal.")

# ── INICIAR BOT ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))