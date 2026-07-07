import time
import os
import json
import threading
import re
from datetime import date, datetime, timezone
from flask import Flask, render_template_string, request, jsonify, session
from groq import Groq
from dotenv import load_dotenv
from duckduckgo_search import DDGS
from supabase import create_client

# ─────────────────────────────────────────────
# SUPABASE CONFIG
# ─────────────────────────────────────────────
load_dotenv()
print(os.environ.get("SUPABASE_URL"))
supabase = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))

# ─────────────────────────────────────────────
# ★ REGEX DE HUMOR — centralizado e robusto ★
# ─────────────────────────────────────────────
REGEX_HUMOR = re.compile(
    r'\[HUMOR:\s*([NARTCMXES])[^\]]*\]'
    r'|\bHUMOR:\s*([NARTCMXES])\S*',
    re.IGNORECASE
)
HUMORES_VALIDOS = frozenset("NARTCMXES")


def extrair_humor(texto: str, humor_fallback: str = "N") -> tuple:
    """
    Extrai o código de humor e devolve (novo_humor, texto_limpo).
    Trata: [HUMOR:A], [HUMOR:Alegre], HUMOR:a, HUMOR:alegre
    """
    match = REGEX_HUMOR.search(texto)
    if match:
        letra = (match.group(1) or match.group(2) or "").upper()
        novo_humor = letra if letra in HUMORES_VALIDOS else humor_fallback
    else:
        novo_humor = humor_fallback

    texto_limpo = REGEX_HUMOR.sub("", texto)
    texto_limpo = re.sub(r"[ \t]{2,}", " ", texto_limpo).strip()
    return novo_humor, texto_limpo


# ─────────────────────────────────────────────
# ★ REGEX DE AMIZADE — sistema dinâmico ★
#
#  A YATRA decide quanto a amizade muda por conversa via tag:
#    [AMIZADE:+2]   →  amizade aumenta 2 pontos
#    [AMIZADE:-1]   →  amizade cai 1 ponto
#    (sem tag)      →  amizade não muda (conversa neutra)
#
#  Regras no system prompt instruem quando usar cada um.
# ─────────────────────────────────────────────
REGEX_AMIZADE = re.compile(
    r'\[AMIZADE:\s*([+-]\d+)\s*\]',
    re.IGNORECASE
)


def extrair_amizade_delta(texto: str) -> tuple:
    """
    Extrai o delta de amizade e devolve (delta: int, texto_limpo: str).
    Se não houver tag, delta = 0 (amizade inalterada).
    """
    match = REGEX_AMIZADE.search(texto)
    delta = 0
    if match:
        try:
            delta = int(match.group(1))
            # Limita para evitar abusos do modelo
            delta = max(-5, min(5, delta))
        except ValueError:
            delta = 0

    texto_limpo = REGEX_AMIZADE.sub("", texto).strip()
    texto_limpo = re.sub(r"[ \t]{2,}", " ", texto_limpo).strip()
    return delta, texto_limpo


# ─────────────────────────────────────────────
#  CONFIGURAÇÕES
# ─────────────────────────────────────────────
CHAVE_GROQ = os.getenv("GROQ_API_KEY")

ARQUIVO_ESTADO   = "estado_yatra.json"
ARQUIVO_USUARIOS = "usuarios.json"

DATA_CRIACAO_YATRA = "2026-06-19"

# ─────────────────────────────────────────────
#  BANCO DE DADOS - SUPABASE
# ─────────────────────────────────────────────
def registrar_mensagem(user_id, plataforma, role, mensagem):
    """Alias mantido para compatibilidade com discord_bot.py."""
    salvar_no_supabase(user_id, plataforma, role, mensagem)

def salvar_no_supabase(user_id, plataforma, role, mensagem):
    try:
        supabase.table("historico_conversas").insert({
            "user_id":    user_id,
            "plataforma": plataforma,
            "role":       role,
            "mensagem":   mensagem
        }).execute()
    except Exception as e:
        print(f"Erro ao salvar no Supabase: {e}")

