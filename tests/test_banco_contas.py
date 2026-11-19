"""Fase 7: dinheiro em cêntimos, fichas de clientes, contas e depósitos."""
import pytest

from app import criar_app
from detector import clientes, contas_bancarias, dinheiro


@pytest.fixture()
def sessoes(pasta_pequena):
    return criar_app(pasta_pequena).config["SESSOES"]


def _form(**extra):
    base = dict(nome="Ana Maria Sitoe", data_nascimento="1990-05-10", tipo_documento="NUIT",
                numero_documento="123456789", telefone="841234567", email="ana@exemplo.co.mz",
                cidade="MAPUTO", morada="Av. 24 de Julho 100")
    base.update(extra)
    return base


def _novo(sessoes, **extra):
    return clientes.criar(sessoes, clientes.validar(_form(**extra)), "teste")


@pytest.mark.parametrize("texto,esperado", [("1234,50", 123450), ("1 234,50", 123450), ("1.234,50", 123450),
                                            ("1234.5", 123450), (10, 1000), (1234.5, 123450),
                                            ("1.500", 150000), ("10.000", 1000000), ("1234.50", 123450),
                                            ("999.999", 99999900)])
def test_para_centavos_aceita(texto, esperado):
    assert dinheiro.para_centavos(texto) == esperado


@pytest.mark.parametrize("texto", ["0", "-10", "abc", "10,555", "", "100000001", "1.2.3", "١٢٣", "1 23 456"])
def test_para_centavos_recusa(texto):
    with pytest.raises(ValueError):
        dinheiro.para_centavos(texto)


def test_para_centavos_milhares_validos():
    assert dinheiro.para_centavos("1.234.567") == 123456700
    assert dinheiro.para_centavos("1 234 567,5") == 123456750


def test_pesquisar_ignora_maiusculas_acentos_e_escapa_curingas(sessoes):
    c = _novo(sessoes, nome="João Macamo")
    for texto in ("joao", "JOÃO", "macamo"):
        assert [x.id for x in clientes.pesquisar(sessoes, texto)] == [c.id]
    assert clientes.pesquisar(sessoes, "%") == []
    assert clientes.pesquisar(sessoes, "_") == []


def test_criar_cliente_abre_conta_com_saldo_zero(sessoes):
    c = _novo(sessoes)
    assert c.codigo == "C0301"  # os códigos até C0300 são dos clientes do treino
    assert c.conta.saldo == 0 and c.conta.estado == "ativa"
    assert len(c.conta.numero) == 12
    assert _novo(sessoes, numero_documento="987654321").codigo == "C0302"


def test_documento_repetido_e_recusado(sessoes):
    _novo(sessoes)
    with pytest.raises(ValueError, match="Já existe"):
        _novo(sessoes)


def test_validar_recusa_menor_de_idade_e_nuit_invalido():
    with pytest.raises(ValueError, match="18"):
        clientes.validar(_form(data_nascimento="2020-01-01"))
    with pytest.raises(ValueError, match="NUIT"):
        clientes.validar(_form(numero_documento="123"))


def test_pesquisar_e_editar(sessoes):
    c = _novo(sessoes)
    assert [x.id for x in clientes.pesquisar(sessoes, "sitoe")] == [c.id]
    mudancas = clientes.editar(sessoes, c.id, "821111111", "", "Rua Nova 1", "BEIRA", "teste")
    assert len(mudancas) == 4
    assert clientes.obter(sessoes, c.id).cidade == "BEIRA"


def test_deposito_soma_ao_saldo_e_fica_no_extrato(sessoes):
    c = _novo(sessoes)
    contas_bancarias.depositar(sessoes, c.conta.id, 50000, "balcao", "teste")
    assert clientes.obter(sessoes, c.id).conta.saldo == 50000
    assert contas_bancarias.extrato(sessoes, c.conta.id)[0].saldo_apos == 50000
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_deposito_em_conta_bloqueada_e_recusado(sessoes):
    c = _novo(sessoes)
    contas_bancarias.bloquear(sessoes, c.conta.id, True, "teste")
    with pytest.raises(ValueError, match="bloqueada"):
        contas_bancarias.depositar(sessoes, c.conta.id, 100, "balcao", "teste")
    with pytest.raises(ValueError, match="Canal"):
        contas_bancarias.depositar(sessoes, c.conta.id, 100, "xyz", "teste")


def _perfis(n):
    from detector import simulacao
    return simulacao.clientes_do_treino(n)


