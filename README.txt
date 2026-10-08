SCREENSHOT CLOTHESLINE (Tendedero)
==================================

Screenshots, hung out to dry.
Inspired by Tendedero: https://github.com/alejandrobujan/tendedero

Every screenshot you take hangs from a clothesline across the top of your screen,
pinned by realistic wooden clothespins. Out of sight, within reach.


GESTURES & CONTROLS
-------------------
  Click a card          Copy the image to your clipboard (instant toast feedback)
  Double-click a card   Open in Markup & Crop editor
  Right-click a card    Context menu (Copy, Edit, Reveal in Folder, Save As, Unclip)
  Drag a card down      Unclip and drop it off the line
  Hover top tab         Glides the clothesline down
  Hover away            Folds away smoothly after a brief delay
  Escape or ▲ button    Tuck the clothesline back up
  Pin button (📌)       Keep the clothesline pinned open permanently
  Clear button (🗑️)      Unclip all screenshots from the line
  Folder button (📁)     Open the screenshots folder in your file manager
  Scroll wheel / ◀ ▶    Slide horizontally through screenshot history


SETUP: LINUX (GNOME, KDE, Wayland, X11)
---------------------------------------
1. Install system dependencies (usually preinstalled):

       Ubuntu / Debian:
       sudo apt install python3-gi python3-cairo python3-pil python3-tk wl-clipboard

       Fedora:
       sudo dnf install python3-gobject python3-cairo python3-pillow python3-tkinter wl-clipboard

2. Run:

       python3 clothesline.py
       # or directly:
       ./clothesline.py


SETUP: WINDOWS
--------------
1. Install Python 3.10+ from python.org (check "Add python.exe to PATH").
2. Open terminal in this folder and install Pillow:

       pip install pillow

3. Run:

       python clothesline.py


OPTIONS
-------
  --folder PATH     folder your system saves screenshots to (default: Pictures/Screenshots)
  --count N         number of screenshots visible on the line at once (default: 6)
  --pinned          keep the clothesline pinned open (no auto-hide)
  --no-clipboard    ignore screenshots that are only on the clipboard


MARKUP EDITOR SHORTCUTS
-----------------------
  Ctrl + Z          Undo last stroke or crop
  Enter             Apply active crop box
  Ctrl + C          Copy edited image directly to clipboard
  Ctrl + S          Save a copy (appends -edited.png)
  Esc               Close editor
