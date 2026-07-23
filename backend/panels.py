"""
backend/panels.py — the panel layer (UI, Composite pattern).

A rendered frame is a stack of panels, each a small object that knows how to
draw ONE region (the code card, the variables card, the loop-progress list, a
DataFrame grid, ...). Every panel exposes `draw(canvas) -> int`, returning the
y-coordinate just below what it drew so a composer can stack panels; panels that
fill to the bottom of the frame return that bottom edge.

Design rule enforced here: panels do not *compute*, they *draw*. The "which list
element is current" decision is made in loops.py and handed to LoopListPanel as
plain data; the DataFrame diff decision is the panel's only real branching and
is kept local to DataFramePanel. Layout/geometry is owned by the composers
(composer.py), which construct these panels with explicit coordinates.
"""
try:  # package import in dev (imported as backend.panels)
    from .pysyntax import iter_tokens
except ImportError:  # top-level module on the serverless runtime
    from pysyntax import iter_tokens

import pandas as pd

# Strikethrough / dropped-row grey, shared by DataFramePanel (matches the
# original draw_df literal).
_DROPPED_GREY = (96, 100, 120)


def draw_code_line(canvas, x, y, text, font, palette):
    """Draw one line of Python, each token span in its palette color.

    Shared by the plain-Python and pandas code panels and kept in sync with
    pysyntax.iter_tokens and the frontend editor (see CLAUDE.md).
    """
    cursor_x = x
    for span_text, category in iter_tokens(text):
        canvas.text((cursor_x, y), span_text, font=font,
                    fill=palette.get(category, palette["code"]))
        cursor_x += canvas.text_width(span_text, font=font)


def fmt_cell(value):
    """Format a DataFrame cell for display (trim trailing zeros on floats)."""
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    return str(value)


class Panel:
    """Base panel. Subclasses store their geometry/data and implement draw()."""

    def __init__(self, palette, fonts):
        self.palette = palette
        self.fonts = fonts

    def draw(self, canvas):
        raise NotImplementedError


# ==========================================================================
# Plain-Python panels
# ==========================================================================
class ExecutionOrderPanel(Panel):
    """The row of L<line> pills across the top (most recent 10, current lit)."""

    def __init__(self, palette, fonts, pad, steps, step_idx, is_final):
        super().__init__(palette, fonts)
        self.pad = pad
        self.steps = steps
        self.step_idx = step_idx
        self.is_final = is_final

    def draw(self, canvas):
        p = self.palette
        pad = self.pad
        canvas.text((pad, pad - 8), "execution order", font=self.fonts["title"], fill=p["title"])
        executed_labels = [str(self.steps[k].line) for k in range(self.step_idx + 1)
                           if self.steps[k].line is not None]
        visible_labels = executed_labels[-10:]
        has_overflow = len(executed_labels) > 10
        pill_x, pill_y = pad, pad + 44
        if has_overflow:
            canvas.text((pill_x, pill_y + 8), "...", font=self.fonts["var"], fill=p["muted"])
            pill_x += 56
        for j, label in enumerate(visible_labels):
            is_current_pill = (not self.is_final) and j == len(visible_labels) - 1
            pill_text = "L" + label
            pill_w = canvas.text_width(pill_text, font=self.fonts["pill"]) + 36
            canvas.rounded_rect([pill_x, pill_y, pill_x + pill_w, pill_y + 56], 12,
                                fill=p["pill_cur"] if is_current_pill else p["pill_bg"])
            canvas.text((pill_x + 18, pill_y + 10), pill_text, font=self.fonts["pill"],
                        fill=p["pill_cur_tx"] if is_current_pill else p["pill_tx"])
            pill_x += pill_w + 12
            if j < len(visible_labels) - 1:
                canvas.text((pill_x, pill_y + 8), ">", font=self.fonts["var"], fill=p["muted"])
                pill_x += 32
        return pill_y + 56


