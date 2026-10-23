# -*- coding: utf-8 -*-
"""Mapea los marcadores UTF-16 y la estructura por-tile de un chunk externo."""
import sys, zlib
from pathlib import Path

clip = Path(sys.argv[1])
data = clip.read_bytes()

chunks = {}
pos = data.find(b'CHNKExta')
while pos != -1:
    idlen = int.from_bytes(data[pos+16:pos+24], 'big')
    extid = bytes(data[pos+24:pos+24+idlen]).decode('ascii', 'ignore')
    dlen = int.from_bytes(data[pos+24+idlen:pos+32+idlen], 'big')
    chunks[extid] = (pos+32+idlen, dlen)
    pos = data.find(b'CHNKExta', pos+8)

key = max(chunks, key=lambda k: chunks[k][1])
start, length = chunks[key]
blob = data[start:start+length]
print("Chunk %s (%d bytes)\n" % (key, length))

# marcadores UTF-16BE que empiezan con "Block"
needle = b'\x00B\x00l\x00o\x00c\x00k'
offs = []
i = blob.find(needle)
while i != -1:
    offs.append(i)
    i = blob.find(needle, i+1)
print("Marcadores 'Block*' encontrados:", len(offs))
for o in offs[:8]:
    nlen = int.from_bytes(blob[o-4:o], 'big')
    name = blob[o:o+nlen*2].decode('utf-16-be', 'ignore')
    after = blob[o+nlen*2:o+nlen*2+40]
    print("  @%-8d len=%-3d %-22s  sig=%s" % (o, nlen, name, after.hex(' ')))

# caminar los primeros registros de tile asumiendo:
# [u32 uncompSize][u32 w][u32 h][u32 flag][u32 compSize][zlib...]
print("\nCaminando tiles desde el primer zlib:")
# el primer registro empieza tras el nombre BlockDataBeginChunk
first_name_len = int.from_bytes(blob[4:8], 'big')
p = 8 + first_name_len*2
tilecount = 0
while p < len(blob) and tilecount < 12:
    # buscar el proximo zlib valido a <=64 bytes vista, registrando el header
    hdr_start = p
    z = -1
    for k in range(p, min(p+80, len(blob)-1)):
        if blob[k] == 0x78 and blob[k+1] in (0x01,0x5e,0x9c,0xda) and (blob[k]*256+blob[k+1])%31==0:
            z = k; break
    if z == -1:
        print("  no mas zlib desde @%d" % p); break
    hdr = blob[hdr_start:z]
    try:
        d = zlib.decompressobj()
        out = d.decompress(blob[z:])
        consumed = len(blob)-z-len(d.unused_data)
        print("  tile %2d  hdr(%d B)=%s  ->desc=%d (=%d*65536)  comp=%d"
              % (tilecount, len(hdr), hdr.hex(' '), len(out), len(out)//65536, consumed))
        p = z + consumed
    except Exception as e:
        print("  tile %2d  zlib@%d fallo: %s" % (tilecount, z, e)); break
    tilecount += 1
