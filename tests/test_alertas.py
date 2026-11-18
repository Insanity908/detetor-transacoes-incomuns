"""Fase 3: fila de alertas, atribuição, decisão, comentários, histórico, painel, notificações e e-mail."""
import io
import re
from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import select, update

from app import criar_app
from detector import alertas, auditoria, correio, repositorio
from detector.banco import Alerta
from detector.configuracao import ler_configuracao
from tests.conftest import criar_cliente


@pytest.fixture()
def app(pasta_pequena):
    return criar_app(pasta_pequena)


def _sessoes(app):
    return app.config["SESSOES"]


def _criar_alertas(app, niveis, cliente="C0001", valor=1000.0, origem="ficheiro"):
    """Grava uma análise com uma transação por nível e devolve os números dos alertas criados."""
    linhas = [dict(id_transacao=f"T{i}", cliente=cliente, data_hora=pd.Timestamp("2026-05-01 10:00") + pd.Timedelta(minutes=i),
                   valor=valor * (i + 1), cidade="MAPUTO", pais="MZ", categoria="MERCEARIA",
                   risco={"Alto risco": 90.0, "Atenção": 60.0}.get(n, 5.0), anomalia=20.0, nivel=n,
                   motivos=[f"Motivo {i}"] if n != "Normal" else [], historico_curto=False)
              for i, n in enumerate(niveis)]
    sessoes = _sessoes(app)
    with sessoes() as s:
        antes = s.scalar(select(Alerta.id).order_by(Alerta.id.desc()).limit(1)) or 0
    repositorio.guardar_analise(sessoes, pd.DataFrame(linhas), origem, "random_forest", "lote.csv",
                                gerar_alertas=origem == "ficheiro")
    with sessoes() as s:
        return list(s.scalars(select(Alerta.id).where(Alerta.id > antes).order_by(Alerta.id)))


def _alerta(app, alerta_id):
    return alertas.obter(_sessoes(app), alerta_id)


def _acoes(app, **filtro):
    eventos, _ = auditoria.listar(_sessoes(app), auditoria.Filtro(**filtro), por_pagina=500)
    return [(e.nome_utilizador, e.acao, e.alvo) for e in eventos]


def _acao(cliente, alerta_id, acao, **dados):
    return cliente.post(f"/alertas/{alerta_id}/{acao}", data=dados, follow_redirects=True)


# ------------------------------------------------------------ criação dos alertas
def test_so_transacoes_com_alerta_entram_na_fila(app):
    ids = _criar_alertas(app, ["Alto risco", "Normal", "Atenção", "Normal"])
    assert len(ids) == 2
    estados = [(_alerta(app, i).estado, _alerta(app, i).transacao.nivel) for i in ids]
    assert estados == [("novo", "Alto risco"), ("novo", "Atenção")]


def test_testes_manuais_nao_enchem_a_fila(app):
    assert _criar_alertas(app, ["Alto risco"], origem="formulario") == []
    cliente = criar_cliente(app, "analista")
    cliente.post("/", data={"exemplo": "valor_alto"})
    with _sessoes(app)() as s:
        assert s.scalar(select(Alerta.id)) is None


def test_ficheiro_analisado_gera_alertas_e_avisa(app):
    csv = ("cliente,data_hora,valor,cidade,pais,categoria\n"
           "C0001,2026-05-01 14:30,150,Maputo,MZ,MERCEARIA\nC0001,2026-05-01 15:00,90000,Maputo,MZ,MERCEARIA\n")
    cliente = criar_cliente(app, "analista")
    html = cliente.post("/analisar", data={"ficheiro": (io.BytesIO(csv.encode()), "lote.csv")},
                        content_type="multipart/form-data").get_data(as_text=True)
    n = int(re.search(r"<strong>(\d+)</strong> com alerta", html).group(1))
    assert n >= 1 and "fila de alertas" in html
    lista, total = alertas.listar(_sessoes(app), cliente.utilizador, alertas.Filtro(vista="novos"))
    assert total == n and lista[0].transacao.cliente == "C0001"


