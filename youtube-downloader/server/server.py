"""Servidor local que recebe URLs do YouTube da extensao e baixa com yt-dlp.

Escuta apenas no host configurado - nada fica exposto para fora da maquina.
Dentro de um container, escuta em 0.0.0.0 e quem restringe o acesso e o publish
da porta no compose.yaml ("127.0.0.1:8756:8756").

A extensao manda junto os cookies do youtube.com lidos pela API do proprio
Chrome. Isso e necessario porque o YouTube pode exigir cookies para liberar o
download ("Sign in to confirm you're not a bot") e, desde o Chrome 127, o
yt-dlp nao consegue mais decifrar o banco de cookies do Chrome no Windows.
E tambem o que torna o container viavel: o servidor nunca precisa enxergar o
navegador nem o sistema de arquivos do host.
"""

import contextlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp

HOST = os.environ.get("YTDL_HOST", "127.0.0.1")
PORT = int(os.environ.get("YTDL_PORT", "8756"))
OUTPUT_DIR = Path(
    os.environ.get("YTDL_OUTPUT_DIR") or Path.home() / "Downloads" / "YouTube"
)

# Dentro do container, OUTPUT_DIR e "/downloads" - um caminho que nao existe
# para quem esta olhando o Windows. O compose informa aqui o caminho do host
# que esta montado ali, e e esse que a popup mostra. Sem isso, a mensagem de
# "salvo em ..." aponta para um lugar que o usuario nao consegue abrir.
DISPLAY_DIR = os.environ.get("YTDL_DISPLAY_DIR") or str(OUTPUT_DIR)

# Onde o download acontece de fato. A pasta de saida so recebe o MP4 pronto:
# as trilhas separadas (".f137.mp4"), os parciais (".part") e o estado de
# fragmentos (".ytdl") vivem e morrem aqui dentro.
#
# Antes disso tudo caia junto na pasta do usuario, que passava a misturar
# resultado com canteiro de obras - e, quando algo dava errado, o Explorer era
# a unica interface que sobrava para entender o estrago.
#
# Precisa ser DENTRO da pasta de saida: o passo final e um rename, e rename
# entre volumes diferentes vira copia (num arquivo de 800 MB isso se nota).
TRABALHO_DIR = OUTPUT_DIR / ".em-andamento"

# So aceita URLs de video do YouTube; qualquer outra coisa e recusada antes
# de chegar perto do yt-dlp.
YOUTUBE_RE = re.compile(
    r"^https://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?|shorts/|live/)|youtu\.be/)",
    re.IGNORECASE,
)

# Status em que um download ainda esta vivo. Fora deles, ele terminou.
ATIVOS = ("starting", "downloading", "merging")

# Por quanto tempo um job terminado fica na memoria. E o que permite a popup
# e o service worker verem o desfecho de um download que acabou enquanto
# ninguem estava olhando.
RETENCAO_S = 3600

# O id do video tem 11 caracteres e aparece entre colchetes no nome do
# arquivo. E por ele que a gente descobre a que video um parcial pertence.
ID_NO_NOME = re.compile(r"\[([A-Za-z0-9_-]{11})\]")
ID_NA_URL = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")

# Na pasta de trabalho tudo e parcial por definicao. Ja na pasta de saida
# (onde versoes antigas deixaram sobras) so estes padroes contam - assim um
# MP4 pronto nunca entra na lista nem corre risco de ser apagado.
PARCIAL_NA_SAIDA = re.compile(r"\.f\d+\.|\.part$|\.ytdl$|\.part-Frag\d+$")

jobs = {}
jobs_lock = threading.Lock()

# Jobs que o usuario mandou parar. O progress hook confere a cada batida.
cancelados = set()


class Cancelado(Exception):  # noqa: N818 - sinal de controle, nao erro
    """O usuario pediu para parar este download.

    Sem sufixo "Error" de proposito: nada deu errado aqui, e o nome descreve o
    que aconteceu. Levantar de dentro do progress hook e como o yt-dlp aborta
    um download em andamento.
    """


def set_job(job_id, **fields):
    with jobs_lock:
        jobs.setdefault(job_id, {}).update(fields)


