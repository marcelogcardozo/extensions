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
import time
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


# ------------------------------------------------- downloads em andamento


def esvaziar_fila():
    while not server.fila.empty():
        server.fila.get_nowait()
        server.fila.task_done()


@pytest.fixture
def jobs_limpos():
    """Cada teste comeca e termina sem job nem pedido pendente."""
    server.jobs.clear()
    esvaziar_fila()
    yield server.jobs
    server.jobs.clear()
    esvaziar_fila()


def test_download_duplicado_reaproveita_o_job(base_url, jobs_limpos):
    """Clicar de novo no mesmo video nao pode subir um segundo yt-dlp.

    Dois downloads simultaneos da mesma URL escrevem nos MESMOS arquivos
    .part e travam os dois. Foi assim que um download real ficou pela metade:
    a popup fechou, perdeu o rastro do job, e o segundo clique brigou com o
    primeiro.
    """
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    server.set_job(
        "jaexiste",
        status="downloading",
        percent=12.0,
        url=url,
        qualidade="melhor",
        nome=None,
        criado=time.time(),
    )

    codigo, corpo = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={"url": url},
    )

    assert codigo == 200
    assert corpo["id"] == "jaexiste"
    assert corpo["ja_em_andamento"] is True
    assert list(jobs_limpos) == ["jaexiste"]  # nenhum job novo foi criado


@pytest.mark.usefixtures("jobs_limpos")
def test_job_terminado_nao_bloqueia_nova_tentativa():
    """Depois de um erro, clicar de novo tem que comecar um download novo."""
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    server.set_job("falhou", status="error", url=url, finalizado=time.time())
    assert server.job_ativo_para(url, "melhor", None) is None


@pytest.mark.usefixtures("jobs_limpos")
def test_jobs_lista_em_ordem_e_informa_a_pasta(base_url):
    """E por aqui que a popup reencontra um download que ja estava rolando."""
    server.set_job("velho", status="done", percent=100, criado=1.0)
    server.set_job("novo", status="downloading", percent=40.0, criado=2.0)

    codigo, corpo = pedir(f"{base_url}/jobs", headers=EXTENSAO)

    assert codigo == 200
    assert [j["id"] for j in corpo["jobs"]] == ["velho", "novo"]
    assert corpo["pasta"] == server.DISPLAY_DIR


def test_jobs_exige_o_cabecalho_da_extensao(base_url):
    codigo, _ = pedir(f"{base_url}/jobs")
    assert codigo == 403


def test_job_terminado_ha_muito_tempo_sai_da_memoria(jobs_limpos):
    agora = time.time()
    server.set_job("antigo", status="done", finalizado=agora - server.RETENCAO_S - 1)
    server.set_job("recente", status="done", finalizado=agora)
    server.set_job("rodando", status="downloading", criado=agora)

    server.limpar_jobs_antigos()

    assert sorted(jobs_limpos) == ["recente", "rodando"]


# ------------------------------------------- parciais e downloads interrompidos


@pytest.fixture
def pastas(tmp_path, monkeypatch):
    """Aponta as pastas do servidor para um diretorio descartavel."""
    saida = tmp_path / "saida"
    trabalho = saida / ".em-andamento"
    trabalho.mkdir(parents=True)
    monkeypatch.setattr(server, "OUTPUT_DIR", saida)
    monkeypatch.setattr(server, "TRABALHO_DIR", trabalho)
    return saida, trabalho


def criar(pasta, nome, tamanho=10):
    arquivo = pasta / nome
    arquivo.write_bytes(b"x" * tamanho)
    return arquivo


def test_pasta_de_trabalho_fica_dentro_da_pasta_de_saida():
    """O passo final e um rename - entre volumes diferentes viraria copia.

    Num arquivo de 800 MB a diferenca entre renomear e copiar se nota.
    """
    assert server.TRABALHO_DIR.parent == server.OUTPUT_DIR


@pytest.mark.usefixtures("jobs_limpos")
def test_interrompidos_agrupa_arquivos_do_mesmo_video(pastas):
    saida, trabalho = pastas
    criar(trabalho, "Aula 2 [fj8XgF2__0M].f137.mp4", 500)
    criar(trabalho, "Aula 2 [fj8XgF2__0M].f140.m4a.part", 200)
    criar(saida, "Aula 1 [aqz-KE-bpKQ].mp4", 900)  # pronto: nao e interrompido

    itens = server.listar_interrompidos()

    assert len(itens) == 1
    assert itens[0]["id"] == "fj8XgF2__0M"
    assert itens[0]["titulo"] == "Aula 2"
    assert itens[0]["bytes"] == 700
    assert itens[0]["arquivos"] == 2


