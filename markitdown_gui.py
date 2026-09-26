"""MarkItDown GUI - 파일/URL을 Markdown으로 변환.

설치: pip install "markitdown[all]" openai
실행: python markitdown_gui.py
"""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from markitdown import MarkItDown


NO_AI = "사용 안 함"
# 공급자 이름 -> (OpenAI 호환 API 주소, 키 환경변수, 기본 모델). 키 환경변수가 None이면 키 불필요.
PROVIDERS = {
    NO_AI: ("", None, ""),
    "NVIDIA": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "moonshotai/kimi-k3"),
    "OpenAI": ("https://api.openai.com/v1", "OPENAI_API_KEY", ""),
    "Anthropic": ("https://api.anthropic.com/v1/", "ANTHROPIC_API_KEY", "claude-sonnet-5"),
    "Google Gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", ""),
    "OpenRouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", ""),
    "Ollama (내 PC)": ("http://localhost:11434/v1", None, ""),
    "직접 입력": ("", "", ""),
}
IMAGE_PROMPT = ("이미지 속 모든 텍스트를 그대로 추출하고, 표는 Markdown 표로 만들어줘. "
                "텍스트가 없으면 이미지를 한국어로 자세히 설명해줘.")


def make_client(base_url, api_key):
    from openai import OpenAI  # AI를 쓸 때만 필요
    return OpenAI(api_key=api_key or "none", base_url=base_url, max_retries=3)


def make_converter(base_url="", api_key="", model=""):
    if not (base_url and model):
        return MarkItDown()
    return MarkItDown(llm_client=make_client(base_url, api_key), llm_model=model,
                      llm_prompt=IMAGE_PROMPT)


