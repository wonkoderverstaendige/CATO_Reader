import math
from statistics import mean
from matplotlib import pyplot as plt
import matplotlib.patches as mpl_patches
from collections.abc import Iterable

from src.cato_reader.PDFReader import Page


def plot_page(pages):
    if isinstance(pages, Page):
        pages = [pages]

    n_col = 4
    n_row = math.ceil(len(pages) / n_col)

    fig, ax = plt.subplots(n_row, n_col, figsize=(5 * n_col, 7 * n_row))
    for i, ax in enumerate(fig.axes):
        if i >= len(pages):
            break
        page = pages[i]
        for rect in page.rectangles:
            nsc = mean(rect.non_stroking_color) if isinstance(rect.non_stroking_color,
                                                              Iterable) else rect.non_stroking_color
            if nsc < 1:
                pass  # ax.add_patch(mpl_patches.Rectangle((rect.x0, rect.y0), rect.x1-rect.x0, rect.y1-rect.y0, facecolor=plt.cm.gray(rect.non_stroking_color)))  # facecolor=[rect.non_stroking_color]*3

        # horizontal lines
        for n, line in enumerate(page.h_lines):
            if line.stroke:
                ax.plot([line.x0, line.x1], [line.y0, line.y1], c='gray', alpha=0.4)  # f'C{n}'

        # vertical lines
        for n, line in enumerate(page.v_lines):
            if line.stroke:
                ax.plot([line.x0, line.x1], [line.y0, line.y1], c='gray',
                        alpha=0.4)  # plt.cm.Pastel2(line.stroking_color)

        # visit markers
        for visit in page.visits:
            ax.add_patch(
                mpl_patches.Rectangle((visit.x0, visit.y0), visit.x1 - visit.x0, visit.y1 - visit.y0, facecolor='green',
                                      alpha=0.1))

        # record markers
        for record in page.records:
            for anchor in record.anchor:
                ax.add_patch(mpl_patches.Rectangle((anchor.x0, anchor.y0), anchor.x1 - anchor.x0, anchor.y1 - anchor.y0,
                                                   facecolor='red', alpha=0.1))
            ax.add_patch(mpl_patches.Rectangle((record.x0, record.y0), record.x1 - record.x0, record.y1 - record.y0,
                                               facecolor='yellow', alpha=0.1))

            for entry in record.entries:
                ax.add_patch(mpl_patches.Rectangle((entry.x0, entry.y0), entry.x1 - entry.x0, entry.y1 - entry.y0,
                                                   facecolor='purple', alpha=0.1))
        # ax.set_xlim((page.bbox[0], page.bbox[2]))
        # ax.set_ylim((page.bbox[1], page.bbox[3]));
        ax.set_xlim((40, 560))
        ax.set_ylim((50, 750))
        ax.set_title(page.page_number)
    return fig
