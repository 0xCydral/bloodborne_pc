BLOODBORNE DOES NOT OPEN ANY MORE
=================================

No need to reinstall. Your saves, the game and the DLC stay.

1. Close Bloodborne.
2. Unzip bloodborne-repair.zip (right-click > "Extract All...").
3. Double-click REPAIR.bat.
   If Windows shows "Windows protected your PC": click "More info", then "Run anyway".
4. In the setup window that opens, leave Upscaler on "Off", press "Save settings"
   (NOT "Install / Update"), then "Play".
5. Send the file bloodborne-report.txt from your Desktop to the person who gave you the kit.

Keep the upscaler Off for now, also in the game menu (Insert / L3+R3).
Without the GPU cache the game may stutter for a moment the first time it shows a new area.

If the game still crashes, a black window stays open with text: take a photo of it and send it.

What it does: it renames the GPU cache (C:\bbport\user\cache) and the settings file
(C:\bbport\bbport.ini) so the game makes new ones, turns the GPU cache off and sets the upscaler
to Off. Nothing is deleted: the old files stay next to them with "before-repair" in the name.
To turn the GPU cache back on later, run this in a command window:  setx BB_PIPELINE_CACHE 1
