THE OLD HUNTERS (DLC) FOR BLOODBORNE ON PC
==========================================

Do this after the game already works with bloodborne-kit.

1. Close Bloodborne.
2. Unzip bloodborne-dlc-addon.zip (right-click > "Extract All...").
3. Move your DLC .pkg (Bloodborne The Old Hunters, a small file of about 128 KB) into the
   unzipped folder, next to 2-ADD-DLC.bat.
4. Double-click 2-ADD-DLC.bat.
   If Windows shows "Windows protected your PC": click "More info", then "Run anyway".
5. Start Bloodborne from the Desktop shortcut as usual.

The title screen looks the same as before. The DLC opens after you beat Vicar Amelia: the
Messengers in the Hunter's Dream give you the Eye of a Blood-drunk Hunter, and outside Oedon
Chapel an Amygdala takes you to the Hunter's Nightmare.
Make a copy of C:\bbport\user (your saves) before you go in.

This is experimental and not yet confirmed in game: if the Messengers do not give you the Eye
after Vicar Amelia, it did not work. Take a screenshot and send it.

To remove it: copy C:\bbport\out\bb-probe.exe.before-dlc over C:\bbport\out\bb-probe.exe, then
run this in a command window:
   setx BB_ADDCONT ""
Do not remove it while your character is inside the DLC areas.
