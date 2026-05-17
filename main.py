import tkinter as tk
from tkinter import font as tkfont, messagebox
import time, threading, sys, os, struct, tempfile, subprocess

#  SOUND ENGINE  — generates real WAV bytes with numpy, plays cross-platform

def _make_wav(freqs_durs, sr=44100, volume=0.82):
    """
    Build an in-memory WAV file from a list of (freq_hz, duration_sec) tuples.
    Returns raw bytes ready to write to a .wav file or pipe into a player.
    """
    try:
        import numpy as np
        frames = b""
        for freq, dur in freqs_durs:
            n   = int(sr * dur)
            t   = np.linspace(0, dur, n, endpoint=False)
            # Sine wave + small harmonic for a richer bell tone
            sig = (np.sin(2 * np.pi * freq * t)
                   + 0.3 * np.sin(2 * np.pi * freq * 2 * t)
                   + 0.1 * np.sin(2 * np.pi * freq * 3 * t))
            sig /= sig.max()               # normalise
            # Exponential decay envelope (bell-like fade)
            env  = np.exp(-3.5 * t / dur)
            pcm  = (sig * env * volume * 32767).astype(np.int16)
            frames += pcm.tobytes()
    except ImportError:
        # Fallback: 440 Hz square wave without numpy
        frames = b""
        for freq, dur in freqs_durs:
            n      = int(sr * dur)
            period = sr // freq
            pcm    = bytearray(n * 2)
            for i in range(n):
                val = 20000 if (i % period) < (period // 2) else -20000
                struct.pack_into('<h', pcm, i * 2, val)
            frames += bytes(pcm)

    num_ch, bps = 1, 16
    byte_rate   = sr * num_ch * bps // 8
    block_align = num_ch * bps // 8
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + len(frames), b'WAVE',
        b'fmt ', 16, 1, num_ch, sr, byte_rate, block_align, bps,
        b'data', len(frames)
    )
    return header + frames


# Alarm melody: ascending arpeggio × 2
_MELODY = [
    (523, 0.12), (659, 0.12), (784, 0.12), (1047, 0.22),
    (784, 0.10), (1047, 0.30),
]