@pytest.mark.usefixtures("jobs_limpos")
def test_video_baixando_agora_nao_aparece_como_interrompido(pastas):
    _, trabalho = pastas
    criar(trabalho, "Aula 2 [fj8XgF2__0M].f137.mp4")
    server.set_job(
        "ativo",
        status="downloading",
        url="https://www.youtube.com/watch?v=fj8XgF2__0M",
        criado=time.time(),
    )
    assert server.listar_interrompidos() == []


def test_descartar_nao_toca_no_video_pronto(pastas):
    """A garantia que faz o botao "Descartar" ser seguro de clicar."""
    saida, trabalho = pastas
    parcial = criar(trabalho, "Aula 2 [fj8XgF2__0M].f140.m4a.part")
    intermediario = criar(saida, "Aula 2 [fj8XgF2__0M].f137.mp4")
    pronto = criar(saida, "Aula 2 [fj8XgF2__0M].mp4")
    alheio = criar(saida, "Aula 9 [aqz-KE-bpKQ].f137.mp4")

    assert server.descartar("fj8XgF2__0M") == 2

    assert not parcial.exists()
    assert not intermediario.exists()
    assert pronto.exists()  # o que importa: o video baixado sobrevive
    assert alheio.exists()  # e o parcial de outro video tambem


def test_descartar_recusa_id_invalido(base_url):
    codigo, _ = pedir(
        f"{base_url}/descartar",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={"id": "../../qualquer-coisa"},
    )
    assert codigo == 400


@pytest.mark.usefixtures("jobs_limpos")
def test_cancelar_marca_o_job_para_parar(base_url):
    server.set_job("rodando", status="downloading", criado=time.time())
    try:
        codigo, _ = pedir(
            f"{base_url}/cancelar",
            metodo="POST",
            headers={**EXTENSAO, "Content-Type": "application/json"},
            corpo={"id": "rodando"},
        )
        assert codigo == 200
        assert "rodando" in server.cancelados
    finally:
        server.cancelados.discard("rodando")


@pytest.mark.usefixtures("jobs_limpos")
def test_cancelar_job_que_nao_esta_rodando(base_url):
    codigo, _ = pedir(
        f"{base_url}/cancelar",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={"id": "naoexiste"},
    )
    assert codigo == 404


# ------------------------------------------------------ faixas e progresso


@pytest.mark.parametrize(
    ("formato", "esperado"),
    [
        ({"vcodec": "avc1.64001f", "acodec": "none"}, "video"),
        ({"vcodec": "none", "acodec": "mp4a.40.2"}, "audio"),
        ({"vcodec": "avc1", "acodec": "mp4a"}, "completo"),
        ({"vcodec": "none", "acodec": "none"}, ""),
        ({"vcodec": None, "acodec": None}, ""),
        ({}, ""),
    ],
)
def test_faixa_do_formato(formato, esperado):
    assert server.faixa_do_formato(formato) == esperado


def test_progresso_cobre_as_duas_faixas_sem_reiniciar():
    """A barra ia a 100 na trilha de video e voltava a zero no audio.

    O YouTube entrega video e audio separados e o yt-dlp baixa duas vezes;
    medir so a faixa atual fazia a segunda parecer um recomeco.
    """
    plano = {"faixas": 2, "total": 1000, "concluidos": 0, "bytes_prontos": 0}

    assert server.percentual(plano, 400, 800) == 40.0  # metade do video

    plano["bytes_prontos"] = 800  # video terminou, audio comeca do zero
    assert server.percentual(plano, 0, 200) == 80.0  # nao volta para zero
    assert server.percentual(plano, 200, 200) == 100.0


def test_progresso_sem_tamanho_previsto_mede_so_a_faixa():
    plano = {"faixas": 2, "total": None, "concluidos": 0, "bytes_prontos": 0}
    assert server.percentual(plano, 50, 200) == 25.0
    assert server.percentual(plano, 50, None) is None


def test_progresso_nunca_passa_de_cem():
    """total_bytes as vezes e estimativa, e estimativa erra para baixo."""
    plano = {"faixas": 1, "total": 100, "concluidos": 0, "bytes_prontos": 0}
    assert server.percentual(plano, 140, 100) == 100.0


