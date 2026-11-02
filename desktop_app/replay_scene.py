"""Embedded OpenGL view of measured hand vertices and the calibrated pad."""

import math
import numpy as np
from OpenGL import GL
from OpenGL.GLU import gluLookAt, gluPerspective
from pyopengltk import OpenGLFrame

from .egopressure import display_points, pressure_rgb


class ReplayScene(OpenGLFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.vertices = self.joints = self.force = None
        self.show_joints = True
        self.texture = None
        self.texture_dirty = True
        self.limit_kpa = 20
        self.reset_camera()
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._orbit)
        self.bind("<ButtonPress-3>", self._press)
        self.bind("<B3-Motion>", self._pan)
        self.bind("<MouseWheel>", self._zoom)
        self.bind("<Double-Button-1>", lambda _: self.reset_camera())

    def reset_camera(self, top=False):
        self.yaw, self.elevation, self.distance = (-math.pi/2, math.radians(89.5), .48) if top else (-1.08, .92, .48)
        self.target = np.array([0., .025, .02])
        self.render()

    def _press(self, event):
        self.mouse = (event.x, event.y)

    def _orbit(self, event):
        dx, dy = event.x-self.mouse[0], event.y-self.mouse[1]
        self.yaw -= dx*.006
        self.elevation = float(np.clip(self.elevation+dy*.006, -.3, 1.56))
        self.mouse = event.x, event.y
        self.render()

    def _pan(self, event):
        dx, dy = event.x-self.mouse[0], event.y-self.mouse[1]
        right = np.array([-math.sin(self.yaw), math.cos(self.yaw), 0.])
        up = np.array([-math.cos(self.yaw)*math.sin(self.elevation),
                       -math.sin(self.yaw)*math.sin(self.elevation), math.cos(self.elevation)])
        self.target += (-dx*right+dy*up)*self.distance/max(self.winfo_height(), 1)
        self.mouse = event.x, event.y
        self.render()

    def _zoom(self, event):
        self.distance = float(np.clip(self.distance * (0.88 if event.delta > 0 else 1/0.88), .12, 1.8))
        self.render()
        return "break"

    def set_frame(self, vertices, joints, force):
        self.vertices = None if vertices is None else np.ascontiguousarray(display_points(vertices))
        self.joints = None if joints is None else np.ascontiguousarray(display_points(joints))
        self.force = force
        self.texture_dirty = True
        self.render()

    def initgl(self):
        GL.glClearColor(.957, .965, .977, 1)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glEnable(GL.GL_POINT_SMOOTH)
        GL.glEnable(GL.GL_LINE_SMOOTH)
        if self.texture is None:
            self.texture = GL.glGenTextures(1)
        self.renderer = (GL.glGetString(GL.GL_RENDERER) or b"unknown").decode(errors="replace")

    def render(self):
        if getattr(self, "context_created", False) and self.winfo_ismapped():
            self.tkMakeCurrent()
            self.redraw()
            self.tkSwapBuffers()

    def tkResize(self, event):
        # The library's resize handler doesn't bind the current context first.
        self.width, self.height = event.width, event.height
        self.render()

    def tkExpose(self, _event):
        self.render()

    def _quad(self, corners, color):
        GL.glColor4f(*color)
        GL.glBegin(GL.GL_QUADS)
        for point in corners:
            GL.glVertex3f(*point)
        GL.glEnd()

    def redraw(self):
        width, height = max(self.winfo_width(), 1), max(self.winfo_height(), 1)
        GL.glViewport(0, 0, width, height)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        gluPerspective(35, width/height, .005, 5)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        eye = self.target + self.distance * np.array([
            math.cos(self.elevation)*math.cos(self.yaw), math.cos(self.elevation)*math.sin(self.yaw), math.sin(self.elevation)])
        gluLookAt(*eye, *self.target, 0, 0, 1)

        # Subtle studio grid, with a calibrated pad sitting above it.
        GL.glColor4f(.73, .77, .83, .24)
        GL.glLineWidth(1)
        GL.glBegin(GL.GL_LINES)
        for value in np.arange(-.3, .31, .025):
            GL.glVertex3f(value, -.3, -.009); GL.glVertex3f(value, .3, -.009)
            GL.glVertex3f(-.3, value, -.009); GL.glVertex3f(.3, value, -.009)
        GL.glEnd()
        w, h, z = .124, .07275, -.006
        self._quad([(-w,-h,z), (w,-h,z), (w,h,z), (-w,h,z)], (.43,.47,.55,1))
        for a,b in [((-w,-h),(w,-h)), ((w,-h),(w,h)), ((w,h),(-w,h)), ((-w,h),(-w,-h))]:
            self._quad([(*a,z),(*b,z),(*b,-.001),(*a,-.001)], (.55,.59,.65,1))
        self._quad([(-w,-h,-.001), (w,-h,-.001), (w,h,-.001), (-w,h,-.001)], (.69,.73,.79,1))

        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture)
        if self.texture_dirty:
            pixels = pressure_rgb(self.force, self.limit_kpa) if self.force is not None else np.full((105,185,3), 150, np.uint8)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            GL.glTexImage2D(GL.GL_TEXTURE_2D,0,GL.GL_RGB,185,105,0,GL.GL_RGB,GL.GL_UNSIGNED_BYTE,pixels)
            self.texture_dirty = False
        GL.glEnable(GL.GL_TEXTURE_2D)
        GL.glColor4f(1,1,1,1)
        GL.glBegin(GL.GL_QUADS)
        # Row zero is world -y, now display +y. No mirror or transpose.
        for uv, position in [((0,0),(-.120,.06875,0)), ((1,0),(.120,.06875,0)),
                             ((1,1),(.120,-.06875,0)), ((0,1),(-.120,-.06875,0))]:
            GL.glTexCoord2f(*uv); GL.glVertex3f(*position)
        GL.glEnd()
        GL.glDisable(GL.GL_TEXTURE_2D)

        density = float(self.tk.call("tk", "scaling"))/(96/72)
        if self.joints is not None and self.show_joints:
            GL.glLineWidth(2*density)
            GL.glColor4f(.16, .40, .69, .65)
            GL.glBegin(GL.GL_LINES)
            for start in (1,5,9,13,17):
                chain = [0, *range(start,start+4)]
                for a,b in zip(chain,chain[1:]):
                    GL.glVertex3fv(self.joints[a]); GL.glVertex3fv(self.joints[b])
            GL.glEnd()
        if self.vertices is not None:
            GL.glPointSize(float(np.clip(3.2*density*.48/self.distance, 2, 13)))
            GL.glColor4f(.12,.44,.80,.92)
            GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
            GL.glVertexPointer(3, GL.GL_FLOAT, 0, self.vertices)
            GL.glDrawArrays(GL.GL_POINTS, 0, len(self.vertices))
            GL.glDisableClientState(GL.GL_VERTEX_ARRAY)

    def destroy(self):
        if self.context_created and self.winfo_ismapped():
            self.tkMakeCurrent()
            if self.texture is not None:
                GL.glDeleteTextures([self.texture])
        # pyopengltk 0.0.4 does not release its Windows context on destruction.
        if self.context_created and hasattr(self, "_OpenGLFrame__context"):
            from OpenGL.WGL import wglMakeCurrent, wglDeleteContext
            from ctypes import windll, c_void_p
            windll.user32.ReleaseDC.argtypes = [c_void_p, c_void_p]
            wglMakeCurrent(0, 0)
            wglDeleteContext(self._OpenGLFrame__context)
            windll.user32.ReleaseDC(self.winfo_id(), self._OpenGLFrame__window)
        super().destroy()
