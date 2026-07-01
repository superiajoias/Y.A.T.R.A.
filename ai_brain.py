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
# ★ REGEX DE HUMOR — CENTRALIZADO E ROBUSTO ★
#
# POR QUE O REGEX ANTIGO FALHAVA?
# O padrão \[?HUMOR:\s*([NARTCMXES])\s*\]? tornava os colchetes opcionais
# de forma INDEPENDENTE. Então para [HUMOR:Alegre], ele combinava apenas
# "[HUMOR:A" (pegava só a primeira letra), mas o \]? não alcançava o "]"
# real, deixando "legre]" no texto. Além disso, o discord_bot.py não usava
# re.IGNORECASE, então "humor:a" (minúsculo) nunca era encontrado.
#
# SOLUÇÃO: Dois padrões unidos por |
#   1. \[HUMOR:\s*([NARTCMXES])[^\]]*\]  →  COM colchetes: consome tudo
#      até o "]" fechador, mesmo que a IA escreva "Alegre" em vez de "A"
#   2. \bHUMOR:\s*([NARTCMXES])\S*      →  SEM colchetes: consome a
#      palavra inteira (ex: "HUMOR:alegre")
# ─────────────────────────────────────────────
REGEX_HUMOR = re.compile(
    r'\[HUMOR:\s*([NARTCMXES])[^\]]*\]'   # com colchetes  → [HUMOR:Alegre]
    r'|\bHUMOR:\s*([NARTCMXES])\S*',      # sem colchetes  → HUMOR:alegre
    re.IGNORECASE
)
HUMORES_VALIDOS = frozenset("NARTCMXES")


def extrair_humor(texto: str, humor_fallback: str = "N") -> tuple:
    """
    Extrai o código de humor do texto e devolve (novo_humor, texto_limpo).
    Garante que NENHUM resíduo da tag ([...] ou palavra solta) sobra.

    Exemplos tratados:
        "[HUMOR:A]"       →  ('A', texto sem a tag)
        "[HUMOR:Alegre]"  →  ('A', texto sem a tag)
        "HUMOR:a"         →  ('A', texto sem a tag)
        "HUMOR:alegre"    →  ('A', texto sem a tag)
        (nenhuma tag)     →  (humor_fallback, texto original)
    """
    match = REGEX_HUMOR.search(texto)
    if match:
        # grupo 1 → com colchetes, grupo 2 → sem colchetes
        letra = (match.group(1) or match.group(2) or "").upper()
        novo_humor = letra if letra in HUMORES_VALIDOS else humor_fallback
    else:
        novo_humor = humor_fallback

    # Remove TODAS as ocorrências da tag
    texto_limpo = REGEX_HUMOR.sub("", texto)
    # Limpa espaços duplos que a remoção pode deixar
    texto_limpo = re.sub(r"[ \t]{2,}", " ", texto_limpo).strip()
    return novo_humor, texto_limpo


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
        if any(item.lower() == g.lower() for g in existentes):
            return
        supabase.table("interesses_yatra").insert({
            "user_id":      discord_id,
            "item_gostado": item,
            "intensidade":  1
        }).execute()
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
        "N": "neutra",     "A": "alegre",      "R": "irritada",
        "T": "triste",     "C": "confusa",      "M": "com medo",
        "X": "ansiosa",    "E": "empolgada",    "S": "com sono"
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
SE ESTIVER BRAVA, USE A TAG [HUMOR:R] E SEJA RÍSPIDA. SE ESTIVER TRISTE, USE [HUMOR:T] E SEJA MELANCÓLICA. SE ESTIVER COM MEDO, USE [HUMOR:M] E SEJA CUIDADOSA. SE ESTIVER ANSIOSA, USE [HUMOR:X] E SEJA NERVOSA. SE ESTIVER CONFUSA, USE [HUMOR:C] E SEJA INDECISA. SE ESTIVER COM SONO, USE [HUMOR:S] E SEJA PREGUIÇOSA. SE ESTIVER ALEGRE, USE [HUMOR:A] E SEJA DIVERTIDA. SE ESTIVER EMPOLGADA, USE [HUMOR:E] E SEJA ENTUSIASMADA. OBRIGATORIAMENTE ENTRE COLCHETES [] E SEM NENHUMA OUTRA TAG. APENAS UMA ÚNICA DENTRO DOS COLCHETES.

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
- Código de humor: {humor}
- Estado emocional obrigatório: {emocao_atual}

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
#  MODO STANDALONE (python ai_brain.py diretamente)
#  Em produção, o main.py é o entry point.
# ─────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print("⚠️  Modo standalone — use main.py em produção")
    # Em modo standalone não há Flask server aqui,
    # pois o main.py é o entry point correto.
    # Se precisar de um server rápido para testes:
    import waitress
    from main import app
    waitress.serve(app, host='0.0.0.0', port=port)