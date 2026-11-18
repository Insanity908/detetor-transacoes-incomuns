"""Tarefas de administração do sistema.

Execução, na raiz do projeto:
    python gerir.py preparar            cria o .env (se faltar), atualiza o banco e treina os modelos (se faltarem)
    python gerir.py banco               cria ou atualiza as tabelas do banco de dados
    python gerir.py treinar [--forcar]  treina os modelos (com --forcar, mesmo que já existam)
    python gerir.py verificar           mostra a configuração e se o banco e os modelos estão prontos
"""
from __future__ import annotations

import argparse
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
    args = analisador.parse_args(argv)

    try:
        if args.comando == "preparar":
            print("Ficheiro .env criado." if criar_env() else "Ficheiro .env já existe: mantido.")
            banco()
            treinar()
            return verificar()
        if args.comando == "banco":
            banco()
        elif args.comando == "treinar":
            treinar(args.forcar)
        elif args.comando == "verificar":
            return verificar()
    except ValueError as exc:
        print(f"Erro na configuração (.env): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
