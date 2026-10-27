"""Gera os icones da extensao, sem depender de nenhuma biblioteca de imagem.

A marca funde as duas ideias que a extensao junta: o triangulo de "play" -
que e como todo player de video se anuncia, YouTube inclusive - virado para
baixo, pousando sobre uma base. O mesmo desenho le "video" e "baixar".

O que ele NAO tem, de proposito, e a haste vertical do glifo universal de
download: e ela que faz um icone parecer o botao de baixar de qualquer site.
Sem a haste, sobra uma silhueta cheia, que e o que resiste a 16px - o unico
tamanho que realmente importa, porque e o da barra do navegador.

As formas sao definidas por SDF (funcao de distancia com sinal), o que da tres
coisas de graca: cantos arredondados de verdade, antialiasing analitico (sem
supersampling) e um jeito simples de mexer nas proporcoes sem redesenhar nada.

    python generate.py
"""

import struct
import zlib

TAMANHOS = (16, 48, 128)

# ------------------------------------------------------------------ desenho

FUNDO_RAIO = 0.23  # canto do quadrado, em fracao do lado

# Gradiente vertical sutil: some a 16px, da profundidade a 128px.
TOPO = (237, 28, 28)
BASE = (186, 0, 0)

# Triangulo de play virado para baixo. Estes sao os vertices do nucleo: o
# arredondamento expande a forma para fora, entao a silhueta final e maior
# que o triangulo escrito aqui.
TRIANGULO = ((0.345, 0.270), (0.655, 0.270), (0.500, 0.570))
TRIANGULO_RAIO = 0.036

# A base onde o triangulo pousa. Raio = metade da altura, entao vira pilula.
BARRA_CENTRO = (0.500, 0.747)
BARRA_METADE = (0.200, 0.046)
BARRA_RAIO = 0.046


def sdf_retangulo(x, y, centro, metade, raio):
    """Distancia com sinal ate um retangulo de cantos arredondados."""
    dx = abs(x - centro[0]) - metade[0] + raio
    dy = abs(y - centro[1]) - metade[1] + raio
    fora = (max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2) ** 0.5
    dentro = min(max(dx, dy), 0.0)
    return fora + dentro - raio


def planos(vertices):
    """Semiplanos de um poligono convexo, com as normais apontando para fora.

    A orientacao dos vertices nao importa: a normal de cada aresta e conferida
    contra o centroide e invertida quando aponta para dentro.
    """
    n = len(vertices)
    cx = sum(v[0] for v in vertices) / n
    cy = sum(v[1] for v in vertices) / n

    saida = []
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        nx, ny = by - ay, -(bx - ax)
        comprimento = (nx * nx + ny * ny) ** 0.5
        nx, ny = nx / comprimento, ny / comprimento
        if nx * (cx - ax) + ny * (cy - ay) > 0:
            nx, ny = -nx, -ny
        saida.append((nx, ny, nx * ax + ny * ay))
    return saida


def sdf_convexo(x, y, semiplanos, raio):
    """Poligono convexo arredondado: o maximo dos semiplanos, encolhido."""
    return max(nx * x + ny * y - d for nx, ny, d in semiplanos) - raio


def cobertura(distancia, lado):
    """Quanto do pixel a forma ocupa, a partir da distancia ate a borda.

    E isto que faz o antialiasing: a distancia esta em fracao do lado, entao
    multiplicar por `lado` da a distancia em pixels, e meio pixel de cada lado
    da borda vira a rampa de transparencia.
    """
    return min(max(0.5 - distancia * lado, 0.0), 1.0)


def pixel(lado, x, y, semiplanos):
    fx, fy = (x + 0.5) / lado, (y + 0.5) / lado

    alfa = cobertura(sdf_retangulo(fx, fy, (0.5, 0.5), (0.5, 0.5), FUNDO_RAIO), lado)
    if alfa <= 0.0:
        return (0, 0, 0, 0)

    glifo = max(
        cobertura(sdf_convexo(fx, fy, semiplanos, TRIANGULO_RAIO), lado),
        cobertura(sdf_retangulo(fx, fy, BARRA_CENTRO, BARRA_METADE, BARRA_RAIO), lado),
    )

    canais = []
    for topo, base in zip(TOPO, BASE, strict=True):
        vermelho = topo + (base - topo) * fy
        canais.append(round(vermelho + (255 - vermelho) * glifo))
    return (*canais, round(alfa * 255))


# -------------------------------------------------------------------- PNG


def bloco(tipo, dados):
    return (
        struct.pack(">I", len(dados))
        + tipo
        + dados
        + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)
    )


def png(lado):
    semiplanos = planos(TRIANGULO)
    linhas = b"".join(
        b"\x00" + b"".join(bytes(pixel(lado, x, y, semiplanos)) for x in range(lado))
        for y in range(lado)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + bloco(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 6, 0, 0, 0))
        + bloco(b"IDAT", zlib.compress(linhas, 9))
        + bloco(b"IEND", b"")
    )


if __name__ == "__main__":
    for lado in TAMANHOS:
        dados = png(lado)
        with open(f"icon{lado}.png", "wb") as arquivo:
            arquivo.write(dados)
        print(f"icon{lado}.png ok ({len(dados)} bytes)")
