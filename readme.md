Too Chatty 🔥💧

Opposites attract, messages too.
Two desktop pets live on two different Windows computers: a fire pet and a water pet. Double-click one, type a message, and its friend on the other computer receives it.
Quick start for new users
1. Install Python from python.org/downloads on both computers. Tick "Add python.exe to PATH" in the installer.
2. Install Tailscale from tailscale.com/download on both computers and sign in.
3. Put both computers on the same Tailscale network. Either sign in to the same Tailscale account on both, or invite your friend's account from the Tailscale admin page (login.tailscale.com, Users tab).
4. On the fire computer, find its Tailscale IP. It looks like 100.x.y.z and is shown in the Tailscale app.
5. Choose your own secret word, something long and hard to guess. Share it with your friend privately, never in this repository.
6. On both computers, open gift_config.json in Notepad and set the same secret word. On the water computer, also set host_ip to the fire computer's Tailscale IP.
7. Start the fire pet first by double-clicking start.bat in the fire folder. When Windows Firewall asks, allow Python (port 50505).
8. Then start the water pet by double-clicking start.bat in the water folder.
9. Double-click a pet, type a message, and watch it arrive on the other computer.
How it works
• Each pet is a small Python app that wanders around your screen.
• The fire pet is the host. The water pet connects to it over port 50505.
• The two computers talk through Tailscale, a private network, so there are no public servers, accounts, or API keys.
• If the fire computer is off, the water pet keeps retrying and connects as soon as fire is back online.
Folders
• deskpet_fire: the fire pet (host)
• deskpet_water_gift: the water pet (client)
Using the pets
• Double-click a pet to type a message. On the fire pet, the box hides after 3 seconds if you type nothing.
• Right-click a pet for the menu, including "Start with Windows".
Troubleshooting
• Water can't connect: check that fire is running, that both computers show as connected in Tailscale, and that host_ip matches fire's Tailscale IP.
• Still nothing: make sure the secret word is identical on both computers, and that Windows Firewall allows Python on port 50505.
Requirements
Each folder has a requirements.txt, which start.bat installs automatically.
Security note
Never commit invite links or secret words to this repository. Share them privately instead.