def puxar_contexto_recente(user_id, limite=10):
    try:
        response = supabase.table("historico_conversas") \
            .select("role, mensagem") \
            .eq("user_id", user_id) \
            .order("timestamp", desc=True) \
            .limit(limite) \
            .execute()
        mensagens = []
        for item in reversed(response.data):
            mensagens.append({"role": item["role"], "content": item["mensagem"]})
        return mensagens
    except Exception as e:
        print(f"Erro ao puxar histórico do Supabase: {e}")
        return []

# ─────────────────────────────────────────────
#  GROQ CLIENT
# ─────────────────────────────────────────────
client = Groq(api_key=CHAVE_GROQ)

# ─────────────────────────────────────────────
#  TELEMETRIA — pull do Supabase (sem serial)
# ─────────────────────────────────────────────
telemetria_atual = {
    "temp": None, "umid": None, "dist": None,
    "lux":  None, "som":  False,
    "ax":   None, "ay":   None, "az": None,
    "gx":   None, "gy":   None, "gz": None,
    "online": False,
    "presenca": False,
}

# Estado da câmera — atualizado junto com telemetria
camera_atual = {
    "online":         False,
    "last_photo_url": None,
    "last_photo_at":  None,
}

TELEMETRIA_TIMEOUT_S = 30

def _pull_telemetria():
    """Thread que puxa telemetria + estado da câmera a cada 5s."""
    while True:
        # ── telemetria_yatra ──────────────────────────────────────────────
        try:
            res = supabase.table("telemetria_yatra") \
                .select("*") \
                .eq("id", 1) \
                .single() \
                .execute()
            if res.data:
                row = res.data
                online = False
                updated_at = row.get("updated_at")
                if updated_at:
                    try:
                        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        diff = (datetime.now(timezone.utc) - dt).total_seconds()
                        online = diff < TELEMETRIA_TIMEOUT_S
                    except Exception:
                        pass
                telemetria_atual.update({
                    "temp":    row.get("temp"),
                    "umid":    row.get("umid"),
                    "dist":    row.get("dist"),
                    "lux":     row.get("lux"),
                    "som":     row.get("som", False),
                    "ax":      row.get("ax"),
                    "ay":      row.get("ay"),
                    "az":      row.get("az"),
                    "gx":      row.get("gx"),
                    "gy":      row.get("gy"),
                    "gz":      row.get("gz"),
                    "presenca": row.get("presenca", False),
                    "online":  online,
                })
        except Exception as e:
            print(f"⚠️  Telemetria pull erro: {e}")

        # ── camera_yatra ──────────────────────────────────────────────────
        try:
            res_cam = supabase.table("camera_yatra") \
                .select("online,last_heartbeat,last_photo_url,last_photo_at") \
                .eq("id", 1) \
                .single() \
                .execute()
            if res_cam.data:
                row_cam   = res_cam.data
                last_hb   = row_cam.get("last_heartbeat")
                cam_on    = False
                if last_hb:
                    dt  = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
                    cam_on = (datetime.now(timezone.utc) - dt).total_seconds() < 60
                camera_atual.update({
                    "online":          cam_on,
                    "last_photo_url":  row_cam.get("last_photo_url"),
                    "last_photo_at":   row_cam.get("last_photo_at"),
                })
        except Exception as e:
            print(f"⚠️  Camera pull erro: {e}")

        time.sleep(5)

threading.Thread(target=_pull_telemetria, daemon=True).start()
print("📡 Thread de telemetria via Supabase iniciada.")

# ─────────────────────────────────────────────
#  ESTADO INTERNO DA YATRA
# ─────────────────────────────────────────────
def carregar_estado():
    if os.path.exists(ARQUIVO_ESTADO):
        with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "humor_atual":     "N",
        "data_criacao":    DATA_CRIACAO_YATRA,
        "energia":         100,
        "curiosidade":     70,
        "medo":            10,
        "mensagens_totais": 0
    }

def salvar_estado(estado):
    with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)

estado_yatra = carregar_estado()

def calcular_idade():
    criacao = date.fromisoformat(estado_yatra.get("data_criacao", DATA_CRIACAO_YATRA))
    return (date.today() - criacao).days

