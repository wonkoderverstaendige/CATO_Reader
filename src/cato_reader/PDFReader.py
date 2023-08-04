import re
import sys
from pathlib import Path
from collections import namedtuple
import logging
from datetime import datetime
import locale
import argparse
import json

import pdfminer.high_level as pdfhl
import pdfminer.layout as pdflt

from tqdm import tqdm

from src.cato_reader.geometry import line_angle_rad, is_actually_line, as_line, is_visible, color_float
from src.cato_reader.constants import DRUGS_OF_INTEREST, DRUGS_OF_NOTE, EXCLUDED_TREATMENT_KEYWORDS, DRUG_COMBINATIONS, \
    DRUGS_OF_LOW_PRIORITY

# Set the locale so that some conversions understand the german month names
locale.setlocale(locale.LC_ALL, 'de_DE.UTF-8')

DEBUG = False

Point = namedtuple("Point", "x y")
Box = namedtuple("Box", "x0 y0 x1 y1")

# entry-based parameterization
PRE_VISIT_DIST = 10
PRE_RECORD_DIST = 20
PRE_PAGE_END_DIST = 70
RECORD_WIDTH = 553


def create_logger(level=logging.INFO):
    log_handle = logging.getLogger('cato_reader')
    log_handle.propagate = False
    log_handle.setLevel(level)

    if not log_handle.handlers:
        log_handle.addHandler(logging.StreamHandler())

    return log_handle


