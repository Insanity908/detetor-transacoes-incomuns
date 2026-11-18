"""Fase 1: configuração, base de dados, migrações, persistência, registo de eventos e /saude."""
import io
import re

import pandas as pd
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import criar_app
from tests.conftest import criar_cliente
from detector import repositorio
from detector.banco import Base, criar_fabrica_sessoes, criar_motor, migrar
from detector.configuracao import ler_configuracao

CSV = ("cliente,data_hora,valor,cidade,pais,categoria\n"
       "C0001,2026-05-01 14:30,150,Maputo,MZ,MERCEARIA\nC0001,2026-05-01 15:00,90000,Maputo,MZ,MERCEARIA\n")


def _enviar(cliente, conteudo=CSV, nome="lote.csv"):
    return cliente.post("/analisar", data={"ficheiro": (io.BytesIO(conteudo.encode()), nome)},
                        content_type="multipart/form-data")


# ------------------------------------------------------------ configuração
def test_configuracao_padrao(monkeypatch):
    for nome in ("URL_BANCO", "PASTA_LOGS", "PORTA", "HOST"):
        monkeypatch.delenv(nome, raising=False)
    cfg = ler_configuracao(ficheiro_env=None)
    assert cfg.ambiente == "teste" and cfg.porta == 5000 and cfg.host == "127.0.0.1"
    assert cfg.url_banco.startswith("sqlite:///") and cfg.url_banco.endswith("instancia/detetor.db")
    assert len(cfg.chave_secreta) >= 32


def test_producao_exige_chave_secreta(monkeypatch):
    monkeypatch.setenv("AMBIENTE", "producao")
    monkeypatch.setenv("CHAVE_SECRETA", "")
    with pytest.raises(ValueError, match="CHAVE_SECRETA"):
        ler_configuracao(ficheiro_env=None)


@pytest.mark.parametrize("nome,valor", [("AMBIENTE", "outro"), ("PORTA", "abc"), ("PORTA", "70000"),
                                        ("NIVEL_LOG", "MUITO")])
def test_configuracao_invalida_explica_o_erro(monkeypatch, nome, valor):
    monkeypatch.setenv(nome, valor)
    with pytest.raises(ValueError, match=nome):
        ler_configuracao(ficheiro_env=None)