class PythonCodePanel(Panel):
    """The code card for the plain-Python view (larger type, current line lit)."""

    def __init__(self, palette, fonts, code_x, code_panel_w, panel_top, bottom,
                 src_lines, step, step_idx, total_steps, line_height):
        super().__init__(palette, fonts)
        self.code_x = code_x
        self.code_panel_w = code_panel_w
        self.panel_top = panel_top
        self.bottom = bottom
        self.src_lines = src_lines
        self.step = step
        self.step_idx = step_idx
        self.total_steps = total_steps
        self.line_height = line_height

    def draw(self, canvas):
        p = self.palette
        code_x, code_panel_w = self.code_x, self.code_panel_w
        panel_top, line_height = self.panel_top, self.line_height
        canvas.rounded_rect([code_x, panel_top, code_x + code_panel_w, self.bottom], 20, fill=p["panel"])
        phase_text = "finished" if self.step.is_final else "running line %s" % self.step.line
        canvas.text((code_x + 32, panel_top + 24),
                    "code   step %d/%d  %s" % (self.step_idx + 1, self.total_steps, phase_text),
                    font=self.fonts["title"], fill=p["title"])
        current_line = self.step.line
        total_lines = len(self.src_lines)
        if total_lines <= 20 or current_line is None:
            visible_start, visible_end = 0, total_lines
        else:
            visible_start = max(0, current_line - 1 - 9)
            visible_end = min(total_lines, visible_start + 20)
            visible_start = max(0, visible_end - 20)
        line_y = panel_top + 88
        for line_idx in range(visible_start, visible_end):
            is_current_line = (line_idx + 1) == current_line
            if is_current_line:
                canvas.rounded_rect([code_x + 16, line_y - 2, code_x + code_panel_w - 20, line_y + line_height - 5],
                                    10, fill=p["hl"])
                canvas.rect([code_x + 16, line_y - 2, code_x + 24, line_y + line_height - 5], fill=p["bar"])
            canvas.text((code_x + 32, line_y), "%3d" % (line_idx + 1), font=self.fonts["code"], fill=p["gutter"])
            canvas.text((code_x + 104, line_y), ">" if is_current_line else " ", font=self.fonts["code"], fill=p["bar"])
            line_text = self.src_lines[line_idx]
            max_chars = int((code_panel_w - 180) / canvas.text_width("m", font=self.fonts["code"]))
            if len(line_text) > max_chars:
                line_text = line_text[:max_chars - 1] + "…"
            draw_code_line(canvas, code_x + 140, line_y, line_text, self.fonts["code"], p)
            line_y += line_height
        return self.bottom


class VariablesPanel(Panel):
    """The variables card (name = value, changed values in green)."""

    def __init__(self, palette, fonts, right_x, right_w, panel_top, step, prev_step, max_var_rows):
        super().__init__(palette, fonts)
        self.right_x = right_x
        self.right_w = right_w
        self.panel_top = panel_top
        self.step = step
        self.prev_step = prev_step
        self.max_var_rows = max_var_rows

    def draw(self, canvas):
        p = self.palette
        right_x, right_w, panel_top = self.right_x, self.right_w, self.panel_top
        prev_vars = self.prev_step.variables if self.prev_step is not None else {}
        vars_panel_h = 80 + max(1, self.max_var_rows) * 56
        canvas.rounded_rect([right_x, panel_top, right_x + right_w, panel_top + vars_panel_h], 20, fill=p["panel"])
        canvas.text((right_x + 32, panel_top + 24), "variables  (changed in green)",
                    font=self.fonts["title"], fill=p["title"])
        var_y = panel_top + 80
        var_items = list(self.step.variables.items())[:10]
        if not var_items:
            canvas.text((right_x + 40, var_y), "(none yet)", font=self.fonts["var"], fill=p["muted"])
        for name, value in var_items:
            canvas.text((right_x + 40, var_y), name, font=self.fonts["var"], fill=p["name"])
            text_x = right_x + 40 + canvas.text_width(name + " ", font=self.fonts["var"])
            canvas.text((text_x, var_y), "= ", font=self.fonts["var"], fill=p["muted"])
            text_x += canvas.text_width("= ", font=self.fonts["var"])
            value_text = repr(value)
            available_w = (right_x + right_w - 36) - text_x
            while canvas.text_width(value_text, font=self.fonts["var"]) > available_w and len(value_text) > 4:
                value_text = value_text[:-4] + "..."
            is_changed = prev_vars.get(name, "\0__missing__") != value
            canvas.text((text_x, var_y), value_text, font=self.fonts["var"],
                        fill=p["changed"] if is_changed else p["val"])
            var_y += 56
        return panel_top + vars_panel_h


