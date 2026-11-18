"""Servidor de produção (waitress), usado pelo iniciar.bat.

Execução, na raiz do projeto:
    python servidor.py            (com --abrir, abre o navegador quando o servidor estiver pronto)
O endereço, a porta e o número de threads vêm do .env (HOST, PORTA, THREADS).
"""
from __future__ import annotations

import sys
import threading
import webbrowser

from waitress import serve

from app import criar_app
from detector.configuracao import ler_configuracao


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        configuracao = ler_configuracao()
    except ValueError as exc:
        print(f"Erro na configuração (.env): {exc}", file=sys.stderr)
        return 1
    app = criar_app(configuracao=configuracao)
    endereco = "127.0.0.1" if configuracao.host in ("0.0.0.0", "::") else configuracao.host
    url = f"http://{endereco}:{configuracao.porta}"
    print(f"Detetor de Fraudes a funcionar em {url}  (feche esta janela ou Ctrl+C para parar)", flush=True)
    if "--abrir" in sys.argv[1:]:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    serve(app, host=configuracao.host, port=configuracao.porta, threads=configuracao.threads,
          ident="detetor", max_request_body_size=app.config["MAX_CONTENT_LENGTH"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
