# Rescate CLIP

Exporta dibujos de **Clip Studio Paint** (`.clip`) a **PNG a resolución completa**
leyendo el archivo directamente, sin abrir Clip Studio.

También encuentra sola la carpeta de **autoguardados de recuperación** de Clip Studio,
así que sirve para recuperar un dibujo que quedó en un autoguardado.

<p align="center"><img src="icono.png" width="96" alt="ícono"></p>

## Descargar

En **[Releases](../../releases)**:

- **`RescateCLIP_v1.2.zip`** (recomendado): descomprimilo y abrí `RescateCLIP.exe`.
  Dejá la carpeta `_internal` al lado del `.exe`.
- **`RescateCLIP.exe`**: un solo archivo, más cómodo. Algunos antivirus lo marcan
  por error (es un falso positivo típico de los programas hechos con PyInstaller).

No necesita instalar nada. Funciona en Windows 10/11 de 64 bits.

> El programa no está firmado, así que Windows puede mostrar *"Windows protegió su PC"*.
> Se abre con **Más información → Ejecutar de todas formas**.

## Uso

### Con ventana

1. Abrí `RescateCLIP.exe`. A la izquierda aparecen los autoguardados de Clip Studio,
   el más reciente arriba.
2. Elegí uno (o **Abrir otro .clip...** para cualquier archivo) y vas a ver la vista previa
   y el tamaño real del lienzo.
3. **Exportar PNG a resolución completa...** y elegí dónde guardarlo.
   Con **Fondo transparente** sale sin el papel blanco.

Si no encuentra la carpeta de autoguardados te dice dónde debería estar, y con
**Elegir carpeta de autoguardados...** la podés indicar a mano (se recuerda).

Al terminar muestra la diferencia media con la miniatura que guarda Clip Studio
(0–255): cuanto más cerca de 0, más fiel.

### Por consola

```bash
python rescate_clip.py dibujo.clip salida.png
```

## Desde el código

```bash
pip install -r requirements.txt
python rescate_clip.py
```

Para armar los ejecutables: `compilar.bat` (deja todo en `dist\`).

## Cómo funciona: el formato `.clip`

Un `.clip` es un contenedor con chunks (`CSFCHUNK`):

- Una **base SQLite** embebida (empieza con `SQLite format 3`) con la descripción del
  lienzo y las capas: tablas `Canvas`, `Layer`, `Mipmap`, `MipmapInfo`, `Offscreen`,
  `CanvasPreview`...
- Chunks **`CHNKExta`** con los píxeles. Cada uno tiene un id `extrnlid…` y los datos.

De una capa a sus píxeles:

```
Layer.LayerRenderMipmap → Mipmap.BaseMipmapInfo → MipmapInfo.Offscreen (escala 100 %)
  → Offscreen.Attribute (ancho/alto tras "Parameter") + Offscreen.BlockData = "extrnlid…"
```

Los píxeles van en **tiles de 256×256**, cada uno en un bloque
`BlockDataBeginChunk` … `BlockDataEndChunk` (nombres en UTF-16BE). Después del nombre
hay un `u32` con el índice del tile en la grilla (fila × ancho + columna), una cabecera
de 28 bytes en total y luego los datos comprimidos con **zlib**. Un tile descomprimido
mide 256×256×5 bytes:

- primero un **plano de alfa** de 256×256,
- después el **color BGRX intercalado** de 256×256×4.

Orden de las capas: `Canvas.CanvasRootFolder` → `LayerFirstChildIndex` y luego
`LayerNextIndex` (referencian `MainId`). La primera es la de abajo. Una capa se ve si
`LayerVisibility & 1`, y su opacidad es `LayerOpacity / 256`.

Clip Studio guarda además una miniatura ya aplanada en `CanvasPreview` (en general a
menor resolución), que el programa usa para medir qué tan fiel salió la exportación.
El aplanado a resolución completa del lienzo **no** se suele guardar, por eso el
programa compone las capas.

## Limitaciones

- Los **modos de fusión** (multiplicar, superponer...) se componen como *Normal*.
- No se aplican **máscaras de capa**, **recortes a la capa inferior** ni la opacidad
  o el modo de fusión de las **carpetas**.
- Las capas de **relleno** sin píxeles (como *Papel*) se toman como fondo blanco.
- Las capas se ubican en la esquina superior izquierda del lienzo; una capa que se
  movió fuera de los bordes podría salir corrida.

En la práctica, en un dibujo de 37 capas la diferencia con la miniatura de Clip Studio
fue de ~1/255. Si en tu dibujo es alta, alguna de estas cosas está pesando.

## Carpeta `investigacion/`

Los scripts que se usaron para descifrar el formato (sondear chunks, ver estadísticas
por plano, el orden de capas, la primera reconstrucción con calibración contra la
miniatura). No hacen falta para usar el programa.

## Licencia

[MIT](LICENSE)