# ------------------------------------------------------------ do início ao fim
def test_analista_recebe_investiga_e_encerra_um_alerta(app):
    [i] = _criar_alertas(app, ["Alto risco"])
    ana = criar_cliente(app, "analista", "ana")

    fila = ana.get("/alertas").get_data(as_text=True)
    assert f'href="/alertas/{i}"' in fila and "Novo" in fila
    pagina = ana.get(f"/alertas/{i}").get_data(as_text=True)
    assert "Assumir este alerta" in pagina and "Motivo 0" in pagina and "Encerrar alerta" not in pagina

    pagina = _acao(ana, i, "assumir").get_data(as_text=True)
    assert "O alerta está agora consigo" in pagina and "Encerrar alerta" in pagina
    assert f'href="/alertas/{i}"' in ana.get("/alertas?vista=meus").get_data(as_text=True)

    _acao(ana, i, "comentar", texto="Liguei ao cliente: não reconhece a compra.")
    pagina = _acao(ana, i, "decidir", decisao="fraude", texto="Cliente confirmou que o cartão foi clonado.").get_data(as_text=True)
    assert "Alerta encerrado: fraude confirmada" in pagina
    assert "Liguei ao cliente" in pagina and "Cliente confirmou" in pagina  # histórico

    a = _alerta(app, i)
    assert (a.estado, a.responsavel.nome_utilizador, a.decisor.nome_utilizador) == ("fraude", "ana", "ana")
    assert a.decidido_em is not None
    tipos = [(e.tipo, e.de, e.para) for e in alertas.historico(_sessoes(app), i)]
    assert tipos == [("estado", "novo", "em_analise"), ("atribuicao", None, "ana"), ("comentario", None, None),
                     ("estado", "em_analise", "fraude")]
    registos = _acoes(app, utilizador="ana")
    for acao in ("consultar_alerta", "assumir_alerta", "comentar_alerta", "decidir_alerta"):
        assert ("ana", acao, str(i)) in registos
    assert f'href="/alertas/{i}"' in ana.get("/alertas?vista=encerrados").get_data(as_text=True)


def test_decisao_exige_justificacao_e_guarda_o_rascunho(app):
    [i] = _criar_alertas(app, ["Alto risco"])
    ana = criar_cliente(app, "analista", "ana")
    _acao(ana, i, "assumir")
    pagina = _acao(ana, i, "decidir", decisao="fraude", texto="ok").get_data(as_text=True)
    assert "Escreva uma justificação" in pagina and ">ok</textarea>" in pagina
    assert _alerta(app, i).estado == "em_analise"
    pagina = _acao(ana, i, "decidir", decisao="aprovado", texto="texto suficiente").get_data(as_text=True)
    assert "Decisão desconhecida" in pagina and _alerta(app, i).estado == "em_analise"


def test_devolver_a_fila(app):
    [i] = _criar_alertas(app, ["Atenção"])
    ana = criar_cliente(app, "analista", "ana")
    _acao(ana, i, "assumir")
    assert "voltou à fila" in _acao(ana, i, "devolver").get_data(as_text=True)
    a = _alerta(app, i)
    assert a.estado == "novo" and a.atribuido_a is None


# ------------------------------------------------------------ quem pode o quê
def test_analista_nao_ve_nem_mexe_no_alerta_de_outro(app):
    [i, j] = _criar_alertas(app, ["Alto risco", "Atenção"])
    ana, rui = criar_cliente(app, "analista", "ana"), criar_cliente(app, "analista", "rui")
    _acao(ana, i, "assumir")
    assert rui.get(f"/alertas/{i}").status_code == 404
    assert rui.post(f"/alertas/{i}/decidir", data={"decisao": "falso_alarme", "texto": "não é nada"}).status_code == 404
    assert rui.post(f"/alertas/{i}/comentar", data={"texto": "olá"}).status_code == 404
    fila = rui.get("/alertas").get_data(as_text=True)
    assert f'href="/alertas/{i}"' not in fila and f'href="/alertas/{j}"' in fila  # vê os novos, não os da Ana
    assert _alerta(app, i).estado == "em_analise"


def test_dois_analistas_assumem_ao_mesmo_tempo(app):
    [i] = _criar_alertas(app, ["Alto risco"])
    ana, rui = criar_cliente(app, "analista", "ana"), criar_cliente(app, "analista", "rui")
    rui.get(f"/alertas/{i}")  # ambos viram o alerta como novo
    _acao(ana, i, "assumir")
    resposta = rui.post(f"/alertas/{i}/assumir")  # o Rui clica depois, com a página antiga
    assert resposta.status_code == 404 or "outra pessoa assumiu" in rui.get(f"/alertas/{i}").get_data(as_text=True)
    assert _alerta(app, i).responsavel.nome_utilizador == "ana"
    with pytest.raises(alertas.AlertaMudou):
        alertas.assumir(_sessoes(app), i, rui.utilizador)


