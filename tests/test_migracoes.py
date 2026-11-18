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
