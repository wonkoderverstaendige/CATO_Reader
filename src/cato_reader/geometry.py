import math
import statistics

from pdfminer import layout as pdflt


def line_angle_rad(line):
    """Angle of a line in radians."""
    return math.atan2((line.y1 - line.y0), (line.x1 - line.x0)) / math.pi


def line_len(line):
    """Euclidean length of a line (or the diagonal of a bounding box).
    """
    return math.sqrt(math.pow(line.x1 - line.x0, 2) + math.pow(line.x1 - line.y0, 2))


def distance(x0, y0, x1, y1):
    """Euclidean distance between coordinates.
    """
    return math.sqrt(math.pow(x1 - x0, 2) + math.pow(y1 - y0, 2))


def is_actually_line(element, method='width'):  # threshold=0.01,
    """Checks if element might be considered a line.

    If threshold is >= 0.2, it'll be interpreted as maximum pixel width.
    If threshold is < 0.2, it'll be interpreted as aspect ratio.
    The threshold-threshold is pretty arbitrary and will be updated later.

    Note: We assume horizontal or vertical orientation, no rotations. I.e. the
    bounding box is looked at, not the actual shape of the object.
    """
    if isinstance(element, pdflt.LTLine):
        return True
    is_line = False

    dx = abs(element.x1 - element.x0)
    dy = abs(element.y1 - element.y0)

    # check if any dimension is less than what we consider the width of a reasonable line
    if method == 'width':
        threshold = 3
        is_line = min(dx, dy) < threshold
        # if is_line:
        #     logging.info(f'{"Is" if is_line else "Is not"} line due to dx={dx:.2f} or dy={dy:.2f}')

    # check if the objects aspect ratio makes us consider it line-like
    elif method == 'aspect':
        threshold = 40
        aspect_ratio = max(dx, dy) / min(dx, dy) if dy > 0 and dx > 0 else math.inf
        is_line = aspect_ratio < threshold
        # if is_line:
        #     logging.info(f'{"Is" if is_line else "Is not"} line due to aspect ratio ={aspect_ratio:.2f}')

    # if is_line:
    #     logging.info(f"Element {element} is considered a line!")

    return is_line


def find_with_vertex_at(point, elements, corner='any', epsilon=4):
    """Find elements with bounding box vertices near given point.
    corner: point of origin for search
        any for all vertices, ul, ll, lr, ur for corner specific search, center for center point
    epsilon: distance threshold
    """
    matches = []
    for el in elements:
        d_vtx = dict()
        d_vtx['ul'] = distance(x0=point[0], y0=point[1], x1=min(el.x0, el.x1), y1=max(el.y0, el.y1)) < epsilon
        d_vtx['ur'] = distance(x0=point[0], y0=point[1], x1=max(el.x0, el.x1), y1=max(el.y0, el.y1)) < epsilon
        d_vtx['ll'] = distance(x0=point[0], y0=point[1], x1=min(el.x0, el.x1), y1=min(el.y0, el.y1)) < epsilon
        d_vtx['lr'] = distance(x0=point[0], y0=point[1], x1=max(el.x0, el.x1), y1=min(el.y0, el.y1)) < epsilon

        d_vtx['any'] = any(d_vtx.values())

        if d_vtx[corner]:
            matches.append(el)
    return matches


def grow_line(line, elements, direction):
    """Starting from a line, find connected colinear line segments and return a merged line object."""
    if direction in ['up', 'down', 'v']:
        pre = ((line.x0 + line.x1) / 2, min(line.y0, line.y1))
        post = ((line.x0 + line.x1) / 2, max(line.y0, line.y1))
    elif direction in ['left', 'right', 'h']:
        pre = (min(line.x0, line.x1), (line.y0 + line.y1) / 2)
        post = (max(line.x0, line.x1), (line.y0 + line.y1) / 2)
    else:
        raise NotImplementedError

    # recursively expand the line segment in the chosen direction
    extension = []
    if direction in ['left', 'up', 'h', 'v']:
        extension += [ln for ln in find_with_vertex_at(point=(pre[0], pre[1]), elements=elements) if ln != line]

    if direction in ['right', 'down', 'h', 'v']:
        extension += [ln for ln in find_with_vertex_at(point=(post[0], post[1]), elements=elements) if ln != line]

    if len(extension) == 1:
        last_segment = extension[-1]
        extension.extend(grow_line(last_segment, elements, direction))
    return extension


def merge_lines(lines):
    """Assumes only horizontal and vertical lines"""
    nl = 0
    while nl < len(lines):
        line = lines[nl]
        direction = 'h' if line_angle_rad(lines[nl]) < 0.1 else 'v'
        extended = [line] + list(set(grow_line(lines[nl], lines[nl + 1:], direction)))
        p0 = (min([min(ln.x0, ln.x1) for ln in extended]), min([min(ln.y0, ln.y1) for ln in extended]))
        p1 = (max([max(ln.x0, ln.x1) for ln in extended]), max([max(ln.y0, ln.y1) for ln in extended]))
        joined = pdflt.LTLine(line.linewidth, p0, p1, line.stroke, line.fill, line.evenodd, line.stroking_color,
                              line.non_stroking_color)
        lines = lines[:nl] + [joined] + [ln for ln in lines[nl + 1:] if ln not in extended]
        nl += 1
    return lines


def as_line(el, straighten=False):
    """Convert a layout element to a line from its bounding box.
    """
    # TODO: Make line horizontal or vertical!
    if straighten:
        raise NotImplementedError('TODO: STRAIGHTEN LINE!')
    return pdflt.LTLine(linewidth=1, p0=(el.x0, el.y0), p1=(el.x1, el.y1),
                        stroke=True, fill=False, stroking_color=el.stroking_color,
                        non_stroking_color=el.non_stroking_color)


def is_visible(color):
    if color is None:
        return False
    if isinstance(color, int):
        return color < 1
    if isinstance(color, list):
        return False
    if isinstance(color, tuple):
        return statistics.mean(color) < 1


def color_float(color):
    if not is_visible(color):
        return 1.0
    else:
        if isinstance(color, tuple):
            return statistics.mean(color)
        if isinstance(color, list):
            return 1.0
        if isinstance(color, int):
            return float(color)