class Entry:
    def __init__(self, anchor, bbox, page):
        self.parent_page = page
        self.logger = self.parent_page.logger
        self.anchor = anchor
        self.bbox = bbox
        self.x0, self.y0, self.x1, self.y1 = self.bbox

        self.data = self.get_data()
        self.application = None
        self.mednr = None

    def get_data(self):
        """Given the entry bounding box, extract all relevant information
        from the page within that the roi"""
        lines = [tl for tb in self.parent_page.textlines for tl in tb if
                 self.bbox.y0 < (tl.y1 + tl.y0) / 2 < self.bbox.y1]

        timestamp, med_item = self.anchor
        self.mednr = int(med_item.get_text().strip().split('Med. Nr.:')[1])

        timestamp = timestamp.get_text().strip()
        start, end = timestamp.split(' - ') if ' - ' in timestamp else (timestamp, timestamp)

        entry_data = {'mednr': self.mednr,
                      'start': start,
                      'end': end,
                      'application': None,
                      'premed': None,
                      'drug': None,
                      'arzt': None,
                      'apotheker': None,
                      'verabreicht': None,
                      'exclusion': None
                      }

        # TODO: Deduplicate !!!
        # Arzt
        #
        end_of_entry_elements = []
        arzt_pattern = r'Arzt:.*\((\w*)\)'
        arzt = None
        arzt_lines = [ln for ln in lines if 'Arzt' in ln.get_text()]
        for line in arzt_lines:
            re_result = re.search(arzt_pattern, line.get_text().strip('\n'))
            if re_result:
                arzt = re_result.group(1).lower()
                end_of_entry_elements.append(line)
                break

        # Sometimes we have a name, but the name is too long and parts of it spill over to a line below
        if arzt is None:
            if not len(arzt_lines):
                self.logger.debug(f'No lines matching "Arzt"! MedNr. {self.mednr} in '
                                  f'{self.parent_page.parent.path.name} on p.{self.parent_page.page_number}')
            else:
                # find the lowest line, and check below for short name
                edge = min([ln.y0 for ln in arzt_lines]) - 1
                next_lines = [ln for ln in lines if ln.y0 < edge and ln.x0 < 150]
                if len(next_lines):
                    for line in next_lines:
                        re_result = re.search(r'\((\w*)\)', line.get_text().lower())
                        if re_result:
                            arzt = re_result.group(1)
                            end_of_entry_elements.append(line)
                else:
                    self.logger.warning(f'No "Arzt" on second try. MedNr. {self.mednr} in '
                                        f'{self.parent_page.parent.path.name} on p.{self.parent_page.page_number}')
                    print(next_lines)

        # Apotheker
        apotheker_pattern = r'Apotheker:.*\((\w*)\)'
        apotheker = None
        apotheker_lines = [ln for ln in lines if 'Apotheker' in ln.get_text()]
        for line in apotheker_lines:
            re_result = re.search(apotheker_pattern, line.get_text().strip('\n'))
            if re_result:
                apotheker = re_result.group(1).lower()
                end_of_entry_elements.append(line)
                break

        # Sometimes we have a name, but the name is too long and parts of it spill over to a line below
        if apotheker is None:
            if not len(apotheker_lines):
                self.logger.debug(f'No lines matching "Apotheker"! MedNr. {self.mednr} in '
                                  f'{self.parent_page.parent.path.name} on p.{self.parent_page.page_number}')
            else:
                # find the lowest line, and check below for short name
                edge = min([ln.y0 for ln in apotheker_lines]) + 1
                next_lines = [ln for ln in lines if ln.y1 < edge and ln.x0 > 200 and ln.x0 < 350]
                if len(next_lines):
                    for line in next_lines:
                        re_result = re.search(r'\((\w*)\)', line.get_text().lower())
                        if re_result:
                            apotheker = re_result.group(1)
                            end_of_entry_elements.append(line)
                else:
                    self.logger.warning(f'No "Apotheker" on second try. MedNr. {self.mednr} in '
                                        f'{self.parent_page.parent.path.name} on p.{self.parent_page.page_number}')

        # Verabreicht
        #
        nurse_pattern = r'Verabreicht:.*\((\w*)\)'
        nurse = None
        nurse_lines = [ln for ln in lines if 'Verabreicht' in ln.get_text()]
        for line in nurse_lines:
            re_result = re.search(nurse_pattern, line.get_text().strip('\n'))
            if re_result:
                nurse = re_result.group(1).lower()
                end_of_entry_elements.append(line)
                break

        # Sometimes we have a name, but the name is too long and parts of it spill over to a line below
        if nurse is None:
            if not len(nurse_lines):
                self.logger.debug(f'No lines matching "Verabreicht"! MedNr. {self.mednr} in '
                                  f'{self.parent_page.parent.path.name} on p.{self.parent_page.page_number}')
            else:
                # find the lowest line, and check below for short name
                edge = min([ln.y0 for ln in nurse_lines]) - 1
                next_lines = [ln for ln in lines if ln.y1 < edge and ln.x0 > 300]
                if len(next_lines):
                    for line in next_lines:
                        re_result = re.search(r'\((\w*)\)', line.get_text().lower())
                        if re_result:
                            nurse = re_result.group(1)
                            end_of_entry_elements.append(line)
                else:
                    self.logger.warning(f'No "Verabreicht" on second try. MedNr. {self.mednr} in '
                                        f'{self.parent_page.parent.path.name} on p.{self.parent_page.page_number}')

        # Entries that are cancelled look like valid entries, but do not contain necessary values
        # Such entries should be excluded.
        if not len(end_of_entry_elements):
            self.logger.debug(f'Invalid entry. Cancelled? MedNr. {self.mednr} in '
                              f'{self.parent_page.parent.path.name} on p.{self.parent_page.page_number}')
            cancelled = any([('storniert' in line.get_text().lower()) for line in lines])
            entry_data['exclusion'] = 'CancelledEntry' if cancelled else 'InvalidEntry'

            # bailing out on invalid entry data
            return entry_data

        # Remove all lines below the three arzt/apotheker/verabreicht elements
        # TODO: This is an issue with not detecting visit boundaries...
        limit = min([min(ln.y0, ln.y1) for ln in end_of_entry_elements])

        new_lines = []
        for line in lines:
            if line.y1 > limit - 15:
                new_lines.append(line)
            else:
                self.logger.debug(f'Removed line: {line}')

        if len(lines) > len(new_lines):
            self.logger.warning(f'Removed {len(lines) - len(new_lines)} lines from entry {self.mednr} in '
                                f'{self.parent_page.parent.path.name} on p.{self.parent_page.page_number}')
        lines = new_lines

        infusion = any(['intravenöse infusion' in tl.get_text().strip('\n').lower() for tl in lines])
        injection = any(['intravenöse injektion' in tl.get_text().strip('\n').lower() for tl in lines])

        application = 'other'
        if infusion:
            application = 'infusion'
        elif injection:
            application = 'injection'

        premeds = []
        drugs = []

        # DRUGS
        for drug in DRUGS_OF_INTEREST:
            for line in [ln.get_text() for ln in lines]:
                if drug in line:
                    if not any([kw in line.lower() for kw in EXCLUDED_TREATMENT_KEYWORDS]):
                        drugs.append(drug)
                    else:
                        self.logger.debug(f'Rejected: {line}')

        drugs = list(set(drugs))

        # if len(drugs) > 1:
        #     # go through drug combinations dictionary and look for drugs that can be removed via their combination
        #     for pKey, pItems in DRUG_COMBINATIONS.items():
        #         if all([pi in drugs for pi in pItems]):
        #             # found matching combination! Replace items with combination
        #             for pi in pItems:
        #                 drugs.remove(pi)
        #             drugs.append(pKey)

        # If we remain with multiple potential drugs...

        swap = []
        for drug in drugs:
            if drug in DRUGS_OF_NOTE:
                swap.append(drug)

        for sd in swap:
            drugs.remove(sd)
            premeds.append(sd)

        if len(drugs) > 1:
            self.logger.warning(f'Multiple notable drugs found in MedNr {self.mednr} '
                                f'of {self.parent_page.parent.path.name} p. {self.parent_page.page_number}')
            print(drugs)
            print(self.bbox)
            for line in lines:
                print(line.get_text())

        # PREMEDS

        for pm in DRUGS_OF_NOTE:
            for line in [ln.get_text() for ln in lines]:
                if pm in line:
                    if not any([kw in line.lower() for kw in EXCLUDED_TREATMENT_KEYWORDS]):
                        premeds.append(pm)

        premeds = list(set(premeds))

        if len(drugs) == 1:
            final_drug = drugs[0]
        elif len(drugs) > 1:
            final_drug = '+'.join(drugs)
        else:
            final_drug = ''

        if not len(drugs) and not len(premeds):
            for pk, pm in DRUGS_OF_LOW_PRIORITY.items():
                for line in [ln.get_text() for ln in lines]:
                    if pm in line:
                        if not any([kw in line.lower() for kw in EXCLUDED_TREATMENT_KEYWORDS]):
                            premeds.append(pk)

        if len(premeds) == 1:
            final_premed = premeds[0]
        elif len(premeds) > 1:
            final_premed = '+'.join(premeds)
        else:
            final_premed = ''

        entry_data['application'] = application
        entry_data['drug'] = final_drug
        entry_data['premed'] = final_premed
        entry_data['arzt'] = arzt
        entry_data['apotheker'] = apotheker
        entry_data['verabreicht'] = nurse

        return entry_data


