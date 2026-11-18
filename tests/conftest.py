from pathlib import Path

import pandas as pd
import pytest
from flask.testing import FlaskClient

from detector.artefatos import Artefatos
from detector.avaliacao import dividir_periodos
from detector.gerador import gerar_transacoes
from detector.modelos import treinar
from detector.perfis import calcular_features


CHAVE_TESTE = "chave-de-teste-" + "0" * 40
SENHA_TESTE = "senha-de-teste-123"


class ClienteComCsrf(FlaskClient):
    """Cliente de teste que junta o token CSRF da sessão a cada POST, como o navegador faz com o campo escondido.

    Para testar a proteção em si, envie csrf_token explicitamente (vazio ou errado).
    """

    def post(self, *args, **kwargs):
        dados = kwargs.get("data") or {}
        if "json" not in kwargs and isinstance(dados, dict) and "csrf_token" not in dados:
            with self.session_transaction() as sessao:
                sessao.setdefault("csrf_token", "token-de-teste")
                kwargs["data"] = {**dados, "csrf_token": sessao["csrf_token"]}  # cópia: não muda o dict do teste
        return super().post(*args, **kwargs)


def criar_cliente(app, perfil: str | None = "administrador", nome: str | None = None):
    """Cliente de teste já autenticado com uma conta nova deste perfil (None = sem sessão)."""
    from detector import contas
    app.test_client_class = ClienteComCsrf
    cliente = app.test_client()
    if perfil is not None:
        sessoes = app.config["SESSOES"]
        nome = nome or f"teste.{perfil}"
        u = contas.obter_por_nome(sessoes, nome) or contas.criar(sessoes, nome, f"Teste {perfil}", perfil, SENHA_TESTE)
        with cliente.session_transaction() as sessao:
            from detector.acesso import impressao
            sessao.update(uid=u.id, versao=u.versao_sessao, conta=impressao(u))
        cliente.utilizador = u
    return cliente


@pytest.fixture(autouse=True)
def ambiente_de_teste(tmp_path, monkeypatch):
    """Cada teste usa a sua base de dados e pasta de logs temporárias, nunca as da instalação."""
    monkeypatch.setenv("AMBIENTE", "teste")
    monkeypatch.setenv("CHAVE_SECRETA", CHAVE_TESTE)
    monkeypatch.setenv("URL_BANCO", "sqlite:///" + (tmp_path / "teste.db").as_posix())
    monkeypatch.setenv("PASTA_LOGS", str(tmp_path / "logs"))
    monkeypatch.setenv("PASTA_ENVIOS", str(tmp_path / "envios"))
    monkeypatch.setenv("NIVEL_LOG", "WARNING")
    return tmp_path


@pytest.fixture(scope="session")
def artefatos_pequenos():
    """Modelos treinados com 80 clientes, sem resultados nem gráficos: rápido para testar o serviço e a interface."""
    df = gerar_transacoes(80, 120)
    X = calcular_features(df)
    p = dividir_periodos(df["data_hora"])
    y = df["fraude"].to_numpy()
    detector, _ = treinar(X[p["treino"]], y[p["treino"]], X[p["validacao"]], y[p["validacao"]])
    return Artefatos(detector, df, pd.DataFrame(), {}, Path("."))


@pytest.fixture(scope="session")
def pasta_pequena(artefatos_pequenos, tmp_path_factory):
    from detector.artefatos import gravar
    pasta = tmp_path_factory.mktemp("modelos_pequenos")
    gravar(pasta, artefatos_pequenos, CHAVE_TESTE)
    return pasta


@pytest.fixture()
def cliente(pasta_pequena):
    """Administrador autenticado: tem acesso a todas as páginas."""
    from app import criar_app
    return criar_cliente(criar_app(pasta_pequena))


@pytest.fixture(scope="session")
def pasta_treinada(tmp_path_factory):
    """Treino completo com 120 clientes: tem resultados, gráficos e transações de teste avaliadas."""
    from treinar import executar
    raiz = tmp_path_factory.mktemp("treinada")
    with pytest.MonkeyPatch.context() as mp:  # corre antes da fixture autouse: a chave tem de ser a dos testes
        mp.setenv("CHAVE_SECRETA", CHAVE_TESTE)
        executar(raiz / "modelos", raiz / "data", None, n_clientes=120)
    return raiz / "modelos"


@pytest.fixture()
def cliente_treinado(pasta_treinada):
    from app import criar_app
    return criar_cliente(criar_app(pasta_treinada))
