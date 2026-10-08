#!/usr/bin/env python3
"""Screenshot Clothesline

Screenshots hung out to dry:
- True 100% per-pixel RGBA transparency on Wayland & X11 (NO black box / void!).
- Single natural clothesline rope with realistic wooden clothespins clamping the cards.
- Delicate light cord in collapsed idle state at the top edge of the screen.
- Smooth pull-down animation: cord lowers down gracefully when expanded.
- Unclip a card and it swings off one corner and falls away; Clear All drops them one by one.
- New screenshots swoop up onto the line and get pinned with a little bounce.
- The rope sags under each card, wobbles when one arrives or leaves, and gusts of wind sway the cards.
- Movable & draggable: click and drag the cord or header to position anywhere along the top edge.
- 100% Flicker-free: zero polling of focused windows (VS Code will never flicker).
- Click to copy image to clipboard with green feedback badge.
- Double-click (or ✏️) to open Markup Editor; edits save directly in-place to the screenshot file!

Linux draws with GTK3 + Cairo. Windows draws the same Cairo scene into a Tkinter window.
"""
import argparse
import math
import os
import platform
import random
import subprocess
import sys
import tempfile
import time

# Clean Snap / VS Code environment variables before any GUI imports to prevent GLib/libc symbol conflicts
for _var in [
    'GTK_PATH',
    'GTK_EXE_PREFIX',
    'GDK_PIXBUF_MODULE_FILE',
    'GDK_PIXBUF_MODULEDIR',
    'GIO_MODULE_DIR',
    'GTK_IM_MODULE_FILE',
    'GSETTINGS_SCHEMA_DIR',
]:
    os.environ.pop(_var, None)
if 'XDG_DATA_DIRS_VSCODE_SNAP_ORIG' in os.environ:
    os.environ['XDG_DATA_DIRS'] = os.environ['XDG_DATA_DIRS_VSCODE_SNAP_ORIG']
if 'XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG' in os.environ:
    os.environ['XDG_CONFIG_DIRS'] = os.environ['XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG']

# Wayland does not let an app place its own window at the top of the screen, and GTK
# never shows the line there. XWayland does, so use it whenever it is available.
if os.environ.get('WAYLAND_DISPLAY') and os.environ.get('DISPLAY') and 'GDK_BACKEND' not in os.environ:
    os.environ['GDK_BACKEND'] = 'x11'

from PIL import Image, ImageChops

from shots import (
    FolderWatcher,
    TextPainter,
    copy_image_to_clipboard,
    default_folder,
    fingerprint,
    load_image,
    relative_time_str,
    reveal_in_folder,
)

try:
    import cairo
except ImportError:
    sys.exit('Clothesline needs pycairo. Windows: "pip install pycairo". '
             'Linux: "sudo apt install python3-cairo".')

# Attempt to load GTK3 (native per-pixel RGBA transparency on Linux)
HAS_GTK = False
try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Pango', '1.0')
    gi.require_version('PangoCairo', '1.0')
    from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango, PangoCairo
    HAS_GTK = True
except Exception:
    HAS_GTK = False

HAS_TK = False
try:
    import tkinter as tk
    from PIL import ImageTk
    HAS_TK = True
except Exception:
    HAS_TK = False

IS_WINDOWS = (platform.system() == 'Windows')

# Dimensions & Tuning
EXPANDED_HEIGHT = 185
COLLAPSED_HEIGHT = 22
COLLAPSED_WIDTH = 250

EXPANDED_CORD_Y = 32
COLLAPSED_CORD_Y = 10

CARD_W = 148
CARD_H = 122
CARD_GAP = 14
MOUNT_X = 6             # where the rope ends are tied to the top edge
CARDS_X = 36            # leaves room for the ◀ arrow
THUMB_SLOT = (136, 82)
MAX_HISTORY = 40
VISIBLE_CARDS = 4

TILTS = [-1.1, 0.8, -0.6, 1.0, -0.8, 0.7, -0.9, 0.8]

# Animation tuning
FRAME_MS = 16           # while something is flying
WIND_FRAME_MS = 40      # while cards only sway
FX_EXTRA = 420          # room below the line for falling and arriving cards
OPEN_TIME = 0.14
ENTER_TIME = 0.8
SWING_TIME = 0.32
SWING_DEG = 38
FALL_TIME = 0.6
GRAVITY = 2400
PIN_POP_TIME = 0.45
CLEAR_STAGGER = 0.09
ROPE_SAG = 2.0          # px the bare rope droops in the middle
CARD_SAG = 2.2          # extra px each hanging card pulls the rope down
BOUNCE_TIME = 1.6
SWAY_DEG = 2.4
GUST_EVERY = (15, 40)    # seconds between breezes while the line is open

# Windows colour-key transparency: pixels of this colour are see-through
KEY_RGB = (1, 2, 3)
KEY_HEX = '#010203'
KEY_ALPHA = 128


def clamp01(u):
    return 0.0 if u < 0 else 1.0 if u > 1 else u


def ease_out_cubic(u):
    return 1.0 - (1.0 - u) ** 3


def ease_out_back(u):
    """Ease out that overshoots a little, then settles."""
    c1 = 1.70158
    return 1 + (c1 + 1) * (u - 1) ** 3 + c1 * (u - 1) ** 2


def pil_to_surface(img):
    """A cairo surface holding a copy of a Pillow image (alpha premultiplied, as cairo wants)."""
    rgba = img.convert('RGBA')
    r, g, b, a = rgba.split()
    if a.getextrema() != (255, 255):
        r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))
    w, h = rgba.size
    data = bytearray(Image.merge('RGBA', (b, g, r, a)).tobytes())
    return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, w, h, w * 4)


def surface_to_keyed_image(surface):
    """An RGB picture of the surface for a colour-key window: faint pixels become KEY_RGB, the rest solid."""
    surface.flush()
    w, h = surface.get_width(), surface.get_height()
    data = bytes(surface.get_data())
    try:
        img = Image.frombuffer('RGBA', (w, h), data, 'raw', 'BGRa', surface.get_stride(), 1)
    except ValueError:
        img = Image.frombuffer('RGBA', (w, h), data, 'raw', 'BGRA', surface.get_stride(), 1)
    mask = img.getchannel('A').point(lambda a: 255 if a >= KEY_ALPHA else 0)
    out = Image.new('RGB', (w, h), KEY_RGB)
    out.paste(img.convert('RGB'), mask=mask)
    return out


class Shot:
    """A screenshot: either a file on disk or an image from the clipboard."""

    def __init__(self, path=None, image=None, mtime=None, tilt=0.0):
        self.path = path
        self.image = image
        self.mtime = mtime or (os.path.getmtime(path) if path and os.path.exists(path) else time.time())
        self.tilt = tilt
        self.thumb = None
        self.surface = None
        self.fp = None
        self.resolution = ''
        self.is_clipboard = (path is None)

        # Animation state
        self.draw_x = None      # where the card is drawn while it slides to its spot
        self.enter_t0 = None    # when it started flying onto the line
        self.sway = 1.0         # 0 holds still (hovered), 1 sways freely

    def ensure_thumb(self):
        """Prepare thumbnail image and cached surface."""
        if self.thumb is not None:
            return True
        img = self.image if self.image is not None else load_image(self.path)
        if img is None:
            return False
        self.fp = fingerprint(img)
        w, h = img.size
        self.resolution = f'{w}x{h}'

        thumb_copy = img.copy()
        thumb_copy.thumbnail(THUMB_SLOT, Image.Resampling.LANCZOS)
        self.thumb = thumb_copy.convert('RGBA')
        self.surface = pil_to_surface(self.thumb)
        return True

    def full(self):
        return self.image if self.image is not None else load_image(self.path)

    def refresh(self):
        self.thumb = None
        self.surface = None
        self.image = None
        if self.path and os.path.exists(self.path):
            self.mtime = os.path.getmtime(self.path)
        return self.ensure_thumb()