def get_job(job_id):
    with jobs_lock:
        return dict(jobs.get(job_id, {}))


def listar_jobs():
    """Todos os jobs conhecidos, do mais antigo para o mais novo."""
    with jobs_lock:
        itens = [{"id": i, **j} for i, j in jobs.items()]
    return sorted(itens, key=lambda j: j.get("criado", 0))


def job_ativo_para(url):
    """Id de um download em andamento para esta URL, se existir.

    Sem isto, reabrir a popup e clicar de novo sobe um segundo yt-dlp
    escrevendo nos MESMOS arquivos .part do primeiro. Os dois se atropelam e
    o download trava pela metade - que e exatamente o sintoma de "fechei a
    janelinha e sobrou um .part na pasta".
    """
    with jobs_lock:
        for job_id, job in jobs.items():
            if job.get("url") == url and job.get("status") in ATIVOS:
                return job_id
    return None


def _parciais():
    """Arquivos de trabalho existentes, agrupados por video."""
    achados = {}
    for pasta, tudo_e_parcial in ((TRABALHO_DIR, True), (OUTPUT_DIR, False)):
        if not pasta.is_dir():
            continue
        for arquivo in pasta.iterdir():
            if not arquivo.is_file():
                continue
            if not tudo_e_parcial and not PARCIAL_NA_SAIDA.search(arquivo.name):
                continue
            achado = ID_NO_NOME.search(arquivo.name)
            if achado:
                achados.setdefault(achado.group(1), []).append(arquivo)
    return achados


def listar_interrompidos():
    """Downloads que pararam no meio e ainda dao para retomar.

    Sem isto, depois que os parciais sairam da pasta de saida eles ficariam
    invisiveis - bom para a prateleira, pessimo para quem tinha 500 MB de
    aula ja baixados.
    """
    with jobs_lock:
        em_curso = {
            m.group(1)
            for j in jobs.values()
            if j.get("status") in ATIVOS and (m := ID_NA_URL.search(j.get("url") or ""))
        }

    saida = []
    for video_id, arquivos in _parciais().items():
        if video_id in em_curso:
            continue
        titulo = arquivos[0].name.split(f"[{video_id}]")[0].strip()
        saida.append(
            {
                "id": video_id,
                "titulo": titulo or video_id,
                # Bytes, e nao porcentagem: sem consultar o YouTube de novo nao da
                # para saber o total, e porcentagem inventada e pior que numero
                # honesto.
                "bytes": sum(a.stat().st_size for a in arquivos),
                "arquivos": len(arquivos),
            }
        )
    return sorted(saida, key=lambda i: i["titulo"].lower())


def descartar(video_id):
    """Apaga os arquivos de trabalho de um video. Nunca toca num MP4 pronto."""
    apagados = 0
    for arquivo in _parciais().get(video_id, []):
        with contextlib.suppress(OSError):
            arquivo.unlink()
            apagados += 1
    return apagados


def limpar_jobs_antigos():
    """Jobs terminados nao precisam ficar na memoria para sempre."""
    agora = time.time()
    with jobs_lock:
        velhos = [
            job_id
            for job_id, job in jobs.items()
            if job.get("status") not in ATIVOS
            and agora - job.get("finalizado", agora) > RETENCAO_S
        ]
        for job_id in velhos:
            del jobs[job_id]


def escrever_cookies(cookies):
    """Grava os cookies da extensao num arquivo no formato Netscape."""
    linhas = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        dominio = c.get("domain", "")
        if not dominio or not c.get("name"):
            continue
        linhas.append(
            "\t".join(
                [
                    dominio,
                    "TRUE" if dominio.startswith(".") else "FALSE",
                    c.get("path", "/"),
                    "TRUE" if c.get("secure") else "FALSE",
                    str(int(c.get("expires") or 0)),
                    c["name"],
                    c.get("value", ""),
                ]
            )
        )

    fd, caminho = tempfile.mkstemp(prefix="ytdl-cookies-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")
    return caminho


