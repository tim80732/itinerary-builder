/* Light or dark. Two visible states, three real ones: with nothing stored the
   attribute is absent and app.css lets the OS decide, which is what Auto means.
   The first click reads what is actually on screen rather than assuming dark —
   otherwise it appears to do nothing for anyone already running light. */
(function () {
  var btn = document.getElementById('themeBtn');
  if (!btn) return;
  btn.addEventListener('click', function () {
    var set = document.documentElement.getAttribute('data-theme');
    var now = set || (window.matchMedia &&
      window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    var next = now === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('ib-theme', next); } catch (e) {}
  });
})();

/* Shows only the fields that the selected card type needs, and words the
   time labels the way that type behaves. */
(function () {
  var select = document.getElementById('typeSelect');
  var kindsEl = document.getElementById('type-kinds');
  if (!select || !kindsEl) return;

  var KINDS = JSON.parse(kindsEl.textContent);

  var WORDING = {
    move: { start_time: 'Depart time', end_time: 'Arrive time',
            from_head: 'Departure', to_head: 'Arrival',
            start_date: 'day of travel' },
    hire: { start_time: 'Pick-up time', end_time: 'Drop-off time',
            from_head: 'Pick-up', to_head: 'Drop-off',
            start_date: 'pick-up day' },
    stay: { start_time: 'Check-in time', end_time: 'Check-out time',
            start_date: 'check-in day' },
    point: { start_time: 'Time', end_time: 'Ends at (optional)',
             start_date: 'day' }
  };

  function apply() {
    var kind = KINDS[select.value] || 'point';
    var words = WORDING[kind] || WORDING.point;

    Array.prototype.forEach.call(document.querySelectorAll('.kind-field'), function (node) {
      var allowed = (node.getAttribute('data-kinds') || '').split(' ');
      node.classList.toggle('hide', allowed.indexOf(kind) === -1);
    });

    Array.prototype.forEach.call(document.querySelectorAll('[data-label]'), function (node) {
      var key = node.getAttribute('data-label');
      if (words[key]) node.textContent = words[key];
    });

    Array.prototype.forEach.call(document.querySelectorAll('[data-hint]'), function (node) {
      var key = node.getAttribute('data-hint');
      node.textContent = words[key] ? '(' + words[key] + ')' : '';
    });

    /* "Type label" only makes sense for the custom type */
    var labelField = document.querySelector('[name="type_label"]');
    if (labelField) {
      labelField.closest('label').classList.toggle('hide', select.value !== 'custom');
    }
  }

  select.addEventListener('change', apply);
  apply();
})();


/* Warn before leaving a page with unsaved edits. In-app links get a real
   choice; a tab close or refresh falls back to the browser's own prompt. */
(function () {
  var forms = document.querySelectorAll('form[data-dirty-guard]');
  var modal = document.getElementById('leaveModal');
  if (!forms.length || !modal) return;

  var guarded = forms[0];
  var dirty = false;
  var pendingHref = null;

  Array.prototype.forEach.call(forms, function (f) {
    f.addEventListener('input', function () { dirty = true; });
    f.addEventListener('change', function () { dirty = true; });
  });
  /* any submit (including delete buttons elsewhere on the page) is a decision */
  document.addEventListener('submit', function () { dirty = false; });

  window.addEventListener('beforeunload', function (e) {
    if (!dirty) return;
    e.preventDefault();
    e.returnValue = '';
  });

  document.addEventListener('click', function (e) {
    if (!dirty) return;
    var link = e.target.closest('a[href]');
    if (!link) return;
    var href = link.getAttribute('href');
    if (!href || href.charAt(0) === '#') return;                 // same page
    if (link.target && link.target !== '_self') return;          // new tab
    if (link.hasAttribute('download') || link.hasAttribute('data-no-guard')) return;
    e.preventDefault();
    pendingHref = link.href;
    modal.classList.add('open');
  });

  function close() { modal.classList.remove('open'); }

  document.getElementById('leaveSave').addEventListener('click', function () {
    dirty = false;
    close();
    if (guarded.requestSubmit) guarded.requestSubmit();
    else guarded.submit();
  });
  document.getElementById('leaveDiscard').addEventListener('click', function () {
    dirty = false;
    close();
    window.location.href = pendingHref;
  });
  document.getElementById('leaveStay').addEventListener('click', function () {
    pendingHref = null;
    close();
  });
  modal.addEventListener('click', function (e) {
    if (e.target === modal) { pendingHref = null; close(); }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && modal.classList.contains('open')) {
      pendingHref = null;
      close();
    }
  });
})();