# ==============================================================================
# Shared Core: layout, drawing, animation, and clicks (same on every desktop)
# ==============================================================================
class ClotheslineCore:
    """The clothesline itself. A backend subclass supplies the window, timers, and text."""

    def __init__(self, folder, count=VISIBLE_CARDS, pinned=False, wind=True):
        self.folder = folder
        self.visible_count = count
        self.pinned = pinned
        self.wind = wind
        self.collapsed = False

        self.shots = []
        self.scroll_offset = 0
        self.hover_shot_idx = None
        self.hover_action = None
        self.toast = None
        self.toast_id = None

        # Dragging & Position
        self.win_x = None
        self.placed = None
        self.press_pos = None
        self.drag_start_x = None
        self.is_dragging = False

        # Open / close animation
        self.animating = False
        self.anim_opening = True
        self.anim_from = 1.0
        self.anim_t0 = 0.0
        self.anim_id = None
        self.anim_progress = 1.0  # 0.0 = fully collapsed, 1.0 = fully expanded
        self.auto_hide_id = None

        # Fun effects
        self.fallers = []
        self.hold_width = 0
        self.bounce_t0 = -BOUNCE_TIME
        self.bounce_amp = 0.0
        self.tick_id = None
        self.last_tick = 0.0
        self.gust_t0 = 0.0
        self.gust_until = 0.0

        # Setup folder watcher
        self.watcher = FolderWatcher(folder)
        self.watcher.prime()

    # --- Backend hooks ---
    def schedule(self, ms, fn):
        """Run fn once after ms milliseconds; returns a handle for unschedule."""
        raise NotImplementedError

    def unschedule(self, handle):
        raise NotImplementedError

    def screen_width(self):
        raise NotImplementedError

    def place_window(self, x, w, h):
        raise NotImplementedError

    def redraw(self):
        raise NotImplementedError

    def set_tooltip(self, text):
        pass

    def draw_text(self, cr, text, x, y, pt, rgba, bold=False, center_w=None):
        """Draw text with its top-left at (x, y); centred in center_w px when given."""
        raise NotImplementedError

    def show_menu(self, items, event):
        """Pop up items: a list of (label, callback), or None for a separator."""
        raise NotImplementedError

    def open_quick_look(self, shot):
        raise NotImplementedError

    def copy_extra(self, img):
        """Also hand the image to the toolkit's own clipboard. True if that worked."""
        return False

    def quit(self):
        raise NotImplementedError

    # --- Startup ---
    def start(self):
        existing = self.watcher.existing()[:MAX_HISTORY]
        for idx, (mtime, path) in enumerate(existing):
            shot = Shot(path=path, mtime=mtime, tilt=TILTS[idx % len(TILTS)])
            if shot.ensure_thumb():
                self.shots.append(shot)

        screen_w = self.screen_width()
        dock_w = self.get_current_width()
        self.win_x = max(20, min(screen_w - dock_w - 20, (screen_w - dock_w) // 2 + 180))
        self.update_geometry()

        # Pure filesystem watcher (zero compositor flicker)
        self.schedule(2000, self.poll_files)
        self.schedule(random.randint(*GUST_EVERY) * 1000, self.breeze)

        # Initially schedule auto-hide if not pinned
        if not self.pinned:
            self.schedule_auto_hide(4000)
        self.start_gust(2.5)
        self.kick()

    # --- Geometry ---
    def fx_active(self):
        """True while a card is falling off or flying on (the window needs room below)."""
        return bool(self.fallers) or any(s.enter_t0 is not None for s in self.shots)

    def get_expanded_width(self):
        num_cards = min(self.visible_count, max(1, len(self.shots)))
        needed = 44 + num_cards * (CARD_W + CARD_GAP) + 24
        held = self.hold_width if self.fx_active() else 0
        return max(340, needed, held)

    def get_current_width(self):
        if self.collapsed:
            return COLLAPSED_WIDTH
        return self.get_expanded_width()

    def get_current_height(self):
        if self.collapsed:
            return COLLAPSED_HEIGHT
        h = int(COLLAPSED_HEIGHT + (EXPANDED_HEIGHT - COLLAPSED_HEIGHT) * self.anim_progress)
        if self.fx_active():
            h += FX_EXTRA
        return h

    def update_geometry(self):
        screen_w = self.screen_width()
        cur_w = self.get_current_width()
        cur_h = self.get_current_height()

        if self.win_x is None:
            self.win_x = (screen_w - cur_w) // 2
        self.win_x = max(0, min(screen_w - cur_w, self.win_x))
        if self.placed != (self.win_x, cur_w, cur_h):
            self.placed = (self.win_x, cur_w, cur_h)
            self.place_window(self.win_x, cur_w, cur_h)
        self.redraw()

    # --- Smooth Open / Close ---
    def expand(self):
        if self.animating and self.anim_opening:
            return
        if not self.collapsed and not self.animating:
            return
        self.cancel_auto_hide()
        self.animate_transition(opening=True)

    def collapse(self):
        if self.collapsed or self.animating:
            return
        self.cancel_auto_hide()
        self.fallers.clear()
        for shot in self.shots:
            shot.enter_t0 = None
        self.hold_width = 0
        self.animate_transition(opening=False)

    def animate_transition(self, opening):
        self.unschedule(self.anim_id)
        self.anim_from = 0.0 if self.collapsed else self.anim_progress
        self.anim_opening = opening
        self.anim_t0 = time.monotonic()
        self.animating = True
        self.collapsed = False
        self.animation_step()

    def animation_step(self):
        self.anim_id = None
        u = clamp01((time.monotonic() - self.anim_t0) / OPEN_TIME)
        if self.anim_opening:
            self.anim_progress = self.anim_from + (1.0 - self.anim_from) * ease_out_cubic(u)
        else:
            self.anim_progress = self.anim_from * (1.0 - u * u)

        if u < 1.0:
            self.anim_id = self.schedule(FRAME_MS, self.animation_step)
        else:
            self.animating = False
            self.anim_progress = 1.0 if self.anim_opening else 0.0
            self.collapsed = not self.anim_opening
            if self.anim_opening:
                self.start_gust(2.2)
                self.kick()
        self.update_geometry()

    # --- Auto-hide ---
    def schedule_auto_hide(self, ms=2000):
        self.cancel_auto_hide()
        if not self.pinned:
            self.auto_hide_id = self.schedule(ms, self.on_auto_hide_timeout)

    def on_auto_hide_timeout(self):
        self.auto_hide_id = None
        self.collapse()

    def cancel_auto_hide(self):
        if self.auto_hide_id is not None:
            self.unschedule(self.auto_hide_id)
            self.auto_hide_id = None

    # --- Animation Clock ---
    def kick(self):
        """Make sure the animation clock is running."""
        if self.tick_id is None:
            self.last_tick = time.monotonic()
            self.tick_id = self.schedule(FRAME_MS, self.tick)

    def tick(self):
        self.tick_id = None
        now = time.monotonic()
        dt = min(0.05, now - self.last_tick)
        self.last_tick = now

        had_fx = self.fx_active()
        pace = self.advance(now, dt)
        if had_fx and not self.fx_active():
            self.hold_width = 0
        self.update_geometry()

        if pace:
            self.tick_id = self.schedule(FRAME_MS if pace == 'fast' else WIND_FRAME_MS, self.tick)

    def advance(self, now, dt):
        """Move every animation forward. Returns 'fast', 'wind', or None when all is still."""
        if self.collapsed:
            self.fallers.clear()
            return None
        fast = False

        self.fallers = [f for f in self.fallers if now - f['t0'] < f['life']]
        if self.fallers:
            fast = True

        for shot in self.shots:
            if shot.enter_t0 is not None:
                if now - shot.enter_t0 >= ENTER_TIME:
                    shot.enter_t0 = None
                else:
                    fast = True

        # Cards slide to their new spot when a neighbour arrives or leaves
        first, last = self.scroll_offset, self.scroll_offset + self.visible_count
        for idx, shot in enumerate(self.shots):
            if not first <= idx < last:
                shot.draw_x = None
                continue
            target = CARDS_X + (idx - first) * (CARD_W + CARD_GAP)
            if shot.draw_x is None:
                shot.draw_x = target
            gap = target - shot.draw_x
            if abs(gap) < 0.5:
                shot.draw_x = target
            else:
                shot.draw_x += gap * min(1.0, dt * 14)
                fast = True
            calm = 0.0 if self.hover_shot_idx == idx else 1.0
            shot.sway += (calm - shot.sway) * min(1.0, dt * 5)

        if now - self.bounce_t0 < BOUNCE_TIME:
            fast = True

        if fast:
            return 'fast'
        if now < self.gust_until and not self.animating:
            return 'wind'
        return None

    def start_gust(self, length=None):
        """A breeze that sways the cards for a few seconds; between gusts nothing redraws."""
        if not self.wind or self.collapsed:
            return
        now = time.monotonic()
        if now < self.gust_until:
            return
        self.gust_t0 = now
        self.gust_until = now + (length or random.uniform(2.5, 4.5))
        self.kick()

    def gust_strength(self, now):
        if not self.wind or now >= self.gust_until:
            return 0.0
        return math.sin(math.pi * clamp01((now - self.gust_t0) / (self.gust_until - self.gust_t0)))

    def breeze(self):
        self.start_gust()
        self.schedule(random.randint(*GUST_EVERY) * 1000, self.breeze)

    def bump(self, amp):
        """Twang the rope."""
        self.bounce_amp = amp
        self.bounce_t0 = time.monotonic()
        self.kick()

    def bounce(self, now):
        age = now - self.bounce_t0
        if age < 0 or age > BOUNCE_TIME:
            return 0.0
        return self.bounce_amp * math.exp(-age * 3.5) * math.sin(age * 2 * math.pi * 2.4)

    # --- Rope & Card Layout ---
    def rope_y(self, x, base_y, w, loads, now):
        """Height of the rope at x: a gentle sag, a dip under every card, and a bounce after a bump."""
        left, right = 16.0, w - 16.0
        u = clamp01((x - left) / max(1.0, right - left))
        y = base_y + (ROPE_SAG + self.bounce(now)) * 4 * u * (1 - u)
        for px, weight in loads:
            if x <= px:
                tent = (x - left) / max(1.0, px - left)
            else:
                tent = (right - x) / max(1.0, right - px)
            y += CARD_SAG * weight * clamp01(tent)
        return y

    def card_slots(self, now=None):
        """Where each visible card hangs right now. Returns (slots, rope base y, rope loads, width)."""
        now = time.monotonic() if now is None else now
        progress = self.anim_progress
        w = self.get_expanded_width()
        base_y = COLLAPSED_CORD_Y + (EXPANDED_CORD_Y - COLLAPSED_CORD_Y) * progress
        lift = (1.0 - progress) * 45

        slots = []
        visible = self.shots[self.scroll_offset : self.scroll_offset + self.visible_count]
        for i, shot in enumerate(visible):
            x = shot.draw_x if shot.draw_x is not None else CARDS_X + i * (CARD_W + CARD_GAP)
            enter = None if shot.enter_t0 is None else clamp01((now - shot.enter_t0) / ENTER_TIME)
            slots.append({
                'i': i, 'idx': self.scroll_offset + i, 'shot': shot, 'x': x,
                'pin_x': x + CARD_W / 2.0, 'enter': enter, 'lift': lift,
                'weight': 1.0 if enter is None else clamp01((enter - 0.55) / 0.25),
            })

        # Cards still waiting their turn to fall keep pulling on the rope
        loads = [(s['pin_x'], s['weight']) for s in slots]
        loads += [(f['pin_x'], 1.0) for f in self.fallers if now < f['t0']]

        gust = self.gust_strength(now)
        for s in slots:
            shot = s['shot']
            rope = self.rope_y(s['pin_x'], base_y, w, loads, now)
            angle = shot.tilt
            if gust:
                angle += SWAY_DEG * shot.sway * gust * math.sin(now * 1.7 + s['i'] * 1.9)
            dy, alpha, scale, pin_dy, pin_alpha = 0.0, 1.0, 1.0, 0.0, 1.0
            if s['enter'] is not None:
                # Swoop up from below, overshoot a touch, then the pin snaps on
                e = s['enter']
                m = clamp01(e / 0.7)
                dy = (1.0 - ease_out_back(m)) * 160
                angle -= (1.0 - ease_out_cubic(m)) * 14
                alpha = clamp01(e / 0.25)
                scale = 0.75 + 0.25 * ease_out_cubic(m)
                p = clamp01((e - 0.55) / 0.3)
                pin_dy = -22 * (1.0 - ease_out_back(p))
                pin_alpha = clamp01(p * 3)
            s.update(rope_y=rope, y=rope + 12 - lift, angle=angle, dy=dy, alpha=alpha,
                     scale=scale, pin_dy=pin_dy, pin_alpha=pin_alpha)
        return slots, base_y, loads, w

    # --- Cairo Drawing ---
    def paint(self, cr):
        # 1. 100% CLEAR to transparent RGBA (zero black box!)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        if self.collapsed:
            self.draw_collapsed_cord(cr)
        else:
            self.draw_expanded_dock(cr)

    def with_alpha(self, cr, alpha, draw):
        if alpha <= 0.001:
            return
        if alpha >= 0.999:
            draw()
            return
        cr.push_group()
        draw()
        cr.pop_group_to_source()
        cr.paint_with_alpha(alpha)

    def draw_collapsed_cord(self, cr):
        """Minimal, elegant cord at the top edge."""
        w = COLLAPSED_WIDTH
        cord_y = COLLAPSED_CORD_Y

        # Rope tied up to two mounts on the top edge
        pts = [(3, 0), (8, cord_y)]
        for x in range(14, w - 8, 6):
            u = (x - 8) / (w - 16)
            pts.append((x, cord_y + 1.35 * 4 * u * (1 - u)))
        pts += [(w - 8, cord_y), (w - 3, 0)]
        self.stroke_rope(cr, pts, width=2.6)
        for ax in [3, w - 3]:
            self.draw_mount(cr, ax, small=True)

        # Center Wooden Grip Pin
        mid_x = w / 2.0
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.35)
        self.rounded_rect(cr, mid_x - 3, cord_y - 3, 7, 14, 2)
        cr.fill()

        cr.set_source_rgba(0.87, 0.69, 0.45, 1.0)
        self.rounded_rect(cr, mid_x - 4, cord_y - 4, 7, 14, 2)
        cr.fill_preserve()
        cr.set_source_rgba(0.58, 0.40, 0.22, 1.0)
        cr.set_line_width(0.8)
        cr.stroke()

        cr.set_source_rgba(0.35, 0.38, 0.45, 1.0)
        cr.rectangle(mid_x - 4, cord_y + 2, 7, 2)
        cr.fill()

    def draw_expanded_dock(self, cr):
        """Hanging clothesline with cards, pins, and controls."""
        now = time.monotonic()
        slots, base_y, loads, w = self.card_slots(now)

        # 1. Header controls (Pill buttons at top right)
        btn_x = w - 114
        btn_y = max(4, base_y - 26)
        btns = [('btn_pin', '📌'), ('btn_folder', '📂'), ('btn_clear', '🗑️')]
        for i, (action_id, emoji) in enumerate(btns):
            bx = btn_x + i * 30
            is_active = (action_id == 'btn_pin' and self.pinned)
            is_hov = (self.hover_action == action_id)

            if is_active:
                cr.set_source_rgba(0.15, 0.35, 0.70, 0.9)
            elif is_hov:
                cr.set_source_rgba(0.22, 0.28, 0.38, 0.95)
            else:
                cr.set_source_rgba(0.12, 0.16, 0.22, 0.85)

            self.rounded_rect(cr, bx, btn_y, 26, 22, 11)
            cr.fill_preserve()
            cr.set_source_rgba(0.35, 0.45, 0.60, 0.9 if is_active or is_hov else 0.6)
            cr.set_line_width(1.0)
            cr.stroke()
            self.draw_text(cr, emoji, bx + 5, btn_y + 2, 10, (1.0, 1.0, 1.0, 1.0))

        # 2. Main Clothesline Cord, sagging under the cards
        self.draw_rope(cr, w, base_y, loads, now)

        # 3. Hanging Cards
        if not slots and not self.fallers:
            self.draw_text(cr, '✨ Take a screenshot to hang it here', CARDS_X + 30, base_y + 40,
                           10, (0.7, 0.78, 0.88, 0.9))

        for s in slots:
            self.draw_hanging(cr, s)

        # 4. Cards falling off the line, in front of everything
        for f in self.fallers:
            self.draw_faller(cr, f, now)

        # 5. Scroll navigation arrows (if more cards exist than fit)
        if self.scroll_offset > 0:
            self.draw_nav_arrow(cr, 8, base_y + 40, '◀', self.hover_action == 'nav_left')
        if self.scroll_offset + self.visible_count < len(self.shots):
            self.draw_nav_arrow(cr, w - 24, base_y + 40, '▶', self.hover_action == 'nav_right')

    def draw_rope(self, cr, w, base_y, loads, now):
        xs = list(range(16, int(w) - 16, 6)) + [w - 16] + [px for px, _ in loads if 16 < px < w - 16]
        pts = [(x, self.rope_y(x, base_y, w, loads, now)) for x in sorted(xs)]

        # Both ends run up to mounts on the top edge of the screen, so the line really hangs
        pts = [(MOUNT_X, 0)] + pts + [(w - MOUNT_X, 0)]
        self.stroke_rope(cr, pts, width=3.0)

        # Knots where the line turns up, and the mounts it is tied to
        for kx in [16, w - 16]:
            cr.set_source_rgba(0.62, 0.48, 0.31, 1.0)
            cr.arc(kx, base_y, 2.8, 0, 2 * math.pi)
            cr.fill()
        for ax in [MOUNT_X, w - MOUNT_X]:
            self.draw_mount(cr, ax)

    def stroke_rope(self, cr, pts, width):
        """A hemp rope along the points: shadow, body, and a light strand on top."""
        cr.save()
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        strands = [
            (1.5, (0.0, 0.0, 0.0, 0.28), width + 0.5),   # shadow
            (0.0, (0.84, 0.70, 0.51, 1.0), width),       # hemp
            (-0.8, (0.98, 0.93, 0.85, 0.75), width / 3), # highlight
        ]
        for dy, rgba, line in strands:
            cr.set_source_rgba(*rgba)
            cr.set_line_width(line)
            cr.move_to(pts[0][0], pts[0][1] + dy)
            for x, y in pts[1:]:
                cr.line_to(x, y + dy)
            cr.stroke()
        cr.restore()

    def draw_mount(self, cr, x, small=False):
        """A little metal plate screwed to the top edge that the rope is tied to."""
        half = 4 if small else 6
        h = 4 if small else 6
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.3)
        self.rounded_rect(cr, x - half + 1, -2, half * 2, h + 3, 2)
        cr.fill()
        cr.set_source_rgba(0.55, 0.61, 0.70, 1.0)
        self.rounded_rect(cr, x - half, -3, half * 2, h + 3, 2)
        cr.fill_preserve()
        cr.set_source_rgba(0.30, 0.35, 0.43, 1.0)
        cr.set_line_width(0.8)
        cr.stroke()
        if not small:
            cr.set_source_rgba(0.85, 0.89, 0.94, 0.9)
            cr.arc(x, 2, 1.2, 0, 2 * math.pi)
            cr.fill()

    def draw_hanging(self, cr, s):
        """A card on the line, turned around its clothespin."""
        idx = s['idx']
        pivot_x = s['pin_x']
        pivot_y = s['rope_y'] - s['lift']

        cr.save()
        cr.translate(pivot_x, pivot_y + s['dy'])
        cr.rotate(math.radians(s['angle']))
        cr.scale(s['scale'], s['scale'])
        cr.translate(-pivot_x, -pivot_y)
        self.with_alpha(cr, s['alpha'], lambda: self.draw_card(cr, s['shot'], s['x'], s['y'], idx))
        cr.restore()

        # ONE Single Wooden Clothespin in the Center!
        is_pin_hov = idx is not None and self.hover_action == f'unpin_{idx}'
        cr.save()
        cr.translate(pivot_x, pivot_y)
        cr.rotate(math.radians(s['angle']))
        cr.translate(-pivot_x, -pivot_y)
        self.with_alpha(cr, s['pin_alpha'],
                        lambda: self.draw_pin(cr, pivot_x - 5, pivot_y + 2 + s['pin_dy'], is_hover=is_pin_hov))
        cr.restore()

    def draw_faller(self, cr, f, now):
        """A card that was unclipped: the pin pops off, the card swings on one corner and drops."""
        age = now - f['t0']
        if age < 0:
            self.draw_hanging(cr, f['slot'])
            return

        # The clothespin springs up and away
        if age < PIN_POP_TIME:
            px = f['pin_x'] - 5 + f['pin_side'] * 110 * age
            py = f['rope_y'] + 2 - 170 * age + 0.5 * 1500 * age * age
            cr.save()
            cr.translate(px + 5, py + 3)
            cr.rotate(f['pin_side'] * age * 9)
            cr.translate(-px - 5, -py - 3)
            self.with_alpha(cr, 1.0 - age / PIN_POP_TIME, lambda: self.draw_pin(cr, px, py))
            cr.restore()

        side = f['side']
        if f['swing']:
            hinge_x = f['x'] if side < 0 else f['x'] + CARD_W
            swing_time = SWING_TIME
        else:
            hinge_x = f['pin_x']
            swing_time = 0.0
        hinge_y = f['y']

        if age < swing_time:
            angle = f['angle'] - side * SWING_DEG * ease_out_back(age / swing_time)
            ox = oy = 0.0
            alpha = 1.0
        else:
            fall = age - swing_time
            swung = SWING_DEG if f['swing'] else 0
            angle = f['angle'] - side * (swung + f['spin'] * fall)
            ox = f['drift'] * fall
            oy = f['v0'] * fall + 0.5 * GRAVITY * fall * fall
            alpha = 1.0 - clamp01((fall - 0.12) / (FALL_TIME - 0.12))

        cr.save()
        cr.translate(hinge_x + ox, hinge_y + oy)
        cr.rotate(math.radians(angle))
        cr.translate(-hinge_x, -hinge_y)
        self.with_alpha(cr, alpha, lambda: self.draw_card(cr, f['shot'], f['x'], f['y']))
        cr.restore()

    def draw_card(self, cr, shot, cx, cy, idx=None):
        is_hovered = idx is not None and self.hover_shot_idx == idx

        # Card drop shadow
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.35)
        self.rounded_rect(cr, cx, cy + 4, CARD_W, CARD_H, 6)
        cr.fill()

        # Card container
        cr.set_source_rgba(0.12, 0.15, 0.21, 0.96)
        self.rounded_rect(cr, cx, cy, CARD_W, CARD_H, 6)
        cr.fill_preserve()

        if is_hovered:
            cr.set_source_rgba(0.38, 0.65, 0.98, 1.0)
            cr.set_line_width(1.8)
        else:
            cr.set_source_rgba(0.24, 0.31, 0.42, 0.9)
            cr.set_line_width(1.0)
        cr.stroke()

        # Thumbnail slot
        slot_w = CARD_W - 12
        slot_h = 80
        slot_x = cx + 6
        slot_y = cy + 6

        cr.set_source_rgba(0.07, 0.09, 0.13, 1.0)
        self.rounded_rect(cr, slot_x, slot_y, slot_w, slot_h, 4)
        cr.fill()

        # Render thumbnail centered
        if shot.ensure_thumb() and shot.surface:
            pw = shot.surface.get_width()
            ph = shot.surface.get_height()
            tx = slot_x + (slot_w - pw) // 2
            ty = slot_y + (slot_h - ph) // 2
            cr.set_source_surface(shot.surface, tx, ty)
            cr.rectangle(tx, ty, pw, ph)
            cr.fill()

        # Bottom info row:
        # Left: Time
        self.draw_text(cr, relative_time_str(shot.mtime), cx + 8, cy + 98, 8, (0.65, 0.72, 0.82, 1.0))

        # Right: Action Buttons (🔍 View and ✏️ Edit)
        by_btn = cy + 92
        for bx, action, emoji in [(cx + CARD_W - 56, 'view', '🔍'), (cx + CARD_W - 28, 'edit', '✏️')]:
            hov = idx is not None and self.hover_action == f'{action}_{idx}'
            cr.set_source_rgba(0.20, 0.28, 0.40, 0.95 if hov else 0.8)
            self.rounded_rect(cr, bx, by_btn, 24, 22, 5)
            cr.fill_preserve()
            cr.set_source_rgba(0.38, 0.65, 0.98 if hov else 0.45, 0.9 if hov else 0.6)
            cr.set_line_width(1.0)
            cr.stroke()
            self.draw_text(cr, emoji, bx + 4, by_btn + 3, 9, (1.0, 1.0, 1.0, 1.0))

        # Toast badge (e.g. "✓ Copied!")
        if self.toast and self.toast.get('shot') is shot:
            cr.set_source_rgba(0.12, 0.68, 0.38, 0.95)
            self.rounded_rect(cr, cx + 24, cy + 36, CARD_W - 48, 26, 13)
            cr.fill_preserve()
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.8)
            cr.set_line_width(1.0)
            cr.stroke()
            self.draw_text(cr, self.toast.get('text', ''), cx + 24, cy + 40, 9, (1.0, 1.0, 1.0, 1.0),
                           bold=True, center_w=CARD_W - 48)

    def draw_pin(self, cr, px, py, is_hover=False):
        """Single realistic wooden clothespin clamping the line and card."""
        # Shadow
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.35)
        self.rounded_rect(cr, px + 1, py - 9, 10, 26, 2)
        cr.fill()

        # Wooden body
        if is_hover:
            cr.set_source_rgba(0.96, 0.78, 0.52, 1.0)  # Brighter on hover
        else:
            cr.set_source_rgba(0.87, 0.69, 0.45, 1.0)
        self.rounded_rect(cr, px, py - 10, 10, 26, 2)
        cr.fill_preserve()
        cr.set_source_rgba(0.68 if is_hover else 0.58, 0.48 if is_hover else 0.40, 0.25 if is_hover else 0.22, 1.0)
        cr.set_line_width(1.0 if is_hover else 0.8)
        cr.stroke()

        # Metal coil spring in groove
        cr.set_source_rgba(0.35, 0.38, 0.45, 1.0)
        cr.rectangle(px, py - 1, 10, 3)
        cr.fill()
        cr.set_source_rgba(0.85, 0.90, 0.95, 0.9)
        cr.rectangle(px + 2, py - 1, 6, 1)
        cr.fill()

    def draw_nav_arrow(self, cr, ax, ay, symbol, is_hover):
        cr.set_source_rgba(0.20, 0.26, 0.36, 0.95 if is_hover else 0.8)
        self.rounded_rect(cr, ax, ay, 20, 36, 6)
        cr.fill_preserve()
        cr.set_source_rgba(0.38, 0.65, 0.98 if is_hover else 0.55, 0.8)
        cr.set_line_width(1.0)
        cr.stroke()
        self.draw_text(cr, symbol, ax + 4, ay + 10, 10, (1.0, 1.0, 1.0, 1.0))

    def rounded_rect(self, cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    # --- Hit Testing ---
    def hit_test(self, ex, ey):
        """(card index, action) under the pointer; either may be None."""
        if self.collapsed:
            return None, None
        w = self.get_expanded_width()
        cord_y = EXPANDED_CORD_Y

        # Check header buttons
        btn_x = w - 114
        btn_y = max(4, cord_y - 26)
        for i, act_id in enumerate(['btn_pin', 'btn_folder', 'btn_clear']):
            bx = btn_x + i * 30
            if bx <= ex <= bx + 26 and btn_y <= ey <= btn_y + 22:
                return None, act_id

        # Check nav buttons
        if self.scroll_offset > 0 and 8 <= ex <= 28 and cord_y + 40 <= ey <= cord_y + 76:
            return None, 'nav_left'
        if (self.scroll_offset + self.visible_count < len(self.shots)
                and w - 24 <= ex <= w - 4 and cord_y + 40 <= ey <= cord_y + 76):
            return None, 'nav_right'

        # Check cards and single clothespin
        for s in self.card_slots()[0]:
            idx, cx, cy = s['idx'], s['x'], s['y']

            # 1. Top single wooden clothespin (Click to unpin/drop!)
            pin_cx = s['pin_x']
            if pin_cx - 8 <= ex <= pin_cx + 8 and s['rope_y'] - 12 <= ey <= cy + 4:
                return idx, f'unpin_{idx}'

            # 2. Card body & bottom buttons
            if cx <= ex <= cx + CARD_W and cy <= ey <= cy + CARD_H:
                bx_view = cx + CARD_W - 56
                bx_edit = cx + CARD_W - 28
                by_btn = cy + 92
                if bx_view <= ex <= bx_view + 24 and by_btn <= ey <= by_btn + 24:
                    return idx, f'view_{idx}'
                if bx_edit <= ex <= bx_edit + 24 and by_btn <= ey <= by_btn + 24:
                    return idx, f'edit_{idx}'
                return idx, None
        return None, None

    def tooltip_text(self):
        act = self.hover_action or ''
        if act.startswith('unpin_'):
            return 'Unclip & drop from clothesline'
        if act.startswith('view_'):
            return 'View larger image (Quick Look)'
        if act.startswith('edit_'):
            return 'Markup & crop'
        if act == 'btn_pin':
            return 'Keep open' if not self.pinned else 'Auto-hide'
        if act == 'btn_folder':
            return 'Open screenshots folder'
        if act == 'btn_clear':
            return 'Clear all from clothesline'
        if self.hover_shot_idx is not None:
            return 'Click to copy • Double-click to edit • Drag down to drop'
        return None

    # --- Pointer Events (backends translate their events into these) ---
    def pointer_enter(self):
        self.cancel_auto_hide()
        if self.collapsed:
            self.expand()

    def pointer_leave(self):
        self.hover_shot_idx = None
        self.hover_action = None
        self.set_tooltip(None)
        self.redraw()
        if not self.collapsed and not self.pinned:
            self.schedule_auto_hide(2000)

    def pointer_move(self, ex, ey, x_root, y_root, button1):
        self.cancel_auto_hide()

        # Handle window dragging horizontally or card pull-down
        if self.press_pos is not None and button1:
            dx = x_root - self.press_pos[0]
            dy = y_root - self.press_pos[1]

            # Pull down gesture to remove/drop card from clothesline
            if not self.collapsed and self.hover_shot_idx is not None and dy > 45:
                self.drop(self.hover_shot_idx, swing=False)
                self.press_pos = None
                self.is_dragging = False
                return

            can_drag = self.collapsed or (self.hover_shot_idx is None and self.hover_action is None)
            if can_drag and (abs(dx) > 4 or abs(dy) > 4):
                self.is_dragging = True
                self.win_x = int(x_root - self.drag_start_x)
                self.update_geometry()
                return

        prev = (self.hover_shot_idx, self.hover_action)
        self.hover_shot_idx, self.hover_action = self.hit_test(ex, ey)
        self.set_tooltip(self.tooltip_text())
        if prev != (self.hover_shot_idx, self.hover_action):
            self.redraw()

    def pointer_down(self, x_root, y_root):
        self.press_pos = (x_root, y_root)
        self.drag_start_x = x_root - (self.win_x or 0)
        self.is_dragging = False

    def pointer_double(self):
        # Double-click opens editor
        if self.hover_shot_idx is not None and self.hover_shot_idx < len(self.shots):
            self.open_editor(self.shots[self.hover_shot_idx])

    def pointer_up(self):
        was_dragging = self.is_dragging
        self.press_pos = None
        self.is_dragging = False

        if was_dragging:
            return

        if self.collapsed:
            self.expand()
            return

        # Handle clicks
        act = self.hover_action or ''
        if act == 'btn_pin':
            self.toggle_pinned()
        elif act == 'btn_folder':
            reveal_in_folder(self.folder)
        elif act == 'btn_clear':
            self.clear_all()
        elif act == 'nav_left':
            self.scroll_left()
        elif act == 'nav_right':
            self.scroll_right()
        elif act.startswith(('unpin_', 'view_', 'edit_')):
            idx = int(act.split('_')[1])
            if idx >= len(self.shots):
                return
            if act.startswith('unpin_'):
                self.drop(idx)
            elif act.startswith('view_'):
                self.open_quick_look(self.shots[idx])
            else:
                self.open_editor(self.shots[idx])
        elif self.hover_shot_idx is not None:
            # Clicking card body copies to clipboard
            self.copy_shot(self.hover_shot_idx)

    def wheel(self, step):
        if step < 0:
            self.scroll_left()
        elif step > 0:
            self.scroll_right()

    def right_click(self, event):
        idx = self.hover_shot_idx
        if idx is not None and idx < len(self.shots):
            shot = self.shots[idx]
            items = [
                ('📋 Copy Image to Clipboard', lambda: self.copy_shot(idx)),
                ('🔍 View Full-Size Image', lambda: self.open_quick_look(shot)),
                ('✏️ Open in Editor (Markup / Crop)', lambda: self.open_editor(shot)),
            ]
            if shot.path and os.path.exists(shot.path):
                items.append(('📂 Reveal in File Manager', lambda: reveal_in_folder(shot.path)))
            items += [None, ('🗑️ Unclip / Drop from Clothesline', lambda: self.drop_shot(shot))]
        else:
            items = [
                ('📌 Auto-hide' if self.pinned else '📌 Keep Open', self.toggle_pinned),
                ('🍃 Stop the Wind' if self.wind else '🍃 Let Cards Sway', self.toggle_wind),
                ('📂 Open Screenshots Folder', lambda: reveal_in_folder(self.folder)),
                ('🗑️ Clear All', self.clear_all),
                None,
                ('✖ Quit Clothesline', self.quit),
            ]
        self.show_menu(items, event)

    def scroll_left(self):
        if self.scroll_offset > 0:
            self.scroll_offset -= 1
            self.kick()
            self.redraw()

    def scroll_right(self):
        if self.scroll_offset + self.visible_count < len(self.shots):
            self.scroll_offset += 1
            self.kick()
            self.redraw()

    # --- Actions ---
    def toggle_pinned(self):
        self.pinned = not self.pinned
        self.cancel_auto_hide()
        self.redraw()

    def toggle_wind(self):
        self.wind = not self.wind
        self.gust_until = 0.0
        self.start_gust()
        self.kick()

    def copy_shot(self, idx):
        if idx >= len(self.shots):
            return
        shot = self.shots[idx]
        img = shot.full()
        if img:
            ok = copy_image_to_clipboard(img)
            ok = self.copy_extra(img) or ok
            self.show_toast(shot, '✓ Copied!' if ok else 'Copy failed')

    def show_toast(self, shot, text):
        self.toast = {'shot': shot, 'text': text}
        self.unschedule(self.toast_id)
        self.toast_id = self.schedule(1800, self.clear_toast)
        self.redraw()

    def clear_toast(self):
        self.toast_id = None
        self.toast = None
        self.redraw()

    def make_faller(self, slot, swing=True, delay=0.0):
        side = random.choice((-1, 1))
        slot = dict(slot, idx=None, lift=0.0)
        return {
            'shot': slot['shot'], 'slot': slot,
            'x': slot['x'], 'y': slot['y'], 'pin_x': slot['pin_x'], 'rope_y': slot['rope_y'],
            'angle': slot['angle'], 'side': side, 'pin_side': -side, 'swing': swing,
            't0': time.monotonic() + delay,
            'spin': random.uniform(60, 140),
            'drift': random.uniform(-70, 70),
            'v0': 0.0 if swing else 260.0,
            'life': (SWING_TIME if swing else 0.0) + FALL_TIME,
        }

    def forget_hover(self):
        self.hover_shot_idx = None
        self.hover_action = None
        self.set_tooltip(None)

    def drop(self, idx, swing=True):
        """Unclip a card: it falls off the line and disappears (the file stays on disk)."""
        if not 0 <= idx < len(self.shots):
            return
        if not self.collapsed:
            self.hold_width = max(self.hold_width, self.get_expanded_width())
            for slot in self.card_slots()[0]:
                if slot['idx'] == idx:
                    self.fallers.append(self.make_faller(slot, swing=swing))
                    break
        del self.shots[idx]
        if self.scroll_offset > 0 and self.scroll_offset >= len(self.shots):
            self.scroll_offset = max(0, len(self.shots) - 1)
        self.forget_hover()
        self.bump(2.5)
        self.update_geometry()

    def drop_shot(self, shot):
        if shot in self.shots:
            self.drop(self.shots.index(shot))

    def clear_all(self):
        """Every card on show falls off, one after another."""
        if not self.collapsed:
            self.hold_width = max(self.hold_width, self.get_expanded_width())
            for n, slot in enumerate(self.card_slots()[0]):
                self.fallers.append(self.make_faller(slot, delay=n * CLEAR_STAGGER))
        self.shots.clear()
        self.scroll_offset = 0
        self.forget_hover()
        self.bump(4.0)
        self.update_geometry()

    def open_editor(self, shot):
        img = shot.full()
        if img is None:
            return

        # Ensure we have a valid file path for standalone editor invocation
        target_path = shot.path
        if not target_path or not os.path.exists(target_path):
            tmp = tempfile.NamedTemporaryFile(suffix='.png', prefix='clothesline_shot_', delete=False)
            img.save(tmp.name)
            tmp.close()
            target_path = tmp.name
            shot.path = target_path

        if getattr(sys, 'frozen', False):
            # Packaged .exe: the editor lives inside the same executable
            subprocess.Popen([sys.executable, '--edit', target_path])
        else:
            editor_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'editor.py')
            subprocess.Popen([sys.executable, editor_script, target_path])

    def add_shot(self, shot):
        if any(s.fp is not None and s.fp == shot.fp for s in self.shots[:20]):
            return
        shot.enter_t0 = time.monotonic()
        self.shots.insert(0, shot)
        del self.shots[MAX_HISTORY:]
        self.scroll_offset = 0
        self.expand()
        # Twang the rope as the pin snaps on
        self.schedule(int(ENTER_TIME * 0.62 * 1000), lambda: self.bump(2.2))
        self.schedule(int(ENTER_TIME * 1000), lambda: self.start_gust(2.5))
        self.kick()
        self.update_geometry()
        self.schedule_auto_hide(6000)

    def poll_files(self):
        """Silently check filesystem for new screenshots or modifications."""
        # Check for new files
        for path in reversed(self.watcher.new_paths()):
            shot = Shot(path=path, tilt=TILTS[len(self.shots) % len(TILTS)])
            if shot.ensure_thumb():
                self.add_shot(shot)
            else:
                self.watcher.seen.discard(path)

        # Check if any existing shot was edited on disk
        changed = False
        for shot in self.shots:
            if shot.path and os.path.exists(shot.path):
                current_mtime = os.path.getmtime(shot.path)
                if current_mtime > shot.mtime + 0.5:
                    shot.refresh()
                    changed = True

        if changed:
            self.redraw()
        self.schedule(2000, self.poll_files)


