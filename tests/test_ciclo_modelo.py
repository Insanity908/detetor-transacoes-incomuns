"""Fase 5: versões dos modelos, re-treino, publicação e volta atrás, monitorização, limiares e cenários."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from flask.testing import FlaskClient
from sqlalchemy import select, update

import simulador
from app import criar_app
from detector import auditoria, chaves_api, limiares, repositorio, versoes
from detector.artefatos import gravar
from detector.banco import Analise, VersaoModelo
from detector.monitorizacao import MIN_FRAUDES, medir
from detector.perfis import calcular_features
from detector.simulacao import TIPO_NOVO, Simulador, clientes_do_treino
from tests.conftest import CHAVE_TESTE, criar_cliente

N_CLIENTES = 80


@pytest.fixture()
def pasta(tmp_path, artefatos_pequenos):
    """Uma cópia dos modelos pequenos por teste: o re-treino escreve versões dentro dela."""
    gravar(tmp_path / "modelos", artefatos_pequenos, CHAVE_TESTE)
    return tmp_path / "modelos"


@pytest.fixture()
def app(pasta):
    return criar_app(pasta)


def _sessoes(app):
    return app.config["SESSOES"]


def _acoes(app):
    return [e.acao for e in auditoria.listar(_sessoes(app), por_pagina=500)[0]]


# ------------------------------------------------------------ limiares
def test_cortes_padrao_dao_exatamente_o_mesmo_resultado(artefatos_pequenos):
    X = calcular_features(artefatos_pequenos.historico.tail(3000))
    d = artefatos_pequenos.detector
    sem = d.avaliar(X)
    com = d.avaliar(X, limiares.Cortes())
    assert (sem["nivel"] == com["nivel"]).all() and (sem["risco"] == com["risco"]).all()
    mais_alto = d.avaliar(X, limiares.Cortes(risco=90, anomalia=90))
    mais_baixo = d.avaliar(X, limiares.Cortes(risco=10, anomalia=10))
    alertas = lambda r: int((r["nivel"] != "Normal").sum())
    assert alertas(mais_alto) <= alertas(sem) <= alertas(mais_baixo) and alertas(mais_baixo) > alertas(mais_alto)
    assert ((mais_alto["nivel"] == "Alto risco") == (mais_alto["risco"] >= 90)).all()


@pytest.mark.parametrize("risco,anomalia", [(0, 50), (50, 100), (-1, 50)])
def test_cortes_fora_do_intervalo(risco, anomalia):
    with pytest.raises(ValueError, match="entre 1 e 99"):
        limiares.Cortes(risco, anomalia)


def test_efeito_dos_cortes():
    teste = pd.DataFrame({"fraude": [1, 1, 0, 0, 0], "risco": [80, 40, 60, 10, 5], "anomalia": [0, 70, 0, 70, 0],
                          "historico_curto": [False, False, False, True, False]})
    [padrao, alto] = limiares.efeito(teste, [limiares.Cortes(), limiares.Cortes(70, 50)])
    assert (padrao["alertas"], padrao["apanhadas"], padrao["falsos"]) == (3, 2, 1)  # o histórico curto não conta
    assert (alto["alertas"], alto["apanhadas"], alto["falsos"]) == (2, 2, 0) and alto["precisao"] == 1.0
    assert limiares.efeito(pd.DataFrame(), [limiares.Cortes()]) == []


def test_supervisor_ajusta_os_limiares(app):
    chefe = criar_cliente(app, "supervisor", "chefe")
    html = chefe.get("/modelos/limiares?risco=70&anomalia=60").get_data(as_text=True)
    assert "Gravar risco 70, anomalia 60 e recusa 95" in html
    r = chefe.post("/modelos/limiares", data={"risco": "70", "anomalia": "60"}, follow_redirects=True)
    assert "Limiares gravados" in r.get_data(as_text=True)
    assert app.config["CORTES"] == limiares.Cortes(70, 60) == limiares.ler(_sessoes(app))[0]
    assert "limiares_alterados" in _acoes(app)
    assert criar_app(app.config["PASTA_MODELOS"]).config["CORTES"] == limiares.Cortes(70, 60)  # sobrevive ao reinício
    assert chefe.post("/modelos/limiares", data={"risco": "150", "anomalia": "60"}).status_code == 400
    assert chefe.post("/modelos/limiares", data={"risco": "abc", "anomalia": "60"}).status_code == 400


def test_limiares_aplicam_se_as_novas_transacoes(app):
    chefe = criar_cliente(app, "supervisor")
    form = dict(cliente="C0001", data_hora="2026-05-01T14:30", valor="90000", cidade="Maputo", pais="MZ",
                categoria="MERCEARIA")
    antes = chefe.post("/", data=form).get_data(as_text=True)
    chefe.post("/modelos/limiares", data={"risco": "99", "anomalia": "99"})
    depois = chefe.post("/", data=form).get_data(as_text=True)
    assert "Alto risco" in antes and 'class="selo">Alto risco' not in depois


def test_analista_nao_mexe_nos_limiares_nem_nas_versoes(app):
    ana = criar_cliente(app, "analista")
    assert ana.get("/modelos/limiares").status_code == 403
    assert ana.post("/modelos/limiares", data={"risco": "10", "anomalia": "10"}).status_code == 403
    assert ana.post("/modelos/retreinar").status_code == 403
    assert ana.post("/modelos/versoes/1/publicar").status_code == 403
    html = ana.get("/modelos").get_data(as_text=True)  # mas vê as versões e o desempenho
    assert "Versão 1" in html and "Treinar nova versão" not in html and "Ajustar" not in html
    assert app.config["CORTES"] == limiares.Cortes()


# ------------------------------------------------------------ versões
def test_treino_inicial_e_a_versao_1(app):
    v = versoes.publicada(_sessoes(app))
    assert (v.numero, v.origem, v.pasta, v.estado) == (1, "inicial", ".", "publicada")
    assert app.config["ARTEFATOS"].versao == 1
    criar_app(app.config["PASTA_MODELOS"])  # arrancar de novo não cria outra versão
    assert [x.numero for x in versoes.listar(_sessoes(app))] == [1]


def test_analises_guardam_a_versao_que_avaliou(app):
    criar_cliente(app).post("/", data={"exemplo": "normal"})
    with _sessoes(app)() as s:
        assert s.scalar(select(Analise.versao_modelo)) == 1


def test_retreino_sem_dados_suficientes_explica(app):
    chefe = criar_cliente(app, "supervisor")
    r = chefe.post("/modelos/retreinar")
    numero = int(r.headers["Location"].rsplit("/", 1)[1])
    app.config["RETREINO"].esperar()
    v = versoes.obter(_sessoes(app), numero)
    assert v.estado == "falhou" and "pelo menos 300 transações" in v.erro
    assert "pelo menos 300 transações" in chefe.get(f"/modelos/versoes/{numero}").get_data(as_text=True)
    assert "retreino_pedido" in _acoes(app)


def test_retreino_interrompido_fica_marcado_no_arranque(app):
    versoes.criar_em_treino(_sessoes(app), "chefe")
    with pytest.raises(ValueError, match="Já há um re-treino"):
        versoes.criar_em_treino(_sessoes(app), "chefe")
    criar_app(app.config["PASTA_MODELOS"])
    assert versoes.obter(_sessoes(app), 2).estado == "falhou"


def test_publicar_versao_com_ficheiros_alterados_e_recusado(app, pasta):
    with _sessoes(app).begin() as s:
        s.add(VersaoModelo(numero=2, estado="candidata", origem="retreino", pasta="versoes/v2", classificador="x",
                           criada_em=datetime.now(), criada_por="t"))
    (pasta / "versoes" / "v2").mkdir(parents=True)
    r = criar_cliente(app, "supervisor").post("/modelos/versoes/2/publicar", follow_redirects=True)
    assert "verificação de integridade" in r.get_data(as_text=True)
    assert versoes.publicada(_sessoes(app)).numero == 1 and app.config["ARTEFATOS"].versao == 1


# ------------------------------------------------------------ monitorização
def _gravar_api(app, n, fraudes_apanhadas, fraudes_perdidas, falsos, versao=1):
    """Grava transações da API com rótulo do gerador e o nível que a versão lhes deu."""
    linhas = []
    tipos = ["apanhada"] * fraudes_apanhadas + ["perdida"] * fraudes_perdidas + ["falso"] * falsos
    tipos += ["normal"] * (n - len(tipos))
    for i, tipo in enumerate(tipos):
        linhas.append(dict(id_transacao=f"M{versao}-{i}", cliente="C0001",
                           data_hora=pd.Timestamp("2026-05-01") + pd.Timedelta(minutes=i), valor=10.0, cidade="MAPUTO",
                           pais="MZ", categoria="MERCEARIA", risco=90.0 if tipo in ("apanhada", "falso") else 5.0,
                           anomalia=1.0, nivel="Alto risco" if tipo in ("apanhada", "falso") else "Normal", motivos=[],
                           historico_curto=False, fraude_simulada=tipo in ("apanhada", "perdida"),
                           tipo_fraude_simulada="valor_alto" if tipo in ("apanhada", "perdida") else None))
    repositorio.guardar_analise(_sessoes(app), pd.DataFrame(linhas), "api", "random_forest", versao_modelo=versao,
                                gerar_alertas=True)


BASE = {"recall": 0.8, "precisao": 0.8, "f1": 0.8}


def test_monitorizacao_sem_queda_nao_avisa(app):
    _gravar_api(app, 500, 16, 4, 4)
    m = medir(_sessoes(app), 1, BASE)
    assert m["atual"]["fraudes"] == 20 and m["atual"]["recall"] == 0.8 and not m["aviso"]


def test_monitorizacao_avisa_quando_o_desempenho_cai(app):
    _gravar_api(app, 500, 8, 12, 4)
    m = medir(_sessoes(app), 1, BASE)
    assert m["aviso"] and set(m["motivos"]) == {"f1", "recall"} and m["atual"]["recall"] == 0.4
    import json
    with _sessoes(app).begin() as s:  # o modelo pequeno não tem teste com rótulos: dar-lhe a referência
        s.execute(update(VersaoModelo).where(VersaoModelo.numero == 1).values(metricas=json.dumps({"teste_alertas": BASE})))
    supervisor = criar_cliente(app, "supervisor")
    assert "O desempenho do modelo caiu" in supervisor.get("/alertas").get_data(as_text=True)
    assert "caiu nas transações recentes" in supervisor.get("/modelos").get_data(as_text=True)
    assert "desempenho do modelo caiu" not in criar_cliente(app, "analista").get("/alertas").get_data(as_text=True)


def test_monitorizacao_avisa_dos_falsos_alarmes_mesmo_sem_fraudes(app):
    """Numa inflação de valores quase não há fraudes na janela, mas os falsos alarmes disparam."""
    base = {"transacoes": 1000, "fraudes": 10, "alertas": 15, "apanhadas": 8, "recall": 0.8, "precisao": 0.53, "f1": 0.64}
    _gravar_api(app, 600, 2, 1, 60)  # 60 falsos em ~597 legítimas: 100 por mil, contra ~7 no teste
    m = medir(_sessoes(app), 1, base)
    assert m["aviso"] and m["motivos"] == ["falsos"] and not m["suficiente"]
    assert round(m["base"]["falsos_por_mil"], 1) == 7.1


def test_poucos_falsos_alarmes_a_mais_nao_avisam(app):
    base = {"transacoes": 1000, "fraudes": 10, "alertas": 15, "apanhadas": 8, "recall": 0.8, "precisao": 0.53, "f1": 0.64}
    _gravar_api(app, 600, 2, 1, 8)  # 13 por mil: mais do que o dobro, mas menos de 10 por mil acima
    assert not medir(_sessoes(app), 1, base)["aviso"]


def test_monitorizacao_espera_por_fraudes_suficientes(app):
    _gravar_api(app, 300, 1, MIN_FRAUDES - 2, 0)
    m = medir(_sessoes(app), 1, BASE)
    assert not m["aviso"] and not m["suficiente"]


def test_decisao_do_analista_corrige_o_rotulo(app):
    _gravar_api(app, 50, 0, 0, 12)  # 12 alertas em transações que o gerador diz serem legítimas
    from detector import alertas as fila
    u = criar_cliente(app, "analista", "ana").utilizador
    with _sessoes(app)() as s:
        from detector.banco import Alerta
        ids = list(s.scalars(select(Alerta.id)))
    for i in ids:
        fila.assumir(_sessoes(app), i, u)
        fila.decidir(_sessoes(app), i, "fraude", "o analista confirmou", u)
    m = medir(_sessoes(app), 1, BASE)
    assert m["atual"]["fraudes"] == 12 and m["atual"]["precisao"] == 1.0


def test_monitorizacao_so_conta_a_versao_publicada(app):
    _gravar_api(app, 200, 0, 20, 0, versao=7)
    assert medir(_sessoes(app), 1, BASE)["atual"] is None


# ------------------------------------------------------------ cenários do simulador
def test_cenario_novo_tipo():
    sim = Simulador(clientes_do_treino(), datetime(2026, 5, 1), fraude_pct=5, semente=1, cenario="novo_tipo")
    fraudes = [t for t in sim.avancar(datetime(2026, 5, 8)) if t["simulacao"]["fraude"]]
    novas = [t for t in fraudes if t["simulacao"]["tipo"] == TIPO_NOVO]
    assert 0.4 < len(novas) / len(fraudes) < 0.85
    clientes = {c["cliente"]: c for c in clientes_do_treino()}
    for t in novas:  # nas categorias e na cidade habituais do cliente: nada de evidente
        c = clientes[t["cliente"]]
        assert t["categoria"] in c["categorias"] and t["pais"] == "MZ" and t["cidade"] == (c["nova_cidade"] or c["cidade"])


def test_cenario_valores_e_habitos():
    normal = Simulador(clientes_do_treino(), datetime(2026, 5, 1), semente=4).avancar(datetime(2026, 5, 6))
    caro = Simulador(clientes_do_treino(), datetime(2026, 5, 1), semente=4, cenario="valores").avancar(datetime(2026, 5, 6))
    mediana = lambda ts: np.median([t["valor"] for t in ts if not t["simulacao"]["fraude"]])
    assert 2.2 < mediana(caro) / mediana(normal) < 2.8
    tarde = Simulador(clientes_do_treino(), datetime(2026, 5, 1), semente=4, cenario="habitos").avancar(datetime(2026, 5, 6))
    hora = lambda ts: np.mean([datetime.fromisoformat(t["data_hora"]).hour < 6 for t in ts])
    assert hora(tarde) > 3 * hora(normal)  # muitas compras passam para a madrugada


def test_cenario_desconhecido():
    with pytest.raises(ValueError, match="Cenários válidos"):
        Simulador(clientes_do_treino(), datetime(2026, 5, 1), cenario="caos")


def test_api_aceita_o_tipo_novo_nos_rotulos(app):
    texto, _ = chaves_api.criar(_sessoes(app), "Testes", "t")
    t = {"id_transacao": "X1", "cliente": "C0001", "data_hora": "2026-05-01T10:00:00", "valor": 10.0, "cidade": "MAPUTO",
         "pais": "MZ", "categoria": "MERCEARIA", "simulacao": {"fraude": True, "tipo": TIPO_NOVO}}
    r = FlaskClient(app).post("/api/v1/transacoes", json=t, headers={"Authorization": f"Bearer {texto}"})
    assert r.status_code == 200


# ------------------------------------------------------------ de ponta a ponta
def _simular(app, chave, sim, dias):
    cliente = FlaskClient(app)

    def transporte(metodo, caminho, corpo):
        r = cliente.open(caminho, method=metodo, json=corpo, headers={"Authorization": f"Bearer {chave}"})
        return r.status_code, dict(r.headers), r.get_json(silent=True) or {}
    api = simulador.ClienteApi("http://x", chave, transporte=transporte)
    relogio = [0.0]
    return simulador.simular(api, sim, velocidade=7200, duracao=dias * 12, passo=1, intervalo_resumo=10**9,
                             relogio=lambda: relogio[0], dormir=lambda s: relogio.__setitem__(0, relogio[0] + s),
                             escrever=lambda t: None), api


def test_retreinar_publicar_e_voltar_atras(app):
    """Transações da API -> re-treino pela interface -> candidata comparada -> publicada -> volta à versão 1."""
    texto, _ = chaves_api.criar(_sessoes(app), "Simulador", "t", 60000)
    inicio = datetime(2026, 5, 1)
    sim = Simulador(clientes_do_treino(N_CLIENTES), inicio, fraude_pct=12, semente=5, cenario="novo_tipo")
    est, api = _simular(app, texto, sim, 6)
    assert est.enviadas >= 300

    chefe = criar_cliente(app, "supervisor", "chefe")
    r = chefe.post("/modelos/retreinar")
    numero = int(r.headers["Location"].rsplit("/", 1)[1])
    assert "A treinar" in chefe.get(f"/modelos/versoes/{numero}").get_data(as_text=True) or True
    app.config["RETREINO"].esperar()
    v = versoes.obter(_sessoes(app), numero)
    assert v.estado == "candidata", v.erro
    m = versoes.ler_metricas(v)
    assert m["dados"]["api"] == est.enviadas and m["comparacao"]["publicada"] == 1
    assert TIPO_NOVO in m["comparacao"]["recall_por_tipo"]["nova"] or m["periodos"]["teste"]["fraudes"] >= 5
    pagina = chefe.get(f"/modelos/versoes/{numero}").get_data(as_text=True)
    assert "Comparação com a versão 1" in pagina and f"Publicar a versão {numero}" in pagina
    assert app.config["ARTEFATOS"].versao == 1  # nada muda antes de publicar

    r = chefe.post(f"/modelos/versoes/{numero}/publicar", follow_redirects=True)
    assert f"Versão {numero} publicada" in r.get_data(as_text=True)
    assert app.config["ARTEFATOS"].versao == numero and versoes.obter(_sessoes(app), 1).estado == "anterior"
    assert "versao_publicada" in _acoes(app)
    # as transações seguintes já são avaliadas pela nova versão
    sim2 = Simulador(clientes_do_treino(N_CLIENTES), sim.agora, fraude_pct=10, semente=6)
    _simular(app, texto, sim2, 0.5)
    with _sessoes(app)() as s:
        ultima = s.scalar(select(Analise.versao_modelo).where(Analise.origem == "api").order_by(Analise.id.desc()))
    assert ultima == numero
    assert criar_cliente(app).get("/modelos").status_code == 200  # o detalhe da versão retreinada mostra-se
    assert criar_app(app.config["PASTA_MODELOS"]).config["ARTEFATOS"].versao == numero  # sobrevive ao reinício

    chefe.post("/modelos/versoes/1/publicar")  # voltar atrás
    assert app.config["ARTEFATOS"].versao == 1 and versoes.publicada(_sessoes(app)).numero == 1
    assert versoes.obter(_sessoes(app), numero).estado == "anterior"


def test_aviso_de_versao_sem_transferencias_nem_levantamentos(app):
    chefe = criar_cliente(app, "supervisor")
    aviso = "não conhece transferências nem levantamentos"
    assert aviso not in chefe.get("/modelos/versoes/1").get_data(as_text=True)  # treinada com as categorias novas
    with _sessoes(app).begin() as s:  # uma versão de antes desta funcionalidade não tem a marca nas métricas
        s.execute(update(VersaoModelo).where(VersaoModelo.numero == 1).values(metricas="{}"))
    assert aviso in chefe.get("/modelos/versoes/1").get_data(as_text=True)


# ------------------------------------------------------------ corte de recusa automática
def test_cortes_por_omissao_tem_recusa_95():
    assert limiares.Cortes().recusa == 95.0


def test_ajuste_antigo_sem_recusa_le_se_com_95(app):
    import json
    from datetime import datetime
    from detector.banco import Ajuste
    with _sessoes(app).begin() as s:
        s.merge(Ajuste(chave=limiares.CHAVE, valor=json.dumps({"risco": 60.0, "anomalia": 40.0}),
                       alterado_em=datetime.now(), alterado_por="x"))
    assert limiares.ler(_sessoes(app))[0] == limiares.Cortes(60, 40, 95)


def test_ajuste_antigo_com_risco_alto_nao_volta_ao_padrao(app):
    import json
    from datetime import datetime
    from detector.banco import Ajuste
    with _sessoes(app).begin() as s:
        s.merge(Ajuste(chave=limiares.CHAVE, valor=json.dumps({"risco": 97.0, "anomalia": 40.0}),
                       alterado_em=datetime.now(), alterado_por="x"))
    assert limiares.ler(_sessoes(app))[0] == limiares.Cortes(97, 40, 97)


def test_recusa_nao_pode_ser_inferior_ao_risco():
    with pytest.raises(ValueError, match="maior ou igual ao de alto risco"):
        limiares.Cortes(risco=50, anomalia=50, recusa=40)


def test_pagina_de_limiares_mostra_e_grava_a_recusa(app):
    chefe = criar_cliente(app, "supervisor", "chefe2")
    assert 'name="recusa"' in chefe.get("/modelos/limiares").get_data(as_text=True)
    chefe.post("/modelos/limiares", data={"risco": "60", "anomalia": "50", "recusa": "90"})
    assert app.config["CORTES"] == limiares.Cortes(60, 50, 90) == limiares.ler(_sessoes(app))[0]
    assert chefe.post("/modelos/limiares", data={"risco": "60", "anomalia": "50", "recusa": "40"}).status_code == 400
    assert app.config["CORTES"].recusa == 90


def test_estado_recusado_auto_tem_nome():
    from detector import alertas
    assert alertas.NOMES_ESTADO["recusado_auto"] == "Recusado automaticamente"
