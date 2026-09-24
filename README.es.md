# Codex Notifier

[English](README.md)

Notificaciones de finalización para Codex en Windows y Linux. Puede enviar una
Adaptive Card a Microsoft Teams, publicar un embed en Discord y reproducir una
alerta de voz local después de un tiempo configurable.

Codex Notifier usa únicamente la biblioteca estándar de Python. Toda la
configuración de los canales vive en un único archivo JSON, sin duplicarla en
variables de entorno.

## Comportamiento predeterminado

El instalador copia `config.example.json` cuando todavía no existe una
configuración local.

| Canal | Habilitado | Duración mínima | Notificar si la duración es desconocida |
| --- | --- | ---: | --- |
| Teams | No | 300 segundos | No |
| Discord | No | 300 segundos | No |
| Voz | Sí | 30 segundos | Sí |

Las alertas de voz se silencian entre las 23:00 y las 07:00. Mencionan el
proyecto, la duración y el título del chat cuando Codex lo proporciona. Teams y
Discord incluyen el mensaje final del asistente como un resumen limitado.

Cada canal se ejecuta de forma independiente: el fallo de un webhook no impide
que funcionen la voz o el otro webhook. El notificador mantiene un registro
diario respetuoso con la privacidad y nunca almacena el prompt ni el mensaje
final del asistente.

## Requisitos

- Python 3.10 o posterior.
- Codex con acceso a `~/.codex/config.toml`.
- Voz en Windows: SAPI, normalmente incluido con Windows.
- Voz en Linux: `spd-say`, `espeak-ng` o `espeak`.

Teams y Discord no necesitan un motor de voz local.

## Instalación

Ejecuta una vez el instalador multiplataforma:

```shell
python install.py
```

El instalador:

1. Configura `notify` de Codex en `~/.codex/config.toml`.
2. Añade o actualiza el hook `UserPromptSubmit` en `~/.codex/hooks.json` sin
   eliminar otros hooks.
3. Copia `config.example.json` a la ruta de configuración local si todavía no
   existe.

Los archivos existentes de Codex se respaldan antes de modificarlos. Una
configuración existente del notificador se conserva salvo que se use
explícitamente `--force-config`. Después de instalar o cambiar el hook, abre un
chat nuevo y usa `/hooks` para revisarlo y confiar en él.

Opciones del instalador:

```text
--codex-home PATH          usa otro CODEX_HOME
--config-path PATH         usa otro config.json del notificador
--python-executable PATH   usa un ejecutable concreto de Python 3
--force-config             reemplaza config.json con el ejemplo y crea respaldo
```

`--force-config` reemplaza las URL de webhook y preferencias locales con los
valores predeterminados del ejemplo.

## Configuración

Rutas predeterminadas:

- Windows: `%LOCALAPPDATA%\CodexNotifier\config.json`
- Linux con `XDG_CONFIG_HOME`: `$XDG_CONFIG_HOME/codex-notifier/config.json`
- Otros sistemas Linux: `~/.config/codex-notifier/config.json`

`config.json` es la fuente de verdad para el comportamiento de todos los
canales:

```json
{
  "teams": {
    "enabled": false,
    "minimum_seconds": 300,
    "notify_when_duration_unknown": false,
    "summary_max_chars": 1800,
    "summary_tail_chars": 600,
    "webhook_url": ""
  },
  "discord": {
    "enabled": false,
    "minimum_seconds": 300,
    "notify_when_duration_unknown": false,
    "summary_max_chars": 1800,
    "webhook_url": ""
  },
  "voice": {
    "enabled": true,
    "minimum_seconds": 30,
    "notify_when_duration_unknown": true,
    "quiet_start": "23:00",
    "quiet_end": "07:00",
    "language": "auto",
    "spanish_voice": "",
    "english_voice": ""
  },
  "logging": {
    "enabled": true,
    "retention_days": 7
  }
}
```

`minimum_seconds` es inclusivo. `notify_when_duration_unknown` omite únicamente
el umbral de duración: el canal debe seguir habilitado, los canales de webhook
necesitan una URL HTTPS válida y la voz sigue respetando el horario silencioso.

El instalador no combina claves nuevas dentro de una configuración existente.
Al actualizar, compara tu archivo local con `config.example.json` y añade
manualmente cualquier clave nueva.

### Teams

Teams usa una Adaptive Card. Al truncar, su resumen conserva el inicio y una
cola configurable; `summary_max_chars` se limita a valores entre 100 y 6000
caracteres. `summary_tail_chars` controla cuánto contenido del final se
conserva.

### Discord

Discord usa un embed, solicita confirmación de entrega, deshabilita las
menciones procedentes del resumen y limita `summary_max_chars` al máximo de
4096 caracteres admitido por la descripción del embed.

### Voz

`language` acepta:

- `auto`: detecta español o inglés a partir del mensaje final del asistente.
- `es`: siempre habla en español.
- `en`: siempre habla en inglés.

El mensaje final se usa únicamente para detectar el idioma y no se lee en voz
alta. La alerta pronunciada contiene el proyecto, la duración y el título del
chat cuando está disponible.

En Windows, `spanish_voice` y `english_voice` pueden contener parte del nombre
de una voz SAPI instalada. Los valores vacíos seleccionan automáticamente una
voz del idioma correspondiente. Linux pasa el código del idioma al motor de voz
instalado.

Configura `quiet_start` y `quiet_end` con la misma hora para deshabilitar el
horario silencioso.

## Sobrescritura de rutas

Se conservan dos variables de entorno porque seleccionan archivos, no porque
dupliquen opciones de los canales:

| Variable | Propósito |
| --- | --- |
| `CODEX_NOTIFIER_CONFIG` | Sobrescribe la ruta de `config.json` |
| `CODEX_NOTIFIER_STATE_DIR` | Sobrescribe la carpeta temporal de estado y logs |

El instalador también acepta `--config-path`, que normalmente resulta más claro
para una instalación puntual.

## Registro de trazas

El notificador escribe un archivo JSON Lines por día dentro de su carpeta de
estado:

- Windows: `%LOCALAPPDATA%\CodexNotifier\logs\notifier-YYYY-MM-DD.jsonl`
- Linux con `XDG_STATE_HOME`:
  `$XDG_STATE_HOME/codex-notifier/logs/notifier-YYYY-MM-DD.jsonl`
- Otros sistemas Linux:
  `~/.local/state/codex-notifier/logs/notifier-YYYY-MM-DD.jsonl`

Las entradas contienen únicamente la fecha y hora, el ID del chat, la duración,
el estado de los canales y detalles de error limitados. Las URL de webhook se
ocultan. No se registran prompts, respuestas ni rutas de proyectos.
`retention_days` se limita a valores entre 1 y 365; configura `logging.enabled`
como `false` para deshabilitar el registro.

## Probar la voz

```shell
python notifier.py voice-test es
python notifier.py voice-test en
```

Estos comandos reproducen audio; las pruebas automatizadas simulan las llamadas
de voz y webhook.

## Ejecutar las pruebas

```powershell
python -m unittest discover -s tests -v
```

## Cómo funciona

El hook `UserPromptSubmit` registra una marca de inicio sin guardar el prompt.
Después, Codex invoca `notify` con un evento `agent-turn-complete`. El
notificador relaciona ambos eventos mediante `turn_id`, calcula y consume la
marca de duración y evalúa cada canal de forma independiente. Si falta la marca,
cada canal aplica su propia opción `notify_when_duration_unknown`.
