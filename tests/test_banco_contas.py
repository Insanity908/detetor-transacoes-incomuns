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
                                            ("1234.5", 123450), (10, 1000), (1234.5, 123450)])
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
    assert c.codigo == "C0001"
    assert c.conta.saldo == 0 and c.conta.estado == "ativa"
    assert len(c.conta.numero) == 12
    assert _novo(sessoes, numero_documento="987654321").codigo == "C0002"


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
    assert _novo(sessoes).codigo == "C0006"


def test_verificar_saldos_pelo_gerir(pasta_pequena, capsys):
    import gerir
    assert gerir.main(["verificar-saldos"]) == 0