class LoopListPanel(Panel):
    """The list-progress card: done (struck through) / current / waiting (dashed).

    Receives the already-computed `current_idx` (from loops.current_index) and the
    visible items; it makes no decision beyond styling each row by position.
    """

    def __init__(self, palette, fonts, right_x, right_w, y_cursor, iterable_name, visible_items, current_idx):
        super().__init__(palette, fonts)
        self.right_x = right_x
        self.right_w = right_w
        self.y_cursor = y_cursor
        self.iterable_name = iterable_name
        self.visible_items = visible_items
        self.current_idx = current_idx

    def draw(self, canvas):
        p = self.palette
        right_x, right_w, y_cursor = self.right_x, self.right_w, self.y_cursor
        current_idx = self.current_idx
        loop_panel_h = 80 + max(1, len(self.visible_items)) * 76
        canvas.rounded_rect([right_x, y_cursor, right_x + right_w, y_cursor + loop_panel_h], 20, fill=p["panel"])
        canvas.text((right_x + 32, y_cursor + 24), "list %s  done/current/waiting" % self.iterable_name,
                    font=self.fonts["title"], fill=p["title"])
        list_top = y_cursor + 80
        for pos, item in enumerate(self.visible_items):
            item_y = list_top + pos * 76
            label = "[%d] %r" % (pos, item)
            if current_idx is not None and current_idx >= 0 and pos < current_idx:
                canvas.rounded_rect([right_x + 28, item_y, right_x + right_w - 28, item_y + 64], 16, fill=p["done_bg"])
                canvas.text((right_x + 52, item_y + 14), label, font=self.fonts["lst"], fill=p["done_tx"])
                label_w = canvas.text_width(label, font=self.fonts["lst"])
                canvas.line([right_x + 52, item_y + 34, right_x + 52 + label_w, item_y + 34], fill=p["done_tx"], width=3)
                canvas.text((right_x + right_w - 128, item_y + 18), "done", font=self.fonts["tag"], fill=p["done_tx"])
            elif current_idx is not None and pos == current_idx:
                canvas.rounded_rect([right_x + 28, item_y, right_x + right_w - 28, item_y + 64], 16,
                                    fill=p["cur_bg"], outline=p["cur_bd"], width=3)
                canvas.text((right_x + 52, item_y + 14), label, font=self.fonts["lst"], fill=p["cur_tx"])
                canvas.text((right_x + right_w - 208, item_y + 18), "<- current", font=self.fonts["tag"], fill=p["cur_tx"])
            else:
                for dash_x in range(right_x + 28, int(right_x + right_w - 28), 24):
                    canvas.line([dash_x, item_y, min(dash_x + 12, right_x + right_w - 28), item_y], fill=p["muted"], width=1)
                    canvas.line([dash_x, item_y + 64, min(dash_x + 12, right_x + right_w - 28), item_y + 64], fill=p["muted"], width=1)
                canvas.line([right_x + 28, item_y, right_x + 28, item_y + 64], fill=p["muted"], width=1)
                canvas.line([right_x + right_w - 28, item_y, right_x + right_w - 28, item_y + 64], fill=p["muted"], width=1)
                canvas.text((right_x + 52, item_y + 14), label, font=self.fonts["lst"], fill=p["wait_tx"])
                canvas.text((right_x + right_w - 148, item_y + 18), "waiting", font=self.fonts["tag"], fill=p["muted"])
        return y_cursor + loop_panel_h


