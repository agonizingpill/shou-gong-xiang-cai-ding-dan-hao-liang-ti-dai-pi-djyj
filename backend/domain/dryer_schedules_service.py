from uuid import uuid4

from sqlalchemy import select

from core.records import get, need, scan, update
from domain.dryer_schedules_model import (
    dryer_racks, dryer_schedule_events, dryer_schedules, dryer_starts,
)
from domain.dryer_schedules_planner import (
    RACKS, ScheduleError, first_key, normalize_capacities, optimize_assignments,
    optimize_schedule, plan_schedule, rack_key, reschedule_orders,
    schedule_orders,
)
from domain.loss_sheets_model import loss_sheets
from domain.powder_lots_model import powder_lots
from domain.scent_orders_model import scent_orders


RACK_STATUS = {True: '运行中', False: '已停用'}


def as_payload(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, 'model_dump'):
        return value.model_dump()
    return dict(value)


def adopt_call_args(user, data, payload=None):
    """Support both route style (conn, user, data) and direct (conn, data)."""
    if payload is not None and data is None:
        data = payload
    if data is None:
        if isinstance(user, dict) or hasattr(user, 'model_dump'):
            data = user
            user = None
        else:
            data = {}
    return user or {'sub': 'local-rule', 'role': 'reviewer'}, as_payload(data)


def _plan_error(exc: ScheduleError):
    need(False, str(exc))


def ensure_rack_rows(conn):
    rows = [{'code': code, 'active': True, 'capacity_g': None} for code in RACKS]
    dialect = conn.dialect.name
    if dialect == 'mysql':
        from sqlalchemy.dialects.mysql import insert as dialect_insert
        stmt = dialect_insert(dryer_racks).values(rows)
        stmt = stmt.on_duplicate_key_update(code=dryer_racks.c.code)
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
        stmt = dialect_insert(dryer_racks).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=['code'])
    elif dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
        stmt = dialect_insert(dryer_racks).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=['code'])
    else:
        stmt = dryer_racks.insert().values(rows)
    conn.execute(stmt)


def lock_racks(conn, racks=RACKS):
    ensure_rack_rows(conn)
    result = {}
    for code in sorted(set(racks)):
        row = conn.execute(
            select(dryer_racks).where(dryer_racks.c.code == code).with_for_update()
        ).mappings().first()
        need(row is not None, f'未知干燥架 {code}')
        result[code] = dict(row)
    return result


def current_revision(conn, lock=False):
    stmt = select(dryer_schedule_events.c.id).order_by(
        dryer_schedule_events.c.id.desc()).limit(1)
    if lock:
        stmt = stmt.with_for_update()
    return int(conn.execute(stmt).scalar() or 0)


def add_event(conn, kind, order_ref=None, dryer=None):
    result = conn.execute(dryer_schedule_events.insert().values(
        kind=kind, order_ref=order_ref, dryer=dryer))
    return int(result.inserted_primary_key[0])


def list_racks(conn):
    ensure_rack_rows(conn)
    rows = scan(conn, dryer_racks)
    revision = current_revision(conn)
    for row in rows:
        row['status'] = RACK_STATUS.get(bool(row['active']), '未知')
        row['revision'] = revision
        row['version'] = revision
    return rows


def list_starts(conn):
    return scan(conn, dryer_starts)


def started_assignments(conn, lock=False):
    stmt = select(dryer_starts)
    if lock:
        stmt = stmt.with_for_update()
    return [dict(row) for row in conn.execute(stmt).mappings().all()]


def latest_sheet(conn, order_ref):
    return conn.execute(
        select(loss_sheets)
        .where(loss_sheets.c.order_ref == order_ref)
        .order_by(loss_sheets.c.code.desc())
    ).mappings().first()


def estimate_needs(conn, order, lot_ref=None, sheet_ref=None):
    sheet = None
    if sheet_ref:
        sheet = get(conn, loss_sheets, sheet_ref)
        need(sheet['order_ref'] == order['code'], '耗量凭证与订单不一致')
        lot_ref = sheet['primary_lot']
    if not lot_ref:
        sheet = latest_sheet(conn, order['code'])
        if sheet:
            sheet = dict(sheet)
            lot_ref = sheet['primary_lot']
    need(lot_ref, f"{order['code']} 尚未选定参考粉料批")
    lot = get(conn, powder_lots, lot_ref)

    from domain.incense_rules import compatible
    need(compatible(order, lot), f'{order["code"]} 的参考粉料批香型或标签不一致')

    from domain.learning import calculate_racks
    estimates = calculate_racks({
        'target': order['grams'], 'scent': order['scent'],
        'moisture': lot['moisture'], 'lot': lot['code'],
    })
    needs = {rack: int(estimates[rack]['need_g']) for rack in RACKS}
    evidence = {rack: {
        'loss_g': estimates[rack]['loss_g'],
        'need_g': estimates[rack]['need_g'],
        'input_hash': estimates[rack]['input_hash'],
    } for rack in RACKS}
    return needs, lot['code'], evidence


