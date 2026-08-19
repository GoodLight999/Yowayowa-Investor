(() => {
  function localISO(date) {
    const copy = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return copy.toISOString().slice(0, 10);
  }

  function applyRange(kind) {
    const start = new Date();
    const end = new Date(start);
    if (kind === 'week') end.setDate(end.getDate() + ((7 - start.getDay()) % 7));
    else end.setDate(end.getDate() + Number(kind));
    document.querySelector('#calendar-start').value = localISO(start);
    document.querySelector('#calendar-end').value = localISO(end);
    document.querySelector('#calendar-form')?.requestSubmit();
  }

  document.querySelectorAll('[data-calendar-range]').forEach(button => {
    button.addEventListener('click', () => applyRange(button.dataset.calendarRange));
  });
})();
