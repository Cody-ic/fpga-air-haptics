"""Vector hand illustration in reference coordinates, never a tracked hand."""

import numpy as np

from .palm import HAND_OUTLINE


class HandView:
    # Illustration geometry only; these are not measured hand dimensions.
    LOW = np.array([-72.0, -66.0])
    HIGH = np.array([54.0, 113.0])

    def __init__(self, canvas, path=None):
        self.canvas = canvas
        self.width = max(canvas.winfo_width(), 260)
        self.height = max(canvas.winfo_height(), 180)
        low, high = self.LOW.copy(), self.HIGH.copy()
        if path is not None:
            path = np.asarray(path)
            path = path[np.isfinite(path).all(axis=1)]
            if len(path):
                low = np.minimum(low, np.min(path, axis=0))
                high = np.maximum(high, np.max(path, axis=0))
        self.origin = (low + high) / 2
        span = (high-low)*1.12
        self.scale = min((self.width-100)/span[0], (self.height-80)/span[1])
        self.center = (self.width/2, self.height/2)

    def screen(self, point):
        return (self.center[0]+(point[0]-self.origin[0])*self.scale,
                self.center[1]-(point[1]-self.origin[1])*self.scale)

    def curve(self, start, segments):
        points = [start]
        for a, b, end in segments:
            p = np.asarray(start)
            a, b, end = map(np.asarray, (a, b, end))
            for t in np.linspace(0, 1, 13)[1:]:
                points.append((1-t)**3*p+3*(1-t)**2*t*a+3*(1-t)*t*t*b+t**3*end)
            start = end
        return [value for point in points for value in self.screen(point)]

    def draw(self):
        c, width, height = self.canvas, self.width, self.height
        c.create_rectangle(46, 28, width-26, height-36, outline="#e5eaf0")
        c.create_text(width-40, 14, text="手掌示意 · 非位置检测", anchor="e", fill="#748398",
                      font=("Microsoft YaHei UI", 9), tags="hand_caption")
        outline = [value for point in HAND_OUTLINE for value in self.screen(point)]
        c.create_polygon(*outline, fill="#fff0df", outline="#d6b99d", width=2, tags="hand_reference")
        creases = [((-26, 17), [((-13, 11), (11, 14), (32, 22))]),
                   ((-21, -1), [((-6, 3), (13, -3), (28, -8))]),
                   ((-29, 5), [((-14, -7), (-12, -23), (-22, -33))]),
                   ((-20, -46), [((-6, -43), (9, -43), (23, -44))])]
        for start, segments in creases:
            c.create_line(*self.curve(start, segments), fill="#e6cdb6", width=1.5, tags="hand_reference")
        x, y = self.screen((0, 0))
        c.create_line(x-5, y, x+5, y, fill="#ccb299", tags="hand_reference")
        c.create_line(x, y-5, x, y+5, fill="#ccb299", tags="hand_reference")
        c.create_text(48, 14, text="Y / mm", anchor="w", fill="#7a8da2", font=("Microsoft YaHei UI", 8))
        c.create_text(width-28, height-16, text="X / mm", anchor="e", fill="#7a8da2", font=("Microsoft YaHei UI", 8))
        for fraction in (0, .25, .5, .75, 1):
            px = 48+(width-100)*fraction
            py = 30+(height-70)*fraction
            vx = (px-self.center[0])/self.scale+self.origin[0]
            vy = (self.center[1]-py)/self.scale+self.origin[1]
            c.create_text(px, height-24, text=f"{vx:.0f}", fill="#8794a5", font=("Microsoft YaHei UI", 8))
            c.create_text(40, py, text=f"{vy:.0f}", anchor="e", fill="#8794a5", font=("Microsoft YaHei UI", 8))