def test_env_nao_substitui_o_ambiente(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("PORTA=8123\nHOST=0.0.0.0\n", encoding="utf-8")
    monkeypatch.setenv("PORTA", "5001")
    monkeypatch.delenv("HOST", raising=False)
    cfg = ler_configuracao(env)
    assert cfg.porta == 5001 and cfg.host == "0.0.0.0"


# ------------------------------------------------------------ banco e migrações
def test_migracoes_correspondem_as_tabelas(ambiente_de_teste):
    url = "sqlite:///" + (ambiente_de_teste / "migrado.db").as_posix()
    migrar(url)
    migrar(url)  # repetir não faz mal
    motor = criar_motor(url)
    with motor.connect() as ligacao:
        diferencas = compare_metadata(MigrationContext.configure(ligacao), Base.metadata)
    motor.dispose()
    assert diferencas == []


def test_repositorio_guarda_e_le_pela_mesma_ordem(ambiente_de_teste):
    url = "sqlite:///" + (ambiente_de_teste / "repo.db").as_posix()
    migrar(url)
    sessoes = criar_fabrica_sessoes(criar_motor(url))
    resultado = pd.DataFrame([
        dict(id_transacao="A", cliente="C1", data_hora=pd.Timestamp("2026-05-01 10:00"), valor=10.5, cidade="MAPUTO",
             pais="MZ", categoria="MERCEARIA", risco=80.2, anomalia=10.0, nivel="Alto risco",
             motivos=["Valor 8,2 vezes o habitual", "País novo: ZA"], historico_curto=False),
        dict(id_transacao="B", cliente="C2", data_hora=pd.Timestamp("2026-05-01 11:00"), valor=99.0, cidade="BEIRA",
             pais="MZ", categoria="VIAGEM", risco=5.0, anomalia=1.0, nivel="Normal", motivos=[], historico_curto=True),
    ])
    chave = repositorio.guardar_analise(sessoes, resultado, "ficheiro", "random_forest", "x.csv")
    analise = repositorio.obter_analise(sessoes, chave)
    assert (analise.total, analise.alertas, analise.nome_ficheiro, analise.modelo) == (2, 1, "x.csv", "random_forest")
    lido = repositorio.obter_transacoes(sessoes, analise)
    assert list(lido.id_transacao) == ["A", "B"] and lido.motivos[0] == resultado.motivos[0]
    assert lido.data_hora[0] == resultado.data_hora[0] and bool(lido.historico_curto[1]) is True
    assert list(repositorio.obter_transacoes(sessoes, analise, so_alertas=True).id_transacao) == ["A"]
    assert repositorio.obter_analise(sessoes, "nao-existe") is None
    with pytest.raises(ValueError):
        repositorio.guardar_analise(sessoes, resultado, "outra", "random_forest")


# ------------------------------------------------------------ persistência
def test_analise_sobrevive_ao_reinicio(pasta_pequena):
    html = _enviar(criar_cliente(criar_app(pasta_pequena))).get_data(as_text=True)
    chave = re.search(r"/analisar/([\w-]+)\.csv", html).group(1)
    novo = criar_cliente(criar_app(pasta_pequena))  # outra instância, como depois de reiniciar o servidor
    assert novo.get(f"/analisar/{chave}").status_code == 200
    r = novo.get(f"/analisar/{chave}.csv")
    assert r.status_code == 200 and r.data.decode("utf-8-sig").count("\r\n") == 3


def test_analises_recentes_aparecem_na_pagina(cliente):
    _enviar(cliente, nome="lote_de_maio.csv")
    html = cliente.get("/analisar").get_data(as_text=True)
    assert "Análises recentes" in html and "lote_de_maio.csv" in html


def test_formulario_e_exemplos_ficam_gravados(cliente):
    cliente.post("/", data={"exemplo": "normal"})
    sessoes = cliente.application.config["SESSOES"]
    assert [a.origem for a in repositorio.listar_analises(sessoes)] == ["exemplo"]


# ------------------------------------------------------------ saúde e registo
def test_saude_com_tudo_pronto(cliente):
    r = cliente.get("/saude")
    assert r.status_code == 200 and r.json["estado"] == "ok" and r.json["banco"] == "ok"
    assert r.json["modelos"] == "carregados" and r.json["ambiente"] == "teste"


def test_saude_sem_modelos_responde_503_em_json(tmp_path):
    r = criar_app(tmp_path / "vazia").test_client().get("/saude")
    assert r.status_code == 503 and r.json["modelos"] == "em_falta" and r.json["banco"] == "ok"


def test_pedidos_e_analises_ficam_no_ficheiro_de_log(pasta_pequena, ambiente_de_teste, monkeypatch):
    monkeypatch.setenv("NIVEL_LOG", "INFO")
    cliente = criar_cliente(criar_app(pasta_pequena))
    _enviar(cliente, nome="registado.csv")
    texto = (ambiente_de_teste / "logs" / "detetor.log").read_text(encoding="utf-8")
    assert "Modelos carregados" in texto and "POST /analisar 200" in texto and "'registado.csv' analisado" in texto


def test_erro_inesperado_mostra_pagina_amigavel_e_fica_registado(pasta_pequena, ambiente_de_teste, monkeypatch):
    app = criar_app(pasta_pequena)

    @app.get("/falha-de-teste")
    def _falha():
        raise RuntimeError("falha simulada")

    r = criar_cliente(app).get("/falha-de-teste")
    assert r.status_code == 500 and "erro inesperado" in r.get_data(as_text=True)
    assert "falha simulada" in (ambiente_de_teste / "logs" / "detetor.log").read_text(encoding="utf-8")


# ------------------------------------------------------------ gerir.py
def test_criar_env_gera_chave_e_nao_reescreve(tmp_path, monkeypatch):
    import gerir
    exemplo = tmp_path / ".env.exemplo"
    exemplo.write_text("AMBIENTE=producao\nCHAVE_SECRETA=\nPORTA=5000\n", encoding="utf-8")
    monkeypatch.setattr(gerir, "ENV", tmp_path / ".env")
    monkeypatch.setattr(gerir, "ENV_EXEMPLO", exemplo)
    assert gerir.criar_env() is True
    texto = (tmp_path / ".env").read_text(encoding="utf-8")
    assert re.search(r"^CHAVE_SECRETA=[0-9a-f]{64}$", texto, re.M) and "PORTA=5000" in texto
    assert gerir.criar_env() is False and (tmp_path / ".env").read_text(encoding="utf-8") == texto


def test_env_exemplo_tem_todas_as_variaveis():
    from pathlib import Path
    texto = (Path(__file__).parent.parent / ".env.exemplo").read_text(encoding="utf-8")
    for nome in ("AMBIENTE", "CHAVE_SECRETA", "URL_BANCO", "PASTA_MODELOS", "PASTA_LOGS", "NIVEL_LOG",
                 "HOST", "PORTA", "THREADS", "SESSAO_HORAS", "SMTP_SERVIDOR", "SMTP_PORTA",
                 "SMTP_UTILIZADOR", "SMTP_SENHA", "SMTP_REMETENTE", "SMTP_TLS", "EMAIL_ALERTAS", "URL_PUBLICA"):
        assert re.search(rf"^{nome}=", texto, re.M), nome
