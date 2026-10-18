const list = document.querySelector('#modules');
const error = document.querySelector('#error');
const summary = document.querySelector('#summary');
const stopAll = document.querySelector('#stop-all');
const icons = {data:'M3 4h18v16H3zM3 9h18M9 4v16',calculation:'M5 3h14v18H5zM8 6h8M8 11h1M12 11h1M16 11h1M8 15h1M12 15h1M16 15h1',screen:'M3 4h18v13H3zM8 21h8M12 17v4',clipboard:'M9 5H5v16h14V5h-4M9 3h6v4H9z',activity:'M3 12h4l3-8 4 16 3-8h4',audio:'M9 4a3 3 0 0 1 6 0v8a3 3 0 0 1-6 0zM5 11v1a7 7 0 0 0 14 0v-1M12 19v3',services:'M3 3h18v7H3zM3 14h18v7H3zM7 7h1M7 18h1',disk:'M5 3h14l3 14v4H2v-4zM2 17h20M6 20h1M17 20h1',products:'M4 7h16l1 14H3zM8 7V5a4 4 0 0 1 8 0v2',shipments:'M3 6h12v12H3zM15 10h4l3 4v4h-7M5 20h2M17 20h2'};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function request(path, init = {}) {
  const response = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', 'X-Hoard-Request': '1' } });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : body.error || 'No se pudo guardar el cambio.');
  return body;
}

function showError(message) { error.textContent = message; error.hidden = !message; }

function render(data) {
  document.querySelector('#brand').textContent = data.name;
  document.title = data.name;
  const active = data.modules.filter(m => m.enabled).length;
  summary.textContent = active ? `${active} de ${data.modules.length} funciones activas` : 'Todo desactivado · ningún motor en segundo plano';
  summary.parentElement.classList.toggle('active', active > 0);
  stopAll.disabled = active === 0;
  list.replaceChildren();
  const nav = document.querySelector('#module-nav'); nav.replaceChildren();
  for (const module of data.modules) {
    const navLink = element('a', '', 'nav-link'); navLink.href = '#' + module.id;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', icons[module.id]); svg.append(path);
    navLink.append(svg, document.createTextNode(module.name)); nav.append(navLink);
    const row = element('article', '', 'module'); row.id = module.id;
    const content = element('div');
    const heading = element('h2', module.name); heading.append(element('span', module.origin, 'origin'));
    content.append(heading, element('p', module.description));
    const details = element('details'); details.append(element('summary', 'Ubicación de los datos'), element('code', module.data_dir)); content.append(details);
    if (module.error) content.append(element('p', module.error, 'module-error'));
    const controls = element('div', '', 'controls');
    const open = element('a', 'Abrir', 'open'); open.href = `/${module.id}/`; open.setAttribute('aria-label', `Abrir ${module.name}`);
    open.setAttribute('aria-disabled', String(!module.enabled)); if (!module.enabled) open.tabIndex = -1;
    const toggle = element('button', '', 'switch'); toggle.type = 'button'; toggle.setAttribute('role', 'switch'); toggle.setAttribute('aria-checked', String(module.enabled)); toggle.setAttribute('aria-label', module.name);
    const label = element('span', module.enabled ? 'Activada' : 'Desactivada');
    const track = element('span', '', 'switch-track'); track.setAttribute('aria-hidden', 'true'); track.append(element('span', '', 'switch-thumb'));
    toggle.append(label, track);
    toggle.onclick = async () => {
      toggle.disabled = true; label.textContent = module.enabled ? 'Deteniendo…' : 'Activando…'; showError('');
      try { await request(`/api/modules/${module.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !module.enabled }) }); }
      catch (e) { showError(e.message); }
      finally { await refresh(); document.querySelector(`#${module.id} button`)?.focus(); }
    };
    controls.append(open, toggle); row.append(content, controls); list.append(row);
  }
  list.setAttribute('aria-busy', 'false');
}

async function refresh() {
  try { render(await request('/api/modules')); }
  catch (e) { showError(`${e.message} Comprueba que el servidor siga abierto y recarga la página.`); }
}
stopAll.onclick = async () => {
  stopAll.disabled = true; showError('');
  try { render(await request('/api/modules/stop-all', { method: 'POST', body: '{}' })); }
  catch (e) { showError(e.message); await refresh(); }
};
await refresh();
