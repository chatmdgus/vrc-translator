"""실행 시 뜨는 설정 창. 시작을 누르면 설정을 저장하고 cfg를 돌려준다 (닫으면 None)."""
import copy
import os
import threading
import webbrowser

from . import events, settings
from .i18n import lang_name, set_lang, t

FORMALITY_CODES = ["default", "prefer_less", "prefer_more"]
SUB_SIZES = {"s": (16, 600), "m": (20, 720), "l": (26, 900)}   # 코드 → (글자 크기, 창 폭)
SPEED_CODES = [350, 500, 750]
LANG_CODES = ["ko", "ja", "en"]
MY_LANGS = [("한국어", "ko"), ("日本語", "ja")]     # 언어 이름은 그 언어로
DEFAULT_DEVICE_KEY = "__default__"
DEEPL_SIGNUP = "https://www.deepl.com/pro-api"


def _label(pairs, code):
    return next((l for l, c in pairs if c == code), pairs[0][0])


def _code(pairs, label):
    return next((c for l, c in pairs if l == label), pairs[0][1])


def _usage_msg(count, limit, available=None):
    msg = t("u.month", count=count, limit=limit)
    if available is not None:
        msg += t("u.avail", n=available)
    return msg


def check_translator(tcfg):
    """(성공 여부, 메시지, 사용량 정보)"""
    from .translate import GoogleFreeTranslator, has_deepl
    if tcfg.get("provider", "deepl") == "google":
        try:
            GoogleFreeTranslator().translate("안녕", "ko", "ja")
            g_ok, g_err = True, None
        except Exception as e:
            g_ok, g_err = False, e
        if has_deepl(tcfg):
            ok, msg, info = _check_translator(tcfg)       # 대신 쓸 DeepL 쪽 상태
            if g_ok:
                return True, t("e.google_ok") + (" · " + msg if ok else ""), info if ok else {}
            return ok, (t("e.google_fallback", e=g_err) + "\n" + msg) if ok else msg, info
        if g_ok:
            return True, t("e.google_ok"), {}
        return False, t("e.google_fail", e=g_err), {}
    ok, msg, info = _check_translator(tcfg)
    return ok, msg, info


def _check_translator(tcfg):
    if tcfg["engine"] == "relay":
        from .translate import RelayError, RelayTranslator
        try:
            u = RelayTranslator(tcfg["relay_url"], tcfg["relay_token"]).usage()
        except RelayError as e:
            return False, str(e), {}
        except Exception as e:
            return False, t("e.relay_conn", e=e), {}
        if "daily_limit" in u and u.get("today_used", 0) >= u["daily_limit"]:
            return False, t("e.daily"), u
        if u["available"] <= 0:
            return False, t("e.monthly_app"), u
        msg = _usage_msg(u["count"], u["limit"], u["available"])
        if "daily_limit" in u:
            msg += t("u.today", used=u["today_used"], limit=u["daily_limit"])
        return True, msg, u

    import deepl
    try:
        u = deepl.Translator(tcfg["deepl_api_key"]).get_usage()
    except deepl.AuthorizationException:
        return False, t("e.key"), {}
    except Exception as e:
        return False, t("e.deepl_conn", e=e), {}
    c = u.character
    if c and c.valid:
        info = {"count": c.count, "limit": c.limit}
        if c.limit_reached:
            return False, t("e.free_used", count=c.count, limit=c.limit), info
        return True, _usage_msg(c.count, c.limit), info
    return True, t("e.key_ok"), {}