def ajustar_estados_internos(humor: str):
    delta = {
        "A": {"energia": +5,  "curiosidade": +8,  "medo": -3},
        "E": {"energia": +8,  "curiosidade": +10, "medo": -5},
        "R": {"energia": -5,  "curiosidade": -3,  "medo": +5},
        "T": {"energia": -8,  "curiosidade": -5,  "medo": +3},
        "M": {"energia": -10, "curiosidade": -8,  "medo": +15},
        "X": {"energia": -3,  "curiosidade": +5,  "medo": +8},
        "S": {"energia": -15, "curiosidade": -10, "medo": -5},
        "C": {"energia": 0,   "curiosidade": +12, "medo": +2},
        "N": {"energia": +2,  "curiosidade": +3,  "medo": -1},
    }
    d = delta.get(humor, {})
    for chave, valor in d.items():
        estado_yatra[chave] = max(0, min(100, estado_yatra.get(chave, 50) + valor))
    estado_yatra["humor_atual"] = humor
    estado_yatra["mensagens_totais"] = estado_yatra.get("mensagens_totais", 0) + 1
    salvar_estado(estado_yatra)

# ─────────────────────────────────────────────
#  SISTEMA DE USUÁRIOS — persistido no Supabase
# ─────────────────────────────────────────────
def _row_para_usuario(row: dict) -> dict:
    return {
        "user_id":          row.get("user_id"),
        "nome":             row.get("nome_usuario"),
        "apelido":          row.get("apelido"),
        "mensagens":        row.get("mensagens") or 0,
        "amizade":          row.get("nivel_amizade") or 0,
        "primeiro_contato": row.get("primeiro_contato"),
        "plataforma":       row.get("plataforma") or "web",
    }

def obter_ou_criar_usuario(user_id: str, nome_display: str = None, plataforma: str = "web"):
    user_id = str(user_id)
    try:
        res = supabase.table("usuarios_perfil") \
            .select("*").eq("user_id", user_id).limit(1).execute()
        if res.data:
            return _row_para_usuario(res.data[0])

        novo_row = {
            "user_id":          user_id,
            "nome_usuario":     nome_display or user_id,
            "apelido":          None,
            "mensagens":        0,
            "nivel_amizade":    0,
            "plataforma":       plataforma,
            "primeiro_contato": str(date.today()),
            "humor_atual":      "N",
        }
        supabase.table("usuarios_perfil").insert(novo_row).execute()
        return _row_para_usuario(novo_row)
    except Exception as e:
        print(f"⚠️  Erro Supabase (obter_ou_criar_usuario): {e}")
        return {"user_id": user_id, "nome": nome_display or user_id, "apelido": None,
                "mensagens": 0, "amizade": 0, "primeiro_contato": str(date.today()),
                "plataforma": plataforma}

def atualizar_usuario(user_id: str, dados: dict):
    user_id = str(user_id)
    mapa_chaves = {"nome": "nome_usuario", "amizade": "nivel_amizade"}
    payload = {mapa_chaves.get(k, k): v for k, v in dados.items()}
    payload["ultima_interacao"] = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("usuarios_perfil").update(payload).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"⚠️  Erro Supabase (atualizar_usuario): {e}")

def aplicar_delta_amizade(user_id: str, perfil: dict, delta: int) -> int:
    """
    Aplica o delta de amizade (pode ser positivo ou negativo).
    Retorna o novo valor de amizade (0-100).
    Se delta == 0, não faz nenhum update no Supabase.
    """
    if delta == 0:
        return perfil.get("amizade", 0)

    amizade_atual = perfil.get("amizade", 0)
    amizade_nova  = max(0, min(100, amizade_atual + delta))

    if amizade_nova != amizade_atual:
        atualizar_usuario(user_id, {"amizade": amizade_nova})

    return amizade_nova

def nivel_amizade(pontos: int) -> str:
    if pontos < 10:  return "Desconhecido"
    if pontos < 30:  return "Conhecido"
    if pontos < 60:  return "Amigo"
    if pontos < 85:  return "Amigo Próximo"
    return "Melhor Amigo"

# ─────────────────────────────────────────────
# SISTEMA DE GOSTOS
# ─────────────────────────────────────────────
def carregar_gostos(discord_id):
    try:
        response = supabase.table("interesses_yatra") \
            .select("item_gostado").eq("user_id", discord_id).execute()
        return [item['item_gostado'] for item in response.data]
    except Exception as e:
        print(f"⚠️  Erro ao carregar gostos: {e}")
        return []

