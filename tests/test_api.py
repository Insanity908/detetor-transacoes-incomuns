"""Fase 4: API REST com chaves e limite de pedidos, processamento em segundo plano e simulador."""
import io
import json
import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import simulador
from app import criar_app
from detector import alertas, auditoria, chaves_api
from detector.api import MAX_LOTE, validar_pedido, ErroValidacao
from detector.banco import Alerta, Analise, ChaveApi, Tarefa, TransacaoAvaliada
from detector.simulacao import Simulador, clientes_do_treino
from tests.conftest import criar_cliente

N_CLIENTES = 80  # como a fixture pasta_pequena


@pytest.fixture()
def app(pasta_pequena):
    return criar_app(pasta_pequena)


def _sessoes(app):
    return app.config["SESSOES"]


@pytest.fixture()
def chave(app):
    texto, _ = chaves_api.criar(_sessoes(app), "Testes", "teste")
    return texto


def _api(app, chave_texto=None, metodo="post", caminho="/api/v1/transacoes", **kwargs):
    cabecalhos = {"Authorization": f"Bearer {chave_texto}"} if chave_texto else {}
    from flask.testing import FlaskClient
    cliente = FlaskClient(app)  # sem sessão nem CSRF, como um sistema externo
    return getattr(cliente, metodo)(caminho, headers=cabecalhos | kwargs.pop("headers", {}), **kwargs)


def _transacao(i=1, **extra):
    return {"id_transacao": f"T-{i}", "cliente": "C0001", "data_hora": "2026-05-01T14:30:00", "valor": 150.0,
            "cidade": "Maputo", "pais": "MZ", "categoria": "MERCEARIA"} | extra


def _contar(app, modelo, *condicoes):
    with _sessoes(app)() as s:
        return s.scalar(select(func.count()).select_from(modelo).where(*condicoes))


# ------------------------------------------------------------ autenticação
def test_sem_chave_ou_com_chave_errada_e_recusado(app, chave):
    for cabecalho in (None, "dtf_inventada", chave + "x"):
        r = _api(app, cabecalho, json=_transacao())
        assert r.status_code == 401 and r.json["erro"] == "nao_autenticado" and "Bearer" in r.headers["WWW-Authenticate"]
    assert _contar(app, TransacaoAvaliada) == 0


def test_sessao_da_interface_nao_serve_de_chave(app):
    admin = criar_cliente(app)  # tem cookie de sessão, mas a API só aceita chaves
    assert admin.post("/api/v1/transacoes", json=_transacao()).status_code == 401


def test_chave_no_cabecalho_alternativo(app, chave):
    r = _api(app, None, json=_transacao(), headers={"X-Chave-API": chave})
    assert r.status_code == 200


def test_chave_guardada_so_como_hash(app):
    texto, registo = chaves_api.criar(_sessoes(app), "Outra", "teste")
    with _sessoes(app)() as s:
        guardada = s.get(ChaveApi, registo.id)
    assert texto.startswith("dtf_") and len(texto) > 40 and texto not in (guardada.hash, guardada.prefixo)
    assert texto.startswith(guardada.prefixo)


# ------------------------------------------------------------ receber transações
def test_transacao_avaliada_gravada_e_alerta_na_fila(app, chave):
    r = _api(app, chave, json={"transacoes": [_transacao(1), _transacao(2, valor=90000.0, data_hora="2026-05-01T15:00:00")]})
    assert r.status_code == 200
    corpo = r.json
    assert (corpo["recebidas"], corpo["avaliadas"], corpo["duplicadas"]) == (2, 2, 0)
    primeira, alto = corpo["resultados"]
    assert [r["id_transacao"] for r in corpo["resultados"]] == ["T-1", "T-2"]  # pela ordem enviada
    for r in corpo["resultados"]:  # alerta na fila se e só se o nível não for Normal
        assert (r["alerta_id"] is not None) == r["alerta"] == (r["nivel"] != "Normal")
    assert alto["alerta"] and alto["estado_alerta"] == "novo" and alto["motivos"]
    assert corpo["alertas"] == sum(r["alerta"] for r in corpo["resultados"]) == _contar(app, Alerta)
    with _sessoes(app)() as s:
        analise = s.scalar(select(Analise).where(Analise.origem == "api"))
        assert analise.total == 2 and analise.chave_api_id is not None and analise.utilizador_id is None
    analista = criar_cliente(app, "analista")
    assert f'href="/alertas/{alto["alerta_id"]}"' in analista.get("/alertas").get_data(as_text=True)
    with _sessoes(app)() as s:
        registo = s.scalar(select(ChaveApi))
    assert (registo.pedidos, registo.transacoes) == (1, 2) and registo.ultimo_uso is not None


