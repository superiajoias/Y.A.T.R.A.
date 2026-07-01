"""
main.py — Entry point do Y.A.T.R.A. (Flask + Discord Bot)

CORREÇÕES IMPLEMENTADAS
───────────────────────
1. SESSÃO HTTP
   • Removido o "local_web_user" global. Cada navegador recebe um cookie
     de sessão único gerado no primeiro acesso (anon_<uuid>).
   • Sistema de Login/Cadastro opcional via /register e /login.
     Senha mínima: 6 caracteres. Hash com bcrypt.
   • Após login, a sessão passa a usar o user_id registrado → histórico
     e humores de usuários diferentes NUNCA se misturam.

2. REGEX DE HUMOR
   • Centralizado em ai_brain.extrair_humor() — veja ai_brain.py.
   • Lida com [HUMOR:A], [HUMOR:Alegre], HUMOR:a, etc.

SUPABASE — tabela necessária (rode uma vez no SQL Editor):
───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS usuarios_auth (
    user_id    TEXT PRIMARY KEY,
    email      TEXT UNIQUE NOT NULL,
    senha_hash TEXT NOT NULL,
    criado_em  TIMESTAMPTZ DEFAULT NOW()
);
"""

import sys
import threading
import asyncio
import os
import uuid
import secrets
import json
import bcrypt
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, session

# ── FIX WINDOWS ───────────────────────────────────────────────────────────────
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

load_dotenv()

import ai_brain
from discord_bot import bot

# ── FLASK ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)

# SECRET_KEY obrigatório para assinar o cookie de sessão.
# Em produção, coloque FLASK_SECRET_KEY nas variáveis de ambiente do Render.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

# Sessão expira quando o browser fecha (não permanente por padrão).
# Para manter login entre sessões: app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPER DE SESSÃO
#  Retorna SEMPRE um user_id consistente para o browser atual.
#  • Visitante:  "anon_<12 chars hex>"  gerado UMA vez e salvo no cookie.
#  • Logado:     o user_id registrado, salvo no cookie após /login.
# ═══════════════════════════════════════════════════════════════════════════════
def get_user_id() -> str:
    """
    Lê (ou cria) o user_id da sessão Flask.
    O cookie é assinado com app.secret_key — o cliente não consegue falsificá-lo.
    """
    if "user_id" not in session:
        # Primeira visita: gera ID anônimo único para este browser
        session["user_id"] = "anon_" + uuid.uuid4().hex[:12]
    return session["user_id"]


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH — CADASTRO
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/register", methods=["POST"])
def register():
    """
    Body JSON: { "email": "...", "senha": "...", "nome": "..." }
    Cria conta no Supabase (tabela usuarios_auth) com bcrypt.
    Após sucesso, já loga o usuário na sessão.
    """
    dados = request.get_json(force=True) or {}
    email = (dados.get("email") or "").strip().lower()
    senha = dados.get("senha", "")
    nome  = (dados.get("nome") or email.split("@")[0]).strip()

    # ── Validações ────────────────────────────────────────────────────────────
    if not email or "@" not in email:
        return jsonify({"ok": False, "erro": "E-mail inválido."}), 400
    if len(senha) < 6:
        return jsonify({"ok": False, "erro": "Senha mínima: 6 caracteres."}), 400

    # ── Verifica duplicata ────────────────────────────────────────────────────
    try:
        existing = ai_brain.supabase.table("usuarios_auth") \
            .select("user_id").eq("email", email).limit(1).execute()
        if existing.data:
            return jsonify({"ok": False, "erro": "E-mail já cadastrado."}), 409
    except Exception as e:
        return jsonify({"ok": False, "erro": f"Erro de banco: {e}"}), 500

    # ── Hash bcrypt ───────────────────────────────────────────────────────────
    # bcrypt.gensalt() gera salt aleatório com custo 12 (padrão seguro).
    # A senha nunca é armazenada em texto puro.
    senha_bytes = senha.encode("utf-8")
    senha_hash  = bcrypt.hashpw(senha_bytes, bcrypt.gensalt()).decode("utf-8")

    # ── Cria user_id único para este usuário registrado ───────────────────────
    user_id = "web_" + uuid.uuid4().hex[:16]

    try:
        ai_brain.supabase.table("usuarios_auth").insert({
            "user_id":    user_id,
            "email":      email,
            "senha_hash": senha_hash,
        }).execute()
        # Garante que o perfil existe em usuarios_perfil (histórico isolado)
        ai_brain.obter_ou_criar_usuario(user_id, nome, plataforma="web")
    except Exception as e:
        return jsonify({"ok": False, "erro": f"Erro ao criar conta: {e}"}), 500

    # ── Inicia sessão ─────────────────────────────────────────────────────────
    session["user_id"] = user_id
    session["nome"]    = nome
    session["logado"]  = True
    return jsonify({"ok": True, "user_id": user_id, "nome": nome})


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH — LOGIN
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/login", methods=["POST"])
def login():
    """
    Body JSON: { "email": "...", "senha": "..." }
    bcrypt.checkpw() compara a senha com o hash armazenado.
    """
    dados = request.get_json(force=True) or {}
    email = (dados.get("email") or "").strip().lower()
    senha = dados.get("senha", "")

    if not email or not senha:
        return jsonify({"ok": False, "erro": "E-mail e senha obrigatórios."}), 400

    try:
        res = ai_brain.supabase.table("usuarios_auth") \
            .select("user_id, senha_hash").eq("email", email).limit(1).execute()

        if not res.data:
            return jsonify({"ok": False, "erro": "E-mail não encontrado."}), 404

        row      = res.data[0]
        user_id  = row["user_id"]
        hash_db  = row["senha_hash"].encode("utf-8")

        # bcrypt.checkpw() é tempo-constante — resiste a timing attacks
        if not bcrypt.checkpw(senha.encode("utf-8"), hash_db):
            return jsonify({"ok": False, "erro": "Senha incorreta."}), 401

        # Busca nome do perfil
        perfil = ai_brain.obter_ou_criar_usuario(user_id, email.split("@")[0], plataforma="web")
        nome   = perfil.get("nome") or email.split("@")[0]

        session["user_id"] = user_id
        session["nome"]    = nome
        session["logado"]  = True
        return jsonify({"ok": True, "user_id": user_id, "nome": nome})

    except Exception as e:
        return jsonify({"ok": False, "erro": f"Erro de banco: {e}"}), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH — LOGOUT
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/logout", methods=["POST"])
def logout():
    session.clear()   # Remove todos os dados do cookie assinado
    return jsonify({"ok": True})


