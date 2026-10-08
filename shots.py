"""Pillow-only helpers: finding screenshots, spotting repeats, and drawing marks onto pictures."""
import hashlib
import io
import os
import platform
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageGrab

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'}
INK = (255, 59, 48)


def default_folder():
    """Where Windows and the usual Linux desktops (GNOME, KDE) save screenshots."""
    return os.path.join(os.path.expanduser('~'), 'Pictures', 'Screenshots')


def load_image(path):
    """An RGB copy of the picture, or None if it is missing or still being written."""
    try:
        with Image.open(path) as im:
            im.load()
            return im.convert('RGB')
    except Exception:
        return None


def fingerprint(img):
    """A small digest that matches the same screenshot whatever format it was saved in."""
    small = img.convert('L').resize((24, 24))
    return hashlib.md5(small.tobytes() + str(img.size).encode()).hexdigest()


class FolderWatcher:
    """Reports image files that appeared in a folder since the last check."""

    def __init__(self, folder):
        self.folder = folder
        self.seen = set()

    def existing(self):
        """(modified time, path) for each image in the folder, newest first."""
        found = []
        try:
            with os.scandir(self.folder) as entries:
                for entry in entries:
                    if entry.is_file() and os.path.splitext(entry.name)[1].lower() in IMAGE_EXTS:
                        found.append((entry.stat().st_mtime, entry.path))
        except OSError:
            return []
        return sorted(found, reverse=True)

    def prime(self):
        """Count everything already in the folder as old, so only new screenshots hang."""
        self.seen = {path for _, path in self.existing()}

    def new_paths(self):
        """Paths that appeared since the last call, newest first."""
        fresh = []
        for _, path in self.existing():
            if path not in self.seen:
                self.seen.add(path)
                fresh.append(path)
        return fresh


def read_clipboard_image():
    """The image on the clipboard, or None.

    Windows and macOS use Pillow. Linux (X11 or Wayland) uses wl-paste or xclip if installed.
    """
    try:
        if platform.system() == 'Linux':
            if shutil.which('wl-paste'):
                # Fast MIME check: never bother apps (like VS Code) if clipboard contains text/code
                types_res = subprocess.run(['wl-paste', '--list-types'], capture_output=True, text=True, timeout=1)
                types = types_res.stdout.splitlines() if types_res.returncode == 0 else []
                if not any('image' in t for t in types):
                    return None
                cmd = ['wl-paste', '--type', 'image/png']
            elif shutil.which('xclip'):
                cmd = ['xclip', '-selection', 'clipboard', '-t', 'image/png', '-o']
            else:
                return None
            data = subprocess.run(cmd, capture_output=True, timeout=3).stdout
            if not data:
                return None
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                return im.convert('RGB')
        grabbed = ImageGrab.grabclipboard()
        return grabbed.convert('RGB') if isinstance(grabbed, Image.Image) else None
    except Exception:
        return None


# A mark is (kind, points) in picture pixels. 'pen' has many points;
# 'circle' and 'box' have two: opposite corners of their bounding box.

def bbox_of(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def bake_marks(image, marks):
    """A copy of the picture with every mark drawn on it at full resolution."""
    out = image.convert('RGB').copy()
    draw = ImageDraw.Draw(out)
    width = max(3, round(min(out.size) / 220))
    for item in marks:
        kind = item[0]
        pts = item[1]
        col = item[2] if len(item) > 2 else INK
        if len(pts) < 2:
            continue
        if kind == 'pen':
            draw.line([tuple(p) for p in pts], fill=col, width=width, joint='curve')
        elif kind == 'circle':
            draw.ellipse(bbox_of(pts), outline=col, width=width)
        elif kind == 'box':
            draw.rectangle(bbox_of(pts), outline=col, width=width)
    return out


def shift_marks(marks, dx, dy):
    """Move marks by (dx, dy), for example after cropping away the top-left corner."""
    shifted = []
    for item in marks:
        kind, pts = item[0], item[1]
        new_pts = [(x - dx, y - dy) for x, y in pts]
        if len(item) > 2:
            shifted.append((kind, new_pts, item[2]))
        else:
            shifted.append((kind, new_pts))
    return shifted


def relative_time_str(ts):
    """Format timestamp into short human-readable relative time string."""
    if not ts:
        return 'Recent'
    import time
    diff = time.time() - ts
    if diff < 60:
        return 'Just now'
    elif diff < 3600:
        return f'{int(diff // 60)}m ago'
    elif diff < 86400:
        return f'{int(diff // 3600)}h ago'
    elif diff < 86400 * 7:
        return f'{int(diff // 86400)}d ago'
    else:
        return time.strftime('%b %d', time.localtime(ts))


def reveal_in_folder(path):
    """Open the file manager and show the file."""
    if not path or not os.path.exists(path):
        return False
    sys_name = platform.system()
    try:
        if sys_name == 'Linux':
            folder = os.path.dirname(os.path.abspath(path))
            subprocess.Popen(['xdg-open', folder])
            return True
        elif sys_name == 'Windows':
            subprocess.Popen(['explorer', '/select,', os.path.abspath(path)])
            return True
        elif sys_name == 'Darwin':
            subprocess.Popen(['open', '-R', os.path.abspath(path)])
            return True
    except Exception:
        pass
    return False


def copy_image_to_clipboard(image):
    """Copy a PIL Image to the system clipboard across Wayland, X11, Windows, and macOS."""
    if image is None:
        return False
    sys_name = platform.system()
    try:
        if sys_name == 'Linux':
            output = io.BytesIO()
            image.save(output, format='PNG')
            data = output.getvalue()
            if shutil.which('wl-copy'):
                res = subprocess.run(['wl-copy', '--type', 'image/png'], input=data, timeout=3)
                return res.returncode == 0
            elif shutil.which('xclip'):
                res = subprocess.run(['xclip', '-selection', 'clipboard', '-t', 'image/png'], input=data, timeout=3)
                return res.returncode == 0
            return False
        elif sys_name == 'Windows':
            import ctypes
            output = io.BytesIO()
            image.convert('RGB').save(output, 'BMP')
            data = output.getvalue()[14:]  # Strip 14-byte BITMAPFILEHEADER
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            GMEM_MOVEABLE = 0x0002
            CF_DIB = 8
            h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h_mem:
                return False
            p_mem = kernel32.GlobalLock(h_mem)
            ctypes.memmove(p_mem, data, len(data))
            kernel32.GlobalUnlock(h_mem)
            if user32.OpenClipboard(None):
                user32.EmptyClipboard()
                user32.SetClipboardData(CF_DIB, h_mem)
                user32.CloseClipboard()
                return True
            return False
        elif sys_name == 'Darwin':
            output = io.BytesIO()
            image.save(output, format='PNG')
            data = output.getvalue()
            proc = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
            proc.communicate(data)
            return proc.returncode == 0
    except Exception:
        pass
    return False