/* Project page: the cards panel shows either a calendar (default) or the flat
   list. Picking a day reveals that day's cards and its own add button.

   Which view, which day and where the page was scrolled are kept in
   sessionStorage, so a round trip through the card editor comes back to the
   spot it left from. The server sets ?restore=1 on the way back — without it a
   fresh visit gets the server's own choice of day. The scroll position is
   stored relative to the cards panel, so the flash message that appears after
   a save doesn't push everything down by its own height. */
(function () {
  var host = document.getElementById('cardViews');
  var tabs = document.getElementById('cardViewTabs');
  if (!host || !tabs) return;

  var panels = document.getElementById('dayPanels');
  var anchor = host.closest('.panel') || host;
  var layout = document.getElementById('projectLayout');
  var VIEW_KEY = 'ib-card-view';
  var STATE_KEY = 'ib-proj:' + (host.getAttribute('data-trip') || '');
  var restoring = host.getAttribute('data-restore') === '1';
  var view = 'calendar';
  var day = '';

  /* On a wide screen the layout is an app shell, so "where the page was" is the
     scroll position of these panes rather than of the window. */
  var panes = {
    side: document.getElementById('projectSide'),
    cal: document.getElementById('calRegion'),
    day: panels,
    list: document.getElementById('listScroll')
  };

  function shell() { return window.matchMedia('(min-width: 1101px)').matches; }

  /* Give the layout exactly the height left below its top edge; the columns
     then scroll inside themselves and the page itself doesn't move. */
  function sizeShell() {
    if (!layout) return;
    if (!shell()) { layout.style.height = ''; return; }
    var top = layout.getBoundingClientRect().top + (window.pageYOffset || 0);
    layout.style.height = Math.max(420, Math.round(window.innerHeight - top - 18)) + 'px';
  }

  function anchorTop() {
    return anchor.getBoundingClientRect().top + (window.pageYOffset || 0);
  }

  function remember() {
    var tops = {}, k;
    for (k in panes) {
      if (panes[k]) tops[k] = Math.round(panes[k].scrollTop);
    }
    try {
      sessionStorage.setItem(STATE_KEY, JSON.stringify({
        view: view, day: day, tops: tops,
        off: Math.round((window.pageYOffset || 0) - anchorTop())
      }));
    } catch (e) {}
  }

  function showView(name) {
    view = name;
    Array.prototype.forEach.call(host.querySelectorAll('.cardview'), function (v) {
      v.classList.toggle('hide', v.getAttribute('data-cardview') !== name);
    });
    Array.prototype.forEach.call(tabs.querySelectorAll('[data-cardview]'), function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-cardview') === name);
    });
    try { localStorage.setItem(VIEW_KEY, name); } catch (e) {}
  }

  function openDay(key, reveal) {
    day = key;
    if (!panels) return;
    var opened = null;
    Array.prototype.forEach.call(panels.querySelectorAll('.day-panel'), function (panel) {
      var mine = panel.getAttribute('data-day') === key;
      panel.classList.toggle('hide', !mine);
      if (mine) opened = panel;
    });
    Array.prototype.forEach.call(host.querySelectorAll('.cal-day[data-day]'), function (cell) {
      cell.classList.toggle('sel', cell.getAttribute('data-day') === key);
    });
    /* only on a click: show the new day from its top, and scroll the page just
       enough if the panel sits off-screen (it never does in shell mode) */
    if (reveal && opened) {
      if (panels) panels.scrollTop = 0;
      if (opened.scrollIntoView) opened.scrollIntoView({ block: 'nearest' });
    }
  }

  tabs.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-cardview]');
    if (btn) { showView(btn.getAttribute('data-cardview')); remember(); }
  });

  host.addEventListener('click', function (ev) {
    var cell = ev.target.closest('.cal-day[data-day]');
    if (cell) { openDay(cell.getAttribute('data-day'), true); remember(); }
  });

  var tick = null;
  function later() {
    if (tick) return;
    tick = setTimeout(function () { tick = null; remember(); }, 200);
  }
  window.addEventListener('scroll', later);
  window.addEventListener('pagehide', remember);
  for (var name in panes) {
    if (panes[name]) panes[name].addEventListener('scroll', later);
  }

  /* --- draggable divider between the calendar and the day's cards ---
     Dragging pins an explicit height on #calRegion, which overrides the
     --cal-floor / --cal-max sizing; a double-click drops it and hands the
     height back to the stylesheet. Kept per trip, since a fortnight and a
     three-month trip want different splits. */
  var splitter = document.getElementById('calSplitter');
  var cal = panes.cal;
  var calContent = cal ? cal.scrollHeight : 0;   /* read before we pin anything */
  var CAL_KEY = 'ib-cal-h:' + (host.getAttribute('data-trip') || '');
  var CAL_MIN = 100;            /* about one week row */
  var CARDS_MIN = 140;          /* the day header plus a card */

  function storedCal() {
    try { return parseInt(localStorage.getItem(CAL_KEY) || '', 10) || 0; } catch (e) { return 0; }
  }

  function pinCal(px, save) {
    if (!cal) return;
    /* never taller than the months themselves, nor so tall the cards vanish */
    var room = Math.max(CAL_MIN, host.clientHeight - CARDS_MIN);
    px = Math.min(Math.max(CAL_MIN, Math.round(px)), Math.min(room, calContent || room));
    cal.style.flex = '0 0 auto';
    cal.style.maxHeight = 'none';
    cal.style.height = px + 'px';
    if (save) { try { localStorage.setItem(CAL_KEY, String(px)); } catch (e) {} }
  }

  function unpinCal() {
    if (!cal) return;
    cal.style.flex = '';
    cal.style.maxHeight = '';
    cal.style.height = '';
  }

  function applyCal() {
    if (!cal) return;
    if (!shell()) { unpinCal(); return; }      /* the stacked layout sizes itself */
    var saved = storedCal();
    if (saved) pinCal(saved, false);
  }

  if (splitter && cal) {
    splitter.addEventListener('pointerdown', function (down) {
      if (!shell()) return;
      down.preventDefault();
      splitter.classList.add('dragging');
      document.body.style.userSelect = 'none';
      var top = cal.getBoundingClientRect().top;

      function move(ev) { pinCal(ev.clientY - top, false); }
      function up() {
        splitter.classList.remove('dragging');
        document.body.style.userSelect = '';
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        pinCal(cal.getBoundingClientRect().height, true);
      }
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    });

    splitter.addEventListener('dblclick', function () {
      unpinCal();
      try { localStorage.removeItem(CAL_KEY); } catch (e) {}
    });
  }

  window.addEventListener('resize', function () { sizeShell(); applyCal(); });
  sizeShell();
  applyCal();

  var saved = null;
  if (restoring) {
    try { saved = JSON.parse(sessionStorage.getItem(STATE_KEY) || 'null'); } catch (e) {}
  }
  var stored = null;
  try { stored = localStorage.getItem(VIEW_KEY); } catch (e) {}

  showView((saved && saved.view) || (stored === 'list' ? 'list' : 'calendar'));

  var wanted = saved && saved.day;
  if (wanted && panels && !panels.querySelector('.day-panel[data-day="' + wanted + '"]')) {
    wanted = null;                       /* the trip dates moved under it */
  }
  openDay(wanted || host.getAttribute('data-selected') || '', false);

  if (saved && saved.tops) {
    for (var pane in saved.tops) {
      if (panes[pane]) panes[pane].scrollTop = saved.tops[pane];
    }
  }
  if (saved && typeof saved.off === 'number' && !shell()) {
    var back = function () { window.scrollTo(0, Math.max(0, Math.round(anchorTop() + saved.off))); };
    back();
    setTimeout(back, 0);                 /* again once the day panel has laid out */
  }
  if (restoring && window.history.replaceState) {
    window.history.replaceState(null, '', window.location.pathname);
  }
})();


