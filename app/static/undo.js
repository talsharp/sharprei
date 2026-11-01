/*
 * Undo for every delete (Tal, 2026-11-01): the item disappears, a bar shows
 * "X deleted · Undo · ×" for 10 seconds, and only then is it really deleted.
 * Leaving the page early still deletes it. One pending delete at a time - a
 * new one finishes the previous.
 *
 * Use it from a page's own script:
 *   undoable('Buyer removed', {undo: function() {...}, commit: function(leaving) {...}});
 * or with attributes (no script needed):
 *   data-undo="Payment deleted"      on an hx-post button, a <form>, or a submit button
 *   data-undo-confirm="Sure?"        ask first (big deletes only)
 *   data-undo-hide="closest .x"      what to hide meanwhile (default: the row it's in);
 *                                    "all:<selector>" hides every match, "none" hides nothing
 *   data-undo-after="reload|follow"  after a form delete: reload the page, or go where it leads
 */
(function() {
  var SECONDS = 10, bar = null, pending = null;

  function makeBar() {
    bar = document.createElement('div');
    bar.className = 'undo-bar';
    bar.setAttribute('role', 'status');
    bar.innerHTML = '<span class="undo-msg"></span><button type="button" class="undo-btn">Undo</button><button type="button" class="undo-close" aria-label="Close">×</button>';
    document.body.appendChild(bar);
    bar.querySelector('.undo-btn').addEventListener('click', function() { finish(false); });
    bar.querySelector('.undo-close').addEventListener('click', function() { finish(true); });
  }

  function finish(commit, leaving) {
    if (!pending) return;
    var p = pending;
    pending = null;
    clearTimeout(p.timer);
    if (bar) bar.classList.remove('show');
    try { if (commit) p.commit(!!leaving); else p.undo(); } catch (e) { console.error(e); }
  }

  window.undoable = function(message, opts) {
    finish(true);
    if (!bar) makeBar();
    bar.querySelector('.undo-msg').textContent = message;
    pending = {undo: opts.undo || function() {}, commit: opts.commit || function() {},
               timer: setTimeout(function() { finish(true); }, SECONDS * 1000)};
    bar.classList.remove('show');
    void bar.offsetWidth;  // restart the countdown animation
    bar.classList.add('show');
  };

  // A POST that still goes through when the page is closing.
  window.undoPost = function(url, leaving, body) {
    return fetch(url, {method: 'POST', body: body, keepalive: !!leaving, credentials: 'same-origin'});
  };

  window.addEventListener('pagehide', function() { finish(true, true); });

  function hideTargets(el) {
    var how = el.getAttribute('data-undo-hide') || '';
    if (how === 'none') return [];
    if (how.indexOf('all:') === 0) return Array.prototype.slice.call(document.querySelectorAll(how.slice(4)));
    if (how.indexOf('closest ') === 0) return [el.closest(how.slice(8))].filter(Boolean);
    return [el.closest('tr') || el.closest('form') || el];
  }
  function hide(els) { els.forEach(function(e) { e.dataset.undoDisplay = e.style.display; e.style.display = 'none'; }); }
  function show(els) { els.forEach(function(e) { e.style.display = e.dataset.undoDisplay || ''; }); }

  // htmx delete buttons: hold the request, send it when the 10 seconds are up.
  document.addEventListener('htmx:confirm', function(evt) {
    var el = evt.detail.elt, message = el.getAttribute('data-undo');
    if (!message) return;
    evt.preventDefault();
    var question = el.getAttribute('data-undo-confirm');
    if (question && !confirm(question)) return;
    var hidden = hideTargets(el);
    hide(hidden);
    undoable(message, {
      undo: function() { show(hidden); },
      commit: function(leaving) {
        if (leaving) undoPost(evt.detail.path, true);
        else evt.detail.issueRequest(true);
      }
    });
  });

  // Plain form deletes.
  document.addEventListener('submit', function(evt) {
    var form = evt.target, button = evt.submitter;
    var holder = button && button.hasAttribute('data-undo') ? button : form.hasAttribute('data-undo') ? form : null;
    if (!holder) return;
    evt.preventDefault();
    evt.stopImmediatePropagation();
    var question = holder.getAttribute('data-undo-confirm');
    if (question && !confirm(question)) return;
    var action = (button && button.hasAttribute('formaction')) ? button.formAction : form.action;
    var after = holder.getAttribute('data-undo-after');
    var hidden = hideTargets(holder);
    hide(hidden);
    undoable(holder.getAttribute('data-undo'), {
      undo: function() { show(hidden); },
      commit: function(leaving) {
        undoPost(action, leaving, new FormData(form)).then(function(r) {
          if (leaving) return;
          // A page with unsaved boxes (deal page editor) saves them before it reloads.
          var go = function() { if (after === 'follow') location.href = r.url; else if (after === 'reload') location.reload(); };
          if (after && window.saveTextThen) window.saveTextThen(go); else go();
        });
      }
    });
  }, true);
})();
