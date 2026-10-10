"""Run-local actor pixels and annotated foot anchor; never a handoff gate.

The caller supplies a verified source frame and human pixel geometry. This is
not automatic character identification. Templates are installed with official
Maa override_image, remain in memory, and are erased on close. No protocol,
route, expected logical position, game input or product image resource is used.
"""
from hashlib import sha256
import math
from threading import RLock

import numpy as np
from maa.custom_recognition import CustomRecognition
from maa.define import AlgorithmEnum


class WorldMarkerFailure(ValueError):
    pass


_MAP_ROI = (0, 80, 1280, 530)
_MINIMUM_NCC = 0.70  # A raw match threshold, never a confidence/probability.
_MAX_CANDIDATES = 64


def _box(value, *, small=False):
    if type(value) not in (tuple, list) or len(value) != 4 or any(type(x) is not int for x in value):
        raise WorldMarkerFailure('marker_geometry_invalid')
    x, y, w, h = value
    if x < 0 or y < 80 or w <= 0 or h <= 0 or x + w > 1280 or y + h > 610:
        raise WorldMarkerFailure('marker_geometry_invalid')
    if small and (w > 32 or h > 24):
        raise WorldMarkerFailure('marker_foot_annotation_invalid')
    return tuple(value)


def _image(value):
    if type(value) is not np.ndarray or value.dtype != np.uint8 or value.shape != (720, 1280, 3):
        raise WorldMarkerFailure('marker_frame_invalid')
    return value


def _inside(box, roi):
    return (box[0] >= roi[0] and box[1] >= roi[1]
            and box[0] + box[2] <= roi[0] + roi[2]
            and box[1] + box[3] <= roi[1] + roi[3])