def _explicit_complete(raw):
    from domain.dryer_schedules_planner import normalize_order
    try:
        item = normalize_order(raw)
    except ScheduleError:
        return False
    return all(item['needs'][rack] is not None for rack in RACKS)


def hydrate_orders(conn, data):
    raw_orders = first_key(data, 'orders', 'items', 'candidates', 'order_items',
                           'scheduling_orders', 'schedule_orders')
    ref_list = first_key(data, 'order_refs', 'codes', 'order_codes', 'refs')
    reference_lots = first_key(data, 'reference_lots', 'lots_by_order', 'lot_by_order', default={}) or {}
    reference_sheets = first_key(data, 'reference_sheets', 'sheets_by_order', default={}) or {}
    global_lot = first_key(data, 'primary_lot', 'reference_lot', 'lot')
    ineligible = []

    if raw_orders is not None:
        need(isinstance(raw_orders, list), 'orders 必须是列表')
        prepared = []
        for index, raw in enumerate(raw_orders):
            if isinstance(raw, str):
                ref = raw
                raw_dict = {'order_ref': ref}
            elif isinstance(raw, dict):
                raw_dict = dict(raw)
                ref = first_key(raw_dict, 'order_ref', 'order_code', 'order', 'code', 'id', 'ref')
            else:
                continue

            if _explicit_complete(raw_dict):
                if ref:
                    order_row = conn.execute(select(scent_orders).where(
                        scent_orders.c.code == str(ref))).mappings().first()
                    if order_row is not None:
                        order_row = dict(order_row)
                        raw_dict = {
                            **order_row,
                            **{k: v for k, v in raw_dict.items() if v not in (None, '')},
                        }
                prepared.append(raw_dict)
                continue

            if not ref:
                ineligible.append({'order_ref': None, 'reason': '缺少订单编号'})
                continue
            order = conn.execute(select(scent_orders).where(
                scent_orders.c.code == str(ref))).mappings().first()
            if order is None:
                if _explicit_complete(raw_dict):
                    prepared.append(raw_dict)
                else:
                    ineligible.append({'order_ref': str(ref), 'reason': '找不到订单或模型克数'})
                continue
            order = dict(order)
            merged = {**order, **{k: v for k, v in raw_dict.items() if v not in (None, '')}}
            lot_ref = first_key(merged, 'primary_lot', 'reference_lot', 'lot_ref', 'lot')
            lot_ref = lot_ref or reference_lots.get(order['code']) or global_lot
            sheet_ref = first_key(merged, 'sheet', 'sheet_ref', 'loss_sheet')
            sheet_ref = sheet_ref or reference_sheets.get(order['code'])
            try:
                needs, lot_code, evidence = estimate_needs(
                    conn, order, lot_ref=lot_ref, sheet_ref=sheet_ref)
            except Exception as exc:
                # Business 409s and missing references make this order unschedulable,
                # but should not prevent the page from replanning other orders.
                ineligible.append({'order_ref': order['code'], 'reason': str(exc.detail if hasattr(exc, 'detail') else exc)})
                continue
            merged.update({
                'due_date': order.get('due_date'),
                'rack_a_wet_g': needs['rack-a'],
                'rack_b_wet_g': needs['rack-b'],
                'primary_lot': lot_code,
                'model_evidence': evidence,
            })
            prepared.append(merged)
        return prepared, ineligible

    if ref_list:
        need(isinstance(ref_list, list), 'order_refs 必须是列表')
        refs = [str(x) for x in ref_list]
    else:
        refs = [row['code'] for row in scan(conn, scent_orders)
                if row['state'] not in {'已冻结', '已核销'}]

    prepared = []
    for ref in refs:
        order = conn.execute(select(scent_orders).where(
            scent_orders.c.code == ref)).mappings().first()
        if order is None:
            ineligible.append({'order_ref': ref, 'reason': '找不到订单'})
            continue
        order = dict(order)
        if order['state'] in {'已冻结', '已核销'}:
            ineligible.append({'order_ref': ref, 'reason': f"订单状态为{order['state']}"})
            continue
        lot_ref = reference_lots.get(ref) if isinstance(reference_lots, dict) else None
        sheet_ref = reference_sheets.get(ref) if isinstance(reference_sheets, dict) else None
        try:
            needs, lot_code, evidence = estimate_needs(
                conn, order, lot_ref=lot_ref or global_lot, sheet_ref=sheet_ref)
        except Exception as exc:
            ineligible.append({'order_ref': ref, 'reason': str(exc.detail if hasattr(exc, 'detail') else exc)})
            continue
        prepared.append({
            'order_ref': order['code'], 'code': order['code'],
            'title': order['title'], 'due_date': order.get('due_date'),
            'dryer': order['dryer'], 'state': order['state'],
            'rack_a_wet_g': needs['rack-a'], 'rack_b_wet_g': needs['rack-b'],
            'primary_lot': lot_code, 'model_evidence': evidence,
        })
    return prepared, ineligible