# ==============================================================================
# GTK3 + Cairo Engine (True Per-Pixel RGBA Transparency on Linux)
# ==============================================================================
if HAS_GTK:
    class QuickLookWindow(Gtk.Window):
        """Full-size screenshot preview window (Quick Look)."""

        def __init__(self, shot, on_edit=None):
            super().__init__(type=Gtk.WindowType.TOPLEVEL)
            self.shot = shot
            self.on_edit = on_edit

            title = os.path.basename(shot.path) if shot.path else 'Screenshot Preview'
            self.set_title(title)
            self.set_position(Gtk.WindowPosition.CENTER)
            self.set_keep_above(True)

            # Dark theme background
            self.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.08, 0.10, 0.14, 1.0))

            header = Gtk.HeaderBar(show_close_button=True)
            header.set_title(title)
            header.set_subtitle(f'{shot.resolution} • {relative_time_str(shot.mtime)}')
            self.set_titlebar(header)

            btn_edit = Gtk.Button(label='✏️ Edit')
            btn_edit.set_tooltip_text('Open in Markup & Crop editor (e)')
            btn_edit.connect('clicked', self.on_click_edit)
            header.pack_start(btn_edit)

            btn_copy = Gtk.Button(label='📋 Copy')
            btn_copy.set_tooltip_text('Copy image to clipboard (c)')
            btn_copy.connect('clicked', self.on_click_copy)
            header.pack_start(btn_copy)

            if shot.path and os.path.exists(shot.path):
                btn_folder = Gtk.Button(label='📂 Folder')
                btn_folder.set_tooltip_text('Reveal in file manager')
                btn_folder.connect('clicked', lambda w: reveal_in_folder(shot.path))
                header.pack_start(btn_folder)

            img = shot.full()
            if img:
                screen = self.get_screen()
                max_w = int(screen.get_width() * 0.86)
                max_h = int(screen.get_height() * 0.80)
                iw, ih = img.size
                scale = min(1.0, max_w / max(1, iw), max_h / max(1, ih))
                tw, th = max(10, int(iw * scale)), max(10, int(ih * scale))
                resized = img.resize((tw, th), Image.Resampling.LANCZOS) if scale < 1.0 else img

                raw = resized.convert('RGBA').tobytes()
                pb = GdkPixbuf.Pixbuf.new_from_data(
                    raw, GdkPixbuf.Colorspace.RGB, True, 8, tw, th, tw * 4
                )
                gtk_image = Gtk.Image.new_from_pixbuf(pb)

                event_box = Gtk.EventBox()
                event_box.add(gtk_image)
                event_box.connect('button-press-event', lambda w, e: self.destroy())

                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                box.set_margin_start(10)
                box.set_margin_end(10)
                box.set_margin_top(10)
                box.set_margin_bottom(10)
                box.pack_start(event_box, True, True, 0)
                self.add(box)

            self.connect('key-press-event', self.on_key)
            self.show_all()

        def on_key(self, widget, event):
            if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_space, Gdk.KEY_q):
                self.destroy()
                return True
            elif event.keyval in (Gdk.KEY_e, Gdk.KEY_E):
                self.on_click_edit(None)
                return True
            elif event.keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self.on_click_copy(None)
                return True
            return False

        def on_click_edit(self, widget):
            self.destroy()
            if self.on_edit:
                self.on_edit(self.shot)

        def on_click_copy(self, widget):
            img = self.shot.full()
            if img:
                copy_image_to_clipboard(img)
                set_gtk_clipboard(img)

    def set_gtk_clipboard(img):
        try:
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            full_rgba = img.convert('RGBA')
            fw, fh = full_rgba.size
            full_pb = GdkPixbuf.Pixbuf.new_from_data(
                full_rgba.tobytes(), GdkPixbuf.Colorspace.RGB, True, 8, fw, fh, fw * 4
            )
            clip.set_image(full_pb)
            clip.store()
            return True
        except Exception:
            return False

    class ClotheslineGtkApp(ClotheslineCore):
        """Clothesline desktop interface using GTK3 and Cairo with 100% RGBA transparency."""

        def __init__(self, folder, count=VISIBLE_CARDS, pinned=False, wind=True):
            super().__init__(folder, count=count, pinned=pinned, wind=wind)

            # Window setup: POPUP window gives override-redirect (no WM borders, no focus stealing)
            self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
            self.win.set_app_paintable(True)
            self.win.set_keep_above(True)

            screen = self.win.get_screen()
            visual = screen.get_rgba_visual()
            if visual:
                self.win.set_visual(visual)

            # DrawingArea receives Cairo paint events and user input
            self.da = Gtk.DrawingArea()
            self.win.add(self.da)

            self.da.set_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.POINTER_MOTION_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
                | Gdk.EventMask.SCROLL_MASK
            )

            self.da.connect('draw', lambda w, cr: self.paint(cr) or False)
            self.da.connect('motion-notify-event', self.on_motion)
            self.da.connect('button-press-event', self.on_press)
            self.da.connect('button-release-event', self.on_release)
            self.da.connect('enter-notify-event', lambda w, e: self.pointer_enter())
            self.da.connect('leave-notify-event', lambda w, e: self.pointer_leave())
            self.da.connect('scroll-event', self.on_scroll)

            self.win.show_all()
            self.start()

        # --- Backend hooks ---
        def schedule(self, ms, fn):
            return GLib.timeout_add(ms, lambda: fn() and False)

        def unschedule(self, handle):
            if handle is not None:
                GLib.source_remove(handle)

        def screen_width(self):
            return self.win.get_screen().get_width()

        def place_window(self, x, w, h):
            gdk_win = self.win.get_window()
            if not gdk_win:
                return
            gdk_win.move_resize(x, 0, w, h)
            # The extra room for falling cards must not catch clicks meant for the windows below
            try:
                region = cairo.Region(cairo.RectangleInt(0, 0, w, min(h, EXPANDED_HEIGHT)))
                gdk_win.input_shape_combine_region(region, 0, 0)
            except Exception:
                pass

        def redraw(self):
            self.da.queue_draw()

        def set_tooltip(self, text):
            if self.da.get_tooltip_text() != text:
                self.da.set_tooltip_text(text)

        def draw_text(self, cr, text, x, y, pt, rgba, bold=False, center_w=None):
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(Pango.FontDescription(f'Sans {"Bold " if bold else ""}{pt}'))
            layout.set_text(text, -1)
            if center_w is not None:
                ink, logical = layout.get_pixel_extents()
                x += (center_w - logical.width) // 2
            cr.set_source_rgba(*rgba)
            cr.move_to(x, y)
            PangoCairo.show_layout(cr, layout)

        def show_menu(self, items, event):
            menu = Gtk.Menu()
            for item in items:
                if item is None:
                    menu.append(Gtk.SeparatorMenuItem())
                    continue
                label, callback = item
                mi = Gtk.MenuItem(label=label)
                mi.connect('activate', lambda w, cb=callback: cb())
                menu.append(mi)
            menu.show_all()
            menu.popup(None, None, None, None, event.button, event.time)

        def open_quick_look(self, shot):
            QuickLookWindow(shot, on_edit=self.open_editor)

        def copy_extra(self, img):
            return set_gtk_clipboard(img)

        def quit(self):
            Gtk.main_quit()

        # --- Native events ---
        def on_motion(self, widget, event):
            button1 = bool(event.state & Gdk.ModifierType.BUTTON1_MASK)
            self.pointer_move(event.x, event.y, event.x_root, event.y_root, button1)

        def on_press(self, widget, event):
            if event.button == 1:
                self.pointer_down(event.x_root, event.y_root)
                if event.type == Gdk.EventType._2BUTTON_PRESS:
                    self.pointer_double()
            elif event.button == 3:
                # Right-click context menu
                self.right_click(event)

        def on_release(self, widget, event):
            if event.button == 1:
                self.pointer_up()

        def on_scroll(self, widget, event):
            if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
                self.wheel(-1)
            elif event.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
                self.wheel(1)


