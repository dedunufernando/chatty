# DeskPet

A small desktop pet that lives on your screen, reminds you to drink water, runs simple commands, and chats with a friend's pet. No accounts, no API keys, no VPN.

## Run it
Double-click `start.bat`. The first run sets up what it needs, then a short welcome guides you: pair with your friend using a code, pick an avatar, give your pet a name.

## Pairing (no Tailscale, nothing to connect)
One person chooses "I'm going first" and gets a 12-letter code. The other chooses "I already have a code" and types it in. That's all. Right-click the pet, then Friend pairing, to show the code again, pair with someone else, or unpair.

How it works: both pets make outgoing HTTPS requests to a small public relay (ntfy.sh). Your code is turned into a secret topic name and an encryption key on your own PC, so the relay only ever sees scrambled text. Messages sent while your friend is offline wait (about 12 hours) and arrive when their pet starts. Treat the code like a password. To use your own relay, put `"relay_server": "https://your-ntfy-server"` in `%USERPROFILE%\.deskpet\config.json`.

## Make it a real app (DeskPet.exe)
Double-click `build_exe.bat` once. When it finishes, your app is `dist\DeskPet\DeskPet.exe`. Zip the whole `dist\DeskPet` folder to share it. Put `gift_config.json` next to the exe to pre-fill a friend's settings (their pairing code, name and avatar).

## Gifts
Put `{"code": "XXXX-XXXX-XXXX", "nickname": "Bubbles", "avatar": "Water", "welcome_text": "..."}` in `gift_config.json`, enter the same code on your own pet, and your friend's pet is already paired the first time it opens.

## Avatars
Right-click the pet, then Avatar. Add a new one from any picture, or drag a picture onto the pet. A plain background is removed automatically. Each avatar can have its own effect: bubbles, sparks, hearts or none. Your avatars are kept in `%USERPROFILE%\.deskpet\avatars`.

## Chatting and commands
Typing sends a message to your friend. To give the pet a command, start with its name (`Ember, time`) or a slash (`/time`). Say `help` that way for the full list. Set `"chat_first": false` in the config to run commands without the name.

## Shortcut icon
On first run the pet adds a shortcut to your Desktop and Start menu. Right-click, `Add shortcut icon to Desktop` makes a new one with the current avatar as its icon.

## Size and opacity
Right-click, Size or Opacity. Ctrl + mouse wheel changes size, Ctrl + Shift + wheel changes opacity.

## If something goes wrong
Look in `%USERPROFILE%\.deskpet\error.log`.

The fire and water artwork is by MRTIDAMA and is for personal use only, which is why `assets/` is git-ignored.
