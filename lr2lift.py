"""
LR2LIFT - LR2スキンCSVのLIFT値を自動調整するツール
"""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import copy

LIFT_MARKER = "//LR2LIFT_VAL"

# セクションコメントとフラグのマッピング
# スキンのコメントにこれらのキーワードが含まれていれば対象セクションとみなす
LIFT_KEYWORDS = [
    "lane",        # lane 1p, lane 2p など
    "判定ライン",   # 判定ライン, [判定ライン]
    "レーザー",
    "小節線",
    "ノート関連",
    "ボム",
    "LN EFFECT",
    "ln effect",
]

LIFT_JUDGE_KEYWORDS = [
    "ジャッジ",    # ジャッジ表示
    "ghost type",  # ghost typeA
    "FAST",        # FAST,SLOW
]

# DST_の引数定義: #DST_XXX, index, time, x, y, w, h, ...
# → y は 0-based index 4 (Cassava 1-based の列5)
Y_COL = 4


def detect_encoding(path):
    with open(path, 'rb') as f:
        raw = f.read(1024)
    if b'\x00' in raw:
        return 'utf-16'
    try:
        raw.decode('shift_jis')
        return 'shift_jis'
    except Exception:
        return 'utf-8'


def read_csv(path):
    enc = detect_encoding(path)
    with open(path, 'rb') as f:
        content = f.read()
    text = content.decode(enc, errors='replace')
    # 改行コードを統一
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text.split('\n'), enc


def write_csv(path, lines, enc):
    text = '\r\n'.join(lines)
    with open(path, 'wb') as f:
        f.write(text.encode(enc, errors='replace'))


def parse_marker(lines):
    """先頭行にLR2LIFT_VALマーカーがあれば現在値を返す"""
    if lines and lines[0].startswith(LIFT_MARKER):
        try:
            parts = lines[0].split(',')
            lift_val = int(parts[1]) if len(parts) > 1 else 0
            judge_val = int(parts[2]) if len(parts) > 2 else 0
            return lift_val, judge_val, True
        except Exception:
            return 0, 0, True
    return 0, 0, False


def get_section_flag(comment_line):
    """コメント行からセクションのフラグ(L/LS/None)を返す"""
    lower = comment_line.lower()
    for kw in LIFT_JUDGE_KEYWORDS:
        if kw.lower() in lower:
            return 'LS'
    for kw in LIFT_KEYWORDS:
        if kw.lower() in lower:
            return 'L'
    return None


def scan_sections(lines, has_marker):
    """
    CSVを走査して、各DST_行に対するフラグ(L/LS/S/None)を返す。
    列25(0-based index 24)に既存フラグがある場合はそれを優先。
    なければセクションコメントから自動推定。
    返り値: dict {行インデックス: 'L'|'LS'|'S'}
    """
    result = {}
    current_flag = None
    after_blank = True   # ファイル先頭は「空行後」扱い
    start = 1 if has_marker else 0

    # 構造コメント: フラグ判定をスキップ (//SRC定義, //DST定義 など)
    STRUCTURAL_PREFIXES = ('//src定義', '//dst定義', '/////')

    for i in range(start, len(lines)):
        line = lines[i]
        stripped = line.strip()

        # 空行(またはカンマのみ行)は after_blank をセット
        if not stripped or stripped.replace(',', '') == '':
            after_blank = True
            continue

        parts = line.split(',')

        if stripped.startswith('//'):
            lower = stripped.lower()
            is_structural = any(lower.startswith(p) for p in STRUCTURAL_PREFIXES)

            if not is_structural:
                flag = get_section_flag(stripped)
                if flag is not None:
                    # キーワード一致: 空行後・途中どちらでも常にフラグを更新
                    current_flag = flag
                elif after_blank:
                    # キーワード無しのコメントが空行後に来た場合のみリセット
                    # (同一セクション内の複数コメントは after_blank=False のため維持)
                    current_flag = None
            after_blank = False
            continue

        after_blank = False

        # DST_行を処理
        if parts[0].startswith('#DST_'):
            # 列25(index 24)に明示フラグがあれば優先
            if len(parts) > 24 and parts[24].strip() in ('L', 'LS', 'S'):
                result[i] = parts[24].strip()
            elif current_flag:
                result[i] = current_flag
        elif parts[0].startswith('#') and not parts[0].startswith('#SRC_') \
                and not parts[0].startswith('#DST_'):
            # #IF, #ENDIF などの制御コマンド → フラグリセット
            current_flag = None

    return result