# ==============================================================================
# Tkinter Engine (Windows): the same Cairo scene, shown through a colour-key window
# ==============================================================================
class PilText(TextPainter):
    """Text for desktops without Pango, cached as cairo surfaces."""

    def __init__(self):
        super().__init__()
        self.cache = {}

    def render(self, text, pt, rgba, bold=False):
        """(cairo surface, width) for the text."""
        key = (text, pt, rgba, bold)
        if key not in self.cache:
            if len(self.cache) > 400:
                self.cache.clear()
            img = self.image(text, pt, rgba, bold)
            self.cache[key] = (pil_to_surface(img), img.width)
        return self.cache[key]


class ClotheslineTkApp(ClotheslineCore):
    """Tkinter interface (Windows): Cairo draws the scene, Tk shows it in a colour-key window."""

    def __init__(self, root, folder, count=VISIBLE_CARDS, pinned=False, wind=True):
        super().__init__(folder, count=count, pinned=pinned, wind=wind)
        self.root = root
        self.text = PilText()
        self.photo = None
        self.render_pending = False
        self.tip = None
        self.tip_text = None
        self.tip_id = None

        root.title('Clothesline')
        root.overrideredirect(True)
        root.attributes('-topmost', True)

        # Windows transparency colorkey
        try:
            root.attributes('-transparentcolor', KEY_HEX)
        except tk.TclError:
            pass  # Not on Windows: the key colour stays visible (handy for testing)
        root.configure(bg=KEY_HEX)

        self.canvas = tk.Canvas(root, width=COLLAPSED_WIDTH, height=COLLAPSED_HEIGHT,
                                bg=KEY_HEX, highlightthickness=0, cursor='hand2')
        self.canvas.pack(fill='both', expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor='nw')

        self.canvas.bind('<Motion>', self.on_motion)
        self.canvas.bind('<ButtonPress-1>', lambda e: self.pointer_down(e.x_root, e.y_root))
        self.canvas.bind('<ButtonRelease-1>', lambda e: self.pointer_up())
        self.canvas.bind('<Double-Button-1>', lambda e: self.pointer_double())
        self.canvas.bind('<Button-3>', self.right_click)
        self.canvas.bind('<Enter>', lambda e: self.pointer_enter())
        self.canvas.bind('<Leave>', lambda e: self.pointer_leave())
        self.canvas.bind('<MouseWheel>', lambda e: self.wheel(-1 if e.delta > 0 else 1))
        self.canvas.bind('<Button-4>', lambda e: self.wheel(-1))
        self.canvas.bind('<Button-5>', lambda e: self.wheel(1))

        self.start()

    # --- Backend hooks ---
    def schedule(self, ms, fn):
        return self.root.after(ms, fn)

    def unschedule(self, handle):
        if handle is not None:
            try:
                self.root.after_cancel(handle)
            except tk.TclError:
                pass

    def screen_width(self):
        return self.root.winfo_screenwidth()

    def place_window(self, x, w, h):
        self.root.geometry(f'{w}x{h}+{x}+0')
        self.canvas.config(width=w, height=h)

    def redraw(self):
        if not self.render_pending:
            self.render_pending = True
            self.root.after_idle(self.render)

    def render(self):
        self.render_pending = False
        if not self.placed:
            return
        _, w, h = self.placed
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        self.paint(cairo.Context(surface))
        self.photo = ImageTk.PhotoImage(surface_to_keyed_image(surface))
        self.canvas.itemconfig(self.image_item, image=self.photo)

    def draw_text(self, cr, text, x, y, pt, rgba, bold=False, center_w=None):
        surface, width = self.text.render(text, pt, tuple(rgba), bold)
        if center_w is not None:
            x += (center_w - width) // 2
        cr.set_source_surface(surface, x, y)
        cr.paint()

    def set_tooltip(self, text):
        if text == self.tip_text:
            return
        self.tip_text = text
        self.unschedule(self.tip_id)
        self.tip_id = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
        if text:
            self.tip_id = self.schedule(600, self.show_tooltip)

    def show_tooltip(self):
        self.tip_id = None
        if not self.tip_text:
            return
        self.tip = tk.Toplevel(self.root)
        self.tip.overrideredirect(True)
        self.tip.attributes('-topmost', True)
        x = self.root.winfo_pointerx() + 14
        y = self.root.winfo_pointery() + 18
        self.tip.geometry(f'+{x}+{y}')
        tk.Label(self.tip, text=self.tip_text, background='#1e293b', foreground='#f8fafc',
                 relief='solid', borderwidth=1, padx=6, pady=2).pack()

    def show_menu(self, items, event):
        menu = tk.Menu(self.root, tearoff=0)
        for item in items:
            if item is None:
                menu.add_separator()
            else:
                label, callback = item
                menu.add_command(label=label, command=callback)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def open_quick_look(self, shot):
        img = shot.full()
        if img is None:
            return
        win = tk.Toplevel(self.root, bg='#141a24')
        win.title(f'{os.path.basename(shot.path) if shot.path else "Screenshot Preview"}'
                  f'  •  {shot.resolution}  •  {relative_time_str(shot.mtime)}')
        win.attributes('-topmost', True)

        max_w = int(self.root.winfo_screenwidth() * 0.86)
        max_h = int(self.root.winfo_screenheight() * 0.80)
        iw, ih = img.size
        scale = min(1.0, max_w / max(1, iw), max_h / max(1, ih))
        if scale < 1.0:
            img = img.resize((max(10, int(iw * scale)), max(10, int(ih * scale))), Image.Resampling.LANCZOS)
        win.photo = ImageTk.PhotoImage(img)
        label = tk.Label(win, image=win.photo, bg='#141a24', cursor='hand2')
        label.pack(padx=10, pady=10)

        def edit(e=None):
            win.destroy()
            self.open_editor(shot)

        label.bind('<Button-1>', lambda e: win.destroy())
        for key in ('<Escape>', '<space>', 'q'):
            win.bind(key, lambda e: win.destroy())
        win.bind('e', edit)
        win.bind('c', lambda e: copy_image_to_clipboard(shot.full()))
        win.focus_force()

    def quit(self):
        self.root.destroy()

    # --- Native events ---
    def on_motion(self, e):
        self.pointer_move(e.x, e.y, e.x_root, e.y_root, bool(e.state & 0x0100))