def test_consultar_transacao_mostra_a_decisao(app, chave):
    alerta_id = _api(app, chave, json=_transacao(7, valor=90000.0)).json["resultados"][0]["alerta_id"]
    r = _api(app, chave, "get", "/api/v1/transacoes/T-7")
    assert r.status_code == 200 and r.json["estado_alerta"] == "novo" and r.json["cliente"] == "C0001"
    ana = criar_cliente(app, "analista", "ana").utilizador
    alertas.assumir(_sessoes(app), alerta_id, ana)
    alertas.decidir(_sessoes(app), alerta_id, "fraude", "Confirmado com o cliente.", ana)
    assert _api(app, chave, "get", "/api/v1/transacoes/T-7").json["estado_alerta"] == "fraude"
    assert _api(app, chave, "get", "/api/v1/transacoes/nao-existe").status_code == 404
    assert _api(app, chave, "get", "/api/v1/transacoes/com espaco").status_code == 404


def test_reenviar_o_mesmo_id_nao_avalia_de_novo(app, chave):
    primeira = _api(app, chave, json=_transacao(1, valor=90000.0)).json
    segunda = _api(app, chave, json={"transacoes": [_transacao(1, valor=10.0), _transacao(2)]}).json
    assert (segunda["avaliadas"], segunda["duplicadas"]) == (1, 1)
    assert segunda["alertas"] == int(segunda["resultados"][1]["alerta"])  # só conta a nova
    repetida = segunda["resultados"][0]
    assert repetida["duplicada"] and repetida["risco"] == primeira["resultados"][0]["risco"]
    assert _contar(app, TransacaoAvaliada) == 2 and _contar(app, Alerta) == 1 + segunda["alertas"]


def test_historico_entre_pedidos_conta_para_a_rajada(app, chave):
    """Uma rajada enviada compra a compra, em pedidos separados, é reconhecida na última."""
    inicio = datetime(2026, 5, 1, 14, 0)
    ultima = None
    for i in range(6):
        t = _transacao(100 + i, valor=60.0, data_hora=(inicio + timedelta(minutes=2 * i)).isoformat())
        ultima = _api(app, chave, json=t).json["resultados"][0]
    assert any("compras na última hora" in m for m in ultima["motivos"])


def test_fuso_horario_convertido_para_hora_local(app, chave):
    r = _api(app, chave, json=_transacao(data_hora="2026-05-01T12:30:00+00:00"))
    esperado = datetime.fromisoformat("2026-05-01T12:30:00+00:00").astimezone().replace(tzinfo=None)
    with _sessoes(app)() as s:
        assert s.scalar(select(TransacaoAvaliada.data_hora)) == esperado
    assert r.status_code == 200


# ------------------------------------------------------------ validação
@pytest.mark.parametrize("corpo,campo", [
    ([_transacao()], "corpo"),
    ({"transacoes": []}, "transacoes"),
    ({"transacoes": [_transacao()] * (MAX_LOTE + 1)}, "transacoes"),
    ({"transacoes": [_transacao()], "extra": 1}, "extra"),
    (_transacao(valor=-5), "valor"),
    (_transacao(valor=0), "valor"),
    (_transacao(valor=True), "valor"),
    (_transacao(valor="150"), "valor"),
    (_transacao(valor=2e9), "valor"),
    (_transacao(data_hora="01/05/2026 14:30"), "data_hora"),
    (_transacao(data_hora="2999-01-01T00:00:00"), "data_hora"),
    (_transacao(data_hora="1999-12-31T23:00:00"), "data_hora"),
    (_transacao(id_transacao="com espaço"), "id_transacao"),
    (_transacao(id_transacao=""), "id_transacao"),
    (_transacao(pais="Moçambique"), "pais"),
    (_transacao(cliente="   "), "cliente"),
    (_transacao(cliente="C1\x00"), "cliente"),
    (_transacao(cidade="x" * 101), "cidade"),
    (_transacao(categoria=5), "categoria"),
    (_transacao(senha="x"), "senha"),
    ({k: v for k, v in _transacao().items() if k != "cliente"}, "cliente"),
    (_transacao(simulacao={"fraude": "sim"}), "simulacao"),
    (_transacao(simulacao={"fraude": True, "tipo": "inventado"}), "simulacao"),
    ({"transacoes": [_transacao(1), _transacao(1)]}, "id_transacao"),
    ({"transacoes": [_transacao(1), "texto"]}, "transacao"),
])
def test_dados_invalidos_sao_recusados_sem_gravar_nada(app, chave, corpo, campo):
    r = _api(app, chave, json=corpo)
    assert r.status_code == 422 and r.json["erro"] == "dados_invalidos"
    assert campo in {e["campo"] for e in r.json["erros"]}
    assert _contar(app, TransacaoAvaliada) == 0


def test_todos_os_erros_vem_com_o_indice(app, chave):
    r = _api(app, chave, json={"transacoes": [_transacao(1), _transacao(2, valor=-1), _transacao(3, pais="1")]})
    assert sorted((e["indice"], e["campo"]) for e in r.json["erros"]) == [(1, "valor"), (2, "pais")]


