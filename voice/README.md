# Red Button Assistant

A voice-first accessibility assistant designed for blind users. One big red button on screen - tap it, speak, and the assistant responds.

## Features

- **Big Red Button**: Single-click or tap interface - no text, no menus
- **Voice Conversation**: Natural 2-way dialogue via Whisper STT and TTS
- **Vision**: Take photos, describe scenes, read text (OCR), find objects
- **Gmail**: Send and read emails via voice
- **Web Control**: Navigate websites via Playwright automation
- **Timer**: "Take a break" functionality with timer wake
- **Memory**: Imports existing Claude Code MD files
- **Conversation**: Maintains context across sessions

## Requirements

- Arch Linux (tested)
- Python 3.9+
- Local Ollama running at `http://localhost:11434`
- USB microphone
- USB speaker or audio output
- USB webcam (for vision features)

## Installation

```bash
cd red-button-assistant
chmod +x autostart/install.sh
./autostart/install.sh
```

## Configuration

1. Make sure Ollama is running and the configured models are pulled
   (defaults: `gemma4:31b-cloud` for chat/vision, `glm-5.1:cloud` for tools).
   Adjust them in `config/settings.yaml` under `dockbox` (`model`, `tools_model`, `vision_model`).

2. (Optional) Import existing Claude memories:

```bash
cp -r ~/path/to/claude/memories/* data/md_imports/
```

3. (Optional) Set up Gmail:
   - Place `credentials.json` from Google Cloud Console in `config/`
   - Run once to complete OAuth

## Usage

### Run directly:
```bash
python main.py
```

### Run with virtual environment:
```bash
./red-button-launcher.sh
```

### Start via systemd:
```bash
systemctl --user start red-button
```

### Enable autostart:
```bash
systemctl --user enable red-button
```

## Voice Commands

- **Tap button** → Speak → Assistant responds
- **"Take a break for 10 minutes"** → Silent until timer ends or button pressed
- **"What do you see?"** → Camera captures image, describes scene
- **"Read this"** → OCR on camera view
- **"Find my keys"** → Locates object, describes position
- **"Check my email"** → Reads recent Gmail
- **"Send an email to..."** → Composes and sends email
- **"Search for..."** → Opens browser, searches web
- **"What time is it?"** → System time/date
- **"Goodbye"** → Returns to idle, waits for button

## Project Structure

```
red-button-assistant/
├── config/
│   ├── settings.yaml         # App configuration
│   └── credentials.json      # Gmail OAuth (optional)
├── data/
│   ├── conversation_history/ # Session logs
│   ├── memories/             # User preferences
│   └── md_imports/           # Imported Claude files
├── voice/                    # STT/TTS/audio
├── core/                     # Assistant, conversation, LLM
├── tools/                    # Camera, Gmail, computer, etc.
├── ui/                       # Big red button interface
├── autostart/                # Installation scripts
├── main.py                   # Entry point
└── requirements.txt
```

## Troubleshooting

**No audio input:**
- Check `arecord -l` for microphone
- Check `pavucontrol` for input settings

**No camera:**
- Check `v4l2-ctl --list-devices`
- Ensure user has video group: `sudo usermod -a -G video $USER`

**Gmail not working:**
- Ensure `config/credentials.json` exists
- Run once interactively to complete OAuth

## License

MIT License - Created for accessibility.