class WorldMarkerTemplate:
    """Caller-annotated, one-owner template; only crops are retained in memory.

    foot_box is a small contact-point annotation in source-frame coordinates,
    not the actor body center. platform_crop is an independent gray front-face
    subregion below the feet; it must not overlap the actor recognition crop.
    Other actor appearances, scales and poses require separately checked source
    pixels. The API does not certify the caller's source-frame qualification.
    """
    def __init__(self, source_image, *, source_crop, foot_box, platform_crop, source_note):
        image = _image(source_image)
        self.source_crop = _box(source_crop)
        self.foot_box = _box(foot_box, small=True)
        self.platform_crop = _box(platform_crop)
        if type(source_note) is not str or not source_note.strip() or len(source_note) > 500:
            raise WorldMarkerFailure('marker_source_annotation_required')
        x, y, w, h = self.source_crop
        a, b, c, d = self.foot_box
        px, py, pw, ph = self.platform_crop
        if (not x <= a + c / 2 < x + w or b < y + h / 2 or b + d > y + h + 96
                or not px <= a + c / 2 < px + pw or not 0 <= py - (b + d) <= 32
                or py < y + h):
            raise WorldMarkerFailure('marker_source_geometry_unqualified')
        self.source_frame_sha256 = sha256(image.tobytes()).hexdigest()
        self.source_note = source_note
        self._actor = image[y:y+h, x:x+w].copy()
        self._platform = image[py:py+ph, px:px+pw].copy()
        self._actor.setflags(write=False)
        self._platform.setflags(write=False)
        self.actor_sha256 = sha256(self._actor.tobytes()).hexdigest()
        self.platform_sha256 = sha256(self._platform.tobytes()).hexdigest()
        self._lock = RLock()
        self._resource = None
        self._closed = False
        prefix = 'world_marker_' + self.actor_sha256[:16]
        self.actor_image_name = prefix + '_actor.png'
        self.platform_image_name = prefix + '_platform.png'

    def provenance(self):
        return dict(source='caller annotated game pixels; run-local memory only',
                    source_frame_pixel_sha256=self.source_frame_sha256,
                    source_crop=list(self.source_crop), foot_box=list(self.foot_box),
                    platform_crop=list(self.platform_crop), source_note=self.source_note,
                    actor_pixel_sha256=self.actor_sha256, platform_pixel_sha256=self.platform_sha256,
                    source_qualification='caller responsibility', automatic_role_identity=False)

    def close(self):
        """Call after stopping the owning Tasker; closed templates cannot reinstall."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            resource, self._resource = self._resource, None
            if resource is not None:
                # Erase game pixels in the official resource's in-memory image
                # map as well as our Python crops; no disk cache is created.
                for name in (self.actor_image_name, self.platform_image_name):
                    resource.override_image(name, np.zeros((1, 1, 3), dtype=np.uint8))
            self._actor = self._platform = None


def _matches(detail, size, roi):
    if detail is None or detail.algorithm != AlgorithmEnum.TemplateMatch:
        raise WorldMarkerFailure('marker_native_result_unknown')
    raw = detail.raw_detail
    if (type(detail.hit) is not bool or type(raw) is not dict
            or set(raw) != {'all', 'filtered', 'best'}
            or type(raw['all']) is not list or type(raw['filtered']) is not list
            or len(raw['all']) > _MAX_CANDIDATES
            or len(raw['filtered']) > _MAX_CANDIDATES):
        raise WorldMarkerFailure('marker_native_result_unknown')
    for row in raw['all']:
        if (type(row) is not dict or set(row) != {'box', 'score'}
                or type(row['score']) not in (int, float)
                or not -1 <= row['score'] <= 1 or not math.isfinite(row['score'])):
            raise WorldMarkerFailure('marker_native_result_unknown')
        box = _box(row['box'])
        if box[2:] != size or not _inside(box, roi):
            raise WorldMarkerFailure('marker_native_result_unknown')
    if (any(row not in raw['all'] or row['score'] < _MINIMUM_NCC for row in raw['filtered'])
            or detail.hit != bool(raw['filtered'])
            or (detail.hit and raw['best'] not in raw['filtered'])
            or (not detail.hit and raw['best'] is not None)):
        raise WorldMarkerFailure('marker_native_result_unknown')
    if len({tuple(row['box']) for row in raw['filtered']}) != len(raw['filtered']):
        raise WorldMarkerFailure('marker_native_result_unknown')
    return raw['filtered'], raw


def world_marker_pipeline(roi=_MAP_ROI):
    roi = _box(roi)
    return {'LabyWorldMarkerObserved': dict(
        recognition='Custom', custom_recognition='LabyWorldMarkerObserved',
        roi=list(roi), action='DoNothing', next=[], timeout=1000,
        pre_delay=0, post_delay=0)}


class MaaWorldMarkerRecognition(CustomRecognition):
    """Official same-frame map guard, actor match and gray-platform association.

    A hit proves a unique run-local pixel anchor supported by the annotated
    source geometry and independently matched platform subregion. It never
    proves logical current ID, fresh account/new run, full platform footprint,
    fresh foot pixels, generic character identity, navigation or handoff.
    """
    def __init__(self, template, roi=_MAP_ROI):
        super().__init__()
        if type(template) is not WorldMarkerTemplate:
            raise WorldMarkerFailure('marker_template_required')
        self.template = template
        self.roi = _box(roi)

    def install(self, resource):
        template = self.template
        with template._lock:
            if template._closed:
                raise WorldMarkerFailure('marker_template_closed')
            if template._resource is not None:
                raise WorldMarkerFailure('marker_template_already_installed')
            template._resource = resource
            try:
                if (not resource.override_image(template.actor_image_name, template._actor)
                        or not resource.override_image(template.platform_image_name, template._platform)):
                    raise WorldMarkerFailure('marker_template_install_failed')
                nodes = world_marker_pipeline(self.roi)
                for node, image_name in (
                        ('LabyWorldActorNative', template.actor_image_name),
                        ('LabyWorldPlatformNative', template.platform_image_name)):
                    nodes[node] = dict(recognition='TemplateMatch', template=image_name,
                                       threshold=_MINIMUM_NCC, method=5, roi=list(self.roi),
                                       action='DoNothing', next=[], pre_delay=0, post_delay=0)
                if (not resource.override_pipeline(nodes)
                        or not resource.register_custom_recognition('LabyWorldMarkerObserved', self)):
                    raise WorldMarkerFailure('marker_template_install_failed')
            except Exception:
                template.close()
                raise WorldMarkerFailure('marker_template_install_failed') from None
        return self

    def _report(self, image):
        return dict(scope='runtime_actor_pixel_anchor', source='maa_pixels',
                    frame_pixel_sha256=sha256(image.tobytes()).hexdigest(),
                    roi=list(self.roi), template=self.template.provenance(),
                    minimum_raw_ncc=_MINIMUM_NCC, qualification_pending=True,
                    complete=False, safe_to_navigate=False, handoff_qualified=False,
                    account_verified=False, new_opening_verified=False,
                    full_platform_verified=False, fresh_foot_pixels_verified=False)

    def analyze(self, context, argv):
        report = dict(status='anchor_unqualified', reason='marker_collection_unknown',
                      qualification_pending=True, complete=False,
                      safe_to_navigate=False, handoff_qualified=False)
        try:
            with self.template._lock:
                image = _image(argv.image)
                report = self._report(image)
                report.update(status='anchor_unqualified', anchor_unqualified=True)
                if self.template._closed or self.template._resource is None:
                    report['reason'] = 'marker_template_closed'
                    return self.AnalyzeResult(None, report)
                if tuple(argv.roi) != self.roi:
                    raise WorldMarkerFailure('marker_roi_mismatch')
                guard = context.run_recognition('LabyMapStableObserved', image)
                report['page_guard_reco_id'] = guard.reco_id if guard else None
                if guard is None or guard.algorithm != AlgorithmEnum.And or guard.hit is not True:
                    report['reason'] = 'map_page_unknown'
                    return self.AnalyzeResult(None, report)
                actor = context.run_recognition('LabyWorldActorNative', image)
                actors, raw_actor = _matches(actor, self.template.source_crop[2:], self.roi)
                report['actor_raw'] = raw_actor
                if len(actors) != 1:
                    report['reason'] = 'actor_missing' if not actors else 'actor_ambiguous'
                    return self.AnalyzeResult(None, report)
                actor_box = actors[0]['box']
                dx = actor_box[0] - self.template.source_crop[0]
                dy = actor_box[1] - self.template.source_crop[1]
                foot_box = _box([self.template.foot_box[0] + dx,
                                 self.template.foot_box[1] + dy,
                                 *self.template.foot_box[2:]], small=True)
                platform = context.run_recognition('LabyWorldPlatformNative', image)
                platforms, raw_platform = _matches(platform, self.template.platform_crop[2:], self.roi)
                report['platform_raw'] = raw_platform
                foot_x = foot_box[0] + foot_box[2] / 2
                supporting = [row for row in platforms
                              if row['box'][0] <= foot_x < row['box'][0] + row['box'][2]
                              and 0 <= row['box'][1] - (foot_box[1] + foot_box[3]) <= 32]
                report['actor_box'] = actor_box
                report['translated_foot_box'] = list(foot_box)
                if len(supporting) != 1:
                    report['reason'] = 'foot_platform_association_unknown'
                    return self.AnalyzeResult(None, report)
                report.update(status='pixel_anchor_observed', anchor_unqualified=False,
                              pixel_anchor=dict(box=list(foot_box),
                                  method='source foot annotation translated by native actor match',
                                  platform_subregion_box=supporting[0]['box'],
                                  actor_raw_ncc=actors[0]['score'],
                                  platform_raw_ncc=supporting[0]['score']),
                              provenance=dict(actor_location='same-frame Maa TemplateMatch',
                                  foot_offset='caller source-frame pixel annotation',
                                  platform_subregion='same-frame independent Maa TemplateMatch'))
                return self.AnalyzeResult(list(foot_box), report)
        except WorldMarkerFailure as failure:
            report.update(status='anchor_unqualified', anchor_unqualified=True, reason=str(failure))
        except Exception:
            report.update(status='anchor_unqualified', anchor_unqualified=True, reason='marker_collection_unknown')
        return self.AnalyzeResult(None, report)


def world_marker_readback(raw, *, hit):
    """Read the official Custom wrapper; a native hit is only a pixel anchor."""
    if (type(hit) is not bool or type(raw) is not dict or set(raw) != {'all', 'filtered', 'best'}
            or type(raw['all']) is not list or len(raw['all']) != 1):
        raise WorldMarkerFailure('marker_readback_unknown')
    row = raw['all'][0]
    if (type(row) is not dict or set(row) != {'box', 'detail'} or type(row['detail']) is not dict
            or raw['filtered'] != ([row] if hit else []) or raw['best'] != (row if hit else None)):
        raise WorldMarkerFailure('marker_readback_unknown')
    report = row['detail']
    if (report.get('complete') is not False or report.get('safe_to_navigate') is not False
            or report.get('handoff_qualified') is not False or report.get('qualification_pending') is not True):
        raise WorldMarkerFailure('marker_readback_unknown')
    if hit:
        if (report.get('status') != 'pixel_anchor_observed' or report.get('anchor_unqualified') is not False
                or row['box'] != report.get('pixel_anchor', {}).get('box')):
            raise WorldMarkerFailure('marker_readback_unknown')
        _box(row['box'], small=True)
    elif report.get('status') != 'anchor_unqualified' or row['box'] != [0, 0, 0, 0]:
        raise WorldMarkerFailure('marker_readback_unknown')
    return report
