# YouTube Downloader Helper

Extensão de Chrome/Edge que baixa o vídeo da aba atual do YouTube — inclusive
quando ele está embutido num `<iframe>`, como nas plataformas de curso. Tudo
acontece na sua máquina; nada é enviado para fora dela.

## Como funciona

```
   Chrome                          seu PC
┌───────────┐   POST /download   ┌──────────────┐   ┌────────┐
│  popup    │ ─────────────────► │  server.py   │──►│ yt-dlp │──► pasta de saída
│ (extensão)│   URL + cookies    │ 127.0.0.1    │   │ +ffmpeg│
│           │ ◄───────────────── │   :8756      │   └────────┘
└───────────┘   GET /status      └──────────────┘
                  progresso
```

A extensão sozinha não dá conta: o YouTube entrega vídeo e áudio em faixas DASH
separadas, com URLs assinadas cujo esquema muda com frequência. O `yt-dlp` já
resolve isso e é atualizado constantemente; o `ffmpeg` junta as duas faixas num
MP4 só.

## Instalação

São duas metades: o **servidor** (Docker ou nativo) e a **extensão** no
navegador. Ambas são necessárias.

### 1. Servidor, com Docker (recomendado)

O servidor precisa de **Python + ffmpeg + Node.js + yt-dlp + yt-dlp-ejs**. São
três instalações de sistema separadas, e esquecer qualquer uma produz um erro
que não diz o que faltou. É isso que a imagem resolve — não o Python sozinho,
mas a combinação.

```bash
cd youtube-downloader
cp .env.example .env        # no Windows: copy .env.example .env
```

Abra o `.env` e aponte `YTDL_OUTPUT_DIR` para onde você quer os vídeos (barras
normais, mesmo no Windows: `C:/Users/fulano/Downloads/YouTube`). Então:

```bash
docker compose up -d
```

A imagem tem ~730 MB e o primeiro build leva alguns minutos. Para acompanhar o
servidor: `docker compose logs -f`. Para parar: `docker compose down`.

O `yt-dlp` é atualizado **toda vez que o container sobe** — é a dependência que
mais precisa disso, e congelada na imagem ela seria o ponto fraco de usar
Docker aqui. Para desligar, `YTDL_AUTO_UPDATE=0` no `.env`.

### 1b. Servidor, nativo (modo dev)

Requer **Python 3.13**, **ffmpeg** e **Node.js** no PATH.

```
server\scripts\install.bat     uma vez
server\scripts\start.bat       deixe a janela aberta enquanto for baixar
```

O `install.bat` avisa se faltar ffmpeg ou Node.

### 2. Extensão

1. Abra `chrome://extensions` (ou `edge://extensions`)
2. Ligue o **Modo do desenvolvedor** no canto superior direito
3. Clique em **Carregar sem compactação**
4. Selecione a pasta `extension` (a que tem o `manifest.json`)
5. Fixe o ícone na barra de ferramentas (ícone de quebra-cabeça → alfinete)

Não há build: a pasta `extension/` é carregável direto.

## Uso

1. Deixe o servidor rodando
2. Abra o vídeo — no YouTube ou numa página que o tenha embutido
3. Clique no ícone da extensão → **Baixar vídeo**

Se a página tiver mais de um vídeo, aparece uma lista para escolher. Se você
abrir a popup com o servidor desligado, ela avisa e fica checando sozinha —
basta subir o servidor e ela conecta em até 1 segundo.

O arquivo sai em MP4, na melhor qualidade disponível, com o nome
`Título [id].mp4`.

## Configuração

Tudo por variável de ambiente — é o que permite o mesmo `server.py` servir aos
dois modos de execução.

| Variável | Padrão | Para que serve |
|---|---|---|
| `YTDL_OUTPUT_DIR` | `~/Downloads/YouTube` | Pasta de saída. No Docker é o caminho do **host** que o compose monta em `/downloads`. |
| `YTDL_PORT` | `8756` | Porta no host. |
| `YTDL_HOST` | `127.0.0.1` | Interface de escuta. O compose usa `0.0.0.0` dentro do container — veja *Segurança*. |
| `YTDL_DISPLAY_DIR` | igual ao `OUTPUT_DIR` | Só para a popup mostrar um caminho que você consegue abrir: dentro do container o destino real é `/downloads`, que não existe no Windows. |
| `YTDL_AUTO_UPDATE` | `1` | `0` não atualiza o `yt-dlp` na subida do container. |

**Ao mudar a porta**, mude também `SERVIDOR` em
[extension/src/popup.js](extension/src/popup.js) e `host_permissions` em
[extension/manifest.json](extension/manifest.json) — o Chrome só deixa a
extensão falar com hosts declarados no manifest.

Qualidade e formato ficam na constante `FORMATO`, em
[server/server.py](server/server.py). Exemplos:

- Limitar a 1080p: `bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best`
- Só áudio: `bestaudio/best` (e troque `merge_output_format` por `mp3`)

## Decisões que não são óbvias

**Por que a extensão manda os cookies.** O YouTube bloqueia downloads anônimos
com "Sign in to confirm you're not a bot". O `yt-dlp` tem a opção
`--cookies-from-browser`, mas desde o Chrome 127 o Windows criptografa o banco
de cookies com App-Bound Encryption e ela não funciona mais. A extensão roda
*dentro* do Chrome, então lê os valores pela API `chrome.cookies` e os passa
adiante. O servidor grava num arquivo temporário, entrega ao `yt-dlp` e **apaga
o arquivo ao terminar**.