class Visit:
    def __init__(self, anchor, bbox, page):
        """
        Collection of entries wrapping a visit.

        Parameters
        ----------
        anchor : Anchoring Element for Visit section
        bbox : Tuple describing visit section bounding box
        page : Parent Page object reference
        """
        # TODO: This here needs to be built out
        # Not dealing with the visit section header anchors causes a lot of headache and hackery
        # downstream as entries overlap with visit headers
        # Right now we kick out everything in a header after the terminating Arzt/Apotheker/Verabreichung lines
        # as delimiters
        self.parent_page = page
        self.anchor = anchor
        self.bbox = bbox
        self.x0, self.y0, self.x1, self.y1 = self.bbox
        self.content = None

    def get_visit_info(self):
        """Given visit header bounding box, extract all visit information
        from the page."""
        return None


class Page:
    def __init__(self, parent, page, page_id):
        """Near top-level object holding references to all the child elements of a page within
        the PDF document of interest.

        Note that several elements must be tracked across pages, as such this is for convenience
        of extracting the information, not for assembly of all related bits of information.
        """
        self.protocol_version = None
        self.protocol_name = None
        self.parent = parent
        self.logger = self.parent.logger
        self.page_id = page_id
        self.page_number = None
        self.banner = None
        self.page = page
        self.bbox = page.bbox
        self.export_date = None
        self.export_user = None

        self.logger.debug(f'Page {self.page_id + 1:02d} {str(self.bbox)}')

        self.patient_id = None

        # We are focusing on visible geometry, as it is hard to use the invisible rest to debug things.
        # There is a lot of information and structure within the pool of white/invisible geometry, and
        # might be worth to look into in the future if the visible geometry does not suffice

        # Geometry
        # - Raw Lines -
        self._lines = [el for el in page if isinstance(el, pdflt.LTLine)]  # all lines, even invisible ones
        self.lines = [l for l in self._lines if is_visible(l.stroking_color)]  # all visible lines

        # - Rectangles and pseudo-lines -
        self._rectangles = [el for el in page if isinstance(el, pdflt.LTRect)]  # all rectangles

        # some boxes end up with a pattern, instead of a color. Let's kick them out...
        try:
            self.rectangles = [r for r in self._rectangles if
                               color_float(r.non_stroking_color) < 1]  # visible rectangles, i.e. non-white
        except TypeError:
            self.logger.error([type(r.non_stroking_color) for r in self._rectangles])
            raise

        for rect in self.rectangles:
            if is_actually_line(rect):
                self.lines.append(as_line(rect))
                self.rectangles.remove(rect)

        # - Processed Geometry -
        # print('Angles', [line_angle_rad(l)*2 for l in self.lines])
        self.v_lines = sorted([ln for ln in self.lines if line_angle_rad(ln) * 2 > 0.99],
                              key=lambda vl: vl.y1)  # all vertical lines sorted from top
        self.h_lines = sorted([ln for ln in self.lines if line_angle_rad(ln) * 2 < 0.01],
                              key=lambda hl: hl.x0)  # all horizontal lines sorted from left

        # Merge visible lines
        # logging.info(f'PRE-MERGE: Vertical: {len(self.v_lines)}, horizontal: {len(self.h_lines)}')
        # self.v_lines = merge_lines(self.v_lines)
        # self.h_lines = merge_lines(self.h_lines)
        # logging.info(f'MERGED: Vertical: {len(self.v_lines)}, horizontal: {len(self.h_lines)}')

        # Text
        self.textboxes = [pe for pe in self.page if isinstance(pe, pdflt.LTTextBox)]  # all text boxes, unsorted
        self.textlines = sorted([tl for tl in self.textboxes], key=lambda tl: tl.y1)  # all text lines sorted from top

        self.header = self.get_header()
        self.footer = self.get_footer()

        # Structures
        self.visits = self.get_visits()
        self.records = self.get_records()

    def get_header(self):
        for line in [ln for ln in self.textlines if ln.y0 > 730]:
            patient_pattern = 'Pat\. Nr\.:\s+(\d+)'
            patient_re = re.search(patient_pattern, line.get_text().strip('\n'))
            if patient_re:
                self.patient_id = patient_re.group(1)
                # hashlib.sha1(patient_re.group(1).encode('utf-8')).hexdigest()[:8]
                break

        if self.page_id == 0:
            # this is the first page in the PDF and has additional header information
            protocol_pattern = r'Basierend auf Protokoll \(Version (\d*)\)'
            protokoll_version = None
            protokoll_name = None
            protokoll_version_line = None
            for line in [ln for ln in self.textlines if 680 < ln.y0 < 730]:
                # print(line.get_text().split('\n'))
                version_line, name_line, _ = line.get_text().split('\n')
                re_result = re.search(protocol_pattern, version_line)
                if re_result:
                    protokoll_name = name_line.strip()
                    protokoll_version = re_result.group(1)
                    protokoll_version_line = line
                    break
            self.logger.debug(f'Protokoll "{protokoll_name}" v.{protokoll_version}')

            # if protokoll_version_line:
            #     for line in [ln for ln in self.textlines if ln.y1 < protokoll_version_line.y1 and ln.y0 > protokoll_version_line.y1 - 20]:
            #         print(line)
            # else:
            #     print('No anchoring Protokoll element')
            self.protocol_name = protokoll_name
            self.protocol_version = protokoll_version

    def get_banner(self):
        pass

    def get_footer(self):
        # get text lines
        footer_tboxes = [tl for tl in self.textlines if tl.y1 < 65]
        print_info = [tb for tb in footer_tboxes if tb.x0 < self.bbox[2] / 2][0].get_text().strip('\n')
        page_text = [tb for tb in footer_tboxes if tb.x0 > self.bbox[2] / 2][0].get_text().strip('\n')

        page_pattern = 'Seite\s(\d+)\/\d+'
        page_re = re.search(page_pattern, page_text)
        assert page_re, "Page information not found!"
        num_str = page_re.group(1)
        self.page_number = int(num_str) if num_str.isnumeric() else num_str
        if not (num_str.isnumeric()):
            self.logger.warning(f'Non-numeric page number on {num_str}')

        # Information on when and who exported the sheet from the catalog
        print_pattern = 'Gedruckt am:\s(\d+.\d+.\d\d\d\d)\s\d+:\d+:\d+ von (\w*)'
        print_re = re.search(print_pattern, print_info)
        date = print_re.group(1)
        self.export_date = datetime.strptime(date, '%d.%m.%Y').strftime('%Y-%m-%d')
        self.export_user = print_re.group(2)

        return [print_info, page_text]

    # def get_markers(self)
    # the straight-up better way would be to use ANY rectangle with the two fill colors, then filter if they are
    # either a visit marker, or a record marker, then iterate over them, branching into objects based on that
    # criterion. This way we automatically get proper sequence of elements as they are mutually exlusive but
    # each potentially delimits the other.
    # This will work best if we first merge the dual-markers into one bigger marker?
    # for rect in rectangles:
    #     if rect is record_marker:
    #        records.append()
    #     elif rect is visit_marker:
    #        visits.append()

    def get_visits(self):
        visit_markers = sorted(
            [r for r in self.rectangles if 0.8 < color_float(r.non_stroking_color) < 0.85 and abs(r.x1 - r.x0) > 500],
            key=lambda r: r.y0)
        visits = []
        for visit_tag in list(zip(visit_markers[::2], visit_markers[1::2])):
            bbox = Box(visit_tag[0].x0, visit_tag[0].y0, visit_tag[1].x1, visit_tag[1].y1)
            visit = Visit(anchor=visit_tag, bbox=bbox, page=self)
            visits.append(visit)
        return visits

    def get_records(self):
        """Find all pairs of record markers on the page and the elements within the box
        spanned by the markers to the next marker set, page end, or visit header start.

        Records are indicated by two black rectangles that appear as one, located at the left
        of a record header field. Markers are about 14 x 12 pixels and have black fill color.

        The original idea to use the bounding lines fails due to some lines not being found, but rather
        embedded as stroked rectangles in inconsistent manners.
            `rec_line = find_with_vertex_at(corner, self.v_lines) ## DOES NOT WORK!!`
        """
        rec_markers = [r for r in self.rectangles if
                       color_float(r.non_stroking_color) == 0 and abs(r.x1 - r.x0) > 11 and abs(r.y1 - r.y0) > 11]
        rec_markers = sorted(rec_markers, key=lambda r: r.y0, reverse=True)
        rec_tags = list(zip(rec_markers[::2], rec_markers[1::2]))

        record_boxes = []
        for nr, rec_tag in enumerate(rec_tags):
            start_corner = (rec_tag[0].x0, rec_tag[0].y1)
            # either the next tag, or bottom of the page!
            if nr + 1 == len(rec_tags):
                end_corner = (RECORD_WIDTH, PRE_PAGE_END_DIST)  # end of page
            else:
                end_corner = (RECORD_WIDTH, rec_tags[nr + 1][1].y1 + PRE_RECORD_DIST)  # next record marker

            # check if there's a visit marker before either of them and use that instead!
            following_visit = [v for v in self.visits if end_corner[1] < v.y1 < start_corner[1]]
            #             assert len(following_visit) in [0, 1]
            #             if len(following_visit) > 1:
            #                 logging.warn('Multiple visit headers found without block!')
            if len(following_visit):
                fw = following_visit[0]
                end_corner = (fw.x1, fw.y1 + PRE_VISIT_DIST)

            record = Record(anchor=rec_tag, bbox=Box(start_corner[0], start_corner[1], end_corner[0], end_corner[1]),
                            visit=None, page=self)
            record_boxes.append(record)
        return record_boxes

    def __str__(self):
        return f"PDFPage(page={self.page_number}, bbox={self.bbox})"

    def __repr__(self):
        return f"PDFPage(page={self.page_number}, bbox={self.bbox})"


