"""Canvas clipping shared by the sketch editor's drawing tools."""


def clip_segment(bounds, start, end):
    left, top, right, bottom = bounds
    x, y = start
    dx, dy = end[0]-x, end[1]-y
    low, high = 0.0, 1.0
    for p, q in ((-dx, x-left), (dx, right-x), (-dy, y-top), (dy, bottom-y)):
        if p == 0:
            if q < 0:
                return None
        elif p < 0:
            low = max(low, q/p)
        else:
            high = min(high, q/p)
        if low > high:
            return None
    return x+low*dx, y+low*dy, x+high*dx, y+high*dy
