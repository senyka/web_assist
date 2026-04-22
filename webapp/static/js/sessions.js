async function loadSessions() {
  const tbody = document.getElementById('sessionsTableBody');
  tbody.innerHTML = '<tr><td colspan="5" class="text-center">Загрузка...</td></tr>';

  try {
    const res = await fetch('/api/sessions', {headers: {'X-Requested-With': 'XMLHttpRequest'}});
    const data = await res.json();
    if (!data.success || !data.sessions.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-center">Нет активных сессий</td></tr>';
      return;
    }

    const currentSid = '{{ session.get("session_id", "") }}';
    tbody.innerHTML = data.sessions.map(s => {
      const isCurrent = s.session_id.startsWith(currentSid.substring(0, 8));
      return `
        <tr class="${isCurrent ? 'table-primary' : ''}">
          <td><code>${s.session_id}</code> ${isCurrent ? '<span class="badge bg-success">Текущая</span>' : ''}</td>
          <td>${new Date(s.created_at).toLocaleString()}</td>
          <td>${new Date(s.last_activity).toLocaleString()}</td>
          <td><small>${s.ip_address}<br>${s.user_agent}</small></td>
          <td>
            ${!isCurrent ? `<button class="btn btn-sm btn-outline-danger" onclick="terminateSession('${s.session_id}')">✕</button>` : '<span class="text-muted">—</span>'}
          </td>
        </tr>`;
    }).join('');
  } catch {
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-danger">Ошибка загрузки</td></tr>';
  }
}

async function terminateSession(sid) {
  if (!confirm('Завершить эту сессию?')) return;
  try {
    const res = await fetch(`/api/sessions/${sid}`, {method: 'DELETE', headers: {'X-Requested-With': 'XMLHttpRequest'}});
    const data = await res.json();
    if (data.success) {
      if (data.message?.includes('logging out')) window.location.href = '/login';
      else loadSessions();
    } else alert('❌ ' + data.error);
  } catch { alert('❌ Ошибка сети'); }
}

async function terminateAllSessions(adminMode) {
  const msg = adminMode ? 'Завершить ВСЕ сессии ВСЕХ пользователей?' : 'Завершить ВСЕ сессии, кроме текущей?';
  if (!confirm(msg)) return;
  try {
    const res = await fetch('/api/sessions/terminate-all', {method: 'POST', headers: {'X-Requested-With': 'XMLHttpRequest'}});
    const data = await res.json();
    if (data.success) {
      alert(`✅ Завершено: ${data.terminated}`);
      loadSessions();
    } else alert('❌ ' + data.error);
  } catch { alert('❌ Ошибка сети'); }
}

document.addEventListener('DOMContentLoaded', () => {
  const modal = document.getElementById('sessionsModal');
  if (modal) {
    modal.addEventListener('shown.bs.modal', loadSessions);
    setInterval(() => { if (modal.classList.contains('show')) loadSessions(); }, 30000);
  }
});