def md_name(source):
    """원본 경로/URL에서 저장용 .md 파일 이름을 만든다."""
    if source.startswith(("http://", "https://")):
        return "".join(c if c.isalnum() else "_" for c in source.split("//", 1)[1])[:80] + ".md"
    return os.path.splitext(os.path.basename(source))[0] + ".md"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MarkItDown 변환기")
        self.geometry("1000x650")
        self.sources = []   # 변환할 파일 경로 / URL
        self.results = {}   # source -> markdown 또는 오류 메시지
        self.q = queue.Queue()

        # 입력 영역
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="파일 추가", command=self.add_files).pack(side="left")
        ttk.Button(top, text="목록 비우기", command=self.clear).pack(side="left", padx=4)
        ttk.Label(top, text="URL:").pack(side="left", padx=(12, 2))
        self.url = ttk.Entry(top)
        self.url.pack(side="left", fill="x", expand=True)
        self.url.bind("<Return>", lambda e: self.add_url())
        ttk.Button(top, text="URL 추가", command=self.add_url).pack(side="left", padx=4)

        # AI 설정 (이미지 변환용, 선택)
        ai = ttk.LabelFrame(self, text="AI 설정 (이미지 변환용, 선택)", padding=6)
        ai.pack(fill="x", padx=8)
        ttk.Label(ai, text="공급자:").grid(row=0, column=0, sticky="w")
        self.provider = ttk.Combobox(ai, values=list(PROVIDERS), state="readonly", width=16)
        self.provider.set(NO_AI)
        self.provider.bind("<<ComboboxSelected>>", lambda e: self.on_provider())
        self.provider.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(ai, text="API 주소:").grid(row=0, column=2, sticky="w")
        self.base_url = ttk.Entry(ai)
        self.base_url.grid(row=0, column=3, columnspan=2, sticky="we", padx=4)
        ttk.Label(ai, text="API 키:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.key = ttk.Entry(ai, show="*", width=40)
        self.key.grid(row=1, column=1, columnspan=2, sticky="we", padx=4, pady=(4, 0))
        ttk.Label(ai, text="모델:").grid(row=1, column=3, sticky="e", pady=(4, 0))
        model_row = ttk.Frame(ai)
        model_row.grid(row=1, column=4, sticky="we", padx=4, pady=(4, 0))
        self.model = ttk.Combobox(model_row, width=34)  # 목록에서 고르거나 직접 입력
        self.model.pack(side="left", fill="x", expand=True)
        ttk.Button(model_row, text="모델 목록 불러오기", command=self.load_models).pack(side="left", padx=(4, 0))
        ai.columnconfigure(3, weight=1)
        ai.columnconfigure(4, weight=2)

        # 목록 + 미리보기
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=8)
        self.listbox = tk.Listbox(body, selectmode="extended", width=40)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.show_preview())
        body.add(self.listbox, weight=1)
        self.preview = tk.Text(body, wrap="word")
        body.add(self.preview, weight=3)

        # 하단 버튼
        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.pack(fill="x")
        self.convert_btn = ttk.Button(bottom, text="변환", command=self.convert)
        self.convert_btn.pack(side="left")
        ttk.Button(bottom, text="선택 항목 저장", command=self.save_selected).pack(side="left", padx=4)
        ttk.Button(bottom, text="모두 폴더에 저장", command=self.save_all).pack(side="left")
        self.status = ttk.Label(bottom, text="파일이나 URL을 추가하세요.")
        self.status.pack(side="left", padx=12)

    # ---- 입력 관리 ----
    def add_source(self, s):
        if s and s not in self.sources:
            self.sources.append(s)
            self.listbox.insert("end", s)

    def add_files(self):
        for p in filedialog.askopenfilenames(title="변환할 파일 선택"):
            self.add_source(p)

    def add_url(self):
        self.add_source(self.url.get().strip())
        self.url.delete(0, "end")

    def clear(self):
        self.sources.clear()
        self.results.clear()
        self.listbox.delete(0, "end")
        self.preview.delete("1.0", "end")

    # ---- AI 설정 ----
    def on_provider(self):
        url, key_env, model = PROVIDERS[self.provider.get()]
        for entry, value in ((self.base_url, url), (self.key, os.environ.get(key_env or "", "")),
                             (self.model, model)):
            entry.delete(0, "end")
            entry.insert(0, value)
        self.model["values"] = ()

    def ai_settings(self):
        """(주소, 키, 모델). AI를 안 쓰면 None, 설정이 모자라면 ValueError."""
        if self.provider.get() == NO_AI:
            return None
        url, key, model = self.base_url.get().strip(), self.key.get().strip(), self.model.get().strip()
        if not url:
            raise ValueError("API 주소를 입력하세요.")
        if not key and PROVIDERS[self.provider.get()][1] is not None:
            raise ValueError("API 키를 입력하세요.")
        return url, key, model

    def load_models(self):
        try:
            settings = self.ai_settings()
            if settings is None:
                return messagebox.showinfo("알림", "공급자를 먼저 선택하세요.")
            self.config(cursor="watch")
            self.update()
            # ponytail: 불러오는 동안 창이 잠깐 멈춤(최대 15초). 느린 공급자가 문제면 스레드로 옮기기
            ids = sorted(m.id for m in make_client(*settings[:2]).with_options(timeout=15).models.list())
        except Exception as e:
            return messagebox.showerror("모델 목록 오류", f"{type(e).__name__}: {e}")
        finally:
            self.config(cursor="")
        self.model["values"] = ids
        self.status.config(text=f"모델 {len(ids)}개를 불러왔습니다. 모델 칸에서 고르세요.")

    # ---- 변환 (UI가 멈추지 않도록 별도 스레드) ----
    def convert(self):
        if not self.sources:
            return messagebox.showinfo("알림", "변환할 파일이나 URL을 먼저 추가하세요.")
        try:
            settings = self.ai_settings()
            if settings and not settings[2]:
                raise ValueError("모델을 입력하거나 '모델 목록 불러오기'로 고르세요.")
            converter = make_converter(*settings) if settings else make_converter()
        except ValueError as e:
            return messagebox.showwarning("AI 설정", str(e))
        except ImportError:
            return messagebox.showerror("오류", "openai 패키지가 필요합니다: pip install openai")
        self.convert_btn.config(state="disabled")
        threading.Thread(target=self.worker, args=(converter, list(self.sources)), daemon=True).start()
        self.poll()

    def worker(self, converter, sources):
        for i, s in enumerate(sources):
            self.q.put(("status", f"변환 중 ({i + 1}/{len(sources)}): {os.path.basename(s) or s}"))
            try:
                self.q.put(("ok", s, converter.convert(s).markdown))
            except Exception as e:  # 한 파일 실패가 나머지를 막지 않도록
                self.q.put(("err", s, f"{type(e).__name__}: {e}"))
        self.q.put(("done",))

    def poll(self):
        while not self.q.empty():
            msg = self.q.get()
            if msg[0] == "status":
                self.status.config(text=msg[1])
            elif msg[0] in ("ok", "err"):
                _, s, text = msg
                self.results[s] = (msg[0] == "ok", text)
                i = self.sources.index(s)
                self.listbox.delete(i)
                self.listbox.insert(i, ("✔ " if msg[0] == "ok" else "✘ ") + s)
                self.listbox.itemconfig(i, fg="black" if msg[0] == "ok" else "red")
            elif msg[0] == "done":
                ok = sum(1 for good, _ in self.results.values() if good)
                self.status.config(text=f"완료: 성공 {ok} / 전체 {len(self.results)}")
                self.convert_btn.config(state="normal")
                self.listbox.selection_set(0)
                self.show_preview()
                return
        self.after(100, self.poll)

    # ---- 미리보기 / 저장 ----
    def selected(self):
        return [self.sources[i] for i in self.listbox.curselection()]

    def show_preview(self):
        sel = self.selected()
        self.preview.delete("1.0", "end")
        if sel and sel[0] in self.results:
            good, text = self.results[sel[0]]
            self.preview.insert("1.0", text if good else "변환 실패\n\n" + text)

    def save_selected(self):
        sel = [s for s in self.selected() if self.results.get(s, (False,))[0]]
        if not sel:
            return messagebox.showinfo("알림", "변환에 성공한 항목을 선택하세요.")
        if len(sel) == 1:
            s = sel[0]
            path = filedialog.asksaveasfilename(defaultextension=".md", initialfile=md_name(s),
                                                filetypes=[("Markdown", "*.md")])
            if path:
                # 미리보기에서 수정한 내용도 반영
                self.write(path, self.preview.get("1.0", "end-1c"))
        else:
            self.save_to_folder(sel)

    def save_all(self):
        self.save_to_folder([s for s in self.sources if self.results.get(s, (False,))[0]])

    def save_to_folder(self, sources):
        if not sources:
            return messagebox.showinfo("알림", "저장할 변환 결과가 없습니다.")
        folder = filedialog.askdirectory(title="저장할 폴더 선택")
        if not folder:
            return
        for s in sources:
            self.write(os.path.join(folder, md_name(s)), self.results[s][1])
        self.status.config(text=f"{len(sources)}개 파일을 저장했습니다: {folder}")

    def write(self, path, text):
        # ponytail: 같은 이름의 파일은 덮어씀. 원본 이름이 겹치는 경우가 잦으면 번호 붙이기 추가
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    App().mainloop()