/* Project page: "copy this card to another date or project". One dialog,
   pointed at whichever row's button was pressed. */
(function () {
  var modal = document.getElementById('copyModal');
  var form = document.getElementById('copyForm');
  if (!modal || !form) return;

  var action = modal.getAttribute('data-action') || '';
  var what = document.getElementById('copyWhat');
  var target = document.getElementById('copyTarget');
  var range = document.getElementById('copyRange');
  var dateEl = document.getElementById('copyDate');
  var docsRow = document.getElementById('copyDocsRow');

  function showRange() {
    var opt = target.options[target.selectedIndex];
    range.textContent = opt ? 'that project runs ' + (opt.getAttribute('data-range') || '') : '';
  }

  function open(btn) {
    var docs = parseInt(btn.getAttribute('data-copy-docs') || '0', 10);
    form.action = action.replace('__ID__', btn.getAttribute('data-copy-card'));
    what.textContent = '“' + (btn.getAttribute('data-copy-title') || '') +
      '” — an end date and any hidden days move along with the start date.';
    dateEl.value = btn.getAttribute('data-copy-date') || '';
    docsRow.classList.toggle('hide', !docs);
    docsRow.querySelector('input').checked = !!docs;
    showRange();
    modal.classList.add('open');
    dateEl.focus();
  }

  function close() { modal.classList.remove('open'); }

  target.addEventListener('change', showRange);
  document.getElementById('copyCancel').addEventListener('click', close);

  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-copy-card]');
    if (btn) { ev.preventDefault(); open(btn); return; }
    if (ev.target === modal) close();
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && modal.classList.contains('open')) close();
  });
})();


