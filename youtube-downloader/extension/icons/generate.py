import struct, zlib

R = 0.20  # raio do canto arredondado, em fracao do lado

def dentro_da_forma(fx, fy):
    cx = min(max(fx, R), 1 - R)
    cy = min(max(fy, R), 1 - R)
    return ((fx - cx) ** 2 + (fy - cy) ** 2) <= R * R

def eh_seta(fx, fy):
    if 0.43 <= fx <= 0.57 and 0.16 <= fy <= 0.50:      # haste
        return True
    if 0.50 <= fy <= 0.72:                              # ponta
        return abs(fx - 0.5) <= 0.28 * (0.72 - fy) / 0.22
    if 0.79 <= fy <= 0.87 and 0.26 <= fx <= 0.74:       # base
        return True
    return False

def px(n, x, y):
    # 4x supersampling para suavizar bordas
    r = g = b = a = 0.0
    for sy in range(4):
        for sx in range(4):
            fx, fy = (x + (sx + 0.5) / 4) / n, (y + (sy + 0.5) / 4) / n
            if not dentro_da_forma(fx, fy):
                continue
            a += 1
            if eh_seta(fx, fy):
                r += 255; g += 255; b += 255
            else:
                r += 204
    if a == 0:
        return (0, 0, 0, 0)
    return (round(r / a), round(g / a), round(b / a), round(a / 16 * 255))

def chunk(tipo, dados):
    return (struct.pack(">I", len(dados)) + tipo + dados
            + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF))

for n in (16, 48, 128):
    raw = b"".join(
        b"\x00" + b"".join(bytes(px(n, x, y)) for x in range(n))
        for y in range(n)
    )
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    open(f"icon{n}.png", "wb").write(png)
    print(f"icon{n}.png ok ({len(png)} bytes)")