class Record:
    def __init__(self, anchor, bbox, visit, page):
        """Collection of entries within a visit.
        """
        # TODO: Bounding box vertices not specified in same order as other elements!!!
        self.parent_page = page
        self.logger = self.parent_page.logger
        self.anchor = anchor  # sorted(anchor, key=lambda a: a.y1, reverse=True)
        assert self.anchor[0].y0 > self.anchor[1].y0, "Order of elements not as expected!"
        self.bbox = bbox
        self.x0, self.y0, self.x1, self.y1 = self.bbox
        self.visit = visit

        self._textboxes = [tb for tb in self.parent_page.textboxes if
                           tb.y1 < max(self.y0, self.y1) and tb.y0 > min(self.y0, self.y1)]

        self.entries = self.get_entries()
        self.data = self.get_data()

    def get_data(self):
        protokoll = [tl for tb in self._textboxes for tl in tb if
                     self.anchor[0].y0 < (tl.y1 + tl.y0) / 2 < self.anchor[0].y1]
        assert len(protokoll) == 1, "Record header 'Protokoll' extraction failed!"

        # Newer protocols don't include the protocol name in header!
        cycle_pattern = r'Zyklus:.*Zyklus (\d*)'
        zyklus = None
        for line in [ln for ln in protokoll]:
            re_result = re.search(cycle_pattern, line.get_text().strip('\n'))
            if re_result:
                zyklus = re_result.group(1)
                break

        header_verordnung = [tl for tb in self._textboxes for tl in tb if
                             self.anchor[1].y0 < (tl.y1 + tl.y0) / 2 < self.anchor[1].y1]
        header_verordnung = sorted(header_verordnung, key=lambda tl: tl.x0)
        if len(header_verordnung) != 3:
            self.logger.debug(f'Header length not 3: {header_verordnung} '
                              f'in {self.parent_page.parent.path.name} on p.{self.parent_page.page_number} ')

        d_fmt = '%a, %d. %b %Y'
        date = datetime.strptime(header_verordnung[0].get_text().strip('\n'), d_fmt)

        day_field = header_verordnung[1].get_text().strip('\n')
        day_pattern = r'Tag (\d*) - Tag (\d*) der'
        re_result = re.search(day_pattern, day_field)

        day_in_cycle = ''
        day_in_protocol = ''
        if re_result:
            day_in_cycle = re_result.group(1)
            day_in_protocol = re_result.group(2)

        locate_string = header_verordnung[2].get_text().strip('\n')
        place = locate_string.split(' |')[0] if '|' in locate_string else 'unknown'
        record_data = {'date_obj': date,
                       'zyklus': zyklus,
                       'date': header_verordnung[0].get_text().strip('\n'),
                       'day_cycle': day_in_cycle,
                       'day_protocol': day_in_protocol,
                       'locale': place,
                       'pageID': self.parent_page.page_id,
                       'pageNumber': self.parent_page.page_number}
        return record_data

    def get_entries(self):
        """Given the record bounding box, extract all entries, starting from the bottom of the
        record marker, and verified by the timestamp."""

        entry_markers = []

        med_nr_pattern = 'Med\. Nr\.:\s+(\d+)'
        med_items = [tl for tb in self._textboxes for tl in tb if re.search(med_nr_pattern, tl.get_text().strip('\n'))]

        timestamp_pattern = '\d{1,2}:\d{2}\s-\s\d{2}:\d{2}'
        time_pattern = '\d{1,2}:\d{2}'

        for med_item in med_items:
            ts_lines = [tl for tb in self._textboxes for tl in tb if (tl.y0 - med_item.y0) < 10]
            timestamp = None
            for tl in ts_lines:
                if re.search(timestamp_pattern, tl.get_text()) or re.search(time_pattern, tl.get_text()):
                    timestamp = tl
                    break

            # Rarely, an entry won't have a timestamp or anything other than a short comment and a Med.Nr.
            # Unsure what this indicates, but we'll ignore those
            if timestamp is None:
                self.logger.error(f"No timestamp found on page {self.parent_page.page_number} for {med_item}")
                continue
            entry_markers.append((timestamp, med_item))

        entries = []
        for np, pair in enumerate(entry_markers):
            # get entry bounding box
            y1 = max([pair[0].y0, pair[0].y1, pair[1].y0, pair[1].y1]) + 3
            if np + 1 < len(entry_markers):
                y0 = max([entry_markers[np + 1][0].y0, entry_markers[np + 1][0].y1, entry_markers[np + 1][1].y0,
                          entry_markers[np + 1][1].y1]) + 13
                bbox = Box(self.x0, y0, self.x1, y1)
            else:
                y0 = min(self.y0, self.y1)
                bbox = Box(self.x0, y0, self.x1, y1)
            entry = Entry(anchor=pair, bbox=bbox, page=self.parent_page)
            if entry is not None:
                entries.append(entry)

        return entries


