(() => {
  const config = JSON.parse(document.getElementById('access-data').textContent);
  const form = document.getElementById('access-form');
  const password = document.getElementById('password');
  const reveal = document.getElementById('show-password');
  const submit = document.getElementById('submit');
  const result = document.getElementById('result');
  const label = submit.querySelector('span');
  const idle = label.textContent;
  reveal.addEventListener('click', () => {
    const visible = password.type === 'password';
    password.type = visible ? 'text' : 'password';
    reveal.textContent = visible ? config.hide : config.show;
    reveal.setAttribute('aria-pressed', String(visible));
  });
  const confirm = document.getElementById('confirm');
  const validate = () => { if (confirm) confirm.setCustomValidity(confirm.value === password.value ? '' : config.mismatch); };
  password.addEventListener('input', validate);
  if (confirm) confirm.addEventListener('input', validate);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submit.disabled) return;
    validate();
    if (!form.reportValidity()) return;
    const data = Object.fromEntries(new FormData(form));
    if (config.setup) data.port = Number(data.port);
    submit.disabled = true;
    form.setAttribute('aria-busy', 'true');
    label.textContent = config.busy;
    result.hidden = true;
    try {
      const response = await fetch(config.setup ? '/setup' : '/login', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': config.token}, body: JSON.stringify(data)
      });
      let value;
      try { value = await response.json(); } catch { throw new Error(config.network); }
      if (!response.ok) throw new Error(response.status === 401 ? config.invalid : value.error || config.network);
      if (value.csrf) { try { sessionStorage.setItem('hq_csrf', value.csrf); } catch {} }
      if (config.setup && value.url) {
        const link = document.createElement('a');
        link.href = value.url;
        link.textContent = config.saved;
        result.replaceChildren(link);
        result.className = 'success';
        result.hidden = false;
        form.querySelectorAll('input,button').forEach(control => control.disabled = true);
        link.focus();
      } else location.assign('/');
    } catch (error) {
      result.textContent = error instanceof TypeError ? config.network : error.message;
      result.className = '';
      result.hidden = false;
      submit.disabled = false;
    } finally {
      form.removeAttribute('aria-busy');
      label.textContent = idle;
    }
  });
})();