def run_launcher(cfg, on_root=None):
    import tkinter as tk
    from tkinter import messagebox

    from .theme import FONT, P
    from .ui import BG, MUTED, PANEL, Card, Pill, PrimaryButton, Segmented, combo, entry, setup_style

    cfg = copy.deepcopy(cfg)
    result = {"cfg": None}
    ui_cfg = cfg.setdefault("ui", {})
    set_lang(cfg.get("my_lang", "ko"))

    root = tk.Tk()
    if on_root:
        on_root(root)
    root.title("VRC Translator")
    root.configure(bg=BG)
    root.resizable(False, False)
    setup_style(root)

    try:
        from .devices import list_devices
        mics, loops = list_devices()
    except Exception as e:
        mics, loops = [], []
        print(f"[launcher] audio devices: {e}")

    state = {"frame": None, "collect": None, "advanced": False}

    def rebuild(new_lang=None):
        """내 언어를 바꾸거나 고급 설정을 펼칠 때: 지금 고른 값을 저장해 두고 다시 그린다."""
        if state["collect"]:
            state["collect"]()
        if new_lang:
            old = cfg.get("my_lang", "ko")
            langs = cfg["listen"].setdefault("langs", [])
            if old != new_lang and old not in langs:     # 원래 내 언어는 이제 번역해서 들을 언어
                langs.append(old)
            cfg["my_lang"] = new_lang
            set_lang(new_lang)
        state["frame"].destroy()
        build()
        center()

    def center():
        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+"
                      f"{max(0, (root.winfo_screenheight() - h) // 2 - 30)}")

    # ------------------------------------------------ 처음 실행: 한국인/일본인 고르기
    def build_chooser():
        frame = tk.Frame(root, bg=BG)
        frame.pack(fill="both", expand=True)
        state["frame"] = frame
        tk.Frame(frame, bg=P["corona2"], height=3).pack(fill="x")
        box = tk.Frame(frame, bg=BG, padx=40, pady=30)
        box.pack()
        tk.Label(box, text="VRC Translator", bg=BG, fg=P["text"], font=(FONT, 18, "bold")).pack()
        tk.Label(box, text="어떤 언어로 말하세요?   /   どの言語で話しますか？", bg=BG, fg=P["dim"],
                 font=(FONT, 11)).pack(pady=(8, 22))
        row = tk.Frame(box, bg=BG)
        row.pack()

        def pick(code):
            cfg["my_lang"] = code
            set_lang(code)
            langs = [x for x in (cfg["listen"].get("langs") or []) if x != code]
            cfg["listen"]["langs"] = langs or ["ja" if code != "ja" else "ko"]
            cfg["mic"]["target_lang"] = "ja" if code != "ja" else "ko"
            ui_cfg["lang_chosen"] = True
            frame.destroy()
            build()
            center()

        for code, big, small in (("ko", "한국어로 말해요", "한국인"), ("ja", "日本語で話します", "日本人")):
            b = PrimaryButton(row, big, lambda c=code: pick(c))
            b.config(font=(FONT, 14, "bold"), padx=30, pady=18)
            b.pack(side="left", padx=10)
        tk.Label(box, text="나중에 설정에서 바꿀 수 있어요   /   あとで設定から変更できます", bg=BG,
                 fg=MUTED, font=(FONT, 9)).pack(pady=(22, 0))

    # ------------------------------------------------ 설정 화면
    def build():
        me = cfg.get("my_lang", "ko")
        dev_default = "(" + {"ko": "기본 장치", "ja": "既定のデバイス"}.get(me, "Default") + ")"
        frame = tk.Frame(root, bg=BG)
        frame.pack(fill="both", expand=True)
        state["frame"] = frame
        w = {}

        # 헤더
        tk.Frame(frame, bg=P["corona2"], height=3).pack(fill="x")
        head = tk.Frame(frame, bg=BG, padx=18, pady=14)
        head.pack(fill="x")
        left = tk.Frame(head, bg=BG)
        left.pack(side="left")
        tk.Label(left, text="VRC Translator", bg=BG, fg=P["text"],
                 font=(FONT, 15, "bold")).pack(anchor="w")
        tk.Label(left, text=t("l.subtitle"), bg=BG, fg=P["dim"], font=(FONT, 9)).pack(anchor="w")
        right = tk.Frame(head, bg=BG)
        right.pack(side="right")
        tk.Label(right, text=t("l.my_lang"), bg=BG, fg=P["mine"],
                 font=(FONT, 10, "bold")).pack(side="left", padx=(0, 10))
        Segmented(right, MY_LANGS, me, bg=BG,
                  command=lambda v: v != cfg.get("my_lang") and root.after(1, lambda: rebuild(v))
                  ).pack(side="left")
        tk.Frame(frame, bg=P["rim"], height=1).pack(fill="x", padx=18)

        grid = tk.Frame(frame, bg=BG, padx=12, pady=8)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=1, uniform="c")
        grid.columnconfigure(1, weight=1, uniform="c")
        col_l = tk.Frame(grid, bg=BG)
        col_l.grid(row=0, column=0, sticky="nsew", padx=6)
        col_r = tk.Frame(grid, bg=BG)
        col_r.grid(row=0, column=1, sticky="nsew", padx=6)

        # ---------- 번역
        locked = settings.TRANSLATOR_LOCKED
        tcfg = cfg["translator"]
        c_tr = Card(col_l, t("l.conn"))
        c_tr.pack(fill="x", pady=6)
        cur_p = tcfg.get("provider", "deepl")
        w["provider"], hp = c_tr.row(t("l.provider"), lambda p: Segmented(
            p, [(t("l.p.deepl"), "deepl"), (t("l.p.google"), "google")], cur_p,
            command=lambda v: (hp.config(text=t(f"l.p_h.{v}")), refresh_engine())),
            hint=t(f"l.p_h.{cur_p}"))
        conn = tk.Frame(c_tr.body, bg=PANEL)
        conn_slot = c_tr._row
        c_tr._row += 1
        if locked:
            tk.Label(conn, text=t("l.locked"), bg=PANEL, fg=P["mine"],
                     font=(FONT, 9, "bold")).grid(row=0, column=0, sticky="w", pady=(4, 0))
        else:
            engines = [(t("l.engine.relay"), "relay"), (t("l.engine.deepl"), "deepl")]
            tk.Label(conn, text=t("l.method"), bg=PANEL, fg=P["dim"], font=(FONT, 9)).grid(
                row=0, column=0, sticky="w", pady=4, padx=(0, 12))
            w["engine"] = Segmented(conn, engines,
                                    tcfg["engine"] if tcfg["engine"] in ("relay", "deepl") else "relay",
                                    command=lambda v: refresh_engine())
            w["engine"].grid(row=0, column=1, sticky="w", pady=4)
            w["url"] = tk.StringVar(value=tcfg.get("relay_url", ""))
            w["tok"] = tk.StringVar(value=tcfg.get("relay_token", ""))
            w["key"] = tk.StringVar(value=tcfg.get("deepl_api_key", ""))
            relay_f = tk.Frame(conn, bg=PANEL)
            for i, (lbl, var, show) in enumerate([(t("l.server"), w["url"], None),
                                                  (t("l.token"), w["tok"], "•")]):
                tk.Label(relay_f, text=lbl, bg=PANEL, fg=P["dim"], font=(FONT, 9)).grid(
                    row=i, column=0, sticky="w", pady=4, padx=(0, 12))
                entry(relay_f, var, show=show, width=28).grid(row=i, column=1, sticky="w", pady=4)
            deepl_f = tk.Frame(conn, bg=PANEL)
            tk.Label(deepl_f, text=t("l.apikey"), bg=PANEL, fg=P["dim"], font=(FONT, 9)).grid(
                row=0, column=0, sticky="w", pady=4, padx=(0, 12))
            entry(deepl_f, w["key"], show="•", width=28).grid(row=0, column=1, sticky="w", pady=4)
            link = tk.Label(deepl_f, text=t("l.getkey"), bg=PANEL, fg=P["corona"],
                            font=(FONT, 9, "underline"), cursor="hand2")
            link.grid(row=1, column=1, sticky="w")
            link.bind("<Button-1>", lambda e: webbrowser.open(DEEPL_SIGNUP))
            w["relay_f"], w["deepl_f"] = relay_f, deepl_f

        def refresh_engine():
            """DeepL을 고르면 연결 정보, Google이면 (필요 없으니) 숨김."""
            use_deepl = w["provider"].get() == "deepl"
            if use_deepl:
                conn.grid(row=conn_slot, column=0, columnspan=2, sticky="w")
            else:
                conn.grid_forget()
            if not locked:
                w["relay_f"].grid_forget()
                w["deepl_f"].grid_forget()
                f = w["relay_f"] if w["engine"].get() == "relay" else w["deepl_f"]
                f.grid(row=1, column=0, columnspan=2, sticky="w")
            if "formality" in w:
                on_chat_lang()

        # ---------- ① 내 말 → 챗박스
        c_mic = Card(col_l, t("l.mic_card"))
        c_mic.pack(fill="x", pady=6)
        mic_on = Pill(c_mic.head_right, t("l.use"), toggle=True, check=True,
                      value=bool(cfg["mic"]["enabled"]))
        mic_on.pack()
        mic_dev, _ = c_mic.row(t("l.mic"), lambda p: combo(p, [dev_default] + mics, width=28))
        mic_dev.set(cfg["mic"]["device"] or dev_default)
        others = [(lang_name(c), c) for c in LANG_CODES if c != me]
        want = cfg["mic"].get("target_lang", "ja")
        chat_lang, _ = c_mic.row(t("l.chat_lang"), lambda p: Segmented(
            p, others, want if want != me else others[0][1], command=lambda v: on_chat_lang()))
        speak, _ = c_mic.row(t("l.speak"), lambda p: Segmented(
            p, [(t("l.speak.both"), "both"), (t("l.speak.mine"), "mine")],
            cfg["mic"].get("speak", "both")))
        w["formality"], _ = c_mic.row(t("l.formality"), lambda p: Segmented(
            p, [(t("l.f.default"), "default"), (t("l.f.less"), "prefer_less"), (t("l.f.more"), "prefer_more")],
            cfg["translator"].get("formality", "default")))
        chat_orig = c_mic.full(lambda p: Pill(p, t("l.chat_orig", lang=lang_name(me)), toggle=True,
                                              check=True,
                                              value=bool(cfg["mic"].get("chatbox_show_original", True))))

        def on_chat_lang():
            # 말투 조절은 DeepL + 일본어일 때만
            w["formality"].set_enabled(chat_lang.get() == "ja" and w["provider"].get() == "deepl")

        # ---------- ② 주변 말 → 자막
        c_lis = Card(col_r, t("l.lis_card"))
        c_lis.pack(fill="x", pady=6)
        lis_on = Pill(c_lis.head_right, t("l.use"), toggle=True, check=True,
                      value=bool(cfg["listen"]["enabled"]))
        lis_on.pack()
        lis_dev, _ = c_lis.row(t("l.out"), lambda p: combo(p, [dev_default] + loops, width=28))
        lis_dev.set(cfg["listen"]["device"] or dev_default)
        cur_langs = set(cfg["listen"].get("langs") or []) - {me}
        if not cur_langs:
            cur_langs = {"ja" if me != "ja" else "ko"}
        lis_pills = {}

        def lang_row(p):
            f = tk.Frame(p, bg=PANEL)
            for code in LANG_CODES:
                if code == me:
                    continue
                pill = Pill(f, lang_name(code), toggle=True, check=True, value=code in cur_langs)
                pill.pack(side="left", padx=(0, 4))
                lis_pills[code] = pill
            return f

        c_lis.row(t("l.listen_langs"), lang_row, hint=t("l.listen_hint"))
        cur_fs = int(cfg["overlay"].get("font_size", 20))
        cur_size = min(SUB_SIZES, key=lambda k: abs(SUB_SIZES[k][0] - cur_fs))
        sub_size, _ = c_lis.row(t("l.size"), lambda p: Segmented(
            p, [(t(f"l.size.{k}"), k) for k in SUB_SIZES], cur_size))

        # 오른쪽 아래: 자주 안 바꾸는 건 접어 둔다
        adv_btn = Pill(col_r, t("l.adv_open") if not state["advanced"] else t("l.adv_close"),
                       lambda: toggle_adv(), bg=BG)
        adv_btn.pack(anchor="e", pady=(4, 0))

        def toggle_adv():
            state["advanced"] = not state["advanced"]
            root.after(1, rebuild)

        # ---------- 고급 설정 (펼쳤을 때만)
        adv = {}
        if state["advanced"]:
            c_adv = Card(frame, t("l.eng_card"))
            c_adv.pack(fill="x", padx=18, pady=(0, 6))
            b = c_adv.body
            b.columnconfigure(3, weight=1)

            def seg_row(r, col, label, options, value, key, hint_key=None):
                tk.Label(b, text=label, bg=PANEL, fg=P["dim"], font=(FONT, 9)).grid(
                    row=r, column=col, sticky="nw", pady=5, padx=(0 if col == 0 else 18, 10))
                seg = Segmented(b, options, value)
                seg.grid(row=r, column=col + 1, sticky="w", pady=4)
                adv[key] = seg

            cur_speed = min(SPEED_CODES, key=lambda x: abs(x - int(cfg["vad"].get("silence_ms", 500))))
            seg_row(0, 0, t("l.stt_mode"), [(t("l.sm.cloud"), "cloud"), (t("l.sm.local"), "local")],
                    cfg.get("stt", {}).get("mode", "cloud"), "stt_mode")
            seg_row(0, 2, t("l.speed"), [(t(f"l.speed.{v}"), v) for v in SPEED_CODES], cur_speed, "speed")
            seg_row(1, 0, t("l.model"), [(t(f"l.m.{k}"), k) for k in ("auto", "accurate", "light")],
                    cfg["whisper"].get("size", "auto"), "msize")
            seg_row(1, 2, t("l.device"), [(t("l.d.auto"), "auto"), ("GPU", "cuda"), ("CPU", "cpu")],
                    cfg["whisper"]["device"], "dev")
            seg_row(2, 0, t("l.quality"), [(t("l.q.hi"), True), (t("l.q.fast"), False)],
                    bool(cfg["translator"].get("quality", True)), "quality")
            seg_row(2, 2, t("l.ov"), [(t("l.ov.desktop"), "desktop"), (t("l.ov.vr"), "vr")],
                    cfg["overlay"]["mode"], "ov")
            adv["sub_orig"] = Pill(b, t("l.sub_orig"), toggle=True, check=True,
                                   value=bool(cfg["listen"]["show_original"]))
            adv["sub_orig"].grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
            tools = tk.Frame(b, bg=PANEL)
            tools.grid(row=3, column=2, columnspan=2, sticky="e", pady=(6, 0))
            Pill(tools, t("l.vocab"), lambda: open_vocab(status)).pack(side="left", padx=(0, 6))
            Pill(tools, t("l.advanced"), lambda: open_advanced()).pack(side="left")

        # ---------- 하단
        tk.Frame(frame, bg=P["rim"], height=1).pack(fill="x", padx=18, pady=(6, 0))
        foot = tk.Frame(frame, bg=BG, padx=18, pady=12)
        foot.pack(fill="x")
        info = tk.Frame(foot, bg=BG)
        info.pack(side="left")
        tk.Label(info, text=t("l.osc"), bg=BG, fg=P["mine"], font=(FONT, 9, "bold")).pack(anchor="w")
        tk.Label(info, text="Action Menu → Options → OSC → Enabled", bg=BG, fg=MUTED,
                 font=(FONT, 8)).pack(anchor="w")
        status = tk.Label(info, text="", bg=BG, fg=P["dim"], font=(FONT, 9))
        status.pack(anchor="w", pady=(4, 0))

        refresh_engine()
        on_chat_lang()

        def collect():
            tc = cfg["translator"]
            tc["provider"] = w["provider"].get()
            if not locked:
                tc["engine"] = w["engine"].get()
                tc["deepl_api_key"] = w["key"].get().strip()
                tc["relay_url"] = w["url"].get().strip()
                tc["relay_token"] = w["tok"].get().strip()
            cfg["my_lang"] = me
            cfg["mic"]["enabled"] = mic_on.get()
            cfg["mic"]["device"] = None if mic_dev.get() == dev_default else mic_dev.get()
            tc["formality"] = w["formality"].get()
            cfg["mic"]["source_lang"] = None          # 실제 인식 언어는 speak 설정으로 정함 (main.py)
            cfg["mic"]["speak"] = speak.get()
            cfg["mic"]["target_lang"] = chat_lang.get()
            cfg["mic"]["chatbox_show_original"] = chat_orig.get()
            cfg["listen"]["enabled"] = lis_on.get()
            cfg["listen"]["device"] = None if lis_dev.get() == dev_default else lis_dev.get()
            cfg["listen"]["source_lang"] = None
            cfg["listen"]["target_lang"] = me
            cfg["listen"]["langs"] = [c for c, p in lis_pills.items() if p.get()]
            cfg["listen"].pop("english", None)
            fs, width = SUB_SIZES[sub_size.get()]
            cfg["overlay"]["font_size"], cfg["overlay"]["desktop_width"] = fs, width
            if adv:
                cfg.setdefault("stt", {})["mode"] = adv["stt_mode"].get()
                cfg["vad"]["silence_ms"] = adv["speed"].get()
                cfg["whisper"]["size"] = adv["msize"].get()
                cfg["whisper"]["device"] = adv["dev"].get()
                tc["quality"] = adv["quality"].get()
                cfg["overlay"]["mode"] = adv["ov"].get()
                cfg["listen"]["show_original"] = adv["sub_orig"].get()
            ui_cfg["autostart"] = autostart.get()

        state["collect"] = collect

        def open_advanced():
            collect()
            path = settings.save(cfg)
            os.startfile(path)   # Windows 기본 편집기로 열기

        def start():
            collect()
            if not (cfg["mic"]["enabled"] or cfg["listen"]["enabled"]):
                messagebox.showwarning("VRC Translator", t("w.none_on"))
                return
            if cfg["listen"]["enabled"] and not cfg["listen"]["langs"]:
                messagebox.showwarning("VRC Translator", t("w.no_langs"))
                return
            tc = cfg["translator"]
            if tc.get("provider") == "deepl":
                if tc["engine"] == "relay" and not (tc["relay_url"] and tc["relay_token"]):
                    messagebox.showwarning("VRC Translator", t("w.no_relay"))
                    return
                if tc["engine"] == "deepl" and not tc["deepl_api_key"]:
                    messagebox.showwarning("VRC Translator", t("w.no_key"))
                    return
            start_btn.set_enabled(False, t("l.checking"))
            status.config(text=t("l.connecting"), fg=P["dim"])

            def worker():
                ok, msg, info_ = check_translator(cfg["translator"])
                root.after(0, lambda: done(ok, msg, info_))

            threading.Thread(target=worker, daemon=True).start()

        def done(ok, msg, info_):
            if not ok:
                status.config(text=t("l.conn_fail"), fg=P["blood"])
                start_btn.set_enabled(True, t("l.start"))
                messagebox.showerror("VRC Translator", msg)
                return
            print(t("log.usage", msg=msg))
            events.emit("usage", text=msg, **{k: v for k, v in info_.items() if k != "text"})
            ui_cfg["setup_done"] = True
            settings.save(cfg)
            result["cfg"] = cfg
            root.destroy()

        start_btn = PrimaryButton(foot, t("l.start"), start)
        start_btn.pack(side="right")
        autostart = Pill(foot, t("l.autostart"), toggle=True, check=True,
                         value=bool(ui_cfg.get("autostart", True)), bg=BG)
        autostart.pack(side="right", padx=12)
        root.bind("<Return>", lambda e: start_btn.enabled and start())

    def open_vocab(status_label):
        """단어장 창: 인식 힌트(이름·자주 쓰는 말)와 고쳐 쓰기 목록."""
        from .ui import FIELD
        vocab = cfg.setdefault("vocab", {"mic": [], "listen": [], "fix": []})
        win = tk.Toplevel(root)
        win.title(t("v.title"))
        win.configure(bg=BG)
        win.transient(root)
        win.grab_set()
        tk.Frame(win, bg=P["corona2"], height=3).pack(fill="x")
        body = tk.Frame(win, bg=BG, padx=18, pady=12)
        body.pack(fill="both", expand=True)
        tk.Label(body, text=t("v.title"), bg=BG, fg=P["text"], font=(FONT, 13, "bold")).pack(anchor="w")
        tk.Label(body, text=t("v.desc"), bg=BG, fg=P["dim"], font=(FONT, 9)).pack(anchor="w", pady=(2, 8))

        def area(title, example, lines):
            tk.Label(body, text=title, bg=BG, fg=P["mine"], font=(FONT, 10, "bold")).pack(
                anchor="w", pady=(8, 0))
            tk.Label(body, text=example, bg=BG, fg=MUTED, font=(FONT, 8)).pack(anchor="w")
            txt = tk.Text(body, height=5, width=56, bg=FIELD, fg=P["text"], insertbackground=P["text"],
                          relief="flat", font=(FONT, 10), highlightthickness=1,
                          highlightbackground=P["rim"], highlightcolor=P["corona"], wrap="none")
            txt.pack(fill="x", pady=(4, 0))
            txt.insert("1.0", "\n".join(lines))
            return txt

        t_mic = area(t("v.mic"), t("v.mic_ex"), vocab.get("mic") or [])
        t_lis = area(t("v.lis"), t("v.lis_ex"), vocab.get("listen") or [])
        t_fix = area(t("v.fix"), t("v.fix_ex"), [f"{a} → {b}" for a, b in (vocab.get("fix") or [])])

        def lines_of(txt):
            return [x.strip() for x in txt.get("1.0", "end").splitlines() if x.strip()]

        def save():
            fixes = []
            for line in lines_of(t_fix):
                for sep in ("→", "->", "=>", "="):
                    if sep in line:
                        a, b = line.split(sep, 1)
                        if a.strip():
                            fixes.append([a.strip(), b.strip()])
                        break
            vocab["mic"], vocab["listen"], vocab["fix"] = lines_of(t_mic), lines_of(t_lis), fixes
            status_label.config(text=t("v.saved"), fg=P["mine"])
            win.destroy()

        bar = tk.Frame(body, bg=BG)
        bar.pack(fill="x", pady=(14, 0))
        PrimaryButton(bar, t("v.save"), save).pack(side="right")
        Pill(bar, t("v.cancel"), win.destroy, bg=BG).pack(side="right", padx=10)
        win.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - win.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + 40
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

    if ui_cfg.get("lang_chosen") or ui_cfg.get("setup_done"):
        build()
    else:
        build_chooser()
    center()
    root.mainloop()
    state.clear()             # 순환 참조 끊기 (창 객체가 빨리 정리되도록)
    return result["cfg"]
