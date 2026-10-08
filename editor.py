"""Editor window: pen, circle, box, and crop, with undo, colors, in-place save, copy to clipboard."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageDraw, ImageTk

from shots import TextPainter, bake_marks, copy_image_to_clipboard, shift_marks

RESAMPLE = getattr(Image, 'Resampling', Image).LANCZOS

COLORS = [
    ('Red', '#ef4444', (239, 68, 68)),
    ('Yellow', '#facc15', (250, 204, 21)),
    ('Green', '#22c55e', (34, 197, 94)),
    ('Blue', '#3b82f6', (59, 130, 246)),
    ('White', '#ffffff', (255, 255, 255)),
]

BG_DARK = '#0f131a'
BAR_BG = '#171c26'
BORDER_COL = '#283142'
TEXT_LIGHT = '#f8fafc'
TEXT_MUTED = '#94a3b8'


PAINTER = TextPainter()


def normalized(box):
    x0, y0, x1, y1 = box
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


class Tooltip:
    """Simple hover tooltip for icon buttons."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')

    def show(self, event=None):
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f'+{x}+{y}')
        label = tk.Label(tw, text=self.text, justify='left',
                         background='#1e293b', foreground='#f8fafc',
                         relief='solid', borderwidth=1,
                         font=('DejaVu Sans', 8), padx=6, pady=2)
        label.pack()

    def hide(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class PillButton(tk.Canvas):
    """Rounded toolbar button drawn with Pillow, in the same style as the clothesline's buttons."""

    # kind: (fill, hover fill, border, text)
    STYLES = {
        'tool': ('#1f2838', '#2b3648', '#4b5d7a', '#f8fafc'),
        'primary': ('#2563eb', '#3b76f6', '#5b8ff9', '#ffffff'),
        'success': ('#059669', '#10a877', '#34c08f', '#ffffff'),
    }
    SELECTED = ('#2659b3', '#2f68c8', '#60a5fa', '#ffffff')
    DISABLED = ('#1a212d', '#1a212d', '#2a3446', '#64748b')
    HEIGHT = 34
    PAD = 12
    SCALE = 3  # draw large, then shrink, for smooth corners

    def __init__(self, master, text='', command=None, kind='tool', tip=None, swatch=None):
        super().__init__(master, height=self.HEIGHT, bg=BAR_BG, highlightthickness=0, bd=0, cursor='hand2')
        self.text = text
        self.command = command
        self.kind = kind
        self.swatch = swatch
        self.hover = False
        self.selected = False
        self.enabled = True
        self.photo = None
        self.bind('<Enter>', lambda e: self.set_hover(True))
        self.bind('<Leave>', lambda e: self.set_hover(False))
        self.bind('<ButtonRelease-1>', self.on_click)
        if tip:
            Tooltip(self, tip)
        self.render()

    def set_hover(self, on):
        self.hover = on
        self.render()

    def set_selected(self, on):
        if on != self.selected:
            self.selected = on
            self.render()

    def set_enabled(self, on):
        if on != self.enabled:
            self.enabled = on
            self.configure(cursor='hand2' if on else '')
            self.render()

    def on_click(self, e):
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        if self.enabled and inside and self.command:
            self.command()

    def render(self):
        fill, hover_fill, border, fg = self.STYLES[self.kind]
        if not self.enabled:
            fill, hover_fill, border, fg = self.DISABLED
        elif self.selected:
            fill, hover_fill, border, fg = self.SELECTED
        h, k = self.HEIGHT, self.SCALE

        if self.swatch:
            w = h
            fill = hover_fill = self.swatch
            border = '#ffffff' if self.selected else '#334155'
            line = 3 if self.selected else 1
        else:
            fg_rgba = tuple(int(fg[i:i + 2], 16) / 255 for i in (1, 3, 5)) + (1.0,)
            icon_only = len(self.text.replace('\ufe0f', '')) == 1
            label = PAINTER.image(self.text, 12 if icon_only else 10, fg_rgba, bold=self.kind != 'tool')
            w = max(h, label.width + 2 * self.PAD)
            line = 1

        img = Image.new('RGBA', (w * k, h * k), BAR_BG)
        ImageDraw.Draw(img).rounded_rectangle(
            (0, 0, w * k - 1, h * k - 1), radius=9 * k,
            fill=hover_fill if self.hover and self.enabled else fill, outline=border, width=line * k)
        img = img.resize((w, h), RESAMPLE)
        if not self.swatch:
            img.alpha_composite(label, ((w - label.width) // 2, max(0, (h - label.height) // 2 + 1)))

        self.photo = ImageTk.PhotoImage(img)
        self.configure(width=w)
        self.delete('all')
        self.create_image(0, 0, anchor='nw', image=self.photo)


def separator(bar):
    tk.Frame(bar, bg='#2a3446', width=1, height=24).pack(side='left', padx=8)


class Editor(tk.Toplevel):
    def __init__(self, master, image, path=None, shot=None, on_saved=None):
        super().__init__(master)
        self.title('Clothesline Markup & Editor')
        self.configure(bg=BG_DARK)
        self.geometry('1020x720')

        self.base = image.convert('RGB')
        self.path = path
        self.shot = shot
        self.on_saved = on_saved
        self.marks = []  # list of (kind, pts, rgb_tuple)
        self.undo_stack = []

        self.tool = 'pen'
        self.color_hex = COLORS[0][1]
        self.color_rgb = COLORS[0][2]
        self.stroke = None
        self.crop = None
        self.start = (0, 0)
        self.scale, self.ox, self.oy = 1.0, 0, 0
        self.photo, self.photo_src, self.photo_size = None, None, None

        # Top Toolbar
        bar = tk.Frame(self, bg=BAR_BG, pady=8, padx=10)
        bar.pack(side='top', fill='x')

        # Clean Emoji Tool Buttons
        self.tool_buttons = {}
        tool_defs = [
            ('pen', '✏️', 'Pen tool'),
            ('circle', '⭕\ufe0f', 'Circle tool'),
            ('box', '⬜\ufe0f', 'Box tool'),
            ('crop', '✂️', 'Crop tool (draw box, then press Enter)'),
        ]
        for name, icon, tip in tool_defs:
            btn = PillButton(bar, icon, command=lambda n=name: self.set_tool(n), tip=tip)
            btn.pack(side='left', padx=3)
            self.tool_buttons[name] = btn

        # Color Palette
        separator(bar)
        self.color_buttons = {}
        for cname, chex, crgb in COLORS:
            cbtn = PillButton(bar, swatch=chex, tip=f'{cname} ink',
                              command=lambda ch=chex, cr=crgb: self.set_color(ch, cr))
            cbtn.pack(side='left', padx=3)
            self.color_buttons[chex] = cbtn

        # Undo & Apply Crop Buttons
        separator(bar)
        self.undo_btn = PillButton(bar, '↩️', command=self.undo, tip='Undo (Ctrl+Z)')
        self.undo_btn.pack(side='left', padx=3)

        self.apply_btn = PillButton(bar, '✂️ Apply', command=self.apply_crop, kind='primary',
                                    tip='Apply Crop (Enter)')
        self.apply_btn.set_enabled(False)
        self.apply_btn.pack(side='left', padx=3)

        # Right-side action buttons: Save directly, Save Copy, Copy to clipboard
        self.save_btn = PillButton(bar, '💾 Save', command=self.save, kind='primary',
                                   tip='Save in-place (Ctrl+S)')
        self.save_btn.pack(side='right', padx=3)

        self.copy_btn = PillButton(bar, '📋 Copy', command=self.copy_to_clip, kind='success',
                                   tip='Copy to clipboard (Ctrl+C)')
        self.copy_btn.pack(side='right', padx=3)

        self.save_copy_btn = PillButton(bar, '💾 Save Copy…', command=self.save_copy_as,
                                        tip='Save as a new copy...')
        self.save_copy_btn.pack(side='right', padx=3)

        self.msg = tk.Label(bar, text='', bg=BAR_BG, fg='#38bdf8', font=('DejaVu Sans', 8))
        self.msg.pack(side='right', padx=8)

        # Canvas for drawing
        self.canvas = tk.Canvas(self, bg='#0b0e14', highlightthickness=0, cursor='crosshair')
        self.canvas.pack(fill='both', expand=True)

        # Bottom shortcut hints
        hint_bar = tk.Frame(self, bg=BAR_BG, pady=3, padx=8)
        hint_bar.pack(side='bottom', fill='x')
        tk.Label(hint_bar, text='Shortcuts: Ctrl+S (Save) • Enter (Apply Crop) • Ctrl+Z (Undo) • Ctrl+C (Copy) • Esc (Close)',
                 bg=BAR_BG, fg=TEXT_MUTED, font=('DejaVu Sans', 8)).pack(side='left')

        # Events
        self.canvas.bind('<Configure>', lambda e: self.redraw())
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.bind('<Control-z>', lambda e: self.undo())
        self.bind('<Control-s>', lambda e: self.save())
        self.bind('<Control-c>', lambda e: self.copy_to_clip())
        self.bind('<Return>', lambda e: self.apply_crop())
        self.bind('<Escape>', lambda e: self.on_close())
        self.protocol('WM_DELETE_WINDOW', self.on_close)

        self.set_tool('pen')
        self.set_color(COLORS[0][1], COLORS[0][2])
        self.lift()
        self.focus_force()

    # --- Tool & Color State ---
    def set_tool(self, name):
        self.tool = name
        self.stroke = None
        self.crop = None
        self.apply_btn.set_enabled(False)
        for n, btn in self.tool_buttons.items():
            btn.set_selected(n == name)
        self.redraw()

    def set_color(self, hex_val, rgb_val):
        self.color_hex = hex_val
        self.color_rgb = rgb_val
        for ch, btn in self.color_buttons.items():
            btn.set_selected(ch == hex_val)

    # --- Coordinates ---
    def to_picture(self, x, y):
        w, h = self.base.size
        px = min(max((x - self.ox) / self.scale, 0), w)
        py = min(max((y - self.oy) / self.scale, 0), h)
        return px, py

    def press(self, e):
        p = self.to_picture(e.x, e.y)
        self.start = p
        if self.tool == 'crop':
            self.crop = (p[0], p[1], p[0], p[1])
            self.apply_btn.set_enabled(False)
        elif self.tool == 'pen':
            self.stroke = [p]
        else:
            self.stroke = [p, p]
        self.redraw()

    def drag(self, e):
        p = self.to_picture(e.x, e.y)
        if self.tool == 'crop' and self.crop is not None:
            self.crop = (self.start[0], self.start[1], p[0], p[1])
        elif self.tool == 'pen' and self.stroke is not None:
            self.stroke.append(p)
        elif self.stroke is not None:
            self.stroke[-1] = p
        self.redraw()

    def release(self, e):
        if self.tool == 'crop':
            ok = False
            if self.crop is not None:
                x0, y0, x1, y1 = normalized(self.crop)
                ok = (x1 - x0 > 5 and y1 - y0 > 5)
            self.apply_btn.set_enabled(ok)
        elif self.stroke is not None:
            pts = self.stroke
            if len(pts) > 1 and (self.tool == 'pen' or pts[0] != pts[-1]):
                self.commit(self.tool, pts, self.color_rgb)
            self.stroke = None
        self.redraw()

    def commit(self, kind, pts, color_rgb):
        self.undo_stack.append((self.base, list(self.marks)))
        self.marks.append((kind, list(pts), color_rgb))

    def undo(self):
        if not self.undo_stack:
            return
        self.base, self.marks = self.undo_stack.pop()
        self.crop = None
        self.apply_btn.set_enabled(False)
        self.redraw()

    def apply_crop(self):
        if self.crop is None or self.tool != 'crop':
            return
        x0, y0, x1, y1 = normalized(self.crop)
        if x1 - x0 < 5 or y1 - y0 < 5:
            return
        box = (round(x0), round(y0), round(x1), round(y1))
        self.undo_stack.append((self.base, list(self.marks)))
        self.base = self.base.crop(box)
        self.marks = shift_marks(self.marks, box[0], box[1])
        self.crop = None
        self.apply_btn.set_enabled(False)
        self.redraw()
        self.set_tool('pen')

    # --- Redraw & Render ---
    def redraw(self):
        c = self.canvas
        cw, ch = c.winfo_width(), c.winfo_height()
        if cw < 20 or ch < 20:
            return
        c.delete('all')
        w, h = self.base.size
        self.scale = min(cw / w, ch / h)
        dw, dh = max(1, round(w * self.scale)), max(1, round(h * self.scale))
        self.ox, self.oy = (cw - dw) // 2, (ch - dh) // 2

        if self.photo_src is not self.base or self.photo_size != (dw, dh):
            self.photo = ImageTk.PhotoImage(self.base.resize((dw, dh), RESAMPLE))
            self.photo_src, self.photo_size = self.base, (dw, dh)

        c.create_image(self.ox, self.oy, image=self.photo, anchor='nw')

        for item in self.marks:
            kind, pts = item[0], item[1]
            col_rgb = item[2] if len(item) > 2 else self.color_rgb
            col_hex = '#%02x%02x%02x' % col_rgb
            self.draw_mark(kind, pts, col_hex)

        if self.stroke is not None:
            self.draw_mark(self.tool, self.stroke, self.color_hex)

        if self.crop is not None:
            x0, y0, x1, y1 = normalized(self.crop)
            s = self.scale
            c.create_rectangle(self.ox + x0 * s, self.oy + y0 * s, self.ox + x1 * s, self.oy + y1 * s,
                               outline='#ffffff', dash=(6, 4), width=2)

    def draw_mark(self, kind, pts, color_hex):
        s, c = self.scale, self.canvas
        d = [(self.ox + x * s, self.oy + y * s) for x, y in pts]
        width = max(2, round(4 * s))
        if len(d) < 2:
            return
        if kind == 'pen':
            c.create_line([v for p in d for v in p], fill=color_hex, width=width,
                          capstyle='round', joinstyle='round')
        else:
            (x0, y0), (x1, y1) = d[0], d[-1]
            make = c.create_oval if kind == 'circle' else c.create_rectangle
            make(x0, y0, x1, y1, outline=color_hex, width=width)

    # --- Output Actions ---
    def copy_to_clip(self):
        final_img = bake_marks(self.base, self.marks)
        ok = copy_image_to_clipboard(final_img)
        if ok:
            self.msg.configure(text='✓ Copied!')
        else:
            self.msg.configure(text='Copy failed')

    def save(self):
        """Save changes directly to the screenshot file and update the clothesline card."""
        final_img = bake_marks(self.base, self.marks)
        saved_path = self.path

        if saved_path and os.path.exists(saved_path):
            try:
                final_img.save(saved_path)
            except Exception as e:
                messagebox.showerror('Error Saving', f'Could not save screenshot: {e}', parent=self)
                return
        else:
            # File does not exist yet (e.g. from clipboard)
            target = filedialog.asksaveasfilename(parent=self, defaultextension='.png',
                                                  initialfile='screenshot.png',
                                                  filetypes=[('PNG Image', '*.png'), ('JPEG Image', '*.jpg')])
            if not target:
                return
            saved_path = target
            try:
                final_img.save(saved_path)
                self.path = saved_path
            except Exception as e:
                messagebox.showerror('Error Saving', f'Could not save screenshot: {e}', parent=self)
                return

        # Update the Shot object directly so the clothesline reflects changes immediately
        if self.shot is not None:
            self.shot.image = final_img
            self.shot.path = saved_path
            self.shot.thumb = None
            self.shot.photo_normal = None
            self.shot.photo_hover = None
            self.shot.ensure_thumb()

        if self.on_saved:
            self.on_saved(self.shot, final_img, saved_path)

        # Clear undo stack to mark as saved
        self.marks.clear()
        self.undo_stack.clear()
        self.destroy()

    def save_copy_as(self):
        """Save as a separate file without modifying the original."""
        final_img = bake_marks(self.base, self.marks)
        if self.path:
            stem, ext = os.path.splitext(self.path)
            init_file = f'{stem}-edited{ext}'
        else:
            init_file = 'screenshot-edited.png'

        target = filedialog.asksaveasfilename(parent=self, defaultextension='.png',
                                              initialfile=os.path.basename(init_file),
                                              filetypes=[('PNG Image', '*.png'), ('JPEG Image', '*.jpg')])
        if not target:
            return
        try:
            final_img.save(target)
            self.msg.configure(text='✓ Saved copy')
        except Exception as e:
            messagebox.showerror('Error Saving', f'Could not save file: {e}', parent=self)

    def on_close(self):
        if self.marks or self.undo_stack:
            resp = messagebox.askyesnocancel('Unsaved Changes',
                                              'Do you want to save your edits before closing?',
                                              parent=self)
            if resp is True:
                self.save()
            elif resp is False:
                self.destroy()
        else:
            self.destroy()


def run_standalone(file_path):
    """Open the editor on one picture in its own window; returns when it closes."""
    if not os.path.exists(file_path):
        return
    root = tk.Tk()
    root.withdraw()
    im = Image.open(file_path).convert('RGB')
    ed = Editor(root, im, path=file_path, on_saved=lambda shot, img, path: root.destroy())
    ed.protocol('WM_DELETE_WINDOW', lambda: (ed.on_close(), root.destroy() if not ed.winfo_exists() else None))
    ed.bind('<Destroy>', lambda e: root.destroy() if e.widget == ed else None)
    root.mainloop()


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        run_standalone(sys.argv[1])