class PDF:
    def __init__(self, path, logger=None, debug=False):
        """Top level document representation.
        Will be used to accumulate extracted page-wise information used then to
        assemble to full set of data, especially sets of records spread over
        multiple pages.
        """
        self.path = path
        self.logger = logger if logger is not None else create_logger()
        self.logger.setLevel(logging.DEBUG if DEBUG or debug else logging.INFO)
        self.logger.debug(self.path)
        __page_generator = pdfhl.extract_pages(str(self.path))
        self.pages = [Page(self, page, np) for np, page in enumerate(__page_generator)]

        # iterate over visits, records and assemble into a coherent data set
        # visits -> records -> entries -> row

    def to_dict(self):
        """
        Assemble the extracted information into a dictionary of entries
        Returns
        -------
        """
        last_date = None
        document = []
        protocol_name = ''
        protocol_version = ''

        for page in self.pages:
            if page.page_id == 0:
                protocol_version = page.protocol_version
                protocol_name = page.protocol_name
            patient = page.patient_id
            for record in page.records:
                # keep date of the last record header in mind and apply to all current entries
                if last_date != record.data["date"]:
                    last_date = record.data["date"]

                for entry in record.entries:
                    dt_start = datetime.strptime(last_date + ' ' + entry.data['start'], '%a, %d. %b %Y %H:%M')
                    dt_end = datetime.strptime(last_date + ' ' + entry.data['end'], '%a, %d. %b %Y %H:%M')

                    duration = dt_end - dt_start
                    exclusion = '' if entry.data['exclusion'] is None else entry.data['exclusion']
                    item = {'patientID': patient,
                            'medNr': entry.data['mednr'],
                            'protocol': protocol_name,
                            'protocolVersion': protocol_version,
                            'datum': last_date,
                            'timeStart': entry.data['start'],
                            'timeEnd': entry.data['end'],
                            'isoStart': dt_start.strftime('%Y-%m-%d %H:%M:%S'),
                            'isoEnd': dt_end.strftime('%Y-%m-%d %H:%M:%S'),
                            'duration': duration.total_seconds(),
                            'application': entry.data['application'],
                            'drug': entry.data['drug'],
                            'premed': entry.data['premed'],
                            'arztShort': entry.data['arzt'],
                            'apothekerShort': entry.data['apotheker'],
                            'verabreichtShort': entry.data['verabreicht'],
                            'zyklus': record.data['zyklus'],
                            'day_cycle': record.data['day_cycle'],
                            'day_protocol': record.data['day_protocol'],
                            'status': None,
                            'MedDesc': None,
                            'pageID': record.data['pageID'],
                            'pageNumber': record.data['pageNumber'],
                            'documentName': self.path.name,
                            'exportDate': page.export_date,
                            'exportUser': page.export_user,
                            'exclusion': exclusion,
                            }

                    if item['exportDate'] in item['isoStart']:
                        self.logger.error('Entry on day of export. May indicate incomplete treatment!')
                        item['exclusion'] = 'ExportDuringTreatment'

                    document.append(item)

        return document


