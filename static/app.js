(() => {
  const $ = s => document.querySelector(s);
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const show = v => (v == null ? 'n/a' : v);
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const charts = {};

  // draw a chart, or update it in place so changes animate instead of flashing
  function draw(id, type, data, options = {}) {
    if (typeof Chart === 'undefined') return;
    const old = charts[id];
    if (old) { old.data.labels = data.labels; old.data.datasets = data.datasets; old.update(); return; }
    Chart.defaults.color = css('--muted');
    Chart.defaults.borderColor = css('--rule');
    Chart.defaults.font.family = css('--sans');
    charts[id] = new Chart($('#' + id), {type, data, options: {
      responsive: true, maintainAspectRatio: false, animation: calm ? false : {duration: 500}, ...options}});
  }

  function table(el, head, rows) {
    el.innerHTML = '<tr>' + head.map(h => `<th${h[1] ? ' class="num"' : ''}>${h[0]}</th>`).join('') + '</tr>' +
      rows.map(r => '<tr>' + r.map((c, i) => `<td${head[i][1] ? ' class="num"' : ''}>${c}</td>`).join('') + '</tr>').join('');
  }

  function dashboard() {
    let D = null;
    const params = () => new URLSearchParams({exam: $('#exam').value, class: $('#class').value, subject: $('#subject').value});
    async function load() {
      const res = await fetch('/api/dashboard?' + params());
      if (!res.ok) { $('#insight').textContent = 'The results could not be loaded. Reload the page.'; return; }
      D = await res.json();
      render();
    }
    function render() {
      const B = css('--boy'), G = css('--girl'), sel = $('#subject').value, k = D.kpi;
      $('#insight').textContent = D.insight;
      $('#stats').innerHTML = [['Students', k.students], ['Boys average', show(k.boys)], ['Girls average', show(k.girls)],
        ['Gap, girls minus boys', show(k.gap)], ['Pass rate', k.pass_rate == null ? 'n/a' : k.pass_rate + '%'],
        ['Boys with open alerts', k.risk_boys]].map(([l, v]) => `<div><dt>${l}</dt><dd>${esc(v)}</dd></div>`).join('');
      const shade = c => D.subjects.map(s => !sel || String(s.id) === sel ? c : c + '55');
      const y100 = {y: {min: 0, max: 100}};
      draw('c-subjects', 'bar', {labels: D.subjects.map(s => s.name), datasets: [
        {label: 'Boys', data: D.subjects.map(s => s.M), backgroundColor: shade(B)},
        {label: 'Girls', data: D.subjects.map(s => s.F), backgroundColor: shade(G)}]},
        {scales: y100, onClick: (e, els) => {
          if (!els.length) return;
          const id = String(D.subjects[els[0].index].id);
          $('#subject').value = $('#subject').value === id ? '' : id;
          load();
        }});
      draw('c-trend', 'line', {labels: D.trend.map(t => t.name), datasets: [
        {label: 'Boys', data: D.trend.map(t => t.M), borderColor: B, backgroundColor: B, tension: .3, pointRadius: 5},
        {label: 'Girls', data: D.trend.map(t => t.F), borderColor: G, backgroundColor: G, tension: .3, pointRadius: 5}]}, {scales: y100});
      draw('c-grades', 'bar', {labels: ['A', 'B', 'C', 'D', 'E', 'F'], datasets: [
        {label: 'Boys', data: D.grades.M, backgroundColor: B}, {label: 'Girls', data: D.grades.F, backgroundColor: G}]});
      draw('c-risk', 'doughnut', {labels: ['Boys', 'Girls'], datasets: [{data: [D.risk.M, D.risk.F], backgroundColor: [B, G], borderColor: css('--paper')}]},
        {onClick: (e, els) => {
          if (els.length) location.href = '/risk?' + new URLSearchParams({exam: $('#exam').value, class: $('#class').value, gender: els[0].index ? 'F' : 'M'});
        }});
      table($('#t-top'), [['Student'], ['Class'], ['Average', 1]],
        D.top.map(t => [`<a href="/student/${t.id}">${esc(t.name)}</a>`, esc(t.cls), t.avg]));
      table($('#t-classes'), [['Class'], ['Students', 1], ['Average', 1]], D.classes.map(c => [esc(c.name), c.n, c.avg]));
    }
    ['exam', 'class', 'subject'].forEach(id => $('#' + id).addEventListener('change', load));
    load();
  }

  function student() {
    const d = JSON.parse($('#data').textContent);
    if (!d.trend.length) return;
    const me = d.gender === 'M' ? css('--boy') : css('--girl'), grey = css('--muted'), y100 = {y: {min: 0, max: 100}};
    draw('c-trend', 'line', {labels: d.trend.map(t => t.name), datasets: [
      {label: 'This student', data: d.trend.map(t => t.mine), borderColor: me, backgroundColor: me, tension: .3, pointRadius: 5},
      {label: 'Class average', data: d.trend.map(t => t.cls), borderColor: grey, backgroundColor: grey, borderDash: [5, 4], tension: .3}]}, {scales: y100});
    draw('c-scores', 'bar', {labels: d.scores.map(s => s.name), datasets: [
      {label: 'This student', data: d.scores.map(s => s.total), backgroundColor: me},
      {label: 'Class average', data: d.scores.map(s => s.cls), backgroundColor: grey}]}, {scales: y100});
  }

  // small behaviours that keep the templates free of inline scripts
  document.querySelectorAll('form[data-auto] select').forEach(s => s.addEventListener('change', () => s.form.submit()));
  document.querySelectorAll('[data-print]').forEach(b => b.addEventListener('click', () => window.print()));
  document.querySelectorAll('form[data-confirm]').forEach(f => f.addEventListener('submit', e => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));
  const page = document.body.dataset.page;
  if (page === 'dashboard') dashboard();
  if (page === 'student') student();
})();