def test_sinteticos_sao_idempotentes(sessoes):
    perfis = _perfis(5)
    assert clientes.criar_sinteticos(sessoes, perfis) == 5
    assert clientes.criar_sinteticos(sessoes, perfis) == 0
    assert [c.codigo for c in clientes.pesquisar(sessoes, "")] == [f"C000{i}" for i in range(1, 6)]
    primeiro = clientes.obter_por_codigo(sessoes, "C0001")
    assert primeiro.sintetico and primeiro.conta.saldo > 0
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_sinteticos_passam_a_validacao_e_tem_documentos_unicos(sessoes):
    assert clientes.criar_sinteticos(sessoes, _perfis(300)) == 300
    todos = clientes.pesquisar(sessoes, "", limite=1000)
    assert len({(c.tipo_documento, c.numero_documento) for c in todos}) == 300
    for c in todos:
        clientes.validar(dict(nome=c.nome, data_nascimento=c.data_nascimento.isoformat(),
                              tipo_documento=c.tipo_documento, numero_documento=c.numero_documento,
                              telefone=c.telefone, email=c.email or "", cidade=c.cidade, morada=c.morada))


def test_cliente_novo_continua_a_numeracao(sessoes):
    clientes.criar_sinteticos(sessoes, _perfis(5))
    assert _novo(sessoes).codigo == "C0301"


def test_verificar_saldos_pelo_gerir(pasta_pequena, capsys):
    import gerir
    assert gerir.main(["verificar-saldos"]) == 0


def test_sinteticos_reparam_deposito_em_falta(sessoes, monkeypatch):
    perfis = _perfis(3)
    original = contas_bancarias.depositar
    def falha(*a, **k):
        raise RuntimeError("falha simulada")
    monkeypatch.setattr(contas_bancarias, "depositar", falha)
    with pytest.raises(RuntimeError):
        clientes.criar_sinteticos(sessoes, perfis)
    assert clientes.obter_por_codigo(sessoes, "C0001").conta.saldo == 0
    monkeypatch.setattr(contas_bancarias, "depositar", original)
    assert clientes.criar_sinteticos(sessoes, perfis) == 2  # C0001 reparado (não conta como criado), C0002 e C0003 novos
    assert all(clientes.obter_por_codigo(sessoes, f"C000{i}").conta.saldo > 0 for i in (1, 2, 3))
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_sinteticos_sorteiam_outro_documento_se_repetido(sessoes, monkeypatch):
    _novo(sessoes, numero_documento="111111111")  # ficha real C0001 com o documento que o sintético vai tentar
    respostas = iter([("NUIT", "111111111"), ("NUIT", "222222222")])
    monkeypatch.setattr(clientes, "_documento", lambda rng, usados: next(respostas))
    perfil = dict(_perfis(2)[1], cliente="C0050")
    assert clientes.criar_sinteticos(sessoes, [perfil]) == 1
    novo = clientes.obter_por_codigo(sessoes, "C0050")
    assert novo.numero_documento == "222222222" and novo.conta.saldo > 0


# --- Tarefa 8: débito, reserva e liquidação ---------------------------------------------------------------

def _conta_com(sessoes, saldo, **extra):
    c = _novo(sessoes, **extra)
    if saldo:
        contas_bancarias.depositar(sessoes, c.conta.id, saldo, "balcao", "teste")
    return c.conta.id


def _debitar(sessoes, conta_id, valor, estado="autorizado", tipo="levantamento", **extra):
    from datetime import datetime
    from detector.banco import ContaBancaria
    with sessoes.begin() as s:
        m = contas_bancarias.debitar(s, s.get(ContaBancaria, conta_id), tipo, valor, "balcao", "teste",
                                     datetime(2026, 1, 1, 10, 0), estado, **extra)
        s.expunge(m)
        return m


def _conta(sessoes, conta_id):
    from detector.banco import ContaBancaria
    with sessoes() as s:
        c = s.get(ContaBancaria, conta_id)
        return c.saldo, c.saldo_retido


