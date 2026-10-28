// Back button: return to the previous page in this system; otherwise go to the fallback (dashboard / login).
(function () {
  document.querySelectorAll('.js-back').forEach(function (link) {
    link.addEventListener('click', function (event) {
      var cameFromHere = document.referrer && document.referrer.indexOf(window.location.origin) === 0
        && document.referrer !== window.location.href;
      if (cameFromHere && window.history.length > 1) {
        event.preventDefault();
        window.history.back();
      }
      // otherwise the link's href (the fallback page) is followed
    });
  });
})();

// Conditional display for dynamic CRF fields (server-side validation mirrors this rule).
(function () {
  function valuesFor(form, name) {
    const inputs = form.querySelectorAll('[name="' + name + '"]');
    const values = [];
    inputs.forEach(function (el) {
      if ((el.type === 'checkbox' || el.type === 'radio')) { if (el.checked) values.push(el.value); }
      else values.push(el.value);
    });
    return values;
  }
  function refresh(form) {
    form.querySelectorAll('[data-show-if]').forEach(function (wrapper) {
      const show = valuesFor(form, wrapper.dataset.showIf).indexOf(wrapper.dataset.showValue) !== -1;
      wrapper.classList.toggle('is-hidden', !show);
    });
  }
  document.querySelectorAll('form').forEach(function (form) {
    if (!form.querySelector('[data-show-if]')) return;
    form.addEventListener('change', function () { refresh(form); });
    refresh(form);
  });

  // Keep the selected tab when returning to a page with #tab in the URL.
  const hash = window.location.hash;
  if (hash) {
    const trigger = document.querySelector('[data-bs-toggle="tab"][data-bs-target="' + hash + '"]');
    if (trigger && window.bootstrap) bootstrap.Tab.getOrCreateInstance(trigger).show();
  }
  document.querySelectorAll('[data-bs-toggle="tab"]').forEach(function (el) {
    el.addEventListener('shown.bs.tab', function (e) { history.replaceState(null, '', e.target.dataset.bsTarget); });
  });
})();

// Linked address pickers: region → district → ward → street/village (Tom Select, searchable).
(function () {
  if (!window.TomSelect) return;
  var pickers = {};
  document.querySelectorAll('select[data-location]').forEach(function (select) {
    pickers[select.id] = new TomSelect(select, {
      valueField: 'id', labelField: 'text', searchField: ['text'], maxOptions: 1000,
      allowEmptyOption: true, placeholder: select.dataset.placeholder,
      plugins: ['clear_button'],
    });
  });
  function load(child, parentValue) {
    var picker = pickers[child.id];
    picker.clear(true);
    picker.clearOptions();
    if (!parentValue) { picker.disable(); return; }
    picker.enable();
    fetch(child.dataset.url + '?parent=' + encodeURIComponent(parentValue), {credentials: 'same-origin'})
      .then(function (r) { return r.json(); })
      .then(function (data) { picker.addOptions(data.results); picker.refreshOptions(false); });
  }
  Object.keys(pickers).forEach(function (id) {
    var child = document.getElementById(id);
    var parent = child.dataset.parent && document.getElementById(child.dataset.parent);
    if (!parent) return;
    if (!parent.value) pickers[id].disable();
    pickers[parent.id].on('change', function (value) {
      load(child, value);
      // clearing a level also clears the levels below it
      Object.keys(pickers).forEach(function (otherId) {
        var other = document.getElementById(otherId);
        if (other.dataset.parent === child.id && !child.value) { pickers[otherId].clear(true); pickers[otherId].clearOptions(); pickers[otherId].disable(); }
      });
    });
  });
})();

// Report filters: show the date boxes only for a custom period.
document.querySelectorAll('.js-period').forEach(function (select) {
  select.addEventListener('change', function () {
    select.form.querySelectorAll('.js-custom-dates').forEach(function (el) {
      el.classList.toggle('d-none', select.value !== 'custom');
    });
  });
});

// Side menu: one section open at a time. The section holding the current page opens; otherwise the last one opened stays open.
document.addEventListener('DOMContentLoaded', () => {
  const nav = document.getElementById('sidebar-nav');
  if (!nav) return;
  const KEY = 'ncd.sidebar.open';
  const open = (section) => {
    if (!section) return;
    section.classList.add('show');
    const button = nav.querySelector(`[data-bs-target="#${section.id}"]`);
    if (button) { button.classList.remove('collapsed'); button.setAttribute('aria-expanded', 'true'); }
  };
  const active = nav.querySelector('.collapse .nav-link.active');
  let remembered = null;
  try { remembered = localStorage.getItem(KEY); } catch (e) { /* storage unavailable */ }
  open(active ? active.closest('.collapse') : (remembered && document.getElementById(remembered)));
  nav.addEventListener('shown.bs.collapse', (event) => {
    try { localStorage.setItem(KEY, event.target.id); } catch (e) { /* storage unavailable */ }
  });
  nav.addEventListener('hidden.bs.collapse', (event) => {
    try { if (localStorage.getItem(KEY) === event.target.id) localStorage.removeItem(KEY); } catch (e) { /* storage unavailable */ }
  });
});

// Eye button next to password boxes: show / hide what was typed.
document.addEventListener('click', (event) => {
  const button = event.target.closest('.js-show-password');
  if (!button) return;
  const input = button.parentElement.querySelector('input');
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  button.querySelector('i').className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
  button.title = show ? 'Hide password' : 'Show password';
  button.setAttribute('aria-label', button.title);
  input.focus();
});
