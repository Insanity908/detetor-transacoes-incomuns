"""Tarefa 9: autorizar() é a única porta entre o banco e o detetor."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import criar_app
from detector import autorizacao, clientes, contas_bancarias, repositorio
from detector.autorizacao import PedidoDebito, autorizar
from detector.banco import Alerta, ContaBancaria, Movimento, TransacaoAvaliada
from detector.limiares import Cortes

AGORA = datetime(2026, 11, 18, 10, 0, 0)


@pytest.fixture()
def sessoes(pasta_pequena):
    return criar_app(pasta_pequena).config["SESSOES"]


@pytest.fixture()
def artefatos(artefatos_pequenos):
    return artefatos_pequenos


def _conta(sessoes, n: int, saldo: int = 0) -> int:
    form = dict(nome=f"Cliente {n}", data_nascimento="1990-05-10", tipo_documento="NUIT",
                numero_documento=f"10000000{n}", telefone="841234567", email=None, cidade="MAPUTO", morada="Rua 1")
    c = clientes.criar(sessoes, clientes.validar(form), "teste")
    if saldo:
        contas_bancarias.depositar(sessoes, c.conta.id, saldo, "balcao", "teste")
    return c.conta.id


def _pedido(conta_id, valor=1000, tipo="compra", minutos=0, **extra):
    base = dict(conta_id=conta_id, tipo=tipo, valor=valor, momento=AGORA + timedelta(minutes=minutos),
                cidade="MAPUTO", pais="MZ", categoria="SUPERMERCADO", canal="balcao", por="teste")
    return PedidoDebito(**(base | extra))


def _forcar(monkeypatch, *niveis_riscos):
    """avaliar_transacoes falso: devolve o nível e o risco pretendidos, pela ordem das linhas."""
    def falso(artefatos, df, historico_extra=None, cortes=None):
        r = df.copy().reset_index(drop=True)
        r["risco"] = [x[1] for x in niveis_riscos[:len(r)]]
        r["anomalia"] = 10.0
        r["nivel"] = [x[0] for x in niveis_riscos[:len(r)]]
        r["motivos"] = [["teste"]] * len(r)
        r["historico_curto"] = False
        return r
    monkeypatch.setattr(autorizacao, "avaliar_transacoes", falso)


def _conta_db(sessoes, conta_id):
    with sessoes() as s:
        c = s.get(ContaBancaria, conta_id)
        return c.saldo, c.saldo_retido


def _contar(sessoes, modelo):
    with sessoes() as s:
        return s.scalar(select(func.count()).select_from(modelo))


def _alerta(sessoes, alerta_id):
    with sessoes() as s:
        return s.get(Alerta, alerta_id)


def test_normal_autoriza_e_debita(sessoes, artefatos, monkeypatch):
    c = _conta(sessoes, 1, 100000)
    _forcar(monkeypatch, ("Normal", 5.0))
    [d] = autorizar(sessoes, artefatos, [_pedido(c, 25000)], Cortes(), "banco")
    assert (d.estado, d.nivel, d.risco, d.alerta_id) == ("autorizado", "Normal", 5.0, None)
    assert _conta_db(sessoes, c) == (75000, 0)
    with sessoes() as s:
        m = s.get(Movimento, d.movimento_id)
        assert m.transacao_id is not None and m.estado == "autorizado" and m.momento == AGORA
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_caminho_real_do_modelo(sessoes, artefatos):
    c = _conta(sessoes, 1, 100000)
    [d] = autorizar(sessoes, artefatos, [_pedido(c, 1000)], Cortes(), "banco")
    assert d.nivel is not None and d.risco is not None
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_alto_risco_retem_e_cria_alerta_novo(sessoes, artefatos, monkeypatch):
    c = _conta(sessoes, 1, 100000)
    _forcar(monkeypatch, ("Alto risco", 70.0))
    [d] = autorizar(sessoes, artefatos, [_pedido(c, 30000)], Cortes(), "banco")
    assert (d.estado, d.nivel) == ("retido", "Alto risco")
    assert _conta_db(sessoes, c) == (100000, 30000)
    assert _alerta(sessoes, d.alerta_id).estado == "novo"
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_atencao_autoriza_com_alerta(sessoes, artefatos, monkeypatch):
    c = _conta(sessoes, 1, 100000)
    _forcar(monkeypatch, ("Atenção", 30.0))
    [d] = autorizar(sessoes, artefatos, [_pedido(c, 30000)], Cortes(), "banco")
    assert d.estado == "autorizado" and _conta_db(sessoes, c) == (70000, 0)
    assert _alerta(sessoes, d.alerta_id).estado == "novo"
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_risco_acima_da_recusa_recusa_com_alerta_fechado(sessoes, artefatos, monkeypatch):
    c = _conta(sessoes, 1, 100000)
    _forcar(monkeypatch, ("Alto risco", 97.0))
    [d] = autorizar(sessoes, artefatos, [_pedido(c, 30000)], Cortes(), "banco")
    assert d.estado == "recusado" and d.motivo == "Risco muito alto: recusado automaticamente."
    assert _conta_db(sessoes, c) == (100000, 0)
    assert _alerta(sessoes, d.alerta_id).estado == "recusado_auto"
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_sem_saldo_recusa_sem_avaliacao_gravada(sessoes, artefatos, monkeypatch):
    c = _conta(sessoes, 1, 1000)
    _forcar(monkeypatch, ("Normal", 5.0))
    antes = _contar(sessoes, TransacaoAvaliada)
    [d] = autorizar(sessoes, artefatos, [_pedido(c, 5000)], Cortes(), "banco")
    assert (d.estado, d.motivo, d.nivel, d.risco, d.alerta_id) == ("recusado", "Saldo insuficiente.", None, None, None)
    assert _contar(sessoes, TransacaoAvaliada) == antes
    assert _conta_db(sessoes, c) == (1000, 0)
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_dois_debitos_que_excedem_o_saldo(sessoes, artefatos, monkeypatch):
    c = _conta(sessoes, 1, 10000)
    _forcar(monkeypatch, ("Normal", 5.0), ("Normal", 5.0))
    d1, d2 = autorizar(sessoes, artefatos, [_pedido(c, 6000), _pedido(c, 6000, minutos=1)], Cortes(), "banco")
    assert (d1.estado, d2.estado) == ("autorizado", "recusado")
    assert d2.motivo == "Saldo insuficiente."
    assert _conta_db(sessoes, c) == (4000, 0)
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_ordem_por_momento_nao_pela_lista(sessoes, artefatos, monkeypatch):
    c = _conta(sessoes, 1, 10000)
    _forcar(monkeypatch, ("Normal", 5.0), ("Normal", 5.0))
    d1, d2 = autorizar(sessoes, artefatos, [_pedido(c, 6000, minutos=5), _pedido(c, 6000)], Cortes(), "banco")
    assert (d1.estado, d2.estado) == ("recusado", "autorizado")
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_transferencia_credita_o_destino(sessoes, artefatos, monkeypatch):
    a, b = _conta(sessoes, 1, 50000), _conta(sessoes, 2)
    _forcar(monkeypatch, ("Normal", 5.0))
    [d] = autorizar(sessoes, artefatos, [_pedido(a, 20000, "transferencia_enviada", contraparte_id=b,
                                                 categoria="TRANSFERENCIA")], Cortes(), "banco")
    assert d.estado == "autorizado"
    assert _conta_db(sessoes, a) == (30000, 0) and _conta_db(sessoes, b) == (20000, 0)
    assert contas_bancarias.verificar_saldos(sessoes) == []


@pytest.mark.parametrize("caso", ["propria", "inexistente", "bloqueada", "origem_bloqueada", "conta_inexistente"])
def test_transferencia_para_a_propria_conta_inexistente_ou_bloqueada(sessoes, artefatos, monkeypatch, caso):
    a, b = _conta(sessoes, 1, 50000), _conta(sessoes, 2, 7000)
    _forcar(monkeypatch, ("Normal", 5.0))
    antes = _contar(sessoes, TransacaoAvaliada)
    conta, destino = a, {"propria": a, "inexistente": 99999}.get(caso, b)
    if caso == "bloqueada":
        contas_bancarias.bloquear(sessoes, b, True, "teste")
    if caso == "origem_bloqueada":
        contas_bancarias.bloquear(sessoes, a, True, "teste")
    if caso == "conta_inexistente":
        conta = 99999
    [d] = autorizar(sessoes, artefatos, [_pedido(conta, 1000, "transferencia_enviada", contraparte_id=destino,
                                                 categoria="TRANSFERENCIA")], Cortes(), "banco")
    assert d.estado == "recusado" and d.motivo and d.nivel is None and d.alerta_id is None
    assert (d.movimento_id == 0) == (caso == "conta_inexistente")
    assert _contar(sessoes, TransacaoAvaliada) == antes
    assert _conta_db(sessoes, a) == (50000, 0) and _conta_db(sessoes, b) == (7000, 0)
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_decisoes_na_ordem_dos_pedidos_com_mistura(sessoes, artefatos, monkeypatch):
    a, b = _conta(sessoes, 1, 50000), _conta(sessoes, 2)
    _forcar(monkeypatch, ("Normal", 5.0), ("Normal", 5.0), ("Alto risco", 60.0))
    ds = autorizar(sessoes, artefatos, [_pedido(a, 1000), _pedido(99999, 1000), _pedido(b, 1000, minutos=2),
                                        _pedido(a, 2000, minutos=1)], Cortes(), "banco")
    assert [d.estado for d in ds] == ["autorizado", "recusado", "recusado", "retido"]
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_historico_inclui_a_origem_banco(sessoes, artefatos, monkeypatch):
    c = _conta(sessoes, 1, 100000)
    _forcar(monkeypatch, ("Normal", 5.0))
    autorizar(sessoes, artefatos, [_pedido(c, 1000)], Cortes(), "banco")
    h = repositorio.historico_api(sessoes, {"C0301"}, AGORA - timedelta(days=1))
    assert len(h) == 1
    assert contas_bancarias.verificar_saldos(sessoes) == []


@pytest.mark.parametrize("alteracao", [dict(valor=0), dict(valor=-5), dict(canal="x"), dict(tipo="deposito")])
def test_pedido_invalido_no_lote_nao_estraga_os_validos(sessoes, artefatos, monkeypatch, alteracao):
    c = _conta(sessoes, 1, 50000)
    _forcar(monkeypatch, ("Normal", 5.0))
    movimentos = _contar(sessoes, Movimento)
    d_mau, d_bom = autorizar(sessoes, artefatos, [_pedido(c, **alteracao), _pedido(c, 1000, minutos=1)],
                             Cortes(), "banco")
    assert (d_mau.movimento_id, d_mau.estado, d_mau.motivo) == (0, "recusado", "Pedido inválido.")
    assert d_bom.estado == "autorizado"
    assert _contar(sessoes, Movimento) == movimentos + 1  # só o válido (o depósito inicial já contava)
    assert _conta_db(sessoes, c) == (49000, 0)
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_corrida_no_saldo_deixa_o_alerta_recusado_auto(sessoes, artefatos, monkeypatch):
    c = _conta(sessoes, 1, 1000)
    _forcar(monkeypatch, ("Alto risco", 60.0))
    monkeypatch.setattr(autorizacao, "_disponivel", lambda s, conta_id: 10**9)  # a pré-verificação deixa passar
    [d] = autorizar(sessoes, artefatos, [_pedido(c, 5000)], Cortes(), "banco")
    assert (d.estado, d.motivo) == ("recusado", "Saldo insuficiente.")
    assert _alerta(sessoes, d.alerta_id).estado == "recusado_auto"
    assert _conta_db(sessoes, c) == (1000, 0)
    assert contas_bancarias.verificar_saldos(sessoes) == []
