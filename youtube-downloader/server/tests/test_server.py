"""Testes que nao tocam a rede.

O foco e o que ja quebrou de verdade ou que so foi validado na mao:
as regras de aceitacao de cliente (uma regra de seguranca sutil, que ninguem
vai reconferir manualmente daqui a seis meses), o formato do arquivo de
cookies e a validacao de URL.
"""

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server

EXTENSAO = {"X-YTDL-Client": "extension"}
ORIGEM_EXTENSAO = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
ORIGEM_SITE = "https://site-malicioso.com"


# --------------------------------------------------------------- servidor


@pytest.fixture(scope="module")
def base_url():
    """Sobe o servidor de verdade numa porta efemera."""
    httpd = server.Servidor(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def pedir(url, metodo="GET", headers=None, corpo=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url, method=metodo, data=dados)
    for chave, valor in (headers or {}).items():
        req.add_header(chave, valor)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        corpo_erro = e.read()
        return e.code, json.loads(corpo_erro) if corpo_erro else {}


# ------------------------------------------------- quem pode falar com o server


def test_extensao_sem_origin_e_aceita(base_url):
    """O Chrome nao manda Origin para hosts em host_permissions.

    Exigir Origin aqui foi um bug real: barrava justamente a extensao.
    """
    codigo, corpo = pedir(f"{base_url}/health", headers=EXTENSAO)
    assert codigo == 200
    assert corpo["ok"] is True


def test_extensao_com_origin_de_extensao_e_aceita(base_url):
    codigo, _ = pedir(
        f"{base_url}/health", headers={**EXTENSAO, "Origin": ORIGEM_EXTENSAO}
    )
    assert codigo == 200


def test_pagina_web_e_recusada(base_url):
    codigo, _ = pedir(f"{base_url}/health", headers={**EXTENSAO, "Origin": ORIGEM_SITE})
    assert codigo == 403


def test_sem_o_cabecalho_e_recusado(base_url):
    codigo, _ = pedir(f"{base_url}/health")
    assert codigo == 403


def test_preflight_de_pagina_web_e_recusado(base_url):
    codigo, _ = pedir(
        f"{base_url}/download", metodo="OPTIONS", headers={"Origin": ORIGEM_SITE}
    )
    assert codigo == 403


def test_post_simples_sem_preflight_e_recusado(base_url):
    """POST com text/plain nao gera preflight - o cabecalho e quem barra."""
    codigo, _ = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={"Origin": ORIGEM_SITE, "Content-Type": "text/plain"},
        corpo={"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"},
    )
    assert codigo == 403


# ------------------------------------------------------------ validacao de URL


def test_url_fora_do_youtube_e_recusada(base_url):
    codigo, corpo = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={"url": "https://exemplo.com/video.mp4"},
    )
    assert codigo == 400
    assert "YouTube" in corpo["error"]


def test_corpo_invalido_e_recusado(base_url):
    codigo, _ = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={"sem_url": 1},
    )
    assert codigo == 400


def test_status_de_job_desconhecido(base_url):
    codigo, _ = pedir(f"{base_url}/status?id=naoexiste", headers=EXTENSAO)
    assert codigo == 404


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "https://m.youtube.com/watch?v=jNQXAC9IVRw",
        "https://www.youtube.com/shorts/AbCdEfGhIjK",
        "https://www.youtube.com/live/AbCdEfGhIjK",
        "https://youtu.be/AbCdEfGhIjK",
    ],
)
def test_urls_aceitas(url):
    assert server.YOUTUBE_RE.match(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://exemplo.com/video.mp4",
        "http://www.youtube.com/watch?v=x",  # http puro
        "https://youtube.com.evil.com/watch?v=x",  # dominio parecido
        "https://www.youtube.com/feed/subscriptions",
    ],
)
def test_urls_recusadas(url):
    assert not server.YOUTUBE_RE.match(url)


# ------------------------------------------------------------------- cookies


def test_cookies_viram_formato_netscape(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    caminho = server.escrever_cookies(
        [
            {
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "expires": 1800000000.5,
                "name": "SID",
                "value": "abc123",
            },
            {
                "domain": "www.youtube.com",
                "path": "/x",
                "secure": False,
                "expires": None,
                "name": "PREF",
                "value": "f6=4",
            },
        ]
    )
    try:
        linhas = Path(caminho).read_text(encoding="utf-8").strip().split("\n")
    finally:
        os.remove(caminho)

    assert linhas[0] == "# Netscape HTTP Cookie File"
    # dominio, includeSubdomains, path, secure, expiry, nome, valor
    assert linhas[1].split("\t") == [
        ".youtube.com",
        "TRUE",
        "/",
        "TRUE",
        "1800000000",
        "SID",
        "abc123",
    ]
    # sem ponto na frente => nao vale para subdominios
    assert linhas[2].split("\t") == [
        "www.youtube.com",
        "FALSE",
        "/x",
        "FALSE",
        "0",
        "PREF",
        "f6=4",
    ]


def test_cookies_invalidos_sao_descartados(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    caminho = server.escrever_cookies(
        [
            {"domain": "", "name": "sem_dominio", "value": "x"},
            {"domain": ".youtube.com", "value": "sem_nome"},
            {"domain": ".youtube.com", "name": "bom", "value": "v"},
        ]
    )
    try:
        conteudo = Path(caminho).read_text(encoding="utf-8")
    finally:
        os.remove(caminho)

    assert "sem_dominio" not in conteudo
    assert "sem_nome" not in conteudo
    assert "bom" in conteudo


# ------------------------------------------------------------------ formatos


def test_cadeia_de_formatos_prefere_mp4_e_tem_fallbacks():
    ramos = server.FORMATO.split("/")
    assert ramos[0] == "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
    # "best" sozinho nao serve de rede de seguranca: no yt-dlp ele significa
    # "melhor formato ja com video E audio juntos", e o YouTube quase so
    # entrega faixas separadas. Precisa haver fallback depois dele.
    assert ramos.index("best") < len(ramos) - 1
    assert "bestvideo*+bestaudio" in ramos


def test_runtimes_js_incluem_node():
    # Sem um runtime JS o yt-dlp nao resolve o desafio "n" do YouTube e so
    # devolve as imagens da miniatura. Deno e o unico ligado por padrao.
    assert "node" in server.JS_RUNTIMES


# -------------------------------------------------------------- pasta exibida


def test_health_informa_a_pasta_de_destino(base_url, monkeypatch):
    """A popup mostra o caminho que o /health informa, nao um escrito na mao.

    Dentro do container o destino real e "/downloads", que nao existe para
    quem esta olhando o Windows: YTDL_DISPLAY_DIR carrega o caminho do host
    montado ali. Sem isso a mensagem de "salvo em ..." aponta para o nada.
    """
    monkeypatch.setattr(server, "DISPLAY_DIR", "C:/Users/fulano/Downloads/YouTube")
    codigo, corpo = pedir(f"{base_url}/health", headers=EXTENSAO)
    assert codigo == 200
    assert corpo["pasta"] == "C:/Users/fulano/Downloads/YouTube"