def apply_lift(lines, has_marker, lift_delta, judge_delta):
    """差分を使ってCSVのY座標を調整する"""
    section_flags = scan_sections(lines, has_marker)
    modified = 0
    start = 1 if has_marker else 0

    for i in range(start, len(lines)):
        flag = section_flags.get(i)
        if flag is None:
            continue

        parts = lines[i].split(',')
        if len(parts) <= Y_COL:
            continue

        try:
            y = int(parts[Y_COL])
        except ValueError:
            continue

        if flag == 'L':
            parts[Y_COL] = str(y - lift_delta)
        elif flag == 'LS':
            parts[Y_COL] = str(y - lift_delta - judge_delta)
        elif flag == 'S':
            parts[Y_COL] = str(y - judge_delta)
        else:
            continue

        lines[i] = ','.join(parts)
        modified += 1

    return lines, modified


class LR2LiftApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LR2LIFT")
        self.root.resizable(False, False)

        self.lines = []
        self.enc = 'shift_jis'
        self.has_marker = False
        self.stored_lift = 0
        self.stored_judge = 0
        self.csv_path = None

        self._build_ui()

    def _build_ui(self):
        pad = {'padx': 8, 'pady': 4}

        # ---- ファイル選択 ----
        file_frame = tk.LabelFrame(self.root, text="CSVファイル", **pad)
        file_frame.pack(fill='x', padx=10, pady=(10, 4))

        self.file_var = tk.StringVar()
        tk.Entry(file_frame, textvariable=self.file_var, width=52,
                 state='readonly').pack(side='left', padx=(4, 4))
        tk.Button(file_frame, text="開く...", width=8,
                  command=self.open_csv).pack(side='left')

        # ---- 値入力 ----
        val_frame = tk.LabelFrame(self.root, text="LIFT設定", **pad)
        val_frame.pack(fill='x', padx=10, pady=4)

        tk.Label(val_frame, text="LIFT値 (px):").grid(row=0, column=0, sticky='e', padx=4)
        self.lift_var = tk.StringVar(value="0")
        tk.Entry(val_frame, textvariable=self.lift_var, width=8).grid(row=0, column=1, sticky='w')

        tk.Label(val_frame, text="判定表示 追加値 (px):").grid(row=0, column=2, sticky='e', padx=(16, 4))
        self.judge_var = tk.StringVar(value="0")
        tk.Entry(val_frame, textvariable=self.judge_var, width=8).grid(row=0, column=3, sticky='w')

        self.stored_label = tk.Label(val_frame, text="現在の設定値: なし", fg='gray')
        self.stored_label.grid(row=1, column=0, columnspan=4, sticky='w', padx=4)

        # ---- プレビュー ----
        preview_frame = tk.LabelFrame(self.root, text="検出セクション", **pad)
        preview_frame.pack(fill='both', expand=True, padx=10, pady=4)

        cols = ('行', 'コマンド', 'フラグ', '現在Y', '調整後Y')
        self.tree = ttk.Treeview(preview_frame, columns=cols, show='headings', height=10)
        for c in cols:
            self.tree.heading(c, text=c)
            w = 60 if c in ('行', 'フラグ', '現在Y', '調整後Y') else 180
            self.tree.column(c, width=w, anchor='center' if c != 'コマンド' else 'w')
        self.tree.pack(side='left', fill='both', expand=True)

        sb = ttk.Scrollbar(preview_frame, command=self.tree.yview)
        sb.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=sb.set)

        # Lift/Judge変更時にプレビュー更新
        self.lift_var.trace_add('write', lambda *_: self._update_preview())
        self.judge_var.trace_add('write', lambda *_: self._update_preview())

        # ---- ボタン ----
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill='x', padx=10, pady=(4, 10))

        self.exec_btn = tk.Button(btn_frame, text="実行して保存", width=16,
                                  command=self.execute, state='disabled',
                                  bg='#4a9eff', fg='white', relief='raised')
        self.exec_btn.pack(side='right', padx=4)

        self.status_var = tk.StringVar(value="CSVファイルを開いてください")
        tk.Label(btn_frame, textvariable=self.status_var, anchor='w').pack(side='left')

    def open_csv(self):
        path = filedialog.askopenfilename(
            title="スキンCSVを選択",
            filetypes=[("CSVファイル", "*.csv"), ("すべてのファイル", "*.*")]
        )
        if not path:
            return
        try:
            lines, enc = read_csv(path)
            self.lines = lines
            self.enc = enc
            self.csv_path = path
            self.file_var.set(path)

            stored_lift, stored_judge, self.has_marker = parse_marker(lines)
            self.stored_lift = stored_lift
            self.stored_judge = stored_judge

            if self.has_marker:
                self.stored_label.config(
                    text=f"現在の設定値: LIFT={stored_lift}px  判定={stored_judge}px",
                    fg='blue'
                )
                self.lift_var.set(str(stored_lift))
                self.judge_var.set(str(stored_judge))
            else:
                self.stored_label.config(text="現在の設定値: なし (初回実行)", fg='gray')
                self.lift_var.set("0")
                self.judge_var.set("0")

            self._update_preview()
            self.exec_btn.config(state='normal')
            self.status_var.set(f"読み込み完了: {len(lines)} 行")

        except Exception as e:
            messagebox.showerror("読み込みエラー", str(e))

    def _parse_values(self):
        try:
            lift = int(self.lift_var.get())
            judge = int(self.judge_var.get())
            return lift, judge
        except ValueError:
            return None, None

    def _update_preview(self):
        self.tree.delete(*self.tree.get_children())
        if not self.lines:
            return

        lift, judge = self._parse_values()
        if lift is None:
            return

        lift_delta = lift - self.stored_lift
        judge_delta = judge - self.stored_judge

        flags = scan_sections(self.lines, self.has_marker)
        start = 1 if self.has_marker else 0

        for i in range(start, len(self.lines)):
            flag = flags.get(i)
            if flag is None:
                continue

            parts = self.lines[i].split(',')
            if len(parts) <= Y_COL:
                continue
            try:
                y = int(parts[Y_COL])
            except ValueError:
                continue

            if flag == 'L':
                new_y = y - lift_delta
            elif flag == 'LS':
                new_y = y - lift_delta - judge_delta
            elif flag == 'S':
                new_y = y - judge_delta
            else:
                continue

            cmd = parts[0]
            self.tree.insert('', 'end', values=(i + 1, cmd, flag, y, new_y))

    def execute(self):
        if not self.lines or not self.csv_path:
            messagebox.showerror("エラー", "CSVファイルを開いてください")
            return

        lift, judge = self._parse_values()
        if lift is None:
            messagebox.showerror("エラー", "LIFT値と判定値は整数で入力してください")
            return

        lift_delta = lift - self.stored_lift
        judge_delta = judge - self.stored_judge

        lines = copy.deepcopy(self.lines)

        # マーカー行の更新/追加
        marker_line = f"{LIFT_MARKER},{lift},{judge}"
        if self.has_marker:
            lines[0] = marker_line
        else:
            # 初回実行: バックアップがなければ作成
            bak_path = self.csv_path + '.bak'
            if not os.path.exists(bak_path):
                try:
                    import shutil
                    shutil.copy2(self.csv_path, bak_path)
                except Exception as e:
                    messagebox.showwarning("警告", f"バックアップの作成に失敗しました:\n{e}")
            lines.insert(0, marker_line)
            self.has_marker = True

        lines, modified = apply_lift(lines, True, lift_delta, judge_delta)

        try:
            write_csv(self.csv_path, lines, self.enc)
        except Exception as e:
            messagebox.showerror("保存エラー", str(e))
            return

        self.lines = lines
        self.stored_lift = lift
        self.stored_judge = judge

        self.stored_label.config(
            text=f"現在の設定値: LIFT={lift}px  判定={judge}px",
            fg='blue'
        )
        self._update_preview()

        self.status_var.set(f"保存完了: {modified} 行を調整しました")
        messagebox.showinfo("完了", f"{modified} 行のY座標を調整して保存しました。\n\nLIFT: {lift}px  判定追加: {judge}px")


def main():
    root = tk.Tk()
    app = LR2LiftApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