def create_draft(conn, user=None, data=None, payload=None):
    user, data = adopt_call_args(user, data, payload)
    # Model inference can be slow. Build candidate estimates before entering the
    # short critical section; locking reads below then observe any starts that
    # committed during inference and assign the draft the current revision.
    orders, ineligible = hydrate_orders(conn, data)

    rack_rows = lock_racks(conn)
    revision = current_revision(conn, lock=True)
    db_started = started_assignments(conn, lock=True)

    blocked = [code for code, row in rack_rows.items() if not bool(row['active'])]
    requested_blocked = first_key(data, 'blocked_racks', 'stopped_racks',
                                  'unavailable_racks', 'disabled_racks', default=[])
    if requested_blocked:
        from domain.dryer_schedules_planner import normalize_rack_set
        blocked = sorted(set(blocked) | normalize_rack_set(requested_blocked))

    payload = dict(data)
    requested_fixed = first_key(
        data, 'fixed_assignments', 'started_assignments', 'locked_assignments',
        'locks', 'started', default=[])
    if not isinstance(requested_fixed, list):
        requested_fixed = []
    payload.update({
        'orders': orders,
        # Database starts are appended last and therefore win duplicate refs.
        'fixed_assignments': [*requested_fixed, *db_started],
        'blocked_racks': blocked,
        'revision': revision,
        'base_revision': revision,
    })
    # Stored rack capacities are only defaults; request capacities win.
    for rack in RACKS:
        key_a = f'capacity_{rack[-1]}_g'
        if payload.get(key_a) in (None, '') and rack_rows[rack]['capacity_g'] is not None:
            payload[key_a] = rack_rows[rack]['capacity_g']

    try:
        plan = plan_schedule(payload)
    except ScheduleError as exc:
        _plan_error(exc)
        raise

    code = first_key(data, 'code', 'schedule_code', 'draft_code') or f'DS-{uuid4().hex[:12].upper()}'
    token = uuid4().hex
    plan.update({
        'code': code, 'schedule_code': code, 'draft_code': code,
        'token': token, 'draft_token': token,
        'status': '草案', 'revision': revision, 'version': revision,
        'base_revision': revision, 'ineligible_orders': ineligible,
    })

    values = {
        'code': code, 'token': token, 'status': '草案',
        'base_revision': revision,
        'capacity_a_g': plan['capacity_a_g'],
        'capacity_b_g': plan['capacity_b_g'],
        'snapshot': plan,
    }
    existing = conn.execute(select(dryer_schedules.c.code).where(
        dryer_schedules.c.code == code)).first()
    if existing:
        conn.execute(dryer_schedules.update().where(
            dryer_schedules.c.code == code).values(**values))
    else:
        conn.execute(dryer_schedules.insert().values(**values))
    return plan


def _schedule_row(conn, data, lock=False):
    code = first_key(data, 'code', 'schedule_code', 'draft_code', 'schedule')
    token = first_key(data, 'token', 'draft_token', 'schedule_token')
    query = select(dryer_schedules)
    if code:
        query = query.where(dryer_schedules.c.code == code)
    elif token:
        query = query.where(dryer_schedules.c.token == token)
    else:
        need(False, '请提供排班草案编号或令牌')
    if lock:
        query = query.with_for_update()
    row = conn.execute(query).mappings().first()
    need(row is not None, '找不到排班草案')
    return dict(row)


def flatten_schedule(row):
    snapshot = dict(row.get('snapshot') or {})
    result = {**snapshot, **{k: v for k, v in row.items() if k != 'snapshot'}}
    result['schedule_code'] = row['code']
    result['draft_code'] = row['code']
    result['draft_token'] = row['token']
    return result


