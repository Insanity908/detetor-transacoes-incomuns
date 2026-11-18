"""As migrações nunca apagam dados: no SQLite, alterar uma tabela é recriá-la, e com as chaves estrangeiras
ligadas isso dispararia os ON DELETE CASCADE / SET NULL das tabelas que apontam para ela."""
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import text

from detector.banco import configuracao_alembic, criar_motor, migrar

LINHA_TRANSACAO = ("INSERT INTO transacoes_avaliadas (id, analise_id, ordem, id_transacao, cliente, data_hora, valor, "
                   "cidade, pais, categoria, risco, anomalia, nivel, motivos, historico_curto) VALUES "
                   "(:id, 1, :id, 'T', 'C1', '2026-05-01 09:00:00', 10, 'X', 'MZ', 'M', 90, 1, :nivel, '[]', 0)")


def _contar(motor, tabela, condicao="1=1"):
    with motor.connect() as c:
        return c.execute(text(f"SELECT count(*) FROM {tabela} WHERE {condicao}")).scalar()


def test_cada_migracao_mantem_os_dados(ambiente_de_teste):
    url = "sqlite:///" + (ambiente_de_teste / "historico.db").as_posix()
    cfg = configuracao_alembic(url)
    revisoes = [r.revision for r in reversed(list(ScriptDirectory.from_config(cfg).walk_revisions()))]
    command.upgrade(cfg, revisoes[0])
    motor = criar_motor(url)
    with motor.begin() as c:
        c.execute(text("INSERT INTO analises (id, chave, origem, criada_em, total, alertas, modelo) "
                       "VALUES (1, 'k', 'ficheiro', '2026-05-01 10:00:00', 2, 1, 'rf')"))
        c.execute(text(LINHA_TRANSACAO), {"id": 1, "nivel": "Alto risco"})
        c.execute(text(LINHA_TRANSACAO), {"id": 2, "nivel": "Normal"})
    for revisao in revisoes[1:]:
        command.upgrade(cfg, revisao)
        assert _contar(motor, "transacoes_avaliadas") == 2, revisao
        if revisao == "0002":
            with motor.begin() as c:
                c.execute(text("INSERT INTO utilizadores (id, nome_utilizador, nome, perfil, senha_hash, ativo, "
                               "trocar_senha, tentativas_falhadas, versao_sessao, criado_em) VALUES "
                               "(1, 'ana', 'Ana', 'analista', 'x', 1, 0, 0, 1, '2026-05-01 10:00:00')"))
                c.execute(text("INSERT INTO auditoria (momento, utilizador_id, nome_utilizador, acao) "
                               "VALUES ('2026-05-01 10:00:00', 1, 'ana', 'entrada')"))
                c.execute(text("UPDATE analises SET utilizador_id = 1"))
        if revisoes.index(revisao) >= revisoes.index("0002"):
            assert _contar(motor, "auditoria", "utilizador_id = 1") == 1, revisao
            assert _contar(motor, "analises", "utilizador_id = 1") == 1, revisao
        if revisoes.index(revisao) >= revisoes.index("0003"):
            assert _contar(motor, "alertas") == 1, revisao
    motor.dispose()
    migrar(url)  # já está na última versão: não muda nada


def test_aplicacao_continua_com_as_chaves_estrangeiras_ligadas(ambiente_de_teste):
    motor = criar_motor("sqlite:///" + (ambiente_de_teste / "fk.db").as_posix())
    with motor.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
    motor.dispose()


def test_migracao_0006_cria_e_remove_o_banco(ambiente_de_teste):
    url = "sqlite:///" + (ambiente_de_teste / "banco.db").as_posix()
    cfg = configuracao_alembic(url)
    command.upgrade(cfg, "0005")
    motor = criar_motor(url)
    with motor.begin() as c:
        c.execute(text("INSERT INTO analises (id, chave, origem, criada_em, total, alertas, modelo) "
                       "VALUES (1, 'k', 'ficheiro', '2026-05-01 10:00:00', 1, 1, 'rf')"))
        c.execute(text(LINHA_TRANSACAO), {"id": 1, "nivel": "Alto risco"})
    tabelas_novas = {"clientes", "contas_bancarias", "movimentos", "acessos_cliente"}

    def tabelas():
        with motor.connect() as c:
            return {linha[0] for linha in c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}

    assert not tabelas_novas & tabelas()
    command.upgrade(cfg, "0006")
    assert tabelas_novas <= tabelas()
    with motor.begin() as c:
        c.execute(text("INSERT INTO clientes (id, codigo, nome, data_nascimento, tipo_documento, numero_documento, "
                       "telefone, cidade, morada, sintetico, criado_em, criado_por) VALUES (1, 'C0001', 'Ana', "
                       "'1990-01-01', 'BI', '123', '840000000', 'Maputo', 'Rua 1', 0, '2026-05-01 10:00:00', 'x')"))
        c.execute(text("INSERT INTO contas_bancarias (id, numero, cliente_id, saldo, saldo_retido, estado, aberta_em) "
                       "VALUES (1, '0001', 1, 1000, 0, 'ativa', '2026-05-01 10:00:00')"))
        c.execute(text("INSERT INTO movimentos (conta_id, tipo, valor, estado, canal, momento, registado_em, "
                       "criado_por) VALUES (1, 'deposito', 500, 'autorizado', 'balcao', '2026-05-01 10:00:00', "
                       "'2026-05-01 10:00:00', 'x')"))
    with motor.begin() as c:
        for sql in ("INSERT INTO movimentos (conta_id, tipo, valor, estado, canal, momento, registado_em, criado_por)"
                    " VALUES (1, 'deposito', 0, 'autorizado', 'balcao', '2026-05-01', '2026-05-01', 'x')",
                    "UPDATE contas_bancarias SET saldo_retido = -1"):
            try:
                with c.begin_nested():
                    c.execute(text(sql))
            except Exception:
                continue
            raise AssertionError("a restrição CHECK devia recusar: " + sql)
    command.downgrade(cfg, "0005")
    assert not tabelas_novas & tabelas()
    assert _contar(motor, "transacoes_avaliadas") == 1
    assert _contar(motor, "analises") == 1
    command.upgrade(cfg, "0006")
    assert tabelas_novas <= tabelas()
    motor.dispose()