def test_supervisor_atribui_reatribui_decide_e_reabre(app):
    [i] = _criar_alertas(app, ["Alto risco"])
    ana, rui = criar_cliente(app, "analista", "ana"), criar_cliente(app, "analista", "rui")
    chefe = criar_cliente(app, "supervisor", "chefe")

    assert "Alerta atribuído a" in _acao(chefe, i, "atribuir", responsavel=ana.utilizador.id).get_data(as_text=True)
    assert _alerta(app, i).estado == "em_analise" and f'href="/alertas/{i}"' in ana.get("/alertas?vista=meus").get_data(as_text=True)
    _acao(chefe, i, "atribuir", responsavel=rui.utilizador.id)
    assert _alerta(app, i).responsavel.nome_utilizador == "rui" and ana.get(f"/alertas/{i}").status_code == 404
    assert "já está com rui" in _acao(chefe, i, "atribuir", responsavel=rui.utilizador.id).get_data(as_text=True)

    _acao(chefe, i, "decidir", decisao="falso_alarme", texto="Compra habitual de fim de mês.")
    assert _alerta(app, i).estado == "falso_alarme" and _alerta(app, i).decisor.nome_utilizador == "chefe"
    assert "Escreva uma justificação" in _acao(chefe, i, "reabrir", texto="").get_data(as_text=True)
    _acao(chefe, i, "reabrir", texto="O cliente afinal contestou a compra.")
    a = _alerta(app, i)
    assert (a.estado, a.decidido_em, a.responsavel.nome_utilizador) == ("em_analise", None, "rui")
    atribuicoes = [(e.de, e.para) for e in alertas.historico(_sessoes(app), i) if e.tipo == "atribuicao"]
    assert atribuicoes == [(None, "ana"), ("ana", "rui")]
    assert ("chefe", "atribuir_alerta", str(i)) in _acoes(app) and ("chefe", "reabrir_alerta", str(i)) in _acoes(app)


def test_analista_nao_atribui_nem_reabre(app):
    [i] = _criar_alertas(app, ["Alto risco"])
    ana, rui = criar_cliente(app, "analista", "ana"), criar_cliente(app, "analista", "rui")
    pagina = _acao(ana, i, "atribuir", responsavel=rui.utilizador.id).get_data(as_text=True)
    assert "Só um supervisor pode atribuir" in pagina and _alerta(app, i).estado == "novo"
    assert ("ana", "acesso_negado", f"/alertas/{i}/atribuir") in _acoes(app)
    _acao(ana, i, "assumir")
    _acao(ana, i, "decidir", decisao="fraude", texto="Confirmado com o banco.")
    assert "Só um supervisor pode reabrir" in _acao(ana, i, "reabrir", texto="quero reabrir").get_data(as_text=True)
    assert _alerta(app, i).estado == "fraude"
    assert ana.post(f"/alertas/{i}/apagar").status_code == 404


def test_formularios_da_ficha_levam_token(app):
    [i] = _criar_alertas(app, ["Alto risco"])
    chefe = criar_cliente(app, "supervisor")
    _acao(chefe, i, "assumir")
    html = chefe.get(f"/alertas/{i}").get_data(as_text=True)
    formularios = re.findall(r'<form method="post".*?</form>', html, re.S)
    assert len(formularios) >= 4 and all('name="csrf_token"' in f for f in formularios)
    assert chefe.post(f"/alertas/{i}/comentar", data={"texto": "x", "csrf_token": ""}).status_code == 400


# ------------------------------------------------------------ fila
def test_filtros_e_ordem_da_fila(app):
    _criar_alertas(app, ["Atenção", "Alto risco"], cliente="C0001")
    _criar_alertas(app, ["Alto risco"], cliente="C0002")
    u = criar_cliente(app, "supervisor").utilizador
    lista, total = alertas.listar(_sessoes(app), u)
    assert total == 3 and [a.transacao.risco for a in lista] == [90.0, 90.0, 60.0]
    _, total = alertas.listar(_sessoes(app), u, alertas.Filtro(nivel="Alto risco"))
    assert total == 2
    lista, _ = alertas.listar(_sessoes(app), u, alertas.Filtro(cliente="c0002"))
    assert [a.transacao.cliente for a in lista] == ["C0002"]
    assert alertas.contar_por_vista(_sessoes(app), u) == {"abertos": 3, "novos": 3, "meus": 0, "encerrados": 0}


