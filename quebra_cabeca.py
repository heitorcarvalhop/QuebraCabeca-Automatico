import json
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import wave
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
except ImportError:
    raise SystemExit("Pillow não instalado. Rode: pip install Pillow")


CANVAS_LARGURA = 920
CANVAS_ALTURA = 600
TABULEIRO_MAX = 430
TAMANHO_MINIMO_PECA = 16
PASSOS_ANIMACAO = 42
INTERVALO_MS = 16
LIMITE_ENCAIXE = 22

DIFICULDADES = {
    "Fácil": (3, 3),
    "Médio": (4, 5),
    "Difícil": (6, 8),
}
BASE_PONTOS = {"Fácil": 1000, "Médio": 2000, "Difícil": 4000}

ARQUIVO_RANKING = os.path.join(
    os.path.expanduser("~"), ".quebra_cabeca_ranking.json"
)


def ease_in_out_cubic(t):
    return 4 * t ** 3 if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2


def ease_out_back(t):
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


class GerenciadorSom:
    TAXA = 22050

    def __init__(self, habilitado=True):
        self.habilitado = habilitado
        self.arquivos = {}
        try:
            self._gerar_sons()
        except Exception:
            self.arquivos = {}

    def _escrever_wav(self, caminho, amostras):
        with wave.open(caminho, "w") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.TAXA)
            dados = b"".join(
                struct.pack("<h", int(max(-1, min(1, s)) * 32000)) for s in amostras
            )
            w.writeframes(dados)

    def _tom(self, freqs, dur, volume=0.6):
        n = int(self.TAXA * dur)
        saida = []
        for i in range(n):
            t = i / self.TAXA
            v = sum(math.sin(2 * math.pi * f * t) for f in freqs) / len(freqs)
            env = 1 - i / n
            saida.append(v * env * volume)
        return saida

    def _gerar_sons(self):
        pasta = tempfile.mkdtemp(prefix="qc_som_")
        encaixe = self._tom([880, 1320], 0.09)
        n = int(self.TAXA * 0.28)
        embaralhar = []
        for i in range(n):
            t = i / self.TAXA
            f = 650 - 350 * (i / n)
            embaralhar.append(math.sin(2 * math.pi * f * t) * (1 - i / n) * 0.5)
        finalizar = (
            self._tom([523], 0.12)
            + self._tom([659], 0.12)
            + self._tom([784], 0.12)
            + self._tom([1047], 0.28)
        )
        for nome, amostras in (
            ("encaixe", encaixe),
            ("embaralhar", embaralhar),
            ("finalizar", finalizar),
        ):
            caminho = os.path.join(pasta, nome + ".wav")
            self._escrever_wav(caminho, amostras)
            self.arquivos[nome] = caminho

    def tocar(self, nome):
        if not self.habilitado:
            return
        caminho = self.arquivos.get(nome)
        if not caminho:
            return
        try:
            if sys.platform.startswith("win"):
                import winsound

                winsound.PlaySound(
                    caminho, winsound.SND_FILENAME | winsound.SND_ASYNC
                )
            elif sys.platform == "darwin":
                subprocess.Popen(
                    ["afplay", caminho],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                for player in ("paplay", "aplay"):
                    try:
                        subprocess.Popen(
                            [player, caminho],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        return
                    except FileNotFoundError:
                        continue
        except Exception:
            pass


class Ranking:
    def __init__(self, caminho=ARQUIVO_RANKING):
        self.caminho = caminho
        self.dados = self._carregar()

    def _carregar(self):
        try:
            with open(self.caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _salvar(self):
        try:
            with open(self.caminho, "w", encoding="utf-8") as f:
                json.dump(self.dados, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def adicionar(self, dificuldade, nome, tempo, pontos):
        lista = self.dados.setdefault(dificuldade, [])
        lista.append({"nome": nome, "tempo": tempo, "pontos": pontos})
        lista.sort(key=lambda x: (x["tempo"], -x["pontos"]))
        self.dados[dificuldade] = lista[:10]
        self._salvar()

    def melhores(self, dificuldade):
        return self.dados.get(dificuldade, [])


class Config:
    def __init__(self):
        self.dificuldade = "Médio"
        self.som = True
        self.preview = True


def _pontos_borda(ax, ay, bx, by, ox, oy, s, amostras=18):
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    pts = [(ax, ay)]
    if s == 0:
        pts.append((bx, by))
        return pts
    neck, r = 0.10, 0.16
    cyf = math.sqrt(r * r - neck * neck)

    def P(af, pf):
        a = af * L
        p = pf * L * s
        return (ax + ux * a + ox * p, ay + uy * a + oy * p)

    pts.append(P(0.5 - neck, 0.0))
    angL = math.atan2(0 - cyf, -neck)
    angR = math.atan2(0 - cyf, neck)
    inicio = angL + 2 * math.pi if angL < angR else angL
    fim = angR
    for i in range(amostras + 1):
        t = i / amostras
        ang = inicio + (fim - inicio) * t
        pts.append(P(0.5 + r * math.cos(ang), cyf + r * math.sin(ang)))
    pts.append(P(0.5 + neck, 0.0))
    pts.append((bx, by))
    return pts


class Peca:
    def __init__(self, linha, coluna, imagem_pil, mascara, pw, ph, margem):
        self.linha = linha
        self.coluna = coluna
        self.imagem_pil = imagem_pil
        self.mascara = mascara
        self.pw = pw
        self.ph = ph
        self.margem = margem
        self.foto = None

        self.destino_x = 0
        self.destino_y = 0
        self.x = 0
        self.y = 0
        self.id_canvas = None
        self.encaixada = False


def gerar_pecas(imagem, linhas, colunas, seed):
    rng = random.Random(seed)
    if imagem.width < colunas * 20 or imagem.height < linhas * 20:
        raise ValueError("imagem_pequena")
    escala = min(TABULEIRO_MAX / imagem.width, TABULEIRO_MAX / imagem.height)
    base = imagem.resize(
        (max(1, int(imagem.width * escala)), max(1, int(imagem.height * escala))),
        Image.LANCZOS,
    )
    W = (base.width // colunas) * colunas
    H = (base.height // linhas) * linhas
    pw, ph = W // colunas, H // linhas

    if pw < TAMANHO_MINIMO_PECA or ph < TAMANHO_MINIMO_PECA:
        raise ValueError("imagem_pequena")

    img = base.resize((W, H), Image.LANCZOS)
    m = int(0.30 * max(pw, ph)) + 1
    padded = ImageOps.expand(img, border=m, fill=(0, 0, 0))

    direita = [[0] * colunas for _ in range(linhas)]
    baixo = [[0] * colunas for _ in range(linhas)]
    for r in range(linhas):
        for c in range(colunas):
            if c < colunas - 1:
                direita[r][c] = rng.choice([-1, 1])
            if r < linhas - 1:
                baixo[r][c] = rng.choice([-1, 1])

    pecas = []
    for r in range(linhas):
        for c in range(colunas):
            top_s = 0 if r == 0 else -baixo[r - 1][c]
            left_s = 0 if c == 0 else -direita[r][c - 1]
            right_s = direita[r][c]
            bot_s = baixo[r][c]

            TL = (m, m)
            TR = (m + pw, m)
            BR = (m + pw, m + ph)
            BL = (m, m + ph)
            poly = []
            poly += _pontos_borda(*TL, *TR, 0, -1, top_s)
            poly += _pontos_borda(*TR, *BR, 1, 0, right_s)
            poly += _pontos_borda(*BR, *BL, 0, 1, bot_s)
            poly += _pontos_borda(*BL, *TL, -1, 0, left_s)

            mask = Image.new("L", (pw + 2 * m, ph + 2 * m), 0)
            ImageDraw.Draw(mask).polygon(poly, fill=255)

            box = (c * pw, r * ph, c * pw + pw + 2 * m, r * ph + ph + 2 * m)
            recorte = padded.crop(box).convert("RGBA")
            recorte.putalpha(mask)

            pecas.append(Peca(r, c, recorte, mask, pw, ph, m))
    return pecas, pw, ph, m, W, H


class TelaInicial(ttk.Frame):
    def __init__(self, app):
        super().__init__(app.root, padding=40)
        self.app = app

        ttk.Label(
            self, text="🧩 Quebra-Cabeça Automático", font=("Helvetica", 22, "bold")
        ).pack(pady=(10, 4))
        ttk.Label(
            self, text="Monte a imagem manualmente ou deixe o programa montar!",
            font=("Helvetica", 11),
        ).pack(pady=(0, 24))

        estilo = {"width": 30}
        ttk.Button(self, text="▶  Novo jogo", command=app.novo_jogo, **estilo).pack(pady=6)
        ttk.Button(self, text="🖼  Carregar imagem", command=app.carregar_imagem, **estilo).pack(pady=6)
        ttk.Button(self, text="💾  Continuar (carregar progresso)", command=app.carregar_progresso, **estilo).pack(pady=6)
        ttk.Button(self, text="⚙  Configurações", command=app.abrir_configuracoes, **estilo).pack(pady=6)
        ttk.Button(self, text="🏆  Ranking", command=app.mostrar_ranking, **estilo).pack(pady=6)
        ttk.Button(self, text="✖  Sair", command=app.root.destroy, **estilo).pack(pady=6)

        ttk.Label(
            self, text="Dificuldade atual: " + app.config.dificuldade,
            font=("Helvetica", 10, "italic"),
        ).pack(pady=(24, 0))


class TelaJogo(ttk.Frame):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.root = app.root
        self.config = app.config
        self.som = app.som
        self.ranking = app.ranking

        self.imagem_pil = None
        self.caminho_imagem = None
        self.pecas = []
        self.linhas = 0
        self.colunas = 0
        self.seed = 0
        self.pw = self.ph = self.margem = 0
        self.offset_x = self.offset_y = 0
        self.W = self.H = 0

        self.movimentos = 0
        self.dicas = 0
        self.segundos = 0
        self.tempo_rodando = False
        self.resolvido = False
        self.auto_usado = False
        self.ocupado = False
        self.animacoes_pendentes = 0

        self.id_para_peca = {}
        self.arrastando = None
        self.arraste_moveu = False
        self._timer_job = None
        self._preview_foto = None

        self._construir()

    def _construir(self):
        topo = ttk.Frame(self, padding=6)
        topo.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(topo, text="☰ Menu", command=self._voltar_menu).pack(side=tk.LEFT, padx=2)
        ttk.Button(topo, text="🔀 Embaralhar", command=self.embaralhar).pack(side=tk.LEFT, padx=2)
        ttk.Button(topo, text="💡 Dica", command=self.dica).pack(side=tk.LEFT, padx=2)
        ttk.Button(topo, text="⚙ Montar tudo", command=self.montar_tudo).pack(side=tk.LEFT, padx=2)
        ttk.Button(topo, text="⏭ Passo a passo", command=self.montar_passo).pack(side=tk.LEFT, padx=2)
        ttk.Button(topo, text="💾 Salvar", command=self.salvar_progresso).pack(side=tk.LEFT, padx=2)

        info = ttk.Frame(self, padding=(8, 2))
        info.pack(side=tk.TOP, fill=tk.X)
        self.var_dif = tk.StringVar()
        self.var_tempo = tk.StringVar(value="Tempo: 00:00")
        self.var_mov = tk.StringVar(value="Movimentos: 0")
        self.var_dicas = tk.StringVar(value="Dicas: 0")
        for var in (self.var_dif, self.var_tempo, self.var_mov, self.var_dicas):
            ttk.Label(info, textvariable=var, font=("Helvetica", 10, "bold")).pack(
                side=tk.LEFT, padx=12
            )

        self.canvas = tk.Canvas(
            self, width=CANVAS_LARGURA, height=CANVAS_ALTURA,
            bg="#23232f", highlightthickness=0,
        )
        self.canvas.pack(side=tk.TOP, padx=8, pady=(0, 6))
        self.canvas.bind("<ButtonPress-1>", self._mouse_down)
        self.canvas.bind("<B1-Motion>", self._mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self._mouse_up)

        self.lbl_preview = tk.Label(self.canvas, bd=2, relief=tk.RIDGE, bg="#23232f")

        self.var_status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.var_status, relief=tk.SUNKEN, anchor=tk.W).pack(
            side=tk.BOTTOM, fill=tk.X
        )

    def iniciar(self, imagem_pil, caminho):
        self.imagem_pil = imagem_pil
        self.caminho_imagem = caminho
        self.linhas, self.colunas = DIFICULDADES[self.config.dificuldade]
        self.var_dif.set("Dificuldade: " + self.config.dificuldade)
        self.seed = random.randint(0, 10 ** 9)
        self.auto_usado = False
        self._criar_e_embaralhar()

    def _criar_e_embaralhar(self):
        try:
            (self.pecas, self.pw, self.ph, self.margem,
             self.W, self.H) = gerar_pecas(
                self.imagem_pil, self.linhas, self.colunas, self.seed
            )
        except ValueError:
            messagebox.showerror(
                "Imagem muito pequena",
                "A imagem é pequena demais para essa dificuldade.\n"
                "Use uma imagem maior ou um nível mais fácil.",
            )
            self._voltar_menu()
            return

        self.offset_x = (CANVAS_LARGURA - self.W) // 2
        self.offset_y = (CANVAS_ALTURA - self.H) // 2
        for p in self.pecas:
            p.destino_x = self.offset_x + p.coluna * self.pw - self.margem
            p.destino_y = self.offset_y + p.linha * self.ph - self.margem
            p.foto = ImageTk.PhotoImage(p.imagem_pil)
            p.encaixada = False

        self.movimentos = 0
        self.dicas = 0
        self.segundos = 0
        self.resolvido = False
        self.var_mov.set("Movimentos: 0")
        self.var_dicas.set("Dicas: 0")
        self._embaralhar_posicoes()
        self._desenhar()
        self._atualizar_preview()
        self._iniciar_timer()
        self.som.tocar("embaralhar")
        self.var_status.set("Arraste as peças ou use os botões automáticos.")

    def _embaralhar_posicoes(self):
        larg = self.pw + 2 * self.margem
        alt = self.ph + 2 * self.margem
        for p in self.pecas:
            p.x = random.randint(0, max(0, CANVAS_LARGURA - larg))
            p.y = random.randint(0, max(0, CANVAS_ALTURA - alt))

    def _desenhar(self):
        self.canvas.delete("all")
        self.id_para_peca.clear()
        self.canvas.create_rectangle(
            self.offset_x - 1, self.offset_y - 1,
            self.offset_x + self.W + 1, self.offset_y + self.H + 1,
            outline="#50506a", dash=(4, 4),
        )
        for p in self.pecas:
            p.id_canvas = self.canvas.create_image(
                p.x, p.y, image=p.foto, anchor=tk.NW
            )
            self.id_para_peca[p.id_canvas] = p
        self._reposicionar_preview()

    def _atualizar_preview(self):
        if not self.config.preview or self.imagem_pil is None:
            self.lbl_preview.place_forget()
            return
        mini = self.imagem_pil.copy()
        mini.thumbnail((130, 130))
        self._preview_foto = ImageTk.PhotoImage(mini)
        self.lbl_preview.config(image=self._preview_foto)
        self._reposicionar_preview()

    def _reposicionar_preview(self):
        if self.config.preview and self.imagem_pil is not None:
            self.lbl_preview.place(x=CANVAS_LARGURA - 150, y=10)
        else:
            self.lbl_preview.place_forget()

    def _iniciar_timer(self):
        self.tempo_rodando = True
        self._cancelar_timer()
        self._tick()

    def _tick(self):
        if not self.tempo_rodando:
            return
        m, s = divmod(self.segundos, 60)
        self.var_tempo.set(f"Tempo: {m:02d}:{s:02d}")
        self.segundos += 1
        self._timer_job = self.root.after(1000, self._tick)

    def _cancelar_timer(self):
        if self._timer_job is not None:
            try:
                self.root.after_cancel(self._timer_job)
            except Exception:
                pass
            self._timer_job = None

    def _add_movimento(self):
        self.movimentos += 1
        self.var_mov.set(f"Movimentos: {self.movimentos}")

    def _peca_no_ponto(self, x, y):
        ids = self.canvas.find_overlapping(x, y, x, y)
        for cid in reversed(ids):
            p = self.id_para_peca.get(cid)
            if not p:
                continue
            lx, ly = int(x - p.x), int(y - p.y)
            if 0 <= lx < p.mascara.width and 0 <= ly < p.mascara.height:
                if p.mascara.getpixel((lx, ly)) > 20:
                    return p
        return None

    def _mouse_down(self, ev):
        if self.ocupado or self.resolvido:
            return
        p = self._peca_no_ponto(ev.x, ev.y)
        if p:
            self.arrastando = p
            self.arraste_moveu = False
            self.canvas.tag_raise(p.id_canvas)

    def _mouse_move(self, ev):
        if self.arrastando is None:
            return
        p = self.arrastando
        meio_x = self.pw / 2 + self.margem
        meio_y = self.ph / 2 + self.margem
        p.x = ev.x - meio_x
        p.y = ev.y - meio_y
        self.arraste_moveu = True
        self.canvas.coords(p.id_canvas, p.x, p.y)

    def _mouse_up(self, ev):
        if self.arrastando is None:
            return
        p = self.arrastando
        self.arrastando = None
        if not self.arraste_moveu:
            return
        self._add_movimento()
        if (abs(p.x - p.destino_x) <= LIMITE_ENCAIXE
                and abs(p.y - p.destino_y) <= LIMITE_ENCAIXE):
            p.x, p.y = p.destino_x, p.destino_y
            self.canvas.coords(p.id_canvas, p.x, p.y)
            if not p.encaixada:
                p.encaixada = True
                self.som.tocar("encaixe")
            self._verificar_conclusao()

    def dica(self):
        if self.ocupado or self.resolvido:
            return
        candidatas = [p for p in self.pecas if not p.encaixada]
        if not candidatas:
            return
        p = random.choice(candidatas)
        self.dicas += 1
        self.var_dicas.set(f"Dicas: {self.dicas}")
        sx, sy = p.x, p.y
        self.ocupado = True
        self.canvas.tag_raise(p.id_canvas)

        def voltar():
            self._animar(p, p.destino_x, p.destino_y, sx, sy,
                         ease_in_out_cubic, self._fim_dica)

        self._animar(p, sx, sy, p.destino_x, p.destino_y,
                     ease_out_back, lambda: self.root.after(650, voltar))

    def _fim_dica(self):
        self.ocupado = False

    def _animar(self, peca, sx, sy, tx, ty, easing, ao_terminar=None, passo=0):
        t = passo / PASSOS_ANIMACAO
        e = easing(t)
        peca.x = sx + (tx - sx) * e
        peca.y = sy + (ty - sy) * e
        self.canvas.coords(peca.id_canvas, peca.x, peca.y)
        if passo < PASSOS_ANIMACAO:
            self.root.after(
                INTERVALO_MS,
                lambda: self._animar(peca, sx, sy, tx, ty, easing, ao_terminar, passo + 1),
            )
        else:
            peca.x, peca.y = tx, ty
            self.canvas.coords(peca.id_canvas, tx, ty)
            if ao_terminar:
                ao_terminar()

    def embaralhar(self):
        if self.ocupado:
            return
        self.auto_usado = False
        self.seed = random.randint(0, 10 ** 9)
        self._criar_e_embaralhar()

    def montar_tudo(self):
        if self.ocupado or self.resolvido:
            return
        pendentes = [p for p in self.pecas if not p.encaixada]
        if not pendentes:
            return
        self.auto_usado = True
        self.ocupado = True
        self.animacoes_pendentes = len(pendentes)
        for i, p in enumerate(pendentes):
            self.canvas.tag_raise(p.id_canvas)
            self.root.after(i * 70, lambda p=p: self._auto_uma(p))

    def _auto_uma(self, p):
        def terminou():
            if not p.encaixada:
                p.encaixada = True
                self.som.tocar("encaixe")
            self.animacoes_pendentes -= 1
            if self.animacoes_pendentes <= 0:
                self.ocupado = False
                self._verificar_conclusao()

        self._animar(p, p.x, p.y, p.destino_x, p.destino_y, ease_out_back, terminou)

    def montar_passo(self):
        if self.ocupado or self.resolvido:
            return
        pendentes = sorted(
            (p for p in self.pecas if not p.encaixada),
            key=lambda p: (p.linha, p.coluna),
        )
        if not pendentes:
            return
        self.auto_usado = True
        self.ocupado = True
        p = pendentes[0]
        self.canvas.tag_raise(p.id_canvas)

        def terminou():
            p.encaixada = True
            self.som.tocar("encaixe")
            self.ocupado = False
            self._verificar_conclusao()

        self._animar(p, p.x, p.y, p.destino_x, p.destino_y, ease_out_back, terminou)

    def _verificar_conclusao(self):
        if self.resolvido or any(not p.encaixada for p in self.pecas):
            return
        self.resolvido = True
        self.tempo_rodando = False
        self._cancelar_timer()
        self.som.tocar("finalizar")

        pontos = self._calcular_pontuacao()
        if self.auto_usado:
            messagebox.showinfo(
                "Concluído",
                "Quebra-cabeça montado com sucesso!\n\n"
                f"Tempo: {self._tempo_fmt()}\n"
                "Montagem automática — sem pontuação no ranking.",
            )
        else:
            nome = simpledialog.askstring(
                "Quebra-cabeça montado com sucesso!",
                f"Tempo: {self._tempo_fmt()}\n"
                f"Movimentos: {self.movimentos}\n"
                f"Pontuação: {pontos}\n\nDigite seu nome para o ranking:",
                parent=self.root,
            )
            if nome:
                self.ranking.adicionar(
                    self.config.dificuldade, nome.strip()[:20], self.segundos, pontos
                )
            messagebox.showinfo(
                "Concluído",
                f"Quebra-cabeça montado com sucesso!\n\nPontuação final: {pontos}",
            )
        self.var_status.set("Quebra-cabeça montado com sucesso!")

    def _calcular_pontuacao(self):
        base = BASE_PONTOS[self.config.dificuldade]
        minimo = self.linhas * self.colunas
        pen_tempo = self.segundos * 2
        pen_mov = max(0, self.movimentos - minimo) * 5
        pen_dica = self.dicas * 50
        return max(0, base - pen_tempo - pen_mov - pen_dica)

    def _tempo_fmt(self):
        m, s = divmod(self.segundos, 60)
        return f"{m:02d}:{s:02d}"

    def salvar_progresso(self):
        if not self.pecas:
            return
        caminho = filedialog.asksaveasfilename(
            title="Salvar progresso",
            defaultextension=".qcsave",
            filetypes=[("Quebra-cabeça", "*.qcsave"), ("Todos", "*.*")],
        )
        if not caminho:
            return
        dados = {
            "imagem": self.caminho_imagem,
            "dificuldade": self.config.dificuldade,
            "linhas": self.linhas,
            "colunas": self.colunas,
            "seed": self.seed,
            "segundos": self.segundos,
            "movimentos": self.movimentos,
            "dicas": self.dicas,
            "auto_usado": self.auto_usado,
            "pecas": [
                {"l": p.linha, "c": p.coluna, "x": p.x, "y": p.y, "ok": p.encaixada}
                for p in self.pecas
            ],
        }
        try:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("Salvo", "Progresso salvo com sucesso!")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível salvar.\n{e}")

    def restaurar(self, dados):
        caminho = dados.get("imagem")
        try:
            imagem = Image.open(caminho).convert("RGB")
        except Exception:
            messagebox.showerror(
                "Erro",
                "A imagem original do progresso não foi encontrada:\n" f"{caminho}",
            )
            self._voltar_menu()
            return

        self.imagem_pil = imagem
        self.caminho_imagem = caminho
        self.config.dificuldade = dados.get("dificuldade", self.config.dificuldade)
        self.linhas = dados["linhas"]
        self.colunas = dados["colunas"]
        self.seed = dados["seed"]
        self.var_dif.set("Dificuldade: " + self.config.dificuldade)

        try:
            (self.pecas, self.pw, self.ph, self.margem,
             self.W, self.H) = gerar_pecas(imagem, self.linhas, self.colunas, self.seed)
        except ValueError:
            messagebox.showerror("Erro", "Imagem incompatível com o progresso.")
            self._voltar_menu()
            return

        self.offset_x = (CANVAS_LARGURA - self.W) // 2
        self.offset_y = (CANVAS_ALTURA - self.H) // 2
        mapa = {(p.linha, p.coluna): p for p in self.pecas}
        for p in self.pecas:
            p.destino_x = self.offset_x + p.coluna * self.pw - self.margem
            p.destino_y = self.offset_y + p.linha * self.ph - self.margem
            p.foto = ImageTk.PhotoImage(p.imagem_pil)
        for info in dados["pecas"]:
            p = mapa.get((info["l"], info["c"]))
            if p:
                p.x, p.y = info["x"], info["y"]
                p.encaixada = info["ok"]

        self.segundos = dados.get("segundos", 0)
        self.movimentos = dados.get("movimentos", 0)
        self.dicas = dados.get("dicas", 0)
        self.auto_usado = dados.get("auto_usado", False)
        self.resolvido = False
        self.var_mov.set(f"Movimentos: {self.movimentos}")
        self.var_dicas.set(f"Dicas: {self.dicas}")
        self._desenhar()
        self._atualizar_preview()
        self._iniciar_timer()
        self.var_status.set("Progresso carregado. Continue de onde parou!")

    def _voltar_menu(self):
        self.tempo_rodando = False
        self._cancelar_timer()
        self.app.mostrar_inicio()


class Aplicativo:
    def __init__(self, root):
        self.root = root
        self.root.title("Quebra-Cabeça Automático — Edição Completa")
        self.root.resizable(False, False)

        self.config = Config()
        self.som = GerenciadorSom(self.config.som)
        self.ranking = Ranking()

        self.imagem_pil = None
        self.caminho_imagem = None

        self.tela_atual = None
        self.tela_jogo = None
        self.mostrar_inicio()

    def _trocar_tela(self, nova):
        if self.tela_atual is not None:
            self.tela_atual.pack_forget()
            self.tela_atual.destroy()
        self.tela_atual = nova
        nova.pack(fill=tk.BOTH, expand=True)

    def mostrar_inicio(self):
        self.som.habilitado = self.config.som
        self._trocar_tela(TelaInicial(self))

    def _selecionar_imagem(self):
        caminho = filedialog.askopenfilename(
            title="Escolha uma imagem",
            filetypes=[
                ("Imagens", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                ("Todos", "*.*"),
            ],
        )
        if not caminho:
            return False
        try:
            self.imagem_pil = Image.open(caminho).convert("RGB")
            self.caminho_imagem = caminho
            return True
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir a imagem.\n{e}")
            return False

    def carregar_imagem(self):
        if self._selecionar_imagem():
            self._iniciar_partida()

    def novo_jogo(self):
        if self.imagem_pil is None:
            messagebox.showinfo("Sem imagem", "Selecione uma imagem para começar.")
            if not self._selecionar_imagem():
                return
        self._iniciar_partida()

    def _iniciar_partida(self):
        self.som.habilitado = self.config.som
        tela = TelaJogo(self)
        self._trocar_tela(tela)
        self.tela_jogo = tela
        tela.iniciar(self.imagem_pil, self.caminho_imagem)

    def carregar_progresso(self):
        caminho = filedialog.askopenfilename(
            title="Carregar progresso",
            filetypes=[("Quebra-cabeça", "*.qcsave"), ("Todos", "*.*")],
        )
        if not caminho:
            return
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível ler o arquivo.\n{e}")
            return
        self.som.habilitado = self.config.som
        tela = TelaJogo(self)
        self._trocar_tela(tela)
        self.tela_jogo = tela
        tela.restaurar(dados)

    def abrir_configuracoes(self):
        win = tk.Toplevel(self.root)
        win.title("Configurações")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="Dificuldade:", font=("Helvetica", 10, "bold")).grid(
            row=0, column=0, sticky=tk.W, padx=12, pady=(12, 4)
        )
        var_dif = tk.StringVar(value=self.config.dificuldade)
        for i, nome in enumerate(DIFICULDADES):
            l, c = DIFICULDADES[nome]
            ttk.Radiobutton(
                win, text=f"{nome}  ({l}x{c} = {l*c} peças)",
                variable=var_dif, value=nome,
            ).grid(row=1 + i, column=0, sticky=tk.W, padx=24)

        var_som = tk.BooleanVar(value=self.config.som)
        ttk.Checkbutton(win, text="Sons e efeitos", variable=var_som).grid(
            row=5, column=0, sticky=tk.W, padx=12, pady=(12, 2)
        )
        var_prev = tk.BooleanVar(value=self.config.preview)
        ttk.Checkbutton(win, text="Mostrar pré-visualização", variable=var_prev).grid(
            row=6, column=0, sticky=tk.W, padx=12, pady=2
        )

        def salvar():
            self.config.dificuldade = var_dif.get()
            self.config.som = var_som.get()
            self.config.preview = var_prev.get()
            self.som.habilitado = self.config.som
            win.destroy()
            self.mostrar_inicio()

        ttk.Button(win, text="Salvar", command=salvar).grid(row=7, column=0, pady=12)

    def mostrar_ranking(self):
        win = tk.Toplevel(self.root)
        win.title("🏆 Ranking — melhores tempos")
        win.resizable(False, False)
        win.transient(self.root)

        nb = ttk.Notebook(win)
        nb.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
        for dif in DIFICULDADES:
            aba = ttk.Frame(nb, padding=8)
            nb.add(aba, text=dif)
            melhores = self.ranking.melhores(dif)
            if not melhores:
                ttk.Label(aba, text="Nenhum registro ainda.").pack(pady=20)
                continue
            ttk.Label(
                aba, text=f"{'#':<3}{'Nome':<16}{'Tempo':<8}{'Pontos':<8}",
                font=("Courier", 10, "bold"),
            ).pack(anchor=tk.W)
            for i, r in enumerate(melhores, 1):
                m, s = divmod(r["tempo"], 60)
                ttk.Label(
                    aba,
                    text=f"{i:<3}{r['nome']:<16}{m:02d}:{s:02d}   {r['pontos']:<8}",
                    font=("Courier", 10),
                ).pack(anchor=tk.W)


def main():
    root = tk.Tk()
    Aplicativo(root)
    root.mainloop()


if __name__ == "__main__":
    main()