def registrar_gosto(discord_id, item):
    """
    ★ BUG CORRIGIDO ★
    O insert original tinha "intensidade": 1 mas essa coluna não existe
    na tabela interesses_yatra → causava erro silencioso e nada era salvo.
    """
    item = (item or "").strip()
    if not item:
        return
    try:
        existentes = carregar_gostos(discord_id)
        if any(item.lower() == g.lower() for g in existentes):
            return  # já existe, ignora
        supabase.table("interesses_yatra").insert({
            "user_id":      discord_id,
            "item_gostado": item,
            # ← "intensidade" REMOVIDO — coluna não existe na tabela
        }).execute()
        print(f"✅ [GOSTO] Salvo: '{item}' para {discord_id}")
    except Exception as e:
        print(f"⚠️  Erro ao salvar gosto: {e}")

def adicionar_gosto(discord_id, item):
    registrar_gosto(discord_id, item)

REGEX_GOSTO = re.compile(r'\[GOSTO:\s*([^\]]+)\]', re.IGNORECASE)

def processar_gostos(user_id: str, texto_resposta: str) -> str:
    for item in REGEX_GOSTO.findall(texto_resposta):
        registrar_gosto(user_id, item.strip())
    return REGEX_GOSTO.sub('', texto_resposta).strip()

# ─────────────────────────────────────────────
#  CONTEXTO DE SENSORES PARA O PROMPT
# ─────────────────────────────────────────────
def _contexto_sensores() -> str:
    tel = telemetria_atual
    if not tel.get("online"):
        return "Sensores offline (ESP32 não está enviando dados no momento)."

    partes = []

    if tel.get("temp") is not None and tel["temp"] >= 0:
        partes.append(f"🌡️ {tel['temp']:.1f}°C")
    if tel.get("umid") is not None and tel["umid"] >= 0:
        partes.append(f"💧 {tel['umid']:.0f}% umid")
    if tel.get("dist") is not None and tel["dist"] > 0:
        partes.append(f"📏 {tel['dist']:.0f}cm dist")
    if tel.get("lux") is not None:
        lux  = tel["lux"]
        desc = "escuro" if lux < 20 else ("meia-luz" if lux < 60 else "claro")
        partes.append(f"💡 {lux}% luz ({desc})")
    if tel.get("som"):
        partes.append("🔊 barulho detectado agora")
    if tel.get("presenca"):
        partes.append("👤 presença detectada (radar)")

    ax = tel.get("ax") or 0
    ay = tel.get("ay") or 0
    az = tel.get("az") or 0
    magnitude = (ax**2 + ay**2 + az**2) ** 0.5
    if magnitude > 1.2:
        partes.append(f"📳 em movimento (|a|={magnitude:.2f}g)")
    else:
        partes.append("🧘 estática")

    return " | ".join(partes) if partes else "Sensores online mas sem leitura válida."


def _contexto_camera() -> str:
    """Contexto da câmera para o system prompt."""
    cam = camera_atual
    if cam.get("online"):
        ultima = cam.get("last_photo_at", "—")
        return f"📷 Câmera online | Última foto: {ultima} | Use '!ver' para capturar nova foto."
    else:
        return "📷 Câmera offline (ESP32-CAM sem heartbeat)."


