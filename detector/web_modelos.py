"""Páginas do ciclo de vida do modelo: versões, re-treino, publicação (e voltar atrás), monitorização e limiares."""
from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from sqlalchemy.orm import sessionmaker

from . import limiares, versoes
from .acesso import auditar, exige, utilizador_atual
from .avaliacao import NOMES_PERIODO
from .modelos import NOMES, ORDEM_SIMPLICIDADE
from .monitorizacao import JANELA, MIN_FRAUDES, QUEDA_F1, QUEDA_RECALL, Monitor
from .retreino import MIN_FRAUDES as MIN_FRAUDES_RETREINO
from .retreino import MIN_TRANSACOES, Retreino
from .simulacao import NOMES_TIPO_SIMULACAO

SEM_MODELOS = frozenset({"modelos", "modelo_versao", "modelo_publicar", "modelos_retreinar", "modelos_limiares",
                         "modelos_limiares_post"})


def instalar(app: Flask, sessoes: sessionmaker, pasta_modelos: Path) -> None:
    monitor = app.config["MONITOR"] = Monitor(sessoes)
    retreino = app.config["RETREINO"] = Retreino(sessoes, pasta_modelos, lambda: app.config["ARTEFATOS"],
                                                 lambda: app.config["CORTES"])

    def estado_monitor() -> dict:
        art = app.config["ARTEFATOS"]
        publicada = versoes.publicada(sessoes)
        base = versoes.ler_metricas(publicada).get("teste_alertas")
        return monitor.estado(art.versao if art else None, base)

    @app.context_processor
    def _contexto_modelo():
        u = utilizador_atual()
        if u is None or u.trocar_senha or not u.pode("supervisor"):
            return {"aviso_modelo": None}
        estado = estado_monitor()
        return {"aviso_modelo": estado if estado["aviso"] else None}

    # ------------------------------------------------------------ página principal
    @app.get("/modelos")
    def modelos():
        art = app.config["ARTEFATOS"]
        publicada = versoes.publicada(sessoes)
        return render_template(
            "modelos.html", res=art.resultados if art else None, nomes=NOMES,
            ordem=[*ORDEM_SIMPLICIDADE, "isolation_forest"], periodos=NOMES_PERIODO, tipos=NOMES_TIPO_SIMULACAO,
            versoes=versoes.listar(sessoes), publicada=publicada, metricas=versoes.ler_metricas,
            estados=versoes.NOMES_ESTADO, monitor=estado_monitor(), janela=JANELA, min_fraudes=MIN_FRAUDES,
            queda_f1=QUEDA_F1, queda_recall=QUEDA_RECALL, cortes=app.config["CORTES"],
            min_transacoes=MIN_TRANSACOES, min_fraudes_retreino=MIN_FRAUDES_RETREINO)

    @app.post("/modelos/retreinar")
    @exige("supervisor")
    def modelos_retreinar():
        try:
            numero = retreino.pedir(utilizador_atual().nome_utilizador)
        except ValueError as exc:
            flash(str(exc), "erro")
            return redirect(url_for("modelos"))
        auditar("retreino_pedido", alvo=f"v{numero}")
        return redirect(url_for("modelo_versao", numero=numero))

    # ------------------------------------------------------------ versões
    @app.get("/modelos/versoes/<int:numero>")
    def modelo_versao(numero):
        v = versoes.obter(sessoes, numero)
        if v is None:
            abort(404)
        return render_template("versao.html", v=v, m=versoes.ler_metricas(v), estados=versoes.NOMES_ESTADO,
                               nomes=NOMES, tipos=NOMES_TIPO_SIMULACAO, periodos=NOMES_PERIODO)

    @app.post("/modelos/versoes/<int:numero>/publicar")
    @exige("supervisor")
    def modelo_publicar(numero):
        v = versoes.obter(sessoes, numero)
        if v is None:
            abort(404)
        if v.estado not in ("candidata", "anterior"):
            flash("Só se publica uma versão candidata ou anterior.", "erro")
            return redirect(url_for("modelo_versao", numero=numero))
        novos = versoes.carregar_versao(pasta_modelos, v)  # carregar (e verificar a assinatura) antes de marcar
        if novos is None:
            flash("Os ficheiros desta versão estão em falta ou não passaram a verificação de integridade.", "erro")
            return redirect(url_for("modelo_versao", numero=numero))
        anterior = app.config["ARTEFATOS"].versao if app.config["ARTEFATOS"] else None
        versoes.publicar(sessoes, numero, utilizador_atual().nome_utilizador)
        app.config["USAR_ARTEFATOS"](novos)
        monitor.esquecer()
        auditar("versao_publicada", alvo=f"v{numero}", detalhe=f"substitui a v{anterior}" if anterior else None)
        app.logger.info("Versão %d publicada por %s (antes: %s)", numero, utilizador_atual().nome_utilizador, anterior)
        flash(f"Versão {numero} publicada: as novas transações já são avaliadas por ela.", "sucesso")
        return redirect(url_for("modelos"))

    # ------------------------------------------------------------ limiares
    def _pagina_limiares(proposta: limiares.Cortes | None = None, erro: str | None = None, estado: int = 200):
        atuais, ajuste = limiares.ler(sessoes)
        art = app.config["ARTEFATOS"]
        candidatos = [atuais, *[limiares.Cortes(c, atuais.anomalia) for c in limiares.CORTES_COMPARADOS]]
        if proposta is not None:
            candidatos.insert(1, proposta)
        vistos, unicos = set(), []
        for c in candidatos:
            if c not in vistos:
                vistos.add(c)
                unicos.append(c)
        efeitos = limiares.efeito(art.teste_avaliado if art else None, unicos)
        return render_template("limiares.html", atuais=atuais, ajuste=ajuste, efeitos=efeitos, proposta=proposta,
                               erro=erro, minimo=limiares.MINIMO, maximo=limiares.MAXIMO), estado

    @app.get("/modelos/limiares")
    @exige("supervisor")
    def modelos_limiares():
        proposta = None
        if request.args.get("risco") or request.args.get("anomalia"):
            try:
                proposta = limiares.Cortes(float(request.args.get("risco", 50)), float(request.args.get("anomalia", 50)))
            except (ValueError, TypeError) as exc:
                return _pagina_limiares(erro=str(exc) if "limite" in str(exc) else "Use números entre 1 e 99.", estado=400)
        return _pagina_limiares(proposta)

    @app.post("/modelos/limiares")
    @exige("supervisor")
    def modelos_limiares_post():
        try:
            novos = limiares.Cortes(float(request.form.get("risco", "")), float(request.form.get("anomalia", "")))
        except (ValueError, TypeError) as exc:
            return _pagina_limiares(erro=str(exc) if "limite" in str(exc) else "Use números entre 1 e 99.", estado=400)
        antes = app.config["CORTES"]
        limiares.gravar(sessoes, novos, utilizador_atual().nome_utilizador)
        app.config["CORTES"] = novos
        auditar("limiares_alterados", detalhe=f"risco {antes.risco:g} -> {novos.risco:g}; "
                                              f"anomalia {antes.anomalia:g} -> {novos.anomalia:g}")
        flash("Limiares gravados. Valem para as transações avaliadas a partir de agora.", "sucesso")
        return redirect(url_for("modelos_limiares"))
