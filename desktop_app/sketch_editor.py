"""Small sketch workspace: geometry, relations, ordering and palm preview."""

import json
import math
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np

from .editor import clip_segment
from .model import encode_strokes
from .sketch import Bezier, Sketch, cross


RELATIONS = {'horizontal': '水平', 'vertical': '竖直', 'parallel': '平行',
             'perpendicular': '垂直', 'tangent': '相切', 'length': '长度', 'radius': '半径',
             'diameter': '直径', 'width': '宽度', 'height': '高度'}


class SketchEditor(ttk.Frame):
    def __init__(self, parent, on_preview, on_apply, on_save, on_load, on_change=None):
        super().__init__(parent)
        self.on_change = on_change or (lambda: None)
        self.sketch = Sketch()
        self.raw_fields = None
        self.blank_us = 2000
        self.compiled_key = self.compiled_value = None
        self.cx = self.cy = 0.0
        self.view_span = 80.0
        self.selected = []
        self.undo_stack, self.redo_stack = [], []
        self.anchor = self.hover_point = None
        self.chain = None
        self.curve_points = []
        self.drag = None
        self.debug = False
        self.tool = tk.StringVar(value='select')
        self.message = tk.StringVar(value='选择工具开始绘制；Shift 可多选线条。')
        self.hint = tk.StringVar()
        self.selection_text = tk.StringVar(value='尚未选择线条')
        self.dimension_hint = tk.StringVar(value='先选中要修改的线条或轮廓。')
        self.dimension_selection = None
        self.size_value = tk.StringVar()
        self.size_kind = tk.StringVar(value='长度')
        self.scale_value = tk.StringVar(value='1.0')
        self.order_auto = tk.BooleanVar(value=True)
        self.tool_buttons = {}
        tools = ttk.Frame(self, padding=(8, 6))
        tools.pack(fill='x')
        for key, label in [('select', '选择'), ('polyline', '连续直线'), ('bezier', '控制点曲线'),
                           ('rectangle', '矩形'), ('circle', '圆'), ('move', '移动'), ('erase', '擦除')]:
            button = ttk.Radiobutton(tools, text=label, value=key, variable=self.tool, command=self.choose_tool)
            button.pack(side='left', padx=2)
            self.tool_buttons[key] = button
        bar = ttk.Frame(self, padding=(8, 2))
        bar.pack(fill='x')
        for label, command in [('撤销', self.undo), ('重做', self.redo), ('清空', self.clear),
                               ('完成绘制', self.finish), ('适合窗口', self.fit)]:
            ttk.Button(bar, text=label, command=command, width=9).pack(side='left', padx=2)
        ttk.Button(bar, text='导入图形', command=on_load).pack(side='right', padx=2)
        ttk.Button(bar, text='保存图形', command=on_save).pack(side='right', padx=2)
        ttk.Label(self, textvariable=self.hint, foreground='#526680', padding=(10, 5)).pack(fill='x')
        actions = ttk.Frame(self, padding=8)
        actions.pack(side='bottom', fill='x')
        self.apply_button = ttk.Button(actions, text='发送到设备', command=on_apply)
        self.apply_button.pack(side='left', padx=2)
        ttk.Button(actions, text='手掌预览', command=on_preview).pack(side='left', padx=8)
        self.message_label = ttk.Label(self, textvariable=self.message, foreground='#526680', padding=(10, 6), wraplength=680)
        self.message_label.pack(side='bottom', fill='x')
        body = ttk.Frame(self)
        body.pack(fill='both', expand=True)
        side = ttk.Frame(body, width=224, padding=(6, 0, 8, 0))
        side.pack(side='right', fill='y')
        side.pack_propagate(False)
        self.canvas = tk.Canvas(body, bg='#f8fbff', highlightthickness=0, height=390, takefocus=True)
        self.canvas.pack(fill='both', expand=True, padx=(8, 0))
        self.canvas.bind('<Configure>', lambda _: self.draw())
        self.canvas.bind('<Button-1>', self.press)
        self.canvas.bind('<Double-Button-1>', self.select_contour)
        self.canvas.bind('<B1-Motion>', self.motion)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<Motion>', self.hover)
        self.canvas.bind('<Button-3>', lambda _: self.finish())
        self.canvas.bind('<Escape>', lambda _: self.finish())
        self.canvas.bind('<Delete>', lambda _: self.delete_selected())
        self.canvas.bind('<Control-z>', lambda _: self.undo())
        self.canvas.bind('<Control-y>', lambda _: self.redo())
        self.canvas.bind('<MouseWheel>', lambda e: self.zoom(.8 if e.delta > 0 else 1.25))
        tabs = ttk.Notebook(side)
        tabs.pack(fill='both', expand=True)
        def scroll_page(label):
            tab = ttk.Frame(tabs)
            tabs.add(tab, text=label)
            scroll = ttk.Scrollbar(tab, orient='vertical')
            scroll.pack(side='right', fill='y')
            view = tk.Canvas(tab, width=170, highlightthickness=0, bg='#edf2f7', yscrollcommand=scroll.set)
            view.pack(fill='both', expand=True)
            scroll.configure(command=view.yview)
            frame = ttk.Frame(view, padding=6)
            window = view.create_window(0, 0, window=frame, anchor='nw')
            frame.bind('<Configure>', lambda _: view.configure(scrollregion=view.bbox('all')))
            view.bind('<Configure>', lambda e: view.itemconfigure(window, width=e.width))
            view.bind('<MouseWheel>', lambda e: view.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
            return frame
        properties, relationships, ordering = scroll_page('尺寸'), scroll_page('关系'), scroll_page('顺序')
        ttk.Label(properties, textvariable=self.selection_text, wraplength=168).pack(fill='x', pady=5)
        ttk.Label(relationships, textvariable=self.selection_text, wraplength=168).pack(fill='x', pady=5)
        relation_buttons = ttk.Frame(relationships)
        relation_buttons.pack(fill='x')
        for i, kind in enumerate(('horizontal', 'vertical', 'parallel', 'perpendicular', 'tangent')):
            ttk.Button(relation_buttons, text=RELATIONS[kind], width=7,
                       command=lambda k=kind: self.add_relation(k)).grid(row=i//2, column=i % 2, padx=2, pady=2)
        ttk.Label(properties, text='设置实际尺寸 / mm').pack(anchor='w', pady=(10, 3))
        row = ttk.Frame(properties)
        row.pack(fill='x')
        self.dimension_picker = ttk.Combobox(row, textvariable=self.size_kind, values=[], state='readonly', width=5)
        self.dimension_picker.pack(side='left')
        self.dimension_picker.bind('<<ComboboxSelected>>', lambda _: self.show_dimension())
        ttk.Entry(row, textvariable=self.size_value, width=9).pack(side='right')
        self.dimension_button = ttk.Button(properties, text='应用尺寸', command=self.dimension)
        self.dimension_button.pack(fill='x', pady=4)
        ttk.Label(properties, textvariable=self.dimension_hint, foreground='#526680', wraplength=170).pack(fill='x')
        ttk.Label(properties, text='整体缩放（改变实际大小）', wraplength=170).pack(anchor='w', pady=(8, 3))
        row = ttk.Frame(properties)
        row.pack(fill='x')
        ttk.Entry(row, textvariable=self.scale_value, width=8).pack(side='left')
        ttk.Button(row, text='缩放', width=7, command=self.scale_all).pack(side='right')
        ttk.Label(properties, text='例如 2 倍：20 mm → 40 mm；已设尺寸一起更新。', foreground='#526680', wraplength=170).pack(fill='x', pady=4)
        ttk.Label(relationships, text='所选线条的关系与尺寸').pack(anchor='w', pady=(10, 3))
        self.relation_list = tk.Listbox(relationships, height=5, exportselection=False)
        self.relation_list.pack(fill='both', expand=True)
        ttk.Button(relationships, text='移除选中的关系', command=self.remove_relation).pack(fill='x', pady=4)
        ttk.Button(relationships, text='删除所选线条', command=self.delete_selected).pack(fill='x')
        ttk.Checkbutton(ordering, text='自动安排（跳过排序）', variable=self.order_auto, command=self.auto_order).pack(anchor='w')
        ttk.Label(ordering, text='选中轮廓后上下移动。\n公共线仅在首次经过时呈现。', wraplength=168).pack(fill='x', pady=8)
        self.order_list = tk.Listbox(ordering, height=9, exportselection=False)
        self.order_list.pack(fill='both', expand=True)
        self.order_list.bind('<<ListboxSelect>>', self.select_group)
        row = ttk.Frame(ordering)
        row.pack(fill='x', pady=5)
        ttk.Button(row, text='上移', width=7, command=lambda: self.reorder(-1)).pack(side='left')
        ttk.Button(row, text='下移', width=7, command=lambda: self.reorder(1)).pack(side='right')
        ttk.Label(ordering, text='尽量避免闭合轮廓共边；删除部分线条后，剩余线条仍可呈现。', wraplength=168).pack(fill='x', pady=5)
        self.choose_tool()

    def snapshot(self):
        return self.sketch.document(), self.raw_fields

    def signature(self):
        return json.dumps(self.snapshot(), sort_keys=True, separators=(',', ':'))

    def config_fields(self):
        if self.raw_fields is not None:
            return dict(self.raw_fields)
        key = json.dumps(self.sketch.document(), sort_keys=True)
        if key == self.compiled_key:
            return dict(self.compiled_value)
        paths, _ = self.sketch.compile()
        value = dict(path_xy_um='NONE', path_closed=1, scan_paths=encode_strokes(paths))
        self.compiled_key, self.compiled_value = key, value
        return dict(value)

    def set_config(self, config, document=None):
        from .model import encode_points
        if document is not None:
            sketch = Sketch.from_document(document)
            paths, _ = sketch.compile()
            if config.scan_paths != encode_strokes(paths):
                raise ValueError('草图与保存的扫描路径不一致，文件未载入。')
        else:
            sketch = Sketch()
            paths = config.strokes_um() if config.scan_paths != 'NONE' else ([config.points_um()] if config.points_um() else [])
            for path in paths:
                points = [tuple(v/1000 for v in p) for p in path]
                if len(points) > 1:
                    closed = config.path_closed if config.scan_paths == 'NONE' else points[0] == points[-1]
                    sketch.add_polyline(points, closed, join=False)
                elif points:
                    sketch.add_point(points[0])
        self.sketch = sketch
        self.blank_us = config.blank_us
        self.raw_fields = dict(path_xy_um=encode_points(config.points_um()), path_closed=config.path_closed, scan_paths=config.scan_paths)
        self.selected = []
        self.undo_stack, self.redo_stack = [], []
        self.anchor = self.chain = self.drag = None
        self.tool.set('select')
        self.fit()
        self.refresh()

    def stored_document(self):
        # A device-only or old-format import keeps its exact original path until edited.
        fields = self.config_fields()
        if fields['scan_paths'] != 'NONE' and encode_strokes(self.sketch.compile()[0]) == fields['scan_paths']:
            return self.sketch.document()
        return None

    def set_debug(self, enabled):
        self.debug = enabled
        self.draw()

    def set_origin(self, cx, cy):
        self.cx, self.cy = cx, cy
        self.draw()

    def transaction(self, action, solve=False):
        old = self.snapshot()
        candidate = self.sketch.copy()
        try:
            action(candidate)
            candidate.validate()
            if solve:
                candidate.solve()
            candidate.check_constraints()
        except (ValueError, StopIteration, OverflowError, FloatingPointError) as error:
            self.message.set(str(error))
            return False
        self.undo_stack.append(old)
        self.undo_stack = self.undo_stack[-80:]
        self.redo_stack.clear()
        self.sketch, self.raw_fields = candidate, None
        self.refresh()
        self.on_change()
        return True

    def restore(self, value):
        self.sketch = Sketch.from_document(value[0])
        self.raw_fields = value[1]
        self.selected = []
        self.anchor = self.chain = self.drag = None
        self.refresh()
        self.on_change()

    def undo(self):
        self.finish()
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.restore(self.undo_stack.pop())

    def redo(self):
        self.finish()
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self.restore(self.redo_stack.pop())

    def clear(self):
        self.finish()
        if not self.sketch.edges:
            return
        if messagebox.askyesno('清空草图', '清空全部线条？清空后仍可撤销。', parent=self):
            self.transaction(lambda s: s.remove([e['id'] for e in s.edges]))

    def choose_tool(self):
        self.anchor = self.hover_point = self.chain = None
        self.curve_points = []
        descriptions = {
            'select': '点击选线，双击选整个轮廓；拖端点改位置、拖线改弧度。Shift 多选。',
            'line': '点击起点、终点，或按住拖出一条直线。',
            'polyline': '依次点击连线，点击起点闭合；右键或 Esc 完成，之后可继续画其他轮廓。',
            'bezier': '依次点击起点、两个控制点、终点；画完后拖动控制点塑形。',
            'rectangle': '点击两个对角，或按住拖出矩形。',
            'circle': '点击圆心，再点击圆周，或按住拖出圆。',
            'move': '拖动线条移动位置，相连端点会一起调整。',
            'erase': '点击擦除一条线或一段圆弧；可撤销。'}
        self.hint.set(descriptions[self.tool.get()])
        self.canvas.configure(cursor='arrow' if self.tool.get() == 'select' else 'crosshair')
        self.draw()

    def finish(self):
        self.tool.set('select')
        self.choose_tool()

    def select_contour(self, event):
        if self.tool.get() not in ('select', 'move'):
            return
        identifier = self.hit(event)
        if identifier is not None:
            self.drag = None
            self.selected = next(group[:] for group in self.sketch.groups if identifier in group)
            self.refresh()
        return 'break'

    def to_screen(self, point):
        return self.center_x+point[0]*self.scale, self.center_y-point[1]*self.scale

    def from_screen(self, x, y, snap=False):
        if not (self.left <= x <= self.right and self.top <= y <= self.bottom):
            return None
        point = ((x-self.center_x)/self.scale, (self.center_y-y)/self.scale)
        if max(map(abs, point)) > 300:
            return None
        if snap:
            for p in self.sketch.nodes:
                sx, sy = self.to_screen(p)
                if math.hypot(sx-x, sy-y) < 7:
                    return tuple(p)
        return point

    def hit(self, event):
        result, best = None, 9.0
        for edge in self.sketch.edges:
            curve = self.sketch.curve(edge)
            pts = [self.to_screen(p) for p in curve.samples(tolerance=.05)]
            for a, b in zip(pts, pts[1:]):
                d = np.subtract(b, a)
                f = np.clip(np.dot(np.subtract((event.x, event.y), a), d)/max(1e-9, np.dot(d, d)), 0, 1)
                distance = np.linalg.norm(np.array(a)+f*d-(event.x, event.y))
                if distance < best:
                    result, best = edge['id'], distance
        return result

    def press(self, event):
        self.canvas.focus_set()
        point = self.from_screen(event.x, event.y, snap=self.tool.get() in ('line', 'polyline', 'bezier'))
        if point is None:
            return
        tool = self.tool.get()
        if tool == 'bezier':
            self.curve_points.append(point)
            if len(self.curve_points) == 4:
                if self.transaction(lambda s: s.add_bezier(self.curve_points)):
                    self.selected = [self.sketch.edges[-1]['id']]
                    self.finish()
                    self.refresh()
                else:
                    self.curve_points = []
            self.draw()
            return
        if tool in ('line', 'rectangle', 'circle'):
            if self.anchor is None:
                self.anchor = point
            else:
                self.create_primitive(tool, self.anchor, point)
                self.anchor = None
            self.draw()
            return
        if tool == 'polyline':
            if self.anchor is None:
                self.anchor = point
                self.chain = None
            else:
                start = self.anchor
                group_index = self.chain
                def add(s):
                    if group_index is None:
                        s.add_polyline([start, point])
                    else:
                        a, b = s.node(start), s.node(point)
                        s.groups[group_index].append(s.add_edge(a, b))
                if self.transaction(add):
                    last_id = self.sketch.edges[-1]['id']
                    self.chain = next(i for i, group in enumerate(self.sketch.groups) if last_id in group)
                    self.anchor = point
                    if self.sketch.is_closed(self.sketch.groups[self.chain]):
                        self.finish()
            self.draw()
            return
        identifier = self.hit(event)
        if tool == 'erase':
            if identifier is not None:
                self.transaction(lambda s: s.remove([identifier]))
            return
        # Selected endpoint handles take precedence over the curve beneath them.
        if tool == 'select':
            for selected in self.selected:
                edge = self.sketch.edge(selected)
                for node in [edge['a'], *(edge['controls'] or [])] + ([edge['b']] if edge['b'] is not None else []):
                    x, y = self.to_screen(self.sketch.nodes[node])
                    if math.hypot(x-event.x, y-event.y) < 8:
                        self.drag = ('node', node, point, self.snapshot())
                        return
        if getattr(event, 'state', 0) & 1:
            if identifier is not None:
                self.selected = [i for i in self.selected if i != identifier] if identifier in self.selected else self.selected+[identifier]
        elif tool != 'move' or identifier not in self.selected:
            self.selected = [] if identifier is None else [identifier]
        if identifier is not None and not (getattr(event, 'state', 0) & 1):
            self.drag = ('move' if tool == 'move' else 'bend', self.selected[:] if tool == 'move' else identifier, point, self.snapshot())
        self.refresh()

    def create_primitive(self, tool, a, b):
        def create(s):
            if tool == 'rectangle':
                s.add_rectangle(a, b)
            elif tool == 'circle':
                s.add_circle(a, float(np.linalg.norm(np.subtract(a, b))))
            else:
                # Join an existing open stroke at its final endpoint, preserving its identity.
                for group in s.groups:
                    last = s.curve(s.edge(group[-1]))
                    if not s.is_closed(group) and np.linalg.norm(last.end-a) < .001:
                        group.append(s.add_edge(s.node(a), s.node(b)))
                        return
                s.add_polyline([a, b])
        if self.transaction(create):
            self.selected = [self.sketch.edges[-1]['id']]
            self.refresh()

    def hover(self, event):
        self.hover_point = self.from_screen(event.x, event.y, snap=self.tool.get() in ('line', 'polyline'))
        if self.anchor is not None:
            self.draw()

    def motion(self, event):
        self.hover(event)
        if self.drag is None:
            return
        point = self.from_screen(event.x, event.y)
        if point is None:
            return
        kind, identifier, start, before = self.drag
        if np.linalg.norm(np.subtract(point, start))*self.scale < 2:
            return
        candidate = Sketch.from_document(before[0])
        if kind == 'node':
            candidate.nodes[identifier] = list(point)
        elif kind == 'move':
            delta = np.subtract(point, start)
            nodes = {n for i in identifier for e in [candidate.edge(i)]
                     for n in [e['a'], e['b'], *(e['controls'] or [])] if n is not None}
            for node in nodes:
                candidate.nodes[node] = (np.array(candidate.nodes[node])+delta).tolist()
        else:
            e = candidate.edge(identifier)
            a = np.array(candidate.nodes[e['a']])
            if e['radius'] is not None:
                e['radius'] = float(np.linalg.norm(np.subtract(point, a)))
            elif e['controls'] is not None:
                delta = np.subtract(point, start)
                for node in e['controls']:
                    candidate.nodes[node] = (np.array(candidate.nodes[node])+delta).tolist()
            else:
                b = np.array(candidate.nodes[e['b']])
                d = b-a
                if np.dot(d, d) < 1e-9:
                    return
                e['bend'] = float(np.clip(2*cross(d, np.subtract(point, (a+b)/2))/np.dot(d, d), -1, 1))
        try:
            candidate.validate()
        except ValueError as error:
            self.message.set(str(error))
            return
        self.sketch = candidate
        self.raw_fields = None
        self.draw()

    def release(self, event):
        if self.drag is not None:
            before = self.drag[3]
            self.drag = None
            if self.sketch.document() != before[0]:
                candidate = self.sketch.copy()
                self.sketch = Sketch.from_document(before[0])
                self.raw_fields = before[1]
                def assign(s):
                    s.__dict__.update(candidate.__dict__)
                if not self.transaction(assign, solve=True):
                    self.draw()
            return
        if self.anchor is not None and self.tool.get() in ('line', 'rectangle', 'circle') and event is not None:
            end = self.from_screen(event.x, event.y, snap=self.tool.get() == 'line')
            if end is not None and np.linalg.norm(np.subtract(end, self.anchor))*self.scale > 4:
                self.create_primitive(self.tool.get(), self.anchor, end)
                self.anchor = None
                self.draw()

    def delete_selected(self):
        if self.selected:
            ids = self.selected[:]
            self.selected = []
            self.transaction(lambda s: s.remove(ids))

    def add_relation(self, kind):
        ids = self.selected[:]
        self.transaction(lambda s: s.constrain(kind, ids))

    def dimension(self):
        try:
            value = float(self.size_value.get())
        except ValueError:
            self.message.set('请输入有效尺寸。')
            return
        kind = {'长度': 'length', '半径': 'radius', '直径': 'diameter', '宽度': 'width', '高度': 'height'}[self.size_kind.get()]
        if self.transaction(lambda s: s.constrain(kind, self.selected, value)):
            self.show_dimension()
            self.message.set(f'已设置{self.size_kind.get()} {value:g} mm；后续编辑会保持此尺寸，可在关系列表中移除。')

    def scale_all(self):
        try:
            factor = float(self.scale_value.get())
        except ValueError:
            self.message.set('请输入有效缩放倍数。')
            return
        if self.transaction(lambda s: s.scale(factor)):
            self.fit()
            self.show_dimension()
            self.message.set(f'整个草图已缩放为 {factor:g} 倍，已设尺寸同步更新；可撤销。')

    def remove_relation(self):
        choice = self.relation_list.curselection()
        if choice:
            index = self.visible_relations[choice[0]]
            self.transaction(lambda s: s.constraints.pop(index))

    def auto_order(self):
        automatic = self.order_auto.get()
        self.transaction(lambda s: setattr(s, 'order', [] if automatic else list(range(len(s.groups)))))

    def reorder(self, step):
        choice = self.order_list.curselection()
        if not choice:
            return
        old = choice[0]
        new = old+step
        if not 0 <= new < len(self.sketch.groups):
            return
        def change(s):
            order = s.order or list(range(len(s.groups)))
            order[old], order[new] = order[new], order[old]
            s.order = order
        if self.transaction(change):
            self.order_list.selection_set(new)

    def select_group(self, _event=None):
        choice = self.order_list.curselection()
        if choice and choice[0] < len(self.sketch.groups):
            _, group = self.sketch.ordered_groups()[choice[0]]
            self.selected = group[:]
            self.refresh_properties()
            self.draw()

    def refresh_properties(self):
        identifiers = {e['id'] for e in self.sketch.edges}
        self.selected = [i for i in self.selected if i in identifiers]
        self.selection_text.set('尚未选择线条\n双击选整个轮廓；Shift 多选。')
        options = []
        if len(self.selected) == 1:
            edge = self.sketch.edge(self.selected[0])
            curve = self.sketch.curve(edge)
            if edge['radius'] is not None:
                title, options = '圆', ['直径', '半径']
            elif edge['controls'] is not None:
                title, options = '控制点曲线', ['宽度', '高度']
            elif curve.center is not None:
                title, options = '圆弧', ['半径', '直径', '宽度', '高度']
            elif edge['a'] == edge['b']:
                title = '固定点'
            else:
                title, options = '直线', ['长度', '宽度', '高度']
            self.selection_text.set(f'已选：{title}')
        elif self.selected:
            group = next((g for g in self.sketch.groups if set(g) == set(self.selected)), None)
            title = '闭合轮廓' if group and self.sketch.is_closed(group) else '所选线条范围'
            if group and self.sketch.is_closed(group) and len(group) == 4:
                curves = [self.sketch.curve(self.sketch.edge(i)) for i in group]
                directions = [c.end-c.start for c in curves]
                if (all(c.center is None and not isinstance(c, Bezier) for c in curves)
                        and all(abs(np.dot(a, b)) < .001 for a, b in zip(directions, directions[1:]+directions[:1]))):
                    title = '矩形'
            self.selection_text.set(f'已选：{title}（{len(self.selected)} 条线）')
            options = ['宽度', '高度']
        self.dimension_picker.configure(values=options, state='readonly' if options else 'disabled')
        self.dimension_button.configure(state='normal' if options else 'disabled')
        selection = tuple(self.selected)
        if selection != self.dimension_selection:
            self.dimension_selection = selection
            self.size_kind.set(options[0] if options else '长度')
            self.show_dimension()
        elif self.size_kind.get() not in options and options:
            self.size_kind.set(options[0])
            self.show_dimension()
        elif options:
            self.show_dimension()
        self.relation_list.delete(0, 'end')
        self.visible_relations = []
        for i, c in enumerate(self.sketch.constraints):
            if set(c['edges']).intersection(self.selected):
                label = RELATIONS[c['kind']]+(' '+f"{c['value']:g} mm" if 'value' in c else '')
                self.relation_list.insert('end', label)
                self.visible_relations.append(i)

    def show_dimension(self):
        if not self.selected:
            self.size_value.set('')
            self.dimension_hint.set('先选中要修改的线条或轮廓。')
            return
        name = self.size_kind.get()
        if name in ('宽度', '高度'):
            low, high = self.sketch.selection_bounds(self.selected)
            value = (high-low)[0 if name == '宽度' else 1]
            hint = '沿水平 X 方向的总跨度。' if name == '宽度' else '沿竖直 Y 方向的总跨度。'
        else:
            curve = self.sketch.curve(self.sketch.edge(self.selected[0]))
            value = np.linalg.norm(curve.end-curve.start) if name == '长度' else curve.radius*(2 if name == '直径' else 1)
            hint = {'长度': '两个端点之间的实际距离。', '半径': '圆心至圆周的距离。', '直径': '穿过圆心的完整宽度；等于两倍半径。'}[name]
        self.size_value.set(f'{value:g}')
        self.dimension_hint.set(f'当前{name} {value:g} mm。\n{hint}')

    def refresh(self):
        self.refresh_properties()
        self.order_auto.set(not self.sketch.order)
        self.order_list.delete(0, 'end')
        for index, group in self.sketch.ordered_groups():
            self.order_list.insert('end', f'轮廓 {index+1} · '+('闭合' if self.sketch.is_closed(group) else '未闭合'))
        try:
            _, shared = self.sketch.compile()
            self.message.set('发现公共线：按呈现顺序仅输出一次。尽量避免闭合轮廓共边。' if shared
                             else '各轮廓之间会关闭输出再切换。滚轮缩放；右键或 Esc 完成绘制。')
        except ValueError as error:
            self.message.set(str(error))
        self.draw()

    def fit(self):
        values = [abs(v) for p in self.sketch.nodes for v in p]
        for edge in self.sketch.edges:
            if edge['radius'] is not None:
                values += [abs(v)+edge['radius'] for v in self.sketch.nodes[edge['a']]]
        self.view_span = max(60, min(660, max(values, default=20)*2.5))
        self.draw()

    def zoom(self, factor):
        self.view_span = min(660, max(2, self.view_span*factor))
        self.draw()
        return 'break'

    def draw(self):
        c = self.canvas
        c.delete('all')
        width, height = max(c.winfo_width(), 240), max(c.winfo_height(), 180)
        self.left, self.top, self.right, self.bottom = 35, 25, width-18, height-30
        self.center_x, self.center_y = (self.left+self.right)/2, (self.top+self.bottom)/2
        self.scale = min(self.right-self.left, self.bottom-self.top)/self.view_span
        c.create_rectangle(self.left, self.top, self.right, self.bottom, fill='white', outline='#dce5ef')
        c.create_line(self.left, self.center_y, self.right, self.center_y, fill='#dce5ef')
        c.create_line(self.center_x, self.top, self.center_x, self.bottom, fill='#dce5ef')
        c.create_text(self.right, height-12, text=f'毫米 · 视野 {self.view_span:g} mm', anchor='e', fill='#647994')
        c.create_text(8, 10, text=f'原点 ({self.cx:g}, {self.cy:g}) mm', anchor='w', fill='#647994')
        for edge in self.sketch.edges:
            curve = self.sketch.curve(edge)
            positions = [self.to_screen(p) for p in curve.samples(tolerance=.08)]
            if np.linalg.norm(curve.end-curve.start) < .001 and curve.center is None and not isinstance(curve, Bezier):
                x, y = positions[0]
                c.create_oval(x-4, y-4, x+4, y+4, fill='#2463c5', outline='white', tags='sketch_line')
            for a, b in zip(positions, positions[1:]):
                clipped = clip_segment((self.left, self.top, self.right, self.bottom), a, b)
                if clipped:
                    c.create_line(*clipped, fill='#e68a2e' if edge['id'] in self.selected else '#2463c5',
                                  width=3 if edge['id'] in self.selected else 2, tags='sketch_line')
            if edge['id'] in self.selected:
                nodes = [edge['a'], *(edge['controls'] or [])] + ([edge['b']] if edge['b'] is not None else [])
                if edge['controls'] is not None:
                    coords = [v for node in nodes for v in self.to_screen(self.sketch.nodes[node])]
                    c.create_line(*coords, fill='#bc965c', dash=(3, 4), tags='control_polygon')
                for node in nodes:
                    x, y = self.to_screen(self.sketch.nodes[node])
                    c.create_rectangle(x-4, y-4, x+4, y+4, fill='white', outline='#e68a2e', tags='handle')
                x, y = self.to_screen(curve.point(.5))
                c.create_oval(x-4, y-4, x+4, y+4, fill='#e68a2e', outline='white', tags='handle')
        for relation in self.sketch.constraints:
            if 'value' not in relation:
                continue
            if self.selected and not set(relation['edges']).intersection(self.selected):
                continue
            low, high = self.sketch.selection_bounds(relation['edges'])
            left, bottom = self.to_screen(low)
            right, top = self.to_screen(high)
            name = relation['kind']
            color = '#996225' if set(relation['edges']).intersection(self.selected) else '#768b99'
            label = f"{RELATIONS[name]} {relation['value']:g} mm"
            if name == 'width':
                y = bottom+18
                c.create_line(left, bottom, left, y+4, fill=color, tags='dimension')
                c.create_line(right, bottom, right, y+4, fill=color, tags='dimension')
                c.create_line(left, y, right, y, arrow='both', fill=color, tags='dimension')
                c.create_text((left+right)/2, y+10, text=label, fill=color, tags='dimension')
            elif name == 'height':
                x = right+18
                c.create_line(right, bottom, x+4, bottom, fill=color, tags='dimension')
                c.create_line(right, top, x+4, top, fill=color, tags='dimension')
                c.create_line(x, bottom, x, top, arrow='both', fill=color, tags='dimension')
                c.create_text(x+8, (top+bottom)/2, text=label, anchor='w', fill=color, tags='dimension')
            else:
                curve = self.sketch.curve(self.sketch.edge(relation['edges'][0]))
                x, y = self.to_screen(curve.point(.5))
                c.create_text(x+7, y-15, text=label, anchor='w', fill=color, tags='dimension')
        if self.curve_points:
            pts = self.curve_points+([self.hover_point] if self.hover_point is not None else [])
            if len(pts) > 1:
                c.create_line(*[v for p in pts for v in self.to_screen(p)], fill='#087e8b', dash=(3, 4))
            if len(pts) == 4:
                preview = Bezier(pts)
                c.create_line(*[v for p in preview.samples() for v in self.to_screen(p)], fill='#2463c5', width=2)
            for p in self.curve_points:
                x, y = self.to_screen(p)
                c.create_rectangle(x-3, y-3, x+3, y+3, fill='#087e8b')
        if self.anchor is not None:
            x, y = self.to_screen(self.anchor)
            c.create_oval(x-4, y-4, x+4, y+4, fill='#087e8b', outline='white')
            if self.hover_point is not None:
                u, v = self.to_screen(self.hover_point)
                if self.tool.get() == 'rectangle':
                    c.create_rectangle(x, y, u, v, outline='#087e8b', dash=(4, 3))
                elif self.tool.get() == 'circle':
                    r = math.hypot(u-x, v-y)
                    c.create_oval(x-r, y-r, x+r, y+r, outline='#087e8b', dash=(4, 3))
                else:
                    c.create_line(x, y, u, v, fill='#087e8b', dash=(4, 3))
        if not self.sketch.edges:
            c.create_text(width/2, height/2-20, text='选择上方工具，开始绘制草图', fill='#647994', font=('Microsoft YaHei UI', 12))
