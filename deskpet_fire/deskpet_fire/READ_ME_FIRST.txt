DeskPet - FIRE package (the host)

1. Install Python from python.org/downloads. Tick "Add python.exe to PATH" in the installer.
2. Install Tailscale (tailscale.com/download) and sign in. Fire's Tailscale IP
   (100.x.y.z) must match "host_ip" in the water package's gift_config.json.
3. Double-click start.bat. The first run sets itself up (about a minute), then the
   fire pet appears and will start with Windows from now on. No CMD window stays open.
4. When Windows Firewall asks, allow Python (port 50505), otherwise water cannot connect.

Double-click the pet to type; it hides after 3 seconds if you type nothing.
Right-click for the menu. The shared secret in gift_config.json ("hope") must be
the same on both PCs.