# ═══════════════════════════════════════════════════════════════════════════════
#  ME — quem está logado agora?
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/me")
def me():
    user_id = get_user_id()   # Cria anon se não existir
    logado  = session.get("logado", False)
    nome    = session.get("nome", "Visitante")
    return jsonify({"user_id": user_id, "logado": logado, "nome": nome})


# ═══════════════════════════════════════════════════════════════════════════════
#  ROTA RAIZ
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return render_template("index.html")


# ═══════════════════════════════════════════════════════════════════════════════
#  STATUS — carregado pelo JS na abertura da página
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/status")
def status():
    # ← Usa a sessão Flask, NÃO query param user_id
    user_id = get_user_id()
    nome    = session.get("nome", "Visitante")

    usuario = ai_brain.obter_ou_criar_usuario(user_id, nome, plataforma="web")
    apelido = usuario.get("apelido") or usuario.get("nome", "usuário")
    pontos  = usuario.get("amizade", 0)

    return jsonify({
        "idade_dias":       ai_brain.calcular_idade(),
        "mensagens_totais": ai_brain.estado_yatra.get("mensagens_totais", 0),
        "humor_atual":      ai_brain.estado_yatra.get("humor_atual", "N"),
        "energia":          ai_brain.estado_yatra.get("energia", 100),
        "curiosidade":      ai_brain.estado_yatra.get("curiosidade", 70),
        "medo":             ai_brain.estado_yatra.get("medo", 10),
        "greeting":         ai_brain.gerar_greeting(usuario),
        "nivel_amizade":    ai_brain.nivel_amizade(pontos),
        "pontos_amizade":   pontos,
        "apelido":          apelido,
        "logado":           session.get("logado", False),
        "nome":             nome,
    })