# Cadeia de formatos, do mais desejavel ao mais tolerante.
#
# Atencao a uma pegadinha do yt-dlp: "best" nao quer dizer "o melhor que
# houver", e sim "o melhor formato que ja venha com video E audio no mesmo
# arquivo". O YouTube hoje quase so entrega faixas separadas, entao "best"
# falha justamente nos casos em que a gente contava com ele de rede de
# seguranca. "bestvideo*" (com asterisco) aceita tambem formatos que ja tenham
# audio embutido, e por isso e um fallback melhor.
# fmt: off
FORMATO = "/".join([
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]",  # o ideal: MP4 + M4A
    "bestvideo*+bestaudio",                   # qualquer container
    "best",                                   # arquivo unico ja mixado
    "bestvideo*",                             # so video, se for so o que tem
    "bestaudio",                              # so audio, ultimo recurso
])
# fmt: on

# O YouTube protege as URLs com um desafio em JavaScript (o parametro "n"). O
# yt-dlp nao resolve isso sozinho: delega a um runtime JS externo, via pacote
# yt-dlp-ejs. So o "deno" vem habilitado por padrao, e a maioria das maquinas
# nao tem Deno - mas tem Node. Sem isso, a extracao devolve apenas as imagens
# da miniatura e o download falha com "Requested format is not available",
# mensagem que nao da nenhuma pista da causa real.
JS_RUNTIMES = {"deno": {}, "node": {}}


class Registrador:
    """Guarda os avisos do yt-dlp para que aparecam junto do erro na popup."""

    def __init__(self):
        self.mensagens = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self.mensagens.append(msg)

    def error(self, msg):
        self.mensagens.append(msg)


