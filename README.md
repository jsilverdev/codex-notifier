# Codex Notifier

Notificador de finalización para Codex en Windows y Linux. Envía una Adaptive
Card a Microsoft Teams y puede reproducir un aviso por voz cuando el turno
supera los umbrales configurados.

No requiere paquetes de `pip`; usa únicamente Python 3 y herramientas del
sistema operativo.

## Comportamiento predeterminado

La configuración inicial se copia literalmente desde `config.example.json`:

- Teams desactivado hasta añadir un webhook y cambiar `enabled` a `true`.
- Teams a partir de 300 segundos.
- Los resúmenes largos conservan el inicio y los últimos 600 caracteres dentro
  de un máximo de 1.800 caracteres.
- Voz a partir de 65 segundos.
- Silencio de voz entre las 23:00 y las 07:00.
- Idioma de voz automático según el mensaje final.
- Nunca se guarda el prompt; el estado temporal contiene solo identificadores,
  directorio y hora de inicio.

## Requisitos

- Python 3.10 o posterior.
- Codex instalado y un `~/.codex/config.toml` accesible.
- Windows: SAPI, incluido normalmente con Windows.
- Linux, solo para voz: `spd-say` (Speech Dispatcher), `espeak-ng` o `espeak`.
  Teams funciona aunque no haya motor de voz.

`mise` no es obligatorio. El instalador busca Python 3 en este orden:

1. `python3`.
2. En Windows, `py -3`.
3. `python` si realmente es Python 3.
4. `mise exec -- python` si no encontró un Python independiente.
5. El intérprete con el que se inició `install.py`.

## Instalación única

Hay un único instalador multiplataforma: `install.py`.

Windows:

```powershell
python install.py
```

También puedes iniciarlo con:

```powershell
py -3 install.py
mise exec -- python install.py
```

Linux:

```bash
python3 install.py
```

Si Python solo está administrado por mise:

```bash
mise exec -- python install.py
```

El instalador realiza las tres tareas:

1. Configura `notify` en `~/.codex/config.toml`.
2. Añade o actualiza el hook `UserPromptSubmit` en `~/.codex/hooks.json`, sin
   eliminar otros hooks.
3. Si todavía no existe, copia `config.example.json` a la ubicación local de
   configuración.

Los archivos de Codex existentes reciben una copia `.bak` antes de modificarse.
La configuración local existente nunca se sobrescribe automáticamente. Después
de cambiar el hook, abre un chat nuevo y usa `/hooks` para revisarlo y confiar
en él.

Opciones útiles:

```text
--codex-home RUTA          CODEX_HOME alternativo
--config-path RUTA         config.json alternativo
--python-executable RUTA   Python 3 específico para notify y el hook
--force-config             reemplaza config.json desde el ejemplo, con backup
```

Ten cuidado con `--force-config`: reemplaza el webhook y las preferencias
locales por los valores del ejemplo.

## Ubicación de la configuración

Windows:

```text
%LOCALAPPDATA%\CodexNotifier\config.json
```

Linux:

```text
$XDG_CONFIG_HOME/codex-notifier/config.json
```

Si `XDG_CONFIG_HOME` no existe:

```text
~/.config/codex-notifier/config.json
```

Se puede usar otra ruta definiendo `CODEX_NOTIFIER_CONFIG` o pasando
`--config-path` al instalar.

## Configuración

```json
{
  "teams": {
    "enabled": false,
    "minimum_seconds": 300,
    "summary_max_chars": 1800,
    "summary_tail_chars": 600,
    "webhook_url": ""
  },
  "voice": {
    "enabled": true,
    "minimum_seconds": 65,
    "quiet_start": "23:00",
    "quiet_end": "07:00",
    "language": "auto",
    "spanish_voice": "",
    "english_voice": ""
  }
}
```

Para activar Teams, completa ambos valores:

```json
"enabled": true,
"webhook_url": "https://tu-webhook"
```

El webhook es una credencial. El archivo local está fuera del repositorio y no
debe compartirse ni añadirse a Git.

Cuando el mensaje final supera `summary_max_chars`, la card muestra el comienzo,
un aviso con el número de caracteres omitidos y los últimos
`summary_tail_chars`. El máximo configurable se mantiene entre 100 y 6.000
caracteres para dejar margen bajo el límite total de 28 KB de Teams. Si la
configuración local existente no incluye estas propiedades, se aplican los
valores predeterminados de 1.800 y 600.

`language` admite:

- `auto`: estima español o inglés usando el mensaje final.
- `es`: siempre español.
- `en`: siempre inglés.

En Windows, `spanish_voice` y `english_voice` pueden contener parte del nombre
de una voz SAPI instalada. Si están vacíos, se elige automáticamente una voz del
idioma. Linux utiliza el código de idioma con `spd-say` o `espeak`.

## Probar la voz

Windows:

```powershell
python .\notifier.py voice-test es
python .\notifier.py voice-test en
```

Linux:

```bash
python3 ./notifier.py voice-test es
python3 ./notifier.py voice-test en
```

Estos comandos reproducen sonido. Las pruebas automatizadas no reproducen audio
ni llaman al webhook.

## Variables de entorno opcionales

El archivo JSON tiene prioridad. Si una propiedad no existe, se admiten estos
fallbacks:

| Variable | Predeterminado | Uso |
| --- | ---: | --- |
| `CODEX_TEAMS_WEBHOOK_URL` | vacío | Webhook alternativo |
| `CODEX_NOTIFIER_TEAMS_ENABLED` | según exista webhook | Activa Teams |
| `CODEX_NOTIFIER_TEAMS_MIN_SECONDS` | `300` | Umbral de Teams |
| `CODEX_NOTIFIER_VOICE_ENABLED` | `1` | Activa la voz |
| `CODEX_NOTIFIER_VOICE_MIN_SECONDS` | `65` | Umbral de voz |
| `CODEX_NOTIFIER_QUIET_START` | `23:00` | Inicio del silencio |
| `CODEX_NOTIFIER_QUIET_END` | `07:00` | Fin del silencio |
| `CODEX_NOTIFIER_STATE_DIR` | dependiente del SO | Estado temporal |
| `CODEX_NOTIFIER_CONFIG` | dependiente del SO | Ruta de configuración |

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

## Funcionamiento

`UserPromptSubmit` registra la hora de inicio y `notify` recibe el evento
`agent-turn-complete`. Ambos se correlacionan mediante `turn_id`; al terminar se
calcula la duración, se consume el marcador y se aplican los canales y el
horario configurados.
