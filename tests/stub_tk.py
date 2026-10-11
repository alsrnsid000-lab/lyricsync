"""tkinter 대역. GUI 로직을 디스플레이 없이 실행하기 위한 것.

위젯은 호출을 기록하는 빈 껍데기로 두고, 변수(StringVar 등)와 대화상자만
실제로 동작하게 만든다. 이걸로 _build()가 터지지 않는지, _collect()의 입력
검증이 의도대로 동작하는지 확인할 수 있다.

외관은 이걸로 검증되지 않는다 — 윈도우에서 실제로 띄워 봐야 한다.
"""

from __future__ import annotations

import sys
import types


class TclError(Exception):
    pass


class _Var:
    def __init__(self, master=None, value=None):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class StringVar(_Var):
    def __init__(self, master=None, value=""):
        super().__init__(master, value)


class BooleanVar(_Var):
    def __init__(self, master=None, value=False):
        super().__init__(master, bool(value))


class IntVar(_Var):
    def __init__(self, master=None, value=0):
        super().__init__(master, int(value))


class _Widget:
    """모든 위젯의 공통 껍데기. 어떤 메서드를 불러도 조용히 받는다."""

    created: list[str] = []

    def __init__(self, master=None, **kw):
        self._kw = dict(kw)
        self.children: list[_Widget] = []
        _Widget.created.append(type(self).__name__)
        if isinstance(master, _Widget):
            master.children.append(self)

    # 레이아웃
    def grid(self, **kw): return self
    def pack(self, **kw): return self
    def place(self, **kw): return self
    def grid_configure(self, **kw): return self
    def columnconfigure(self, *a, **kw): return self
    def rowconfigure(self, *a, **kw): return self

    # 상태
    def configure(self, **kw):
        self._kw.update(kw)
        return self
    config = configure

    def cget(self, key): return self._kw.get(key)
    def __getitem__(self, key): return self._kw.get(key)

    # 텍스트 위젯
    def insert(self, index, text, tags=None): self._kw.setdefault("_text", []).append(text)
    def see(self, index): pass
    def delete(self, *a): self._kw["_text"] = []
    def tag_configure(self, *a, **kw): pass
    def yview(self, *a, **kw): pass

    def bind(self, *a, **kw): pass
    def destroy(self): pass

    # 스크롤바 연동 (Text.yscrollcommand=Scrollbar.set)
    def set(self, *a): pass
    def get(self): return 0.0, 1.0

    # 진행바
    def start(self, *a): pass
    def stop(self, *a): pass
    def step(self, *a): pass


class Frame(_Widget): pass
class Label(_Widget): pass
class Entry(_Widget): pass
class Button(_Widget): pass
class Checkbutton(_Widget): pass
class Combobox(_Widget): pass
class Progressbar(_Widget): pass
class Scrollbar(_Widget): pass
class LabelFrame(_Widget): pass
class Text(_Widget): pass
class Notebook(_Widget): pass
class Style(_Widget): pass


class _TkCall:
    def call(self, *args):
        raise TclError("stub: tk call 미지원")


class Tk(_Widget):
    def __init__(self, **kw):
        super().__init__(None, **kw)
        self.tk = _TkCall()
        self.scheduled: list[tuple[int, object]] = []
        self.destroyed = False
        self._protocols: dict = {}

    def title(self, text=None): self._kw["title"] = text
    def minsize(self, w=None, h=None): pass
    def geometry(self, spec=None): pass
    def iconbitmap(self, *a, **kw): pass
    def protocol(self, name, fn): self._protocols[name] = fn
    def after(self, ms, fn=None, *a):
        # 실제로 실행하지 않는다. 테스트가 원할 때 직접 호출한다.
        self.scheduled.append((ms, fn))
        return f"timer{len(self.scheduled)}"
    def after_cancel(self, tid): pass
    def mainloop(self): pass
    def destroy(self): self.destroyed = True
    def update(self): pass
    def update_idletasks(self): pass
    def withdraw(self): pass


class _Dialogs:
    """대화상자 기록기. 테스트가 응답을 미리 정해둘 수 있다."""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []
        self.yesno_answer = True

    def _record(self, kind, title, message):
        self.calls.append((kind, title, str(message)))

    def showinfo(self, title="", message="", **kw): self._record("info", title, message)
    def showwarning(self, title="", message="", **kw): self._record("warning", title, message)
    def showerror(self, title="", message="", **kw): self._record("error", title, message)
    def askyesno(self, title="", message="", **kw):
        self._record("askyesno", title, message)
        return self.yesno_answer

    def reset(self):
        self.calls.clear()
        self.yesno_answer = True

    @property
    def last(self):
        return self.calls[-1] if self.calls else None

    def kinds(self):
        return [c[0] for c in self.calls]


class _FileDialog:
    """파일 선택 대화상자 기록기. 돌려줄 경로를 미리 정해둔다."""

    def __init__(self):
        self.next_open = ""
        self.next_dir = ""
        self.calls: list[str] = []

    def askopenfilename(self, **kw):
        self.calls.append("askopenfilename")
        return self.next_open

    def askdirectory(self, **kw):
        self.calls.append("askdirectory")
        return self.next_dir

    def asksaveasfilename(self, **kw):
        self.calls.append("asksaveasfilename")
        return self.next_open


messagebox = _Dialogs()
filedialog = _FileDialog()


def install() -> tuple[_Dialogs, _FileDialog]:
    """sys.modules에 가짜 tkinter를 심는다. lyricsync.gui 임포트 전에 호출."""
    tk_mod = types.ModuleType("tkinter")
    for name in (
        "Tk", "StringVar", "BooleanVar", "IntVar", "Text", "Frame", "Label",
        "Entry", "Button", "Checkbutton", "Scrollbar", "TclError",
    ):
        setattr(tk_mod, name, globals()[name])

    ttk_mod = types.ModuleType("tkinter.ttk")
    for name in (
        "Frame", "Label", "Entry", "Button", "Checkbutton", "Combobox",
        "Progressbar", "Scrollbar", "LabelFrame", "Notebook", "Style",
    ):
        setattr(ttk_mod, name, globals()[name])

    fd_mod = types.ModuleType("tkinter.filedialog")
    fd_mod.askopenfilename = filedialog.askopenfilename
    fd_mod.askdirectory = filedialog.askdirectory
    fd_mod.asksaveasfilename = filedialog.asksaveasfilename

    mb_mod = types.ModuleType("tkinter.messagebox")
    mb_mod.showinfo = messagebox.showinfo
    mb_mod.showwarning = messagebox.showwarning
    mb_mod.showerror = messagebox.showerror
    mb_mod.askyesno = messagebox.askyesno

    tk_mod.ttk = ttk_mod
    tk_mod.filedialog = fd_mod
    tk_mod.messagebox = mb_mod

    sys.modules["tkinter"] = tk_mod
    sys.modules["tkinter.ttk"] = ttk_mod
    sys.modules["tkinter.filedialog"] = fd_mod
    sys.modules["tkinter.messagebox"] = mb_mod
    return messagebox, filedialog
