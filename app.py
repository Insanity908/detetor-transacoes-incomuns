"""Interface web do detetor de fraudes financeiras.

Em produção, use o iniciar.bat (servidor waitress). Para desenvolvimento:
    python app.py
e abra http://127.0.0.1:5000
"""
from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

import pandas as pd
from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask.logging import default_handler

from detector import acesso, api, repositorio, tarefas, web_alertas, web_api
from detector.acesso import auditar, utilizador_atual
from detector.artefatos import carregar
from detector.banco import criar_fabrica_sessoes, criar_motor, descrever, migrar, verificar_ligacao
from detector.configuracao import Configuracao, ler_configuracao
from detector.entrada import MAX_BYTES, ler_csv, validar
from detector.exemplos import DESCRICOES, criar_exemplos
from detector.exemplos import NOMES as NOMES_EXEMPLOS
from detector.explicacao import formatar_decimal, formatar_mt, nome_categoria
from detector.avaliacao import NOMES_PERIODO
from detector.gerador import CATEGORIAS, CIDADES, NOMES_TIPO_FRAUDE, PAISES_ESTRANHOS, PAIS_HABITUAL
from detector.modelos import NOMES, ORDEM_SIMPLICIDADE
from detector.perfis import perfil_cliente
from detector.registo import configurar_registo
from detector.servico import avaliar_transacoes

RAIZ = Path(__file__).parent
CAMPOS = ("cliente", "data_hora", "valor", "cidade", "pais", "categoria")
MAX_LINHAS_TABELA = 500
ANALISES_RECENTES = 10
VERSAO = "1.4.0"
# Páginas que funcionam sem modelos treinados: contas, utilizadores e auditoria.
SEM_MODELOS = frozenset({"static", "saude", "entrar", "entrar_post", "sair", "senha", "senha_post", "utilizadores",
                         "utilizadores_post", "utilizador", "utilizador_post", "utilizador_senha", "ver_auditoria",
                         *web_alertas.ENDPOINTS, *web_api.ENDPOINTS, "tarefa"})
GRAFICOS = ("matriz_confusao.png", "importancia.png", "comparacao.png")
INICIO_FORMULA = ("=", "+", "-", "@")
INICIO_PERIGOSO = ("\t", "\r")


def _neutralizar(texto: str) -> str:
    """Impede que um texto do ficheiro vire fórmula ao abrir o CSV no Excel."""
    perigoso = texto.startswith(INICIO_PERIGOSO) or texto.lstrip().startswith(INICIO_FORMULA)
    return "'" + texto if perigoso else texto