/* Card editor: which days a multi-day card appears on. The span is recomputed
   from the type and the dates as they are edited — the same rule as
   `datesOf` in the exported page — and every ticked day posts a `show_day`;
   whatever is left over becomes the card's hide list. The marker field is set
   here rather than in the template so that a page without JS posts no marker
   and the server leaves the stored list alone. */
(function () {
  var box = document.getElementById('dayPick');
  var chips = document.getElementById('dayChips');
  var marker = document.getElementById('dayToggles');
  var select = document.getElementById('typeSelect');
  var kindsEl = document.getElementById('type-kinds');
  var form = document.getElementById('cardForm');
  if (!box || !chips || !marker || !select || !kindsEl || !form) return;

  var KINDS = JSON.parse(kindsEl.textContent);
  var startEl = form.querySelector('[name="start_date"]');
  var endEl = form.querySelector('[name="end_date"]');
  if (!startEl || !endEl) return;
  marker.value = '1';

  var MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  var DAY3 = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var ISO = /^\d{4}-\d{2}-\d{2}$/;
  var MAX_DAYS = 400;

  var hidden = {};
  (box.getAttribute('data-hidden') || '').split(' ').forEach(function (d) {
    if (d) hidden[d] = true;
  });

  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function parseD(s) { var p = s.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function ymd(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function label(key) {
    var d = parseD(key);
    return DAY3[d.getDay()] + ' ' + d.getDate() + ' ' + MON[d.getMonth()];
  }

  function span() {
    var s = (startEl.value || '').trim();
    if (!ISO.test(s)) return [];
    var e = (endEl.value || '').trim();
    if (!ISO.test(e) || e < s) e = s;
    var kind = KINDS[select.value] || 'point';
    var out = [], d = parseD(s), end = parseD(e);

    if (kind === 'stay') {                       /* nights: check-out excluded */
      while (d < end && out.length < MAX_DAYS) { out.push(ymd(d)); d.setDate(d.getDate() + 1); }
      if (!out.length) out.push(s);
    } else if (kind === 'hire') {
      while (d <= end && out.length < MAX_DAYS) { out.push(ymd(d)); d.setDate(d.getDate() + 1); }
    } else if (kind === 'move') {
      out.push(s);
      if (e !== s) out.push(e);
    } else {
      out.push(s);
    }
    return out;
  }

  function boxes() { return chips.querySelectorAll('input[type="checkbox"]'); }

  function paint() {
    Array.prototype.forEach.call(boxes(), function (b) {
      b.parentNode.classList.toggle('off', !b.checked);
    });
  }

  function set(b, on) {
    b.checked = on;
    if (on) delete hidden[b.value];
    else hidden[b.value] = true;
  }

  function keepOne() {                 /* a card with no days left would vanish */
    var all = boxes(), any = false;
    Array.prototype.forEach.call(all, function (b) { if (b.checked) any = true; });
    if (!any && all.length) set(all[0], true);
  }

  function render() {
    var days = span();
    chips.innerHTML = '';
    days.forEach(function (key) {
      var chip = document.createElement('label');
      chip.className = 'daychip';
      var tick = document.createElement('input');
      tick.type = 'checkbox';
      tick.name = 'show_day';
      tick.value = key;
      tick.checked = !hidden[key];
      tick.addEventListener('change', function () {
        set(tick, tick.checked);
        keepOne();
        paint();
      });
      chip.appendChild(tick);
      chip.appendChild(document.createTextNode(label(key)));
      chips.appendChild(chip);
    });
    keepOne();
    paint();
    box.classList.toggle('hide', days.length < 2);
  }

  box.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-days]');
    if (!btn) return;
    var all = boxes();
    Array.prototype.forEach.call(all, function (b, i) {
      set(b, btn.getAttribute('data-days') === 'all' || i === 0 || i === all.length - 1);
    });
    paint();
  });

  select.addEventListener('change', render);
  Array.prototype.forEach.call([startEl, endEl], function (el) {
    el.addEventListener('change', render);
    el.addEventListener('input', render);
  });
  render();
})();


/* Project page: drag a card to another place in the day, or into another part
   of it. Pointer events rather than HTML5 drag-and-drop, so it works the same
   under a finger; the row itself moves through the DOM as you go, and only its
   final neighbours are sent to the server. */
