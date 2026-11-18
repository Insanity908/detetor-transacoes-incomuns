"""Tarefas de administração do sistema.

Execução, na raiz do projeto:
    python gerir.py preparar            cria o .env (se faltar), atualiza o banco e treina os modelos (se faltarem)
    python gerir.py banco               cria ou atualiza as tabelas do banco de dados
    python gerir.py treinar [--forcar]  treina os modelos (com --forcar, mesmo que já existam)
    python gerir.py verificar           mostra a configuração e se o banco e os modelos estão prontos
    python gerir.py criar-admin         cria uma conta de administrador (pede o nome e a senha)
    python gerir.py desbloquear NOME    reativa uma conta e limpa o bloqueio por tentativas falhadas
    python gerir.py verificar-saldos    confere os saldos do banco com os movimentos (código 1 se houver diferenças)
    python gerir.py criar-chave NOME    cria uma chave da API (por exemplo, para o simulador) e mostra-a uma vez
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ENV = RAIZ / ".env"
ENV_EXEMPLO = RAIZ / ".env.exemplo"
MARCA_CHAVE = "CHAVE_SECRETA="


def criar_env() -> bool:
    """Cria o .env a partir do .env.exemplo, com uma chave secreta nova. Não toca num .env existente."""
    if ENV.exists():
        return False
    linhas = []
    for linha in ENV_EXEMPLO.read_text(encoding="utf-8").splitlines():
        linhas.append(MARCA_CHAVE + secrets.token_hex(32) if linha.startswith(MARCA_CHAVE) else linha)
    ENV.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return True


def banco() -> None:
    from detector.banco import migrar
    from detector.configuracao import ler_configuracao
    cfg = ler_configuracao()
    migrar(cfg.url_banco)
    print("Base de dados pronta.")


def treinar(forcar: bool = False) -> None:
    from detector.artefatos import carregar
    from detector.configuracao import ler_configuracao
    cfg = ler_configuracao()
    if not forcar and carregar(cfg.pasta_modelos) is not None:
        print(f"Os modelos já existem em {cfg.pasta_modelos} (use --forcar para treinar de novo).")
        return
    import treinar as treino
    print("A gerar os dados e a treinar os modelos (1 a 2 minutos)...", flush=True)
    res = treino.executar(pasta_modelos=cfg.pasta_modelos, pasta_dados=RAIZ / "data", relatorio=None)
    teste = res["modelos"][res["escolhido"]]["teste"]
    print(f"Modelo escolhido: {treino.NOMES[res['escolhido']]} — F1 no teste {treino._num(teste['f1'])}.")


def _sessoes():
    from detector.banco import criar_fabrica_sessoes, criar_motor, migrar
    from detector.configuracao import ler_configuracao
    url = ler_configuracao().url_banco
    migrar(url)
    return criar_fabrica_sessoes(criar_motor(url))


def criar_sinteticos(n: int | None = None) -> None:
    """Cria os clientes do banco que correspondem aos do treino (idempotente)."""
    from detector import clientes, simulacao
    from detector.gerador import N_CLIENTES_TREINO
    n = N_CLIENTES_TREINO if n is None else n
    criados = clientes.criar_sinteticos(_sessoes(), simulacao.clientes_do_treino(n))
    print(f"Clientes de demonstração: {criados} criados." if criados else "Clientes de demonstração já existem.")


def verificar_saldos() -> int:
    from detector import contas_bancarias
    linhas = contas_bancarias.verificar_saldos(_sessoes())
    for linha in linhas:
        print(linha)
    if not linhas:
        print("Saldos coerentes com os movimentos.")
    return 1 if linhas else 0


def criar_admin(nome_utilizador: str, nome: str, senha: str) -> None:
    from detector import auditoria, contas
    sessoes = _sessoes()
    novo = contas.criar(sessoes, nome_utilizador, nome, "administrador", senha)
    auditoria.registar(sessoes, "utilizador_criado", nome_utilizador="gerir.py", alvo=novo.nome_utilizador,
                       detalhe="perfil administrador (linha de comandos)")
    print(f"Administrador '{novo.nome_utilizador}' criado. Já pode entrar no sistema.")


def criar_admin_interativo() -> None:
    print("Criar a conta de administrador.")
    nome_utilizador = input("Nome de utilizador [admin]: ").strip() or "admin"
    nome = input("Nome completo [Administrador]: ").strip() or "Administrador"
    while True:
        senha = getpass.getpass("Senha (não aparece ao escrever): ")
        if senha != getpass.getpass("Repita a senha: "):
            print("As senhas não são iguais. Tente de novo.")
            continue
        try:
            criar_admin(nome_utilizador, nome, senha)
            return
        except ValueError as exc:
            print(exc)
            if "Já existe" in str(exc):
                return


def desbloquear(nome_utilizador: str) -> int:
    from detector import contas
    sessoes = _sessoes()
    u = contas.obter_por_nome(sessoes, nome_utilizador)
    if u is None:
        print(f"Não existe o utilizador '{nome_utilizador}'.", file=sys.stderr)
        return 1
    with sessoes.begin() as sessao:
        alvo = sessao.get(type(u), u.id)
        alvo.ativo, alvo.tentativas_falhadas, alvo.bloqueado_ate = True, 0, None
    print(f"Conta '{u.nome_utilizador}' ativa e desbloqueada.")
    return 0


def criar_chave(nome: str, limite: int) -> str:
    from detector import auditoria, chaves_api
    sessoes = _sessoes()
    chave, registo = chaves_api.criar(sessoes, nome, "gerir.py", limite)
    auditoria.registar(sessoes, "chave_criada", nome_utilizador="gerir.py", alvo=registo.prefixo,
                       detalhe=f"{registo.nome}; {registo.limite_por_minuto}/min (linha de comandos)")
    print(f"Chave '{registo.nome}' criada. Guarde-a: não volta a ser mostrada.\n\n    {chave}\n")
    print(f"Para o simulador:  python simulador.py --chave {chave}")
    return chave


def verificar() -> int:
    from detector.artefatos import carregar
    from detector.banco import criar_motor, descrever, verificar_ligacao
    from detector.configuracao import ler_configuracao
    cfg = ler_configuracao()
    motor = criar_motor(cfg.url_banco)
    banco_ok = verificar_ligacao(motor)
    modelos_ok = carregar(cfg.pasta_modelos) is not None
    print(f"Ambiente:  {cfg.ambiente}")
    print(f"Endereço:  http://{cfg.host}:{cfg.porta}")
    print(f"Banco:     {descrever(motor)} -> {'ok' if banco_ok else 'SEM LIGAÇÃO'}")
    print(f"Modelos:   {cfg.pasta_modelos} -> {'ok' if modelos_ok else 'EM FALTA (python gerir.py treinar)'}")
    print(f"Logs:      {cfg.pasta_logs}")
    if banco_ok:
        from detector import contas
        from detector.banco import criar_fabrica_sessoes
        try:
            n = contas.contar(criar_fabrica_sessoes(motor))
            print(f"Contas:    {n}" + ("" if n else " -> NENHUMA (python gerir.py criar-admin)"))
        except Exception:  # banco ainda sem as tabelas da Fase 2
            print("Contas:    tabelas em falta -> python gerir.py banco")
    motor.dispose()
    return 0 if banco_ok and modelos_ok else 1


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    analisador = argparse.ArgumentParser(description="Administração do Detetor de Fraudes Financeiras")
    comandos = analisador.add_subparsers(dest="comando", required=True)
    comandos.add_parser("preparar", help="cria o .env, atualiza o banco e treina os modelos se faltarem")
    comandos.add_parser("banco", help="cria ou atualiza as tabelas")
    t = comandos.add_parser("treinar", help="treina os modelos")
    t.add_argument("--forcar", action="store_true", help="treina mesmo que os modelos já existam")
    comandos.add_parser("verificar", help="mostra a configuração e o estado")
    comandos.add_parser("criar-admin", help="cria uma conta de administrador")
    d = comandos.add_parser("desbloquear", help="reativa e desbloqueia uma conta")
    d.add_argument("nome_utilizador")
    comandos.add_parser("verificar-saldos", help="confere os saldos do banco com os movimentos")
    k = comandos.add_parser("criar-chave", help="cria uma chave da API")
    k.add_argument("nome", help="quem a vai usar, por exemplo Simulador")
    k.add_argument("--limite", type=int, default=600, help="pedidos por minuto (600 por omissão)")
    args = analisador.parse_args(argv)

    try:
        if args.comando == "preparar":
            print("Ficheiro .env criado." if criar_env() else "Ficheiro .env já existe: mantido.")
            banco()
            treinar()
            criar_sinteticos()
            from detector import contas
            if contas.contar(_sessoes()) == 0:
                try:
                    if not sys.stdin.isatty():
                        raise EOFError
                    criar_admin_interativo()
                except (EOFError, KeyboardInterrupt):  # sem teclado (ou Ctrl+C): a instalação continua
                    print("\nNenhuma conta criada. Crie o administrador com: .venv\\Scripts\\python gerir.py criar-admin")
            return verificar()
        if args.comando == "banco":
            banco()
        elif args.comando == "treinar":
            treinar(args.forcar)
        elif args.comando == "verificar":
            return verificar()
        elif args.comando == "criar-admin":
            criar_admin_interativo()
        elif args.comando == "desbloquear":
            return desbloquear(args.nome_utilizador)
        elif args.comando == "verificar-saldos":
            return verificar_saldos()
        elif args.comando == "criar-chave":
            criar_chave(args.nome, args.limite)
    except ValueError as exc:
        print(f"Erro na configuração (.env): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
