BLOODBORNE ON PC - OFFLINE INSTALL
==================================

You need:
  - Windows 10 (version 1803 or newer) or Windows 11, 64-bit.
  - About 40 GB free on drive C:
  - A graphics card with up-to-date drivers.
  - Your two Bloodborne .pkg files: the game and the v1.09 update.
  No internet connection is needed: everything else is in this kit.

Steps:
  1. Unzip bloodborne-kit.zip (right-click > "Extract All...").
  2. Move BOTH .pkg files into the unzipped folder, next to 1-START-HERE.bat.
  3. Double-click 1-START-HERE.bat.
     If Windows shows "Windows protected your PC": click "More info", then "Run anyway".
  4. Wait 20-45 minutes. It unpacks the tools to C:\msys64 and C:\bbport, then extracts the
     game to C:\Games. The window can look frozen while it works; leave it open.
  5. A "Bloodborne PC (bbport) setup" window opens with the game folder already filled in.
     Pick your resolution and frame rate, then press "Save settings".
     Do NOT press "Install / Update": that one needs the internet and rebuilds everything.
  6. Press "Play", or use the "Bloodborne (bbport)" shortcut on the Desktop.
     The first start takes a minute longer while it prepares the game files.

In the game:
  - Insert key (or L3+R3 on the controller) opens the settings menu.
  - F11 switches fullscreen.
  - DLSS works only on NVIDIA RTX cards; on other cards choose FSR in the setup.

If something fails, take a screenshot of the window with the red ERROR text and send it.
If it stops halfway, run 1-START-HERE.bat again: it continues where it stopped.
After everything works you can delete the .pkg files and this kit folder.

Note: the kit sets the Windows user variable BB_PREBUILT=1, so the game never tries to rebuild
itself before starting.
