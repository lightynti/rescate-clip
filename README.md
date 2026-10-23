# Rescate CLIP

Exporta dibujos de **Clip Studio Paint** (`.clip`) a **PNG a resolución completa**
leyendo el archivo directamente, sin abrir Clip Studio.

También encuentra sola la carpeta de **autoguardados de recuperación** de Clip Studio,
así que sirve para recuperar un dibujo que quedó en un autoguardado.

> 🤖 **Hecho con IA.** Este programa, el análisis del formato `.clip` y esta
> documentación se hicieron con inteligencia artificial (**Claude**, de Anthropic),
> guiada y probada por su autor con dibujos reales.

<p align="center"><img src="icono.png" width="96" alt="ícono"></p>

## Descargar

En **[Releases](../../releases)**:

- **`RescateCLIP_v1.3.zip`** (recomendado): descomprimilo y abrí `RescateCLIP.exe`.
  Dejá la carpeta `_internal` al lado del `.exe`.
- **`RescateCLIP.exe`**: un solo archivo, más cómodo. Algunos antivirus lo marcan
  por error (es un falso positivo típico de los programas hechos con PyInstaller).

No necesita instalar nada. Funciona en Windows 10/11 de 64 bits.

> El programa no está firmado, así que Windows puede mostrar *"Windows protegió su PC"*.
> Se abre con **Más información → Ejecutar de todas formas**.

## ⏱️ Recomendado: bajá el intervalo de autoguardado de Clip Studio

Rescate CLIP lee los **autoguardados de recuperación** que Clip Studio escribe solo cada
cierto tiempo. Lo que dibujaste después del último autoguardado todavía no está en
ningún archivo, así que **no puede aparecer en la exportación**.

Para que funcione mejor, en Clip Studio abrí las **Preferencias**, sección **Archivo**, y
**bajá el intervalo de guardado de los datos de recuperación** al mínimo que te resulte
cómodo. Así el autoguardado está siempre al día. (El nombre exacto de la opción cambia
un poco según la versión de Clip Studio.)

En el programa, al elegir un archivo se ve **hace cuánto se guardó**; si es viejo, esperá
al próximo autoguardado y tocá **Actualizar lista**.

## Uso

### Con ventana

1. Abrí `RescateCLIP.exe`. A la izquierda aparecen los autoguardados de Clip Studio,
   el más reciente arriba.
2. Elegí uno (o **Abrir otro .clip...** para cualquier archivo) y vas a ver la vista previa,
   el tamaño real del lienzo y hace cuánto se guardó.
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

## Qué respeta de tus capas

- Capas visibles y ocultas, opacidad y orden.
- **Los 28 modos de fusión** de Clip Studio: Multiplicar, Superponer, Trama, Luz suave,
  Sobreexponer color, Color, Luminosidad, Dividir, etc.
- **Carpetas** con opacidad o modo de fusión propio (se componen aparte, como en Clip Studio).
- **Recortar con la capa inferior** (implementado, pero sin probar con un dibujo real).

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
`LayerVisibility & 1`, su opacidad es `LayerOpacity / 256` y `LayerClip` indica que
está recortada con la capa inferior.

**`LayerComposite`** (modo de fusión) es la posición en el menú de Clip Studio, contando
desde 0:

| # | Modo | # | Modo | # | Modo | # | Modo |
|---|---|---|---|---|---|---|---|
| 0 | Normal | 7 | Aclarar | 14 | Superponer | 21 | Mezcla definida |
| 1 | Oscurecer | 8 | Trama | 15 | Luz suave | 22 | Exclusión |
| 2 | Multiplicar | 9 | Sobreexponer color | 16 | Luz fuerte | 23 | Tono |
| 3 | Subexponer color | 10 | Sobreexponer (brillo) | 17 | Diferencia | 24 | Saturación |
| 4 | Subexposición lineal | 11 | Añadir | 18 | Luz intensa | 25 | Color |
| 5 | Sustraer | 12 | Añadir (brillo) | 19 | Luz lineal | 26 | Luminosidad |
| 6 | Color más oscuro | 13 | Color más claro | 20 | Luz focal | 27 | Dividir |

Clip Studio no lo documenta: los códigos 6, 13 y 26 se confirmaron comparando contra la
miniatura, y caen justo en su lugar del menú. Si aparece un código fuera de la tabla, el
programa prueba todos los modos contra la miniatura, se queda con el que mejor la
reproduce y lo recuerda para la próxima.

Clip Studio guarda además una miniatura ya aplanada en `CanvasPreview` (en general a
menor resolución), que el programa usa para medir qué tan fiel salió la exportación.
El aplanado a resolución completa del lienzo **no** se suele guardar, por eso el
programa compone las capas.

## Limitaciones

- **Máscaras de capa**: no se aplican todavía.
- Los modos **Sobreexponer (brillo)** y **Añadir (brillo)** usan la fórmula de sus
  versiones comunes; con capas semitransparentes pueden verse un poco distintos.
- Las capas de **relleno** sin píxeles (como *Papel*) se toman como fondo blanco.
- Las capas se ubican en la esquina superior izquierda del lienzo; una capa que se
  movió fuera de los bordes podría salir corrida.

En los dibujos de prueba (hasta 37 capas, 4K) la diferencia con la miniatura de Clip
Studio queda alrededor de 1/255 o menos.

## Carpeta `investigacion/`

Los scripts que se usaron para descifrar el formato (sondear chunks, estadísticas por
plano, orden de capas, la reconstrucción con calibración contra la miniatura y la
deducción de los códigos de modo de fusión). No hacen falta para usar el programa.

## Licencia

[MIT](LICENSE)
