"""
backend/render/composer.py — frame composition (UI, Strategy pattern).

A composer owns layout: it computes the frame geometry from all steps, then for
each step constructs the right panels (panels.py) at the right coordinates and
draws them onto a Canvas. `PythonComposer` and `PandasComposer` are two
interchangeable strategies behind `FrameComposer`; the visualizer (visualizer.py)
picks one per request and hands it the palette + font size.

Panels do the drawing; the composer does the arithmetic. The small per-frame
"which grid changed / how to order grids" decisions for pandas live here (they
are presentation ordering, not drawing) alongside the geometry.
"""
# Same-subpackage siblings — these resolve in both environments, so they stay OUT
# of the try below (a shared block would make a cross-package failure report one
# of *these* lines instead of the real one).
from .canvas import Canvas, load_font, MONO, MONO_B
from . import panels as _panels

try:  # dev: backend.render.composer, so `..` is backend
    from ..execution.loops import active_loop, current_index
    from ..execution.masks import apply_cmp
except ImportError:  # Vercel: render.composer, so `..` is beyond the top level
    from execution.loops import active_loop, current_index
    from execution.masks import apply_cmp

import numpy as np
import pandas as pd


def as_frame(obj):
    """A DataFrame as-is, a Series widened to one column, a 1D/2D ndarray as a grid, else None."""
    if isinstance(obj, pd.DataFrame):
        return obj
    if isinstance(obj, pd.Series):
        return obj.to_frame(name=(obj.name if obj.name is not None else "value"))
    if isinstance(obj, np.ndarray) and obj.ndim == 2:
        return pd.DataFrame(obj)
    if isinstance(obj, np.ndarray) and obj.ndim == 1:
        return pd.DataFrame({"value": obj})
    return None


class FrameComposer:
    """Base composer. Subclasses build fonts, compute layout, and compose frames."""

    def __init__(self, palette_colors, code_size):
        self.palette = palette_colors
        self.code_size = code_size
        self.fonts = self.load_fonts(code_size)

    def load_fonts(self, code_size):
        raise NotImplementedError

    def compute_layout(self, steps, src_lines):
        raise NotImplementedError

    def compose(self, step, step_idx, steps, src_lines, layout):
        raise NotImplementedError

    def _char_width(self):
        """Width of an 'm' in the code font (used for panel-width sizing)."""
        return Canvas(8, 8, (0, 0, 0)).text_width("m", font=self.fonts["code"])


# ==========================================================================
# Plain-Python
# ==========================================================================
class PythonComposer(FrameComposer):
    def __init__(self, palette_colors, code_size, loops):
        super().__init__(palette_colors, code_size)
        self.loops = loops

    def load_fonts(self, code_size):
        return dict(code=load_font(MONO, code_size), kw=load_font(MONO, code_size),
                    title=load_font(MONO_B, 28), pill=load_font(MONO_B, 30),
                    var=load_font(MONO, 32), tag=load_font(MONO, 26),
                    out=load_font(MONO, 30), lst=load_font(MONO, 32))

    def compute_layout(self, steps, src_lines):
        loops = self.loops
        code_size = self.code_size
        char_w = self._char_width()
        max_var_rows = max((len(list(step.variables.items())[:10]) for step in steps), default=1)
        longest_line_len = max((len(line) for line in src_lines), default=20)
        code_panel_w = min(140 + int(longest_line_len * char_w) + 60, 1240)
        code_panel_w = max(code_panel_w, 720)
        code_panel_h = 88 + min(len(src_lines), 20) * (code_size + 22) + 32

        vars_panel_h = 80 + max(1, max_var_rows) * 56
        if loops:
            max_items = min(max((len(step.variables.get(loops[0].iterable_name, []))
                                 if isinstance(step.variables.get(loops[0].iterable_name), (list, tuple)) else 0)
                                for step in steps) or len(loops), 8)
            for loop in loops:
                for step in steps:
                    value = step.variables.get(loop.iterable_name)
                    if isinstance(value, (list, tuple)):
                        max_items = max(max_items, min(len(value), 8))
            loop_panel_h = 80 + max(1, max_items) * 76 + 28
        else:
            loop_panel_h = 0
        right_column_h = vars_panel_h + 28 + loop_panel_h + 300
        panel_top = 48 + 148
        body_h = max(code_panel_h, right_column_h)
        width = code_panel_w + 44 + 880 + 48 * 2
        height = panel_top + body_h + 48
        line_height = code_size + 22
        return dict(width=width, height=height, code_panel_w=code_panel_w, pad=48,
                    panel_top=panel_top, line_height=line_height, max_var_rows=max_var_rows)

    def _loop_view(self, step):
        """Compute (iterable_name, visible_items, current_idx) for the loop panel.

        Mirrors the old render(): the active loop's list drives it, falling back
        to the first tracked loop's iterable so the panel still shows something
        before/after the loop runs. The index itself comes from execution/loops.py.
        """
        current_line = step.line
        current_loop = active_loop(current_line, self.loops)
        sequence = None
        has_list = False
        idx = None
        if current_loop:
            sequence = step.variables.get(current_loop.iterable_name)
            if isinstance(sequence, (list, tuple)):
                has_list = True
                idx = current_index(step, current_loop)
        if has_list:
            visible_items = sequence
        else:
            fallback = step.variables.get(self.loops[0].iterable_name)
            visible_items = fallback if isinstance(fallback, (list, tuple)) else []
        visible_items = list(visible_items)[:8]
        iterable_name = current_loop.iterable_name if current_loop else self.loops[0].iterable_name
        return iterable_name, visible_items, idx

    def compose(self, step, step_idx, steps, src_lines, layout):
        canvas = Canvas(layout["width"], layout["height"], self.palette["bg"])
        pad = layout["pad"]
        panel_top = layout["panel_top"]
        bottom = layout["height"] - pad
        code_x = pad
        code_panel_w = layout["code_panel_w"]
        right_x = code_x + code_panel_w + 44
        right_w = layout["width"] - right_x - pad

        _panels.ExecutionOrderPanel(self.palette, self.fonts, pad, steps, step_idx, step.is_final).draw(canvas)
        _panels.PythonCodePanel(self.palette, self.fonts, code_x, code_panel_w, panel_top, bottom,
                                src_lines, step, step_idx, len(steps), layout["line_height"]).draw(canvas)

        prev_step = steps[step_idx - 1] if step_idx > 0 else None
        vars_bottom = _panels.VariablesPanel(self.palette, self.fonts, right_x, right_w, panel_top,
                                             step, prev_step, layout["max_var_rows"]).draw(canvas)
        y_cursor = vars_bottom + 28

        if self.loops:
            iterable_name, visible_items, idx = self._loop_view(step)
            loop_bottom = _panels.LoopListPanel(self.palette, self.fonts, right_x, right_w, y_cursor,
                                                iterable_name, visible_items, idx).draw(canvas)
            y_cursor = loop_bottom + 28

        _panels.PythonConsolePanel(self.palette, self.fonts, right_x, right_w, y_cursor, bottom, step).draw(canvas)
        return canvas.image


