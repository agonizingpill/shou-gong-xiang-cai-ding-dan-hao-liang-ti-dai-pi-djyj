import {request} from '../api.js';
import {el} from '../dom.js';
import {status, titleBlock} from '../widgets.js';

const RACK_LABELS = {'rack-a': 'A 架', 'rack-b': 'B 架'};
let latestDraft = null;

function grams(value) {
  return value === null || value === undefined ? '—' : String(value);
}

function assignmentTable(draft, role, onChange) {
  const rows = draft.assignments || draft.items || [];
  if (!rows.length) return status('草案没有可上架订单。');
  return el('div', {class: 'scroll'}, el('table', {},
    el('thead', {}, el('tr', {},
      el('th', {scope: 'col'}, '订单'),
      el('th', {scope: 'col'}, '交期'),
      el('th', {scope: 'col'}, '参考批'),
      el('th', {scope: 'col'}, 'A 架湿料 g'),
      el('th', {scope: 'col'}, 'B 架湿料 g'),
      el('th', {scope: 'col'}, '定稿架位'),
      el('th', {scope: 'col'}, '克数'),
      el('th', {scope: 'col'}, '操作'),
    )),
    el('tbody', {}, rows.map(item => el('tr', {},
      el('td', {}, item.order_ref),
      el('td', {}, item.due_date || '未填'),
      el('td', {}, item.primary_lot || '—'),
      el('td', {}, grams(item.rack_a_wet_g)),
      el('td', {}, grams(item.rack_b_wet_g)),
      el('td', {}, RACK_LABELS[item.dryer] || item.dryer),
      el('td', {}, grams(item.wet_grams)),
      el('td', {}, item.started || item.locked
        ? '已开工并保留'
        : (role === 'viewer' ? '只读' : el('button', {
            type: 'button',
            onclick: () => startFromDraft(draft, item, onChange),
          }, '开工'))),
    )))));
}

async function startFromDraft(draft, item, onChange) {
  const notice = document.getElementById('schedule-notice');
  try {
    const result = await request('/dryer_schedules/start', {
      schedule_code: draft.code,
      draft_token: draft.draft_token || draft.token,
      order_ref: item.order_ref,
      dryer: item.dryer,
      wet_grams: item.wet_grams,
    });
    if (notice) {
      notice.textContent = `${item.order_ref} 已开工：${RACK_LABELS[result.dryer] || result.dryer} · ${result.wet_grams}g`;
      notice.className = 'status ok';
    }
    await onChange();
  } catch (error) {
    if (notice) {
      notice.textContent = error.message;
      notice.className = 'status error';
    }
  }
}

