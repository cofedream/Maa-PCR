"""Reject known obstructions on a text-verified home frame."""
import math
import traceback

from maa.custom_recognition import CustomRecognition
from maa.define import AlgorithmEnum


class HomeNoKnownLargePanel(CustomRecognition):
    def analyze(self, context, argv):
        try:
            if argv.image.shape != (720, 1280, 3) or argv.image.dtype.name != 'uint8':
                return None
            # Reuse the And recognition's frame, never capture or navigate here.
            # A white home background is not a dialog; require its native title too.
            panel = context.run_recognition('HomeKnownLargePanel', argv.image)
            if panel is None or panel.algorithm != AlgorithmEnum.And or panel.hit:
                return None
            detail = panel.raw_detail
            if not isinstance(detail, list):
                return None
            # Either fixed notice tab vetoes HOME even while its pale panel is translucent.
            notice = context.run_recognition('HomeNoticeTabs', argv.image)
            if (notice is None or notice.algorithm != AlgorithmEnum.Or or notice.hit is not False
                    or not isinstance(notice.raw_detail, list)):
                return None
            tabs = notice.raw_detail
            if (len(tabs) != 2 or any(not isinstance(row, dict) for row in tabs)
                    or [row.get('name') for row in tabs] != ['HomeNoticeUpdatesText', 'HomeNoticeCalendarText']
                    or any(row.get('algorithm') != 'OCR' or row.get('box', False) is not None for row in tabs)):
                return None
            busy = context.run_recognition('HomeDataConnection', argv.image)
            if busy is None or busy.algorithm != AlgorithmEnum.OCR or busy.hit is not False:
                return None
            connection = busy.raw_detail
            # only_rec produces one native ROI result even for a blank crop.
            # A valid miss differs from absent/malformed recognition evidence.
            if (not isinstance(connection, dict) or not isinstance(connection.get('all'), list)
                    or len(connection['all']) != 1 or connection.get('filtered') != []
                    or connection.get('best', False) is not None):
                return None
            word = connection['all'][0]
            if (not isinstance(word, dict) or word.get('box') != [980, 15, 285, 65]
                    or not isinstance(word.get('text'), str)
                    or type(word.get('score')) not in (int, float)
                    or not math.isfinite(word['score']) or not 0 <= word['score'] <= 1):
                return None
            return self.AnalyzeResult([350, 220, 580, 280], {
                'scope': 'no known large pale panel, notice tabs or data-connection text',
                'native_reco_id': panel.reco_id, 'native': detail,
                'notice_reco_id': notice.reco_id, 'notice': notice.raw_detail,
                'connection_reco_id': busy.reco_id, 'connection': connection,
            })
        except Exception:
            traceback.print_exc()
            # No exception may escape the SDK ctypes callback; uncertainty is a miss.
            return None