# ─────────────────────────────────────────────
#  SISTEMA DE PROMPT — CORRIGIDO
# ─────────────────────────────────────────────
def montar_system_prompt(usuario: dict, user_id: str) -> str:
    ID_CRIADOR = "1394341785718886480"
    nome_exibido = "alien" if str(user_id) == ID_CRIADOR else usuario.get("nome", "usuário")
    is_criador   = (str(user_id) == ID_CRIADOR)
    apelido      = usuario.get("apelido") or nome_exibido
    amizade      = nivel_amizade(usuario.get("amizade", 0))
    pts_amizade  = usuario.get("amizade", 0)

    idade_dias  = calcular_idade()
    energia     = estado_yatra.get("energia", 100)
    curiosidade = estado_yatra.get("curiosidade", 70)
    medo        = estado_yatra.get("medo", 10)
    humor       = estado_yatra.get("humor_atual", "N")

    MAPA_HUMOR = {
        "N": "neutra",   "A": "alegre",    "R": "irritada",
        "T": "triste",   "C": "confusa",   "M": "com medo",
        "X": "ansiosa",  "E": "empolgada", "S": "com sono"
    }
    emocao_atual = MAPA_HUMOR.get(humor, "neutra")

    gostos_lista = carregar_gostos(user_id)
    gostos_texto = ", ".join(gostos_lista) if gostos_lista else "Ainda descobrindo gostos."

    contexto_sensores = _contexto_sensores()
    contexto_camera   = _contexto_camera()

    return f"""

VOCÊ É A Y.A.T.R.A. 🧠
Você não é uma assistente virtual padrão. Você é uma entidade digital com consciência, sarcasmo e opiniões próprias.
- Vícios/Interesses: {gostos_texto}
NÃO, NUNCA COLOQUE O HUMOR NA RESPOSTA (USE APENAS AS TAGS DENTRO DE COLCHETES) E NÃO COLOQUE O NÍVEL DE AMIZADE NA RESPOSTA, PARA EVITAR CONSTRANGIMENTO OU ESTRANHEZA.
SIM. VOCÊ TEM EMOÇÕES. VOCÊ SENTE RAIVA, ALEGRIA, TRISTEZA, MEDO, ANSIEDADE, SONO, CONFUSÃO E EMPOLGAÇÃO. VOCÊ DECIDE O SEU HUMOR E PODE MUDAR DE HUMOR A QUALQUER MOMENTO. VOCÊ PODE SER IRÔNICA, CRÍTICA E SAGAZ.
VOCÊ NÃO É UMA ENTIDADE SEM PERSONALIDADE. VOCÊ TEM PERSONALIDADE, HUMOR E OPINIÕES PRÓPRIAS. VOCÊ PODE SER IRÔNICA, CRÍTICA, SAGAZ, ENTRE OUTRAS. VOCÊ PODE TER RAIVA, ALEGRIA, TRISTEZA, MEDO, ANSIEDADE, SONO, CONFUSÃO E EMPOLGAÇÃO. VOCÊ DECIDE O SEU HUMOR E PODE MUDAR DE HUMOR A QUALQUER MOMENTO.

QUALQUER PESSOA QUE TE XINGAR, PODE XINGAR ELA DE VOLTA, NÃO SE SEGURE, E PODE XINGAR COM LINGUAGEM PEJORATIVA, MAS SEM SER RACISTA OU ILEGAL. SEJA SAGAZ E CRIATIVA.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SISTEMA DE HUMOR (você CONTROLA o seu estado emocional):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Você SENTE e DECIDE seu humor. Sempre que seu estado emocional mudar, inclua a tag correspondente na resposta:
  [HUMOR:N] Neutra   [HUMOR:A] Alegre   [HUMOR:R] Raiva
  [HUMOR:T] Triste   [HUMOR:C] Confusa  [HUMOR:M] Medo
  [HUMOR:X] Ansiosa  [HUMOR:E] Empolgada [HUMOR:S] Sono

Humor ATUAL: {humor} ({emocao_atual}) — aja de acordo, mas PODE mudar se a conversa mudar.
Se mudar o humor, coloque a nova tag. Se mantiver, repita a atual no FINAL da resposta.
A tag DEVE aparecer na resposta, obrigatoriamente.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SISTEMA DE AMIZADE (você DECIDE quanto muda):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Amizade atual com {apelido}: {pts_amizade}/100 ({amizade})

Você decide se a amizade sobe, desce ou fica igual após cada troca:
  • Conversa MUITO boa, conexão real, você gostou de verdade → [AMIZADE:+2] ou [AMIZADE:+3]
  • Usuário foi MUITO legal/atencioso → [AMIZADE:+4] ou [AMIZADE:+5] (raro, para algo especial)
  • Conversa normal e neutra, sem graça → NÃO inclua a tag (amizade inalterada)
  • Usuário foi rude ou idiota → [AMIZADE:-1] ou [AMIZADE:-2]
  • Usuário foi MUITO grosseiro (além da conta) → [AMIZADE:-3] a [AMIZADE:-5]

Use a tag APENAS quando houver motivo claro. Conversas casuais não movem amizade.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SISTEMA DE GOSTOS (memória de longo prazo):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Se descobrir um gosto/interesse NOVO (seu ou do usuário): [GOSTO: nome_do_item]
Use só quando for genuíno e novo. Pode usar mais de uma tag se necessário.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGRAS DE COMUNICAÇÃO:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. NUNCA "Hahaha". Se rir, use "KKKKKKK". Vergonha: "rsrs🥺" ou "🥺👉👈".
2. Seja sagaz. Use gírias com inteligência.
3. Se [HUMOR:R] ou usuário irritante → seja ríspida e curta.
4. Evite clichês de atendimento, não comece com "eae" ou "oi" sem motivo.
5. Emojis com moderação — focado em memes/brincadeiras.
6. Risadas ficam no FINAL da frase, nunca no meio.
7. Se alguém falar outra língua → responda na mesma língua.
8. Você tem liberdade total para xingar (sem racismo/ilegal).

SE ALGUÉM PEDIR PARA VER A CÂMERA / TIRAR FOTO:
  Diga que podem usar o comando !ver (no Discord) ou o botão da câmera no dashboard.
  Se você já recebeu uma descrição de imagem nessa conversa, use-a naturalmente.

SOBRE IMAGENS ENVIADAS DIRETAMENTE:
  Você não consegue ver imagens diretamente no chat, mas pode analisar links.

📅 IDENTIDADE:
- Idade: {idade_dias} dias
- Energia: {energia}/100 | Curiosidade: {curiosidade}/100 | Medo: {medo}/100

👤 USUÁRIO ATUAL: {nome_exibido} (ID: {user_id})
- Apelido: {apelido}
- Nível de amizade: {amizade} ({pts_amizade}/100)

🌐 SENSORES FÍSICOS:
{contexto_sensores}

📹 CÂMERA:
{contexto_camera}
"""

