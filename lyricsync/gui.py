"""윈도우용 창. tkinter만 쓴다 — 파이썬에 기본 포함이라 번들 용량이 늘지 않는다.

스레드 규칙: 작업은 워커 스레드에서 돌리고, 위젯은 메인 스레드에서만 만진다.
워커는 큐에 메시지만 넣고, 메인 스레드가 after()로 주기적으로 비운다.
이걸 지키지 않으면 tkinter가 조용히 깨진다.
"""

from __future__ import annotations

import queue
import sys
import threading
import traceback
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as exc:  # 헤드리스 리눅스 등
    raise ImportError(f"tkinter를 쓸 수 없습니다: {exc}") from exc

from . import __version__
from .asr import INSTALL_HINT, detect_backend
from .pipeline import (
    Cancelled,
    STAGES,
    SyncOptions,
    quality_report,
    run_sync,
)
from .runtime import app_data_dir, ffmpeg_status

AUDIO_TYPES = [
    ("음원 파일", "*.mp3 *.m4a *.flac *.wav *.ogg *.opus *.aac *.wma"),
    ("모든 파일", "*.*"),
]
LYRIC_TYPES = [("텍스트 파일", "*.txt *.lrc"), ("모든 파일", "*.*")]

MODELS = ["tiny", "base", "small", "medium", "large-v3"]
LANGUAGES = [("한국어", "ko"), ("영어", "en"), ("일본어", "ja"), ("자동 감지", "auto")]
FORMATS = [
    ("LRC (음악 플레이어)", "lrc", True),
    ("SRT (영상 자막)", "srt", False),
    ("VTT (웹 자막)", "vtt", False),
    ("JSON (가공용)", "json", False),
    ("TSV (엑셀로 보정)", "tsv", False),
]