# ------------------------------------------------------------------- fila


def test_download_entra_na_fila_em_vez_de_comecar_na_hora(base_url, jobs_limpos):
    """Quem baixa e o trabalhador, nao o handler do POST.

    E o que permite limitar quantos downloads correm juntos: dois arquivos de
    800 MB dividindo a mesma banda terminam os dois mais tarde do que se
    tivessem ido em sequencia.
    """
    codigo, corpo = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"},
    )

    assert codigo == 200
    assert jobs_limpos[corpo["id"]]["status"] == "queued"
    assert server.fila.qsize() == 1


@pytest.mark.usefixtures("jobs_limpos")
def test_video_esperando_na_fila_nao_e_enfileirado_de_novo(base_url):
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    server.set_job(
        "esperando",
        status="queued",
        url=url,
        qualidade="melhor",
        nome=None,
        criado=time.time(),
    )

    codigo, corpo = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={"url": url},
    )

    assert codigo == 200
    assert corpo["id"] == "esperando"
    assert server.fila.qsize() == 0


@pytest.mark.usefixtures("jobs_limpos")
def test_posicao_na_fila_segue_a_ordem_de_chegada():
    server.set_job("terceiro", status="queued", criado=3.0)
    server.set_job("primeiro", status="queued", criado=1.0)
    server.set_job("baixando", status="downloading", criado=2.0)
    server.set_job("segundo", status="queued", criado=2.5)

    posicoes = {j["id"]: j.get("posicao") for j in server.listar_jobs()}

    assert posicoes["primeiro"] == 1
    assert posicoes["segundo"] == 2
    assert posicoes["terceiro"] == 3
    assert posicoes["baixando"] is None  # quem ja comecou nao tem posicao


@pytest.mark.usefixtures("jobs_limpos")
def test_cancelar_quem_esta_na_fila_nao_espera_o_hook(base_url):
    """Nao ha yt-dlp para abortar ainda, entao o cancelamento e imediato."""
    server.set_job("esperando", status="queued", criado=time.time())

    codigo, _ = pedir(
        f"{base_url}/cancelar",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={"id": "esperando"},
    )

    assert codigo == 200
    assert server.get_job("esperando")["status"] == "canceled"
    assert "esperando" not in server.cancelados


@pytest.mark.usefixtures("jobs_limpos")
def test_trabalhador_ignora_pedido_cancelado_enquanto_esperava():
    server.set_job("desistiu", status="canceled", criado=time.time())
    server.fila.put(
        {
            "id": "desistiu",
            "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            "cookies": [],
            "nome": None,
            "qualidade": "melhor",
        }
    )

    # Uma volta do trabalhador, sem thread: nao pode chamar o download.
    pedido = server.fila.get_nowait()
    assert server.get_job(pedido["id"]).get("status") != "queued"


# --------------------------------------------------------- nome do arquivo


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("Aula 04 - VPC", "Aula 04 - VPC"),
        ("  Aula   04  ", "Aula 04"),
        ("AWS: Redes", "AWS Redes"),
        ("Aula 4.", "Aula 4"),  # Windows recusa ponto no fim
        ("Aula 04 — Sub-redes e ACLs", "Aula 04 — Sub-redes e ACLs"),
        ("", None),
        ("   ", None),
        ("...", None),
        (None, None),
    ],
)
def test_nome_de_arquivo(bruto, esperado):
    assert server.nome_de_arquivo(bruto) == esperado


def test_nome_escolhido_nunca_vira_caminho():
    """Um nome de arquivo nao pode escapar da pasta de saida."""
    barra = chr(92)
    assert server.nome_de_arquivo(f"..{barra}..{barra}Windows") == "Windows"
    assert server.nome_de_arquivo("../../etc/passwd") == "etcpasswd"


def test_nome_e_cortado_no_limite():
    assert len(server.nome_de_arquivo("a" * 500)) == server.LIMITE_DO_NOME


def test_modelo_dobra_o_por_cento():
    """Senao o yt-dlp leria um "%" digitado como inicio de template."""
    assert server.modelo_de_saida("100% AWS") == "100%% AWS [%(id)s].%(ext)s"


@pytest.mark.parametrize("nome", [None, "Aula 04"])
def test_modelo_sempre_guarda_o_id_no_nome(nome):
    """A varredura de parciais descobre o video pelo [id] no nome."""
    assert "[%(id)s]" in server.modelo_de_saida(nome)


