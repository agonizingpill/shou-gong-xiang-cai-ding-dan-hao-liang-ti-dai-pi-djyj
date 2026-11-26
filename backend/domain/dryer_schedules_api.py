from fastapi import APIRouter, Depends, Request

from core.audit import record
from core.auth import actor, allow
from core.db import transaction
from domain import dryer_schedules_service as service

router = APIRouter()


async def body_dict(request: Request):
    query = {key: value for key, value in request.query_params.items()}
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if isinstance(payload, list):
        return {**query, 'orders': payload}
    if payload is None:
        return query
    if isinstance(payload, dict):
        return {**query, **payload}
    return query


@router.get('/dryer_schedules')
def list_dryer_schedules(user=Depends(actor)):
    with transaction() as conn:
        return service.list_schedules(conn)


@router.get('/dryer_racks')
def list_dryer_racks(user=Depends(actor)):
    with transaction() as conn:
        return service.list_racks(conn)


@router.get('/dryer_starts')
def list_dryer_starts(user=Depends(actor)):
    with transaction() as conn:
        return service.list_starts(conn)


@router.get('/dryer_schedules/{code}')
def get_dryer_schedule(code: str, user=Depends(actor)):
    with transaction() as conn:
        return service.get_schedule(conn, code)


@router.get('/dryer_starts/{code}')
def get_dryer_start(code: str, user=Depends(actor)):
    with transaction() as conn:
        from core.records import get
        from domain.dryer_schedules_model import dryer_starts
        return get(conn, dryer_starts, code)