# ==========================================================================
# Pandas
# ==========================================================================
def _grid_height(frame_df):
    """Reserved pixel height of a DataFrame grid (title + header + up to 6 rows)."""
    return 26 + 26 + min(frame_df.shape[0], 6) * 26 + (22 if frame_df.shape[0] > 6 else 0)


class PandasComposer(FrameComposer):
    def __init__(self, palette_colors, code_size, mask_filters=()):
        super().__init__(palette_colors, code_size)
        self.mask_filters = mask_filters

    def load_fonts(self, code_size):
        title_size = max(10, int(code_size * 0.82))
        header_size = max(10, int(code_size * 0.88))
        caption_size = max(9, int(code_size * 0.82))
        return dict(code=load_font(MONO, code_size), title=load_font(MONO_B, title_size),
                    header=load_font(MONO_B, header_size), cell=load_font(MONO, code_size),
                    caption=load_font(MONO, caption_size))

    def compute_layout(self, steps, src_lines):
        char_w = self._char_width()
        longest_line_len = max((len(line) for line in src_lines), default=20)
        code_panel_w = int(min(max(96 + longest_line_len * char_w + 20, 380), 640))
        right_w = 480
        width = 24 + code_panel_w + 22 + right_w + 24

        max_right_h = 0
        for step in steps:
            step_frames = sorted(
                [as_frame(value) for value in step.variables.values() if as_frame(value) is not None],
                key=lambda frame_df: -_grid_height(frame_df),
            )[:3]
            right_h = sum(_grid_height(frame_df) + 16 for frame_df in step_frames)
            if any(as_frame(value) is None for value in step.variables.values()):
                right_h += 28
            max_right_h = max(max_right_h, right_h)
        height = min(max(24 * 2 + 42 + len(src_lines) * 28, max_right_h + 96, 380), 960)
        return dict(width=width, height=height, code_panel_w=code_panel_w, pad=24)

    def _has_changed(self, prev_snapshot, name, value):
        prev_value = prev_snapshot.get(name)
        try:
            if isinstance(value, (pd.DataFrame, pd.Series)):
                return (prev_value is None) or (not value.equals(prev_value))
            if isinstance(value, np.ndarray):
                return (prev_value is None) or not np.array_equal(value, prev_value)
            return prev_value != value
        except Exception:
            return True

    def _order_grids(self, grids, prev_snapshot):
        """Detect a filter (subset of a bigger table) and order grids for display.

        Returns (ordered_grids, row_status): row_status marks the parent table's
        rows kept/dropped so DataFramePanel can strike out the dropped ones.

        Only pandas objects participate: a real DataFrame/Series filter (e.g.
        `df[df.x > 2]`) preserves the surviving rows' original index labels, so a
        proper-subset-of-labels check means something. `as_frame` gives a plain
        ndarray a synthetic RangeIndex(0..n-1) instead, which makes any smaller
        array's labels a trivial "subset" of any bigger array's — a slice like
        `A[3:5, 3:5]` would otherwise be misread as a row filter that dropped
        most of A.
        """
        row_status = {}
        filter_related = set()
        for name, frame_df, original in grids:
            if isinstance(original, np.ndarray):
                continue
            if not self._has_changed(prev_snapshot, name, original):
                continue
            for parent_name, parent_df, parent_original in grids:
                if parent_name == name or parent_df.shape[0] <= frame_df.shape[0]:
                    continue
                if isinstance(parent_original, np.ndarray):
                    continue
                try:
                    if set(frame_df.columns) <= set(parent_df.columns) and set(frame_df.index) < set(parent_df.index):
                        kept_labels = set(frame_df.index)
                        row_status[parent_name] = {
                            row_label: ("kept" if row_label in kept_labels else "dropped")
                            for row_label in parent_df.index
                        }
                        filter_related.update({name, parent_name})
                        break
                except TypeError:
                    continue

        def display_priority(grid_entry):
            name, frame_df, original = grid_entry
            if name in filter_related:
                return 0
            return 1 if self._has_changed(prev_snapshot, name, original) else 2

        ordered = sorted(grids, key=display_priority)
        return ordered, row_status

    def _compute_cell_masks(self, step):
        """Per-cell pass/fail for each detected array mask filter (execution/masks.py),
        re-verified against this step's live values.

        Unlike _order_grids' row_status (which trusts pandas index labels), this
        re-applies the statically-known comparison to the parent's real values and
        checks the result against the child's real values — so a name that got
        reassigned to something else after the filter line just fails the check
        and falls back to no highlighting, rather than showing a stale mask.

        Returns {parent_name: {(row_label, column): bool}}, keyed the same way
        DataFramePanel already reads df cells, so it needs no shape/ndim awareness.
        """
        cell_masks = {}
        for mf in self.mask_filters:
            parent = step.variables.get(mf.parent_name)
            child = step.variables.get(mf.child_name)
            if not (isinstance(parent, np.ndarray) and isinstance(child, np.ndarray)
                    and child.ndim == 1 and parent.ndim in (1, 2)):
                continue
            try:
                mask = apply_cmp(parent, mf.cmp, mf.thresh)
                if not np.array_equal(parent[mask], child):
                    continue
            except Exception:
                continue
            if parent.ndim == 1:
                cell_masks[mf.parent_name] = {(i, "value"): bool(mask[i]) for i in range(parent.shape[0])}
            else:
                cell_masks[mf.parent_name] = {
                    (i, j): bool(mask[i, j])
                    for i in range(parent.shape[0]) for j in range(parent.shape[1])
                }
        return cell_masks

    def compose(self, step, step_idx, steps, src_lines, layout):
        p = self.palette
        width, height, pad = layout["width"], layout["height"], layout["pad"]
        code_panel_w = layout["code_panel_w"]
        canvas = Canvas(width, height, p["bg"])

        _panels.PandasCodePanel(p, self.fonts, pad, code_panel_w, height - pad,
                                src_lines, step, step_idx, len(steps)).draw(canvas)

        right_x = pad + code_panel_w + 22
        right_w = width - right_x - pad
        y = pad
        prev_snapshot = steps[step_idx - 1].variables if step_idx > 0 else {}

        grids = []
        scalars = []
        for name, value in step.variables.items():
            frame_df = as_frame(value)
            if frame_df is not None:
                grids.append((name, frame_df, value))
            else:
                scalars.append((name, value))

        grids, row_status = self._order_grids(grids, prev_snapshot)
        cell_masks = self._compute_cell_masks(step)
        for name, frame_df, original in grids[:3]:
            prev_frame_df = as_frame(prev_snapshot.get(name))
            y = _panels.DataFramePanel(p, self.fonts, right_x, y, right_w, name, frame_df, prev_frame_df,
                                       max_rows=6, row_status=row_status.get(name),
                                       cell_mask=cell_masks.get(name)).draw(canvas) + 16

        if row_status:
            parent_name = next(iter(row_status))
            kept_count = sum(1 for state in row_status[parent_name].values() if state == "kept")
            canvas.text((right_x, min(y, height - pad - 24)),
                        "filter kept %d of %d rows" % (kept_count, len(row_status[parent_name])),
                        font=self.fonts["caption"], fill=p["cap"])
            y += 24
        if scalars:
            scalars_y = min(y, height - pad - 24)
            scalar_parts = ["%s=%s" % (name, _panels.fmt_cell(value)) for name, value in scalars[:6]]
            canvas.text((right_x, scalars_y), "scalars:  " + "   ".join(scalar_parts),
                        font=self.fonts["caption"], fill=p["cap"])
        if step.error:
            error_y = min(y + 8, height - pad - 24)
            canvas.text((right_x, error_y), ("! " + step.error)[:60], font=self.fonts["caption"], fill=(243, 139, 168))
        return canvas.image