def list_schedules(conn):
    return [flatten_schedule(row) for row in scan(conn, dryer_schedules)]


def get_schedule(conn, code):
    return flatten_schedule(_schedule_row(conn, {'code': code}))


def finalize_draft(conn, user=None, data=None, payload=None):
    user, data = adopt_call_args(user, data, payload)
    # Keep lock order identical to start/draft: rack rows first, then schedule.
    lock_racks(conn)
    row = _schedule_row(conn, data, lock=True)
    revision = current_revision(conn, lock=True)
    result = flatten_schedule(row)

    if row['status'] == '草案':
        need(row['base_revision'] == revision,
             '草案已因开工或停架变化而失效，请重算')
        conn.execute(dryer_schedules.update().where(
            dryer_schedules.c.code == row['code']).values(status='已定稿'))
        result['status'] = '已定稿'
        result['finalized'] = True
    elif row['status'] == '已定稿':
        result['idempotent'] = True
    else:
        need(False, '当前排班状态不能定稿')
    result['revision'] = revision
    result['version'] = revision
    return result


def _assignment_from_schedule(row, order_ref):
    plan = flatten_schedule(row)
    for item in plan.get('assignments', []):
        if item.get('order_ref') == order_ref:
            return item, plan
    return None, plan


def start_order(conn, user=None, data=None, payload=None):
    user, data = adopt_call_args(user, data, payload)
    order_ref = first_key(data, 'order_ref', 'order_code', 'order', 'code')
    need(order_ref, '请提供开工订单号')
    order_ref = str(order_ref)

    # One consistent lock order across start/stop/draft/finalize prevents races.
    rack_rows = lock_racks(conn)
    order = get(conn, scent_orders, order_ref, lock=True)

    existing = conn.execute(
        select(dryer_starts).where(dryer_starts.c.order_ref == order_ref).with_for_update()
    ).mappings().first()
    need(existing is None, '订单已开工，原架和克数已保留')

    schedule_row = None
    plan = None
    assignment = None
    schedule_ref = first_key(data, 'schedule_code', 'draft_code', 'schedule', 'code_schedule')
    token_ref = first_key(data, 'draft_token', 'schedule_token', 'token')
    if schedule_ref or token_ref or first_key(data, 'use_latest_schedule', default=False):
        lookup = dict(data)
        if schedule_ref:
            lookup['code'] = schedule_ref
        if token_ref:
            lookup['token'] = token_ref
        schedule_row = _schedule_row(conn, lookup, lock=True)
        assignment, plan = _assignment_from_schedule(schedule_row, order_ref)
        need(assignment is not None, '定稿排班中没有该订单')

        revision = current_revision(conn, lock=True)
        if schedule_row['status'] == '草案':
            need(schedule_row['base_revision'] == revision,
                 '草案已因另一单开工或停架而失效，请重算')
            conn.execute(dryer_schedules.update().where(
                dryer_schedules.c.code == schedule_row['code']).values(status='已定稿'))
            schedule_row['status'] = '已定稿'
        elif schedule_row['status'] != '已定稿':
            need(False, '排班草案状态不允许开工')

        dryer = assignment['dryer']
        wet_grams = int(assignment['wet_grams'])
        requested_dryer = rack_key(first_key(data, 'dryer', 'rack', 'assigned_rack'))
        requested_grams = first_key(data, 'wet_grams', 'need_g', 'grams', 'wet_material_g')
        if requested_dryer is not None:
            need(requested_dryer == dryer, '开工架位与定稿不一致，请照定稿备料')
        if requested_grams not in (None, ''):
            need(int(requested_grams) == wet_grams, '开工克数与定稿不一致，请照定稿备料')
    else:
        dryer = rack_key(first_key(data, 'dryer', 'rack', 'assigned_rack')) or rack_key(order['dryer'])
        need(dryer in RACKS, '请指定有效干燥架')
        wet_grams = first_key(data, 'wet_grams', 'wet_material_g', 'need_g',
                              'grams', 'planned_grams_g')
        if wet_grams in (None, ''):
            needs, _lot, _evidence = estimate_needs(conn, order)
            wet_grams = needs[dryer]
        wet_grams = int(wet_grams)
        need(wet_grams > 0, '开工湿料克数必须为正整数')

    rack = rack_rows[dryer]
    need(bool(rack['active']), f'{dryer} 已停用，不接新单；已开工单保留原记录')

    request_capacities = normalize_capacities(data)
    capacity = request_capacities[dryer]
    if capacity is None and plan:
        capacity = (plan.get('capacities') or {}).get(dryer) or plan.get(f'capacity_{dryer[-1]}_g')
    if capacity in (None, ''):
        capacity = rack['capacity_g']
    start_rows = conn.execute(
        select(dryer_starts).where(dryer_starts.c.dryer == dryer).with_for_update()
    ).mappings().all()
    used = sum(int(row['wet_grams']) for row in start_rows)
    if capacity not in (None, ''):
        need(int(used) + wet_grams <= int(capacity),
             f'{dryer} 当班用量将超过上限')

    revision = add_event(conn, 'started', order_ref, dryer)
    start_code = first_key(data, 'start_code', 'record_code') or f'DST-{uuid4().hex[:12].upper()}'
    due_date = order.get('due_date') or (assignment or {}).get('due_date')
    conn.execute(dryer_starts.insert().values(
        code=str(start_code), order_ref=order_ref,
        schedule_code=schedule_row['code'] if schedule_row else None,
        dryer=dryer, wet_grams=wet_grams,
        due_date=due_date, revision=revision, state='已开工'))
    update(conn, scent_orders, order_ref, dryer=dryer)

    row = get(conn, dryer_starts, str(start_code))
    preparation = {
        'order_ref': order_ref, 'dryer': dryer,
        'wet_material_g': wet_grams,
        'reference_lot': (assignment or {}).get('primary_lot'),
        'instruction': '照定稿架位与克数备料',
    }
    pick_list = []
    if preparation['reference_lot']:
        pick_list.append({'lot': preparation['reference_lot'], 'grams': wet_grams})
    row.update({
        'revision': revision, 'version': revision,
        'status': '已开工',
        'wet_material_g': wet_grams,
        'preparation': preparation,
        'material_preparation': preparation,
        'pick_list': pick_list,
    })
    return row