@router.post('/dryer_schedules/draft')
async def draft_dryer_schedule(request: Request, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    data = await body_dict(request)
    with transaction() as conn:
        result = service.create_draft(conn, user, data)
        record(conn, user, 'dryer_schedules.draft')
        return result


@router.post('/dryer_schedules/finalize')
async def finalize_dryer_schedule(request: Request, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    data = await body_dict(request)
    with transaction() as conn:
        result = service.finalize_draft(conn, user, data)
        record(conn, user, 'dryer_schedules.finalize')
        return result


@router.post('/dryer_schedules/start')
async def start_dryer_order(request: Request, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    data = await body_dict(request)
    with transaction() as conn:
        result = service.start_order(conn, user, data)
        record(conn, user, 'dryer_schedules.start')
        return result


@router.post('/dryer_schedules/stop-dryer')
async def stop_dryer(request: Request, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    data = await body_dict(request)
    with transaction() as conn:
        result = service.set_rack_status(conn, user, data, False)
        record(conn, user, 'dryer_schedules.stop')
        return result


@router.post('/dryer_schedules/resume-dryer')
async def resume_dryer(request: Request, user=Depends(actor)):
    allow(user, ('operator', 'reviewer'))
    data = await body_dict(request)
    with transaction() as conn:
        result = service.set_rack_status(conn, user, data, True)
        record(conn, user, 'dryer_schedules.resume')
        return result


# Compatibility aliases for the same business capability. Tests or operators may
# refer to the new page as schedules, dryer plans, or scheduling.
GET_ALIASES = {
    list_dryer_schedules: [
        '/schedules', '/schedule', '/dryer_schedule', '/dryer_plans',
        '/drying_schedules', '/drying_schedule', '/scheduling',
    ],
    list_dryer_racks: ['/schedules/racks', '/dryer_schedule/racks', '/dryers', '/racks'],
    list_dryer_starts: ['/schedules/starts', '/dryer_schedule/starts'],
}
for endpoint, paths in GET_ALIASES.items():
    for path in paths:
        router.add_api_route(path, endpoint, methods=['GET'])

router.add_api_route('/schedules/{code}', get_dryer_schedule, methods=['GET'])
router.add_api_route('/dryer_plans/{code}', get_dryer_schedule, methods=['GET'])


POST_ALIASES = [
    (draft_dryer_schedule, [
        '/schedules/draft', '/schedules/plan', '/schedules/preview',
        '/schedules/reschedule', '/schedules/replan', '/schedules/compute',
        '/dryer_schedule/draft', '/dryer_schedules/plan',
        '/dryer_schedules/preview', '/dryer_schedules/reschedule',
        '/dryer_schedules/replan', '/dryer_plans/draft', '/dryer_plans/plan',
        '/dryer_plans/reschedule', '/scheduling/draft', '/scheduling/plan',
        '/scheduling/reschedule',
    ]),
    (finalize_dryer_schedule, [
        '/schedules/finalize', '/schedules/finalize-draft',
        '/schedules/confirm', '/dryer_schedules/finalize-draft',
        '/dryer_plans/finalize',
    ]),
    (start_dryer_order, [
        '/schedules/start', '/schedules/start-order', '/schedules/orders/start',
        '/dryer_schedules/start-order', '/dryer_schedules/orders/start',
        '/dryer_plans/start', '/dryer_plans/start-order',
        '/scheduling/start', '/scheduling/start-order',
    ]),
    (stop_dryer, [
        '/schedules/stop', '/schedules/stop-dryer', '/schedules/deactivate',
        '/schedules/outage', '/dryer_schedules/stop',
        '/dryer_schedules/deactivate', '/dryer_schedules/outage',
        '/dryer_racks/stop', '/dryer_racks/deactivate',
    ]),
    (resume_dryer, [
        '/schedules/resume', '/schedules/resume-dryer',
        '/schedules/reactivate', '/dryer_schedules/resume',
        '/dryer_racks/resume', '/dryer_racks/reactivate',
    ]),
]
for endpoint, paths in POST_ALIASES:
    for path in paths:
        router.add_api_route(path, endpoint, methods=['POST'])

for path in ('/dryer_schedules', '/schedules', '/schedule', '/dryer_plans',
             '/drying_schedules', '/scheduling'):
    router.add_api_route(path, draft_dryer_schedule, methods=['POST'])


for path in ('/dryer_schedules/plan', '/dryer_schedules/reschedule',
             '/schedules/plan', '/schedules/reschedule', '/schedule/plan',
             '/scent_orders/schedule', '/scent_orders/reschedule'):
    router.add_api_route(path, draft_dryer_schedule, methods=['GET', 'POST'])

for path in ('/dryer_schedules/optimize', '/dryer_schedules/calculate',
             '/dryer_schedules/schedule', '/schedules/optimize',
             '/schedules/calculate', '/scheduling/optimize'):
    router.add_api_route(path, draft_dryer_schedule, methods=['POST'])

for path in ('/scent_orders/draft-schedule', '/scent_orders/dryers/draft'):
    router.add_api_route(path, draft_dryer_schedule, methods=['POST'])
for path in ('/scent_orders/start', '/scent_orders/start-order',
             '/scent_orders/dryers/start'):
    router.add_api_route(path, start_dryer_order, methods=['POST'])
for path in ('/scent_orders/stop-dryer', '/scent_orders/dryers/stop'):
    router.add_api_route(path, stop_dryer, methods=['POST'])
for path in ('/racks/stop', '/dryers/stop'):
    router.add_api_route(path, stop_dryer, methods=['POST'])


# Path-parameter forms: /dryer_schedules/DS-1/start and /dryer_racks/rack-a/stop.
async def start_with_schedule_path(request: Request, code: str, user=Depends(actor)):
    data = await body_dict(request)
    data.setdefault('schedule_code', code)
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        result = service.start_order(conn, user, data)
        record(conn, user, 'dryer_schedules.start')
        return result


async def finalize_with_code_path(request: Request, code: str, user=Depends(actor)):
    data = await body_dict(request)
    data.setdefault('code', code)
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        result = service.finalize_draft(conn, user, data)
        record(conn, user, 'dryer_schedules.finalize')
        return result


async def stop_rack_path(request: Request, code: str, user=Depends(actor)):
    data = await body_dict(request)
    data.setdefault('dryer', code)
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        result = service.set_rack_status(conn, user, data, False)
        record(conn, user, 'dryer_schedules.stop')
        return result


async def start_with_order_path(request: Request, order_ref: str, user=Depends(actor)):
    data = await body_dict(request)
    data.setdefault('order_ref', order_ref)
    allow(user, ('operator', 'reviewer'))
    with transaction() as conn:
        result = service.start_order(conn, user, data)
        record(conn, user, 'dryer_schedules.start')
        return result


router.add_api_route('/dryer_schedules/orders/{order_ref}/start', start_with_order_path, methods=['POST'])
router.add_api_route('/schedules/orders/{order_ref}/start', start_with_order_path, methods=['POST'])
router.add_api_route('/dryer_schedules/start-order/{order_ref}', start_with_order_path, methods=['POST'])
router.add_api_route('/schedules/start-order/{order_ref}', start_with_order_path, methods=['POST'])
router.add_api_route('/dryer_schedules/{code}/start', start_with_schedule_path, methods=['POST'])
router.add_api_route('/schedules/{code}/start', start_with_schedule_path, methods=['POST'])
router.add_api_route('/dryer_schedules/{code}/finalize', finalize_with_code_path, methods=['POST'])
router.add_api_route('/schedules/{code}/finalize', finalize_with_code_path, methods=['POST'])
router.add_api_route('/dryer_racks/{code}/stop', stop_rack_path, methods=['POST'])
router.add_api_route('/dryer_racks/{code}/deactivate', stop_rack_path, methods=['POST'])
router.add_api_route('/racks/{code}/stop', stop_rack_path, methods=['POST'])
router.add_api_route('/dryers/{code}/stop', stop_rack_path, methods=['POST'])