def read(path):
    return f'Nothing Here at {path}'


def main(paths, logger=None):
    logger = logger if logger is not None else create_logger()
    logger.info(f'Processing {len(paths)} files...')
    last_date = None
    verordnungen = dict()
    # per patient data

    pdfs = [PDF(file) for file in paths]

    for pdf in tqdm(pdfs):
        for page in pdf.pages:
            patient = page.patient_id
            verordnungen[patient] = dict()
            for record in page.records:
                if last_date != record.data["date"]:
                    last_date = record.data["date"]
                for entry in record.entries:
                    data = entry.data
                    verordnungen[patient][data['mednr']] = {'patient': patient, 'protocol': record.data['protocol'],
                                                            'date': last_date, 'start': data['start'],
                                                            'end': data['end'],
                                                            'application': data['application'], 'drug': data['drug'],
                                                            }
    return verordnungen


def resolve_paths(paths):
    source_paths = []
    for path in paths:
        # TODO: Allow wildcards in path names, too
        if '*' in str(path.parent) or '?' in str(path.parent):
            raise NotImplementedError('Wildcards are only supported in filenames.')
        if '*' in path.name or '?' in path.name:
            source_paths.extend(list(path.parent.glob(path.name)))
        else:
            if path.is_dir():
                pass
            elif path.is_file():
                pass
            else:
                logger.error(f'Invalid target path: {path}. Exiting.')
                sys.exit(65)
        source_paths.append(path)

        # check that all targets exist
    for sp in source_paths:
        if not sp.exists():
            logger.error(f'Invalid target path: {sp}. Exiting.')
            sys.exit(65)

    # process targets one by one
    resolved_files = []
    for sp in source_paths:
        if sp.is_dir():
            logger.debug(f'{sp} is a directory, will unpack.')
            resolved_files.extend(list(sp.glob('*.[pP][dD][fF]')))
        elif sp.is_file() and sp.suffix.lower() == '.pdf':
            logger.debug(f'{sp} is file.')
            resolved_files.append(sp)
    return resolved_files


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Cato Reader')
    parser.add_argument('paths', nargs="*", help='List of paths to cato PDF files. If the _filename_ contains'
                                                 ' a wildcard character, it will be used as a glob instead.')
    parser.add_argument('-d', '--debug', action='store_true', help='Debug mode')
    # parser.add_argument('-c', '--config', help='Configuration file')
    parser.add_argument('-o', '--output', help='Output location. No output is written if not specified.')

    cli_args = parser.parse_args()

    DEBUG = cli_args.debug

    log_level = logging.DEBUG if DEBUG else logging.INFO
    logger = create_logger(log_level)
    if not len(cli_args.paths):
        logger.error('No valid paths given.')
        sys.exit(66)

    pdf_files = resolve_paths(map(Path, cli_args.paths))
    logger.info(f'Found {len(pdf_files)} files')

    data = main(pdf_files)

    # Todo: prevent overwrite
    isodatetime = datetime.now().strftime("%Y%m%d-%H%M%S")
    outpath = '../../data/interim/extract{}.json'.format('' if DEBUG else '_' + isodatetime)
    logger.debug(f'Output stored in {outpath}')
    with open(outpath, 'w') as jsonfile:
        json.dump(data, jsonfile, indent=4)