(function () {
  var panels = document.getElementById('dayPanels');
  if (!panels) return;
  var TEMPLATE = panels.getAttribute('data-place-url') || '';
  if (!TEMPLATE) return;

  var row = null, handle = null, started = false, startY = 0;
  var EDGE = 44, STEP = 10;          /* auto-scroll near the pane's edges */

  function lists() { return panels.querySelectorAll('.list[data-drop]'); }

  function neighbour(node, dir) {
    var next = dir < 0 ? node.previousElementSibling : node.nextElementSibling;
    while (next && !next.classList.contains('card-row')) {
      next = dir < 0 ? next.previousElementSibling : next.nextElementSibling;
    }
    return next;
  }

  function moveTo(x, y) {
    var under = document.elementFromPoint(x, y);
    if (!under) return;

    var overRow = under.closest ? under.closest('.card-row') : null;
    if (overRow && overRow !== row && !overRow.closest('.day-pinned')) {
      var box = overRow.getBoundingClientRect();
      var after = y > box.top + box.height / 2;
      overRow.parentNode.insertBefore(row, after ? overRow.nextSibling : overRow);
      return;
    }
    /* not over a card: drop into whichever section the pointer is inside */
    var list = under.closest ? under.closest('.list[data-drop]') : null;
    if (!list) {
      var sec = under.closest ? under.closest('.day-sec') : null;
      list = sec ? sec.querySelector('.list[data-drop]') : null;
    }
    if (list && row.parentNode !== list) list.appendChild(row);
  }

  function autoScroll(y) {
    var box = panels.getBoundingClientRect();
    if (y < box.top + EDGE) panels.scrollTop -= STEP;
    else if (y > box.bottom - EDGE) panels.scrollTop += STEP;
  }

  function finish() {
    var list = row.parentNode;
    var body = new FormData();
    body.set('section', list.getAttribute('data-drop') || '');
    body.set('day', list.getAttribute('data-day') || '');
    var above = neighbour(row, -1), below = neighbour(row, 1);
    body.set('after', above ? above.getAttribute('data-card') : '');
    body.set('before', below ? below.getAttribute('data-card') : '');

    fetch(TEMPLATE.replace('__ID__', row.getAttribute('data-card')),
          { method: 'POST', body: body })
      .then(function (r) { if (!r.ok) window.location.reload(); })
      .catch(function () { window.location.reload(); });

    /* a section that has just gained or lost its only card */
    Array.prototype.forEach.call(lists(), function (l) {
      l.parentNode.classList.toggle('empty', !l.querySelector('.card-row'));
    });
  }

  function stop(send) {
    if (started && send) finish();
    if (row) { row.classList.remove('dragging'); }
    panels.classList.remove('dnd');
    document.body.style.userSelect = '';
    row = null; handle = null; started = false;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onCancel);
  }

  function onMove(ev) {
    if (!row) return;
    if (!started) {
      if (Math.abs(ev.clientY - startY) < 4) return;   /* a tap is not a drag */
      started = true;
      row.classList.add('dragging');
      panels.classList.add('dnd');
      document.body.style.userSelect = 'none';
    }
    ev.preventDefault();
    autoScroll(ev.clientY);
    moveTo(ev.clientX, ev.clientY);
  }

  function onUp() { stop(true); }
  function onCancel() { stop(false); }

  panels.addEventListener('pointerdown', function (ev) {
    var grip = ev.target.closest('.drag-handle');
    if (!grip) return;
    var card = grip.closest('.card-row');
    if (!card || card.closest('.day-pinned')) return;   /* the hotel is pinned */
    ev.preventDefault();
    row = card; handle = grip; startY = ev.clientY; started = false;
    if (grip.setPointerCapture) { try { grip.setPointerCapture(ev.pointerId); } catch (e) {} }
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onCancel);
  });
})();


/* Live preview: the server renders the card with the real exporter and we show
   it in an iframe sized to the pane. */