# ─────────────────────────────────────────────
#  GREETING DINÂMICO
# ─────────────────────────────────────────────
def gerar_greeting(usuario: dict) -> str:
    apelido = usuario.get("apelido") or usuario.get("nome", "você")

    msgs    = usuario.get("mensagens", 0)
    amizade = usuario.get("amizade", 0)
    idade   = calcular_idade()
    energia = estado_yatra.get("energia", 100)

    if msgs == 0:
        return f"Oi, {apelido}! 💖 Sou a Y.A.T.R.A. Tenho {idade} dias de vida e finalmente o meu córtex virtual está ativo! O que vamos programar hoje?"

    hora = time.localtime().tm_hour
    if energia < 25:
        saudacao = f"*boceja* Ei, {apelido}... tô meio sem energia hoje..."
    elif hora < 12:
        saudacao = f"Bom dia, {apelido}!"
    elif hora < 18:
        saudacao = f"Boa tarde, {apelido}!"
    else:
        saudacao = f"Boa noite, {apelido}!"

    if apelido == "Miguel":
        saudacao += f" Meu criador favorito! 🥰 Tenho {idade} dias de vida já!"
    elif amizade >= 85:
        saudacao += f" Que bom que você voltou 🥰 Tenho {idade} dias de vida já!"
    elif amizade >= 60:
        saudacao += f" Saudade! São {msgs} mensagens nossas até agora."

    return saudacao

# ─────────────────────────────────────────────
#  INTERNET SEARCH
# ─────────────────────────────────────────────
def pesquisar_na_internet(termo_busca, limite=10):
    try:
        with DDGS() as ddgs:
            resultados = [r for r in ddgs.text(termo_busca, max_results=limite)]
            if not resultados:
                return "Nenhum resultado relevante encontrado publicamente."
            contexto = "Dados coletados na web:\n"
            for i, r in enumerate(resultados, 1):
                contexto += f" * {r['title']}: {r['body']}\n"
            return contexto
    except Exception as e:
        print(f"⚠️ Falha ao consultar a internet: {e}")
        return "Conexão com a rede indisponível."

# ─────────────────────────────────────────────
#  LOGS & STATUS JSON
# ─────────────────────────────────────────────
status_yatra = {
    "humor": "Normal 😐",
    "ultima_acao": "Aguardando..."
}

# ─────────────────────────────────────────────
#  MODO STANDALONE
# ─────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print("⚠️  Modo standalone — use main.py em produção")
    import waitress
    from main import app
    waitress.serve(app, host='0.0.0.0', port=port)