def _csv_excel(resultado: pd.DataFrame) -> bytes:
    """CSV no formato do Excel em português: `;`, vírgula decimal e UTF-8 com BOM."""
    buffer = io.StringIO()
    escrita = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    escrita.writerow(["id_transacao", "cliente", "data_hora", "valor", "cidade", "pais", "categoria",
                      "risco", "anomalia", "nivel", "motivos"])
    numero = lambda v: f"{v:.2f}".replace(".", ",")
    for r in resultado.to_dict("records"):
        escrita.writerow([_neutralizar(str(r["id_transacao"])), _neutralizar(str(r["cliente"])),
                          r["data_hora"].strftime("%Y-%m-%d %H:%M:%S"), numero(r["valor"]),
                          _neutralizar(str(r["cidade"])), _neutralizar(str(r["pais"])),
                          _neutralizar(str(r["categoria"])), numero(r["risco"]), numero(r["anomalia"]),
                          r["nivel"], _neutralizar(" | ".join(r["motivos"]))])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def criar_app(pasta_modelos: Path | None = None, configuracao: Configuracao | None = None) -> Flask:
    """Cria a aplicação. Sem argumentos, lê tudo do .env; `pasta_modelos` substitui PASTA_MODELOS."""
    cfg = configuracao or ler_configuracao()
    registo = configurar_registo(cfg.pasta_logs, cfg.nivel_log)
    pasta_modelos = Path(pasta_modelos) if pasta_modelos is not None else cfg.pasta_modelos

    app = Flask(__name__, template_folder=str(RAIZ / "templates"), static_folder=str(RAIZ / "static"))
    app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES + 64 * 1024
    app.config["SECRET_KEY"] = cfg.chave_secreta
    app.config["CONFIGURACAO"] = cfg
    app.logger.removeHandler(default_handler)  # os erros do Flask vão para o mesmo registo (consola e ficheiro)
    for handler in registo.handlers:
        app.logger.addHandler(handler)
    app.logger.propagate = False

    migrar(cfg.url_banco)
    motor = criar_motor(cfg.url_banco)
    sessoes = criar_fabrica_sessoes(motor)
    app.config["MOTOR"], app.config["SESSOES"] = motor, sessoes

    app.config["ARTEFATOS"] = artefatos = carregar(pasta_modelos)
    if artefatos is None:
        registo.warning("Modelos não encontrados ou inválidos em %s: execute python treinar.py", pasta_modelos)
    else:
        registo.info("Modelos carregados de %s (classificador: %s)", pasta_modelos, artefatos.detector.escolhido)
    registo.info("Aplicação pronta (ambiente: %s, banco: %s)", cfg.ambiente, descrever(motor))
    exemplos = criar_exemplos(artefatos.historico) if artefatos else {}
    clientes = sorted(artefatos.historico["cliente"].unique()) if artefatos else []

    @app.template_filter("mt")
    def _mt(valor):
        return formatar_mt(float(valor))

    @app.template_filter("decimal")
    def _decimal(valor, casas=1):
        return formatar_decimal(float(valor), casas)

    @app.template_filter("nota")
    def _nota(valor):
        return str(int(float(valor)))  # trunca: 49,6 mostra 49, abaixo do limite de alerta (50)

    @app.template_filter("pct")
    def _pct(valor):
        return "—" if valor is None else f"{formatar_decimal(100 * float(valor))}%"

    @app.template_filter("motivos")
    def _motivos(valor):
        return json.loads(valor) if isinstance(valor, str) else list(valor or [])

    @app.template_filter("horas")
    def _horas(valor):
        if valor is None:
            return "—"
        if valor < 1:
            return f"{max(1, round(valor * 60))} min"
        return f"{formatar_decimal(valor)} h" if valor < 48 else f"{formatar_decimal(valor / 24)} dias"

    @app.template_filter("categoria")
    def _categoria(valor):
        return nome_categoria(str(valor).strip().upper())

    @app.context_processor
    def _contexto():
        return {"aba": request.endpoint}

    @app.before_request
    def _inicio_pedido():
        g.inicio = time.perf_counter()

    acesso.instalar(app, sessoes, registo, cfg.sessao_horas, cfg.producao)  # login, perfis, CSRF e auditoria
    web_alertas.instalar(app, sessoes)  # fila de alertas, painel e notificações
    api.instalar(app)  # API REST v1 (chaves, limite de pedidos)
    web_api.instalar(app, sessoes)  # documentação da API e gestão das chaves
    fila = app.config["FILA"] = tarefas.FilaTarefas(sessoes, cfg.pasta_envios, lambda: app.config["ARTEFATOS"], cfg)
    fila.retomar()  # ficheiros que ficaram por processar quando o servidor parou

    @app.before_request
    def _exige_modelos():
        if app.config["ARTEFATOS"] is None and request.endpoint not in SEM_MODELOS and not api.e_pedido_api():
            return render_template("sem_modelos.html"), 503
        return None

    @app.after_request
    def _registar_pedido(resposta):
        if request.endpoint not in ("static", "saude"):
            ms = (time.perf_counter() - g.get("inicio", time.perf_counter())) * 1000
            u = g.get("utilizador")
            registo.info("%s %s %s %.0f ms [%s]", request.method, request.path, resposta.status_code, ms,
                         u.nome_utilizador if u else "-")
        return resposta

    @app.errorhandler(500)
    def _erro_interno(_):  # o Flask já registou o erro completo em app.logger
        if api.e_pedido_api():
            return api.erro(500, "erro_interno", "Erro inesperado; ficou registado no servidor.")
        return render_template("erro.html"), 500

    @app.errorhandler(404)
    def _nao_encontrado(exc):
        return api.erro(404, "nao_encontrado", "Endereço da API desconhecido.") if api.e_pedido_api() else exc

    @app.errorhandler(405)
    def _metodo(exc):
        return api.erro(405, "metodo_nao_permitido", "Método não permitido neste endereço.") if api.e_pedido_api() else exc

    @app.get("/saude")
    def saude():
        banco_ok = verificar_ligacao(motor)
        modelos_ok = app.config["ARTEFATOS"] is not None
        estado = {
            "estado": "ok" if banco_ok and modelos_ok else "com_problemas",
            "versao": VERSAO,
            "ambiente": cfg.ambiente,
            "banco": "ok" if banco_ok else "sem_ligacao",
            "modelos": "carregados" if modelos_ok else "em_falta",
        }
        return jsonify(estado), 200 if estado["estado"] == "ok" else 503

    def _modelo() -> str:
        return app.config["ARTEFATOS"].detector.escolhido

    def _formulario(erro=None, valores=None, linhas=None, estado=200):
        return render_template(
            "testar.html", erro=erro, valores=valores or {}, linhas=linhas, clientes=clientes,
            cidades=CIDADES, paises=(PAIS_HABITUAL, "ZA", *PAISES_ESTRANHOS), categorias=CATEGORIAS,
            exemplos=[(n, DESCRICOES[n]) for n in NOMES_EXEMPLOS],
        ), estado

    @app.get("/")
    def testar():
        return _formulario()

    @app.post("/")
    def testar_post():
        nome = request.form.get("exemplo")
        valores = {c: request.form.get(c, "").strip() for c in CAMPOS}
        origem = "exemplo" if nome else "formulario"
        if nome:
            if nome not in exemplos:
                return _formulario(erro="Exemplo desconhecido.", estado=400)
            transacoes = pd.DataFrame(exemplos[nome])
            valores = {}
        else:
            try:
                transacoes = validar(pd.DataFrame([{"id_transacao": "FORM-1", **valores}]))
            except ValueError as exc:
                mensagem = str(exc).removeprefix("Linha 2: ")
                return _formulario(erro=mensagem[0].upper() + mensagem[1:], valores=valores, estado=400)
        resultado = avaliar_transacoes(app.config["ARTEFATOS"], transacoes)
        chave = repositorio.guardar_analise(sessoes, resultado, origem, _modelo(), utilizador_id=utilizador_atual().id)
        final = resultado.iloc[-1]
        auditar("testar_transacao", alvo=str(final["cliente"]),
                detalhe=f"{f'exemplo {nome}' if nome else 'formulário'}; {len(resultado)} transação(ões); "
                        f"{final['nivel']} (risco {int(final['risco'])}); análise {chave}")
        return _formulario(valores=valores, linhas=resultado.to_dict("records"))

    def _ve_tudo() -> bool:
        return utilizador_atual().pode("supervisor")

    def _analise_visivel(chave):
        """A análise, se existir e o utilizador a puder ver: o analista só vê as suas; os outros recebem 404."""
        analise = repositorio.obter_analise(sessoes, chave)
        if analise is None or not (_ve_tudo() or analise.utilizador_id == utilizador_atual().id):
            abort(404)
        return analise

    def _analisar(erro=None, estado=200, chave=None, so_alertas=False):
        analise = repositorio.obter_analise(sessoes, chave) if chave else None
        linhas, total, alertas = [], 0, 0
        if analise is not None:
            total, alertas = analise.total, analise.alertas
            visiveis = repositorio.obter_transacoes(sessoes, analise, so_alertas, MAX_LINHAS_TABELA)
            linhas = visiveis.to_dict("records")
        recentes = repositorio.listar_analises(sessoes, "ficheiro", ANALISES_RECENTES,
                                               None if _ve_tudo() else utilizador_atual().id)
        em_curso = fila.listar_do_utilizador(None if _ve_tudo() else utilizador_atual().id)
        return render_template("analisar.html", erro=erro, chave=chave if analise is not None else None,
                               analise=analise, linhas=linhas, total=total, alertas=alertas, so_alertas=so_alertas,
                               limite=MAX_LINHAS_TABELA, recentes=recentes, ve_tudo=_ve_tudo(),
                               em_curso=em_curso, linhas_segundo_plano=cfg.linhas_segundo_plano), estado

    @app.get("/analisar")
    def analisar():
        return _analisar()

    @app.post("/analisar")
    def analisar_post():
        ficheiro = request.files.get("ficheiro")
        if ficheiro is None or not ficheiro.filename:
            return _analisar(erro="Escolha um ficheiro CSV para enviar.", estado=400)
        conteudo = ficheiro.read()
        try:
            transacoes = validar(ler_csv(conteudo))  # validar já aqui: os erros aparecem logo, mesmo nos grandes
        except ValueError as exc:
            return _analisar(erro=str(exc), estado=400)
        if len(transacoes) >= cfg.linhas_segundo_plano:
            tarefa = fila.submeter(conteudo, ficheiro.filename, len(transacoes), utilizador_atual().id)
            return redirect(url_for("tarefa", chave=tarefa))
        chave, total, n_alertas = tarefas.analisar_ficheiro(app.config["ARTEFATOS"], sessoes, cfg, transacoes,
                                                            ficheiro.filename, utilizador_atual().id)
        auditar("analisar_ficheiro", alvo=chave,
                detalhe=f"{ficheiro.filename[:200]}: {total} transações, {n_alertas} com alerta")
        return _analisar(chave=chave)

    @app.get("/analisar/tarefa/<chave>")
    def tarefa(chave):
        t = fila.obter(chave)
        if t is None or not (_ve_tudo() or t.utilizador_id == utilizador_atual().id):
            abort(404)
        if t.estado == "concluida" and t.analise_chave:
            return redirect(url_for("analisar_ver", chave=t.analise_chave))
        return render_template("tarefa.html", t=t)

    @app.get("/analisar/<chave>.csv")
    def analisar_download(chave):
        analise = _analise_visivel(chave)
        resultado = repositorio.obter_transacoes(sessoes, analise)
        auditar("descarregar_analise", alvo=chave, detalhe=f"{analise.total} transações")
        return Response(_csv_excel(resultado), mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="resultado.csv"'})

    @app.get("/analisar/<chave>")
    def analisar_ver(chave):
        analise = _analise_visivel(chave)
        auditar("consultar_analise", alvo=chave,
                detalhe=f"{analise.nome_ficheiro or analise.origem}: {analise.alertas} alerta(s)")
        return _analisar(chave=chave, so_alertas=request.args.get("alertas") == "1")

    @app.errorhandler(413)
    def _grande_demais(_):
        if api.e_pedido_api():
            return api.erro(413, "pedido_grande_demais", "O pedido passa de 5 MB; divida-o em lotes menores.")
        if utilizador_atual() is None:
            return render_template("erro.html", titulo="Pedido grande demais", mensagem="O pedido passa de 5 MB."), 413
        return _analisar(erro="O ficheiro passa de 5 MB.", estado=413)

    @app.get("/perfil")
    def perfil():
        codigo = request.args.get("cliente", "").strip().upper()
        dados = {"clientes": clientes, "codigo": codigo, "perfil": None, "marcadas": [], "tipos": NOMES_TIPO_FRAUDE}
        if not codigo:
            return render_template("perfil.html", **dados)
        art = app.config["ARTEFATOS"]
        dados["perfil"] = perfil_cliente(art.historico, codigo)
        auditar("consultar_perfil", alvo=codigo[:100],
                detalhe=None if dados["perfil"] is not None else "cliente não encontrado")
        if dados["perfil"] is None:
            return render_template("perfil.html", **dados, erro="Cliente não encontrado."), 404
        teste = art.teste_avaliado
        if len(teste):
            marcadas = teste[(teste["cliente"].astype(str).str.strip().str.upper() == codigo) & (teste["nivel"] != "Normal")]
            dados["marcadas"] = marcadas.sort_values("data_hora").to_dict("records")
        dados["maximo_hora"] = max(dados["perfil"]["horas"]) or 1
        return render_template("perfil.html", **dados)

    @app.get("/modelos")
    def modelos():
        return render_template("modelos.html", res=app.config["ARTEFATOS"].resultados, nomes=NOMES,
                               ordem=[*ORDEM_SIMPLICIDADE, "isolation_forest"], periodos=NOMES_PERIODO,
                               tipos=NOMES_TIPO_FRAUDE)

    @app.get("/graficos/<nome>")
    def graficos(nome):
        if nome not in GRAFICOS:
            abort(404)
        return send_from_directory(app.config["ARTEFATOS"].pasta / "graficos", nome)

    return app


if __name__ == "__main__":
    configuracao = ler_configuracao()
    criar_app(configuracao=configuracao).run(host=configuracao.host, port=configuracao.porta)