def listar_formatos(url, opts):
    """Formatos que o YouTube realmente ofereceu, para diagnostico."""
    # Precisa herdar js_runtimes, senao a sonda roda sem resolver o desafio
    # e lista "nenhum formato" mesmo quando o download real teria formatos.
    sonda = {
        k: v for k, v in opts.items() if k in ("cookiefile", "quiet", "js_runtimes")
    }
    sonda["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(sonda) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:  # noqa: BLE001 - diagnostico e best-effort
        return []

    linhas = []
    for f in info.get("formats", []):
        marca = "" if f.get("url") else "  <- sem URL"
        linhas.append(
            f"{f.get('format_id', '?'):>8}  {f.get('ext', '?'):<5}"
            f"  {f.get('resolution') or f.get('format_note') or '?'}{marca}"
        )
    return linhas


def faixa_do_formato(formato):
    """Se este formato e a trilha de video, a de audio, ou um arquivo unico.

    O YouTube entrega video e audio separados, entao o yt-dlp baixa duas
    vezes - e o progresso vai a 100% duas vezes. Sem dizer qual faixa esta
    vindo, a segunda volta parece o download recomecando do zero.
    """
    tem_video = (formato.get("vcodec") or "none") != "none"
    tem_audio = (formato.get("acodec") or "none") != "none"
    if tem_video and tem_audio:
        return "completo"
    if tem_video:
        return "video"
    if tem_audio:
        return "audio"
    return ""


def tamanho_do_formato(formato):
    return formato.get("filesize") or formato.get("filesize_approx")


def percentual(plano, baixado, total_faixa):
    """Progresso do download inteiro, e nao da faixa atual.

    Com os tamanhos previstos das duas faixas, a barra anda de 0 a 100 uma vez
    so. Sem eles, so resta medir a faixa que esta vindo - e ai ela vai a 100
    duas vezes, o que o rotulo da etapa passa a explicar.
    """
    if plano["total"]:
        bruto = (plano["bytes_prontos"] + baixado) / plano["total"] * 100
    elif total_faixa:
        bruto = baixado / total_faixa * 100
    else:
        return None
    return round(min(bruto, 100), 1)


def download(job_id, url, cookies):
    # O progresso e do download inteiro, nao de cada faixa: somando os
    # tamanhos previstos, a barra anda de 0 a 100 uma vez so.
    plano = {"faixas": 1, "total": None, "concluidos": 0, "bytes_prontos": 0}

    def hook(d):
        with jobs_lock:
            parar = job_id in cancelados
        if parar:
            # Levantar dentro do hook e como o yt-dlp aborta um download.
            raise Cancelado

        faixa = faixa_do_formato(d.get("info_dict") or {})

        if d["status"] == "downloading":
            total_faixa = d.get("total_bytes") or d.get("total_bytes_estimate")
            set_job(
                job_id,
                status="downloading",
                percent=percentual(plano, d.get("downloaded_bytes", 0), total_faixa),
                faixa=faixa,
            )

        elif d["status"] == "finished":
            plano["concluidos"] += 1
            plano["bytes_prontos"] += d.get("total_bytes") or d.get(
                "downloaded_bytes", 0
            )
            # So e hora de juntar quando TODAS as faixas chegaram. Antes disso,
            # marcar "merging" fazia a tela mostrar baixar -> juntar -> baixar.
            if plano["concluidos"] >= plano["faixas"]:
                set_job(job_id, status="merging", percent=100)

    arquivo_cookies = escrever_cookies(cookies) if cookies else None
    registrador = Registrador()

    opts = {
        "outtmpl": "%(title)s [%(id)s].%(ext)s",
        "paths": {"home": str(OUTPUT_DIR), "temp": str(TRABALHO_DIR)},
        "format": FORMATO,
        "merge_output_format": "mp4",
        "progress_hooks": [hook],
        "noprogress": True,
        "quiet": True,
        "logger": registrador,
        "js_runtimes": JS_RUNTIMES,
    }
    if arquivo_cookies:
        opts["cookiefile"] = arquivo_cookies

    try:
        TRABALHO_DIR.mkdir(parents=True, exist_ok=True)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            set_job(job_id, title=info.get("title", ""))

            faixas = info.get("requested_formats") or [info]
            tamanhos = [tamanho_do_formato(f) for f in faixas]
            plano["faixas"] = len(faixas)
            plano["total"] = sum(tamanhos) if all(tamanhos) else None

            ydl.download([url])
        set_job(job_id, status="done", percent=100, finalizado=time.time())

    except Cancelado:
        # O parcial fica na pasta de trabalho de proposito: vira um item
        # "interrompido", que da para retomar ou descartar depois.
        set_job(job_id, status="canceled", percent=None, finalizado=time.time())

    except Exception as exc:  # noqa: BLE001 - queremos mostrar qualquer erro na popup
        partes = [str(exc)]

        # Nenhum formato serviu: mostrar o que o YouTube ofereceu, senao nao ha
        # como saber se faltou faixa de video, se as URLs vieram vazias, etc.
        if "format is not available" in str(exc).lower():
            formatos = listar_formatos(url, opts)
            if formatos:
                partes.append("\n\nFormatos disponiveis:\n" + "\n".join(formatos))
            else:
                partes.append("\n\nO YouTube nao ofereceu nenhum formato.")

        if registrador.mensagens:
            partes.append(
                "\n\nAvisos do yt-dlp:\n" + "\n".join(registrador.mensagens[:10])
            )

        set_job(
            job_id,
            status="error",
            error="".join(partes),
            finalizado=time.time(),
        )

    finally:
        with jobs_lock:
            cancelados.discard(job_id)

        # Os cookies sao credenciais: nao deixar sobrando no disco.
        if arquivo_cookies:
            with contextlib.suppress(OSError):
                os.remove(arquivo_cookies)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    # Somente a propria extensao pode falar com o servidor. Sem isso, qualquer
    # pagina aberta no navegador poderia mandar requisicoes para a porta local.
    #
    # O Chrome NAO manda o header Origin nas requisicoes da extensao para hosts
    # declarados em host_permissions (ele dispensa o CORS nesse caminho). Ja uma
    # pagina web sempre manda Origin numa requisicao cross-origin. Entao:
    #
    #   1. Origin de site (http/https) -> recusa.
    #   2. Exige um header proprio. Uma pagina web so consegue envia-lo passando
    #      por um preflight, e o preflight carrega Origin, barrado na regra 1.
    #      Isso fecha tambem o buraco das "simple requests" (POST text/plain),
    #      que passariam sem preflight.
    def _cliente_ok(self):
        origin = self.headers.get("Origin", "")
        if origin and not origin.startswith("chrome-extension://"):
            return False
        return self.headers.get("X-YTDL-Client") == "extension"

    def _recusar(self):
        cabecalhos = {
            k: v
            for k, v in self.headers.items()
            if k.lower() in ("origin", "referer", "user-agent", "x-ytdl-client")
        }
        print(f"  recusado: {cabecalhos}", flush=True)
        self._send(403, {"error": "requisicao nao veio da extensao"})

    def _send(self, code, payload, origin=None):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._cliente_ok():
            self._recusar()
            return
        self.send_response(204)
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-YTDL-Client")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._cliente_ok():
            self._recusar()
            return
        origin = self.headers.get("Origin")
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._send(200, {"ok": True, "pasta": DISPLAY_DIR}, origin)
            return

        if parsed.path == "/jobs":
            limpar_jobs_antigos()
            self._send(
                200,
                {
                    "jobs": listar_jobs(),
                    "interrompidos": listar_interrompidos(),
                    "pasta": DISPLAY_DIR,
                },
                origin,
            )
            return

        if parsed.path == "/status":
            job_id = parse_qs(parsed.query).get("id", [""])[0]
            job = get_job(job_id)
            if not job:
                self._send(404, {"error": "job desconhecido"}, origin)
                return
            self._send(200, job, origin)
            return

        self._send(404, {"error": "rota desconhecida"}, origin)

    def do_POST(self):
        if not self._cliente_ok():
            self._recusar()
            return
        origin = self.headers.get("Origin")

        rota = urlparse(self.path).path
        if rota not in ("/download", "/cancelar", "/descartar"):
            self._send(404, {"error": "rota desconhecida"}, origin)
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            corpo = json.loads(self.rfile.read(length))
        except ValueError:
            self._send(400, {"error": "corpo invalido"}, origin)
            return

        if rota == "/cancelar":
            job_id = corpo.get("id", "")
            if get_job(job_id).get("status") not in ATIVOS:
                self._send(404, {"error": "nao ha download ativo com esse id"}, origin)
                return
            with jobs_lock:
                cancelados.add(job_id)
            self._send(200, {"ok": True}, origin)
            return

        if rota == "/descartar":
            video_id = corpo.get("id", "")
            if not ID_NO_NOME.fullmatch(f"[{video_id}]"):
                self._send(400, {"error": "id de video invalido"}, origin)
                return
            self._send(200, {"apagados": descartar(video_id)}, origin)
            return

        try:
            url = corpo["url"]
            cookies = corpo.get("cookies") or []
        except KeyError:
            self._send(400, {"error": "corpo invalido"}, origin)
            return

        if not YOUTUBE_RE.match(url):
            self._send(400, {"error": "nao e uma URL de video do YouTube"}, origin)
            return

        limpar_jobs_antigos()

        # Ja tem um download desta URL rodando: devolve o mesmo job em vez de
        # subir um segundo yt-dlp para brigar pelos mesmos arquivos .part.
        em_andamento = job_ativo_para(url)
        if em_andamento:
            self._send(200, {"id": em_andamento, "ja_em_andamento": True}, origin)
            return

        job_id = uuid.uuid4().hex
        set_job(
            job_id,
            status="starting",
            percent=None,
            title="",
            url=url,
            criado=time.time(),
        )
        threading.Thread(
            target=download, args=(job_id, url, cookies), daemon=True
        ).start()
        self._send(200, {"id": job_id}, origin)


class Servidor(ThreadingHTTPServer):
    # O padrao do Python e SO_REUSEADDR ligado. No Windows isso deixa um
    # segundo processo tomar a porta sem erro nenhum: o servidor novo sobe
    # calado e o antigo e quem continua respondendo - o que rende horas de
    # confusao depois de editar o codigo. Melhor recusar de cara.
    allow_reuse_address = False


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TRABALHO_DIR.mkdir(parents=True, exist_ok=True)
    try:
        servidor = Servidor((HOST, PORT), Handler)
    except OSError:
        print(f"A porta {PORT} ja esta em uso.")
        print("Provavelmente ja existe uma janela deste servidor aberta.")
        print("Feche a outra janela (ou mude YTDL_PORT).")
        raise SystemExit(1) from None

    print(f"Baixando para: {OUTPUT_DIR}", flush=True)
    print(f"Ouvindo em http://{HOST}:{PORT} (Ctrl+C para parar)", flush=True)
    servidor.serve_forever()