def test_nan_e_infinito_sao_recusados():
    with pytest.raises(ErroValidacao):
        validar_pedido(_transacao(valor=float("nan")))
    with pytest.raises(ErroValidacao):
        validar_pedido(_transacao(valor=float("inf")))


def test_corpo_que_nao_e_json(app, chave):
    assert _api(app, chave, data="cliente=C1").status_code == 415
    r = _api(app, chave, data="{nao e json", content_type="application/json")
    assert r.status_code == 400 and r.json["erro"] == "json_invalido"


def test_erros_da_api_vem_em_json(app, chave):
    assert _api(app, chave, "get", "/api/v1/nao-existe").json["erro"] == "nao_encontrado"
    r = _api(app, chave, "put", "/api/v1/transacoes", json={})
    assert r.status_code == 405 and r.json["erro"] == "metodo_nao_permitido"
    grande = {"transacoes": [_transacao(i, cidade="x" * 100) for i in range(30_000)]}
    r = _api(app, chave, data=json.dumps(grande), content_type="application/json")
    assert r.status_code == 413 and r.json["erro"] == "pedido_grande_demais"


def test_sem_modelos_responde_503(tmp_path):
    app = criar_app(tmp_path / "vazia")
    texto, _ = chaves_api.criar(_sessoes(app), "Testes", "teste")
    r = _api(app, texto, json=_transacao())
    assert r.status_code == 503 and r.json["erro"] == "sem_modelos"


# ------------------------------------------------------------ limite de pedidos
def test_limite_por_chave(app):
    texto, _ = chaves_api.criar(_sessoes(app), "Lenta", "teste", limite_por_minuto=2)
    outra, _ = chaves_api.criar(_sessoes(app), "Outra", "teste", limite_por_minuto=2)
    assert [_api(app, texto, "get", "/api/v1/estado").status_code for _ in range(3)] == [200, 200, 429]
    r = _api(app, texto, "get", "/api/v1/estado")
    assert r.json["erro"] == "limite_excedido" and int(r.headers["Retry-After"]) >= 1
    assert _api(app, outra, "get", "/api/v1/estado").status_code == 200  # cada chave tem o seu limite


def test_janela_do_limite_desliza():
    agora = [0.0]
    limitador = chaves_api.LimitadorPedidos(janela=60, relogio=lambda: agora[0])
    assert limitador.permitir(1, 2) == (True, 0) and limitador.permitir(1, 2)[0]
    assert limitador.permitir(1, 2) == (False, 61)
    agora[0] = 30
    assert limitador.permitir(1, 2) == (False, 31)
    agora[0] = 60.5
    assert limitador.permitir(1, 2)[0]


# ------------------------------------------------------------ estado e documentação
def test_estado_da_api(app, chave):
    r = _api(app, chave, "get", "/api/v1/estado").json
    assert r["chave"] == "Testes" and r["ultima_transacao_recebida"] is None and r["max_transacoes_por_pedido"] == MAX_LOTE
    _api(app, chave, json=_transacao(data_hora="2026-05-02T10:00:00"))
    assert _api(app, chave, "get", "/api/v1/estado").json["ultima_transacao_recebida"] == "2026-05-02T10:00:00"


def test_especificacao_openapi(app):
    r = _api(app, None, "get", "/api/v1/openapi.json")  # pública: só descreve a API
    espec = r.json
    assert r.status_code == 200 and espec["openapi"].startswith("3.")
    assert {"/api/v1/transacoes", "/api/v1/transacoes/{id_transacao}", "/api/v1/estado"} <= set(espec["paths"])
    transacao = espec["components"]["schemas"]["Transacao"]
    assert transacao["additionalProperties"] is False and "valor" in transacao["required"]


def test_documentacao_exige_entrar(app):
    assert criar_cliente(app, None).get("/api/docs").status_code == 302
    html = criar_cliente(app, "analista").get("/api/docs").get_data(as_text=True)
    assert "/api/v1/transacoes" in html and "Authorization: Bearer" in html and "id_transacao" in html


# ------------------------------------------------------------ gestão das chaves
def test_administrador_cria_e_revoga_chaves(app):
    admin = criar_cliente(app)
    html = admin.post("/chaves", data={"nome": "Banco X", "limite": "100"}).get_data(as_text=True)
    texto = re.search(r'<code class="chave">(dtf_[^<]+)</code>', html).group(1)
    assert "não volta a ser mostrada" in html
    assert texto not in admin.get("/chaves").get_data(as_text=True)  # só aparece uma vez
    assert _api(app, texto, "get", "/api/v1/estado").json["limite_por_minuto"] == 100
    with _sessoes(app)() as s:
        chave_id = s.scalar(select(ChaveApi.id).where(ChaveApi.nome == "Banco X"))
    admin.post(f"/chaves/{chave_id}/revogar")
    assert _api(app, texto, "get", "/api/v1/estado").status_code == 401
    acoes = [e.acao for e in auditoria.listar(_sessoes(app))[0]]
    assert "chave_criada" in acoes and "chave_revogada" in acoes
    assert admin.post("/chaves", data={"nome": "x"}).status_code == 400


