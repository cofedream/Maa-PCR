"""Read-only arithmetic over an existing inspection; never execution permission."""


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def evaluate_daily_goal(result, target=None, *, count_context=None):
    """Target applies to each selected row of this explicit preset inspection.

    same-day-unreset is a caller assertion about the reading's counter period,
    not a freshness/reset fact inferred from an image or receipt timestamp.
    No inputs are modified. Partial arithmetic does not certify the preset.
    """
    reasons = []
    target_valid = _nonnegative_int(target)
    if not target_valid:
        reasons.append('target_unknown' if target is None else 'invalid_target')
    context_valid = count_context in ('same-day-unreset', 'user-reported-unreset', 'gui-current-state')
    if not context_valid:
        reasons.append('count_context_unconfirmed')
    if result.get('native_ok') is not True:
        reasons.append('scan_failed')
    if result.get('list_complete') is not True or result.get('incomplete_reason'):
        reasons.append('list_incomplete')
    if result.get('view') != 'checked':
        reasons.append('checked_view_unverified')
    slot = result.get('requested_slot')
    slot_valid = type(slot) is int and 1 <= slot <= 7
    selected = result.get('selected_slot')
    if not slot_valid or type(selected) is not int or selected != slot:
        reasons.append('selected_slot_unverified')
    observation = result.get('observation') or {}
    final = result.get('ocr_receipt') or {}
    if (not isinstance(observation, dict) or not isinstance(final, dict)
            or final.get('native_ok') is not True
            or observation.get('page') != 'presets'
            or not slot_valid or type(observation.get('selected_slot')) is not int
            or observation.get('selected_slot') != slot):
        reasons.append('final_slot_unverified')
    raw_rows = result.get('rows')
    if not isinstance(raw_rows, list):
        reasons.append('invalid_rows')
        raw_rows = []
    if not raw_rows:
        reasons.append('empty_or_unverified_list')

    rows = []
    for index, raw in enumerate(raw_rows):
        row_reasons = []
        if not isinstance(raw, dict):
            raw = {}
            row_reasons.append('invalid_row')
        limit, remaining = raw.get('limit'), raw.get('remaining')
        counts_valid = (_nonnegative_int(limit) and _nonnegative_int(remaining)
                        and remaining <= limit)
        if not counts_valid:
            row_reasons.append('invalid_counts')
        if not target_valid:
            row_reasons.append('target_unknown' if target is None else 'invalid_target')
        if not context_valid:
            row_reasons.append('count_context_unconfirmed')
        if raw.get('checked') is not True:
            row_reasons.append('row_not_checked')
        # Preserve order and the reader's composite identity, never group by name.
        identity = {k: raw.get(k) for k in ('title', 'category', 'difficulty')}
        used = limit - remaining if counts_valid and context_valid else None
        needed = max(0, target - used) if target_valid and used is not None else None
        rows.append(dict(index=index, identity=identity, target=target,
                         limit=limit, remaining=remaining, used=used, needed=needed,
                         available_now=remaining if counts_valid else None,
                         unavailable_gap=max(0, needed - remaining) if needed is not None else None,
                         goal_met=needed == 0 if not row_reasons else None,
                         unknown_reasons=row_reasons))
    if any(row['unknown_reasons'] for row in rows):
        reasons.append('row_evaluation_unknown')
    return dict(preset_goal_met=all(row['goal_met'] is True for row in rows) if not reasons else None,
                target_per_selected_stage=target, target_source='explicit_input' if target is not None else 'not_provided',
                count_context=count_context, count_context_source=('current-game-observation' if count_context == 'gui-current-state' else 'user-reported' if count_context == 'user-reported-unreset' else 'caller_assertion') if context_valid else 'unconfirmed',
                reading_source=result.get('source'), requested_slot=slot,
                assumption=('current displayed counter progress only; no assertion of no reset or game refresh'
                            if count_context == 'gui-current-state' else
                            'limit - remaining represents usage in the asserted same day without counter reset'),
                read_only=True, rows=rows, unknown_reasons=reasons,
                incomplete_reason=result.get('incomplete_reason'))
