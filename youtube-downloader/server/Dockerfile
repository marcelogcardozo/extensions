# syntax=docker/dockerfile:1

# O Node vem por multi-stage porque o pacote do Debian esta velho demais: o
# yt-dlp precisa de um runtime JS moderno para resolver o desafio "n" do
# YouTube (via yt-dlp-ejs). Sem ele, a extracao devolve so as miniaturas.
# A imagem do Node e bookworm de proposito - binario compilado contra uma
# glibc mais antiga roda na mais nova, o contrario nao.
FROM node:22-bookworm-slim AS node

FROM python:3.13-slim

# ffmpeg junta as faixas separadas de video e audio que o YouTube entrega.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV YTDL_HOST=0.0.0.0 \
    YTDL_PORT=8756 \
    YTDL_OUTPUT_DIR=/downloads

EXPOSE 8756

# O /health so responde para quem manda o cabecalho da extensao.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request as u; \
      u.urlopen(u.Request('http://127.0.0.1:8756/health', \
      headers={'X-YTDL-Client': 'extension'}), timeout=4)" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