@pytest.mark.parametrize("perfil", ["analista", "supervisor"])
def test_so_o_administrador_gere_chaves(app, perfil):
    cliente = criar_cliente(app, perfil)
    assert cliente.get("/chaves").status_code == 403
    assert cliente.post("/chaves", data={"nome": "Intrusa"}).status_code == 403
    assert _contar(app, ChaveApi) == 0


def test_gerir_cria_chave(app, capsys):
    import gerir
    texto = gerir.criar_chave("Simulador", 300)
    assert texto in capsys.readouterr().out
    assert _api(app, texto, "get", "/api/v1/estado").json["limite_por_minuto"] == 300


# ------------------------------------------------------------ ficheiros em segundo plano
CSV = ("cliente,data_hora,valor,cidade,pais,categoria\n"
       "C0001,2026-05-01 14:30,150,Maputo,MZ,MERCEARIA\nC0001,2026-05-01 15:00,90000,Maputo,MZ,MERCEARIA\n"
       "C0002,2026-05-02 03:10,500,Tete,AE,VIAGEM\n")


@pytest.fixture()
def app_segundo_plano(pasta_pequena, monkeypatch):
    monkeypatch.setenv("LINHAS_SEGUNDO_PLANO", "3")
    return criar_app(pasta_pequena)


def _enviar(cliente, conteudo=CSV, nome="grande.csv"):
    return cliente.post("/analisar", data={"ficheiro": (io.BytesIO(conteudo.encode()), nome)},
                        content_type="multipart/form-data")


def test_ficheiro_grande_vai_para_a_fila(app_segundo_plano):
    app = app_segundo_plano
    ana = criar_cliente(app, "analista", "ana")
    r = _enviar(ana)
    assert r.status_code == 302 and "/analisar/tarefa/" in r.headers["Location"]
    caminho = r.headers["Location"]
    app.config["FILA"].esperar()
    r = ana.get(caminho)
    assert r.status_code == 302 and r.headers["Location"].startswith("/analisar/")
    html = ana.get(r.headers["Location"]).get_data(as_text=True)
    assert "grande.csv" in html and "<strong>3</strong> transações analisadas" in html
    assert _contar(app, Alerta) >= 1 and _contar(app, Tarefa, Tarefa.estado == "concluida") == 1
    eventos = auditoria.listar(_sessoes(app), auditoria.Filtro(acao="analisar_ficheiro"))[0]
    assert eventos[0].nome_utilizador == "ana" and "segundo plano" in eventos[0].detalhe
    assert not list(app.config["CONFIGURACAO"].pasta_envios.glob("*.csv"))  # o ficheiro temporário foi apagado
    assert criar_cliente(app, "analista", "rui").get(caminho).status_code == 404


def test_ficheiro_pequeno_continua_imediato(app_segundo_plano):
    r = _enviar(criar_cliente(app_segundo_plano), CSV.rsplit("\n", 2)[0] + "\n")
    assert r.status_code == 200 and "transações analisadas" in r.get_data(as_text=True)


def test_ficheiro_invalido_e_recusado_logo(app_segundo_plano):
    r = _enviar(criar_cliente(app_segundo_plano), CSV.replace("150", "-1"))
    assert r.status_code == 400 and _contar(app_segundo_plano, Tarefa) == 0


def test_tarefa_que_falha_mostra_o_erro(app_segundo_plano):
    app = app_segundo_plano
    cliente = criar_cliente(app)
    caminho = _enviar(cliente).headers["Location"]
    app.config["FILA"].esperar()
    chave = caminho.rsplit("/", 1)[1]
    with _sessoes(app).begin() as s:  # simula uma falha: volta a pendente sem o ficheiro
        t = s.scalar(select(Tarefa).where(Tarefa.chave == chave))
        t.estado, t.analise_chave = "pendente", None
    app.config["FILA"]._enfileirar(chave)
    app.config["FILA"].esperar()
    html = cliente.get(caminho).get_data(as_text=True)
    assert "A análise falhou" in html
    assert "falhou" in cliente.get("/analisar").get_data(as_text=True)


def test_tarefas_por_acabar_retomam_no_arranque(app_segundo_plano, pasta_pequena):
    app = app_segundo_plano
    fila = app.config["FILA"]
    fila._enfileirar = lambda chave: None  # o servidor "para" antes de processar
    caminho = _enviar(criar_cliente(app)).headers["Location"]
    chave = caminho.rsplit("/", 1)[1]
    assert fila.obter(chave).estado == "pendente"
    novo = criar_app(pasta_pequena)  # arranque seguinte, mesma base
    novo.config["FILA"].esperar()
    assert novo.config["FILA"].obter(chave).estado == "concluida"


