#!/bin/sh
# O yt-dlp e a dependencia que mais precisa de atualizacao: o YouTube muda o
# formato e ele corre atras, as vezes em dias. Congelado na imagem, ele vira o
# ponto fraco do Docker aqui - por isso a atualizacao acontece na subida do
# container, e nao no build.
#
# Falha de rede nao pode impedir o servidor de subir: a versao da imagem serve.
set -e

if [ "${YTDL_AUTO_UPDATE:-1}" != "0" ]; then
  echo "Atualizando yt-dlp..."
  # --root-user-action=ignore: rodar como root aqui e deliberado (a pasta de
  # saida e um bind mount do host); o aviso do pip so poluiria o log.
  pip install --quiet --no-cache-dir --root-user-action=ignore --upgrade yt-dlp yt-dlp-ejs \
    || echo "AVISO: nao consegui atualizar; seguindo com a versao da imagem."
fi

python -c "import yt_dlp; print('yt-dlp', yt_dlp.version.__version__)"
exec python server.py