def test_debito_autorizado_e_segundo_recusado_por_saldo(sessoes):
    cid = _conta_com(sessoes, 10000)
    m = _debitar(sessoes, cid, 6000)
    assert m.estado == "autorizado" and m.saldo_apos == 4000
    assert _conta(sessoes, cid) == (4000, 0)
    m2 = _debitar(sessoes, cid, 6000)
    assert m2.estado == "recusado" and m2.motivo == "Saldo insuficiente."
    assert _conta(sessoes, cid) == (4000, 0)
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_reserva_reduz_o_disponivel(sessoes):
    cid = _conta_com(sessoes, 10000)
    assert _debitar(sessoes, cid, 3000, "retido").estado == "retido"
    assert _conta(sessoes, cid) == (10000, 3000)
    assert _debitar(sessoes, cid, 8000).estado == "recusado"
    assert _debitar(sessoes, cid, 7000, "retido").estado == "retido"
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_liquidar_executando_e_nao_executando(sessoes):
    cid = _conta_com(sessoes, 10000)
    m = _debitar(sessoes, cid, 3000, "retido", tipo="compra")
    with sessoes.begin() as s:
        r = contas_bancarias.liquidar(s, m.id, True, "analista")
        assert r.estado == "autorizado" and r.saldo_apos == 7000
    assert _conta(sessoes, cid) == (7000, 0)
    m = _debitar(sessoes, cid, 2000, "retido", tipo="compra")
    with sessoes.begin() as s:
        r = contas_bancarias.liquidar(s, m.id, False, "analista")
        assert r.estado == "cancelado" and r.motivo == "Fraude confirmada."
    assert _conta(sessoes, cid) == (7000, 0)
    with pytest.raises(ValueError):
        with sessoes.begin() as s:
            contas_bancarias.liquidar(s, m.id, True, "analista")
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_liquidar_levantamento_retido_nunca_executa(sessoes):
    cid = _conta_com(sessoes, 10000)
    m = _debitar(sessoes, cid, 3000, "retido")  # levantamento
    with sessoes.begin() as s:
        r = contas_bancarias.liquidar(s, m.id, True, "analista")
        assert (r.estado, r.motivo) == ("cancelado", contas_bancarias.MOTIVO_LEVANTAMENTO_NAO_ENTREGUE)
    assert _conta(sessoes, cid) == (10000, 0)
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_transferencia_autorizada_credita_destino(sessoes):
    a = _conta_com(sessoes, 10000)
    b = _conta_com(sessoes, 0, numero_documento="987654321")
    _debitar(sessoes, a, 4000, tipo="transferencia_enviada", contraparte_id=b)
    assert _conta(sessoes, a) == (6000, 0) and _conta(sessoes, b) == (4000, 0)
    recebido = contas_bancarias.extrato(sessoes, b)[0]
    assert recebido.tipo == "transferencia_recebida" and recebido.contraparte_id == a
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_transferencia_retida_so_credita_ao_liquidar(sessoes):
    a = _conta_com(sessoes, 10000)
    b = _conta_com(sessoes, 0, numero_documento="987654321")
    m = _debitar(sessoes, a, 4000, "retido", tipo="transferencia_enviada", contraparte_id=b)
    assert _conta(sessoes, b) == (0, 0)
    with sessoes.begin() as s:
        contas_bancarias.liquidar(s, m.id, True, "analista")
    assert _conta(sessoes, a) == (6000, 0) and _conta(sessoes, b) == (4000, 0)
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_liquidar_com_destino_bloqueado_cancela_sem_debitar(sessoes):
    a = _conta_com(sessoes, 10000)
    b = _conta_com(sessoes, 0, numero_documento="987654321")
    m = _debitar(sessoes, a, 4000, "retido", tipo="transferencia_enviada", contraparte_id=b)
    contas_bancarias.bloquear(sessoes, b, True, "teste")
    with sessoes.begin() as s:
        r = contas_bancarias.liquidar(s, m.id, True, "analista")
        assert r.estado == "cancelado" and r.motivo == "A conta de destino foi bloqueada entretanto."
    assert _conta(sessoes, a) == (10000, 0) and _conta(sessoes, b) == (0, 0)
    assert contas_bancarias.verificar_saldos(sessoes) == []


def test_recusado_nao_mexe_no_saldo_e_validacoes(sessoes):
    cid = _conta_com(sessoes, 1000)
    m = _debitar(sessoes, cid, 500, "recusado", motivo="Risco alto.")
    assert m.estado == "recusado" and m.motivo == "Risco alto."
    assert _conta(sessoes, cid) == (1000, 0)
    for args in (dict(valor=0), dict(tipo="xyz"), dict(estado="cancelado")):
        with pytest.raises(ValueError):
            _debitar(sessoes, cid, **{"valor": 100, **args})


def test_movimento_da_transacao(sessoes):
    _conta_com(sessoes, 1000)
    with sessoes.begin() as s:
        assert contas_bancarias.movimento_da_transacao(s, 999) is None


def test_sintetico_nao_reutiliza_codigo_de_cliente_real(sessoes):
    clientes.criar(sessoes, clientes.validar(dict(
        nome="Ana Sitoe", data_nascimento="1990-05-10", tipo_documento="NUIT", numero_documento="123456789",
        telefone="841234567", email="", cidade="MAPUTO", morada="Rua 1")), "teste", codigo="C0002")
    with pytest.raises(ValueError, match="cliente real"):
        clientes.criar_sinteticos(sessoes, _perfis(5))


def test_preparar_cria_os_clientes_de_treino(pasta_pequena, monkeypatch, capsys):
    import gerir
    chamadas = []
    monkeypatch.setattr(gerir, "treinar", lambda *a, **k: None)
    monkeypatch.setattr(gerir, "criar_sinteticos", lambda *a, **k: chamadas.append(1))
    monkeypatch.setattr(gerir, "verificar", lambda: 0)
    monkeypatch.setattr(gerir, "criar_env", lambda: False)
    monkeypatch.setattr(gerir, "banco", lambda: None)
    monkeypatch.setattr(gerir, "_sessoes", lambda: __import__("app").criar_app(pasta_pequena).config["SESSOES"])
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False})())
    assert gerir.main(["preparar"]) == 0
    assert chamadas == [1]
