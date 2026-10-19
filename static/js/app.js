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
