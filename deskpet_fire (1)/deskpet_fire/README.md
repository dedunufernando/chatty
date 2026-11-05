# DeskPet - fire and water desktop pets that chat

Two cute desktop pets that float on top of your screen, remind you to drink water, run simple commands, and send live messages to each other. Everything runs on your own computers: no API keys and no cloud server. Tailscale (free) lets two PCs on different Wi-Fi networks reach each other directly.

Fire is the host (waits for a connection). Water is the client (connects to fire's Tailscale IP).

## Setup

1. Install Python 3 and run `pip install -r requirements.txt`.
2. Install Tailscale on both PCs, sign in, and share the network so each PC can see the other. Fire's Tailscale IP looks like 100.x.y.z.
3. Run `python main.py`. On first run it asks which pet this PC is, the fire PC's Tailscale IP (water only), and a shared secret. Use the same secret on both PCs.
4. On the fire PC, allow Python through Windows Firewall when asked (port 50505).

## Using it

Double-click the pet to type, right-click for the menu, drag to move. Anything that is not a known command is sent to the other pet as a message. Use `say <text>` to force a message. Other commands: `help`, `status`, `history`, `search <text>`, `youtube <text>`, `open google|github|notepad`, `remind me to <x> in 10 minutes`, `timer 5 minutes`, `water every 30 minutes`, `note <text>`, `show notes`, `cpu`, `ram`, `battery`, `time`, `date`, `joke`, `quit`.

Settings, notes and chat history live in `~/.deskpet/`.

## Notes

The fire and water artwork is by the artist credited in the original image (MRTIDAMA), so keep the `assets` folder out of any public repository unless you have permission. The `.gitignore` already excludes it.

## Ideas for later

Typing indicator, emoji reactions, pets that wave when the other is online, Pomodoro mode, 20-20-20 eye-rest reminders, offline voice with Vosk, a local AI brain through Ollama.