# ------------------------------------------------------------ simulador
def test_simulador_usa_os_clientes_do_treino(artefatos_pequenos):
    clientes = clientes_do_treino(N_CLIENTES)
    historico = artefatos_pequenos.historico
    assert {c["cliente"] for c in clientes} == set(historico["cliente"])
    c = clientes[0]
    compras = historico[(historico["cliente"] == c["cliente"]) & (historico["fraude"] == 0)]
    assert set(compras["categoria"]) - {"TRANSFERENCIA", "LEVANTAMENTO"} <= set(c["categorias"])


def test_simulador_gera_o_ritmo_e_a_fraude_pedidos():
    sim = Simulador(clientes_do_treino(), datetime(2026, 5, 1), fraude_pct=5, semente=7)
    transacoes = sim.avancar(datetime(2026, 5, 15))
    fraudes = [t for t in transacoes if t["simulacao"]["fraude"]]
    assert 600 < len(transacoes) / 14 < 800  # ~685 compras por dia nos 300 clientes
    assert 3.5 < 100 * len(fraudes) / len(transacoes) < 6.5
    assert {t["simulacao"]["tipo"] for t in fraudes} == {"valor_alto", "pais_estranho", "horario_estranho", "rajada",
                                                         "conta_tomada", "conta_esvaziada"}
    assert len({t["id_transacao"] for t in transacoes}) == len(transacoes)
    horas = [datetime.fromisoformat(t["data_hora"]).hour for t in transacoes if not t["simulacao"]["fraude"]]
    assert sum(h < 5 for h in horas) / len(horas) < 0.05  # de madrugada há poucas compras legítimas
    for t in transacoes[:50]:
        validar_pedido(t, agora=datetime(2026, 6, 1))  # tudo o que gera passa na validação da API


def test_simulador_e_reprodutivel_com_semente():
    a = Simulador(clientes_do_treino(), datetime(2026, 5, 1), semente=3, prefixo="X")
    b = Simulador(clientes_do_treino(), datetime(2026, 5, 1), semente=3, prefixo="X")
    assert a.avancar(datetime(2026, 5, 2)) == b.avancar(datetime(2026, 5, 2))


@pytest.mark.parametrize("args", [dict(fraude_pct=80), dict(tipos=("inventado",)), dict(tipos=())])
def test_simulador_recusa_parametros_invalidos(args):
    with pytest.raises(ValueError):
        Simulador(clientes_do_treino(), datetime(2026, 5, 1), **args)


def test_so_os_tipos_pedidos():
    sim = Simulador(clientes_do_treino(), datetime(2026, 5, 1), fraude_pct=20, tipos=("valor_alto",), semente=1)
    tipos = {t["simulacao"]["tipo"] for t in sim.avancar(datetime(2026, 5, 3)) if t["simulacao"]["fraude"]}
    assert tipos == {"valor_alto"}


def _transporte_flask(app):
    from flask.testing import FlaskClient
    cliente = FlaskClient(app)

    def transporte(metodo, caminho, corpo, chave):
        r = cliente.open(caminho, method=metodo, json=corpo, headers={"Authorization": f"Bearer {chave}"})
        return r.status_code, dict(r.headers), r.get_json(silent=True) or {}
    return transporte


def test_simulador_de_ponta_a_ponta(app, chave):
    """Com o simulador ligado, as transações chegam, são avaliadas e os alertas aparecem na fila."""
    transporte = _transporte_flask(app)
    api = simulador.ClienteApi("http://teste", chave, transporte=lambda m, c, b: transporte(m, c, b, chave))
    inicio = simulador.inicio_padrao(api)
    assert inicio > datetime(2026, 4, 30)  # continua depois do histórico de treino
    sim = Simulador(clientes_do_treino(N_CLIENTES), inicio, fraude_pct=15, semente=11)
    relogio = [0.0]
    saidas = []
    est = simulador.simular(api, sim, velocidade=3600, duracao=12, passo=1, intervalo_resumo=5,
                            relogio=lambda: relogio[0], dormir=lambda s: relogio.__setitem__(0, relogio[0] + s),
                            escrever=saidas.append)
    assert est.enviadas == _contar(app, TransacaoAvaliada) > 20
    assert est.fraudes > 0 and est.fraudes_com_alerta > 0
    lista, total = alertas.listar(_sessoes(app), criar_cliente(app, "supervisor").utilizador)
    assert total == est.fraudes_com_alerta + est.legitimas_com_alerta
    assert any("fraudes" in s and "apanhadas" in s for s in saidas)
    with _sessoes(app)() as s:  # a verdade do gerador fica gravada (para a Fase 5), mas não aparece aos analistas
        assert s.scalar(select(func.count()).where(TransacaoAvaliada.fraude_simulada.is_(True))) == est.fraudes
    pagina = criar_cliente(app, "analista").get(f"/alertas/{lista[0].id}").get_data(as_text=True)
    assert "simulacao" not in pagina and "fraude_simulada" not in pagina
    # um segundo simulador continua depois do primeiro
    assert simulador.inicio_padrao(api) > sim.agora - timedelta(hours=1)


