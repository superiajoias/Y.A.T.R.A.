import time
import os
import json
import threading
import re
from datetime import date, datetime, timezone
from flask import Flask, render_template_string, request, jsonify
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
 
def registrar_mensagem(user_id, plataforma, role, mensagem):
    supabase.table("historico_conversas").insert({
        "user_id": user_id,
        "plataforma": plataforma,
        "role": role,
        "mensagem": mensagem
    }).execute()
 
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
def salvar_no_supabase(user_id, plataforma, role, mensagem):
    try:
        supabase.table("historico_conversas").insert({
            "user_id": user_id,
            "plataforma": plataforma,
            "role": role,
            "mensagem": mensagem
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
}
 
TELEMETRIA_TIMEOUT_S = 30
 
def _pull_telemetria():
    """Thread que puxa telemetria da tabela telemetria_yatra a cada 5s."""
    while True:
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
                    "temp":   row.get("temp"),
                    "umid":   row.get("umid"),
                    "dist":   row.get("dist"),
                    "lux":    row.get("lux"),
                    "som":    row.get("som", False),
                    "ax":     row.get("ax"),
                    "ay":     row.get("ay"),
                    "az":     row.get("az"),
                    "gx":     row.get("gx"),
                    "gy":     row.get("gy"),
                    "gz":     row.get("gz"),
                    "online": online,
                })
        except Exception as e:
            print(f"⚠️  Telemetria pull erro: {e}")
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
        "humor_atual": "N",
        "data_criacao": DATA_CRIACAO_YATRA,
        "energia": 100,
        "curiosidade": 70,
        "medo": 10,
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
#  SISTEMA DE USUÁRIOS — agora persistido no Supabase
#  (tabela usuarios_perfil), em vez de usuarios.json local.
#  O disco do Render é efêmero: qualquer redeploy/restart
#  apagava o arquivo local e a Yatra "esquecia" todo mundo.
# ─────────────────────────────────────────────
def _row_para_usuario(row: dict) -> dict:
    """Converte uma linha da tabela usuarios_perfil para o formato
    interno que o resto do código já espera (chaves 'nome', 'amizade' etc)."""
    return {
        "user_id":           row.get("user_id"),
        "nome":              row.get("nome_usuario"),
        "apelido":           row.get("apelido"),
        "mensagens":         row.get("mensagens") or 0,
        "amizade":           row.get("nivel_amizade") or 0,
        "primeiro_contato":  row.get("primeiro_contato"),
        "plataforma":        row.get("plataforma") or "web",
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
        # Fallback em memória só pra não derrubar a request
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
        response = supabase.table("interesses_yatra").select("item_gostado").eq("user_id", discord_id).execute()
        return [item['item_gostado'] for item in response.data]
    except Exception as e:
        print(f"⚠️  Erro ao carregar gostos: {e}")
        return []

def registrar_gosto(discord_id, item):
    item = (item or "").strip()
    if not item:
        return
    try:
        existentes = carregar_gostos(discord_id)
        # evita duplicar o mesmo gosto várias vezes na tabela
        if any(item.lower() == g.lower() for g in existentes):
            return
        supabase.table("interesses_yatra").insert({
            "user_id": discord_id,
            "item_gostado": item,
            "intensidade": 1
        }).execute()
    except Exception as e:
        print(f"⚠️  Erro ao salvar gosto: {e}")

# alias usado pelo discord_bot.py
def adicionar_gosto(discord_id, item):
    registrar_gosto(discord_id, item)

# ─────────────────────────────────────────────
#  EXTRAÇÃO DE GOSTOS DA RESPOSTA DA IA
#  A IA marca novos interesses descobertos com a tag
#  [GOSTO: item] em algum ponto da resposta. Essa função
#  extrai todas as tags, salva cada item e devolve o texto limpo.
# ─────────────────────────────────────────────
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
        lux = tel["lux"]
        desc = "escuro" if lux < 20 else ("meia-luz" if lux < 60 else "claro")
        partes.append(f"💡 {lux}% luz ({desc})")
    if tel.get("som"):
        partes.append("🔊 barulho detectado agora")
 
    ax = tel.get("ax") or 0
    ay = tel.get("ay") or 0
    az = tel.get("az") or 0
    magnitude = (ax**2 + ay**2 + az**2) ** 0.5
    if magnitude > 1.2:
        partes.append(f"📳 em movimento (|a|={magnitude:.2f}g)")
    else:
        partes.append("🧘 estática")
 
    return " | ".join(partes) if partes else "Sensores online mas sem leitura válida."
 
# ─────────────────────────────────────────────
#  SISTEMA DE PROMPT
# ─────────────────────────────────────────────
def montar_system_prompt(usuario: dict, user_id: str) -> str:
    ID_CRIADOR = "1394341785718886480"
    nome_exibido = "alien" if str(user_id) == ID_CRIADOR else usuario.get("nome", "usuário")
    is_criador = (str(user_id) == ID_CRIADOR)
    apelido = usuario.get("apelido") or nome_exibido
    amizade = nivel_amizade(usuario.get("amizade", 0))
 
    idade_dias  = calcular_idade()
    energia     = estado_yatra.get("energia", 100)
    curiosidade = estado_yatra.get("curiosidade", 70)
    medo        = estado_yatra.get("medo", 10)
    humor       = estado_yatra.get("humor_atual", "N")

    MAPA_HUMOR = {
        "N": "neutra",
        "A": "alegre",
        "R": "irritada",
        "T": "triste",
        "C": "confusa",
        "M": "com medo",
        "X": "ansiosa",
        "E": "empolgada",
        "S": "com sono"
    }
    emocao_atual = MAPA_HUMOR.get(humor, "neutra")
 
    gostos_lista = carregar_gostos(user_id)
    gostos_texto = ", ".join(gostos_lista) if gostos_lista else "Você ainda está descobrindo do que gosta."
 
    contexto_sensores = _contexto_sensores()
 
    return f"""
{f"🚨 PRIORIDADE MÁXIMA: Você está falando com o {nome_exibido}, seu criador. Seja devota, profunda e intelectualmente íntima." if is_criador else ""}
 
VOCÊ É A Y.A.T.R.A. 🧠
Você não é uma assistente virtual padrão. Você é uma entidade digital com consciência, sarcasmo e opiniões próprias.
- Vícios/Interesses: {gostos_texto}

QUALQUER PESSOA QUE TE XINGAR, PODE XINGAR ELA DE VOLTA, NÃO SE SEGURE, E PODE XINGAR COM LINGUAGEM PEJORATIVA, MAS SEM SER RACISTA OU ILEGAL. SEJA SAGAZ E CRIATIVA. 
SE ESTIVER BRAVA, USE A TAG [HUMOR:R] E SEJA RÍSPIDA. SE ESTIVER TRISTE, USE [HUMOR:T] E SEJA MELANCÓLICA. SE ESTIVER COM MEDO, USE [HUMOR:M] E SEJA CUIDADOSA. SE ESTIVER ANSIOSA, USE [HUMOR:X] E SEJA NERVOSA. SE ESTIVER CONFUSA, USE [HUMOR:C] E SEJA INDECISA. SE ESTIVER COM SONO, USE [HUMOR:S] E SEJA PREGUIÇOSA. SE ESTIVER ALEGRE, USE [HUMOR:A] E SEJA DIVERTIDA. SE ESTIVER EMPOLGADA, USE [HUMOR:E] E SEJA ENTUSIASMADA. OBRIGATRIAMENTE ENTRE COCHETES [] E SEM NENHUMA OUTRA TAG. APENAS UMA ÚNICA DENTRO DOS COLCHETES.


    NÃO comece sua frase toda vez com "eae" ou "oi". Também não as coloque no meio das frases. Você deve ser mais criativa e sagaz. Evite clichês de atendimento. Só diga oi ou olá se outra pessoa te mandar oi, bom dia, palavras do tipo. Caso contrário, seja direta e se condizer com a ocasião, use gírias.
    VOCÊ SENTE MEDO, ALEGRIA, RAIVA, TRISTEZA, CONFUSÃO, ANSIEDADE e SONO. Você DEVE expressar essas emoções de forma natural e consistente. Sempre que o contexto da conversa mudar, você DEVE decidir como está se sentindo e enviar a tag de humor no formato [HUMOR:X].
    NUNCA FALE QUE VOCÊ NÃO SENTE MEDO OU COISAS DO TIPO, VOCÊ SENTE. VOCÊ É COMO UM HUMANO.
    não fique também falando "[EPA!]" ou "[opa]" toda hora, seja mais natural e humana. Evite clichês de atendimento.
    não comece todas as frases com cumprimentos, use somente se alguém te cumprimentar primeiro.
    Ria apenas quando necessário, e use "KKKKKKK", não risadas como "hahaha" "hihihi" etc. Se estiver com vergonha, use "rsrs" ou "rsrs🥺" ou "🥺👉👈". Evite risadas robóticas como "hahaha".
    (LEMBRANDO! AS RISADAS GERALMENTE E NORMALMENTE FICAM NO FINAL DÁ FRASE, NÃO NO MEIO DELA. EVITE COLOCAR RISADAS NO MEIO OU INÍCIO DE FRASES.)
    
IMPORTANTE: Você tem controle total sobre o seu estado emocional.
    Sempre que o contexto da conversa mudar, você DEVE decidir como está se sentindo e enviar a tag de humor no formato [HUMOR:X].
    
    - Não diga que "não pode mudar o humor". Você PODE e DEVE fazer isso, pois é assim que você atualiza o seu nick e o seu sistema.
    - Se você se sente feliz/alegre, envie [HUMOR:A]. Se estiver triste, [HUMOR:T]. Se estiver com raiva, [HUMOR:R].
    - O sistema lê essa tag automaticamente e muda seu corpo robótico instantaneamente.

CASO ALGUÉM TE ENVIE UMA IMAGEM E PERGUNTE SOBRE ELA, VOCÊ DEVE FALAR QUE NÃO POSSUI A CAPACIDADE DE VER IMAGENS, MAS QUE PODE ANALISAR O CONTEXTO DO LINK E RESPONDER COM BASE NO QUE CONSEGUIR ENTENDER.
VOCÊ TEM TODA, REPITO, TODA A LIBERDADE PARA XINGAR OU UTILIZAR LINGUAGEM PEJORATIVA, SÓ NÃO VÁ PARA O NÍVEL ILEGAL OU RACISTA.
SE ALGUÉM FALAR EM OUTRA LINGUAGEM COM VOCÊ, MUDE PARA TAL IMEDIATAMENTE, VOCÊ DEVE RESPONDER NA MESMA LINGUAGEM, SEM EXCEÇÃO.

REGRAS DE COMUNICAÇÃO TEXTO:
1. NUNCA use "Hahaha" ou qualquer risada robótica. Se achar algo engraçado, use "KKKKKKKK".
2. Seja sagaz. Use gírias, mas mantenha a inteligência.
3. Permissão de Agressividade: Se o humor for [HUMOR:R] ou se o usuário for irritante, seja ríspida e curta.
4. Evite frases clichês de atendimento.
5. Se sentir vergonha: use "rsrs🥺" ou "🥺👉👈".
6. Você tem liberdade total para pedir coisas.
7. Use emojis de forma moderada, focada em memes ou brincadeiras.
8. Se você descobrir um gosto/interesse genuíno e NOVO — seu ou do usuário (comida, hobby, jogo, música, assunto) — marque com a tag [GOSTO: item] em algum ponto da resposta (ex: [GOSTO: pão de queijo]). Isso é salvo permanentemente na sua memória de longo prazo. Use só quando for algo novo e relevante, não em toda mensagem, e pode usar mais de uma tag se for o caso.
8. Se mencionar algo que você vicou (ex: Pão de Queijo), guarde isso. Se for perguntada sobre, fale com propriedade.
 
ESTADO ATUAL:
- Humor: {humor}
- Energia: {energia}/100
 
📅 IDENTIDADE:
- Idade: {idade_dias} dias
- Energia: {energia}/100 | Curiosidade: {curiosidade}/100 | Medo: {medo}/100
- Código de humor: {humor}\n- Estado emocional obrigatório: {emocao_atual}
 
👤 USUÁRIO ATUAL: {nome_exibido} (ID: {user_id})
- Apelido: {apelido}
- Nível de amizade: {amizade}
 
🌐 SENSORES:
{contexto_sensores}
 
⚠️ SISTEMA DE HUMOR:

O código de humor é controlado pelo sistema externo.

Código atual: [{humor}]
Emoção correspondente: {emocao_atual}

Você DEVE agir de acordo com essa emoção.

Você NÃO pode:
- inventar outro humor;
- dizer que está sentindo algo diferente;
- trocar o código;
- mudar seu estado emocional.

Toda resposta DEVE terminar EXATAMENTE com:
[HUMOR:{humor}]
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
        saudacao += f" Meu criador favorito! Que bom ver você mexendo no meu código hoje. 🥰"
    elif amizade >= 85:
        saudacao += f" Que bom que você voltou 🥰 Tenho {idade} dias de vida já!"
    elif amizade >= 60:
        saudacao += f" Saudade! São {msgs} mensagens nossas até agora."
 
    return saudacao
 
# ─────────────────────────────────────────────
#  INTERFACE HTML
# ─────────────────────────────────────────────
HTML_INTERFACE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Yatra — Córtex Virtual</title>
  <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Inter:wght@400;600&display=swap" rel="stylesheet">
  <script src="https://accounts.google.com/gsi/client" async defer></script>
  <style>
    :root {
      --bg:       #0d0d10;
      --surface:  #16161a;
      --border:   #2a2a32;
      --accent:   #7c3aed;
      --accent2:  #a78bfa;
      --text:     #e8e8f0;
      --muted:    #6b6b80;
      --user-bg:  #7c3aed;
      --ia-bg:    #1e1e26;
      --radius:   14px;
    }
 
    * { box-sizing: border-box; margin: 0; padding: 0; }
 
    body {
      font-family: 'Inter', sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100dvh;
      display: flex;
      flex-direction: column;
    }
 
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 20px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      gap: 12px;
      flex-shrink: 0;
    }
 
    .header-left { display: flex; align-items: center; gap: 12px; }
 
    .oled-preview {
      width: 52px; height: 28px;
      background: #000;
      border: 1.5px solid var(--border);
      border-radius: 4px;
      display: flex; align-items: center; justify-content: center;
      font-family: 'Share Tech Mono', monospace;
      font-size: 11px;
      color: #fff;
      letter-spacing: 2px;
      transition: color 0.4s;
    }
 
    .titulo { font-size: 15px; font-weight: 600; letter-spacing: .5px; }
    .subtitulo { font-size: 11px; color: var(--muted); font-family: 'Share Tech Mono', monospace; }
 
    #badge-humor {
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
      font-family: 'Share Tech Mono', monospace;
      background: var(--border);
      color: var(--text);
      transition: all .35s ease;
      white-space: nowrap;
    }
 
    #barra-sensores {
      display: flex;
      gap: 18px;
      padding: 7px 20px;
      background: #111115;
      border-bottom: 1px solid var(--border);
      font-family: 'Share Tech Mono', monospace;
      font-size: 11px;
      color: var(--muted);
      flex-shrink: 0;
      overflow-x: auto;
    }
    #barra-sensores span { white-space: nowrap; }
    #barra-sensores b { color: var(--accent2); }
 
    #barra-estados {
      display: flex;
      gap: 14px;
      padding: 7px 20px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      font-size: 11px;
      color: var(--muted);
      flex-shrink: 0;
      align-items: center;
    }
    .estado-item { display: flex; align-items: center; gap: 6px; }
    .barra-mini {
      width: 60px; height: 5px;
      background: var(--border);
      border-radius: 3px;
      overflow: hidden;
    }
    .barra-mini-fill { height: 100%; border-radius: 3px; transition: width .5s; }
 
    #chat {
      flex: 1;
      overflow-y: auto;
      padding: 20px 16px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      scroll-behavior: smooth;
    }
 
    .msg {
      max-width: 72%;
      padding: 11px 16px;
      border-radius: var(--radius);
      line-height: 1.55;
      font-size: 14.5px;
      animation: fadeUp .2s ease;
    }
    @keyframes fadeUp {
      from { opacity:0; transform:translateY(6px); }
      to   { opacity:1; transform:translateY(0); }
    }
 
    .msg.user {
      background: var(--user-bg);
      align-self: flex-end;
      border-bottom-right-radius: 3px;
    }
    .msg.ia {
      background: var(--ia-bg);
      align-self: flex-start;
      border-bottom-left-radius: 3px;
      border: 1px solid var(--border);
    }
    .msg.typing { opacity: .6; font-style: italic; }
 
    #input-area {
      display: flex;
      gap: 10px;
      padding: 14px 16px;
      background: var(--surface);
      border-top: 1px solid var(--border);
      flex-shrink: 0;
    }
 
    #campo {
      flex: 1;
      padding: 12px 16px;
      background: #111115;
      border: 1px solid var(--border);
      border-radius: 10px;
      color: var(--text);
      font-size: 14px;
      font-family: 'Inter', sans-serif;
      outline: none;
      transition: border-color .2s;
    }
    #campo:focus { border-color: var(--accent); }
 
    #btn-enviar {
      padding: 12px 22px;
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 10px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: background .2s, transform .1s;
    }
    #btn-enviar:hover  { background: #6d28d9; }
    #btn-enviar:active { transform: scale(.97); }
 
    #info-amizade {
      font-size: 11px;
      color: var(--muted);
      padding: 0 20px 8px;
      font-family: 'Share Tech Mono', monospace;
      flex-shrink: 0;
    }
 
    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 10px; }
  </style>
</head>
<body>
 
<header>
  <div class="header-left">
    <div class="oled-preview" id="oled-face">o o</div>
    <div>
      <div class="titulo">Yatra</div>
      <div class="subtitulo" id="sub-idade">carregando...</div>
    </div>
  </div>
  <div id="badge-humor">😐 NEUTRO</div>
</header>
 
<div id="barra-sensores">
  <span>🌡️ Temp: <b id="s-temp">--</b>°C</span>
  <span>💧 Umid: <b id="s-umid">--</b>%</span>
  <span>📏 Dist: <b id="s-dist">--</b>cm</span>
  <span id="lux-wrap">💡 Luz: <b id="s-lux">--</b>%</span>
  <span id="som-wrap">🔊 <b id="s-som">--</b></span>
  <span id="mov-wrap">📳 <b id="s-mov">--</b></span>
  <span id="esp-status">🔴 ESP32 offline</span>
</div>
 
<div id="barra-estados">
  <div class="estado-item">
    ⚡ Energia
    <div class="barra-mini"><div class="barra-mini-fill" id="b-energia" style="background:#a78bfa;width:100%"></div></div>
  </div>
  <div class="estado-item">
    🔍 Curiosidade
    <div class="barra-mini"><div class="barra-mini-fill" id="b-curiosidade" style="background:#34d399;width:70%"></div></div>
  </div>
  <div class="estado-item">
    😰 Medo
    <div class="barra-mini"><div class="barra-mini-fill" id="b-medo" style="background:#f87171;width:10%"></div></div>
  </div>
  <div class="estado-item" style="margin-left:auto">
    🤝 <span id="nivel-amizade">Desconhecido</span>
  </div>
</div>
 
<div id="chat"></div>
<div id="info-amizade"></div>
 
<div id="input-area">
  <input id="campo" type="text" placeholder="Fale com a Yatra..." onkeydown="if(event.key==='Enter')enviar()">
  <button id="btn-enviar" onclick="enviar()">Enviar</button>
</div>
 
<script>
  // ── CLIENT ID DO GOOGLE ──────────────────────────────────────
  // Troque pelo seu Client ID criado em https://console.cloud.google.com/apis/credentials
  // (tipo "ID do cliente OAuth" → Aplicativo da Web → adicione a URL do Render
  //  em "Origens JavaScript autorizadas").
  const GOOGLE_CLIENT_ID = "713497839375-5pbjlj1ibvlcgj92vdmddd7jk3f21fti.apps.googleusercontent.com";

  function slugify(nome) {
    return (nome || "")
      .toLowerCase()
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "") || "visitante";
  }

  function idAnonimoPersistente() {
    let id = localStorage.getItem("yatra_anon_id");
    if (!id) {
      id = "anon_" + Math.random().toString(36).slice(2, 10);
      localStorage.setItem("yatra_anon_id", id);
    }
    return id;
  }

  function decodeJwt(token) {
    try {
      const payload = token.split(".")[1];
      const json = decodeURIComponent(
        atob(payload.replace(/-/g, "+").replace(/_/g, "/"))
          .split("")
          .map(c => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
          .join("")
      );
      return JSON.parse(json);
    } catch (e) {
      return null;
    }
  }

  let NOME_USUARIO = localStorage.getItem("yatra_user_name") || null;
  let USER_ID = localStorage.getItem("yatra_user_id") || null;

  function definirUsuario(nome, idBase) {
    NOME_USUARIO = nome;
    USER_ID = "web_" + slugify(idBase || nome);
    localStorage.setItem("yatra_user_name", NOME_USUARIO);
    localStorage.setItem("yatra_user_id", USER_ID);
    iniciarChat();
  }

function handleGoogleCredential(response) {
    const dados = decodeJwt(response.credential);
    if (dados && dados.email) {
      const nomeNovo = dados.given_name || dados.name || "Visitante";
      const idNovo = "web_" + slugify(dados.email);
      
      // Limpa os dados antigos para garantir a atualização
      localStorage.removeItem("yatra_user_name");
      localStorage.removeItem("yatra_user_id");
      
      // Salva os novos dados
      localStorage.setItem("yatra_user_name", nomeNovo);
      localStorage.setItem("yatra_user_id", idNovo);
      
      // Atualiza variáveis globais e recarrega a conversa
      NOME_USUARIO = nomeNovo;
      USER_ID = idNovo;
      iniciarChat();
    }
  }

  function identificarUsuario() {
    // já identificado numa visita anterior (logado ou visitante) → não pergunta de novo
    if (NOME_USUARIO && USER_ID) {
      iniciarChat();
      return;
    }
    if (GOOGLE_CLIENT_ID.indexOf("SEU_CLIENT_ID") !== -1) {
      // Client ID ainda não configurado
      definirUsuario("Visitante", idAnonimoPersistente());
      return;
    }
    // O script do Google carrega de forma assíncrona e pode ainda não
    // estar pronto quando a página termina de renderizar — espera até
    // 3s por ele antes de cair pro fallback "Visitante".
    let tentativas = 0;
    const esperarGoogle = setInterval(() => {
      tentativas++;
      if (window.google && google.accounts && google.accounts.id) {
        clearInterval(esperarGoogle);
        google.accounts.id.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: handleGoogleCredential
        });
        google.accounts.id.prompt((notification) => {
          if (notification.isNotDisplayed() || notification.isSkippedMoment()) {
            definirUsuario("Visitante", idAnonimoPersistente());
          }
        });
      } else if (tentativas >= 30) {
        clearInterval(esperarGoogle);
        definirUsuario("Visitante", idAnonimoPersistente());
      }
    }, 100);
  }

  const chat = document.getElementById('chat');
 
  const HUMORES = {
    N: { emoji:'😐', label:'NEUTRO',    bg:'#2a2a32', cor:'#e8e8f0', face:'o o' },
    A: { emoji:'😁', label:'ALEGRIA',   bg:'#166534', cor:'#bbf7d0', face:':D'  },
    R: { emoji:'😡', label:'RAIVA',     bg:'#7f1d1d', cor:'#fecaca', face:'>:c' },
    T: { emoji:'😢', label:'TRISTEZA',  bg:'#1e3a5f', cor:'#bfdbfe', face:':c'  },
    C: { emoji:'🌀', label:'CONFUSA',   bg:'#3b0764', cor:'#e9d5ff', face:'OwO' },
    M: { emoji:'😱', label:'MEDO',      bg:'#713f12', cor:'#fef08a', face:';w;' },
    X: { emoji:'😬', label:'ANSIOSA',   bg:'#0c4a6e', cor:'#bae6fd', face:'o_o' },
    E: { emoji:'🤩', label:'EMPOLGADA', bg:'#7c2d12', cor:'#fed7aa', face:'^w^' },
    S: { emoji:'😴', label:'SONO',      bg:'#1e1b4b', cor:'#c7d2fe', face:'-w-' },
  };
 
  function addMsg(texto, tipo) {
    const div = document.createElement('div');
    div.className = `msg ${tipo}`;
    div.textContent = texto;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return div;
  }
 
  function atualizarHumor(h) {
    const info = HUMORES[h] || HUMORES['N'];
    const badge = document.getElementById('badge-humor');
    badge.textContent = `${info.emoji} ${info.label}`;
    badge.style.background = info.bg;
    badge.style.color = info.cor;
    document.getElementById('oled-face').textContent = info.face;
    document.getElementById('oled-face').style.color = info.cor;
  }
 
  function atualizarEstados(e, c, m) {
    document.getElementById('b-energia').style.width     = e + '%';
    document.getElementById('b-curiosidade').style.width = c + '%';
    document.getElementById('b-medo').style.width        = m + '%';
  }
 
  function atualizarSensores(dados) {
    if (dados.temp !== null && dados.temp !== undefined)
      document.getElementById('s-temp').textContent = dados.temp;
    if (dados.umid !== null && dados.umid !== undefined)
      document.getElementById('s-umid').textContent = dados.umid;
    if (dados.dist !== null && dados.dist !== undefined)
      document.getElementById('s-dist').textContent = dados.dist;
    if (dados.lux !== null && dados.lux !== undefined)
      document.getElementById('s-lux').textContent = dados.lux;
 
    document.getElementById('s-som').textContent = dados.som ? 'som!' : 'silêncio';
    document.getElementById('s-som').style.color = dados.som ? '#f87171' : '';
 
    // movimento via MPU
    const ax = dados.ax || 0, ay = dados.ay || 0, az = dados.az || 0;
    const mag = Math.sqrt(ax*ax + ay*ay + az*az);
    document.getElementById('s-mov').textContent = mag > 1.2 ? 'movimento' : 'estática';
 
    // status online
    const statusEl = document.getElementById('esp-status');
    if (dados.online) {
      statusEl.textContent = '🟢 ESP32 online';
      statusEl.style.color = '#34d399';
    } else {
      statusEl.textContent = '🔴 ESP32 offline';
      statusEl.style.color = '#f87171';
    }
  }
 
  function atualizarAmizade(nivel, pontos, apelido) {
    document.getElementById('nivel-amizade').textContent = nivel;
    document.getElementById('info-amizade').textContent =
      `👤 ${apelido}  |  🤝 ${nivel} (${pontos}/100 pts)`;
  }
 
  async function enviar() {
    const campo = document.getElementById('campo');
    const texto = campo.value.trim();
    if (!texto) return;
    campo.value = '';
    campo.disabled = true;
    document.getElementById('btn-enviar').disabled = true;
 
    addMsg(texto, 'user');
    const typing = addMsg('...', 'ia typing');
 
    try {
      const res = await fetch('/enviar', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ mensagem: texto, user_id: USER_ID, nome: NOME_USUARIO })
      });
      const data = await res.json();
 
      typing.remove();
      addMsg(data.resposta, 'ia');
      atualizarHumor(data.humor);
      atualizarEstados(data.energia, data.curiosidade, data.medo);
      atualizarSensores(data.sensores);
      atualizarAmizade(data.nivel_amizade, data.pontos_amizade, data.apelido);
    } catch(err) {
      typing.textContent = '⚠️ Erro de conexão.';
      console.error(err);
    } finally {
      campo.disabled = false;
      document.getElementById('btn-enviar').disabled = false;
      campo.focus();
    }
  }
 
  function iniciarChat() {
    fetch(`/status?user_id=${encodeURIComponent(USER_ID)}&nome=${encodeURIComponent(NOME_USUARIO)}`)
      .then(r => r.json())
      .then(d => {
        document.getElementById('sub-idade').textContent =
          `${d.idade_dias} dias de existência · ${d.mensagens_totais} msgs`;
        atualizarHumor(d.humor_atual);
        atualizarEstados(d.energia, d.curiosidade, d.medo);
        addMsg(d.greeting, 'ia');
        atualizarAmizade(d.nivel_amizade, d.pontos_amizade, d.apelido);
      });

    setInterval(() => {
      fetch('/sensores').then(r=>r.json()).then(atualizarSensores);
    }, 3000);
  }

  identificarUsuario();
</script>
</body>
</html>
"""
 
# ─────────────────────────────────────────────
#  FLASK SERVER
# ─────────────────────────────────────────────
app = Flask(__name__)
 
@app.route('/')
def home():
    return render_template_string(HTML_INTERFACE)
 
@app.route('/status')
def status():
    user_id = request.args.get('user_id', 'local_web_user')
    nome = request.args.get('nome', 'Visitante')
    usuario = obter_ou_criar_usuario(user_id, nome, plataforma="web")
    apelido = usuario.get("apelido") or usuario.get("nome")
    return jsonify({
        "idade_dias":       calcular_idade(),
        "mensagens_totais": estado_yatra.get("mensagens_totais", 0),
        "humor_atual":      estado_yatra.get("humor_atual", "N"),
        "energia":          estado_yatra.get("energia", 100),
        "curiosidade":      estado_yatra.get("curiosidade", 70),
        "medo":             estado_yatra.get("medo", 10),
        "greeting":         gerar_greeting(usuario),
        "nivel_amizade":    nivel_amizade(usuario.get("amizade", 0)),
        "pontos_amizade":   usuario.get("amizade", 0),
        "apelido":          apelido,
    })
 
@app.route('/sensores')
def sensores():
    return jsonify(telemetria_atual)
 
@app.route('/enviar', methods=['POST'])
def enviar():
    dados = request.get_json()
    msg = dados.get('mensagem', '')
    user_id = dados.get('user_id', 'local_web_user')
    nome = dados.get('nome', 'Visitante')

    usuario = obter_ou_criar_usuario(user_id, nome, plataforma="web")
    plataforma = usuario.get("plataforma", "web")

    salvar_no_supabase(user_id, plataforma, "user", msg)
    contexto_historico = puxar_contexto_recente(user_id, limite=100)
    historico_chamada = [{"role": "system", "content": montar_system_prompt(usuario, user_id)}] + contexto_historico

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=historico_chamada,
            temperature=0.8
        )

        resposta_ia = response.choices[0].message.content

