"""Daily row inventory and normal sequence, using only the guarded caller send."""


def scan_rows(page, send, sent, *, expected=None):
    from .schedule_onekey import ITEMS

    catalog, count = {}, None
    anchored = False
    for _ in range(12):
        if page['state'] != 'schedule':
            raise ValueError('list_replaced_by_unknown_page')
        if 'delegated' in page['sections']:
            candidate = page['sections']['delegated']
            if candidate['total'] != 9 or (count is not None and count != candidate):
                raise ValueError('delegated_scope_or_count_changed')
            count, anchored = candidate, True
        if anchored:
            for key, row in sorted(page['rows'].items(), key=lambda pair: pair[1]['y']):
                state = row['state']
                if key in catalog and catalog[key] != state:
                    raise ValueError('row_changed_during_scan')
                catalog[key] = state
            if page['bottom']:
                if list(catalog) != list(ITEMS):
                    raise ValueError('nine_row_inventory_incomplete_or_changed')
                if sum(s == 'complete' for s in catalog.values()) != count['completed']:
                    raise ValueError('row_marks_counter_disagree')
                if expected is not None and catalog != expected:
                    raise ValueError('nine_row_settlement_not_complete')
                return catalog, page
        direction = 'down' if anchored or 'notifications' in page['sections'] else 'up'
        index = sum(s.startswith(('down:', 'up:')) for s in sent)
        if index >= 24:
            raise ValueError('scroll_budget_exhausted')
        page = send(direction + ':' + str(index), page)
    raise ValueError('inventory_scan_budget_exhausted')


def execute_pending(page, pending, send, report):
    from .schedule_onekey import post_consumer_targets

    page = send('onekey', page, targets=post_consumer_targets(pending))
    for item, stage in (('dungeon_skip', 'dungeon'), ('mana_skip', 'mana'), ('exp_skip', 'exp')):
        if item not in pending:
            continue
        if page['state'] != stage + '_confirm':
            raise ValueError('expected_specific_confirmation:' + stage)
        report.setdefault('quotes', {})[stage] = page.get('quote')
        page = send(stage + '_confirm', page, targets=(
            ('dungeon_finish', 'sweep_result') if stage == 'dungeon' else ('sweep_result', 'sweep_animation')))
        if stage != 'dungeon' and page['state'] == 'sweep_animation':
            page = send(stage + '_skip_finish', page, targets=('sweep_result',))
        if stage == 'dungeon' and page['state'] == 'dungeon_finish':
            # The per-floor reward page is not the final sweep summary.
            # Close it once, then observe the existing summary/next page.
            next_targets = post_consumer_targets(pending, after=stage)
            page = send('dungeon_finish', page, targets=('sweep_result',) + next_targets)
            if page['state'] in next_targets:
                continue
        if page['state'] != 'sweep_result':
            raise ValueError('settlement_missing_no_resend:' + stage)
        page = send(stage + '_result', page, targets=post_consumer_targets(pending, after=stage))
    if 'guild_like' in pending:
        if page['state'] != 'like_result':
            raise ValueError('expected_like_result')
        report['like_ap'] = page['quote']
        page = send('like_result', page, targets=('item_result', 'schedule'))
    # Optional only once: some or all reward rows may already be done.
    if page['state'] == 'item_result':
        page = send('item_result', page, targets=('schedule',))
    return page