def test_cliente_da_api_espera_quando_o_limite_e_atingido():
    respostas = iter([(429, {"Retry-After": "7"}, {}), (503, {}, {"mensagem": "ocupado"}), (200, {}, {"ok": 1})])
    esperas = []
    api = simulador.ClienteApi("http://x", "dtf_x", transporte=lambda *a: next(respostas), dormir=esperas.append)
    assert api.pedir("GET", "/api/v1/estado") == {"ok": 1} and esperas == [7.0, 1.0]


@pytest.mark.parametrize("estado", [401, 422])
def test_cliente_da_api_desiste_quando_nao_adianta(estado):
    api = simulador.ClienteApi("http://x", "dtf_x", transporte=lambda *a: (estado, {}, {"mensagem": "não"}),
                               dormir=lambda s: None)
    with pytest.raises(simulador.ErroFatal):
        api.pedir("POST", "/api/v1/transacoes", {})


def test_cliente_da_api_desiste_sem_servidor():
    def falha(*a):
        raise ConnectionRefusedError("recusado")
    api = simulador.ClienteApi("http://x", "dtf_x", transporte=falha, dormir=lambda s: None)
    with pytest.raises(simulador.ErroFatal, match="várias tentativas"):
        api.pedir("GET", "/api/v1/estado")


def test_simulador_sem_chave_explica(capsys, monkeypatch):
    monkeypatch.delenv("DETETOR_CHAVE_API", raising=False)
    assert simulador.main([]) == 2 and "Falta a chave" in capsys.readouterr().err


# ------------------------------------------------------------ banco: débitos e depósitos
def _cliente_banco(app, codigo="B0001", saldo=0, n=1):
    from detector import clientes, contas_bancarias
    form = dict(nome=f"Cliente Banco {n}", data_nascimento="1990-05-10", tipo_documento="NUIT",
                numero_documento=f"20000000{n}", telefone="841234567", email=None, cidade="MAPUTO", morada="Rua 1")
    c = clientes.criar(_sessoes(app), clientes.validar(form), "teste", codigo=codigo)
    if saldo:
        contas_bancarias.depositar(_sessoes(app), c.conta.id, saldo, "balcao", "teste")
    return c.conta.id


def _saldo(app, conta_id):
    from detector.banco import ContaBancaria
    with _sessoes(app)() as s:
        return s.get(ContaBancaria, conta_id).saldo


def _forcar(monkeypatch, nivel="Normal", risco=10.0):
    """Força o nível e o risco que o detetor devolve às transações das contas (o modelo real é irrelevante aqui)."""
    from detector import autorizacao

    def falso(artefatos, df, historico_extra=None, cortes=None):
        r = df.copy().reset_index(drop=True)
        r["risco"], r["anomalia"], r["nivel"] = risco, 10.0, nivel
        r["motivos"] = [["teste"]] * len(r)
        r["historico_curto"] = False
        return r
    monkeypatch.setattr(autorizacao, "avaliar_transacoes", falso)


def _saldos_certos(app):
    from detector import contas_bancarias
    assert contas_bancarias.verificar_saldos(_sessoes(app)) == []


def test_cliente_com_conta_e_saldo_e_autorizado_e_debitado(app, chave, monkeypatch):
    _forcar(monkeypatch)
    conta = _cliente_banco(app, saldo=100_000)
    r = _api(app, chave, json=_transacao(cliente="B0001", valor=150.0))
    assert r.status_code == 200, r.json
    res = r.json["resultados"][0]
    assert res["movimento"] == {"estado": "autorizado", "motivo": None}
    assert res["nivel"] == "Normal" and res["duplicada"] is False
    assert _saldo(app, conta) == 100_000 - 15_000
    _saldos_certos(app)


def test_alto_risco_fica_retido_com_alerta(app, chave, monkeypatch):
    from detector.banco import ContaBancaria
    _forcar(monkeypatch, "Alto risco", 70.0)
    conta = _cliente_banco(app, saldo=100_000)
    res = _api(app, chave, json=_transacao(cliente="B0001", valor=150.0)).json["resultados"][0]
    assert res["movimento"]["estado"] == "retido" and res["alerta"] is True and res["alerta_id"]
    with _sessoes(app)() as s:
        c = s.get(ContaBancaria, conta)
        assert (c.saldo, c.saldo_retido) == (100_000, 15_000)
    _saldos_certos(app)