def test_fila_pagina_os_resultados(app):
    _criar_alertas(app, ["Atenção"] * (alertas.POR_PAGINA + 3))
    cliente = criar_cliente(app, "supervisor")
    html = cliente.get("/alertas").get_data(as_text=True)
    assert "página 1 de 2" in html and html.count('href="/alertas/') == alertas.POR_PAGINA
    assert cliente.get("/alertas?pagina=2&vista=lixo&nivel=lixo&responsavel=abc").status_code == 200


# ------------------------------------------------------------ notificações
def test_sino_conta_os_alertas_novos_de_alto_risco(app):
    ana = criar_cliente(app, "analista", "ana")
    _criar_alertas(app, ["Alto risco", "Alto risco", "Atenção"])
    assert 'class="contagem-sino">2<' in ana.get("/alertas").get_data(as_text=True)
    pagina = ana.get("/notificacoes").get_data(as_text=True)
    assert pagina.count("Alerta #") == 2 and "contagem-sino" not in pagina
    assert "contagem-sino" not in ana.get("/alertas").get_data(as_text=True)
    [novo] = _criar_alertas(app, ["Alto risco"])
    assert 'class="contagem-sino">1<' in ana.get("/").get_data(as_text=True)
    _acao(criar_cliente(app, "analista", "rui"), novo, "assumir")  # já não é novo: sai do sino
    assert "contagem-sino" not in ana.get("/").get_data(as_text=True)


# ------------------------------------------------------------ painel
def test_painel_so_para_supervisor(app):
    assert criar_cliente(app, "analista").get("/painel").status_code == 403
    html = criar_cliente(app, "supervisor").get("/painel?dias=7").get_data(as_text=True)
    assert "Taxa de falsos alarmes" in html and "Últimos 7 dias" in html and html.count('class="dia"') == 7


def test_indicadores_do_painel(app):
    ids = _criar_alertas(app, ["Alto risco", "Alto risco", "Atenção", "Atenção"], valor=1000.0)  # valores 1000..4000
    sessoes = _sessoes(app)
    ana = criar_cliente(app, "analista", "ana").utilizador
    hoje = date(2026, 5, 10)
    criado = datetime(2026, 5, 9, 8, 0)
    with sessoes.begin() as s:
        s.execute(update(Alerta).values(criado_em=criado))
        s.execute(update(Alerta).where(Alerta.id == ids[0]).values(criado_em=datetime(2026, 1, 1)))  # fora do período
    for i, decisao in ((ids[1], "fraude"), (ids[2], "falso_alarme")):
        alertas.assumir(sessoes, i, ana)
        alertas.decidir(sessoes, i, decisao, "justificação suficiente", ana)
    with sessoes.begin() as s:
        s.execute(update(Alerta).where(Alerta.id == ids[1]).values(decidido_em=criado + timedelta(hours=2)))
        s.execute(update(Alerta).where(Alerta.id == ids[2]).values(decidido_em=criado + timedelta(hours=4)))
    ind = alertas.indicadores(sessoes, 7, hoje=hoje)
    assert ind.criados == 3 and ind.por_dia[-2] == {"dia": date(2026, 5, 9), "alto": 1, "atencao": 2, "total": 3}
    assert (ind.decididos, ind.fraudes, ind.falsos, ind.taxa_falsos) == (2, 1, 1, 0.5)
    assert ind.horas_decisao == pytest.approx(3.0) and ind.horas_decisao_mediana == pytest.approx(3.0)
    assert ind.abertos == {"novo": 2, "em_analise": 0}
    assert ind.valor_em_risco == 1000.0 + 4000.0 and ind.valor_em_risco_alto == 1000.0
    assert ind.valor_fraude == 2000.0
    assert ind.por_pessoa == [{"nome": "ana", "em_analise": 0, "decididos": 2, "fraudes": 1, "falsos": 1,
                               "horas_decisao": pytest.approx(3.0)}]