(function () {
  var form = document.getElementById('cardForm');
  var frame = document.getElementById('previewFrame');
  var stage = document.getElementById('previewStage');
  if (!form || !frame || !stage) return;

  var url = form.getAttribute('data-preview-url');
  var timer = null;
  var pending = false;

  /* The preview always renders at the pane's own width: on a phone that is the
     phone layout, and on a desktop you widen it by dragging the divider. No
     scaling, so nothing can overflow its container. */
  function pinned() {
    return window.matchMedia('(min-width: 1101px)').matches;
  }

  /* Give the two-column layout exactly the height left below its top edge. The
     left column then scrolls inside itself and the preview never moves. */
  function sizePane() {
    var layoutEl = document.getElementById('editorLayout');
    if (!layoutEl) return;
    if (!pinned()) { layoutEl.style.height = ''; return; }
    var naturalTop = layoutEl.getBoundingClientRect().top + window.scrollY;
    var h = Math.max(420, Math.round(window.innerHeight - naturalTop - 20));
    layoutEl.style.height = h + 'px';
  }

  function fit() {
    sizePane();
    frame.style.transform = 'none';
    frame.style.width = (stage.clientWidth || 360) + 'px';
    if (pinned()) {
      /* the pane fills the window height; CSS owns the height and the preview
         scrolls inside the frame */
      frame.style.height = '';
      stage.style.height = '';
      return;
    }
    measure();                 // reading scrollHeight reflows the iframe for us
    setTimeout(measure, 80);   // and once more for late images/fonts
  }

  function measure() {
    if (pinned()) return;
    var doc = frame.contentDocument;
    var h = (doc && doc.body) ? Math.max(120, doc.body.scrollHeight) : 160;
    frame.style.height = h + 'px';
    stage.style.height = h + 'px';
  }

  frame.addEventListener('load', fit);
  window.addEventListener('resize', fit);

  function collect() {
    var data = new FormData(form);
    data.delete('documents');          // files are not needed for the preview
    var picked = form.querySelector('input[type="file"][name="documents"]');
    var extra = picked && picked.files ? picked.files.length : 0;
    /* the document rows still on the form (✕ removes one before saving) */
    var kept = form.querySelectorAll('input[name="doc_order"]').length;
    data.set('doc_count', String(kept + extra));
    return data;
  }

  function render() {
    if (pending) return;
    pending = true;
    fetch(url, { method: 'POST', body: collect() })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        frame.srcdoc = html;
        pending = false;
      })
      .catch(function () { pending = false; });
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(render, 280);
  }

  form.addEventListener('input', schedule);
  form.addEventListener('change', schedule);

  /* --- draggable divider between form and preview --- */
  var layout = document.getElementById('editorLayout');
  var splitter = document.getElementById('splitter');
  var STORE_KEY = 'ib-pane-width';
  var DEFAULT_PANE = 380;

  function setPane(px) {
    if (!layout) return;
    var total = layout.getBoundingClientRect().width;
    var max = Math.max(300, total - 520);      /* always leave room for the form */
    px = Math.min(max, Math.max(300, px));
    layout.style.setProperty('--pane-w', px + 'px');
    try { localStorage.setItem(STORE_KEY, String(Math.round(px))); } catch (e) {}
    fit();
  }

  if (splitter && layout) {
    splitter.addEventListener('pointerdown', function (down) {
      down.preventDefault();
      splitter.classList.add('dragging');
      document.body.style.userSelect = 'none';

      function move(ev) { setPane(layout.getBoundingClientRect().right - ev.clientX); }
      function up() {
        splitter.classList.remove('dragging');
        document.body.style.userSelect = '';
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
      }
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    });

    splitter.addEventListener('dblclick', function () { setPane(DEFAULT_PANE); });

    try {
      var saved = parseInt(localStorage.getItem(STORE_KEY) || '', 10);
      if (saved) layout.style.setProperty('--pane-w', saved + 'px');
    } catch (e) {}
  }

  render();
})();


/* Transit cards say how they travel: the Mode picker shows only for types that
   have modes, and the icon placeholder and the operator label follow the
   chosen mode. The server drops a mode the type doesn't have. */
(function () {
  var type = document.getElementById('typeSelect');
  var mode = document.getElementById('modeSelect');
  var field = document.getElementById('modeField');
  var modesEl = document.getElementById('type-modes');
  var iconsEl = document.getElementById('type-icons');
  if (!type || !mode || !field || !modesEl || !iconsEl) return;

  var MODES = JSON.parse(modesEl.textContent);
  var ICONS = JSON.parse(iconsEl.textContent);
  var icon = document.getElementById('iconInput');
  var service = document.getElementById('serviceLabel');
  var SERVICE = service ? service.textContent : '';

  function apply() {
    var modes = MODES[type.value] || null;
    field.classList.toggle('hide', !modes);
    var m = modes && modes[mode.value];
    if (icon) icon.placeholder = (m && m.icon) || ICONS[type.value] || '';
    if (service) service.textContent = (m && m.service) || SERVICE;
  }

  type.addEventListener('change', apply);
  mode.addEventListener('change', apply);
  apply();
})();


/* Link editor (card links, and the trip's links bar in Project details): add a
   row, remove one, drag one by its grip. The rows post in DOM order, so the
   order on screen is the order on the page. Every change fires an `input` on
   the form, which the dirty guard and the card preview both listen for. */