def _play_wav_bytes(wav_bytes):
    """Write wav_bytes to a temp file and play it with the best available tool."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(wav_bytes)
    tmp.flush()
    tmp.close()
    path = tmp.name

    played = False
    try:
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME)
            played = True
        elif sys.platform == "darwin":
            subprocess.run(["afplay", path],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            played = True
        else:
            # Linux — try player chain
            for player, args in [
                ("ffplay", ["ffplay", "-nodisp", "-autoexit",
                            "-loglevel", "quiet", path]),
                ("aplay",  ["aplay", "-q", path]),
                ("paplay", ["paplay", path]),
                ("mpg123", ["mpg123", "-q", path]),
            ]:
                if subprocess.run(["which", player],
                                   capture_output=True).returncode == 0:
                    subprocess.run(args,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    played = True
                    break
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass

    if not played:
        # Last resort: terminal bell
        sys.stdout.write("\a")
        sys.stdout.flush()
        time.sleep(0.5)


ALARM_DURATION_SEC = 60   # ← alarm বাজবে ঠিক এতক্ষণ, তারপর আপনা-আপনি বন্ধ

def _alarm_sound_loop(stop_event, done_callback):
    """
    Melody লুপ করে বাজায় ALARM_DURATION_SEC সেকেন্ড পর্যন্ত।
    সময় শেষ হলে (বা stop_event সেট হলে) done_callback() কল করে UI আপডেট করে।
    """
    wav      = _make_wav(_MELODY)
    deadline = time.time() + ALARM_DURATION_SEC

    while not stop_event.is_set() and time.time() < deadline:
        _play_wav_bytes(wav)
        # প্রতিটি play শেষে চেক করি সময় শেষ হয়েছে কিনা
        if stop_event.is_set() or time.time() >= deadline:
            break
        time.sleep(0.15)

    # সময় শেষ — নিজে থেকে বন্ধ করো
    if not stop_event.is_set():
        stop_event.set()
        done_callback()   # Tk main thread-এ _auto_stop চালাবে


#  THEME

C = {
    "bg":      "#0b0d12",
    "card":    "#12151f",
    "card2":   "#181c28",
    "border":  "#1e2235",
    "cyan":    "#00e5ff",
    "violet":  "#8b5cf6",
    "amber":   "#fbbf24",
    "green":   "#22c55e",
    "red":     "#ef4444",
    "red_dim": "#3b0000",
    "text":    "#e2e8f0",
    "muted":   "#475569",
    "sub":     "#64748b",
}

SNOOZE_MINUTES = 5


#  HELPERS

def get_greeting(h):
    if 5  <= h < 12: return "☀   Good Morning"
    if 12 <= h < 17: return "🌤   Good Afternoon"
    if 17 <= h < 21: return "🌇   Good Evening"
    return "🌙   Good Night"


def get_time_parts():
    n   = time.localtime()
    h12 = n.tm_hour % 12 or 12
    return dict(
        hour  = f"{h12:02d}",
        min   = f"{n.tm_min:02d}",
        sec   = f"{n.tm_sec:02d}",
        ampm  = "AM" if n.tm_hour < 12 else "PM",
        date  = time.strftime("%B %d, %Y", n),
        day   = time.strftime("%A", n),
        raw_h = n.tm_hour,
        raw_m = n.tm_min,
        raw_s = n.tm_sec,
    )


#  ALARM DATA

class Alarm:
    _ctr = 0

    def __init__(self, hour, minute, label=""):
        Alarm._ctr += 1
        self.id      = Alarm._ctr
        self.hour    = hour
        self.minute  = minute
        self.label   = label or f"Alarm {self.id}"
        self.enabled = True
        self.ringing = False

    @property
    def time_str(self):
        h12  = self.hour % 12 or 12
        ampm = "AM" if self.hour < 12 else "PM"
        return f"{h12:02d}:{self.minute:02d} {ampm}"

    def matches(self, h, m, s):
        return self.enabled and not self.ringing \
               and h == self.hour and m == self.minute and s == 0


#  APPLICATION

class DigitalClockApp:

    def __init__(self, root):
        self.root          = root
        self.running       = True
        self.blink_on      = True
        self.alarms        = []
        self.ringing_alarm = None
        self._stop_ev      = threading.Event()
        self._flashing     = False
        self._flash_phase  = 0

        self._setup_window()
        self._build_fonts()
        self._build_ui()
        self._tick()

    # ── Window ───────────────────────────────────────────────────────
    def _setup_window(self):
        self.root.title("Digital Clock & Alarm")
        self.root.resizable(False, False)
        self.root.configure(bg=C["bg"])
        W, H = 800, 600
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")

    # ── Fonts ─────────────────────────────────────────────────────────
    def _build_fonts(self):
        mono = "Courier New"
        sans = "Segoe UI" if sys.platform == "win32" else "Helvetica"
        self.f_clock = tkfont.Font(family=mono, size=66, weight="bold")
        self.f_sec   = tkfont.Font(family=mono, size=30, weight="bold")
        self.f_ampm  = tkfont.Font(family=sans, size=15, weight="bold")
        self.f_day   = tkfont.Font(family=sans, size=20, weight="bold")
        self.f_date  = tkfont.Font(family=sans, size=12)
        self.f_greet = tkfont.Font(family=sans, size=12, slant="italic")
        self.f_btn   = tkfont.Font(family=sans, size=10, weight="bold")
        self.f_lbl   = tkfont.Font(family=sans, size=11)
        self.f_alrm  = tkfont.Font(family=mono, size=13, weight="bold")
        self.f_tiny  = tkfont.Font(family=sans, size=10, weight="bold")
        self.f_ring  = tkfont.Font(family=sans, size=13, weight="bold")

    # ── Master layout ─────────────────────────────────────────────────
    def _build_ui(self):
        self.left = tk.Frame(self.root, bg=C["bg"], width=460)
        self.left.pack(side="left", fill="both")
        self.left.pack_propagate(False)

        tk.Frame(self.root, bg=C["border"], width=2).pack(
            side="left", fill="y", pady=16)

        self.right = tk.Frame(self.root, bg=C["bg"], width=340)
        self.right.pack(side="left", fill="both", expand=True)
        self.right.pack_propagate(False)

        self._build_clock_panel()
        self._build_alarm_panel()

    # ── Clock panel ───────────────────────────────────────────────────
    def _build_clock_panel(self):
        p = self.left

        self.lbl_greet = tk.Label(p, text="", font=self.f_greet,
                                  bg=C["bg"], fg=C["amber"])
        self.lbl_greet.pack(pady=(20, 0))

        self.lbl_day = tk.Label(p, text="", font=self.f_day,
                                bg=C["bg"], fg=C["cyan"])
        self.lbl_day.pack(pady=(4, 0))

        self.lbl_date = tk.Label(p, text="", font=self.f_date,
                                 bg=C["bg"], fg=C["muted"])
        self.lbl_date.pack(pady=(2, 10))

        tk.Frame(p, height=2, width=380, bg=C["violet"]).pack()

        # ── Main time row ─────────────────────────────────────────
        row = tk.Frame(p, bg=C["bg"])
        row.pack(pady=(10, 0))

        self.lbl_hm = tk.Label(row, text="00:00", font=self.f_clock,
                               bg=C["bg"], fg=C["text"])
        self.lbl_hm.pack(side="left")

        sub = tk.Frame(row, bg=C["bg"])
        sub.pack(side="left", anchor="s", pady=(0, 10))

        self.lbl_col2 = tk.Label(sub, text=":", font=self.f_sec,
                                 bg=C["bg"], fg=C["cyan"])
        self.lbl_col2.pack(side="left")

        self.lbl_sec = tk.Label(sub, text="00", font=self.f_sec,
                                bg=C["bg"], fg=C["cyan"])
        self.lbl_sec.pack(side="left")

        self.lbl_ampm = tk.Label(sub, text="AM", font=self.f_ampm,
                                 bg=C["bg"], fg=C["violet"], width=3)
        self.lbl_ampm.pack(side="left", padx=(8, 0), anchor="s", pady=(0, 2))

        # ── Controls ──────────────────────────────────────────────
        ctrl = tk.Frame(p, bg=C["bg"])
        ctrl.pack(pady=(12, 0))
        self._btn(ctrl, "⏸  Pause",  C["cyan"],  self._pause ).pack(side="left", padx=6)
        self._btn(ctrl, "▶  Resume", C["green"], self._resume).pack(side="left", padx=6)

        # ── Sound test button ─────────────────────────────────────
        self._btn(p, "🔊  Test Sound", C["amber"],
                  self._test_sound).pack(pady=(8, 0))

        # ── Ringing banner (packed only when alarm fires) ─────────
        self.ring_frame = tk.Frame(p, bg=C["red_dim"], pady=4)

        tk.Label(self.ring_frame, text="🔔  ALARM RINGING",
                 font=self.f_ring, bg=C["red_dim"],
                 fg="#fca5a5").pack(pady=(10, 0))

        self.ring_lbl = tk.Label(self.ring_frame, text="",
                                 font=self.f_lbl, bg=C["red_dim"],
                                 fg="#fca5a5")
        self.ring_lbl.pack(pady=(2, 4))

        rb = tk.Frame(self.ring_frame, bg=C["red_dim"])
        rb.pack(pady=(4, 12))
        self._btn(rb, f"💤  Snooze {SNOOZE_MINUTES} min",
                  C["amber"], self._snooze   ).pack(side="left", padx=8)
        self._btn(rb, "⏹  Stop Alarm",
                  C["red"],   self._stop_alarm).pack(side="left", padx=8)

    # ── Alarm panel ───────────────────────────────────────────────────
    def _build_alarm_panel(self):
        p = self.right

        hdr = tk.Frame(p, bg=C["card"], pady=10)
        hdr.pack(fill="x", padx=10, pady=(14, 0))
        tk.Label(hdr, text="🔔  ALARMS", font=self.f_tiny,
                 bg=C["card"], fg=C["cyan"]).pack()

        # Form
        form = tk.Frame(p, bg=C["card2"], padx=12, pady=10)
        form.pack(fill="x", padx=10, pady=(8, 0))

        tr = tk.Frame(form, bg=C["card2"])
        tr.pack(fill="x", pady=(0, 4))

        tk.Label(tr, text="Hour:", font=self.f_lbl,
                 bg=C["card2"], fg=C["muted"]).grid(row=0, column=0, sticky="w")
        self.spin_h = tk.Spinbox(tr, from_=0, to=23, width=4,
                                 format="%02.0f", font=self.f_alrm,
                                 bg=C["card"], fg=C["text"],
                                 buttonbackground=C["card"],
                                 insertbackground=C["text"],
                                 relief="flat", bd=3)
        self.spin_h.grid(row=0, column=1, padx=(4, 12), pady=3)

        tk.Label(tr, text="Min:", font=self.f_lbl,
                 bg=C["card2"], fg=C["muted"]).grid(row=0, column=2, sticky="w")
        self.spin_m = tk.Spinbox(tr, from_=0, to=59, width=4,
                                 format="%02.0f", font=self.f_alrm,
                                 bg=C["card"], fg=C["text"],
                                 buttonbackground=C["card"],
                                 insertbackground=C["text"],
                                 relief="flat", bd=3)
        self.spin_m.grid(row=0, column=3, padx=4, pady=3)

        lr = tk.Frame(form, bg=C["card2"])
        lr.pack(fill="x", pady=(2, 4))
        tk.Label(lr, text="Label:", font=self.f_lbl,
                 bg=C["card2"], fg=C["muted"]).pack(side="left")
        self.entry_lbl = tk.Entry(lr, width=15, font=self.f_lbl,
                                  bg=C["card"], fg=C["text"],
                                  insertbackground=C["text"],
                                  relief="flat", bd=3)
        self.entry_lbl.pack(side="left", padx=(6, 0))
        self.entry_lbl.insert(0, "Wake up")

        self._btn(form, "＋  Set Alarm", C["violet"],
                  self._add_alarm).pack(pady=(6, 2))

        # List
        lh = tk.Frame(p, bg=C["bg"])
        lh.pack(fill="x", padx=10, pady=(10, 2))
        tk.Label(lh, text="Scheduled Alarms", font=self.f_tiny,
                 bg=C["bg"], fg=C["sub"]).pack(side="left")

        self.list_canvas = tk.Canvas(p, bg=C["bg"],
                                     highlightthickness=0, bd=0)
        self.list_canvas.pack(fill="both", expand=True,
                              padx=10, pady=(0, 10))

        self.list_frame = tk.Frame(self.list_canvas, bg=C["bg"])
        self.list_canvas.create_window((0, 0), window=self.list_frame,
                                       anchor="nw")
        self.list_frame.bind(
            "<Configure>",
            lambda e: self.list_canvas.configure(
                scrollregion=self.list_canvas.bbox("all")))

        self._refresh_list()

    # ── Alarm list ────────────────────────────────────────────────────
    def _refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        if not self.alarms:
            tk.Label(self.list_frame, text="No alarms set.",
                     font=self.f_lbl, bg=C["bg"],
                     fg=C["muted"]).pack(pady=20)
            return
        for alarm in self.alarms:
            self._alarm_card(alarm)

    def _alarm_card(self, alarm):
        ringing = alarm.ringing
        bg  = C["red_dim"] if ringing else (C["card"] if alarm.enabled else C["card2"])
        bdr = C["red"]     if ringing else (C["violet"] if alarm.enabled else C["border"])

        card = tk.Frame(self.list_frame, bg=bg, pady=6, padx=10,
                        highlightbackground=bdr, highlightthickness=1)
        card.pack(fill="x", pady=4)

        top = tk.Frame(card, bg=bg)
        top.pack(fill="x")

        time_fg = "#fca5a5" if ringing else (C["cyan"] if alarm.enabled else C["muted"])
        tk.Label(top, text=alarm.time_str, font=self.f_alrm,
                 bg=bg, fg=time_fg).pack(side="left")
        lbl_fg  = "#fca5a5" if ringing else (C["text"] if alarm.enabled else C["muted"])
        tk.Label(top, text=alarm.label, font=self.f_lbl,
                 bg=bg, fg=lbl_fg).pack(side="left", padx=(8, 0))
        if ringing:
            tk.Label(top, text="🔔 RINGING", font=self.f_tiny,
                     bg=bg, fg="#fca5a5").pack(side="right")

        btns = tk.Frame(card, bg=bg)
        btns.pack(fill="x", pady=(5, 0))

        tog_txt = "● ON"  if alarm.enabled else "○ OFF"
        tog_fg  = C["green"] if alarm.enabled else C["muted"]
        tk.Button(btns, text=tog_txt, font=self.f_tiny,
                  bg=C["card2"], fg=tog_fg, relief="flat", bd=0,
                  padx=8, pady=3, cursor="hand2",
                  command=lambda a=alarm: self._toggle(a)
                  ).pack(side="left", padx=(0, 4))

        tk.Button(btns, text="✕ Delete", font=self.f_tiny,
                  bg=C["card2"], fg=C["red"], relief="flat", bd=0,
                  padx=8, pady=3, cursor="hand2",
                  command=lambda a=alarm: self._delete(a)
                  ).pack(side="left")

    # ── Alarm actions ─────────────────────────────────────────────────
    def _add_alarm(self):
        try:
            h = int(self.spin_h.get())
            m = int(self.spin_m.get())
        except ValueError:
            messagebox.showerror("Invalid", "Enter a valid hour and minute.")
            return
        if not (0 <= h <= 23 and 0 <= m <= 59):
            messagebox.showerror("Invalid", "Hour: 0–23  Minute: 0–59")
            return
        lbl = self.entry_lbl.get().strip() or "Alarm"
        self.alarms.append(Alarm(h, m, lbl))
        self._refresh_list()

    def _toggle(self, alarm):
        alarm.enabled = not alarm.enabled
        self._refresh_list()

    def _delete(self, alarm):
        if alarm.ringing:
            self._stop_alarm()
        if alarm in self.alarms:
            self.alarms.remove(alarm)
        self._refresh_list()

    def _ring_alarm(self, alarm):
        alarm.ringing      = True
        self.ringing_alarm = alarm
        self._ring_start   = time.time()          # কখন শুরু হয়েছে রাখি

        self.ring_lbl.config(text=f"{alarm.label}  —  {alarm.time_str}")
        self.ring_frame.pack(fill="x", padx=16, pady=(10, 0))

        # 10s countdown শুরু
        self._update_countdown()

        # Background thread-এ sound চালাও; শেষে _auto_stop callback
        self._stop_ev.clear()
        threading.Thread(
            target=_alarm_sound_loop,
            args=(self._stop_ev, self._auto_stop),
            daemon=True
        ).start()

        self._flashing    = True
        self._flash_phase = 0
        self._flash()
        self._refresh_list()

    def _update_countdown(self):
        """প্রতি সেকেন্ডে countdown label আপডেট করে।"""
        if not self.ringing_alarm:
            return
        elapsed   = time.time() - self._ring_start
        remaining = max(0, ALARM_DURATION_SEC - int(elapsed))
        self.ring_lbl.config(
            text=f"{self.ringing_alarm.label}  —  {self.ringing_alarm.time_str}"
                 f"\n⏱  {remaining}s বাকি আছে"
        )
        if remaining > 0 and self.ringing_alarm:
            self.root.after(1000, self._update_countdown)

    def _auto_stop(self):
        """Sound thread 10s পর এটা call করে — Tk thread-safe।"""
        self.root.after(0, self._stop_alarm)   # Tk main loop-এ নিরাপদে চালাও

    def _stop_alarm(self):
        self._stop_ev.set()
        self._flashing = False
        if self.ringing_alarm:
            self.ringing_alarm.ringing = False
        self.ringing_alarm = None
        self.ring_frame.pack_forget()
        self.root.configure(bg=C["bg"])
        self.left.configure(bg=C["bg"])
        self._refresh_list()

    def _snooze(self):
        if not self.ringing_alarm:
            return
        alarm = self.ringing_alarm
        self._stop_alarm()
        total        = alarm.hour * 60 + alarm.minute + SNOOZE_MINUTES
        alarm.hour   = (total // 60) % 24
        alarm.minute = total % 60
        alarm.label  = "💤 " + alarm.label.lstrip("💤 ")
        alarm.enabled = True
        self._refresh_list()

    def _test_sound(self):
        """Play a short preview of the alarm sound."""
        def _play():
            wav = _make_wav(_MELODY[:3])
            _play_wav_bytes(wav)
        threading.Thread(target=_play, daemon=True).start()

    # ── Flash ─────────────────────────────────────────────────────────
    def _flash(self):
        if not self._flashing:
            return
        bg = "#1a0000" if self._flash_phase % 2 == 0 else C["bg"]
        self.root.configure(bg=bg)
        self.left.configure(bg=bg)
        self._flash_phase += 1
        self.root.after(450, self._flash)

    # ── Clock controls ────────────────────────────────────────────────
    def _pause(self):  self.running = False
    def _resume(self): self.running = True

    # ── Tick ──────────────────────────────────────────────────────────
    def _tick(self):
        if self.running:
            p = get_time_parts()

            self.blink_on = not self.blink_on
            colon = ":" if self.blink_on else " "
            c_col = C["cyan"] if self.blink_on else C["card"]

            self.lbl_hm.config(text=f"{p['hour']}{colon}{p['min']}")
            self.lbl_col2.config(fg=c_col)
            self.lbl_sec.config(text=p["sec"])
            self.lbl_ampm.config(text=p["ampm"])
            self.lbl_day.config(text=p["day"])
            self.lbl_date.config(text=p["date"])
            self.lbl_greet.config(text=get_greeting(p["raw_h"]))

            for alarm in self.alarms:
                if alarm.matches(p["raw_h"], p["raw_m"], p["raw_s"]):
                    self._ring_alarm(alarm)

        self.root.after(1000, self._tick)

    # ── Button factory ────────────────────────────────────────────────
    def _btn(self, parent, text, color, cmd):
        return tk.Button(
            parent, text=text, font=self.f_btn,
            bg=C["card2"], fg=color,
            activebackground=C["card"], activeforeground=color,
            relief="flat", bd=0, padx=14, pady=7,
            cursor="hand2", command=cmd,
        )


#  ENTRY POINT

if __name__ == "__main__":
    root = tk.Tk()
    DigitalClockApp(root)
    root.mainloop()