def test_cliente_com_conta_sem_saldo_e_recusado(app, chave):
    conta = _cliente_banco(app, saldo=1_000)
    r = _api(app, chave, json=_transacao(cliente="B0001", valor=150.0))
    res = r.json["resultados"][0]
    assert res["movimento"] == {"estado": "recusado", "motivo": "Saldo insuficiente."}
    assert res["nivel"] is None and res["risco"] is None and res["alerta"] is False and res["motivos"] == []
    assert _saldo(app, conta) == 1_000


def test_cliente_sem_conta_nao_tem_movimento(app, chave):
    r = _api(app, chave, json=_transacao())
    res = r.json["resultados"][0]
    assert res["movimento"] is None and res["nivel"] is not None and res["duplicada"] is False


def test_lote_misto_mantem_a_ordem(app, chave):
    _cliente_banco(app, saldo=1_000)
    lote = [_transacao(1), _transacao(2, cliente="B0001", valor=150.0), _transacao(3)]
    r = _api(app, chave, json={"transacoes": lote})
    res = r.json["resultados"]
    assert [x["id_transacao"] for x in res] == ["T-1", "T-2", "T-3"]
    assert res[0]["movimento"] is None and res[2]["movimento"] is None
    assert res[1]["movimento"]["estado"] == "recusado"
    assert r.json["recebidas"] == 3 and r.json["avaliadas"] == 3


def test_repetida_de_conta_nao_debita_duas_vezes(app, chave):
    conta = _cliente_banco(app, saldo=1_000)
    _api(app, chave, json=_transacao(cliente="B0001", valor=150.0))  # recusada por saldo
    r = _api(app, chave, json=_transacao(cliente="B0001", valor=150.0))
    res = r.json["resultados"][0]
    assert res["duplicada"] is True and res["movimento"]["estado"] == "recusado"
    assert r.json["duplicadas"] == 1 and _saldo(app, conta) == 1_000
    from detector.banco import Movimento
    assert _contar(app, Movimento, Movimento.id_transacao == "T-1") == 1


def test_repetida_autorizada_devolve_o_mesmo_estado(app, chave, monkeypatch):
    _forcar(monkeypatch)
    conta = _cliente_banco(app, saldo=100_000)
    r1 = _api(app, chave, json=_transacao(cliente="B0001", valor=10.0)).json["resultados"][0]
    assert r1["movimento"]["estado"] == "autorizado" and _saldo(app, conta) == 99_000
    r2 = _api(app, chave, json=_transacao(cliente="B0001", valor=10.0)).json["resultados"][0]
    assert r2["duplicada"] is True and r2["movimento"] == r1["movimento"] and _saldo(app, conta) == 99_000
    _saldos_certos(app)


def test_valor_com_tres_casas_para_conta_e_invalido(app, chave):
    conta = _cliente_banco(app, saldo=100_000)
    r = _api(app, chave, json=_transacao(cliente="B0001", valor=10.123))
    assert r.status_code == 422 and r.json["erros"][0]["campo"] == "valor" and r.json["erros"][0]["indice"] == 0
    assert _saldo(app, conta) == 100_000


def test_transferencia_de_simulacao_sai_para_fora(app, chave, monkeypatch):
    from detector.banco import Movimento
    _forcar(monkeypatch)
    conta = _cliente_banco(app, saldo=100_000)
    r = _api(app, chave, json=_transacao(cliente="B0001", valor=10.0, categoria="TRANSFERENCIA",
                                         simulacao={"fraude": False}))
    assert r.status_code == 200 and r.json["resultados"][0]["movimento"]["estado"] == "autorizado"
    with _sessoes(app)() as s:
        movimentos = s.scalars(select(Movimento).where(Movimento.tipo != "deposito")).all()
    assert [(m.tipo, m.contraparte_id, m.canal) for m in movimentos] == [("transferencia_enviada", None, "simulador")]
    assert _saldo(app, conta) == 99_000  # debitado e sem crédito em mais nenhuma conta
    _saldos_certos(app)


def test_canal_api_sem_simulacao_e_uso_registado(app, chave):
    from detector.banco import Movimento
    _cliente_banco(app, saldo=10_000_000)
    _api(app, chave, json={"transacoes": [_transacao(1, cliente="B0001", valor=10.0), _transacao(2)]})
    with _sessoes(app)() as s:
        m = s.scalars(select(Movimento).where(Movimento.id_transacao == "T-1")).one()
        assert m.canal == "api" and m.tipo == "compra" and m.criado_por == "Testes"
        assert s.scalar(select(ChaveApi.transacoes).where(ChaveApi.nome == "Testes")) == 2


def _deposito(i=1, **extra):
    return {"id_deposito": f"D-{i}", "cliente": "B0001", "valor": 250.5, "data_hora": "2026-05-01T14:30:00",
            "descricao": "Salário"} | extra