function draftPanel(role, reload) {
  const capacityA = el('input', {name: 'capacity_a_g', type: 'number', min: '0', value: '1000', required: ''});
  const capacityB = el('input', {name: 'capacity_b_g', type: 'number', min: '0', value: '1000', required: ''});
  const code = el('input', {name: 'code', type: 'text', placeholder: '留空自动生成', value: ''});
  const notice = el('p', {id: 'schedule-notice', class: 'status', role: 'status'}, '');
  const draftBody = el('div', {class: 'draft-body'});

  async function computeDraft(event) {
    event.preventDefault();
    const button = event.submitter;
    if (button) button.disabled = true;
    notice.className = 'status';
    notice.textContent = '模型正在按参考粉料批估算两座架湿料……';
    try {
      latestDraft = await request('/dryer_schedules/draft', {
        code: code.value.trim() || undefined,
        capacity_a_g: Number(capacityA.value),
        capacity_b_g: Number(capacityB.value),
        all_orders: true,
      });
      notice.textContent = `已生成草案 ${latestDraft.code}（版本 ${latestDraft.version}）`;
      notice.className = 'status ok';
      renderDraft();
      await reload();
    } catch (error) {
      notice.textContent = error.message;
      notice.className = 'status error';
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function finalizeDraft() {
    if (!latestDraft) return;
    try {
      latestDraft = await request('/dryer_schedules/finalize', {
        code: latestDraft.code,
        token: latestDraft.draft_token || latestDraft.token,
        version: latestDraft.version,
      });
      notice.textContent = `排班 ${latestDraft.code} 已定稿，开工照此备料。`;
      notice.className = 'status ok';
      renderDraft();
      await reload();
    } catch (error) {
      notice.textContent = error.message;
      notice.className = 'status error';
      if (error.message.includes('失效')) {
        latestDraft = null;
        renderDraft();
      }
    }
  }

  function renderDraft() {
    draftBody.replaceChildren();
    if (!latestDraft) {
      draftBody.append(status('尚未计算草案。停架或开工后请重新计算；若计算期间又有单开工，旧草案必须重算。'));
      return;
    }
    const skipped = latestDraft.skipped_order_refs || [];
    const blocked = latestDraft.blocked_racks || latestDraft.stopped_racks || [];
    const usage = latestDraft.usage_g || {};
    const remaining = latestDraft.remaining_g || {};
    draftBody.append(
      el('div', {class: 'draft-meta'},
        el('strong', {}, `${latestDraft.code} · ${latestDraft.status} · 版本 ${latestDraft.version}`),
        el('span', {}, `停用架：${blocked.map(x => RACK_LABELS[x] || x).join('、') || '无'}`),
        el('span', {}, `A 架已用 ${grams(usage['rack-a'])}g / 余 ${grams(remaining['rack-a'])}g`),
        el('span', {}, `B 架已用 ${grams(usage['rack-b'])}g / 余 ${grams(remaining['rack-b'])}g`),
      ),
      assignmentTable(latestDraft, role, reload),
      el('div', {class: 'draft-actions'},
        role === 'viewer' ? null : el('button', {type: 'button', onclick: finalizeDraft}, '定稿'),
        el('button', {type: 'button', class: 'quiet', onclick: () => { latestDraft = null; renderDraft(); }}, '关闭草案'),
      ),
      skipped.length ? el('p', {class: 'status'}, `本轮未排入：${skipped.join('、')}`) : null,
      (latestDraft.ineligible_orders || []).length
        ? el('div', {class: 'scroll'}, el('table', {},
            el('thead', {}, el('tr', {}, el('th', {}, '暂不可估算订单'), el('th', {}, '原因'))),
            el('tbody', {}, latestDraft.ineligible_orders.map(x => el('tr', {},
              el('td', {}, x.order_ref || '—'), el('td', {}, x.reason))))))
        : null,
    );
  }

  const form = el('form', {class: 'action schedule-form', onsubmit: computeDraft},
    el('h3', {}, '重排排班草案'),
    el('p', {}, '先最大化排得下的订单数；订单数相同时，按交期更早优先。每单只分配一座架。'),
    el('div', {class: 'fields'},
      el('label', {}, el('span', {}, '草案编号（可空）'), code),
      el('label', {}, el('span', {}, 'A 架当班上限 g'), capacityA),
      el('label', {}, el('span', {}, 'B 架当班上限 g'), capacityB),
    ),
    role === 'viewer' ? status('只读账号可查看，不能重排或开工。') : el('button', {type: 'submit'}, '按参考粉料批重排'),
    notice,
  );

  const panel = el('section', {class: 'ledger schedule-draft', 'aria-label': '排班草案'},
    el('h2', {}, '干燥架排班'), form, draftBody);
  renderDraft();
  return panel;
}

function rackPanel(racks, role, onChange) {
  async function setRack(code, active) {
    const path = active ? '/dryer_schedules/resume-dryer' : '/dryer_schedules/stop-dryer';
    try {
      await request(path, {dryer: code});
      await onChange();
    } catch (error) {
      window.alert(error.message);
    }
  }
  return el('section', {class: 'ledger', 'aria-label': '干燥架状态'},
    el('h2', {}, '架位与并发状态'),
    el('div', {class: 'scroll'}, el('table', {},
      el('thead', {}, el('tr', {},
        el('th', {}, '架位'), el('th', {}, '状态'), el('th', {}, '当班上限 g'),
        el('th', {}, '版本'), el('th', {}, '操作'))),
      el('tbody', {}, (racks || []).map(row => el('tr', {},
        el('td', {}, RACK_LABELS[row.code] || row.code),
        el('td', {}, row.status || (row.active ? '运行中' : '已停用')),
        el('td', {}, row.capacity_g ?? '未设置'),
        el('td', {}, row.revision ?? row.version ?? '—'),
        el('td', {}, role === 'viewer' ? '只读' : el('div', {class: 'button-row'},
          row.active === false || row.active === 0
            ? el('button', {type: 'button', onclick: () => setRack(row.code, true)}, `恢复${RACK_LABELS[row.code] || row.code}`)
            : el('button', {type: 'button', onclick: () => setRack(row.code, false)}, `停用${RACK_LABELS[row.code] || row.code}`),
        )),
      ))))));
}

function startsPanel(starts) {
  const rows = (starts || []).map(row => el('tr', {},
    el('td', {}, row.code), el('td', {}, row.order_ref),
    el('td', {}, RACK_LABELS[row.dryer] || row.dryer),
    el('td', {}, row.wet_grams), el('td', {}, row.schedule_code || '—'),
    el('td', {}, row.state)));
  if (!rows.length) {
    rows.push(el('tr', {}, el('td', {colspan: '6'}, '暂无已开工记录')));
  }
  return el('section', {class: 'ledger', 'aria-label': '已开工保留记录'},
    el('h2', {}, '停架前已开工订单'),
    el('div', {class: 'scroll'}, el('table', {},
      el('thead', {}, el('tr', {},
        el('th', {}, '记录'), el('th', {}, '订单'), el('th', {}, '原架'),
        el('th', {}, '保留克数'), el('th', {}, '草案'), el('th', {}, '状态'))),
      el('tbody', {}, rows))));
}

export const title = '干燥架排班';
export const key = 'dryer_schedules';

export async function render(root, role) {
  root.replaceChildren(titleBlock('干燥架临时停用', '排班与开工保留',
    '按每单选定的参考粉料批，用同一模型估算 A/B 两座架湿料需求。停架前开工的单保留原架和克数；停架后该架不接新单，待开工单重算。'),
    el('div', {class: 'work schedule-layout'},
      el('div', {class: 'schedule-main', id: 'schedule-draft-slot'}),
      el('div', {class: 'schedule-side'},
        el('div', {id: 'schedule-racks-slot'}),
        el('div', {id: 'schedule-starts-slot'}))));

  const draftSlot = document.getElementById('schedule-draft-slot');
  const racksSlot = document.getElementById('schedule-racks-slot');
  const startsSlot = document.getElementById('schedule-starts-slot');

  async function reload() {
    const [racks, starts] = await Promise.all([
      request('/dryer_racks'), request('/dryer_starts')]);
    racksSlot.replaceChildren(rackPanel(racks, role, reload));
    startsSlot.replaceChildren(startsPanel(starts));
  }

  draftSlot.replaceChildren(draftPanel(role, reload));
  try {
    await reload();
  } catch (error) {
    racksSlot.replaceChildren(status(error.message, 'error'));
  }
}