def test_painel_vazio_nao_falha(app):
    ind = alertas.indicadores(_sessoes(app), 30)
    assert ind.criados == 0 and ind.taxa_falsos is None and ind.horas_decisao is None
    assert "nenhuma decisão no período" in criar_cliente(app, "supervisor").get("/painel").get_data(as_text=True)


# ------------------------------------------------------------ e-mail
class _SmtpFalso:
    enviados: list = []
    falhar = False

    def __init__(self, servidor, porta, timeout):
        self.servidor, self.porta = servidor, porta
        self.passos = []

    def __enter__(self):
        if self.falhar:
            raise OSError("ligação recusada")
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context):
        self.passos.append("tls")

    def login(self, utilizador, senha):
        self.passos.append(("login", utilizador))

    def send_message(self, mensagem):
        _SmtpFalso.enviados.append((self.servidor, self.porta, self.passos, mensagem))


@pytest.fixture()
def smtp(monkeypatch):
    _SmtpFalso.enviados, _SmtpFalso.falhar = [], False
    monkeypatch.setattr(correio.smtplib, "SMTP", _SmtpFalso)
    monkeypatch.setenv("SMTP_SERVIDOR", "smtp.exemplo.co.mz")
    monkeypatch.setenv("SMTP_UTILIZADOR", "detetor@exemplo.co.mz")
    monkeypatch.setenv("SMTP_SENHA", "segredo")
    monkeypatch.setenv("EMAIL_ALERTAS", "equipa@exemplo.co.mz; chefe@exemplo.co.mz")
    monkeypatch.setenv("URL_PUBLICA", "https://detetor.exemplo.co.mz/")
    return _SmtpFalso


def test_email_com_resumo_sem_dados_de_clientes(smtp):
    cfg = ler_configuracao(ficheiro_env=None)
    assert cfg.email_ativo and "segredo" not in repr(cfg)
    correio.avisar_alertas(cfg, "maio.csv", alto=3, total=5, esperar=True)
    [(servidor, porta, passos, mensagem)] = smtp.enviados
    assert (servidor, porta, passos) == ("smtp.exemplo.co.mz", 587, ["tls", ("login", "detetor@exemplo.co.mz")])
    assert mensagem["To"] == "equipa@exemplo.co.mz, chefe@exemplo.co.mz" and "3 alerta(s)" in mensagem["Subject"]
    corpo = mensagem.get_content()
    assert "https://detetor.exemplo.co.mz/alertas?vista=novos" in corpo and "C0001" not in corpo


def test_sem_alto_risco_ou_sem_configuracao_nao_envia(smtp, monkeypatch):
    correio.avisar_alertas(ler_configuracao(ficheiro_env=None), "x.csv", alto=0, total=4, esperar=True)
    monkeypatch.setenv("SMTP_SERVIDOR", "")
    correio.avisar_alertas(ler_configuracao(ficheiro_env=None), "x.csv", alto=2, total=4, esperar=True)
    assert smtp.enviados == []