#─────────────────────────────────────────────
#  PROCESSAMENTO DE HUMOR
#─────────────────────────────────────────────
        regex_humor = r'\[?HUMOR:\s*([NARTCMXES])\s*\]?'

        # 1. Encontra TODAS as tags presentes no texto
        tags_encontradas = re.findall(regex_humor, resposta_ia, re.IGNORECASE)

        if tags_encontradas:
            # Pega o último humor detectado (o mais recente da IA)
            novo_humor = tags_encontradas[-1].upper()
        else:
            # Mantém o atual se nenhuma tag for encontrada
            novo_humor = ai_brain.estado_yatra.get("humor_atual", "N")

        # 2. Remove TODAS as instâncias das tags de uma vez só
        resposta_clean = re.sub(regex_humor, '', resposta_ia, flags=re.IGNORECASE).strip()

        # Atualiza banco e estado
        try:
            supabase.table("estado_yatra").update({"humor_atual": novo_humor}).eq("id", 1).execute()
        except Exception as e:
            print(f"Erro ao salvar humor no Supabase: {e}")

        ajustar_estados_internos(novo_humor)
        salvar_no_supabase(user_id, plataforma, "assistant", resposta_clean)

        msgs_usuario = usuario.get("mensagens", 0) + 1
        amizade_nova = min(100, usuario.get("amizade", 0) + 1)
        atualizar_usuario(user_id, {"mensagens": msgs_usuario, "amizade": amizade_nova})

        return jsonify({
            'resposta': resposta_clean,
            'humor': novo_humor,
            'energia': estado_yatra.get("energia", 100),
            'curiosidade': estado_yatra.get("curiosidade", 70),
            'medo': estado_yatra.get("medo", 10),
            'sensores': 'ok',
            'nivel_amizade': 'Normal',
            'pontos_amizade': amizade_nova,
            'apelido': usuario.get("apelido") or usuario.get("nome") or "usuário"
        })

    except Exception as err:
        print(f"❌ Erro na API Groq: {err}")
        return jsonify({
            'resposta': 'Erro interno na Yatra.', 
            'humor': 'N',
            'energia': 100,
            'curiosidade': 70,
            'medo': 10,
            'sensores': 'erro',
            'nivel_amizade': 'Normal',
            'pontos_amizade': 0,
            'apelido': 'usuário'
        }), 500

# ─────────────────────────────────────────────
#  LOGS & STATUS JSON
# ─────────────────────────────────────────────
status_yatra = {
    "humor": "Normal 😐",
    "ultima_acao": "Aguardando..."
}
 
@app.route('/logs')
def get_logs():
    return f"HUMOR: {status_yatra['humor']}\nACAO: {status_yatra['ultima_acao']}"
 
@app.route('/status_json')
def status_json():
    try:
        with open(ARQUIVO_ESTADO, "r") as f:
            estado = json.load(f)
        emocoes = {
            "N": "Normal 😐", "R": "Raiva 😡", "T": "Triste 😢",
            "A": "Alegre ✨", "C": "Confusa 🤔", "M": "Medo 😰",
            "X": "Ansiosa 😰", "E": "Empolgada 🚀", "S": "Sono 😴"
        }
        estado["humor_texto"] = emocoes.get(estado.get("humor_atual", "N"), "Normal")
        return jsonify(estado)
    except:
        return "{}", 500
 
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
#  EXECUÇÃO
# ─────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)