def set_rack_status(conn, user=None, data=None, active=None, payload=None):
    # Allow set_rack_status(conn, data, False) as well as route style.
    if active is None and isinstance(data, bool):
        active = data
        data = user
        user = None
    user, data = adopt_call_args(user, data, payload)
    if active is None:
        active = first_key(data, 'active', 'enabled', default=False)
    dryer = rack_key(first_key(data, 'dryer', 'rack', 'code', 'rack_code'))
    need(dryer in RACKS, '请指定有效干燥架')
    racks = lock_racks(conn)

    capacity = normalize_capacities(data)[dryer]
    if capacity is None:
        generic = first_key(data, 'capacity_g', 'limit_g')
        capacity = int(generic) if generic not in (None, '') else None
    if capacity is not None:
        conn.execute(dryer_racks.update().where(dryer_racks.c.code == dryer).values(
            capacity_g=int(capacity)))

    row = racks[dryer]
    if bool(row['active']) == active and capacity is None:
        revision = current_revision(conn, lock=True)
        return {'code': dryer, 'dryer': dryer, 'active': active,
                'status': RACK_STATUS[active], 'revision': revision,
                'version': revision, 'idempotent': True}

    conn.execute(dryer_racks.update().where(dryer_racks.c.code == dryer).values(active=active))
    revision = add_event(conn, 'resumed' if active else 'stopped', dryer=dryer)
    return {'code': dryer, 'dryer': dryer, 'active': active,
            'status': RACK_STATUS[active], 'revision': revision,
            'version': revision, 'idempotent': False}


# Descriptive aliases used by different page/test naming conventions.
def stop_dryer(conn, user=None, data=None, payload=None):
    return set_rack_status(conn, user, data, False, payload)


def deactivate_dryer(conn, user=None, data=None, payload=None):
    return stop_dryer(conn, user, data, payload)


def stop_rack(conn, user=None, data=None, payload=None):
    return stop_dryer(conn, user, data, payload)


def resume_dryer(conn, user=None, data=None, payload=None):
    return set_rack_status(conn, user, data, True, payload)


def reschedule(conn, user=None, data=None, payload=None):
    return create_draft(conn, user, data, payload)


def reschedule_dryers(conn, user=None, data=None, payload=None):
    return create_draft(conn, user, data, payload)


def schedule_dryers(conn, user=None, data=None, payload=None):
    return create_draft(conn, user, data, payload)


def plan_dryers(conn, user=None, data=None, payload=None):
    return create_draft(conn, user, data, payload)


def draft_schedule(conn, user=None, data=None, payload=None):
    return create_draft(conn, user, data, payload)


def create_schedule(conn, user=None, data=None, payload=None):
    return create_draft(conn, user, data, payload)


def start_work(conn, user=None, data=None, payload=None):
    return start_order(conn, user, data, payload)