class PythonConsolePanel(Panel):
    """The printed-output card for the plain-Python view (fills to the bottom)."""

    def __init__(self, palette, fonts, right_x, right_w, y_cursor, bottom, step):
        super().__init__(palette, fonts)
        self.right_x = right_x
        self.right_w = right_w
        self.y_cursor = y_cursor
        self.bottom = bottom
        self.step = step

    def draw(self, canvas):
        p = self.palette
        right_x, right_w, y_cursor, bottom = self.right_x, self.right_w, self.y_cursor, self.bottom
        canvas.rounded_rect([right_x, y_cursor, right_x + right_w, bottom], 20, fill=p["console"])
        canvas.text((right_x + 32, y_cursor + 24), "printed output", font=self.fonts["title"], fill=p["title"])
        output_y = y_cursor + 80
        max_output_lines = int((bottom - output_y) / 44)
        for output_line in self.step.stdout.splitlines()[-max_output_lines:]:
            canvas.text((right_x + 40, output_y), output_line[:48], font=self.fonts["out"], fill=p["out"])
            output_y += 44
        if self.step.error:
            canvas.text((right_x + 40, output_y), ("! " + self.step.error)[:48], font=self.fonts["out"], fill=p["err"])
        return bottom


# ==========================================================================
# Pandas panels
# ==========================================================================
class PandasCodePanel(Panel):
    """The code card for the pandas view (denser type, no execution pills)."""

    def __init__(self, palette, fonts, pad, code_panel_w, bottom, src_lines, step, step_idx, total_steps):
        super().__init__(palette, fonts)
        self.pad = pad
        self.code_panel_w = code_panel_w
        self.bottom = bottom
        self.src_lines = src_lines
        self.step = step
        self.step_idx = step_idx
        self.total_steps = total_steps

    def draw(self, canvas):
        p = self.palette
        pad, code_panel_w = self.pad, self.code_panel_w
        canvas.rounded_rect([pad, pad, pad + code_panel_w, self.bottom], 10, fill=p["panel"])
        canvas.text((pad + 14, pad + 12), "code  step %d/%d" % (self.step_idx + 1, self.total_steps),
                    font=self.fonts["title"], fill=p["title"])
        line_y = pad + 42
        current_line = self.step.line
        for line_idx, line_text in enumerate(self.src_lines):
            is_current_line = (line_idx + 1) == current_line
            if is_current_line:
                canvas.rounded_rect([pad + 8, line_y - 1, pad + code_panel_w - 10, line_y + 24], 5, fill=p["hl"])
                canvas.rect([pad + 8, line_y - 1, pad + 12, line_y + 24], fill=p["bar"])
            canvas.text((pad + 14, line_y), "%2d" % (line_idx + 1), font=self.fonts["code"], fill=p["gutter"])
            canvas.text((pad + 44, line_y), ">" if is_current_line else " ", font=self.fonts["code"], fill=p["bar"])
            max_text_w = code_panel_w - 96
            clipped_text = line_text
            while clipped_text and canvas.text_width(clipped_text, font=self.fonts["code"]) > max_text_w:
                clipped_text = clipped_text[:-1]
            if clipped_text != line_text and clipped_text:
                clipped_text = clipped_text[:-1] + "…"
            draw_code_line(canvas, pad + 64, line_y, clipped_text, self.fonts["code"], p)
            line_y += 28
        return self.bottom