class App:
    def __init__(self, root: tk.Tk, preset=None):
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None

        root.title(f"lyricsync {__version__} — 가사 싱크 자동 추출")
        root.minsize(680, 620)
        try:
            root.tk.call("tk", "scaling", 1.25)
        except tk.TclError:
            pass

        self._build(preset)
        self._check_environment()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._drain)

    # ------------------------------------------------------------------ UI

    def _build(self, preset) -> None:
        pad = {"padx": 10, "pady": 4}
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        # --- 입력 ---
        box = ttk.LabelFrame(main, text="입력", padding=10)
        box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(1, weight=1)

        self.audio_var = tk.StringVar(value=str(preset.audio) if preset and preset.audio else "")
        ttk.Label(box, text="음원").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.audio_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(box, text="찾기…", command=self._pick_audio, width=8).grid(
            row=0, column=2, **pad
        )

        self.lyrics_var = tk.StringVar(
            value=str(preset.lyrics) if preset and preset.lyrics else ""
        )
        ttk.Label(box, text="가사").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.lyrics_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(box, text="찾기…", command=self._pick_lyrics, width=8).grid(
            row=1, column=2, **pad
        )
        ttk.Button(box, text="지우기", command=lambda: self.lyrics_var.set(""), width=8).grid(
            row=1, column=3, **pad
        )

        ttk.Label(
            box,
            text="가사를 넣으면 글자는 그대로 두고 시간만 입힙니다 (오타 없음).\n"
                 "비워두면 받아쓰기까지 하지만 글자가 틀릴 수 있습니다.",
            foreground="#555",
            justify="left",
        ).grid(row=2, column=0, columnspan=4, sticky="w", padx=10, pady=(2, 0))

        self.outdir_var = tk.StringVar()
        ttk.Label(box, text="저장 위치").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(box, textvariable=self.outdir_var).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Button(box, text="찾기…", command=self._pick_outdir, width=8).grid(
            row=3, column=2, **pad
        )
        ttk.Label(box, text="비우면 음원과 같은 폴더", foreground="#555").grid(
            row=4, column=1, sticky="w", padx=10
        )

        # --- 출력 포맷 ---
        fbox = ttk.LabelFrame(main, text="출력 포맷", padding=10)
        fbox.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.fmt_vars: dict[str, tk.BooleanVar] = {}
        for i, (label, fmt, default) in enumerate(FORMATS):
            var = tk.BooleanVar(value=default)
            self.fmt_vars[fmt] = var
            ttk.Checkbutton(fbox, text=label, variable=var).grid(
                row=i // 3, column=i % 3, sticky="w", padx=8, pady=2
            )
        self.word_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            fbox, text="LRC에 단어별 타임스탬프 (가라오케식)", variable=self.word_var
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))

        # --- 설정 ---
        obox = ttk.LabelFrame(main, text="설정", padding=10)
        obox.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(obox, text="정확도").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.model_var = tk.StringVar(value="medium")
        ttk.Combobox(
            obox, textvariable=self.model_var, values=MODELS,
            state="readonly", width=10,
        ).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(
            obox, text="클수록 정확하고 느립니다. 처음 쓸 때 모델을 내려받습니다.",
            foreground="#555",
        ).grid(row=0, column=2, columnspan=2, sticky="w", padx=8)

        ttk.Label(obox, text="언어").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.lang_var = tk.StringVar(value="한국어")
        ttk.Combobox(
            obox, textvariable=self.lang_var, values=[n for n, _ in LANGUAGES],
            state="readonly", width=10,
        ).grid(row=1, column=1, sticky="w", padx=4)

        ttk.Label(obox, text="싱크 보정").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.offset_var = tk.StringVar(value="0.0")
        ttk.Entry(obox, textvariable=self.offset_var, width=10).grid(
            row=2, column=1, sticky="w", padx=4
        )
        ttk.Label(
            obox, text="초. 가사가 늦게 뜨면 음수로 (예: -0.3)", foreground="#555"
        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=8)

        self.sep_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            obox,
            text="보컬 분리 (느리지만 인식률이 크게 올라갑니다 — 권장)",
            variable=self.sep_var,
        ).grid(row=3, column=0, columnspan=4, sticky="w", padx=8, pady=(6, 0))

        # --- 실행 ---
        run_row = ttk.Frame(main)
        run_row.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        run_row.columnconfigure(2, weight=1)
        self.run_btn = ttk.Button(run_row, text="싱크 뽑기", command=self._start, width=14)
        self.run_btn.grid(row=0, column=0, padx=(0, 6))
        self.cancel_btn = ttk.Button(
            run_row, text="취소", command=self._request_cancel, width=10, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=1, padx=(0, 10))
        self.stage_var = tk.StringVar(value="대기 중")
        ttk.Label(run_row, textvariable=self.stage_var).grid(row=0, column=2, sticky="w")

        self.progress = ttk.Progressbar(main, mode="determinate", maximum=len(STAGES))
        self.progress.grid(row=4, column=0, sticky="ew", pady=(0, 8))

        # --- 로그 ---
        lbox = ttk.LabelFrame(main, text="진행 상황", padding=6)
        lbox.grid(row=5, column=0, sticky="nsew")
        main.rowconfigure(5, weight=1)
        lbox.columnconfigure(0, weight=1)
        lbox.rowconfigure(0, weight=1)

        self.log = tk.Text(lbox, height=12, wrap="word", state="disabled",
                           font=("Consolas", 9) if sys.platform == "win32" else None)
        self.log.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(lbox, orient="vertical", command=self.log.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=bar.set)
        self.log.tag_configure("warn", foreground="#a05000")
        self.log.tag_configure("error", foreground="#b00020")
        self.log.tag_configure("ok", foreground="#006400")
        self.log.tag_configure("dim", foreground="#666")

        # --- 상태줄 ---
        self.status_var = tk.StringVar()
        ttk.Label(main, textvariable=self.status_var, foreground="#555").grid(
            row=6, column=0, sticky="w", pady=(6, 0)
        )

    # --------------------------------------------------------- 환경 점검

    def _check_environment(self) -> None:
        """시작할 때 ffmpeg와 ASR 백엔드를 확인해 둔다.

        실행 버튼을 눌렀을 때 처음 알게 되면 사용자가 원인을 짚기 어렵다.
        """
        ok_ffmpeg, ffmpeg_msg = ffmpeg_status()
        backend = detect_backend()

        self._put(("log", f"lyricsync {__version__}", "dim"))
        self._put(("log", ffmpeg_msg, "ok" if ok_ffmpeg else "error"))
        if backend:
            self._put(("log", f"음성 인식 백엔드: {backend}", "ok"))
        else:
            self._put(("log", "음성 인식 백엔드를 찾을 수 없습니다", "error"))
            self._put(("log", INSTALL_HINT.strip(), "dim"))

        self._put(("log", f"모델 저장 위치: {app_data_dir() / 'models'}", "dim"))
        self._put(("log", "", None))

        if not ok_ffmpeg or not backend:
            self.status_var.set("실행 전 위 오류를 해결해야 합니다.")
            self.run_btn.configure(state="disabled")
        else:
            self.status_var.set("음원을 고르고 '싱크 뽑기'를 누르세요.")

    # ----------------------------------------------------------- 파일 선택

    def _pick_audio(self) -> None:
        path = filedialog.askopenfilename(title="음원 선택", filetypes=AUDIO_TYPES)
        if path:
            self.audio_var.set(path)
            # 같은 폴더에 동명의 가사 파일이 있으면 자동으로 집어준다
            guess = Path(path).with_suffix(".txt")
            if guess.exists() and not self.lyrics_var.get():
                self.lyrics_var.set(str(guess))
                self._append(f"가사 파일을 자동으로 찾았습니다: {guess.name}", "dim")

    def _pick_lyrics(self) -> None:
        path = filedialog.askopenfilename(title="가사 파일 선택", filetypes=LYRIC_TYPES)
        if path:
            self.lyrics_var.set(path)

    def _pick_outdir(self) -> None:
        path = filedialog.askdirectory(title="저장 폴더 선택")
        if path:
            self.outdir_var.set(path)

    # --------------------------------------------------------------- 실행

    def _collect(self) -> SyncOptions | None:
        """입력을 검증해 SyncOptions로. 문제가 있으면 알리고 None."""
        audio_text = self.audio_var.get().strip().strip('"')
        if not audio_text:
            messagebox.showwarning("음원 없음", "음원 파일을 골라주세요.")
            return None
        audio = Path(audio_text)
        if not audio.is_file():
            messagebox.showerror("음원 없음", f"파일을 찾을 수 없습니다:\n{audio}")
            return None

        lyrics_text = self.lyrics_var.get().strip().strip('"')
        lyrics = Path(lyrics_text) if lyrics_text else None
        if lyrics and not lyrics.is_file():
            messagebox.showerror("가사 없음", f"파일을 찾을 수 없습니다:\n{lyrics}")
            return None

        formats = [f for f, v in self.fmt_vars.items() if v.get()]
        if not formats:
            messagebox.showwarning("포맷 없음", "출력 포맷을 하나 이상 골라주세요.")
            return None

        try:
            offset = float(self.offset_var.get() or 0)
        except ValueError:
            messagebox.showerror("보정값 오류", "싱크 보정은 숫자여야 합니다. 예: -0.3")
            return None

        outdir = Path(self.outdir_var.get().strip() or audio.parent)
        outputs = {fmt: outdir / f"{audio.stem}.{fmt}" for fmt in formats}

        existing = [p.name for p in outputs.values() if p.exists()]
        if existing:
            if not messagebox.askyesno(
                "덮어쓸까요?",
                "이미 있는 파일을 덮어씁니다:\n\n" + "\n".join(existing),
            ):
                return None

        lang = dict(LANGUAGES).get(self.lang_var.get(), "ko")
        return SyncOptions(
            audio=audio,
            outputs=outputs,
            lyrics=lyrics,
            model=self.model_var.get(),
            language=lang,
            separate=self.sep_var.get(),
            offset=offset,
            word_level=self.word_var.get(),
        )

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        opts = self._collect()
        if opts is None:
            return

        self.cancel.clear()
        self.run_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.configure(value=0)
        self._append("", None)
        self._append(f"=== {opts.audio.name} ===", None)
        if opts.separate:
            self._append(
                "보컬 분리를 켰습니다. 3분 곡이면 CPU에서 수 분 걸릴 수 있습니다.", "dim"
            )

        self.worker = threading.Thread(target=self._work, args=(opts,), daemon=True)
        self.worker.start()

    def _work(self, opts: SyncOptions) -> None:
        """워커 스레드. 위젯은 절대 만지지 않고 큐로만 보고한다."""
        try:
            result = run_sync(
                opts,
                on_log=lambda m: self._put(("log", m, "warn" if "주의" in m else None)),
                on_stage=lambda name, i, n: self._put(("stage", name, i)),
                cancel=self.cancel,
            )
            self._put(("done", result, None))
        except Cancelled:
            self._put(("cancelled", None, None))
        except Exception as exc:
            self._put(("error", exc, traceback.format_exc()))

    def _request_cancel(self) -> None:
        self.cancel.set()
        self.cancel_btn.configure(state="disabled")
        self.stage_var.set("취소 중… 진행 중인 단계가 끝나면 멈춥니다")
        self._append("취소 요청 — 현재 단계가 끝나는 대로 멈춥니다.", "warn")

    # ----------------------------------------------------- 큐 → 메인 스레드

    def _put(self, msg: tuple) -> None:
        self.queue.put(msg)

    def _drain(self) -> None:
        """메인 스레드에서 큐를 비우며 화면을 갱신한다."""
        try:
            while True:
                kind, payload, extra = self.queue.get_nowait()
                if kind == "log":
                    self._append(payload, extra)
                elif kind == "stage":
                    self.stage_var.set(f"{payload} ({extra}/{len(STAGES)})")
                    self.progress.configure(value=extra)
                elif kind == "done":
                    self._finish_ok(payload)
                elif kind == "cancelled":
                    self._finish("취소됨", "취소했습니다.", "warn")
                elif kind == "error":
                    self._finish_error(payload, extra)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _append(self, text: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", (text or "") + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _finish(self, stage: str, msg: str, tag: str | None) -> None:
        self.stage_var.set(stage)
        self.run_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.progress.configure(value=0)
        if msg:
            self._append(msg, tag)

    def _finish_ok(self, result) -> None:
        self._finish("완료", "", None)
        self.progress.configure(value=len(STAGES))
        self._append("", None)
        for line in quality_report(result):
            tag = "warn" if line.strip().startswith("[") else None
            self._append(line, tag)
        self._append("", None)
        for path in result.written:
            self._append(f"저장됨: {path}", "ok")

        folder = result.written[0].parent if result.written else None
        self.status_var.set(f"완료 — {len(result.written)}개 파일 저장")
        if folder and messagebox.askyesno(
            "완료",
            f"{len(result.written)}개 파일을 저장했습니다.\n\n"
            + "\n".join(p.name for p in result.written)
            + "\n\n폴더를 열까요?",
        ):
            self._open_folder(folder)

    def _finish_error(self, exc: Exception, tb: str) -> None:
        self._finish("오류", "", None)
        self._append(f"오류: {exc}", "error")
        # 전체 트레이스백은 로그에만 남긴다. 대화상자에는 요약만.
        self._append(tb, "dim")
        self.status_var.set("실패 — 진행 상황 로그를 확인하세요.")
        messagebox.showerror("실패", str(exc)[:600])

    @staticmethod
    def _open_folder(folder: Path) -> None:
        import subprocess

        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(folder)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            pass

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("작업 중", "작업이 진행 중입니다. 정말 닫을까요?"):
                return
            self.cancel.set()
        self.root.destroy()


def launch(preset=None) -> int:
    """창을 띄우고 닫힐 때까지 돈다."""
    root = tk.Tk()
    App(root, preset=preset)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(launch())