Esse desenho é o que torna o Docker viável, por acaso feliz: como é a extensão
que envia os cookies, o container não precisa de acesso nenhum ao navegador nem
ao sistema de arquivos do host — só de um volume para a pasta de saída. No
desenho convencional, em que o servidor lê o banco do Chrome, conteinerizar
seria inviável.

**Por que precisa do Node.js.** O YouTube protege as URLs dos formatos com um
desafio em JavaScript (o parâmetro `n`). O `yt-dlp` não resolve isso sozinho —
delega a um runtime JS externo, via o pacote `yt-dlp-ejs`. E o único runtime
habilitado por padrão é o **Deno**, que quase ninguém tem. Por isso o servidor
liga o `node` explicitamente:

```python
"js_runtimes": {"deno": {}, "node": {}}
```

Sem isso a extração devolve só as imagens da miniatura e o download morre com
`Requested format is not available` — uma mensagem que não dá nenhuma pista da
causa real.

**Vídeos embutidos.** Quando o vídeo está num `<iframe>` apontando para
`youtube.com/embed/<id>`, a URL da aba não serve. A extensão injeta um script
que varre os `<iframe>` e pega o id no `src`. Funciona mesmo o iframe sendo de
outro domínio, porque o atributo `src` pertence ao documento de cima.

**O container roda como root.** É deliberado: a pasta de saída é um bind mount
do host, e um usuário não-root dentro do container esbarraria em permissão
dependendo de como o host mapeia o volume. Como o serviço só é alcançável de
`127.0.0.1` e é a sua própria máquina, a troca vale a pena.

**O Node vem de uma imagem `bookworm`, não `trixie`.** Binário compilado contra
uma glibc mais antiga roda na mais nova; o contrário, não. Como a base Python é
trixie, o Node precisa ser o mais velho dos dois.

## Segurança

O servidor é alcançável só de `127.0.0.1` — no Docker, quem garante isso é o
`127.0.0.1:` na frente do publish da porta, no `compose.yaml`. **Publicar como
`8756:8756` exporia o servidor para a rede local inteira** e derrubaria a
premissa de todo o resto.

O que impede uma página aberta no navegador de mandar requisições para ele são
duas regras combinadas:

1. **Recusa qualquer `Origin` de site** (`http://` ou `https://`). Uma página
   web sempre manda `Origin` numa requisição cross-origin. A extensão *não*
   manda — o Chrome dispensa o CORS para hosts declarados em `host_permissions`.
2. **Exige o cabeçalho `X-YTDL-Client: extension`.** Para enviar um cabeçalho
   customizado, uma página web precisa passar por um preflight, e o preflight
   carrega `Origin` — barrado pela regra 1. Isso fecha também o buraco das
   *simple requests* (um `POST` com `text/plain` não gera preflight).

Além disso, só URLs de vídeo do YouTube passam da validação.

O que isso **não** protege: outro programa rodando na sua máquina pode falar
com o servidor à vontade, já que basta mandar o cabeçalho. Para um ambiente
onde isso importe, o caminho seria um token compartilhado; aqui não vale a
fricção, porque um programa hostil rodando como você já teria acesso aos seus
arquivos de qualquer jeito.

Um ponto a ter em mente: os cookies enviados são os da sua sessão logada do
YouTube, então o `yt-dlp` age como você. Se preferir não envolver sua conta,
use um perfil separado do Chrome, deslogado — o bot-check costuma passar com os
cookies de visitante.

## Testes

```bash
cd server
pip install -r requirements-dev.txt
pytest -q
```

O lint é do repositório inteiro, configurado no `pyproject.toml` da raiz:

```bash
ruff check .
ruff format .
```

Os testes não tocam a rede: sobem o servidor de verdade numa porta efêmera e
falam com ele por HTTP. O foco é o que já quebrou ou o que só foi validado à mão — as
regras de aceitação de cliente, o formato do arquivo de cookies, a validação de
URL e a cadeia de formatos.

## Se der erro

| Mensagem | O que fazer |
|---|---|
| `Sign in to confirm you're not a bot` | Faça login no YouTube nesse perfil do Chrome; a extensão usa esses cookies. |
| `Video unavailable` num vídeo embutido | O dono pode ter restringido a reprodução ao site de origem. Tente abrir o vídeo direto no YouTube. |
| `Nenhum vídeo do YouTube nesta página` | O player pode não ser do YouTube, ou o iframe só aparece depois de dar play — dê play e reabra a popup. |
| `Requested format is not available` | Quase sempre é o desafio JS. No Docker não deveria acontecer; no modo nativo, confira se o `node` está no PATH e se o `yt-dlp-ejs` está instalado. O erro vem com os avisos do `yt-dlp` e a lista de formatos. |
| `403` no log do servidor | Versões desencontradas: reinicie o servidor e clique em ⟳ na extensão. |
| `A porta 8756 já está em uso` | Já tem um servidor rodando (talvez um container). `docker compose down` ou feche a janela do `.bat`. |
| Erro de extração qualquer | Atualize o `yt-dlp` (abaixo). |

## Quando parar de funcionar

O YouTube muda o formato regularmente e o `yt-dlp` corre atrás. Quase todo erro
de extração se resolve atualizando:

- **Docker**: `docker compose restart` (o entrypoint atualiza na subida)
- **Nativo**: `python -m pip install --upgrade yt-dlp yt-dlp-ejs`

## Limites

- É para uso pessoal, na sua máquina. Baixar conteúdo de terceiros contraria os
  Termos de Serviço do YouTube; redistribuir material protegido é outra
  história e não é o que isto faz.
- Não funciona em vídeos com DRM (filmes alugados do YouTube).