class DataFramePanel(Panel):
    """A DataFrame drawn as a grid with diff highlighting (was draw_df).

    Highlights brand-new columns and cells whose value changed since the
    previous step; filter-detected rows are marked kept/dropped. Returns the
    y-coordinate below the grid so grids can stack.
    """

    def __init__(self, palette, fonts, x, y, panel_w, name, df, prev_df,
                 max_rows=7, max_cols=6, row_status=None):
        super().__init__(palette, fonts)
        self.x = x
        self.y = y
        self.panel_w = panel_w
        self.name = name
        self.df = df
        self.prev_df = prev_df
        self.max_rows = max_rows
        self.max_cols = max_cols
        self.row_status = row_status

    def draw(self, canvas):
        p = self.palette
        fonts = self.fonts
        df, prev_df = self.df, self.prev_df
        x, y, panel_w = self.x, self.y, self.panel_w
        max_rows, max_cols = self.max_rows, self.max_cols
        row_status = self.row_status
        columns = list(df.columns)[:max_cols]
        new_columns = set(columns) - set(prev_df.columns) if isinstance(prev_df, pd.DataFrame) else set()
        canvas.text((x, y), "%s   %d rows x %d cols" % (self.name, df.shape[0], df.shape[1]),
                    font=fonts["title"], fill=p["title"])
        y += 26
        # column widths
        index_col_w = max(24, max((len(str(row_label)) for row_label in list(df.index)[:max_rows]), default=1) * 9 + 10)
        col_widths = []
        for column in columns:
            cell_texts = [fmt_cell(cell_value) for cell_value in list(df[column])[:max_rows]]
            widest_chars = max([len(str(column))] + [len(text) for text in cell_texts])
            col_widths.append(min(160, widest_chars * 9 + 18))
        # clamp total width to the panel: drop rightmost columns that don't fit
        while col_widths and index_col_w + sum(col_widths) > panel_w - 8:
            col_widths.pop()
            columns = columns[:-1]
        row_h = 26
        # header
        header_x = x + index_col_w
        canvas.rect([x, y, x + index_col_w + sum(col_widths), y + row_h], fill=p["panel"])
        canvas.text((x + 6, y + 5), "idx", font=fonts["header"], fill=p["muted"])
        for col_idx, column in enumerate(columns):
            if column in new_columns:
                canvas.rect([header_x, y, header_x + col_widths[col_idx], y + row_h], fill=p["newbg"])
            canvas.text((header_x + 8, y + 5), str(column)[:16], font=fonts["header"],
                        fill=p["new"] if column in new_columns else p["head"])
            header_x += col_widths[col_idx]
        y += row_h
        # rows
        visible_rows = list(df.index)[:max_rows]
        for row_pos, row_label in enumerate(visible_rows):
            row_state = row_status.get(row_label) if row_status else None
            if row_state == "kept":
                canvas.rect([x, y, x + index_col_w + sum(col_widths), y + row_h], fill=p["newbg"])
            elif row_pos % 2:
                canvas.rect([x, y, x + index_col_w + sum(col_widths), y + row_h], fill=p["zebra"])
            canvas.text((x + 6, y + 5), str(row_label)[:4], font=fonts["cell"], fill=p["muted"])
            cell_x = x + index_col_w
            for col_idx, column in enumerate(columns):
                cell_value = df.at[row_label, column]
                is_changed = column in new_columns
                if (not is_changed and isinstance(prev_df, pd.DataFrame)
                        and column in prev_df.columns and row_label in prev_df.index):
                    try:
                        prev_cell = prev_df.at[row_label, column]
                        # nan != nan is always True in Python/pandas, so compare
                        # NaN-vs-NaN as unchanged rather than a false "changed".
                        is_changed = not (pd.isna(prev_cell) and pd.isna(cell_value)) and prev_cell != cell_value
                    except Exception:
                        is_changed = False
                if is_changed and row_state != "dropped":
                    canvas.rect([cell_x, y, cell_x + col_widths[col_idx], y + row_h], fill=p["newbg"])
                text_color = (
                    _DROPPED_GREY if row_state == "dropped"
                    else (p["new"] if (is_changed or row_state == "kept") else p["cell"]))
                cell_text = fmt_cell(cell_value)[:16]
                canvas.text((cell_x + 8, y + 5), cell_text, font=fonts["cell"], fill=text_color)
                if row_state == "dropped":
                    text_w = canvas.text_width(cell_text, font=fonts["cell"])
                    canvas.line([cell_x + 8, y + row_h // 2, cell_x + 8 + text_w, y + row_h // 2],
                                fill=_DROPPED_GREY, width=2)
                cell_x += col_widths[col_idx]
            y += row_h
        # overflow indicator
        if df.shape[0] > max_rows:
            canvas.text((x + 6, y + 4), "... %d more rows" % (df.shape[0] - max_rows),
                        font=fonts["cell"], fill=p["muted"])
            y += 22
        return y