# ═══════════════════════════════════════════════════════════════════════════════
#  SENSORES — polling a cada 1s pelo JS
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/sensores")
def sensores():
    return jsonify(ai_brain.telemetria_atual)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENVIAR MENSAGEM
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/enviar", methods=["POST"])
def enviar():
    dados = request.get_json(force=True) or {}
    msg   = dados.get("mensagem", "").strip()

    # ← user_id vem SEMPRE da sessão Flask, nunca do body
    user_id = get_user_id()
    nome    = session.get("nome", "Visitante")

    if not msg:
        return jsonify({
            "resposta": "(mensagem vazia)", "humor": "N",
            "energia": 100, "curiosidade": 70, "medo": 10,
            "sensores": ai_brain.telemetria_atual,
            "nivel_amizade": "Desconhecido", "pontos_amizade": 0,
            "apelido": "usuário"
        }), 400

    usuario    = ai_brain.obter_ou_criar_usuario(user_id, nome, plataforma="web")
    plataforma = usuario.get("plataforma", "web")
    apelido    = usuario.get("apelido") or usuario.get("nome", "usuário")

    # 1. Salva mensagem do usuário
    ai_brain.salvar_no_supabase(user_id, plataforma, "user", msg)

    # 2. Puxa histórico DESTE usuário (isolado pelo user_id da sessão)
    contexto  = ai_brain.puxar_contexto_recente(user_id, limite=15)
    historico = [
        {"role": "system", "content": ai_brain.montar_system_prompt(usuario, user_id)}
    ] + contexto

    try:
        # 3. Chama a IA
        response = ai_brain.client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=historico,
            temperature=0.75
        )
        resposta_ia = response.choices[0].message.content

        # 4. ★ EXTRAI HUMOR E LIMPA O TEXTO ★
        #    Usa a função centralizada de ai_brain — handles [HUMOR:A],
        #    [HUMOR:Alegre], HUMOR:a, sem deixar resíduo no texto final.
        humor_fallback  = ai_brain.estado_yatra.get("humor_atual", "N")
        novo_humor, resposta_clean = ai_brain.extrair_humor(resposta_ia, humor_fallback)

        # 5. Processa gostos marcados com [GOSTO: item]
        resposta_clean = ai_brain.processar_gostos(user_id, resposta_clean)

        # 6. Atualiza humor no Supabase (para ESP32 ler)
        try:
            ai_brain.supabase.table("estado_yatra") \
                .update({"humor_atual": novo_humor}) \
                .eq("id", 1).execute()
        except Exception as e:
            print(f"⚠️  Supabase humor update: {e}")

        # 7. Atualiza estados internos (energia/curiosidade/medo)
        ai_brain.ajustar_estados_internos(novo_humor)

        # 8. Salva resposta da IA
        ai_brain.salvar_no_supabase(user_id, plataforma, "assistant", resposta_clean)

        # 9. Atualiza amizade
        msgs_novo    = usuario.get("mensagens", 0) + 1
        amizade_nova = min(100, usuario.get("amizade", 0) + 1)
        ai_brain.atualizar_usuario(user_id, {"mensagens": msgs_novo, "amizade": amizade_nova})

        return jsonify({
            "resposta":       resposta_clean,
            "humor":          novo_humor,
            "energia":        ai_brain.estado_yatra.get("energia", 100),
            "curiosidade":    ai_brain.estado_yatra.get("curiosidade", 70),
            "medo":           ai_brain.estado_yatra.get("medo", 10),
            "sensores":       ai_brain.telemetria_atual,
            "nivel_amizade":  ai_brain.nivel_amizade(amizade_nova),
            "pontos_amizade": amizade_nova,
            "apelido":        apelido,
        })

    except Exception as err:
        print(f"❌ Erro na API Groq: {err}")
        return jsonify({
            "resposta":       "⚠️ Sistema da Yatra teve um erro interno. Tenta de novo!",
            "humor":          "N",
            "energia":        ai_brain.estado_yatra.get("energia", 100),
            "curiosidade":    ai_brain.estado_yatra.get("curiosidade", 70),
            "medo":           ai_brain.estado_yatra.get("medo", 10),
            "sensores":       ai_brain.telemetria_atual,
            "nivel_amizade":  ai_brain.nivel_amizade(usuario.get("amizade", 0)),
            "pontos_amizade": usuario.get("amizade", 0),
            "apelido":        apelido,
        }), 500


# ═══════════════════════════════════════════════════════════════════════════════
#  ROTAS LEGADAS
# ═══════════════════════════════════════════════════════════════════════════════
@app.route("/status_json")
def status_json():
    ARQUIVO_ESTADO = "estado_yatra.json"
    emocoes = {
        "N": "Normal 😐",   "R": "Raiva 😡",    "T": "Triste 😢",
        "A": "Alegre ✨",   "C": "Confusa 🤔",   "M": "Medo 😰",
        "X": "Ansiosa 😰",  "E": "Empolgada 🚀", "S": "Sono 😴"
    }
    try:
        with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
            estado = json.load(f)
        estado["humor_texto"] = emocoes.get(estado.get("humor_atual", "N"), "Normal")
        return jsonify(estado)
    except Exception as e:
        print(f"Erro status_json: {e}")
        return jsonify({}), 500

@app.route("/logs")
def get_logs():
    humor = ai_brain.estado_yatra.get("humor_atual", "N")
    msgs  = ai_brain.estado_yatra.get("mensagens_totais", 0)
    return f"HUMOR: {humor}\nMENSAGENS: {msgs}\nSENSORES: {json.dumps(ai_brain.telemetria_atual)}"


# ═══════════════════════════════════════════════════════════════════════════════
#  RUNNER FLASK (thread separada)
# ═══════════════════════════════════════════════════════════════════════════════
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    print(f"--- [DEBUG] Flask na porta {port} ---")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("--- [DEBUG] main.py iniciado ---")

    threading.Thread(target=run_flask, daemon=True).start()

    print("--- [DEBUG] Iniciando Discord Bot ---")
    token = os.getenv("DISCORD_TOKEN")
    if token:
        try:
            bot.run(token)
        except Exception as e:
            print(f"❌ Bot parou: {e}")
    else:
        print("❌ DISCORD_TOKEN não encontrado — rodando só Flask")
        import time
        while True:
            time.sleep(60)