(function () {
  var editors = document.querySelectorAll('[data-links-editor]');
  Array.prototype.forEach.call(editors, function (box) {
    var rows = box.querySelector('.link-rows');
    var tpl = box.querySelector('.link-template');
    var form = box.closest('form');
    if (!rows || !tpl) return;

    function changed() {
      if (form) form.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function fresh() {
      var holder = document.createElement('div');
      holder.innerHTML = tpl.innerHTML.trim();
      return holder.firstElementChild;
    }

    box.querySelector('.link-add').addEventListener('click', function () {
      var row = fresh();
      rows.appendChild(row);
      row.querySelector('input').focus();
      changed();
    });

    rows.addEventListener('click', function (ev) {
      var btn = ev.target.closest('.link-remove');
      if (!btn) return;
      var row = btn.closest('.link-row');
      if (rows.children.length > 1) {
        row.remove();
      } else {                         /* keep one empty row to type into */
        Array.prototype.forEach.call(row.querySelectorAll('input'), function (i) { i.value = ''; });
      }
      changed();
    });

    /* drag by the grip: the row follows the pointer between its siblings */
    rows.addEventListener('pointerdown', function (down) {
      var grip = down.target.closest('.link-grip');
      if (!grip) return;
      down.preventDefault();
      var row = grip.closest('.link-row');
      row.classList.add('dragging');
      var moved = false;

      function move(ev) {
        var others = Array.prototype.filter.call(rows.children, function (r) { return r !== row; });
        var before = null;
        for (var i = 0; i < others.length; i++) {
          var r = others[i].getBoundingClientRect();
          if (ev.clientY < r.top + r.height / 2) { before = others[i]; break; }
        }
        if (row.nextElementSibling !== before) {
          rows.insertBefore(row, before);
          moved = true;
        }
      }
      function up() {
        row.classList.remove('dragging');
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        window.removeEventListener('pointercancel', up);
        if (moved) changed();
      }
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
    });
  });
})();


/* Trip documents and links (project page). Rows post in DOM order, so the order
   on screen is the order on the live page. A new row is a copy of a <template>
   with __KEY__ swapped for a throwaway key the server only uses to pair a row
   with its files. Every change fires `input` on the form for the dirty guard. */
(function () {
  var form = document.querySelector('[data-items-editor]');
  if (!form) return;
  var rows = form.querySelector('.items-rows');
  var made = 0;

  function changed() { form.dispatchEvent(new Event('input', { bubbles: true })); }

  function count(row) {
    var n = row.querySelectorAll('.tl-file').length;
    var label = row.querySelector('.tl-count');
    if (label) label.textContent = '· ' + n + (n === 1 ? ' file' : ' files');
    var name = row.querySelector('.tl-name');
    var input = row.querySelector('input[name="item_text"]');
    if (name && input) name.textContent = input.value.trim() || 'Document';
  }
  Array.prototype.forEach.call(rows.querySelectorAll('.tl-item.is-doc'), count);

  /* add a document / a link */
  Array.prototype.forEach.call(form.querySelectorAll('[data-add]'), function (btn) {
    btn.addEventListener('click', function () {
      var tpl = form.querySelector('template[data-new="' + btn.getAttribute('data-add') + '"]');
      var key = 'n' + Date.now().toString(36) + (made++);
      var holder = document.createElement('div');
      holder.innerHTML = tpl.innerHTML.split('__KEY__').join(key).trim();
      var row = holder.firstElementChild;
      rows.appendChild(row);
      count(row);
      row.querySelector('input[name="item_text"]').focus();
      changed();
    });
  });

  rows.addEventListener('click', function (ev) {
    var row = ev.target.closest('.tl-item');
    if (!row) return;
    if (ev.target.closest('.tl-remove')) { row.remove(); changed(); return; }
    if (ev.target.closest('.tl-fold') || ev.target.closest('.tl-head')) {
      row.classList.toggle('closed');
      if (!row.classList.contains('closed')) {
        var name = row.querySelector('input[name="item_text"]');
        if (name) name.focus();
      }
      return;
    }
    var del = ev.target.closest('.tl-file-del');
    if (!del) return;
    var file = del.closest('.tl-file');
    var picked = file.__input;
    if (picked) {
      /* new files picked together share one <input>: drop the pick and all its lines */
      Array.prototype.forEach.call(row.querySelectorAll('.tl-file.new'), function (l) {
        if (l.__input === picked) l.remove();
      });
      picked.remove();
    } else {
      file.remove();             /* a saved file: its keep_<key> input goes with it */
    }
    count(row);
    changed();
  });

  rows.addEventListener('input', function (ev) {
    if (ev.target.name === 'item_text') {
      var row = ev.target.closest('.tl-item');
      if (row && row.classList.contains('is-doc')) count(row);
    }
  });

  /* "Add files": each pick keeps its own <input>, so picking twice adds rather
     than replaces; a fresh empty input takes its place for the next pick. */
  rows.addEventListener('change', function (ev) {
    var input = ev.target;
    if (input.type !== 'file' || !input.files || !input.files.length) return;
    var row = input.closest('.tl-item');
    var list = row.querySelector('.tl-new-files');
    var fresh = input.cloneNode();
    fresh.value = '';
    input.parentNode.insertBefore(fresh, input);
    list.appendChild(input);                     /* still inside the form, so it posts */
    var together = input.files.length;
    Array.prototype.forEach.call(input.files, function (f) {
      var line = document.createElement('div');
      line.className = 'tl-file new';
      line.innerHTML = '<span></span><span class="tl-thumb">' + (/\.pdf$/i.test(f.name) ? '📄' : '🖼️') + '</span>' +
        '<span class="tl-file-name"></span>' +
        '<button type="button" class="ghost small danger tl-file-del" title="Remove" aria-label="Remove">✕</button>';
      var nameEl = line.querySelector('.tl-file-name');
      nameEl.textContent = f.name;
      var hint = document.createElement('span');
      hint.className = 'hint';
      hint.textContent = 'new · uploads when you save' +
        (together > 1 ? ' · ✕ removes the ' + together + ' picked together' : '');
      nameEl.appendChild(hint);
      line.__input = input;
      list.insertBefore(line, input);
    });
    count(row);
    changed();
  });

  /* drag a row by its grip */
  rows.addEventListener('pointerdown', function (down) {
    var grip = down.target.closest('.link-grip');
    if (!grip) return;
    down.preventDefault();
    var row = grip.closest('.tl-item');
    row.classList.add('dragging');
    var moved = false;
    function move(ev) {
      var others = Array.prototype.filter.call(rows.children, function (r) { return r !== row; });
      var before = null;
      for (var i = 0; i < others.length; i++) {
        var r = others[i].getBoundingClientRect();
        if (ev.clientY < r.top + r.height / 2) { before = others[i]; break; }
      }
      if (row.nextElementSibling !== before) { rows.insertBefore(row, before); moved = true; }
    }
    function up() {
      row.classList.remove('dragging');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      if (moved) changed();
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  });
})();


/* Drag a row within its own list by a grip. Shared by the card editor's
   document rows and the files inside a home page document; the rows' hidden
   inputs post in DOM order, so the order on screen is the order saved. */
function dragWithin(list, rowSel, gripSel, onMoved) {
  list.addEventListener('pointerdown', function (down) {
    var grip = down.target.closest(gripSel);
    if (!grip || !list.contains(grip)) return;
    var row = grip.closest(rowSel);
    if (!row || row.parentNode !== list) return;
    down.preventDefault();
    down.stopPropagation();          /* a file grip must not also drag the whole item */
    row.classList.add('dragging');
    var moved = false;
    function move(ev) {
      var others = Array.prototype.filter.call(list.children, function (r) {
        return r !== row && r.matches(rowSel);
      });
      var before = null;
      for (var i = 0; i < others.length; i++) {
        var r = others[i].getBoundingClientRect();
        if (ev.clientY < r.top + r.height / 2) { before = others[i]; break; }
      }
      if (row.nextElementSibling !== before) { list.insertBefore(row, before); moved = true; }
    }
    function up() {
      row.classList.remove('dragging');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      if (moved && onMoved) onMoved();
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  });
}

/* Card editor: document rows (reorder, ✕ = leaves the card when saved). */
(function () {
  var list = document.querySelector('[data-doc-rows]');
  if (!list) return;
  var form = list.closest('form');
  function changed() { if (form) form.dispatchEvent(new Event('input', { bubbles: true })); }
  dragWithin(list, '.doc-row', '.link-grip', changed);
  list.addEventListener('click', function (ev) {
    var del = ev.target.closest('.doc-row-del');
    if (!del) return;
    del.closest('.doc-row').remove();
    changed();
  });
})();

/* Home page: files inside each document item (the item grip still moves the item). */
(function () {
  var form = document.querySelector('[data-items-editor]');
  if (!form) return;
  function changed() { form.dispatchEvent(new Event('input', { bubbles: true })); }
  function wire(files) {
    if (files.__wired) return;
    files.__wired = true;
    dragWithin(files, '.tl-file', '.file-grip', changed);
  }
  Array.prototype.forEach.call(form.querySelectorAll('.tl-files'), wire);
  /* rows added later (Add document) bring their own .tl-files */
  new MutationObserver(function () {
    Array.prototype.forEach.call(form.querySelectorAll('.tl-files'), wire);
  }).observe(form, { childList: true, subtree: true });
})();