def test_falha_no_email_fica_no_registo_e_nao_interrompe(smtp, ambiente_de_teste):
    smtp.falhar = True
    cfg = ler_configuracao(ficheiro_env=None)
    from detector.registo import configurar_registo
    configurar_registo(cfg.pasta_logs, "INFO")
    assert correio.enviar(cfg, correio.montar_resumo(cfg, "x.csv", 1, 1)) is False
    assert "Falha ao enviar o e-mail" in (ambiente_de_teste / "logs" / "detetor.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("nome,valor", [("EMAIL_ALERTAS", "sem-arroba"), ("SMTP_TLS", "talvez"), ("SMTP_PORTA", "0")])
def test_configuracao_de_email_invalida(monkeypatch, nome, valor):
    monkeypatch.setenv(nome, valor)
    with pytest.raises(ValueError, match=nome):
        ler_configuracao(ficheiro_env=None)


# ------------------------------------------------------------ migração
def test_migracao_0003_poe_os_alertas_antigos_na_fila(ambiente_de_teste):
    from alembic import command
    from sqlalchemy import text
    from detector.banco import configuracao_alembic, criar_motor, migrar
    url = "sqlite:///" + (ambiente_de_teste / "antigo.db").as_posix()
    command.upgrade(configuracao_alembic(url), "0002")
    motor = criar_motor(url)
    with motor.begin() as c:
        for i, origem in ((1, "ficheiro"), (2, "exemplo")):
            c.execute(text("INSERT INTO analises (id, chave, origem, criada_em, total, alertas, modelo) "
                           "VALUES (:i, :c, :o, '2026-05-01 10:00:00', 2, 1, 'rf')"), {"i": i, "c": f"k{i}", "o": origem})
            for ordem, nivel in enumerate(("Alto risco", "Normal")):
                c.execute(text("INSERT INTO transacoes_avaliadas (analise_id, ordem, id_transacao, cliente, data_hora, "
                               "valor, cidade, pais, categoria, risco, anomalia, nivel, motivos, historico_curto) "
                               "VALUES (:a, :o, 'T', 'C1', '2026-05-01 09:00:00', 10, 'X', 'MZ', 'M', 90, 1, :n, '[]', 0)"),
                          {"a": i, "o": ordem, "n": nivel})
    migrar(url)
    with motor.connect() as c:
        linhas = c.execute(text("SELECT a.estado, t.analise_id, t.nivel FROM alertas a "
                                "JOIN transacoes_avaliadas t ON t.id = a.transacao_id")).all()
    motor.dispose()
    assert linhas == [("novo", 1, "Alto risco")]
    command.downgrade(configuracao_alembic(url), "0002")
    migrar(url)


# ------------------------------------------------------------ decisão liquida o movimento do banco
def _retido(app, artefatos, monkeypatch, valor=25000, destino=False):
    """Cria uma conta com saldo e um débito retido (alerta novo); devolve (alerta_id, conta_id, destino_id, mov_id)."""
    from detector import autorizacao, clientes, contas_bancarias
    from detector.autorizacao import PedidoDebito
    from detector.limiares import Cortes
    from detector.banco import Movimento
    sessoes = _sessoes(app)

    def conta(n, saldo):
        form = dict(nome=f"Cliente {n}", data_nascimento="1990-05-10", tipo_documento="NUIT",
                    numero_documento=f"20000000{n}", telefone="841234567", email=None, cidade="MAPUTO", morada="Rua 1")
        c = clientes.criar(sessoes, clientes.validar(form), "teste")
        if saldo:
            contas_bancarias.depositar(sessoes, c.conta.id, saldo, "balcao", "teste")
        return c.conta.id

    origem, alvo = conta(1, 100000), conta(2, 0)

    def falso(artefatos, df, historico_extra=None, cortes=None):
        r = df.copy().reset_index(drop=True)
        r["risco"], r["anomalia"], r["nivel"] = 90.0, 10.0, "Alto risco"
        r["motivos"] = [["teste"]] * len(r)
        r["historico_curto"] = False
        return r
    monkeypatch.setattr(autorizacao, "avaliar_transacoes", falso)
    tipo = "transferencia_enviada" if destino else "compra"
    pedido = PedidoDebito(conta_id=origem, tipo=tipo, valor=valor, momento=datetime(2026, 11, 18, 10, 0),
                          cidade="MAPUTO", pais="MZ", categoria="SUPERMERCADO", canal="balcao", por="teste",
                          contraparte_id=alvo if destino else None)
    [d] = autorizacao.autorizar(sessoes, artefatos, [pedido], Cortes(), "banco")
    assert d.estado == "retido"
    return d.alerta_id, origem, alvo, d.movimento_id


def _saldos(app, *ids):
    from detector.banco import ContaBancaria
    with _sessoes(app)() as s:
        return [(s.get(ContaBancaria, i).saldo, s.get(ContaBancaria, i).saldo_retido) for i in ids]


def _movimento(app, mov_id):
    from detector.banco import Movimento
    with _sessoes(app)() as s:
        m = s.get(Movimento, mov_id)
        return m.estado, m.motivo


def _supervisor_decide(app, alerta_id, decisao):
    sup = criar_cliente(app, "supervisor", "sup")
    _acao(sup, alerta_id, "assumir")
    return sup, _acao(sup, alerta_id, "decidir", decisao=decisao, texto="Verificado com o cliente.")


def test_falso_alarme_executa_o_movimento_retido(app, artefatos_pequenos, monkeypatch):
    i, o, d, m = _retido(app, artefatos_pequenos, monkeypatch, destino=True)
    assert _saldos(app, o, d) == [(100000, 25000), (0, 0)]
    sup, _ = _supervisor_decide(app, i, "falso_alarme")
    assert _movimento(app, m) == ("autorizado", None)
    assert _saldos(app, o, d) == [(75000, 0), (25000, 0)]
    assert ("sup", "movimento_executado", str(m)) in _acoes(app, utilizador="sup")
    from detector import contas_bancarias
    assert contas_bancarias.verificar_saldos(_sessoes(app)) == []


def test_fraude_cancela_o_movimento_retido(app, artefatos_pequenos, monkeypatch):
    i, o, d, m = _retido(app, artefatos_pequenos, monkeypatch, destino=True)
    _supervisor_decide(app, i, "fraude")
    assert _movimento(app, m) == ("cancelado", "Fraude confirmada.")
    assert _saldos(app, o, d) == [(100000, 0), (0, 0)]
    assert ("sup", "movimento_cancelado", str(m)) in _acoes(app, utilizador="sup")


def test_falso_alarme_com_conta_de_origem_bloqueada_cancela(app, artefatos_pequenos, monkeypatch):
    from detector import contas_bancarias
    i, o, d, m = _retido(app, artefatos_pequenos, monkeypatch)
    contas_bancarias.bloquear(_sessoes(app), o, True, "teste")
    sup, _ = _supervisor_decide(app, i, "falso_alarme")
    assert _alerta(app, i).estado == "falso_alarme"
    assert _movimento(app, m) == ("cancelado", "A conta foi bloqueada entretanto.")
    assert _saldos(app, o) == [(100000, 0)]
    assert ("sup", "movimento_cancelado", str(m)) in _acoes(app, utilizador="sup")


def test_reabrir_nao_mexe_no_movimento(app, artefatos_pequenos, monkeypatch):
    i, o, d, m = _retido(app, artefatos_pequenos, monkeypatch)
    sup, _ = _supervisor_decide(app, i, "falso_alarme")
    pagina = sup.get(f"/alertas/{i}").get_data(as_text=True)
    assert "Reabrir não devolve nem volta a reter o dinheiro." in pagina
    _acao(sup, i, "reabrir", texto="Rever esta decisão.")
    assert _alerta(app, i).estado == "em_analise"
    assert _movimento(app, m) == ("autorizado", None)
    assert _saldos(app, o) == [(75000, 0)]


def test_alerta_de_ficheiro_sem_movimento_decide_na_mesma(app):
    [i] = _criar_alertas(app, ["Alto risco"])
    sup, pagina = _supervisor_decide(app, i, "falso_alarme")
    assert "Alerta encerrado: falso alarme" in pagina.get_data(as_text=True)
    assert _alerta(app, i).estado == "falso_alarme"
    assert not any(a[1].startswith("movimento_") for a in _acoes(app, utilizador="sup"))
    assert "Reabrir não devolve" not in sup.get(f"/alertas/{i}").get_data(as_text=True)


def test_se_liquidar_falha_o_alerta_continua_em_analise(app, artefatos_pequenos, monkeypatch):
    from detector import contas_bancarias
    i, o, d, m = _retido(app, artefatos_pequenos, monkeypatch)
    sup = criar_cliente(app, "supervisor", "sup")
    _acao(sup, i, "assumir")

    def falha(*a, **k):
        raise ValueError("falhou")
    monkeypatch.setattr(contas_bancarias, "liquidar", falha)
    pagina = _acao(sup, i, "decidir", decisao="falso_alarme", texto="Verificado com o cliente.")
    assert _alerta(app, i).estado == "em_analise"
    assert _movimento(app, m)[0] == "retido"
    assert not any(a[1] == "decidir_alerta" for a in _acoes(app, utilizador="sup"))


def test_ficha_mostra_conta_titular_saldo_e_movimento(app, artefatos_pequenos, monkeypatch):
    i, o, d, m = _retido(app, artefatos_pequenos, monkeypatch)
    sup = criar_cliente(app, "supervisor", "sup")
    pagina = sup.get(f"/alertas/{i}").get_data(as_text=True)
    assert "Cliente 1" in pagina and "750,00 MT" in pagina and "250,00 MT" in pagina and "Retido" in pagina
    _acao(sup, i, "assumir")
    _acao(sup, i, "decidir", decisao="fraude", texto="Cliente confirmou a fraude.")
    pagina = sup.get(f"/alertas/{i}").get_data(as_text=True)
    assert "Cancelado" in pagina and "Fraude confirmada." in pagina