@pytest.mark.usefixtures("jobs_limpos")
def test_nome_escolhido_chega_limpo_na_fila(base_url):
    codigo, _ = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={
            "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            "nome": "Aula 04 / VPC ",
        },
    )
    assert codigo == 200

    pedido = server.fila.get_nowait()
    server.fila.task_done()
    assert pedido["nome"] == "Aula 04 VPC"


# ------------------------------------------------------- ja esta na pasta


def test_baixados_lista_so_o_que_esta_pronto(pastas):
    saida, trabalho = pastas
    criar(saida, "Aula 1 [aqz-KE-bpKQ].mp4")
    criar(saida, "Aula 2 [fj8XgF2__0M].f137.mp4")  # trilha solta, nao esta pronto
    criar(trabalho, "Aula 3 [M5ZHGyJrVSY].f140.m4a.part")
    criar(saida, "sem id nenhum.mp4")

    assert server.listar_baixados() == ["aqz-KE-bpKQ"]


def test_baixados_ignora_pasta_inexistente(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path / "nao-existe")
    assert server.listar_baixados() == []


# ---------------------------------------------------------------- qualidade


@pytest.mark.parametrize("qualidade", ["720", "1080"])
def test_qualidade_limitada_nunca_faz_upgrade_silencioso(qualidade):
    """Pedir 720p e receber um arquivo 4K seria pior do que falhar.

    Toda a cadeia carrega o limite; nao ha degrau final sem restricao.
    """
    for ramo in server.FORMATOS[qualidade].split("/"):
        assert f"height<={qualidade}" in ramo


def test_so_audio_nunca_pede_video():
    for ramo in server.FORMATOS["audio"].split("/"):
        assert ramo.startswith("bestaudio")


def test_qualidade_tem_a_mesma_rede_de_seguranca_do_melhor():
    """O "best" sozinho nao serve de fallback - veja o comentario do FORMATO."""
    ramos = server.FORMATOS["720"].split("/")
    assert ramos[0].startswith("bestvideo[height<=720][ext=mp4]")
    assert ramos.index("best[height<=720]") < len(ramos) - 1


@pytest.mark.usefixtures("jobs_limpos")
def test_qualidade_escolhida_chega_na_fila(base_url):
    codigo, corpo = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={
            "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            "qualidade": "720",
        },
    )
    assert codigo == 200
    assert server.get_job(corpo["id"])["qualidade"] == "720"
    assert server.fila.get_nowait()["qualidade"] == "720"


@pytest.mark.parametrize("pedida", ["4k", "", None, "../etc"])
@pytest.mark.usefixtures("jobs_limpos")
def test_qualidade_desconhecida_cai_no_padrao(base_url, pedida):
    """Extensao mais velha nao pode ver o download ser recusado."""
    codigo, corpo = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={
            "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
            "qualidade": pedida,
        },
    )
    assert codigo == 200
    assert server.get_job(corpo["id"])["qualidade"] == server.QUALIDADE_PADRAO


@pytest.mark.usefixtures("jobs_limpos")
def test_mesma_url_em_qualidade_diferente_nao_e_duplicata(base_url):
    """720p e "so audio" do mesmo video dao dois arquivos, nao um.

    Formatos diferentes gravam parciais diferentes, entao nao ha colisao a
    evitar - e recusar o segundo devolvia um job que nao era o pedido.
    """
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    server.set_job(
        "video",
        status="downloading",
        url=url,
        qualidade="720",
        nome=None,
        criado=time.time(),
    )

    codigo, corpo = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={"url": url, "qualidade": "audio"},
    )

    assert codigo == 200
    assert corpo["id"] != "video"
    assert corpo.get("ja_em_andamento") is None


@pytest.mark.usefixtures("jobs_limpos")
def test_mesmo_pedido_repetido_continua_sendo_duplicata(base_url):
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    server.set_job(
        "ja",
        status="downloading",
        url=url,
        qualidade="720",
        nome="Aula 04",
        criado=time.time(),
    )

    codigo, corpo = pedir(
        f"{base_url}/download",
        metodo="POST",
        headers={**EXTENSAO, "Content-Type": "application/json"},
        corpo={"url": url, "qualidade": "720", "nome": "Aula 04"},
    )

    assert codigo == 200
    assert corpo["id"] == "ja"
    assert corpo["ja_em_andamento"] is True
