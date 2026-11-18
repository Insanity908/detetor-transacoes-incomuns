"""Tarefa 17: o guião da demonstração (docs/guiao_demonstracao.md), passo a passo, de ponta a ponta."""
from sqlalchemy import select

from app import criar_app
from detector import autorizacao, clientes, contas_bancarias
from detector.banco import ContaBancaria, EventoAuditoria, Movimento
from tests.conftest import ClienteComCsrf, criar_cliente

FICHA = dict(nome="Rita Chongo", data_nascimento="1992-03-14", tipo_documento="NUIT", numero_documento="112233445",
             telefone="841112233", email="", cidade="MAPUTO", morada="Av. Eduardo Mondlane 10")


def _forcar_nivel(monkeypatch, nivel, risco):
    def falso(artefatos, df, historico_extra=None, cortes=None):
        r = df.copy().reset_index(drop=True)
        r["risco"], r["anomalia"], r["nivel"] = risco, 10.0, nivel
        r["motivos"] = [["Transferência de outra cidade que esvazia a conta"]] * len(r)
        r["historico_curto"] = False
        return r
    monkeypatch.setattr(autorizacao, "avaliar_transacoes", falso)


def test_guiao_de_ponta_a_ponta(pasta_pequena, monkeypatch):
    app = criar_app(pasta_pequena)
    sessoes = app.config["SESSOES"]
    # Um segundo cliente, só para receber as transferências.
    destino = clientes.criar(sessoes, clientes.validar(dict(FICHA, nome="Paulo Muianga", numero_documento="998877665")),
                             "teste")

    # Passo 1: o balcão cria o cliente e deposita 20 000 MT.
    balcao = criar_cliente(app, "balcao")
    r = balcao.post("/balcao/clientes/novo", data=FICHA)
    assert r.status_code == 302
    with sessoes() as s:
        cli = clientes.pesquisar(sessoes, "Rita")[0]
    ficha_url = r.headers["Location"]
    assert balcao.post(f"{ficha_url}/deposito", data={"valor": "20000,00", "descricao": "abertura"}).status_code == 302
    assert "20.000,00 MT" in balcao.get(ficha_url).get_data(as_text=True)
    assert balcao.get("/alertas").status_code in (302, 403, 404)  # o balcão não abre alertas

    # Passo 2: o balcão cria o acesso.
    assert balcao.post(f"{ficha_url}/acesso", data={"senha": "senha-inicial"}).status_code == 302

    # Passo 3: o cliente entra, muda a senha e faz duas transferências pequenas.
    app.test_client_class = ClienteComCsrf
    c = app.test_client()
    r = c.post("/cliente/entrar", data={"codigo": cli.codigo, "senha": "senha-inicial"})
    assert r.headers["Location"].endswith("/cliente/senha")
    r = c.post("/cliente/senha", data={"senha_atual": "senha-inicial", "senha_nova": "minha-senha",
                                       "confirmacao": "minha-senha"})
    assert r.status_code == 200
    _forcar_nivel(monkeypatch, "Normal", 5.0)
    for valor in ("150,00", "75,50"):
        r = c.post("/cliente/transferir", data={"conta_destino": destino.conta.numero, "valor": valor},
                   follow_redirects=True)
        assert "Concluída" in r.get_data(as_text=True)
    inicio = c.get("/cliente/").get_data(as_text=True)
    assert "19.774,50 MT" in inicio

    # Passo 4: "a partir de" Tete, transfere quase todo o saldo: fica em verificação.
    assert 'name="cidade"' in c.get("/cliente/transferir").get_data(as_text=True)
    _forcar_nivel(monkeypatch, "Alto risco", 92.0)
    r = c.post("/cliente/transferir", data={"conta_destino": destino.conta.numero, "valor": "19700,00",
                                            "cidade": "TETE", "pais": "MZ"}, follow_redirects=True)
    assert "Em verificação" in r.get_data(as_text=True)
    with sessoes() as s:
        conta = s.get(ContaBancaria, cli.conta.id)
        assert (conta.saldo, conta.saldo_retido) == (1_977_450, 1_970_000)  # total intacto, disponível 74,50
        mov = s.scalars(select(Movimento).where(Movimento.estado == "retido")).one()
        movimento_id = mov.id
    assert "74,50 MT" in c.get("/cliente/").get_data(as_text=True)

    # Passo 5: o analista vê o alerta, assume e confirma a fraude.
    ana = criar_cliente(app, "analista", "ana")
    assert "Alto risco" in ana.get("/alertas").get_data(as_text=True)
    with sessoes() as s:
        from detector.banco import Alerta
        alerta_id = s.scalars(select(Alerta.id).order_by(Alerta.id.desc())).first()
    assert ana.post(f"/alertas/{alerta_id}/assumir").status_code == 302
    ana.post(f"/alertas/{alerta_id}/decidir", data={"decisao": "fraude", "texto": "O cliente não reconhece o movimento."})
    with sessoes() as s:
        mov = s.get(Movimento, movimento_id)
        assert (mov.estado, mov.motivo) == ("cancelado", "Fraude confirmada.")
        conta = s.get(ContaBancaria, cli.conta.id)
        assert (conta.saldo, conta.saldo_retido) == (1_977_450, 0)  # disponível reposto
        acoes = [e.acao for e in s.scalars(select(EventoAuditoria))]
    assert "movimento_cancelado" in acoes
    assert "19.774,50 MT" in c.get("/cliente/").get_data(as_text=True)

    # Passo 6: o painel mostra o valor protegido.
    sup = criar_cliente(app, "supervisor", "chefe")
    painel = sup.get("/painel").get_data(as_text=True)
    assert "Valor protegido" in painel and "19.700,00 MT" in painel

    assert contas_bancarias.verificar_saldos(sessoes) == []
