# Proyecto: Ingeniería inversa / Forensics de Converter.exe (.264 -> .avi)

## Contexto
Tengo archivos de video con extensión `.264` que no logro convertir correctamente con otros programas (incluyendo ffmpeg y scripts Python). Existe una app legacy que SÍ convierte bien:
- Ejecutable: `C:\Program Files (x86)\HiP2P Client\Converter.exe`

Archivos de prueba:
- `C:\Users\sesa443933\Videos\Borrar\P241223_142018_143018.264`
- `C:\Users\sesa443933\Videos\Borrar\P241223_143018_144017.264`

Objetivo: descubrir qué hace Converter.exe que los demás no hacen (codec/bitstream fixes/timestamps/header propietario) y reproducir el proceso para convertir correctamente con tooling moderno (idealmente ffmpeg + scripts).

## Instrucciones al Agente (Copilot)
Actúa como un ingeniero de reverse-engineering práctico. No inventes. Si falta un dato, propone cómo obtenerlo. Prioriza métodos no intrusivos primero (black-box), luego deep dive (dlls, strings, tracing). 
NO intentes desensamblar todavía sin antes agotar la observación del comportamiento.

## Qué necesito como output (entregables)
1. **Plan de investigación** por fases (Black-box → Observabilidad → Comparación → Hipótesis → Reproducción).
2. **Checklist** ejecutable en Windows (PowerShell) con comandos concretos.
3. Un **reporte** (markdown) con:
   - Hallazgos de `ffprobe/ffmpeg` sobre los .264 y los .avi generados por Converter.exe
   - Diferencias detectadas (SPS/PPS, AnnexB vs AVCC, frame rate, timebase, timestamps, headers extra, etc.)
   - Hipótesis sobre el “secreto” de Converter.exe (sin humo)
4. Una propuesta de **ruta de solución**:
   - opción A: pipeline con ffmpeg (si posible)
   - opción B: pre-procesado del bitstream (si hace falta)
   - opción C: wrapper automatizado del Converter.exe (último recurso)
5. Si detectas dependencias (DLLs/filters/codecs), listarlas con evidencia.

## Restricciones
- No destruyas archivos fuente.
- Todo lo que genere (logs, reportes) debe guardarse en una carpeta de trabajo:
  `C:\Users\sesa443933\Videos\Borrar\investigation\`
- Minimiza “magia”. Cada paso debe tener razón de existir.
- No asumas que `.264` es H.264 puro; puede tener header propietario.

---

# FASE 0 — Preparación (Carpeta y baseline)
## Acciones
- Crea carpeta de trabajo y subcarpetas:
  - `logs\`
  - `outputs\`
  - `reports\`
- Copia los dos `.264` ahí (o trabaja con rutas absolutas, pero registra hashes).
- Calcula hash (SHA256) de inputs para referencia.

## Evidencia
Guardar:
- `reports\hashes.md` con hashes y tamaños.

---

# FASE 1 — Identificación del bitstream con ffmpeg/ffprobe
## Para cada `.264`
1) Ejecutar `ffprobe` con verbose (si aplica).
2) Ejecutar `ffmpeg -loglevel debug` intentando detectar stream.
3) Probar variaciones típicas:
   - forzar formato (`-f h264`)
   - intentar saltar bytes iniciales (`-skip_initial_bytes 512/1024/2048/4096`)
   - detectar si faltan SPS/PPS o si hay NAL inválidos

## Evidencia
Guardar logs completos en:
- `logs\ffprobe_<filename>.log`
- `logs\ffmpeg_debug_<filename>.log`

---

# FASE 2 — Conversión con Converter.exe (caja negra) + recolección
## Acciones
1) Ejecutar Converter.exe manualmente con cada archivo y producir .avi (en outputs).
2) Registrar:
   - parámetros si la UI lo muestra (fps, resolución, audio, etc.)
   - nombre/ubicación del output
3) Analizar cada AVI generado:
   - `ffprobe -show_streams -show_format`
   - extraer metadata relevante (codec_tag, codec_name, pix_fmt, time_base, avg_frame_rate)

## Evidencia
Guardar:
- `logs\ffprobe_output_<avi>.log`
- `reports\avi_analysis.md` con resumen.

---

# FASE 3 — Observabilidad del proceso (sin desensamblar)
## Objetivo
Detectar qué librerías, codecs o pipelines usa Converter.exe, y si genera archivos temporales o reescribe el bitstream antes de muxear.

## Acciones recomendadas
A) **DLL / Módulos cargados**
- Obtener lista de módulos DLL cargados por el proceso mientras corre la conversión.
- Buscar señales: ffmpeg libs, openh264, directshow, vfw, etc.

B) **Process Monitor (ProcMon)**
- Capturar eventos de filesystem/registry:
  - archivos temporales creados
  - lectura/escritura de codecs/filters
  - keys de codecs

C) **Strings scan**
- Extraer strings del exe (y DLLs cercanas) y buscar:
  - "H264", "SPS", "PPS", "AnnexB", "AVCC", "AVI", "DirectShow", "VFW", "codec", etc.

## Evidencia
Guardar:
- `logs\modules_<run>.txt`
- `logs\procmon_<run>.pml` (y export CSV si aplica)
- `reports\strings_findings.md`

---

# FASE 4 — Comparación y hipótesis
## Comparar:
- Input `.264` (estructura esperada/real)
- Output `.avi` (codec + muxing)
- Errores y pistas de logs

## Hipótesis típicas a validar
- El `.264` tiene header propietario (se deben saltar N bytes)
- Falta SPS/PPS al inicio (Converter.exe los inyecta o los recupera)
- Timestamps/fps no existen (Converter.exe los sintetiza)
- El stream no está en AnnexB “normal” (o trae NALs raros)
- El output AVI usa un fourcc específico o VFW/DirectShow

## Evidencia
Generar `reports\hypotheses.md` con:
- hipótesis
- evidencia a favor/en contra
- experimento para confirmar

---

# FASE 5 — Reproducción (solución)
## Meta
Lograr conversión correcta SIN depender de Converter.exe, si es posible.

Proponer:
- Pipeline ffmpeg con flags adecuados
- Pre-procesador que:
  - remueva header
  - reordene / inserte SPS/PPS
  - estabilice fps/timebase
- Si no es viable: wrapper automatizado robusto para Converter.exe (batch conversion) con verificación de outputs

## Evidencia
- `reports\solution_options.md`
- lista de pasos para ejecutar conversion masiva

---

# Preguntas que debes hacerme SOLO si son bloqueantes
- ¿Converter.exe ofrece CLI o solo UI?
- ¿Los AVI generados reproducen bien en VLC/Movies & TV?
- ¿Hay audio en el stream?

---

# Primera tarea que debes ejecutar ya
1) Generar el plan en `reports\plan.md`
2) Generar la checklist de comandos en `reports\runbook.md`
3) Indicar qué herramientas necesito instaladas (ffmpeg, procmon, etc.) y cómo verificar su presencia.
