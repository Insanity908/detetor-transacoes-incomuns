"""Envio opcional de e-mail com o resumo dos alertas de alto risco (configurado no .env).

O envio corre numa thread à parte: um servidor de e-mail lento ou em baixo nunca atrasa a análise.
A mensagem só tem contagens e o link para a fila, nunca dados de clientes.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage

from .configuracao import Configuracao

registo = logging.getLogger("detetor")
TEMPO_LIMITE = 20  # segundos


def montar_resumo(cfg: Configuracao, nome_ficheiro: str, alto: int, total: int) -> EmailMessage:
    base = cfg.url_publica or f"http://{cfg.host}:{cfg.porta}"
    mensagem = EmailMessage()
    mensagem["Subject"] = f"[Detetor de Fraudes] {alto} alerta(s) novo(s) de alto risco"
    mensagem["From"] = cfg.smtp_remetente or cfg.smtp_utilizador or "detetor@localhost"
    mensagem["To"] = ", ".join(cfg.email_alertas)
    mensagem.set_content(
        f"A análise do ficheiro '{nome_ficheiro}' gerou {total} alerta(s), {alto} de alto risco.\n\n"
        f"Abra a fila de alertas para os investigar:\n{base}/alertas?vista=novos&nivel=Alto+risco\n\n"
        "Mensagem automática do Detetor de Fraudes Financeiras.\n")
    return mensagem


def enviar(cfg: Configuracao, mensagem: EmailMessage) -> bool:
    try:
        with smtplib.SMTP(cfg.smtp_servidor, cfg.smtp_porta, timeout=TEMPO_LIMITE) as smtp:
            if cfg.smtp_tls:
                smtp.starttls(context=ssl.create_default_context())
            if cfg.smtp_utilizador:
                smtp.login(cfg.smtp_utilizador, cfg.smtp_senha)
            smtp.send_message(mensagem)
        registo.info("E-mail '%s' enviado para %d destinatário(s)", mensagem["Subject"], len(cfg.email_alertas))
        return True
    except (OSError, smtplib.SMTPException) as exc:
        registo.error("Falha ao enviar o e-mail '%s': %s", mensagem["Subject"], exc)
        return False


def avisar_alertas(cfg: Configuracao, nome_ficheiro: str, alto: int, total: int, esperar: bool = False) -> None:
    """Envia o resumo se o e-mail estiver configurado e houver alertas de alto risco."""
    if not cfg.email_ativo or alto <= 0:
        return
    tarefa = threading.Thread(target=enviar, args=(cfg, montar_resumo(cfg, nome_ficheiro, alto, total)),
                              name="email-alertas", daemon=True)
    tarefa.start()
    if esperar:
        tarefa.join()
