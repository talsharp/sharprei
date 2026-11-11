/*
 * Tag boxes (buyer buybox places, service provider types): type and pick from the
 * dropdown, or type your own and press Enter. The hidden input keeps "a, b, c".
 * Markup: templates/_tag_box.html.
 */
(function() {
document.querySelectorAll('.tag-box').forEach(function(box) {
  var hidden = box.querySelector('input[type=hidden]'), input = box.querySelector('.tag-input');
  var list = box.querySelector('.tag-list'), menu = box.querySelector('.tag-menu');
  var options = JSON.parse(box.dataset.options || '[]'), active = -1;
  var tags = hidden.value.split(',').map(function(t) { return t.trim(); }).filter(Boolean);
  function same(a, b) { return a.toLowerCase() === b.toLowerCase(); }
  function render() {
    list.innerHTML = '';
    tags.forEach(function(t, i) {
      var tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = t;
      var x = document.createElement('button');
      x.type = 'button'; x.className = 'tag-x'; x.textContent = '×'; x.setAttribute('aria-label', 'Remove ' + t);
      x.addEventListener('click', function() { tags.splice(i, 1); render(); input.focus(); });
      tag.appendChild(x);
      list.appendChild(tag);
    });
    hidden.value = tags.join(', ');
  }
  function add(value) {
    value = value.trim();
    if (!value) return;
    var known = options.filter(function(o) { return same(o, value); })[0];
    if (!tags.some(function(t) { return same(t, value); })) tags.push(known || value);
    input.value = '';
    render(); closeMenu();
  }
  function closeMenu() { menu.hidden = true; active = -1; }
  function showMenu() {
    var q = input.value.trim().toLowerCase();
    if (!q) { closeMenu(); return; }
    var matches = options.filter(function(o) { return o.toLowerCase().indexOf(q) >= 0 && !tags.some(function(t) { return same(t, o); }); });
    matches.sort(function(a, b) { return (a.toLowerCase().indexOf(q) !== 0) - (b.toLowerCase().indexOf(q) !== 0) || a.localeCompare(b); });
    matches = matches.slice(0, 8);
    menu.innerHTML = '';
    matches.forEach(function(o) {
      var item = document.createElement('div');
      item.className = 'tag-option'; item.textContent = o; item.setAttribute('role', 'option');
      item.addEventListener('mousedown', function(e) { e.preventDefault(); add(o); });
      menu.appendChild(item);
    });
    var hint = document.createElement('div');
    hint.className = 'tag-hint';
    hint.textContent = matches.length ? 'Click one, or press Enter to add "' + input.value.trim() + '" as typed' : 'Not in the list - press Enter to add "' + input.value.trim() + '"';
    menu.appendChild(hint);
    active = -1;  // Enter adds what was typed, unless one was picked with the arrow keys
    paint();
    menu.hidden = false;
  }
  function paint() { menu.querySelectorAll('.tag-option').forEach(function(el, i) { el.classList.toggle('active', i === active); }); }
  input.addEventListener('input', showMenu);
  input.addEventListener('keydown', function(e) {
    var items = menu.querySelectorAll('.tag-option');
    if (e.key === 'ArrowDown' && items.length) { e.preventDefault(); active = (active + 1) % items.length; paint(); }
    else if (e.key === 'ArrowUp' && items.length) { e.preventDefault(); active = (active - 1 + items.length) % items.length; paint(); }
    else if (e.key === 'Enter' || e.key === ',') {
      if (!input.value.trim()) { if (e.key === ',') e.preventDefault(); return; }  // empty Enter submits as usual
      e.preventDefault();
      add(!menu.hidden && active >= 0 && items[active] ? items[active].textContent : input.value);
    }
    else if (e.key === 'Backspace' && !input.value && tags.length) { tags.pop(); render(); }
    else if (e.key === 'Escape') { closeMenu(); }
  });
  input.addEventListener('blur', function() { closeMenu(); });
  box.addEventListener('click', function(e) { if (e.target === box || e.target === list) input.focus(); });
  // Something typed but not added yet is kept when saving.
  box.closest('form').addEventListener('submit', function() { if (input.value.trim()) add(input.value); });
  render();
});
})();