def main():
    parser = argparse.ArgumentParser(description='Screenshot Clothesline: Screenshots hung out to dry.')
    parser.add_argument('--folder', default=default_folder(), help='folder your system saves screenshots to')
    parser.add_argument('--count', type=int, default=VISIBLE_CARDS, help='number of screenshots visible on the line (default 4)')
    parser.add_argument('--pinned', action='store_true', help='keep clothesline pinned open (no auto-hide)')
    parser.add_argument('--no-wind', action='store_true', help='cards hang still instead of swaying')
    parser.add_argument('--backend', choices=['auto', 'gtk', 'tk'], default='auto',
                        help='drawing toolkit (default: GTK on Linux, Tk elsewhere)')
    parser.add_argument('--edit', metavar='IMAGE', help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.edit:
        from editor import run_standalone
        run_standalone(args.edit)
        return

    os.makedirs(args.folder, exist_ok=True)
    options = dict(count=max(1, args.count), pinned=args.pinned, wind=not args.no_wind)

    use_gtk = HAS_GTK and args.backend != 'tk'
    if use_gtk:
        ClotheslineGtkApp(args.folder, **options)
        Gtk.main()
    elif HAS_TK:
        if IS_WINDOWS:
            # Sharp drawing on scaled (HiDPI) displays
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        root = tk.Tk()
        ClotheslineTkApp(root, args.folder, **options)
        root.mainloop()
    else:
        sys.exit('Clothesline needs GTK3 (python3-gi) or Tkinter.')


if __name__ == '__main__':
    main()
