# -*- coding: utf-8 -*-
"""Sondea el formato binario de un chunk externo (tiles) de un .clip."""
import sys, zlib
from pathlib import Path

clip = Path(sys.argv[1])
data = clip.read_bytes()

# mapa extrnlid -> datos
chunks = {}
pos = data.find(b'CHNKExta')
while pos != -1:
    idlen = int.from_bytes(data[pos+16:pos+24], 'big')
    extid = bytes(data[pos+24:pos+24+idlen]).decode('ascii', 'ignore')
    dlen = int.from_bytes(data[pos+24+idlen:pos+32+idlen], 'big')
    dstart = pos + 32 + idlen
    chunks[extid] = (dstart, dlen)
    pos = data.find(b'CHNKExta', pos+8)

# elegir chunk: argv[2] o el mas grande
if len(sys.argv) > 2:
    key = [k for k in chunks if sys.argv[2] in k][0]
else:
    key = max(chunks, key=lambda k: chunks[k][1])
start, length = chunks[key]
blob = data[start:start+length]
print("Chunk %s  (%d bytes)" % (key, length))
print("Primeros 96 bytes:")
print(" ", blob[:96].hex(' '))

# buscar posibles cabeceras zlib
zcands = []
for i in range(len(blob)-1):
    if blob[i] == 0x78 and blob[i+1] in (0x01, 0x5e, 0x9c, 0xda):
        if (blob[i]*256 + blob[i+1]) % 31 == 0:
            zcands.append(i)
print("\nCandidatos zlib: %d  (primeros offsets: %s)" % (len(zcands), zcands[:12]))

print("\nIntentos de descompresion (offset -> tamano):")
for off in zcands[:8]:
    pre = int.from_bytes(blob[off-4:off], 'big') if off >= 4 else -1
    try:
        d = zlib.decompressobj()
        out = d.decompress(blob[off:])
        print("  off %-8d  u32_prev=%-10d  descomprimido=%d bytes  (sobran %d)"
              % (off, pre, len(out), len(d.unused_data)))
    except Exception as e:
        print("  off %-8d  fallo: %s" % (off, e))