def test_deposito_soma_ao_saldo_e_e_idempotente(app, chave):
    conta = _cliente_banco(app, saldo=1_000)
    r = _api(app, chave, caminho="/api/v1/depositos", json={"depositos": [_deposito()]})
    assert r.status_code == 200, r.json
    assert r.json["resultados"] == [{"id_deposito": "D-1", "estado": "autorizado", "saldo": 26_050 / 100}]
    r = _api(app, chave, caminho="/api/v1/depositos", json={"depositos": [_deposito()]})
    assert r.status_code == 200 and r.json["resultados"][0]["saldo"] == 260.5
    assert _saldo(app, conta) == 26_050


def test_deposito_cliente_sem_conta_ou_valor_invalido_nao_grava_nada(app, chave):
    conta = _cliente_banco(app, saldo=1_000)
    lote = [_deposito(1), _deposito(2, cliente="C0001"), _deposito(3, valor=1.234), _deposito(4, valor=0)]
    r = _api(app, chave, caminho="/api/v1/depositos", json={"depositos": lote})
    assert r.status_code == 422
    assert sorted((e["indice"], e["campo"]) for e in r.json["erros"]) == [(1, "cliente"), (2, "valor"), (3, "valor")]
    assert _saldo(app, conta) == 1_000


def test_deposito_exige_chave_e_conta_bloqueada_e_recusada(app, chave):
    from detector import contas_bancarias
    conta = _cliente_banco(app, saldo=1_000)
    assert _api(app, None, caminho="/api/v1/depositos", json={"depositos": [_deposito()]}).status_code == 401
    contas_bancarias.bloquear(_sessoes(app), conta, True, "teste")
    r = _api(app, chave, caminho="/api/v1/depositos", json={"depositos": [_deposito()]})
    assert r.status_code == 200 and r.json["resultados"][0]["estado"] == "recusado"
    assert r.json["resultados"][0]["motivo"] and _saldo(app, conta) == 1_000


def test_id_de_deposito_nao_colide_com_id_de_transacao(app, chave):
    conta = _cliente_banco(app, saldo=10_000_000)
    _api(app, chave, json=_transacao(1, cliente="B0001", valor=10.0, id_transacao="X-1"))
    r = _api(app, chave, caminho="/api/v1/depositos", json={"depositos": [_deposito(id_deposito="X-1")]})
    assert r.status_code == 200 and r.json["resultados"][0]["estado"] == "autorizado"


def test_pedidos_sobrepostos_com_o_mesmo_id_so_debitam_uma_vez(app, chave, monkeypatch):
    """Dois pedidos veem "sem movimento" ao mesmo tempo: o índice único desfaz o segundo, que devolve o primeiro."""
    from datetime import datetime
    from detector import api, contas_bancarias
    _forcar(monkeypatch)
    conta = _cliente_banco(app, saldo=100_000)
    with _sessoes(app).begin() as s:  # o outro pedido já gravou T-1 e debitou
        from detector.banco import ContaBancaria
        contas_bancarias.debitar(s, s.get(ContaBancaria, conta), "compra", 15_000, "api", "outro",
                                 datetime(2026, 5, 1, 14, 30), "autorizado", id_transacao="T-1")
    real, chamadas = api._movimentos_anteriores, []

    def uma_vez_cego(sessoes, contas):
        chamadas.append(1)
        return {} if len(chamadas) == 1 else real(sessoes, contas)
    monkeypatch.setattr(api, "_movimentos_anteriores", uma_vez_cego)
    r = _api(app, chave, json={"transacoes": [_transacao(1, cliente="B0001", valor=150.0),
                                              _transacao(2, cliente="B0001", valor=10.0)]})
    assert r.status_code == 200, r.json
    res = r.json["resultados"]
    assert res[0]["duplicada"] is True and res[0]["movimento"]["estado"] == "autorizado"
    assert res[1]["duplicada"] is False and res[1]["movimento"]["estado"] == "autorizado"
    assert _saldo(app, conta) == 100_000 - 15_000 - 1_000
    _saldos_certos(app)


def test_depositos_sobrepostos_com_o_mesmo_id_so_creditam_uma_vez(app, chave, monkeypatch):
    from detector import api
    conta = _cliente_banco(app, saldo=1_000)
    real = api._gravar_depositos

    def concorrente(sessoes, depositos):
        if not getattr(concorrente, "feito", False):  # o outro pedido grava D-1 e o índice único desfaz este
            concorrente.feito = True
            real(sessoes, depositos)
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))
        return real(sessoes, depositos)
    monkeypatch.setattr(api, "_gravar_depositos", concorrente)
    r = _api(app, chave, caminho="/api/v1/depositos", json={"depositos": [_deposito()]})
    assert r.status_code == 200 and r.json["resultados"][0]["saldo"] == 26_050 / 100
    assert _saldo(app, conta) == 26_050
    _saldos_certos(app)
